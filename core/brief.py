"""
core/brief.py

Escalation brief: turns the verdict grid into a one-page note a lawyer or
account manager can act on ("if 12h: breach $4,200; if 24h: compliant").

The brief is built from a fixed template, so every number in it comes straight
from the grid. An optional AI "polish" may reword it, but it is rejected
unless it contains no number that the template did not already contain.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from core.envelope import Cell, Decision
from core.rules_ir import ClauseExtraction, Enumeration

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _money(x: Optional[float]) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def _reading_line(reading, cells: List[Cell]) -> str:
    r = reading.rules
    parts = []
    if r.required_uptime_percent is not None:
        parts.append(f"uptime at least {r.required_uptime_percent:g}%")
    if r.max_resolution_hours is not None:
        scope = f" for {'/'.join(r.scope_severities)} events" if r.scope_severities else ""
        parts.append(f"repairs within {r.max_resolution_hours:g}h{scope}")
    terms = ", ".join(parts) or "no checkable threshold"
    outcomes = []
    for c in (x for x in cells if x.reading_id == reading.reading_id):
        label = {"strict": "trusted data only", "inclusive": "including doubtful rows"}.get(c.scenario, c.scenario)
        if c.status == "BREACH":
            credit = f", {c.credit_percent:g}% credit tier" if c.credit_percent is not None else ""
            outcomes.append(f"{label}: BREACH ({_money(c.penalty_usd)}{credit})")
        elif c.status == "UNDETERMINED":
            outcomes.append(f"{label}: cannot be decided")
        else:
            outcomes.append(f"{label}: COMPLIANT")
    return f"- {reading.reading_id} - {terms}\n    -> " + "; ".join(outcomes)


def build_brief(contract_id: str, enum: Enumeration, cells: List[Cell], decision: Decision,
                ext: Optional[ClauseExtraction] = None, suggestion: Optional[Dict[str, Any]] = None) -> str:
    lines = [f"ESCALATION BRIEF - contract {contract_id}", "", f"Why a human is needed: {decision.reason.replace('Hard Gate: ', '')}.", ""]
    if decision.kind in ("no_data", "data_quality"):
        lines += [f"Telemetry quality grade: {decision.data_grade} (A = clean, D = mostly unusable).",
                  "No verdict is possible from this data, and no amount has been calculated.",
                  "", "Action needed: obtain valid telemetry for the period, then re-run the audit."]
        return "\n".join(lines)
    if enum.readings:
        lines.append("What each reading of the contract says:")
        lines += [_reading_line(r, cells) for r in enum.readings]
        lines.append("")
    if decision.exposure_min_usd is not None:
        lines.append(f"Money at stake: {_money(decision.exposure_min_usd)} to {_money(decision.exposure_max_usd)}.")
    lines.append(f"Telemetry quality: grade {decision.data_grade} (A is clean, D is mostly unusable).")
    for n in enum.notes:
        lines.append("Note: " + n.rstrip(".") + ".")
    if ext is not None:
        quotes = [(f"uptime {c.value:g}%", c.quote) for c in ext.uptime_candidates] + \
                 [(f"repair time {c.value:g}h", c.quote) for c in ext.resolution_candidates] + \
                 [("vague wording", q) for q in ext.vague_terms]
        if quotes:
            lines += ["", "Contract evidence (exact sentences):"]
            lines += [f'- {label}: "{quote.strip()}"' for label, quote in quotes]
    if len(enum.readings) == 1:
        lines += ["", "Decision needed: the contract does not give a measurable standard for this requirement. "
                  "Agree a measurable standard with the vendor, or record how it should be judged."]
        return "\n".join(lines + ([f"Earlier rulings on this clause pattern ({suggestion['based_on_rulings']}) chose "
                                   f"{suggestion['reading_id']}. This is a suggestion only."] if suggestion else []))
    lines += ["", "Decision needed: confirm which reading governs"
              + (" and whether the doubtful telemetry rows may be trusted." if any(
                  c.scenario == "inclusive" and c.status != next((x.status for x in cells if x.reading_id == c.reading_id and x.scenario == "strict"), c.status)
                  for c in cells) else ".")]
    if suggestion:
        lines.append(f"Earlier rulings on this clause pattern ({suggestion['based_on_rulings']}) chose {suggestion['reading_id']}. "
                     "This is a suggestion only.")
    return "\n".join(lines)


def numbers_in(text: str) -> set:
    return {n.replace(",", "") for n in _NUM_RE.findall(text)}


def polish_brief(brief: str, rewrite: Callable[[str], str]) -> str:
    """Let `rewrite` (an AI call) reword the brief; keep it only if it adds no new numbers."""
    try:
        candidate = rewrite(brief)
    except Exception:  # noqa: BLE001 - polishing is optional
        return brief
    if candidate and numbers_in(candidate) <= numbers_in(brief):
        return candidate
    return brief
