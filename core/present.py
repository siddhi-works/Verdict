"""
core/present.py

Plain-language wording for results, shared by the Streamlit console and the web app so a reviewer sees the same
sentence everywhere. Pure functions over a result dict (or pandas row); no framework imports.
"""
from __future__ import annotations

from typing import Any, Dict, List

STATUS_LABEL = {"COMPLIANT": ("ok", "Compliant"), "BREACH": ("bad", "Breach"), "ESCALATED": ("warn", "Needs review")}
NOT_CALCULATED = {"not_quantifiable", "fee_unknown", "tier_not_found"}

BASIS_TEXT = {
    "contract_clause": "Rate stated in the contract",
    "contract_tiers": "Credit table in the contract",
    "assumed_default_rate": "Assumed rate, needs commercial review",
    "not_quantifiable": "No amount can be calculated",
    "fee_unknown": "Credit is a percentage of a monthly fee that was not provided",
    "tier_not_found": "The contract's credit table could not be read",
}

REASON_GROUPS = [
    ("outcome depends on how the contract is read", "The contract can be read two or more ways"),
    ("telemetry rows are trusted", "Doubtful telemetry rows decide the outcome"),
    ("no usable telemetry", "No usable telemetry"),
    ("mostly unusable", "Telemetry is mostly unusable"),
    ("vague or missing", "A threshold is vague or missing"),
    ("no numeric", "No numeric threshold found"),
    ("extractor failed", "The reader failed"),
]


def money(x: Any) -> str:
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "-"


def status_label(status: str) -> str:
    return STATUS_LABEL.get(status, ("", str(status).title()))[1]


def reason_group(reason: str) -> str:
    low = str(reason).lower()
    for key, label in REASON_GROUPS:
        if key in low:
            return label
    return "Other"


def plain_reason(row: Any) -> str:
    """One sentence a reviewer would write, built from the structured result (no engine jargon)."""
    status, raw = row["status"], str(row.get("reason", ""))
    env = row.get("envelope") if isinstance(row.get("envelope"), dict) else {}
    readings = {r["reading_id"]: r for r in env.get("readings", [])}
    cells = env.get("cells", [])
    many = len(readings) > 1
    if status == "COMPLIANT":
        return "All service levels were met" + (" under every reading of the contract." if many else ".")
    if status == "BREACH":
        worst = max(cells, key=lambda c: (c.get("uptime_shortfall", 0), c.get("resolution_excess", 0)), default=None)
        if worst and (worst.get("uptime_shortfall", 0) > 0 or worst.get("resolution_excess", 0) > 0):
            r = readings.get(worst["reading_id"], {})
            parts = []
            if worst.get("uptime_shortfall", 0) > 0 and r.get("uptime_threshold") is not None:
                parts.append(f"Fleet uptime was {worst['mean_uptime_percent']:.2f}% against a {r['uptime_threshold']:g}% target")
            if worst.get("resolution_excess", 0) > 0 and r.get("resolution_threshold_hours") is not None:
                scope = " ".join(r.get("scope_severities") or []).lower()
                parts.append(f"the slowest {scope + ' ' if scope else ''}repair took {worst['max_resolution_hours']:.1f} h "
                             f"against a {r['resolution_threshold_hours']:g} h limit")
            if parts:
                text = "; ".join(parts)
                return text[0].upper() + text[1:] + (", which holds under every reading of the contract." if many else ".")
        return raw.replace("Hard Gate: ", "").replace("SLA breach: ", "Service level missed: ") + "."
    low = raw.lower()
    if "extractor failed" in low:
        return "The contract could not be read automatically. Try the audit again or review it manually."
    if "no numeric" in low:
        return "No numeric service level could be found in the contract text."
    if "no usable telemetry" in low:
        return "No telemetry was received for this contract, so compliance cannot be judged."
    if "mostly unusable" in low:
        return "Most telemetry rows for this contract are invalid, so the data cannot be relied on."
    if "doubtful" in low and "read" in low:
        return "The result changes with both the contract wording and doubtful telemetry rows."
    if "doubtful" in low:
        return "The result depends on telemetry rows from a faulty sensor."
    if "how the contract is read" in low:
        return "The contract gives more than one value for the same requirement, and the result changes with which one applies."
    if "vague or missing" in low or "unchecked" in low:
        return "A requirement is described only in vague terms, so it cannot be checked against the telemetry."
    return raw.replace("Hard Gate: ", "").capitalize().rstrip(".") + "."


def credit_text(row: Any) -> str:
    if row["status"] != "BREACH":
        return "-"
    if row.get("penalty_basis") in NOT_CALCULATED:
        return "Not calculated"
    return money(row.get("penalty_usd", 0)) + (" (assumed rate)" if row.get("penalty_basis") == "assumed_default_rate" else "")


def reading_rows(env: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per (reading x telemetry view): the grid a reviewer checks before ruling."""
    if not env:
        return []
    terms = {r["reading_id"]: r for r in env.get("readings", [])}
    rows = []
    for c in env.get("cells", []):
        r = terms.get(c["reading_id"], {})
        up, rs = r.get("uptime_threshold"), r.get("resolution_threshold_hours")
        outcome = "Cannot be decided" if c["status"] == "UNDETERMINED" else status_label(c["status"])
        rows.append({
            "reading": c["reading_id"],
            "uptime_target": "not checked" if up is None else f"{up:g}%",
            "repair_limit": "not checked" if rs is None else f"{rs:g} h",
            "telemetry": {"strict": "Trusted rows only", "inclusive": "Including doubtful rows"}.get(c["scenario"], c["scenario"]),
            "fleet_uptime": None if c.get("mean_uptime_percent") is None else round(c["mean_uptime_percent"], 2),
            "slowest_repair_h": None if c.get("max_resolution_hours") is None else round(c["max_resolution_hours"], 1),
            "outcome": outcome,
            "credit": money(c.get("penalty_usd", 0)) if c["status"] == "BREACH" else "-",
        })
    return rows
