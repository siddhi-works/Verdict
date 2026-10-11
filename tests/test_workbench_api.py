import pytest
from starlette.testclient import TestClient

from service.demo import create_demo_app
from ui.users import load_users

PASSWORD = "Fleet-Audit-2026"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_demo_app(runtime_dir=tmp_path / "rt"))


def login(client, role):
    email = next(u["email"] for u in load_users() if u["role"] == role)
    r = client.post("/api/login", json={"email": email, "password": PASSWORD}).json()
    return {"Authorization": "Bearer " + r["token"]}, r["account"]


def test_each_role_lands_on_its_own_home(client):
    homes = {role: login(client, role)[1]["home"] for role in ("analyst", "reviewer", "admin")}
    assert homes == {"analyst": "workbench", "reviewer": "review", "admin": "approvals"}
    assert "rule" in login(client, "reviewer")[1]["permissions"]
    assert "approve" in login(client, "admin")[1]["permissions"] and "rule" not in login(client, "admin")[1]["permissions"]


def test_workbench_runs_the_real_pipeline_on_pasted_text(client):
    h, _ = login(client, "analyst")
    fleet = client.get("/api/fleets", headers=h).json()["fleets"][0]["id"]
    text = ("Monthly fleet uptime shall be maintained at not less than 98%. Each Critical Event shall be restored "
            "within 12 hours. ADDENDUM A. The restoration window for Critical Events is set at 24 hours.")
    r = client.post("/api/analyze", json={"contract_text": text, "fleet": fleet}, headers=h)
    assert r.status_code == 200
    v = r.json()
    assert [s["key"] for s in v["stages"]] == ["read", "verify", "readings", "data", "envelope", "decision", "proof"]
    readings = next(s for s in v["stages"] if s["key"] == "readings")
    assert len(readings["items"]) == 2  # 12 h and 24 h are both plausible: the system keeps both
    assert next(s for s in v["stages"] if s["key"] == "proof")["sealed"] is False


def test_editing_the_contract_changes_the_verdict(client):
    h, _ = login(client, "analyst")
    base = ("Monthly fleet uptime shall be maintained at not less than 50%. "
            "Each Critical Event shall be restored within {hours} hours.")
    fleet = "V2-007"  # a clean generated fleet
    strict = client.post("/api/analyze", json={"contract_text": base.format(hours=1), "fleet": fleet}, headers=h).json()
    loose = client.post("/api/analyze", json={"contract_text": base.format(hours=500), "fleet": fleet}, headers=h).json()
    assert strict["status"] == "BREACH" and loose["status"] == "COMPLIANT"


def test_workbench_rejects_empty_and_oversized_input(client):
    h, _ = login(client, "analyst")
    assert client.post("/api/analyze", json={"contract_text": "x"}, headers=h).status_code == 422
    assert client.post("/api/analyze", json={"contract_text": "a" * 70000, "fleet": "V2-007"}, headers=h).status_code == 413


def test_stored_audit_view_replays_its_proof(client):
    h, _ = login(client, "analyst")
    v = client.get("/api/audits/V2-007/run", headers=h).json()
    proof = next(s for s in v["stages"] if s["key"] == "proof")
    assert proof["sealed"] and proof["replay"]["ok"] is True and proof["seq"] >= 1
    assert client.get("/api/audits/NOPE/run", headers=h).status_code == 404


def test_audit_detail_carries_the_benchmark_check(client):
    h, _ = login(client, "analyst")
    d = client.get("/api/audits/V2-007", headers=h).json()
    assert d["benchmark"]["expected_status"] in ("COMPLIANT", "BREACH", "ESCALATED") and d["benchmark"]["scenario"]


def test_approvals_are_administrator_only_and_need_an_amount_when_not_calculable(client):
    analyst, _ = login(client, "analyst")
    reviewer, _ = login(client, "reviewer")
    admin, _ = login(client, "admin")
    assert client.get("/api/approvals", headers=analyst).status_code == 403
    assert client.get("/api/approvals", headers=reviewer).status_code == 403
    pending = client.get("/api/approvals", headers=admin).json()["pending"]
    assert pending
    hard = next((m for m in pending if not m["calculated"]), None)
    assert hard is not None
    assert client.post(f"/api/approvals/{hard['contract_id']}", json={"decision": "approve"}, headers=admin).status_code == 422
    ok = client.post(f"/api/approvals/{hard['contract_id']}", json={"decision": "approve", "amount": 1250, "note": "agreed"}, headers=admin)
    assert ok.status_code == 200
    after = client.get("/api/approvals", headers=admin).json()
    assert hard["contract_id"] not in {m["contract_id"] for m in after["pending"]}
    assert after["decided"][0]["decision"]["by"]


def test_administrators_cannot_settle_escalations_but_reviewers_cannot_approve_money(client):
    admin, _ = login(client, "admin")
    reviewer, _ = login(client, "reviewer")
    esc = client.get("/api/audits?status=escalated", headers=reviewer).json()["rows"][0]["id"]
    assert client.post(f"/api/audits/{esc}/ruling", json={"reading_id": "R1"}, headers=admin).status_code == 403
    assert client.post("/api/approvals/V2-002", json={"decision": "approve"}, headers=reviewer).status_code == 403


def test_system_page_is_live_and_admin_only(client):
    admin, _ = login(client, "admin")
    analyst, _ = login(client, "analyst")
    assert client.get("/api/system", headers=analyst).status_code == 403
    s = client.get("/api/system", headers=admin).json()
    assert s["ledger"]["intact"] is True and s["ledger"]["entries"] >= 194
    assert s["benchmark"]["engine"]["wrong_automatic"] == 0 and s["guardrails"]


def test_overview_explains_why_so_many_need_review(client):
    h, _ = login(client, "analyst")
    c = client.get("/api/summary", headers=h).json()["context"]
    assert c["built_for_review"] > 0 and c["sent_for_review"] >= c["built_for_review"] - 5
    assert c["over_cautious"] == max(c["sent_for_review"] - c["built_for_review"], 0)
