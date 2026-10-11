"""
core/precedents.py

Precedent memory. When a reviewer settles an escalation, we remember the
ruling against the clause PATTERN (not the contract): e.g. "uptime 97, repair
times 4 or 24 hours, critical events, no precedence clause -> the 24 hour
reading governs". The next contract with the same pattern gets that ruling as
a SUGGESTION. A human still approves it; the system never applies it alone.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.rules_ir import Enumeration, Reading


def _summary(reading: Reading) -> dict:
    r = reading.rules
    return {"uptime": r.required_uptime_percent, "resolution_hours": r.max_resolution_hours,
            "scope": r.scope_severities}


class PrecedentStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def record(self, fingerprint: str, reading: Reading, reviewer: str, contract_id: str, note: str = "") -> dict:
        data = self._load()
        entry = data.get(fingerprint) or {"rulings": 0, "history": []}
        entry["ruling"] = _summary(reading)
        entry["rulings"] += 1
        entry["history"].append({"contract_id": contract_id, "reviewer": reviewer, "note": note,
                                 "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        data[fingerprint] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return entry

    def suggest(self, enum: Enumeration) -> Optional[dict]:
        entry = self._load().get(enum.fingerprint)
        if not entry:
            return None
        for r in enum.readings:
            if _summary(r) == entry["ruling"]:
                return {"reading_id": r.reading_id, "ruling": entry["ruling"],
                        "based_on_rulings": entry["rulings"],
                        "note": "Suggestion only. A reviewer must approve it."}
        return None
