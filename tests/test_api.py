import pytest
from starlette.testclient import TestClient

from service.main import create_app
from tests.helpers import telemetry

TEXT = ("Monthly fleet uptime shall be maintained at not less than 98%. Each Critical Event shall be restored within 12 hours. "
        "ADDENDUM A. The restoration window for Critical Events is set at 24 hours.")
TOKENS = {
    "t-analyst": {"user": "ann", "role": "analyst", "tenant": "acme"},
    "t-review": {"user": "rev", "role": "reviewer", "tenant": "acme"},
    "t-other": {"user": "oli", "role": "reviewer", "tenant": "globex"},
}


@pytest.fixture
def client(tmp_path):
    app = create_app(tokens=TOKENS, datasets={}, root=tmp_path, mode="mock")
    return TestClient(app)


def h(token):
    return {"Authorization": f"Bearer {token}"}


def body(res=18):
    rows = telemetry("T", [{"up": 99.5, "res": res}, {"up": 99.4, "res": 2}]).to_dict(orient="records")
    return {"contract": {"contract_id": "T", "contract_text": TEXT}, "telemetry": rows}


def test_requires_a_valid_token_and_the_right_role(client):
    assert client.post("/audit", json=body()).status_code == 401
    assert client.post("/audit", json=body(), headers=h("nope")).status_code == 401
    assert client.post("/audit", json=body(), headers=h("t-analyst")).status_code == 403
    assert client.get("/metrics", headers=h("t-analyst")).status_code == 200


def test_audit_resolve_verify_flow(client):
    r = client.post("/audit", json=body(), headers=h("t-review"))
    assert r.status_code == 200 and r.json()["status"] == "ESCALATED"
    pid = r.json()["proof_id"]
    assert [e["proof_id"] for e in client.get("/escalations", headers=h("t-analyst")).json()["open"]] == [pid]
    assert client.post(f"/escalations/{pid}/resolve", json={}, headers=h("t-review")).status_code == 422
    assert client.post(f"/escalations/{pid}/resolve", json={"reading_id": "R2"}, headers=h("t-analyst")).status_code == 403
    done = client.post(f"/escalations/{pid}/resolve", json={"reading_id": "R2", "note": "addendum governs"}, headers=h("t-review"))
    assert done.status_code == 200 and done.json()["status"] == "COMPLIANT" and done.json()["decided_by"] == "human:rev"
    assert client.post(f"/escalations/{pid}/resolve", json={"reading_id": "R1"}, headers=h("t-review")).status_code == 409
    assert client.get("/escalations", headers=h("t-analyst")).json()["open"] == []
    m = client.get("/metrics", headers=h("t-analyst")).json()
    assert m["audits"] == 1 and m["human_resolutions"] == 1 and m["ledger_intact"] is True
    assert client.get("/ledger/verify", headers=h("t-analyst")).json()["intact"] is True
    assert client.get(f"/audit/{pid}/proof", headers=h("t-analyst")).json()["decision"]["status"] == "ESCALATED"


def test_tenants_cannot_see_each_other(client):
    pid = client.post("/audit", json=body(), headers=h("t-review")).json()["proof_id"]
    assert client.get(f"/audit/{pid}/proof", headers=h("t-other")).status_code == 404
    assert client.get("/metrics", headers=h("t-other")).json()["audits"] == 0


def test_bad_input_is_rejected_cleanly(client):
    assert client.post("/audit", json={"contract": {"contract_id": "X"}}, headers=h("t-review")).status_code == 422
    assert client.post("/audit", json={"dataset": "../../etc"}, headers=h("t-review")).status_code == 404
    assert client.get("/healthz").json()["ok"] is True


def test_refuses_to_start_without_tokens(monkeypatch):
    monkeypatch.delenv("SLA_API_TOKENS", raising=False)
    monkeypatch.delenv("SLA_API_INSECURE_DEMO", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
