from dataclasses import dataclass
from typing import Optional

from .schemas import SLARules, TelemetryMetrics, AuditResult

# Used ONLY when the contract states no penalty of its own. Any penalty computed
# from it is flagged "assumed_default_rate" and routed to commercial review.
DEFAULT_PENALTY_USD_PER_POINT = 1000.0


@dataclass
class NumericOutcome:
    """Result of comparing ONE set of thresholds with ONE view of the data."""
    status: str  # COMPLIANT | BREACH | UNDETERMINED
    uptime_shortfall: float = 0.0
    resolution_excess: float = 0.0
    penalty_usd: float = 0.0
    penalty_basis: Optional[str] = None
    needs_commercial_review: bool = False
    credit_percent: Optional[float] = None
    note: str = ""


def tier_credit_percent(tiers, uptime: float):
    """Credit percent of the tier whose range [min, max) contains `uptime`, or None if no row covers it."""
    for t in tiers:
        lo = float("-inf") if t.min_uptime is None else t.min_uptime
        hi = float("inf") if t.max_uptime is None else t.max_uptime
        if lo <= uptime < hi:
            return t.credit_percent
    return None


def evaluate_numeric(rules: SLARules, metrics: TelemetryMetrics) -> NumericOutcome:
    """
    Pure number comparison, no routing. A threshold that is None (vague or
    missing in the contract) is simply not checked; if nothing else is
    breached the outcome is UNDETERMINED, because the unchecked clause could
    still have been violated. Missing metrics are never treated as zero.
    """
    if metrics.valid_log_count == 0 or metrics.mean_uptime_percent is None:
        return NumericOutcome(status="UNDETERMINED", note="no usable telemetry")

    shortfall = 0.0
    if rules.uptime_applicable and rules.required_uptime_percent is not None:
        shortfall = max(0.0, rules.required_uptime_percent - metrics.mean_uptime_percent)
    excess = 0.0
    if rules.resolution_applicable and rules.max_resolution_hours is not None and metrics.max_resolution_hours is not None:
        excess = max(0.0, metrics.max_resolution_hours - rules.max_resolution_hours)

    if shortfall > 0 or excess > 0:
        if rules.credit_tiers and shortfall > 0:
            pct = tier_credit_percent(rules.credit_tiers, metrics.mean_uptime_percent)
            if pct is None:  # the credit table we read has no row for this uptime: never guess zero
                return NumericOutcome("BREACH", shortfall, excess, 0.0, "tier_not_found", True, None)
            if rules.monthly_fee_usd is None:  # the credit is a percent of a fee we were not given
                return NumericOutcome("BREACH", shortfall, excess, 0.0, "fee_unknown", True, pct)
            return NumericOutcome("BREACH", shortfall, excess, round(pct / 100.0 * rules.monthly_fee_usd, 2),
                                  "contract_tiers", False, pct)
        if shortfall > 0 and rules.monthly_fee_usd is not None and rules.penalty_usd_per_point is None:
            # A percent-of-fee SLA whose credit table could not be read: any dollar figure would be invented.
            return NumericOutcome("BREACH", shortfall, excess, 0.0, "tier_not_found", True, None)
        rate, basis = rules.penalty_usd_per_point, "contract_clause"
        if rate is None:
            rate, basis = DEFAULT_PENALTY_USD_PER_POINT, "assumed_default_rate"
        penalty = round(shortfall * rate, 2)
        review = basis != "contract_clause"
        if shortfall == 0:  # only repair time was missed; no uptime-based amount exists
            basis, review = "not_quantifiable", True
        return NumericOutcome("BREACH", shortfall, excess, penalty, basis, review)

    if (rules.uptime_applicable and rules.required_uptime_percent is None) or             (rules.resolution_applicable and rules.max_resolution_hours is None):
        return NumericOutcome(status="UNDETERMINED", note="a threshold is vague or missing")
    return NumericOutcome(status="COMPLIANT")


def evaluate_gate(rules: SLARules, metrics: TelemetryMetrics) -> AuditResult:
    """Single-reading gate (legacy path). The envelope in core/envelope.py generalises it."""
    trace = []
    if rules.conflict_detected:
        trace.append("Gate 1: contract contradiction detected")
        return AuditResult(contract_id=rules.contract_id, status="ESCALATED", reason="Hard Gate: contract contradiction detected", execution_trace=trace)
    if rules.ambiguity_detected or rules.required_uptime_percent is None or rules.max_resolution_hours is None:
        trace.append("Gate 2: ambiguous or missing SLA threshold")
        return AuditResult(contract_id=rules.contract_id, status="ESCALATED", reason="Hard Gate: ambiguous or missing SLA threshold", execution_trace=trace)
    if metrics.valid_log_count == 0:
        # No usable telemetry (all corrupted, or none at all): there is nothing to
        # judge the contract against, so a number must never be made up.
        if metrics.corrupted_sensor_flag:
            trace.append("Gate 3: corrupted sensor data detected (no valid telemetry remains)")
            return AuditResult(contract_id=rules.contract_id, status="ESCALATED", reason="Hard Gate: corrupted sensor data detected", execution_trace=trace)
        trace.append("Gate 3: no telemetry available for this contract")
        return AuditResult(contract_id=rules.contract_id, status="ESCALATED", reason="Hard Gate: no usable telemetry for contract", execution_trace=trace)
    if metrics.corrupted_sensor_flag:
        # Some rows were corrupted and excluded, but enough valid telemetry
        # remains to evaluate the contract on its own merits.
        trace.append(f"Note: corrupted telemetry excluded from evaluation ({metrics.corruption_notes})")
    trace.append("Gate 4: numerical SLA evaluation")
    out = evaluate_numeric(rules, metrics)
    if out.status == "BREACH":
        trace.append(f"SLA comparison: uptime shortfall={out.uptime_shortfall:.2f}, resolution excess={out.resolution_excess:.2f}")
        return AuditResult(
            contract_id=rules.contract_id, status="BREACH",
            reason=f"SLA breach: uptime shortfall {out.uptime_shortfall:.2f} percentage points; resolution excess {out.resolution_excess:.2f} hours",
            penalty_usd=out.penalty_usd, penalty_basis=out.penalty_basis,
            needs_commercial_review=out.needs_commercial_review, execution_trace=trace,
        )
    trace.append("SLA thresholds satisfied")
    return AuditResult(contract_id=rules.contract_id, status="COMPLIANT", reason="All numeric SLA thresholds satisfied", execution_trace=trace)
