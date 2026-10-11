"""
agents/quote_verifier.py

Quote Verifier. Deterministic (no AI). Every number the AI reader reports must
come with the exact sentence it was read from. This module checks that:

  1. the quote really appears in the contract text (whitespace/case-insensitive)
  2. the quoted sentence really contains that number (digits or words)

A candidate that fails either check is thrown away, so an invented threshold
can never reach the rule runner.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from agents.number_words import find_numbers
from core.rules_ir import Candidate, ClauseExtraction

_QUOTE_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_QUOTE_TRANS)).strip().lower()


def quote_in_text(quote: str, *texts: str) -> bool:
    q = normalize(quote)
    return bool(q) and any(q in normalize(t) for t in texts)


def number_in_quote(value: float, quote: str, allow_days: bool = False) -> bool:
    values = [v for v, _, _ in find_numbers(quote)]
    if any(abs(v - value) < 1e-9 for v in values):
        return True
    # "2 days" is stored as 48 hours
    return allow_days and any(abs(v * 24 - value) < 1e-9 for v in values)


def verify_candidate(cand: Candidate, kind: str, *texts: str) -> Tuple[bool, str]:
    if not quote_in_text(cand.quote, *texts):
        return False, "quote not found in contract text"
    if not number_in_quote(cand.value, cand.quote, allow_days=(kind == "resolution")):
        return False, f"quote does not contain the number {cand.value:g}"
    return True, ""


def verify_extraction(ext: ClauseExtraction, *texts: str) -> Tuple[ClauseExtraction, List[str]]:
    """Return a copy of `ext` keeping only verified candidates, plus a list of rejections."""
    rejected: List[str] = []
    kept = {}
    for kind, cands in (("uptime", ext.uptime_candidates), ("resolution", ext.resolution_candidates)):
        good = []
        for c in cands:
            ok, why = verify_candidate(c, kind, *texts)
            if ok:
                good.append(c)
            else:
                rejected.append(f"{kind} candidate {c.value:g} rejected: {why}")
        kept[kind] = good
    update = {"uptime_candidates": kept["uptime"], "resolution_candidates": kept["resolution"]}
    good_tiers = []
    for t in ext.credit_tiers:
        need = [t.credit_percent] + [v for v in (t.min_uptime, t.max_uptime) if v is not None]
        if quote_in_text(t.quote, *texts) and all(number_in_quote(v, t.quote) for v in need):
            good_tiers.append(t)
        else:
            rejected.append(f"credit tier ({t.credit_percent:g}%) rejected: quote missing or does not contain its numbers")
    update["credit_tiers"] = good_tiers
    if ext.precedence_quote and not quote_in_text(ext.precedence_quote, *texts):
        rejected.append("precedence quote not found in contract text")
        update["precedence_quote"] = None
        update["uptime_candidates"] = [c.model_copy(update={"governs": False}) for c in update["uptime_candidates"]]
        update["resolution_candidates"] = [c.model_copy(update={"governs": False}) for c in update["resolution_candidates"]]
    if ext.penalty_usd_per_point is not None:
        if not (ext.penalty_quote and quote_in_text(ext.penalty_quote, *texts)
                and number_in_quote(ext.penalty_usd_per_point, ext.penalty_quote)):
            rejected.append("penalty rate rejected: not backed by a verified quote")
            update["penalty_usd_per_point"] = None
            update["penalty_quote"] = None
    return _reconcile(ext.model_copy(update=update)), rejected


# A sentence only counts as "vague wording" if it actually hedges. A reader that labels an exclusions or
# conflict clause as vague (it states no standard at all) would otherwise add a needless unchecked reading.
_HEDGE_WORDS = ("timely", "reasonable effort", "reasonable time", "reasonable manner", "reasonably possible", "commercially reasonable", "promptly", "prompt ", "best effort", "best-effort", "best practice",
                "industry", "optimal", "appropriate", "adequate", "satisfactory", "as soon as", "expeditious",
                "good faith", "business days", "business day")


def _reconcile(ext: ClauseExtraction) -> ClauseExtraction:
    """
    Consistency rules between what a reader listed (deterministic, idempotent):
      - a number whose quote is a credit-table row is a tier BOUNDARY, not an uptime target;
      - a sentence that states a verified threshold is not "vague", even if it also says "reasonable efforts".
    """
    tier_quotes = {normalize(t.quote) for t in ext.credit_tiers if t.quote}
    uptime = [c for c in ext.uptime_candidates if normalize(c.quote) not in tier_quotes]
    stated = {normalize(c.quote) for c in uptime + ext.resolution_candidates}
    vague = [v for v in ext.vague_terms if normalize(v) not in stated and any(h in v.lower() for h in _HEDGE_WORDS)]
    return ext.model_copy(update={"uptime_candidates": uptime, "vague_terms": vague})
