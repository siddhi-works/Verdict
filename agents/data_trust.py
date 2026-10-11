"""
agents/data_trust.py

Data Trust Engine. Deterministic (no AI): cleans a contract's telemetry and
produces TWO views of it instead of one:

  strict     - only rows we fully trust
  inclusive  - trusted rows + "suspect" rows (sensor FAULT, duplicates, a
               flat 0% uptime reading) that might be real

Rows that cannot be interpreted at all (NaN, uptime outside 0-100, negative
durations) are "unusable" and are dropped in BOTH views; we never invent data.

If the two views give different verdicts, the data itself is in doubt and the
envelope (core/envelope.py) escalates. A data-quality grade (A-D) summarises
how much of the contract's telemetry had to be set aside.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional

import pandas as pd

from core.schemas import TelemetryMetrics

SCENARIOS = ("strict", "inclusive")
GRADE_D_UNUSABLE_FRACTION = 0.30


def _truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(("1", "true", "yes", "y"))


def classify_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Add helper columns: _res, _up, _class (clean/suspect/unusable), _planned, _why."""
    out = df.copy()
    out["_res"] = pd.to_numeric(out["resolution_hours"], errors="coerce")
    out["_up"] = pd.to_numeric(out["actual_uptime_percent"], errors="coerce")
    event = out["event_type"].astype(str).str.lower() if "event_type" in out.columns else pd.Series("", index=out.index)
    is_normal = event == "normal"

    # A "normal" row is not a repair, so a blank resolution time is legitimate there.
    res_bad = (out["_res"] < 0) | (out["_res"].isna() & ~is_normal)
    unusable = out["_up"].isna() | (out["_up"] < 0) | (out["_up"] > 100) | res_bad
    if "downtime_minutes" in out.columns:
        unusable |= pd.to_numeric(out["downtime_minutes"], errors="coerce") < 0

    sensor = out["sensor_status"].astype(str).str.upper()
    dup_cols = [c for c in out.columns if not c.startswith("_") and c != "log_id"]
    duplicate = out.duplicated(subset=dup_cols, keep="first")
    suspect = ((sensor == "FAULT") | duplicate | (out["_up"] == 0)) & ~unusable

    out["_class"] = "clean"
    out.loc[suspect, "_class"] = "suspect"
    out.loc[unusable, "_class"] = "unusable"
    out["_planned"] = _truthy(out["planned_maintenance"]) if "planned_maintenance" in out.columns else False
    why = pd.Series("", index=out.index)
    why[unusable] = "unusable value"
    why[suspect & (sensor == "FAULT")] = "sensor FAULT"
    why[suspect & duplicate] = "duplicate row"
    why[suspect & (out["_up"] == 0) & (why == "")] = "flat 0% uptime"
    out["_why"] = why
    return out


def grade(classified: pd.DataFrame) -> str:
    n = len(classified)
    if n == 0:
        return "D"
    bad_u = int((classified["_class"] == "unusable").sum())
    bad_s = int((classified["_class"] == "suspect").sum())
    if bad_u / n > GRADE_D_UNUSABLE_FRACTION or bad_u == n:
        return "D"
    if bad_u + bad_s == 0:
        return "A"
    if (bad_u + bad_s) / n <= 0.10:
        return "B"
    return "C"


def contract_digest(df: pd.DataFrame) -> str:
    """Stable hash of a contract's raw telemetry rows (used by the proof bundle)."""
    canon = df.sort_values("log_id").to_csv(index=False) if "log_id" in df.columns else df.to_csv(index=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def compute_metrics(
    classified: pd.DataFrame,
    contract_id: str,
    scenario: str,
    severities: Optional[List[str]] = None,
) -> TelemetryMetrics:
    """Aggregate one data view. `severities` limits which events count for repair time."""
    total = len(classified)
    n_unusable = int((classified["_class"] == "unusable").sum())
    n_suspect = int((classified["_class"] == "suspect").sum())
    allowed = ("clean",) if scenario == "strict" else ("clean", "suspect")
    used = classified[classified["_class"].isin(allowed)]

    notes: List[str] = []
    if n_unusable:
        notes.append(f"{n_unusable} unusable telemetry row(s) dropped")
    if n_suspect:
        notes.append(f"{n_suspect} suspect row(s) {'excluded' if scenario == 'strict' else 'included'}")
    flagged = classified[classified["_class"] != "clean"]
    if "truck_id" in classified.columns and len(flagged) and len(flagged) <= 8:
        notes.append("rows: " + ", ".join(flagged["truck_id"].astype(str).tolist()))

    metrics = TelemetryMetrics(
        contract_id=contract_id,
        scenario=scenario,
        log_count=total,
        valid_log_count=len(used),
        unusable_log_count=n_unusable,
        suspect_log_count=n_suspect,
        corrupted_sensor_flag=bool(n_unusable or (n_suspect and scenario == "strict")),
        corruption_notes="; ".join(notes) or None,
        data_grade=grade(classified),
    )
    if used.empty:
        return metrics

    # Fleet uptime: average per truck first so a truck with many events doesn't count more.
    if "truck_id" in used.columns:
        metrics.mean_uptime_percent = float(used.groupby(used["truck_id"].astype(str))["_up"].mean().mean())
    else:
        metrics.mean_uptime_percent = float(used["_up"].mean())

    events = used[~used["_planned"] & used["_res"].notna()]  # planned maintenance is excluded by contract
    if severities:
        wanted = {s.lower() for s in severities}
        events = events[events["severity"].astype(str).str.lower().isin(wanted)]
    metrics.resolution_event_count = len(events)
    if len(events):
        top = events.loc[events["_res"].idxmax()]
        metrics.max_resolution_hours = float(top["_res"])
        if "log_id" in events.columns and pd.notna(top["log_id"]):
            metrics.max_resolution_log_id = int(top["log_id"])
    return metrics


def build_views(
    contract_id: str, telemetry_df: pd.DataFrame, severities: Optional[List[str]] = None
) -> Dict[str, TelemetryMetrics]:
    """Both data views for a contract, keyed by scenario name."""
    required = {"contract_id", "resolution_hours", "actual_uptime_percent", "sensor_status"}
    missing = required - set(telemetry_df.columns)
    if missing:
        raise ValueError(f"Missing telemetry columns: {sorted(missing)}")
    rows = telemetry_df[telemetry_df["contract_id"].astype(str) == str(contract_id)]
    if rows.empty:
        empty = TelemetryMetrics(contract_id=contract_id, log_count=0, corruption_notes="No telemetry rows for contract", data_grade="D")
        return {s: empty.model_copy(update={"scenario": s}) for s in SCENARIOS}
    classified = classify_rows(rows)
    return {s: compute_metrics(classified, contract_id, s, severities) for s in SCENARIOS}
