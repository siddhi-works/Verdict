"""
service/demo.py

Web API behind the Vercel deployment (public/ is the front end).

What it offers
- The workbench: run the real pipeline on any contract text against any fleet's telemetry and return stage-by-stage output.
- The contract register: the pre-audited bundle in demo/, each with the pipeline view and a live proof replay.
- Reviewer rulings (escalations) and administrator approvals (credit memos that need commercial review).
- A system page: live ledger check, guardrails and the benchmark behind the evidence.

Serverless notes
- Instances do not share memory or disk, so sessions are signed tokens (HMAC), not server-side sessions.
- Only the temp directory is writable. Rulings and approvals live there and are NOT permanent; the UI says so.
- Set SLA_SESSION_SECRET in the Vercel project settings. Without it a secret is derived from users.json, which is
  stable but not private: fine for a public demo, not for anything with real data.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.ledger import Ledger
from core.outbox import APPROVAL_LIMIT_USD, plan_actions
from core.pipeline import PipelineConfig, analyse, resolve_escalation
from core.pipeline_view import build_view
from core.precedents import PrecedentStore
from core.present import BASIS_TEXT, credit_text, money, plain_reason, reading_rows, reason_group, status_label
from core.schemas import AuditResult
from core.verify_proof import verify_bundle
from ui.seed_users import DEFAULT_PASSWORD
from ui.users import HOME, PERMISSIONS, ROLE_LABEL, USERS_PATH, authenticate, can, load_users

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "demo"
SESSION_HOURS = 8
MAX_CONTRACT_CHARS = 60_000
NOT_CALCULATED = ("not_quantifiable", "fee_unknown", "tier_not_found")


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


def public_account(u: dict) -> dict:
    return {**{k: v for k, v in u.items() if k not in ("salt", "hash")}, "role_label": ROLE_LABEL.get(u["role"]),
            "permissions": sorted(PERMISSIONS.get(u["role"], set())), "home": HOME.get(u["role"], "overview")}


class DemoData:
    """Everything the API serves. Loaded once per instance; rulings and approvals update the in-memory copy."""

    def __init__(self, demo_dir: Path, runtime_dir: Path):
        self.demo_dir, self.runtime_dir = demo_dir, runtime_dir
        read = lambda name, default=None: (json.loads((demo_dir / name).read_text(encoding="utf-8"))  # noqa: E731
                                           if (demo_dir / name).exists() else default)
        self.results: List[dict] = read("audit_results.json")
        self.contracts: Dict[str, dict] = {c["contract_id"]: c for c in read("contracts.json")}
        self.meta: Dict[str, dict] = read("contract_meta.json", {})
        self.truth: Dict[str, dict] = read("ground_truth.json", {})
        self.benchmark: dict = read("benchmark.json", {})
        self.by_id = {r["contract_id"]: r for r in self.results}
        self._ledger: Optional[Ledger] = None
        self._telemetry: Optional[pd.DataFrame] = None
        self._approvals: Optional[Dict[str, dict]] = None

    # ---- ledger (a temporary, writable copy of the bundled one)
    def ledger(self) -> Ledger:
        if self._ledger is None:
            target = self.runtime_dir / "ledger"
            if not target.exists():
                self.runtime_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(self.demo_dir / "ledger", target)
            self._ledger = Ledger(target)
        return self._ledger

    # ---- telemetry
    def telemetry(self) -> pd.DataFrame:
        if self._telemetry is None:
            path = self.demo_dir / "telemetry.csv.gz"
            self._telemetry = pd.read_csv(path) if path.exists() else pd.DataFrame(
                columns=["contract_id", "truck_id", "resolution_hours", "actual_uptime_percent", "sensor_status"])
        return self._telemetry

    def fleet(self, fleet_id: str) -> pd.DataFrame:
        df = self.telemetry()
        return df[df["contract_id"].astype(str) == str(fleet_id)]

    # ---- approvals (credit memos that need a person's sign-off)
    def approvals(self) -> Dict[str, dict]:
        if self._approvals is None:
            path = self.runtime_dir / "approvals.json"
            self._approvals = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return self._approvals

    def save_approvals(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        (self.runtime_dir / "approvals.json").write_text(json.dumps(self._approvals or {}), encoding="utf-8")

    def customer(self, cid: str) -> str:
        return self.meta.get(cid, {}).get("customer", "-")

    def pending_memos(self) -> List[dict]:
        out = []
        for r in self.results:
            if r["status"] != "BREACH":
                continue
            actions = plan_actions(AuditResult(**{k: v for k, v in r.items() if k in AuditResult.model_fields}))
            memo = next((a for a in actions if a["action"] == "draft_credit_memo"), None)
            if memo and memo["approval"] == "required":
                out.append({"contract_id": r["contract_id"], "customer": self.customer(r["contract_id"]),
                            "amount": r.get("penalty_usd"), "calculated": r.get("penalty_basis") not in NOT_CALCULATED,
                            "basis": BASIS_TEXT.get(r.get("penalty_basis"), "-"),
                            "why": memo.get("approval_reason") or "Needs commercial review",
                            "reason": plain_reason(r), "decision": self.approvals().get(r["contract_id"])})
        return out


def _row(d: DemoData, r: dict) -> dict:
    return {"id": r["contract_id"], "customer": d.customer(r["contract_id"]), "status": r["status"],
            "status_label": status_label(r["status"]), "reason": plain_reason(r), "credit": credit_text(r),
            "scenario": d.truth.get(r["contract_id"], {}).get("category", ""),
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

    async def body_of(request: Request) -> dict:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    # ---------------------------------------------------------------- session
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
        body = await body_of(request)
        acct = authenticate(str(body.get("email", "")), str(body.get("password", "")))
        if not acct:
            return JSONResponse({"error": "The email or password is incorrect."}, status_code=401)
        return JSONResponse({"token": issue_token(acct), "account": public_account(acct)})

    # ---------------------------------------------------------------- the register
    async def summary(request: Request):
        acct, err = guard(request)
        if err:
            return err
        rs = data.results
        total = len(rs)
        counts = {s: sum(1 for r in rs if r["status"] == s) for s in ("COMPLIANT", "BREACH", "ESCALATED")}
        calculable = [r for r in rs if r["status"] == "BREACH" and r.get("penalty_basis") not in NOT_CALCULATED]
        credits = sum(float(r.get("penalty_usd") or 0) for r in calculable)
        reasons: Dict[str, int] = {}
        for r in rs:
            if r["status"] == "ESCALATED":
                g = reason_group(r.get("reason", ""))
                reasons[g] = reasons.get(g, 0) + 1
        should = sum(1 for cid in data.by_id if data.truth.get(cid, {}).get("expected_status") == "ESCALATED")
        return JSONResponse({
            "total": total, "counts": counts, "auto_share": (total - counts["ESCALATED"]) / max(total, 1),
            "credits": credits, "credits_text": money(credits),
            "reasons": sorted(({"reason": k, "count": v} for k, v in reasons.items()), key=lambda x: -x["count"]),
            "waiting": [_row(data, r) for r in rs if r["status"] == "ESCALATED"][:8],
            "top_credits": [_row(data, r) for r in sorted(calculable, key=lambda r: -float(r.get("penalty_usd") or 0))[:6]],
            "context": {"set_name": "Evaluation set of generated contracts", "built_for_review": should,
                        "sent_for_review": counts["ESCALATED"], "over_cautious": max(counts["ESCALATED"] - should, 0)},
            "pending_approvals": sum(1 for m in data.pending_memos() if not m["decision"]),
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
        meta, truth = data.meta.get(cid, {}), data.truth.get(cid, {})
        exposure = None
        if r["status"] == "ESCALATED" and r.get("exposure_max_usd") is not None:
            exposure = f"{money(r.get('exposure_min_usd') or 0)} to {money(r.get('exposure_max_usd'))}"
        pid = r.get("proof_id")
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
            "brief": r.get("brief"), "contract_text": (data.contracts.get(cid) or {}).get("contract_text", ""),
            "reading_ids": [x["reading_id"] for x in env.get("readings", [])],
            "suggested": r.get("suggested_ruling"), "can_rule": r["status"] == "ESCALATED" and can(acct["role"], "rule"),
            "benchmark": ({"scenario": truth.get("category"), "expected": status_label(truth["expected_status"]),
                           "expected_status": truth["expected_status"],
                           "match": truth["expected_status"] == r["status"] or r.get("decided_by", "machine") != "machine"}
                          if truth else None),
        })

    async def audit_run(request: Request):
        """The pipeline view of a stored audit: re-run deterministically, then replay the sealed proof."""
        acct, err = guard(request)
        if err:
            return err
        cid = request.path_params["cid"]
        r = data.by_id.get(cid)
        text = (data.contracts.get(cid) or {}).get("contract_text")
        if r is None or text is None:
            return JSONResponse({"error": "Contract not found."}, status_code=404)
        df = data.fleet(cid)
        a = analyse({"contract_id": cid, "contract_text": text}, df, PipelineConfig(mode="mock"))
        proof: Dict[str, Any] = {}
        pid = r.get("proof_id")
        if pid:
            proof["proof_id"] = pid
            entry = next((e for e in data.ledger().entries() if e["proof_id"] == pid), None)
            if entry:
                proof.update({"seq": entry["seq"], "ts": entry["ts"], "actor": entry["actor"], "hash": entry["hash"][:16],
                              "prev_hash": entry["prev_hash"][:16]})
            try:
                ok, report = verify_bundle(data.ledger().load_proof(pid), text, df)
                proof["replay"] = {"ok": ok, "checks": report}
            except Exception as exc:  # noqa: BLE001
                proof["replay"] = {"ok": False, "checks": [f"Replay could not run: {exc}"]}
        return JSONResponse(build_view(a, proof))

    async def ruling(request: Request):
        acct, err = guard(request, "rule")
        if err:
            return err
        cid = request.path_params["cid"]
        r = data.by_id.get(cid)
        if r is None or r["status"] != "ESCALATED" or not r.get("proof_id"):
            return JSONResponse({"error": "This contract is not waiting for a ruling."}, status_code=409)
        body = await body_of(request)
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

    # ---------------------------------------------------------------- workbench
    async def fleets(request: Request):
        acct, err = guard(request, "analyze")
        if err:
            return err
        df = data.telemetry()
        grp = df.groupby(df["contract_id"].astype(str)).agg(rows=("contract_id", "size"),
                                                           vehicles=("truck_id", "nunique")).reset_index()
        out = [{"id": g.contract_id, "customer": data.customer(g.contract_id), "rows": int(g.rows), "vehicles": int(g.vehicles)}
               for g in grp.itertuples()]
        return JSONResponse({"fleets": sorted(out, key=lambda f: f["id"])})

    async def analyze(request: Request):
        acct, err = guard(request, "analyze")
        if err:
            return err
        body = await body_of(request)
        text = str(body.get("contract_text", "")).strip()
        fleet_id = str(body.get("fleet", ""))
        if len(text) < 40:
            return JSONResponse({"error": "Paste or load a contract first."}, status_code=422)
        if len(text) > MAX_CONTRACT_CHARS:
            return JSONResponse({"error": f"Contracts are limited to {MAX_CONTRACT_CHARS:,} characters here."}, status_code=413)
        df = data.fleet(fleet_id)
        item = {"contract_id": fleet_id or "WORKBENCH", "contract_text": text}
        a = analyse(item, df, PipelineConfig(mode="mock"))
        return JSONResponse({**build_view(a), "fleet": fleet_id, "fleet_rows": int(len(df))})

    # ---------------------------------------------------------------- approvals (administrator)
    async def approvals(request: Request):
        acct, err = guard(request, "approve")
        if err:
            return err
        memos = data.pending_memos()
        return JSONResponse({"limit": APPROVAL_LIMIT_USD, "pending": [m for m in memos if not m["decision"]],
                             "decided": [m for m in memos if m["decision"]]})

    async def decide_approval(request: Request):
        acct, err = guard(request, "approve")
        if err:
            return err
        cid = request.path_params["cid"]
        if cid not in {m["contract_id"] for m in data.pending_memos()}:
            return JSONResponse({"error": "No credit memo is waiting for this contract."}, status_code=404)
        body = await body_of(request)
        decision = body.get("decision")
        if decision not in ("approve", "reject"):
            return JSONResponse({"error": "Choose approve or reject."}, status_code=422)
        amount = body.get("amount")
        base = data.by_id[cid]
        calculable = base.get("penalty_basis") not in NOT_CALCULATED
        if decision == "approve" and not calculable:
            try:
                amount = float(amount)
                if amount <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return JSONResponse({"error": "Enter the amount to issue. The contract does not give a way to calculate it."}, status_code=422)
        elif decision == "approve":
            amount = float(base.get("penalty_usd") or 0) if amount in (None, "") else float(amount)
        data.approvals()[cid] = {"decision": decision, "amount": amount if decision == "approve" else None,
                                 "note": str(body.get("note", ""))[:500], "by": acct["name"],
                                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        data.save_approvals()
        return JSONResponse({"ok": True, "contract_id": cid, "decision": data.approvals()[cid]})

    # ---------------------------------------------------------------- system (administrator)
    async def system(request: Request):
        acct, err = guard(request, "approve")
        if err:
            return err
        ok, msg = data.ledger().verify_chain()
        entries = data.ledger().entries()
        return JSONResponse({
            "ledger": {"intact": ok, "message": msg, "entries": len(entries),
                       "human_rulings": sum(1 for e in entries if e.get("event") == "human_resolution")},
            "guardrails": [
                {"name": "Quote verification", "value": "On", "note": "A number is used only if its quoted sentence is in the contract."},
                {"name": "Readings per contract", "value": "up to 16", "note": "More than this is escalated, not truncated."},
                {"name": "Telemetry quality floor", "value": "Grade D escalates", "note": "More than 30% unusable rows blocks a verdict."},
                {"name": "Credit approval limit", "value": money(APPROVAL_LIMIT_USD), "note": "Above this, or when not backed by a contract clause, a person approves."},
                {"name": "Automated amounts", "value": "Contract-backed only", "note": "Never invented; otherwise 'not calculated'."},
            ],
            "benchmark": data.benchmark,
        })

    return Starlette(routes=[
        Route("/api/health", health),
        Route("/api/accounts", accounts),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/summary", summary),
        Route("/api/audits", audits),
        Route("/api/audits/{cid}", audit_detail),
        Route("/api/audits/{cid}/run", audit_run),
        Route("/api/audits/{cid}/ruling", ruling, methods=["POST"]),
        Route("/api/fleets", fleets),
        Route("/api/analyze", analyze, methods=["POST"]),
        Route("/api/approvals", approvals),
        Route("/api/approvals/{cid}", decide_approval, methods=["POST"]),
        Route("/api/system", system),
    ])
