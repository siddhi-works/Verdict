
import pytest

from core.ledger import Ledger
from core.pipeline import PipelineConfig, audit_contract, resolve_escalation
from core.telemetry import Trace, read_traces
from tests.helpers import contract, telemetry

SECRET = "ZEBRA-CLAUSE-7731"
TEXT = (f"Monthly fleet uptime shall be maintained at not less than 98%. Each Critical Event shall be restored within 12 hours. "
        f"ADDENDUM A. The restoration window for Critical Events is set at 24 hours. Reference code {SECRET}.")


@pytest.fixture
def cfg(tmp_path):
    return PipelineConfig(ledger=Ledger(tmp_path / "ledger"))


def test_every_audit_writes_a_trace_with_stage_spans(cfg):
    df = telemetry("T", [{"up": 99.5, "res": 18}, {"up": 99.4, "res": 2}])
    r = audit_contract(contract("T", TEXT), df, cfg)
    traces = read_traces(cfg.ledger.root / "traces.jsonl")
    assert len(traces) == 1 and traces[0]["trace_id"] == r.trace_id and traces[0]["attrs"]["proof_id"] == r.proof_id
    stages = [s["name"] for s in traces[0]["spans"]]
    assert stages == ["read.pattern", "verify.quotes", "enumerate.readings", "run.grid", "decide.envelope", "seal.proof"]
    assert all(s["status"] == "ok" and s["duration_ms"] >= 0 for s in traces[0]["spans"])


def test_traces_never_contain_contract_text_or_quotes(cfg):
    audit_contract(contract("T", TEXT), telemetry("T", [{"up": 99.5, "res": 18}]), cfg)
    raw = (cfg.ledger.root / "traces.jsonl").read_text(encoding="utf-8")
    assert SECRET not in raw and "restoration window" not in raw


def test_trace_is_outside_the_proof_so_identical_audits_give_identical_proofs(cfg):
    item, df = contract("T", TEXT), telemetry("T", [{"up": 99.5, "res": 18}])
    a, b = audit_contract(item, df, cfg), audit_contract(item, df, cfg)
    assert a.proof_id == b.proof_id and a.trace_id != b.trace_id


def test_ledger_records_who_decided_software_or_human(cfg):
    df = telemetry("T", [{"up": 99.5, "res": 18}, {"up": 99.4, "res": 2}])
    first = audit_contract(contract("T", TEXT), df, cfg)
    resolve_escalation(cfg, first.proof_id, "R2", "alice")
    actors = [e["actor"] for e in cfg.ledger.entries()]
    assert actors == ["system:sla-harness", "human:alice"]
    assert cfg.ledger.verify_chain()[0]


def test_span_records_errors_and_reraises():
    t = Trace()
    with pytest.raises(ValueError):
        with t.span("boom"):
            raise ValueError("x")
    rec = t.finish()
    assert rec["spans"][0]["status"] == "error" and rec["spans"][0]["attrs"]["error.type"] == "ValueError"


def test_ai_usage_is_attached_to_the_span_that_made_the_calls():
    from core.telemetry import record_ai_usage
    t = Trace()
    with t.span("read.ai"):
        record_ai_usage(1200, 300)
        record_ai_usage(1200, 250, retry=True)
    attrs = t.finish()["spans"][0]["attrs"]
    assert attrs["gen_ai.calls"] == 2 and attrs["gen_ai.usage.input_tokens"] == 2400 and attrs["gen_ai.retries"] == 1
