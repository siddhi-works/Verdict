"""
core/pipeline.py

The end-to-end audit: Read -> Fan out -> Verify -> Run -> Decide -> Seal.

  1. Read      two independent readers list every candidate number with its quote
  2. Verify    quotes are checked against the contract text (no AI)
  3. Fan out   every plausible reading of the contract is enumerated
  4. Run       each reading is tested against each view of the telemetry
  5. Decide    the verdict envelope: automate only what never flips
  6. Seal      a proof bundle is written and chained into the audit ledger

Only step 1 (and an optional brief rewrite) may use an AI. Everything else is
repeatable arithmetic, so any sealed verdict can be replayed offline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from agents.clause_reader import PROMPT_VERSION, mask_text, merge_extractions, read_clauses_llm, read_clauses_regex
from agents.data_trust import build_views, contract_digest
from agents.quote_verifier import verify_extraction
from core.brief import build_brief
from core.envelope import Cell, Decision, decide, envelope_dict, resolve_decision, run_grid
from core.ledger import Ledger, sha256_text
from core.precedents import PrecedentStore
from core.rules_ir import ClauseExtraction, Enumeration, enumerate_readings
from core.schemas import AuditResult, TelemetryMetrics
from core.telemetry import Trace, write_trace

SCHEMA = "sla-proof-v1"


@dataclass
class PipelineConfig:
    mode: str = "mock"  # "mock" = regex reader only (offline); "llm" = AI reader + regex reader
    ledger: Optional[Ledger] = None
    precedents: Optional[PrecedentStore] = None
    max_retries: int = 5
    llm_client: object = None  # injectable for tests
    # --- experiment switches (default off): used by eval/ablation.py to measure what each part is worth ---
    # any of: "verifier", "scope", "precedence", "vague", "data_views", "envelope"
    ablate: frozenset = frozenset()
    reader_hook: object = None  # callable(ClauseExtraction) -> ClauseExtraction, e.g. to inject faults


@dataclass
class Analysis:
    """Everything computed for one contract, before it is turned into an AuditResult."""
    item: dict
    text: str
    extraction: ClauseExtraction
    rejections: List[str]
    enum: Enumeration
    views: Dict[str, Dict[str, TelemetryMetrics]]
    cells: List[Cell]
    grades: List[str]
    decision: Decision
    telemetry_sha: str
    trace: Optional[Trace] = None


def contract_text(item: dict) -> str:
    return item.get("raw_text") or item.get("contract_text") or ""


def _scope_key(scope: Optional[Tuple[str, ...]]) -> str:
    return "/".join(scope) if scope else "*"


def analyse(item: dict, df: pd.DataFrame, cfg: PipelineConfig) -> Analysis:
    cid = item.get("contract_id", "UNKNOWN")
    text = contract_text(item)

    tr = Trace("sla.audit", contract_id=cid, mode=cfg.mode)
    with tr.span("read.pattern"):
        regex_ext = read_clauses_regex(item)
    ext = regex_ext
    if cfg.mode == "llm":
        with tr.span("read.ai", **{"gen_ai.request.model": os.getenv("SLA_EXTRACTOR_MODEL", "")}):
            ext = merge_extractions(read_clauses_llm(item, cfg.max_retries, cfg.llm_client), regex_ext)
    if cfg.reader_hook is not None:
        ext = cfg.reader_hook(ext)
    with tr.span("verify.quotes") as at:
        if "verifier" in cfg.ablate:
            ext, rejections = ext, []
        else:
            ext, rejections = verify_extraction(ext, text, mask_text(text))
        at["rejected"] = len(rejections)
    if "scope" in cfg.ablate:
        ext = ext.model_copy(update={"scope_severities": None, "scope_options": []})
    if "precedence" in cfg.ablate:
        ext = ext.model_copy(update={
            "precedence_quote": None,
            "uptime_candidates": [c.model_copy(update={"governs": False}) for c in ext.uptime_candidates],
            "resolution_candidates": [c.model_copy(update={"governs": False}) for c in ext.resolution_candidates]})
    if "vague" in cfg.ablate:
        ext = ext.model_copy(update={"vague_terms": []})
    # Facts that come from the order form / contract profile (set by the operator), not from the SLA wording.
    profile = item.get("sla_profile") or {}
    checks = profile.get("checks")
    ext = ext.model_copy(update={
        "monthly_fee_usd": item.get("monthly_fee_usd"),
        "not_applicable": [k for k in ("uptime", "resolution") if checks is not None and k not in checks],
    })
    with tr.span("enumerate.readings") as at:
        enum = enumerate_readings(ext)
        at["readings"] = len(enum.readings)
    if "envelope" in cfg.ablate and enum.readings:  # no fan-out: just take the first reading, like a single-pass system
        enum = enum.model_copy(update={"readings": enum.readings[:1], "blockers": []})

    views: Dict[str, Dict[str, TelemetryMetrics]] = {}

    def views_for(scope):
        key = _scope_key(scope)
        if key not in views:
            v = build_views(cid, df, list(scope) if scope else None)
            if "data_views" in cfg.ablate:  # one view that trusts every parseable row, as a naive pipeline would
                v = {"strict": v["inclusive"], "inclusive": v["inclusive"]}
            views[key] = v
        return views[key]

    with tr.span("run.grid") as at:
        cells = run_grid(enum.readings, views_for) if enum.readings else []
        if not views:  # blockers only: still look at the data quality for the record
            views_for(None)
        at["cells"] = len(cells)
    grades = [m.data_grade for v in views.values() for m in v.values()]
    with tr.span("decide.envelope") as at:
        decision = decide(enum, cells, grades)
        at["status"] = decision.status
    rows = df[df["contract_id"].astype(str) == str(cid)]
    return Analysis(item, text, ext, rejections, enum, views, cells, grades, decision, contract_digest(rows), tr)


def _bundle(a: Analysis, cfg: PipelineConfig) -> dict:
    return {
        "schema": SCHEMA,
        "contract_id": a.item.get("contract_id", "UNKNOWN"),
        "contract_sha256": sha256_text(a.text),
        "telemetry_sha256": a.telemetry_sha,
        "extractor": {"mode": cfg.mode, "prompt_version": PROMPT_VERSION,
                      "model": os.getenv("SLA_EXTRACTOR_MODEL", "") if cfg.mode == "llm" else "regex",
                      "rejections": a.rejections},
        "extraction": a.extraction.model_dump(),
        "enumeration": a.enum.model_dump(),
        "grades": a.grades,
        "cells": [c.model_dump() for c in a.cells],
        "decision": a.decision.model_dump(),
    }


class _null:
    def __enter__(self):
        return {}

    def __exit__(self, *exc):
        return False


def to_result(a: Analysis, cfg: PipelineConfig) -> AuditResult:
    d = a.decision
    suggestion = cfg.precedents.suggest(a.enum) if (cfg.precedents and d.status == "ESCALATED") else None
    result = AuditResult(
        contract_id=a.item.get("contract_id", "UNKNOWN"), status=d.status, reason=d.reason,
        penalty_usd=d.penalty_usd, penalty_basis=d.penalty_basis,
        needs_commercial_review=d.needs_commercial_review,
        exposure_min_usd=d.exposure_min_usd, exposure_max_usd=d.exposure_max_usd,
        execution_trace=list(d.trace) + [f"Quote check: {r}" for r in a.rejections],
        envelope=envelope_dict(a.enum, a.cells, d), suggested_ruling=suggestion,
    )
    if d.status == "ESCALATED":
        result.brief = build_brief(result.contract_id, a.enum, a.cells, d, a.extraction, suggestion)
    if a.trace is not None:
        result.trace_id = a.trace.trace_id
    if cfg.ledger is not None:
        with a.trace.span("seal.proof") if a.trace is not None else _null():
            result.proof_id = cfg.ledger.seal(_bundle(a, cfg), actor="system:sla-harness")
        if a.trace is not None:
            write_trace(cfg.ledger.root / "traces.jsonl", a.trace.finish(
                proof_id=result.proof_id, status=result.status, readings=len(a.enum.readings), data_grade=a.decision.data_grade))
    return result


def audit_contract(item: dict, df: pd.DataFrame, cfg: Optional[PipelineConfig] = None) -> AuditResult:
    cfg = cfg or PipelineConfig()
    return to_result(analyse(item, df, cfg), cfg)


def resolve_escalation(cfg: PipelineConfig, proof_id: str, reading_id: str, reviewer: str,
                       scenario: Optional[str] = None, note: str = "") -> AuditResult:
    """Record a reviewer's ruling on a sealed escalation and seal the outcome as a new proof."""
    if cfg.ledger is None:
        raise ValueError("a ledger is required to resolve escalations")
    parent = cfg.ledger.load_proof(proof_id)
    if parent["decision"]["status"] != "ESCALATED" or parent.get("human"):
        raise ValueError("only machine-escalated decisions can be resolved")
    if cfg.ledger.resolution_of(proof_id):
        raise ValueError("this escalation already has a recorded ruling")
    enum = Enumeration(**parent["enumeration"])
    cells = [Cell(**c) for c in parent["cells"]]
    decision = resolve_decision(enum, cells, parent["grades"], reading_id, scenario)
    if decision.status == "ESCALATED":  # an inconclusive ruling is not recorded; the reviewer must settle it fully
        raise ValueError("that ruling does not settle the case: " + decision.reason.replace("Hard Gate: ", ""))
    reading = next(r for r in enum.readings if r.reading_id == reading_id)
    human = {"reviewer": reviewer, "reading_id": reading_id, "scenario": scenario, "note": note}
    bundle = {**parent, "parent_proof_id": proof_id, "human": human, "decision": decision.model_dump()}
    new_id = cfg.ledger.seal(bundle, event="human_resolution", actor=f"human:{reviewer}")
    if cfg.precedents is not None and decision.status in ("COMPLIANT", "BREACH"):
        cfg.precedents.record(enum.fingerprint, reading, reviewer, parent["contract_id"], note)
    return AuditResult(
        contract_id=parent["contract_id"], status=decision.status, reason=decision.reason + f" (ruled by {reviewer})",
        penalty_usd=decision.penalty_usd, penalty_basis=decision.penalty_basis,
        needs_commercial_review=decision.needs_commercial_review,
        exposure_min_usd=decision.exposure_min_usd, exposure_max_usd=decision.exposure_max_usd,
        execution_trace=list(decision.trace), proof_id=new_id, decided_by=f"human:{reviewer}",
        envelope={"readings": [r.model_dump() for r in enum.readings], "cells": parent["cells"]},
    )
