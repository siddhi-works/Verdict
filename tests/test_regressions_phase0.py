"""Regression tests for the verdict-changing bugs found in the first review."""
import pandas as pd

from agents.parser import parse_telemetry
from core.gatekeeper import evaluate_gate
from core.schemas import SLARules, TelemetryMetrics
from tests.helpers import CLEAN, audit, contract, telemetry


def rules(**kw):
    base = dict(contract_id="T", required_uptime_percent=99.0, max_resolution_hours=12)
    base.update(kw)
    return SLARules(**base)


def test_no_telemetry_never_produces_a_penalty():
    # Before the fix: mean uptime None -> treated as 0% -> BREACH with a 99,000 USD penalty.
    empty = parse_telemetry("T", pd.DataFrame(columns=["contract_id", "resolution_hours", "actual_uptime_percent", "sensor_status"]))
    result = evaluate_gate(rules(), empty)
    assert result.status == "ESCALATED"
    assert result.penalty_usd == 0.0
    assert "no usable telemetry" in result.reason.lower()


def test_pipeline_escalates_clean_contract_with_no_rows():
    result = audit(contract("T", CLEAN), telemetry("OTHER", [{"up": 99.0, "res": 1}]))
    assert result.status == "ESCALATED" and result.penalty_usd == 0.0


def test_scope_filter_ignores_slow_minor_events():
    df = telemetry("T", [
        {"up": 99.0, "res": 4, "sev": "critical"},
        {"up": 99.0, "res": 40, "sev": "medium", "event": "tire_wear"},
    ])
    assert parse_telemetry("T", df, severities=["critical"]).max_resolution_hours == 4
    assert parse_telemetry("T", df).max_resolution_hours == 40


def test_slow_noncritical_event_is_not_a_breach_when_contract_covers_critical_only():
    df = telemetry("T", [{"up": 99.0, "res": 4}, {"up": 99.0, "res": 70, "sev": "medium", "event": "tire_wear"}])
    assert audit(contract("T", CLEAN), df).status == "COMPLIANT"


def test_assumed_penalty_rate_is_flagged_for_review():
    m = TelemetryMetrics(contract_id="T", mean_uptime_percent=98.0, max_resolution_hours=1.0, valid_log_count=3, log_count=3)
    result = evaluate_gate(rules(), m)
    assert result.status == "BREACH" and result.penalty_basis == "assumed_default_rate" and result.needs_commercial_review


def test_contract_stated_rate_is_trusted():
    text = CLEAN + " Service credits accrue at $400 per percentage point of uptime shortfall in the affected month."
    df = telemetry("T", [{"up": 97.0, "res": 3}, {"up": 97.0, "res": 2}])
    result = audit(contract("T", text), df)
    assert result.status == "BREACH"
    assert result.penalty_usd == 400.0 and result.penalty_basis == "contract_clause" and not result.needs_commercial_review


def test_repair_only_breach_has_no_invented_amount():
    df = telemetry("T", [{"up": 99.5, "res": 30}])
    result = audit(contract("T", CLEAN), df)
    assert result.status == "BREACH" and result.penalty_usd == 0.0
    assert result.penalty_basis == "not_quantifiable" and result.needs_commercial_review


def test_fleet_uptime_weights_trucks_equally():
    # truck A has 5 events at 90%, truck B has 1 event at 100%: fleet mean is 95, not 91.7
    rows = [{"truck": "A", "up": 90.0, "res": 1} for _ in range(5)] + [{"truck": "B", "up": 100.0, "res": 1}]
    assert parse_telemetry("T", telemetry("T", rows)).mean_uptime_percent == 95.0
