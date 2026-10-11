"""Tiered credits, uptime-only profiles and real-document table styles (wording paraphrased, not copied)."""
import pytest

from agents.clause_reader import read_clauses_regex
from agents.quote_verifier import verify_extraction
from core.ledger import Ledger
from core.pipeline import PipelineConfig, audit_contract
from core.rules_ir import ClauseExtraction
from core.schemas import CreditTier
from core.verify_proof import verify_bundle
from tests.helpers import audit, telemetry

ONE_TARGET = """The Provider shall make the service available with a Monthly Uptime Percentage of at least 99.9%.
Monthly Uptime Percentage | Service Credit Percentage
Less than 99.9% but equal to or greater than 99.0% | 10%
Less than 99.0% but equal to or greater than 95.0% | 30%
Less than 95.0% | 100%"""

TWO_TARGETS = """Regional deployments shall have a Monthly Uptime Percentage of at least 99.99%.
Less than 99.99% but equal to or greater than 99.0% | 10%
Less than 99.0% but equal to or greater than 95.0% | 30%
Less than 95.0% | 100%
A single instance shall have an Instance Uptime Percentage of at least 99.5%.
Less than 99.5% but equal to or greater than 99.0% | 10%
Less than 99.0% but equal to or greater than 95.0% | 30%
Less than 95.0% | 100%"""


def item(text, fee=10000, checks=("uptime",)):
    d = {"contract_id": "T", "contract_text": text, "monthly_fee_usd": fee}
    if checks is not None:
        d["sla_profile"] = {"checks": list(checks)}
    return d


def fleet(uptime):
    return telemetry("T", [{"up": uptime, "res": 1, "truck": f"TRK-{i}"} for i in range(1, 5)])


def test_compliant_when_target_met():
    assert audit(item(ONE_TARGET), fleet(99.95)).status == "COMPLIANT"


@pytest.mark.parametrize("uptime,credit", [(99.5, 10), (97.0, 30), (90.0, 100)])
def test_credit_is_the_tier_percentage_of_the_monthly_fee(uptime, credit):
    r = audit(item(ONE_TARGET), fleet(uptime))
    assert r.status == "BREACH"
    assert r.penalty_usd == pytest.approx(10000 * credit / 100)
    assert r.penalty_basis == "contract_tiers" and not r.needs_commercial_review


def test_unknown_fee_never_becomes_a_number():
    r = audit(item(ONE_TARGET, fee=None), fleet(97.0))
    assert r.status == "BREACH" and r.penalty_usd == 0.0
    assert r.penalty_basis == "fee_unknown" and r.needs_commercial_review


def test_uptime_only_profile_does_not_wait_for_a_repair_clause():
    assert audit(item(ONE_TARGET), fleet(99.95)).status == "COMPLIANT"
    # without the profile, the missing repair-time clause cannot be assumed away
    assert audit(item(ONE_TARGET, checks=None), fleet(99.95)).status == "ESCALATED"


def test_two_commitments_escalate_when_they_disagree_and_automate_when_they_agree():
    between = audit(item(TWO_TARGETS), fleet(99.7))  # breaks 99.99 but meets 99.5
    assert between.status == "ESCALATED"
    assert (between.exposure_min_usd, between.exposure_max_usd) == (0.0, 1000.0)
    both = audit(item(TWO_TARGETS), fleet(97.0))  # breaks both, 30% under each table
    assert both.status == "BREACH" and both.penalty_usd == 3000.0 and not both.needs_commercial_review


def test_a_credit_table_with_a_missing_row_is_never_read_as_zero():
    gappy = ONE_TARGET.replace("Less than 99.0% but equal to or greater than 95.0% | 30%\n", "")
    r = audit(item(gappy), fleet(97.0))
    assert r.status == "BREACH" and r.penalty_usd == 0.0
    assert r.penalty_basis == "tier_not_found" and r.needs_commercial_review


def test_definition_sentence_is_not_a_target():
    text = ONE_TARGET + "\nMonthly Uptime Percentage is calculated by subtracting from 100% the share of minutes of downtime."
    assert sorted(c.value for c in read_clauses_regex(item(text)).uptime_candidates) == [99.9]


def test_range_style_credit_rows_and_target_rows():
    text = """Covered Service | Monthly Uptime Percentage
Instances in Multiple Zones | >= 99.99%
Single Instance | >= 99.9%
99.00% - < 99.99% | 10%
95.00% - < 99.00% | 25%
< 95.00% | 100%"""
    e = read_clauses_regex(item(text))
    assert sorted(c.value for c in e.uptime_candidates) == [99.9, 99.99]
    assert [(t.min_uptime, t.max_uptime, t.credit_percent) for t in e.credit_tiers] == [(99.0, 99.99, 10), (95.0, 99.0, 25), (None, 95.0, 100)]


def test_verifier_drops_a_tier_whose_quote_does_not_contain_its_numbers():
    ext = ClauseExtraction(contract_id="T", credit_tiers=[
        CreditTier(max_uptime=99.0, credit_percent=30, quote="Less than 99.0% | 30%"),
        CreditTier(max_uptime=99.0, credit_percent=55, quote="Less than 99.0% | 30%"),  # invented 55
    ])
    kept, rejected = verify_extraction(ext, "Less than 99.0% | 30%")
    assert [t.credit_percent for t in kept.credit_tiers] == [30] and rejected


def test_document_with_no_readable_threshold_says_so():
    r = audit(item("This agreement describes a service. Fees are payable monthly."), fleet(99.0))
    assert r.status == "ESCALATED" and "no numeric SLA threshold" in r.reason


def test_tiered_verdict_is_replayable(tmp_path):
    cfg = PipelineConfig(ledger=Ledger(tmp_path))
    it, df = item(TWO_TARGETS), fleet(99.7)
    r = audit_contract(it, df, cfg)
    ok, report = verify_bundle(cfg.ledger.load_proof(r.proof_id), TWO_TARGETS, df)
    assert ok, report


def test_tier_boundaries_and_hedged_commitments_are_not_misread():
    from core.rules_ir import Candidate
    row = "Less than 99.9% but equal to or greater than 99.0% | 10%"
    commit = "We will use commercially reasonable efforts to make the service available with a Monthly Uptime Percentage of at least 99.9%."
    ai = ClauseExtraction(
        contract_id="T",
        uptime_candidates=[Candidate(value=99.9, quote=commit), Candidate(value=99.0, quote=row)],  # 99.0 is a tier bound
        vague_terms=[commit],  # states a number, so not vague
        credit_tiers=[CreditTier(min_uptime=99.0, max_uptime=99.9, credit_percent=10, quote=row)],
    )
    kept, _ = verify_extraction(ai, commit + " " + row)
    assert [c.value for c in kept.uptime_candidates] == [99.9]
    assert kept.vague_terms == []


def test_percent_of_fee_sla_without_a_readable_table_never_gets_a_dollar_rate():
    text = "The Provider shall make the service available with a Monthly Uptime Percentage of at least 99.9%."
    r = audit(item(text), fleet(98.0))
    assert r.status == "BREACH" and r.penalty_usd == 0.0
    assert r.penalty_basis == "tier_not_found" and r.needs_commercial_review


def test_boilerplate_labelled_vague_by_a_reader_is_ignored_but_real_hedging_is_kept():
    ai = ClauseExtraction(contract_id="T", vague_terms=[
        "The SLA does not apply to any unavailability caused by factors outside the provider's control.",  # exclusion, no hedge
        "Faults shall be addressed in a timely, industry-standard manner.",  # genuine hedge
    ])
    kept, _ = verify_extraction(ai, " ".join(ai.vague_terms))
    assert kept.vague_terms == ["Faults shall be addressed in a timely, industry-standard manner."]
