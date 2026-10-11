"""The Vercel function receives /api/index?__p=<original path>; the entry point must route it like the original request."""
import json
from pathlib import Path

from starlette.testclient import TestClient

from api.index import app
from ui.users import load_users

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Fleet-Audit-2026"


def test_rewritten_requests_reach_the_right_route():
    c = TestClient(app)
    assert c.get("/api/index?__p=health").json()["ok"] is True
    assert c.get("/api/index?__p=accounts").status_code == 200
    assert c.get("/api/health").json()["ok"] is True  # un-rewritten paths still work (local dev)


def test_query_parameters_survive_the_rewrite():
    c = TestClient(app)
    email = next(u["email"] for u in load_users() if u["role"] == "analyst")
    token = c.post("/api/index?__p=login", json={"email": email, "password": PASSWORD}).json()["token"]
    h = {"Authorization": "Bearer " + token}
    all_rows = c.get("/api/index?__p=audits", headers=h).json()
    breaches = c.get("/api/index?__p=audits&status=BREACH", headers=h).json()
    assert 0 < breaches["count"] < all_rows["count"]
    assert all(r["status"] == "BREACH" for r in breaches["rows"])
    one = breaches["rows"][0]["id"]
    assert c.get(f"/api/index?__p=audits/{one}", headers=h).json()["id"] == one


def test_vercel_json_sends_every_api_path_to_the_function():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    rule = cfg["rewrites"][0]
    assert rule["source"] == "/api/:path*" and rule["destination"] == "/api/index?__p=:path*"
