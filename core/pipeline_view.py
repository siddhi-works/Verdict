"""
core/pipeline_view.py

Turns one pipeline run (core.pipeline.Analysis) into the stage-by-stage account a person reads: what each part of the
system looked at, what it produced, and why the final decision followed. Pure data; no framework imports.

Stages, in the order the system runs them:
  1 read       candidate numbers and their quotes
  2 verify     every quote checked against the contract text
  3 readings   every plausible interpretation of the contract
  4 data       telemetry judged into a trusted view and an inclusive view
  5 envelope   every reading tested on every view
  6 decision   the rule that fired, and the outcome
  7 proof      the sealed record (when the run was sealed)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.present import credit_text, money, plain_reason, reading_rows, status_label

SPAN_STAGE = {"read.pattern": "read", "read.ai": "read", "verify.quotes": "verify", "enumerate.readings": "readings",
              "run.grid": "envelope", "decide.envelope": "decision", "seal.proof": "proof"}

RULE_TEXT = {
    "": "Every reading gave the same outcome on both telemetry views, so the verdict can be applied without a person.",
    "flip": "The outcome changes between readings or telemetry views, so the system does not guess: a person decides.",
    "no_reading": "No reading of the contract could be evaluated, so nothing can be automated.",
    "no_data": "There is no usable telemetry for this contract, so no verdict (and no amount) is produced.",
    "data_quality": "Most telemetry rows are unusable (quality grade D), so the data cannot support a verdict.",
}


def _candidates(items) -> List[Dict[str, Any]]:
    return [{"value": c.value, "quote": c.quote, "governs": c.governs} for c in items]


def _spans(trace) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if trace is None:
        return out
    for s in trace.spans:
        key = SPAN_STAGE.get(s["name"])
        if key:
            out[key] = round(out.get(key, 0.0) + float(s.get("duration_ms", 0.0)), 1)
    return out


def build_view(a: Any, proof: Optional[dict] = None) -> Dict[str, Any]:
    """`a` is a core.pipeline.Analysis. `proof` optionally carries sealed-record details (proof_id, seq, actor, replay)."""
    ext, enum, d = a.extraction, a.enum, a.decision
    ms = _spans(a.trace)

    # 1. read
    read = {
        "key": "read", "title": "Read the contract", "ms": ms.get("read"),
        "summary": f"Found {len(ext.uptime_candidates)} uptime target(s) and {len(ext.resolution_candidates)} repair-time limit(s)"
                   + (f", {len(ext.credit_tiers)} credit-table row(s)" if ext.credit_tiers else "")
                   + (f", and {len(ext.vague_terms)} vague requirement(s)." if ext.vague_terms else "."),
        "readers": [
            {"name": "Pattern reader", "state": "ran", "note": "Deterministic. Lists every number with the sentence it came from."},
            {"name": "AI reader", "state": "not enabled here",
             "note": "Needs an API key. When enabled, its output is merged with the pattern reader's, and any extra number becomes another reading."},
        ],
        "uptime": _candidates(ext.uptime_candidates), "resolution": _candidates(ext.resolution_candidates),
        "tiers": [{"range": f"{'' if t.min_uptime is None else f'{t.min_uptime:g}% to '}below {t.max_uptime:g}%" if t.max_uptime is not None else "any",
                   "credit_percent": t.credit_percent, "quote": t.quote} for t in ext.credit_tiers],
        "vague": list(ext.vague_terms), "scope": ext.scope_severities, "precedence_quote": ext.precedence_quote,
        "penalty_rate": ext.penalty_usd_per_point,
    }

    # 2. verify
    checked = len(ext.uptime_candidates) + len(ext.resolution_candidates) + len(ext.credit_tiers)
    verify = {
        "key": "verify", "title": "Check every quote", "ms": ms.get("verify"),
        "summary": f"{checked} number(s) checked against the contract text; {len(a.rejections)} rejected."
                   if checked or a.rejections else "No numbers to check.",
        "rejected": list(a.rejections),
        "note": "A number is kept only if its quoted sentence is really in the contract and really contains that number.",
    }

    # 3. readings
    readings = {
        "key": "readings", "title": "Fan out the readings", "ms": ms.get("readings"),
        "summary": (f"{len(enum.readings)} plausible reading(s) of the contract." if enum.readings
                    else "No reading could be built: " + "; ".join(enum.blockers or ["nothing to evaluate"])),
        "items": [{"id": r.reading_id, "uptime": r.rules.required_uptime_percent, "uptime_applicable": r.rules.uptime_applicable,
                   "resolution_hours": r.rules.max_resolution_hours, "resolution_applicable": r.rules.resolution_applicable,
                   "scope": r.rules.scope_severities, "assumptions": r.assumptions, "tiers": len(r.rules.credit_tiers)}
                  for r in enum.readings],
        "notes": list(enum.notes), "blockers": list(enum.blockers),
    }

    # 4. data
    views = []
    for scope_key, per in a.views.items():
        for scenario, m in per.items():
            views.append({"scope": "all events" if scope_key == "*" else scope_key, "view": scenario,
                          "label": {"strict": "Trusted rows only", "inclusive": "Including doubtful rows"}.get(scenario, scenario),
                          "rows": m.log_count, "used": m.valid_log_count, "unusable": m.unusable_log_count, "doubtful": m.suspect_log_count,
                          "fleet_uptime": None if m.mean_uptime_percent is None else round(m.mean_uptime_percent, 2),
                          "slowest_repair_h": None if m.max_resolution_hours is None else round(m.max_resolution_hours, 1),
                          "grade": m.data_grade})
    data = {
        "key": "data", "title": "Judge the telemetry", "ms": None,
        "summary": f"Data quality grade {d.data_grade}. " + (
            f"{views[0]['rows']} rows: {views[0]['unusable']} unusable and {views[0]['doubtful']} doubtful." if views else "No telemetry rows."),
        "views": views,
        "note": "Unusable rows are always dropped. Doubtful rows (faulty sensor, duplicate, flat 0%) are tested both ways.",
    }

    # 5. envelope
    cells = [c.model_dump() for c in a.cells]
    env_dict = {"readings": [{"reading_id": r.reading_id, "uptime_threshold": r.rules.required_uptime_percent,
                              "resolution_threshold_hours": r.rules.max_resolution_hours, "scope_severities": r.rules.scope_severities}
                             for r in enum.readings], "cells": cells}
    statuses = sorted({c["status"] for c in cells})
    envelope = {
        "key": "envelope", "title": "Test every reading on every view", "ms": ms.get("envelope"),
        "summary": (f"{len(cells)} test(s); every one says {status_label(statuses[0]).lower()}." if len(statuses) == 1 and cells
                    else (f"{len(cells)} tests disagree: " + ", ".join(status_label(s).lower() if s != "UNDETERMINED" else "cannot be decided" for s in statuses) + "."
                          if cells else "Nothing to test.")),
        "rows": reading_rows(env_dict), "agree": len(statuses) == 1 and bool(cells),
    }

    # 6. decision
    result_row = {"status": d.status, "reason": d.reason, "envelope": env_dict, "penalty_usd": d.penalty_usd,
                  "penalty_basis": d.penalty_basis}
    rule = RULE_TEXT["" if d.status != "ESCALATED" else (d.kind if d.kind in RULE_TEXT else "flip")]
    decision = {
        "key": "decision", "title": "Decide", "ms": ms.get("decision"),
        "status": d.status, "status_label": status_label(d.status),
        "summary": plain_reason(result_row), "rule": rule,
        "credit": credit_text(result_row), "needs_commercial_review": d.needs_commercial_review,
        "exposure": (f"{money(d.exposure_min_usd or 0)} to {money(d.exposure_max_usd)}"
                     if d.status == "ESCALATED" and d.exposure_max_usd is not None else None),
        "trace": list(d.trace),
    }

    # 7. proof
    proof_stage = {
        "key": "proof", "title": "Seal the proof", "ms": ms.get("proof"),
        "sealed": bool(proof and proof.get("proof_id")),
        "summary": ("Sealed in the audit ledger; it can be replayed with no AI." if proof and proof.get("proof_id")
                    else "Not sealed. Workbench runs are for inspection and are not written to the ledger."),
        **(proof or {}),
    }

    stages = [read, verify, readings, data, envelope, decision, proof_stage]
    return {"status": d.status, "status_label": status_label(d.status), "summary": decision["summary"],
            "stages": stages, "total_ms": round(sum(v for v in ms.values()), 1)}
