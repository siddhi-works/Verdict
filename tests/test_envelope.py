"""The core promise: automate only what never flips; escalate everything else."""
from tests.helpers import audit, contract, telemetry

UP = "Monthly fleet uptime shall be maintained at not less than 98%. "
CONFLICT = (UP + "Each Critical Event shall be restored within 12 hours. "
            "ADDENDUM A. The restoration window for Critical Events is set at 24 hours.")


def ok_fleet(res):
    return telemetry("T", [{"up": 99.5, "res": res}, {"up": 99.4, "res": 2}])


def test_contradiction_that_cannot_change_the_outcome_is_automated_as_breach():
    result = audit(contract("T", CONFLICT), ok_fleet(30))  # 30h breaks both 12h and 24h
    assert result.status == "BREACH" and "every reading" in result.reason


def test_contradiction_that_cannot_change_the_outcome_is_automated_as_compliant():
    assert audit(contract("T", CONFLICT), ok_fleet(5)).status == "COMPLIANT"


def test_decisive_contradiction_escalates_with_a_brief_and_dollar_range():
    result = audit(contract("T", CONFLICT), ok_fleet(18))  # breach at 12h, fine at 24h
    assert result.status == "ESCALATED"
    assert "R1" in result.brief and "R2" in result.brief and "BREACH" in result.brief
    assert result.penalty_usd == 0.0  # an escalation never carries an automated amount
    assert result.exposure_min_usd == 0.0 and result.exposure_max_usd is not None


def test_vague_term_with_clearly_breached_clear_term_is_still_a_breach():
    text = UP + "Faults shall be addressed in a timely, industry-standard manner."
    df = telemetry("T", [{"up": 95.0, "res": 3}])
    assert audit(contract("T", text), df).status == "BREACH"


def test_vague_term_with_satisfied_clear_term_escalates():
    text = UP + "Faults shall be addressed in a timely, industry-standard manner."
    assert audit(contract("T", text), telemetry("T", [{"up": 99.5, "res": 3}])).status == "ESCALATED"


def test_doubtful_telemetry_that_decides_the_outcome_escalates():
    text = UP + "Each Critical Event shall be restored within 12 hours."
    df = telemetry("T", [
        {"up": 99.5, "res": 3},
        {"up": 99.5, "res": 40, "status": "FAULT", "truck": "TRK-X"},  # only trusted if the FAULT row is believed
    ])
    result = audit(contract("T", text), df)
    assert result.status == "ESCALATED" and "telemetry" in result.reason


def test_doubtful_telemetry_that_cannot_decide_the_outcome_does_not_block():
    text = UP + "Each Critical Event shall be restored within 12 hours."
    df = telemetry("T", [{"up": 99.5, "res": 3}, {"up": 99.5, "res": 4, "status": "FAULT", "truck": "TRK-X"}])
    assert audit(contract("T", text), df).status == "COMPLIANT"


def test_mostly_garbage_telemetry_is_grade_d_and_escalates():
    rows = [{"up": 99.5, "res": 3}] + [{"up": 150.0, "res": 2, "truck": f"TRK-{i}"} for i in range(2, 6)]
    result = audit(contract("T", UP + "Critical Events restored within 12 hours."), telemetry("T", rows))
    assert result.status == "ESCALATED" and "grade D" in result.reason


def test_planned_maintenance_is_excluded_from_repair_time():
    text = UP + "Each Critical Event shall be restored within 12 hours."
    df = telemetry("T", [{"up": 99.5, "res": 3}, {"up": 99.5, "res": 50, "planned": True, "truck": "TRK-P"}])
    assert audit(contract("T", text), df).status == "COMPLIANT"


def test_no_threshold_found_anywhere_escalates():
    result = audit(contract("T", "The Provider shall do its best."), telemetry("T", [{"up": 99.5, "res": 3}]))
    assert result.status == "ESCALATED" and result.penalty_usd == 0.0
