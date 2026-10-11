"""
api/main.py

Small HTTP service around the audit pipeline (Starlette + uvicorn).

    SLA_API_TOKENS='{"<token>": {"user": "alice", "role": "reviewer", "tenant": "michelin"}}' \
        uvicorn service.main:app

    # or, for a local demo only: SLA_API_INSECURE_DEMO=1 creates random one-off tokens and prints them

Roles:   analyst  read verdicts, proofs, metrics
         reviewer analyst + run audits + resolve escalations
         admin    everything
Tenants: each token belongs to one tenant; ledgers, proofs and precedents are stored per tenant
         and never shared.

Datasets are chosen from a fixed allow-list (SLA_DATASETS, default: v1 and v2 under this repo),
so a request can never point the server at an arbitrary path.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.ledger import Ledger
from core.outbox import LogConnector, dispatch
from core.pipeline import PipelineConfig, audit_contract, contract_text, resolve_escalation
from core.precedents import PrecedentStore
from core.verify_proof import verify_bundle

BASE = Path(__file__).resolve().parents[1]
ROLES = {"analyst": 1, "reviewer": 2, "admin": 3}
MAX_TELEMETRY_ROWS = 50_000


def _load_tokens() -> Dict[str, dict]:
    raw = os.getenv("SLA_API_TOKENS")
    if raw:
        tokens = json.loads(raw)
    elif os.getenv("SLA_API_INSECURE_DEMO") == "1":
        tokens = {}
        for role in ROLES:
            t = secrets.token_urlsafe(16)
            tokens[t] = {"user": f"demo-{role}", "role": role, "tenant": "demo"}
            print(f"[demo] {role} token: {t}")
    else:
        raise RuntimeError("Set SLA_API_TOKENS (JSON) or SLA_API_INSECURE_DEMO=1 before starting the API.")
    for t, meta in tokens.items():
        if meta.get("role") not in ROLES or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", meta.get("tenant", "")):
            raise RuntimeError("each token needs a valid role and a simple tenant name")
    return tokens


def _datasets() -> Dict[str, Path]:
    raw = os.getenv("SLA_DATASETS")
    if raw:
        return {k: Path(v) for k, v in json.loads(raw).items()}
    found = {"v1": BASE, "v2": BASE / "data" / "v2"}
    return {k: v for k, v in found.items() if (v / "contracts.json").exists()}


def create_app(tokens: Optional[Dict[str, dict]] = None, datasets: Optional[Dict[str, Path]] = None,
               root: Optional[Path] = None, mode: Optional[str] = None) -> Starlette:
    tokens = tokens or _load_tokens()
    datasets = datasets or _datasets()
    root = Path(root or os.getenv("SLA_API_DATA_DIR") or BASE / "ledger" / "api")
    mode = mode or os.getenv("SLA_MODE", "mock")
    cache: Dict[str, tuple] = {}

    def load(name: str):
        if name not in datasets:
            raise KeyError(f"unknown dataset '{name}'")
        if name not in cache:
            d = datasets[name]
            cache[name] = (json.loads((d / "contracts.json").read_text(encoding="utf-8")),
                           pd.read_csv(d / "telemetry_logs.csv"))
        return cache[name]

    def tenant_cfg(tenant: str) -> PipelineConfig:
        return PipelineConfig(mode=mode, ledger=Ledger(root / tenant), precedents=PrecedentStore(root / tenant / "precedents.json"))

    def auth(request: Request, minimum: str):
        header = request.headers.get("authorization", "")
        meta = tokens.get(header[7:]) if header.startswith("Bearer ") else None
        if meta is None:
            return None, JSONResponse({"error": "missing or invalid token"}, status_code=401)
        if ROLES[meta["role"]] < ROLES[minimum]:
            return None, JSONResponse({"error": f"role '{meta['role']}' cannot do this (needs {minimum})"}, status_code=403)
        return meta, None

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    async def healthz(request):
        return JSONResponse({"ok": True, "mode": mode, "datasets": sorted(datasets)})

    async def post_audit(request: Request):
        meta, err = auth(request, "reviewer")
        if err:
            return err
        data = await body(request)
        try:
            if "contract" in data:
                item = data["contract"]
                rows = data.get("telemetry", [])
                if len(rows) > MAX_TELEMETRY_ROWS:
                    return JSONResponse({"error": f"telemetry limited to {MAX_TELEMETRY_ROWS} rows"}, status_code=413)
                df = pd.DataFrame(rows)
                if df.empty:
                    df = pd.DataFrame(columns=["contract_id", "resolution_hours", "actual_uptime_percent", "sensor_status"])
                if not isinstance(item, dict) or not item.get("contract_id") or not contract_text(item):
                    return JSONResponse({"error": "contract needs contract_id and contract_text"}, status_code=422)
            else:
                contracts, df = load(data.get("dataset", "v1"))
                item = next((c for c in contracts if c["contract_id"] == data.get("contract_id")), None)
                if item is None:
                    return JSONResponse({"error": "contract not found"}, status_code=404)
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        cfg = tenant_cfg(meta["tenant"])
        result = await run_in_threadpool(audit_contract, item, df, cfg)
        dispatch(result, LogConnector(root / meta["tenant"] / "outbox.jsonl"))
        return JSONResponse(result.model_dump())

    async def get_proof(request: Request):
        meta, err = auth(request, "analyst")
        if err:
            return err
        try:
            return JSONResponse(tenant_cfg(meta["tenant"]).ledger.load_proof(request.path_params["proof_id"]))
        except (FileNotFoundError, ValueError):
            return JSONResponse({"error": "unknown proof"}, status_code=404)

    async def verify_proof(request: Request):
        meta, err = auth(request, "analyst")
        if err:
            return err
        try:
            bundle = tenant_cfg(meta["tenant"]).ledger.load_proof(request.path_params["proof_id"])
            contracts, df = load(request.query_params.get("dataset", "v1"))
        except (FileNotFoundError, ValueError):
            return JSONResponse({"error": "unknown proof"}, status_code=404)
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        item = next((c for c in contracts if c["contract_id"] == bundle["contract_id"]), None)
        if item is None:
            return JSONResponse({"error": "contract not in that dataset"}, status_code=404)
        ok, report = verify_bundle(bundle, contract_text(item), df)
        return JSONResponse({"verified": ok, "report": report})

    def open_escalations(ledger: Ledger):
        latest: Dict[str, dict] = {}
        for e in ledger.entries():
            latest[e["contract_id"]] = e
        return [e for e in latest.values() if e["verdict"] == "ESCALATED"]

    async def list_escalations(request: Request):
        meta, err = auth(request, "analyst")
        if err:
            return err
        return JSONResponse({"open": open_escalations(tenant_cfg(meta["tenant"]).ledger)})

    async def resolve(request: Request):
        meta, err = auth(request, "reviewer")
        if err:
            return err
        data = await body(request)
        if not data.get("reading_id"):
            return JSONResponse({"error": "reading_id is required"}, status_code=422)
        cfg = tenant_cfg(meta["tenant"])
        try:
            result = await run_in_threadpool(resolve_escalation, cfg, request.path_params["proof_id"], data["reading_id"],
                                             meta["user"], data.get("scenario"), str(data.get("note", ""))[:500])
        except FileNotFoundError:
            return JSONResponse({"error": "unknown proof"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        dispatch(result, LogConnector(root / meta["tenant"] / "outbox.jsonl"))
        return JSONResponse(result.model_dump())

    async def metrics(request: Request):
        meta, err = auth(request, "analyst")
        if err:
            return err
        ledger = tenant_cfg(meta["tenant"]).ledger
        entries = ledger.entries()
        audits = [e for e in entries if e["event"] == "audit_sealed"]
        by = {s: sum(1 for e in audits if e["verdict"] == s) for s in ("COMPLIANT", "BREACH", "ESCALATED")}
        human = sum(1 for e in entries if e["event"] == "human_resolution")
        n = len(audits)
        return JSONResponse({"audits": n, "verdicts": by, "automation_rate": round(1 - by["ESCALATED"] / n, 4) if n else None,
                             "human_resolutions": human, "open_escalations": len(open_escalations(ledger)),
                             "ledger_intact": ledger.verify_chain()[0]})

    async def ledger_verify(request: Request):
        meta, err = auth(request, "analyst")
        if err:
            return err
        ok, msg = tenant_cfg(meta["tenant"]).ledger.verify_chain()
        return JSONResponse({"intact": ok, "message": msg})

    return Starlette(routes=[
        Route("/healthz", healthz),
        Route("/audit", post_audit, methods=["POST"]),
        Route("/audit/{proof_id}/proof", get_proof),
        Route("/audit/{proof_id}/verify", verify_proof),
        Route("/escalations", list_escalations),
        Route("/escalations/{proof_id}/resolve", resolve, methods=["POST"]),
        Route("/metrics", metrics),
        Route("/ledger/verify", ledger_verify),
    ])


def app_factory() -> Starlette:  # uvicorn service.main:app_factory --factory
    return create_app()


def __getattr__(name):  # lets `uvicorn service.main:app` work without building the app at import time
    if name == "app":
        return create_app()
    raise AttributeError(name)
