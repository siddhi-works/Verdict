"""
service/demo.py

Web API behind the Vercel deployment (public/ is the front end). It serves the pre-audited bundle in demo/ and
lets a signed-in reviewer record rulings.

Serverless notes
- Instances do not share memory or disk, so sessions are signed tokens (HMAC), not server-side sessions.
- Only the temp directory is writable. Rulings extend a temporary copy of demo/ledger and are NOT permanent;
  they can disappear when the instance is recycled. The UI says so.
- Set SLA_SESSION_SECRET in the Vercel project settings. Without it a secret is derived from users.json, which
  is stable but not private: fine for a public demo, not for anything with real data.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.ledger import Ledger
from core.pipeline import PipelineConfig, resolve_escalation
from core.precedents import PrecedentStore
from core.present import BASIS_TEXT, credit_text, money, plain_reason, reading_rows, reason_group, status_label
from ui.seed_users import DEFAULT_PASSWORD
from ui.users import ROLE_LABEL, USERS_PATH, authenticate, can, load_users

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "demo"
SESSION_HOURS = 8


def _secret() -> bytes:
    env = os.getenv("SLA_SESSION_SECRET")
    if env:
        return env.encode("utf-8")
    try:
        return hashlib.sha256(USERS_PATH.read_bytes() + b"sla-demo-session").digest()
    except Exception:
        return b"sla-demo-session"


def issue_token(account: dict, now: Optional[float] = None) -> str:
    body = json.dumps({"e": account["email"], "x": int((now or time.time()) + SESSION_HOURS * 3600)}, separators=(",", ":")).encode()
    part = base64.urlsafe_b64encode(body).decode().rstrip("=")
    sig = hmac.new(_secret(), part.encode(), hashlib.sha256).hexdigest()
    return f"{part}.{sig}"


def read_token(token: str, now: Optional[float] = None) -> Optional[dict]:
    try:
        part, sig = token.split(".", 1)
        expected = hmac.new(_secret(), part.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        if data["x"] < (now or time.time()):
            return None
        return next((u for u in load_users() if u["email"] == data["e"]), None)
    except Exception:  # noqa: BLE001 - any malformed token is simply "not signed in"
        return None


class DemoData:
    """Results and contracts loaded once per instance; rulings update the in-memory rows."""

    def __init__(self, demo_dir: Path, runtime_dir: Path):
        self.demo_dir, self.runtime_dir = demo_dir, runtime_dir
        self.results: List[dict] = json.loads((demo_dir / "audit_results.json").read_text(encoding="utf-8"))
        self.contracts: Dict[str, dict] = {c["contract_id"]: c for c in json.loads((demo_dir / "contracts.json").read_text(encoding="utf-8"))}
        meta_path = demo_dir / "contract_meta.json"
        self.meta: Dict[str, dict] = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        self.by_id = {r["contract_id"]: r for r in self.results}
        self._ledger: Optional[Ledger] = None

    def ledger(self) -> Ledger:
        if self._ledger is None:
            target = self.runtime_dir / "ledger"
            if not target.exists():
                self.runtime_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(self.demo_dir / "ledger", target)
            self._ledger = Ledger(target)
        return self._ledger

    def customer(self, cid: str) -> str:
        return self.meta.get(cid, {}).get("customer", "-")


def _row(d: DemoData, r: dict) -> dict:
    return {"id": r["contract_id"], "customer": d.customer(r["contract_id"]), "status": r["status"],
            "status_label": status_label(r["status"]), "reason": plain_reason(r), "credit": credit_text(r),
            "at_stake": r.get("exposure_max_usd") if r["status"] == "ESCALATED" else None,
            "decided_by": "System" if r.get("decided_by", "machine") == "machine" else r["decided_by"]}


def create_demo_app(demo_dir: Optional[Path] = None, runtime_dir: Optional[Path] = None) -> Starlette:
    data = DemoData(demo_dir or DEMO_DIR, runtime_dir or Path(os.getenv("SLA_DEMO_RUNTIME") or tempfile.gettempdir()) / "sla-demo")

    def account_of(request: Request) -> Optional[dict]:
        header = request.headers.get("authorization", "")
        return read_token(header[7:]) if header.startswith("Bearer ") else None

    def guard(request: Request, action: Optional[str] = None):
        acct = account_of(request)
        if acct is None:
            return None, JSONResponse({"error": "Sign in to continue."}, status_code=401)
        if action and not can(acct["role"], action):
            return None, JSONResponse({"error": f"Your role ({ROLE_LABEL.get(acct['role'])}) cannot do this."}, status_code=403)
        return acct, None

    async def health(request):
        return JSONResponse({"ok": True, "contracts": len(data.results)})

    async def accounts(request):
        people = [{"email": u["email"], "name": u["name"], "role": u["role"], "role_label": ROLE_LABEL.get(u["role"]),
                   "title": u.get("title", "")} for u in load_users()]
        out: Dict[str, Any] = {"accounts": people, "prefill": None}
        if os.getenv("SLA_UI_AUTOFILL", "1") != "0":  # demo convenience; set SLA_UI_AUTOFILL=0 on anything shared
            out["prefill"] = os.getenv("SLA_UI_PASSWORD") or DEFAULT_PASSWORD
        return JSONResponse(out)

    async def login(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        acct = authenticate(str(body.get("email", "")), str(body.get("password", "")))
        if not acct:
            return JSONResponse({"error": "The email or password is incorrect."}, status_code=401)
        return JSONResponse({"token": issue_token(acct), "account": {**acct, "role_label": ROLE_LABEL.get(acct["role"]),
                                                                       "can_rule": can(acct["role"], "rule")}})

    async def summary(request: Request):
        acct, err = guard(request)
        if err:
            return err
        rs = data.results
        total = len(rs)
        counts = {s: sum(1 for r in rs if r["status"] == s) for s in ("COMPLIANT", "BREACH", "ESCALATED")}
        credits = sum(float(r.get("penalty_usd") or 0) for r in rs if r["status"] == "BREACH" and r.get("penalty_basis") not in
                      ("not_quantifiable", "fee_unknown", "tier_not_found"))
        reasons: Dict[str, int] = {}
        for r in rs:
            if r["status"] == "ESCALATED":
                reasons[reason_group(r.get("reason", ""))] = reasons.get(reason_group(r.get("reason", "")), 0) + 1
        top = sorted((r for r in rs if r["status"] == "BREACH" and r.get("penalty_basis") not in
                      ("not_quantifiable", "fee_unknown", "tier_not_found")), key=lambda r: -float(r.get("penalty_usd") or 0))[:6]
        return JSONResponse({
            "total": total, "counts": counts, "auto_share": (total - counts["ESCALATED"]) / max(total, 1),
            "credits": credits, "credits_text": money(credits),
            "reasons": sorted(({"reason": k, "count": v} for k, v in reasons.items()), key=lambda x: -x["count"]),
            "waiting": [_row(data, r) for r in rs if r["status"] == "ESCALATED"][:8],
            "top_credits": [_row(data, r) for r in top],
        })

    async def audits(request: Request):
        acct, err = guard(request)
        if err:
            return err
        status = request.query_params.get("status", "").upper()
        q = request.query_params.get("q", "").strip().lower()
        rows = [_row(data, r) for r in data.results]
        if status in ("COMPLIANT", "BREACH", "ESCALATED"):
            rows = [r for r in rows if r["status"] == status]
        if q:
            rows = [r for r in rows if q in r["id"].lower() or q in r["customer"].lower() or q in r["reason"].lower()]
        return JSONResponse({"count": len(rows), "total": len(data.results), "rows": rows})

    async def audit_detail(request: Request):
        acct, err = guard(request)
        if err:
            return err
        cid = request.path_params["cid"]
        r = data.by_id.get(cid)
        if r is None:
            return JSONResponse({"error": "Contract not found."}, status_code=404)
        env = r.get("envelope") or {}
        quotes: List[str] = []
        pid = r.get("proof_id")
        if pid:
            try:
                ext = data.ledger().load_proof(pid).get("extraction", {})
                quotes = [c["quote"] for c in ext.get("uptime_candidates", []) + ext.get("resolution_candidates", [])] + ext.get("vague_terms", [])
            except Exception:  # noqa: BLE001
                pass
        meta = data.meta.get(cid, {})
        exposure = None
        if r["status"] == "ESCALATED" and r.get("exposure_max_usd") is not None:
            exposure = f"{money(r.get('exposure_min_usd') or 0)} to {money(r.get('exposure_max_usd'))}"
        return JSONResponse({
            **_row(data, r),
            "facts": [
                ["Customer", meta.get("customer", "-")], ["Service period", meta.get("period", "-")], ["Region", meta.get("region", "-")],
                ["Credit", credit_text(r)], ["Credit basis", BASIS_TEXT.get(r.get("penalty_basis"), "-")],
                ["Amount at stake", exposure or "-"],
                ["Commercial review", "Required" if r.get("needs_commercial_review") else "Not required"],
                ["Decided by", "System" if r.get("decided_by", "machine") == "machine" else r["decided_by"]],
                ["Proof", pid or "-"],
            ],
            "readings": reading_rows(env), "notes": env.get("notes", []),
            "brief": r.get("brief"), "contract_text": (data.contracts.get(cid) or {}).get("contract_text", ""), "quotes": quotes,
            "reading_ids": [x["reading_id"] for x in env.get("readings", [])],
            "suggested": r.get("suggested_ruling"), "can_rule": r["status"] == "ESCALATED" and can(acct["role"], "rule"),
        })

    async def ruling(request: Request):
        acct, err = guard(request, "rule")
        if err:
            return err
        cid = request.path_params["cid"]
        r = data.by_id.get(cid)
        if r is None or r["status"] != "ESCALATED" or not r.get("proof_id"):
            return JSONResponse({"error": "This contract is not waiting for a ruling."}, status_code=409)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        if not body.get("reading_id"):
            return JSONResponse({"error": "Choose the reading that applies."}, status_code=422)
        scn = {"strict": "strict", "inclusive": "inclusive"}.get(body.get("scenario"))
        cfg = PipelineConfig(ledger=data.ledger(), precedents=PrecedentStore(data.runtime_dir / "precedents.json"))
        try:
            res = resolve_escalation(cfg, r["proof_id"], body["reading_id"], acct["name"], scn, str(body.get("note", ""))[:500])
        except (ValueError, FileNotFoundError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        updated = res.model_dump()
        data.by_id[cid] = updated
        data.results = [updated if x["contract_id"] == cid else x for x in data.results]
        return JSONResponse(_row(data, updated))

    return Starlette(routes=[
        Route("/api/health", health),
        Route("/api/accounts", accounts),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/summary", summary),
        Route("/api/audits", audits),
        Route("/api/audits/{cid}", audit_detail),
        Route("/api/audits/{cid}/ruling", ruling, methods=["POST"]),
    ])
