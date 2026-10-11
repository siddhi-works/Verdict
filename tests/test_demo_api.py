import pytest
from starlette.testclient import TestClient

from service.demo import create_demo_app, issue_token, read_token
from ui.users import load_users

PASSWORD = "Fleet-Audit-2026"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_demo_app(runtime_dir=tmp_path / "rt"))


def login(client, role):
    email = next(u["email"] for u in load_users() if u["role"] == role)
    r = client.post("/api/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_everything_except_health_and_accounts_needs_a_session(client):
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/accounts").status_code == 200
    for path in ("/api/summary", "/api/audits", "/api/audits/V2-001"):
        assert client.get(path).status_code == 401
    assert client.get("/api/summary", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_wrong_password_is_rejected(client):
    email = load_users()[0]["email"]
    assert client.post("/api/login", json={"email": email, "password": "nope"}).status_code == 401


def test_accounts_never_expose_hashes_or_salts(client):
    raw = client.get("/api/accounts").text
    assert "hash" not in raw and "salt" not in raw


def test_tokens_are_signed_and_expire():
    acct = load_users()[0]
    token = issue_token(acct, now=1_000_000)
    assert read_token(token, now=1_000_100)["email"] == acct["email"]
    assert read_token(token, now=1_000_000 + 9 * 3600) is None  # expired
    body, sig = token.split(".")
    assert read_token(body + "." + "0" * len(sig)) is None  # tampered signature


def test_summary_and_list(client):
    h = login(client, "analyst")
    s = client.get("/api/summary", headers=h).json()
    assert s["total"] == 194 and sum(s["counts"].values()) == 194 and 0 <= s["auto_share"] <= 1
    rows = client.get("/api/audits?status=escalated", headers=h).json()
    assert rows["count"] == s["counts"]["ESCALATED"]
    assert all(r["status"] == "ESCALATED" and r["customer"] != "-" for r in rows["rows"])
    assert client.get("/api/audits?q=zzzz-nothing", headers=h).json()["count"] == 0


def test_detail_has_plain_language_and_evidence(client):
    h = login(client, "analyst")
    rows = client.get("/api/audits?status=escalated", headers=h).json()["rows"]
    cid = next(r["id"] for r in rows if "read" in r["reason"].lower() or "contract" in r["reason"].lower())
    d = client.get(f"/api/audits/{cid}", headers=h).json()
    assert "Hard Gate" not in d["reason"] and d["contract_text"] and d["facts"]
    assert client.get("/api/audits/NOPE", headers=h).status_code == 404


def test_only_reviewers_can_record_rulings_and_it_changes_the_status(client):
    analyst, reviewer = login(client, "analyst"), login(client, "reviewer")
    rows = client.get("/api/audits?status=escalated", headers=reviewer).json()["rows"]
    candidates = [r["id"] for r in rows]
    target = next(c for c in candidates if client.get(f"/api/audits/{c}", headers=reviewer).json()["reading_ids"])
    detail = client.get(f"/api/audits/{target}", headers=reviewer).json()
    assert detail["can_rule"] is True
    assert client.get(f"/api/audits/{target}", headers=analyst).json()["can_rule"] is False
    body = {"reading_id": detail["reading_ids"][0], "scenario": "strict"}
    assert client.post(f"/api/audits/{target}/ruling", json=body, headers=analyst).status_code == 403
    assert client.post(f"/api/audits/{target}/ruling", json={}, headers=reviewer).status_code == 422
    res = client.post(f"/api/audits/{target}/ruling", json=body, headers=reviewer)
    assert res.status_code in (200, 409)  # 409 only if the ruling leaves the case undecided
    if res.status_code == 200:
        assert client.get(f"/api/audits/{target}", headers=reviewer).json()["status"] != "ESCALATED"
