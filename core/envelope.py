"""
core/envelope.py

The Verdict Envelope. For one contract we test EVERY plausible reading of the
contract against EVERY plausible view of the data (see core/rules_ir.py and
agents/data_trust.py) and look at the whole grid of outcomes:

  every cell COMPLIANT  -> auto COMPLIANT   (even if the contract was contradictory)
  every cell BREACH     -> auto BREACH      (even if the contract was contradictory)
  anything else         -> ESCALATED, with the dollar range and what each reading says

Rule of thumb: a contradiction that does not change the outcome is not worth a
lawyer's time; an outcome that flips between readings is.

Nothing here calls an AI. Everything is plain, repeatable arithmetic.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from core.gatekeeper import evaluate_numeric
from core.rules_ir import Enumeration, Reading
from core.schemas import TelemetryMetrics


class Cell(BaseModel):
    reading_id: str
    scenario: str
    status: str  # COMPLIANT | BREACH | UNDETERMINED
    penalty_usd: float = 0.0
    penalty_basis: Optional[str] = None
    needs_commercial_review: bool = False
    credit_percent: Optional[float] = None
    uptime_shortfall: float = 0.0
    resolution_excess: float = 0.0
    mean_uptime_percent: Optional[float] = None
    max_resolution_hours: Optional[float] = None
    max_resolution_log_id: Optional[int] = None
    note: str = ""


class Decision(BaseModel):
    status: str
    reason: str
    penalty_usd: float = 0.0
    penalty_basis: Optional[str] = None
    needs_commercial_review: bool = False
    exposure_min_usd: Optional[float] = None
    exposure_max_usd: Optional[float] = None
    data_grade: str = "A"
    # Why a decision was escalated: no_reading | no_data | data_quality | flip ("" when automated)
    kind: str = ""
    trace: List[str] = Field(default_factory=list)


# views_for(scope_severities) -> {"strict": metrics, "inclusive": metrics}
ViewFn = Callable[[Optional[Tuple[str, ...]]], Dict[str, TelemetryMetrics]]


def run_grid(readings: List[Reading], views_for: ViewFn) -> List[Cell]:
    cells: List[Cell] = []
    for reading in readings:
        scope = tuple(reading.rules.scope_severities) if reading.rules.scope_severities else None
        for scenario, metrics in views_for(scope).items():
            out = evaluate_numeric(reading.rules, metrics)
            cells.append(Cell(
                reading_id=reading.reading_id, scenario=scenario, status=out.status,
                penalty_usd=out.penalty_usd, penalty_basis=out.penalty_basis,
                needs_commercial_review=out.needs_commercial_review, credit_percent=out.credit_percent,
                uptime_shortfall=out.uptime_shortfall, resolution_excess=out.resolution_excess,
                mean_uptime_percent=metrics.mean_uptime_percent,
                max_resolution_hours=metrics.max_resolution_hours,
                max_resolution_log_id=metrics.max_resolution_log_id, note=out.note,
            ))
    return cells


def _dollar_range(cells: List[Cell]) -> Tuple[Optional[float], Optional[float]]:
    amounts = [c.penalty_usd if c.status == "BREACH" else 0.0 for c in cells if c.status != "UNDETERMINED"]
    return (min(amounts), max(amounts)) if amounts else (None, None)


def decide(enum: Enumeration, cells: List[Cell], grades: List[str]) -> Decision:
    """Turn the grid into one decision. Anything doubtful is escalated, never guessed."""
    grade = max(grades) if grades else "D"  # "D" > "A" alphabetically = worst grade
    trace = [f"Envelope: {len(enum.readings)} reading(s) x {len(set(c.scenario for c in cells))} data view(s) = {len(cells)} cell(s); data grade {grade}"]
    for note in enum.notes:
        trace.append(f"Reading note: {note}")

    if enum.blockers or not enum.readings:
        why = "; ".join(enum.blockers) or "no evaluable reading of the contract"
        trace.append("Gate: no evaluable reading")
        return Decision(status="ESCALATED", reason=f"Hard Gate: {why}", trace=trace, data_grade=grade, kind="no_reading")

    if all(r.rules.required_uptime_percent is None and r.rules.max_resolution_hours is None for r in enum.readings):
        trace.append("Gate: no numeric threshold found")
        return Decision(status="ESCALATED", reason="Hard Gate: no numeric SLA threshold could be read from the contract text",
                        trace=trace, data_grade=grade, kind="no_reading")

    lo, hi = _dollar_range(cells)
    if all(c.note == "no usable telemetry" for c in cells):
        trace.append("Gate: no usable telemetry")
        return Decision(status="ESCALATED", reason="Hard Gate: no usable telemetry for contract", trace=trace, data_grade=grade, kind="no_data")
    if grade == "D":
        trace.append("Gate: telemetry quality grade D")
        return Decision(status="ESCALATED", reason="Hard Gate: telemetry is mostly unusable (quality grade D)", trace=trace, data_grade=grade, kind="data_quality")

    statuses = {c.status for c in cells}
    if len(statuses) == 1 and statuses <= {"COMPLIANT", "BREACH"}:
        status = statuses.pop()
        multi = len(enum.readings) > 1
        trace.append(f"Invariant: every cell says {status}")
        if status == "COMPLIANT":
            return Decision(status=status, reason=("All numeric SLA thresholds satisfied" +
                            (" under every reading and data view" if multi else "")), exposure_min_usd=0.0,
                            exposure_max_usd=0.0, trace=trace, data_grade=grade)
        bases = {c.penalty_basis for c in cells}
        basis = bases.pop() if len(bases) == 1 else "mixed"
        review = any(c.needs_commercial_review for c in cells) or lo != hi
        worst = max(cells, key=lambda c: (c.uptime_shortfall, c.resolution_excess))
        parts = []
        if worst.uptime_shortfall > 0:
            parts.append(f"uptime shortfall {worst.uptime_shortfall:.2f} percentage points")
        if worst.resolution_excess > 0:
            parts.append(f"resolution excess {worst.resolution_excess:.2f} hours")
        reason = "SLA breach: " + "; ".join(parts)
        if multi:
            reason += " (holds under every reading and data view)"
        return Decision(status=status, reason=reason, penalty_usd=lo or 0.0, penalty_basis=basis,
                        needs_commercial_review=review, exposure_min_usd=lo, exposure_max_usd=hi,
                        trace=trace, data_grade=grade)

    flips = sorted(statuses)
    trace.append("Flip: outcomes differ across readings/data views: " + ", ".join(flips))
    reasons = []
    by_reading = {r.reading_id: {c.status for c in cells if c.reading_id == r.reading_id} for r in enum.readings}
    if len(enum.readings) > 1 and len({frozenset(v) for v in by_reading.values()} | {frozenset(set.union(*by_reading.values()))}) > 1:
        reasons.append("outcome depends on how the contract is read")
    if any(len(v) > 1 for v in by_reading.values()):
        reasons.append("outcome depends on whether doubtful telemetry rows are trusted")
    if "UNDETERMINED" in statuses and not reasons:
        reasons.append("a threshold is vague or missing and the clear terms are satisfied")
    elif "UNDETERMINED" in statuses:
        reasons.append("some readings leave a threshold unchecked")
    return Decision(status="ESCALATED", reason="Hard Gate: " + "; ".join(reasons or ["outcome is not stable"]),
                    exposure_min_usd=lo, exposure_max_usd=hi, trace=trace, data_grade=grade, kind="flip")


def envelope_dict(enum: Enumeration, cells: List[Cell], decision: Decision) -> dict:
    """Plain-dict form stored in results and proof bundles (and drawn by the UI)."""
    return {
        "fingerprint": enum.fingerprint,
        "notes": enum.notes,
        "blockers": enum.blockers,
        "data_grade": decision.data_grade,
        "readings": [
            {"reading_id": r.reading_id, "assumptions": r.assumptions,
             "uptime_threshold": r.rules.required_uptime_percent,
             "resolution_threshold_hours": r.rules.max_resolution_hours,
             "scope_severities": r.rules.scope_severities,
             "penalty_usd_per_point": r.rules.penalty_usd_per_point}
            for r in enum.readings
        ],
        "cells": [c.model_dump() for c in cells],
    }


def resolve_decision(enum: Enumeration, cells: List[Cell], grades: List[str],
                     reading_id: str, scenario: Optional[str] = None) -> Decision:
    """
    A reviewer has ruled that one reading governs (and optionally which data
    view to trust). Re-run the same decision rule on just that part of the grid.
    Still escalates if the remaining cells do not agree, or the data is grade D.
    """
    chosen = [r for r in enum.readings if r.reading_id == reading_id]
    if not chosen:
        raise ValueError(f"unknown reading {reading_id}")
    kept = [c for c in cells if c.reading_id == reading_id and (scenario is None or c.scenario == scenario)]
    narrowed = enum.model_copy(update={"readings": chosen, "notes": list(enum.notes) + [
        f"Reviewer ruled that {reading_id} governs" + (f" and trusted the '{scenario}' data view" if scenario else "")]})
    return decide(narrowed, kept, grades)
