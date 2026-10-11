"""Small builders shared by the test files."""
from typing import List, Optional

import pandas as pd

from core.pipeline import PipelineConfig, audit_contract


def telemetry(cid: str, rows: List[dict]) -> pd.DataFrame:
    """rows: dicts with truck, up, res (None = no repair), sev, plus optional status/planned/event."""
    out = []
    for i, r in enumerate(rows, start=1):
        out.append({
            "log_id": r.get("log_id", i), "contract_id": cid, "truck_id": r.get("truck", f"TRK-{i}"),
            "event_type": r.get("event", "engine_fault" if r.get("res") is not None else "normal"),
            "severity": r.get("sev", "critical"), "resolution_hours": r.get("res"),
            "actual_uptime_percent": r.get("up", 99.0), "sensor_status": r.get("status", "OK"),
            "planned_maintenance": r.get("planned", False),
        })
    return pd.DataFrame(out)


def contract(cid: str, text: str) -> dict:
    return {"contract_id": cid, "contract_text": text}


def audit(item: dict, df: pd.DataFrame, cfg: Optional[PipelineConfig] = None):
    return audit_contract(item, df, cfg or PipelineConfig())


CLEAN = ("Monthly fleet uptime shall be maintained at not less than 98%. "
         "Each Critical Event shall be restored to operational status within 12 hours of notification.")
