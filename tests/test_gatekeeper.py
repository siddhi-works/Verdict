"""
tests/test_gatekeeper.py

Covers every branch of the hard-gate router: the three ESCALATED gates
(contract conflict, ambiguity/missing thresholds, corrupted sensor), plus
COMPLIANT and BREACH (with penalty math) on the happy path. Gate order is
also checked explicitly, since a mis-ordered gate could mask one failure
mode behind another.
"""
from core.gatekeeper import evaluate_gate
from core.schemas import SLARules, TelemetryMetrics


def make_rules(**overrides):
    defaults = dict(
        contract_id="TEST-1",
        required_uptime_percent=99.0,
        max_resolution_hours=6,
        conflict_detected=False,
        ambiguity_detected=False,
        ambiguity_notes=None,
    )
    defaults.update(overrides)
    return SLARules(**defaults)


def make_metrics(**overrides):
    defaults = dict(
        contract_id="TEST-1",
        mean_uptime_percent=99.5,
        max_resolution_hours=4.0,
        corrupted_sensor_flag=False,
        valid_log_count=4,
        log_count=4,
    )
    defaults.update(overrides)
    return TelemetryMetrics(**defaults)


def test_compliant_when_all_thresholds_met():
    result = evaluate_gate(make_rules(), make_metrics())
    assert result.status == "COMPLIANT"
    assert result.penalty_usd == 0.0


def test_breach_on_uptime_shortfall_computes_penalty():
    rules = make_rules(required_uptime_percent=99.0)
    metrics = make_metrics(mean_uptime_percent=98.15, max_resolution_hours=7.5)
    result = evaluate_gate(rules, metrics)
    assert result.status == "BREACH"
    assert result.penalty_usd == 850.0  # (99.0 - 98.15) * 1000


def test_breach_on_resolution_time_only_floors_penalty_at_zero():
    # Uptime is fine (no shortfall) but resolution time is blown -> penalty
    # formula would go negative; must be floored at 0, not reported negative.
    rules = make_rules(required_uptime_percent=99.0, max_resolution_hours=4)
    metrics = make_metrics(mean_uptime_percent=99.9, max_resolution_hours=10.0)
    result = evaluate_gate(rules, metrics)
    assert result.status == "BREACH"
    assert result.penalty_usd == 0.0


def test_gate1_conflict_escalates_even_with_clean_metrics():
    rules = make_rules(conflict_detected=True)
    result = evaluate_gate(rules, make_metrics())
    assert result.status == "ESCALATED"
    assert "contradiction" in result.reason.lower()


def test_gate2_ambiguity_flag_escalates():
    rules = make_rules(ambiguity_detected=True)
    result = evaluate_gate(rules, make_metrics())
    assert result.status == "ESCALATED"
    assert "ambiguous" in result.reason.lower()


def test_gate2_missing_threshold_escalates_even_without_explicit_flag():
    # ambiguity_detected is False, but a None threshold alone must still trip Gate 2.
    rules = make_rules(ambiguity_detected=False, max_resolution_hours=None)
    result = evaluate_gate(rules, make_metrics())
    assert result.status == "ESCALATED"
    assert "ambiguous" in result.reason.lower()


def test_gate3_corrupted_sensor_escalates_even_with_clean_contract():
    # Total corruption: no valid telemetry rows survive -> must escalate,
    # since there's nothing left to evaluate the contract against.
    metrics = make_metrics(corrupted_sensor_flag=True, valid_log_count=0, log_count=4)
    result = evaluate_gate(make_rules(), metrics)
    assert result.status == "ESCALATED"
    assert "sensor" in result.reason.lower()


def test_gate3_partial_corruption_does_not_escalate_when_valid_data_remains():
    # A single bad sensor row (e.g. a FAULT-status reading) shouldn't poison
    # an otherwise-compliant contract when other valid telemetry exists to
    # evaluate it against. Regression test for the C-001/TRK-44 bug.
    metrics = make_metrics(corrupted_sensor_flag=True, valid_log_count=2, log_count=3)
    result = evaluate_gate(make_rules(), metrics)
    assert result.status == "COMPLIANT"
    assert any("corrupted" in step.lower() for step in result.execution_trace)


def test_gate_priority_conflict_beats_sensor_fault():
    # If both a contract conflict AND a sensor fault are present, Gate 1
    # (contract-level) must win the routing decision, not Gate 3.
    rules = make_rules(conflict_detected=True)
    metrics = make_metrics(corrupted_sensor_flag=True)
    result = evaluate_gate(rules, metrics)
    assert "contradiction" in result.reason.lower()


def test_execution_trace_is_populated():
    result = evaluate_gate(make_rules(), make_metrics())
    assert len(result.execution_trace) > 0