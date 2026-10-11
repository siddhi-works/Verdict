import json

import pytest

from core.brief import polish_brief
from core.ledger import Ledger
from core.outbox import LogConnector, dispatch, plan_actions
from core.pipeline import PipelineConfig, audit_contract, resolve_escalation
from core.precedents import PrecedentStore
from core.schemas import AuditResult
from core.verify_proof import verify_bundle
from tests.helpers import contract, telemetry

TEXT = ("Monthly fleet uptime shall be maintained at not less than 98%. Each Critical Event shall be restored within 12 hours. "
        "ADDENDUM A. The restoration window for Critical Events is set at 24 hours.")


@pytest.fixture
def cfg(tmp_path):
    return PipelineConfig(ledger=Ledger(tmp_path / "ledger"), precedents=PrecedentStore(tmp_path / "precedents.json"))


DF = telemetry("T", [{"up": 99.5, "res": 18}, {"up": 99.4, "res": 2}])  # breach at 12h, fine at 24h
ITEM = contract("T", TEXT)


def test_sealed_verdict_can_be_replayed_with_no_ai(cfg):
    result = audit_contract(ITEM, DF, cfg)
    ok, report = verify_bundle(cfg.ledger.load_proof(result.proof_id), TEXT, DF)
    assert ok, report
    assert cfg.ledger.verify_chain() == (True, "ledger intact")


def test_replay_detects_changed_telemetry_and_changed_contract(cfg):
    result = audit_contract(ITEM, DF, cfg)
    bundle = cfg.ledger.load_proof(result.proof_id)
    changed = DF.copy()
    changed.loc[0, "resolution_hours"] = 1
    assert not verify_bundle(bundle, TEXT, changed)[0]
    assert not verify_bundle(bundle, TEXT.replace("24 hours", "36 hours"), DF)[0]


def test_ledger_detects_tampering(cfg):
    audit_contract(ITEM, DF, cfg)
    audit_contract(contract("T2", TEXT), telemetry("T2", [{"up": 99.5, "res": 18}]), cfg)
    assert cfg.ledger.verify_chain()[0]
    lines = cfg.ledger.chain_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["verdict"] = "COMPLIANT"
    lines[0] = json.dumps(first, sort_keys=True)
    cfg.ledger.chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, msg = cfg.ledger.verify_chain()
    assert not ok and "#1" in msg


def test_ledger_detects_edited_proof_file(cfg):
    result = audit_contract(ITEM, DF, cfg)
    path = cfg.ledger.proofs_dir / f"{result.proof_id}.json"
    path.write_text(path.read_text(encoding="utf-8").replace("ESCALATED", "COMPLIANT"), encoding="utf-8")
    assert not cfg.ledger.verify_chain()[0]


def test_human_resolution_is_sealed_replayable_and_becomes_a_precedent(cfg):
    first = audit_contract(ITEM, DF, cfg)
    assert first.status == "ESCALATED"
    resolved = resolve_escalation(cfg, first.proof_id, reading_id="R2", reviewer="alice", note="addendum governs")
    assert resolved.status == "COMPLIANT" and resolved.decided_by == "human:alice"
    ok, report = verify_bundle(cfg.ledger.load_proof(resolved.proof_id), TEXT, DF)
    assert ok, report
    # a different contract with the same clause pattern now gets the ruling as a suggestion (not applied)
    later = audit_contract(contract("T9", TEXT), telemetry("T9", [{"up": 99.5, "res": 20}]), cfg)
    assert later.status == "ESCALATED"
    assert later.suggested_ruling and later.suggested_ruling["reading_id"] == "R2"
    assert "suggestion" in later.suggested_ruling["note"].lower()


def test_cannot_resolve_an_automatic_verdict_or_resolve_twice(cfg):
    auto = audit_contract(contract("A", "Uptime shall be 98%. Critical Events restored within 12 hours."),
                          telemetry("A", [{"up": 99.5, "res": 3}]), cfg)
    with pytest.raises(ValueError):
        resolve_escalation(cfg, auto.proof_id, "R1", "bob")
    first = audit_contract(ITEM, DF, cfg)
    done = resolve_escalation(cfg, first.proof_id, "R1", "bob")
    with pytest.raises(ValueError):
        resolve_escalation(cfg, done.proof_id, "R2", "bob")


def test_reviewer_choice_still_escalates_if_data_views_disagree(cfg):
    text = "Uptime shall be at least 98%. Each Critical Event shall be restored within 12 hours."
    df = telemetry("D", [{"up": 99.5, "res": 3}, {"up": 99.5, "res": 40, "status": "FAULT", "truck": "TRK-X"}])
    first = audit_contract(contract("D", text), df, cfg)
    assert first.status == "ESCALATED"
    with pytest.raises(ValueError, match="does not settle"):
        resolve_escalation(cfg, first.proof_id, "R1", "bob")  # picking a reading is not enough: the data is in doubt too
    assert cfg.ledger.resolution_of(first.proof_id) is None  # the inconclusive attempt left no ruling behind
    assert resolve_escalation(cfg, first.proof_id, "R1", "bob", scenario="strict").status == "COMPLIANT"
    with pytest.raises(ValueError, match="already has a recorded ruling"):
        resolve_escalation(cfg, first.proof_id, "R1", "carol", scenario="inclusive")


# ---- brief + outbox


def test_ai_polish_is_rejected_if_it_adds_a_number():
    brief = "Exposure is $500.00 under R1."
    assert polish_brief(brief, lambda b: "Exposure is $900.00 under R1.") == brief
    assert polish_brief(brief, lambda b: "Under R1 the exposure is $500.00.") == "Under R1 the exposure is $500.00."
    assert polish_brief(brief, lambda b: (_ for _ in ()).throw(RuntimeError("down"))) == brief


def test_outbox_routes_actions_by_verdict_and_amount(tmp_path):
    compliant = AuditResult(contract_id="a", status="COMPLIANT", reason="ok")
    assert plan_actions(compliant)[0]["action"] == "release_invoice"
    small = AuditResult(contract_id="b", status="BREACH", reason="x", penalty_usd=100, penalty_basis="contract_clause")
    assert plan_actions(small)[0]["approval"] == "not_required"
    big = small.model_copy(update={"penalty_usd": 90000})
    assert plan_actions(big)[0]["approval"] == "required"
    assumed = small.model_copy(update={"needs_commercial_review": True})
    assert plan_actions(assumed)[0]["approval"] == "required"
    esc = AuditResult(contract_id="c", status="ESCALATED", reason="x")
    assert [a["action"] for a in plan_actions(esc)] == ["hold_invoice", "open_review_ticket"]
    sent = dispatch(esc, LogConnector(tmp_path / "outbox.jsonl"))
    assert len((tmp_path / "outbox.jsonl").read_text().splitlines()) == len(sent) == 2
