"""
core/rules_ir.py

The shared vocabulary between the AI reader and the deterministic engine.

  ClauseExtraction  what a reader (AI or regex) found in the contract: every
                    candidate number WITH the quote it came from.
  Reading           one fully-specified interpretation of the contract that
                    the rule runner can execute.
  Enumeration       all readings that are plausible, plus anything that blocks
                    evaluation altogether.

The AI never produces a verdict. It only fills ClauseExtraction.
"""
from __future__ import annotations

import hashlib
import itertools
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from core.schemas import CreditTier, SLARules

MAX_READINGS = 16


class Candidate(BaseModel):
    value: float
    quote: str
    # True when a precedence clause makes this the governing value.
    governs: bool = False


class ClauseExtraction(BaseModel):
    contract_id: str
    uptime_candidates: List[Candidate] = Field(default_factory=list)
    resolution_candidates: List[Candidate] = Field(default_factory=list)
    scope_severities: Optional[List[str]] = None
    # Extra scopes when two readers disagree about which events the clause covers.
    scope_options: List[Optional[List[str]]] = Field(default_factory=list)
    scope_quote: Optional[str] = None
    precedence_quote: Optional[str] = None
    penalty_usd_per_point: Optional[float] = None
    penalty_quote: Optional[str] = None
    # Tiered service credits (percent of the monthly fee); rows of one table share `table`.
    credit_tiers: List[CreditTier] = Field(default_factory=list)
    # From the order form / contract profile, not from the SLA text:
    monthly_fee_usd: Optional[float] = None
    not_applicable: List[str] = Field(default_factory=list)  # e.g. ["resolution"] for an uptime-only SLA
    # Sentences that use qualitative wording where a number should be.
    vague_terms: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    extractor_failed: bool = False


class Reading(BaseModel):
    reading_id: str
    rules: SLARules
    assumptions: List[str] = Field(default_factory=list)


class Enumeration(BaseModel):
    contract_id: str
    readings: List[Reading] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    fingerprint: str = ""


_UPTIME_WORDS = ("uptime", "availab", "operational")
_REPAIR_WORDS = ("fault", "repair", "restor", "resolv", "resolution", "respon", "address", "critical", "event", "business day")


def vague_fields(vague_terms: List[str]) -> Tuple[bool, bool]:
    """Which SLA term(s) do the vague sentences talk about? (uptime, repair time). Unclear -> both."""
    up = rs = False
    for s in vague_terms:
        low = s.lower()
        a = any(w in low for w in _UPTIME_WORDS)
        b = any(w in low for w in _REPAIR_WORDS)
        up, rs = up or a or not (a or b), rs or b or not (a or b)
    return up, rs


def _options(kind: str, cands: List[Candidate], vague: bool, precedence_quote: Optional[str], not_applicable: bool = False):
    """Return ([(value|None, assumption, applicable)], note) for one field. value None = 'left unchecked'."""
    distinct = sorted({c.value for c in cands})
    if not distinct:
        if not_applicable:
            return [(None, f"{kind} is not part of this SLA (contract profile)", False)], ""
        why = "stated only in vague terms" if vague else "not stated in the contract"
        return [(None, f"{kind} threshold {why}; left unchecked", True)], f"{kind}: {why}"
    governing = {c.value for c in cands if c.governs}
    if len(distinct) > 1 and len(governing) == 1 and precedence_quote:
        v = next(iter(governing))
        opts = [(v, f"{kind} = {v:g} because a precedence clause makes it govern", True)]
        note = f"{kind}: conflict resolved by precedence clause"
    elif len(distinct) == 1:
        opts, note = [(distinct[0], f"{kind} = {distinct[0]:g}", True)], ""
    else:
        opts = [(v, f"{kind} = {v:g} (one of {', '.join(f'{d:g}' for d in distinct)} stated)", True) for v in distinct]
        note = f"{kind}: contract states {', '.join(f'{d:g}' for d in distinct)} with no precedence clause"
    if vague:  # a vague sentence about this term may be the one that really governs it
        opts.append((None, f"{kind} also described in vague terms; that wording left unchecked", True))
        note = (note + "; " if note else "") + f"{kind}: vague wording also present"
    return opts, note


def tiers_for_threshold(tiers: List[CreditTier], threshold: Optional[float]) -> List[CreditTier]:
    """The credit table that belongs to an uptime target: the one whose top tier starts at that target."""
    if threshold is None or not tiers:
        return []
    tables = sorted({t.table for t in tiers})
    for tb in tables:
        rows = [t for t in tiers if t.table == tb]
        tops = [t.max_uptime for t in rows if t.max_uptime is not None]
        if tops and abs(max(tops) - threshold) < 1e-9:
            return rows
    return list(tiers) if len(tables) == 1 else []


def enumerate_readings(ext: ClauseExtraction) -> Enumeration:
    """Turn what the reader found into every plausible, fully-specified reading."""
    en = Enumeration(contract_id=ext.contract_id)
    if ext.extractor_failed:
        en.blockers.append("extractor failed: " + "; ".join(ext.notes))
        return en

    vague_up, vague_rs = vague_fields(ext.vague_terms)
    up_opts, up_note = _options("uptime", ext.uptime_candidates, vague_up, ext.precedence_quote, "uptime" in ext.not_applicable)
    rs_opts, rs_note = _options("resolution", ext.resolution_candidates, vague_rs, ext.precedence_quote, "resolution" in ext.not_applicable)
    en.notes = [n for n in (up_note, rs_note) if n]
    if ext.vague_terms:
        en.notes.append("vague wording: " + " | ".join(ext.vague_terms[:3]))

    scope_opts = ext.scope_options or [ext.scope_severities]
    if len(scope_opts) > 1:
        en.notes.append("readers disagree on which events the repair-time clause covers")
    combos = list(itertools.product(up_opts, rs_opts, scope_opts))
    if len(combos) > MAX_READINGS:
        en.blockers.append(f"{len(combos)} possible readings exceeds the limit of {MAX_READINGS}")
        return en

    for i, ((u, ua, u_ok), (r, ra, r_ok), scope) in enumerate(combos, start=1):
        assumptions = [ua, ra]
        if ext.credit_tiers and not tiers_for_threshold(ext.credit_tiers, u):
            assumptions.append("credit table could not be matched to this target; no tiered amount")
        if scope:
            assumptions.append("repair-time clause covers only " + "/".join(scope) + " events")
        rules = SLARules(
            contract_id=ext.contract_id,
            required_uptime_percent=u,
            max_resolution_hours=r,
            scope_severities=scope,
            penalty_usd_per_point=ext.penalty_usd_per_point,
            credit_tiers=tiers_for_threshold(ext.credit_tiers, u),
            monthly_fee_usd=ext.monthly_fee_usd,
            uptime_applicable=u_ok,
            resolution_applicable=r_ok,
            ambiguity_detected=((u is None and u_ok) or (r is None and r_ok)),
            ambiguity_notes="; ".join(en.notes) or None,
        )
        en.readings.append(Reading(reading_id=f"R{i}", rules=rules, assumptions=assumptions))

    # Pattern fingerprint for the precedent store: independent of the contract id.
    pattern = "|".join([
        "u:" + ",".join(f"{o[0]:g}" if o[0] is not None else "-" for o in up_opts),
        "r:" + ",".join(f"{o[0]:g}" if o[0] is not None else "-" for o in rs_opts),
        "s:" + ",".join(sorted(ext.scope_severities or [])),
        "p:" + str(bool(ext.precedence_quote)),
    ])
    en.fingerprint = hashlib.sha256(pattern.encode()).hexdigest()[:16]
    return en
