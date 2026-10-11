"""Property tests over a freshly generated labelled dataset (truth known by construction)."""
import pytest

from core.pipeline import PipelineConfig, audit_contract
from data_gen.generate import generate


@pytest.fixture(scope="module")
def dataset():
    contracts, df, truths = generate(70, seed=11)
    return contracts, df, {t["contract_id"]: t for t in truths}


def test_generation_is_deterministic():
    a = generate(30, seed=3)
    b = generate(30, seed=3)
    assert a[0] == b[0] and a[1].equals(b[1]) and a[2] == b[2]


def test_contract_files_never_contain_the_answers(dataset):
    contracts, _, _ = dataset
    forbidden = {"expected_status", "expected_penalty_usd", "category", "thresholds", "uptime_target_percent"}
    assert all(not (forbidden & set(c)) for c in contracts)


def test_telemetry_looks_dirty_and_timestamped(dataset):
    _, df, _ = dataset
    assert {"event_start", "downtime_minutes", "planned_maintenance", "vehicle_class"} <= set(df.columns)
    assert (df["actual_uptime_percent"] > 100).any() and (df["sensor_status"] == "FAULT").any()
    assert df["resolution_hours"].isna().any()


def test_no_false_automation_and_clear_cases_are_right(dataset):
    contracts, df, truth = dataset
    cfg = PipelineConfig()
    false_auto, clear_wrong = [], []
    for item in contracts:
        t = truth[item["contract_id"]]
        r = audit_contract(item, df, cfg)
        if r.status != "ESCALATED" and r.status != t["expected_status"]:
            false_auto.append((item["contract_id"], t["category"], r.status, t["expected_status"]))
        if not t["hard_for_regex"] and r.status != t["expected_status"]:
            clear_wrong.append((item["contract_id"], t["category"], r.status, t["expected_status"]))
        if r.status == "ESCALATED":
            assert r.penalty_usd == 0.0 and r.brief
        if r.status == "BREACH" and t["expected_penalty_usd"] is not None:
            assert r.penalty_usd == pytest.approx(t["expected_penalty_usd"], abs=1.0)
    assert not false_auto, false_auto
    assert not clear_wrong, clear_wrong
