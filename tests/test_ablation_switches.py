"""The experiment switches must really change behaviour, or the ablation numbers mean nothing."""
from core.pipeline import PipelineConfig
from eval.ablation import misread_numbers
from tests.helpers import audit, contract, telemetry

CONFLICT = ("Monthly fleet uptime shall be maintained at not less than 98%. Each Critical Event shall be restored within 12 hours. "
            "ADDENDUM A. The restoration window for Critical Events is set at 24 hours.")
CLEAN = "Monthly fleet uptime shall be maintained at not less than 98%. Each Critical Event shall be restored within 12 hours."


def df(res):
    return telemetry("T", [{"up": 99.5, "res": res}, {"up": 99.4, "res": 2}])


def test_without_the_envelope_a_decisive_contradiction_is_silently_guessed():
    item = contract("T", CONFLICT)
    assert audit(item, df(18)).status == "ESCALATED"
    guessed = audit(item, df(18), PipelineConfig(ablate=frozenset({"envelope"})))
    assert guessed.status in ("COMPLIANT", "BREACH")  # picks one reading and automates it


def test_without_the_scope_filter_a_slow_minor_event_becomes_a_breach():
    rows = telemetry("T", [{"up": 99.5, "res": 3}, {"up": 99.5, "res": 60, "sev": "medium", "event": "tire_wear", "truck": "TRK-9"}])
    item = contract("T", CLEAN)
    assert audit(item, rows).status == "COMPLIANT"
    assert audit(item, rows, PipelineConfig(ablate=frozenset({"scope"}))).status == "BREACH"


def test_without_data_views_a_decisive_faulty_row_is_trusted():
    rows = telemetry("T", [{"up": 99.5, "res": 3}, {"up": 99.5, "res": 40, "status": "FAULT", "truck": "TRK-X"}])
    item = contract("T", CLEAN)
    assert audit(item, rows).status == "ESCALATED"
    assert audit(item, rows, PipelineConfig(ablate=frozenset({"data_views"}))).status == "BREACH"


def test_verifier_blocks_misread_numbers_but_ablating_it_lets_them_through():
    item, rows = contract("T", CLEAN), df(10)  # truly compliant: 10h <= 12h
    safe = audit(item, rows, PipelineConfig(reader_hook=misread_numbers))
    assert safe.status == "ESCALATED"  # the misread numbers fail verification, so nothing is automated
    unsafe = audit(item, rows, PipelineConfig(reader_hook=misread_numbers, ablate=frozenset({"verifier"})))
    assert unsafe.status in ("COMPLIANT", "BREACH")  # the wrong numbers were used to automate a verdict


def test_defaults_change_nothing():
    assert PipelineConfig().ablate == frozenset() and PipelineConfig().reader_hook is None
