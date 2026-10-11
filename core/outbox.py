"""
core/outbox.py

Turns a verdict into business actions and queues them (append-only JSONL).
Real systems (ERP, ticketing) plug in by implementing `Connector`; the default
`LogConnector` only writes the queue, so nothing leaves the machine in a demo.

  COMPLIANT -> release invoice
  BREACH    -> draft credit memo (needs approval above a dollar limit,
               or whenever the penalty number is not backed by the contract)
  ESCALATED -> hold invoice + open a review ticket
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Protocol

from core.schemas import AuditResult

APPROVAL_LIMIT_USD = float(os.getenv("SLA_APPROVAL_LIMIT_USD", "5000"))


class Connector(Protocol):
    def send(self, action: dict) -> None: ...


class LogConnector:
    def __init__(self, path: Path):
        self.path = Path(path)

    def send(self, action: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(action, sort_keys=True) + "\n")


def plan_actions(result: AuditResult, limit: float = APPROVAL_LIMIT_USD) -> List[dict]:
    base = {"contract_id": result.contract_id, "proof_id": result.proof_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if result.status == "COMPLIANT":
        return [{**base, "action": "release_invoice", "approval": "not_required"}]
    if result.status == "BREACH":
        needs = result.needs_commercial_review or result.penalty_usd > limit
        return [{**base, "action": "draft_credit_memo", "amount_usd": result.penalty_usd,
                 "approval": "required" if needs else "not_required",
                 "approval_reason": ("penalty not backed by a contract clause" if result.needs_commercial_review
                                     else f"amount above ${limit:,.0f} limit" if needs else None)}]
    return [{**base, "action": "hold_invoice", "approval": "not_required"},
            {**base, "action": "open_review_ticket", "approval": "not_required",
             "exposure_usd": [result.exposure_min_usd, result.exposure_max_usd]}]


def dispatch(result: AuditResult, connector: Connector) -> List[dict]:
    actions = plan_actions(result)
    for a in actions:
        connector.send(a)
    return actions
