from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CreditTier(BaseModel):
    """One row of a service-credit table: uptime in [min_uptime, max_uptime) earns credit_percent of the monthly fee."""
    min_uptime: Optional[float] = None  # inclusive; None = no lower limit
    max_uptime: Optional[float] = None  # exclusive; None = no upper limit
    credit_percent: float
    quote: str = ""
    table: int = 0  # rows of one credit table share a number


class SLARules(BaseModel):
    """One fully-specified reading of a contract's numeric SLA terms."""
    contract_id: str
    required_uptime_percent: Optional[float] = None
    max_resolution_hours: Optional[float] = None
    # Which event severities the repair-time clause covers (None = every event).
    scope_severities: Optional[List[str]] = None
    # Penalty stated by the contract itself: USD per percentage point of uptime shortfall.
    penalty_usd_per_point: Optional[float] = None
    # Tiered credits (percent of the monthly fee) and the fee they apply to (from the order form, not the SLA text).
    credit_tiers: List[CreditTier] = Field(default_factory=list)
    monthly_fee_usd: Optional[float] = None
    # False when the contract profile says this SLA has no such clause (e.g. an uptime-only cloud SLA).
    uptime_applicable: bool = True
    resolution_applicable: bool = True
    conflict_detected: bool = False
    ambiguity_detected: bool = False
    ambiguity_notes: Optional[str] = None


class TelemetryMetrics(BaseModel):
    contract_id: str
    mean_uptime_percent: Optional[float] = None
    max_resolution_hours: Optional[float] = None
    corrupted_sensor_flag: bool = False
    valid_log_count: int = 0
    log_count: int = 0
    corruption_notes: Optional[str] = None
    # --- data-trust details (see agents/data_trust.py) ---
    scenario: str = "strict"
    unusable_log_count: int = 0
    suspect_log_count: int = 0
    resolution_event_count: int = 0
    max_resolution_log_id: Optional[int] = None
    data_grade: str = "A"


class AuditResult(BaseModel):
    contract_id: str
    status: str
    reason: str
    penalty_usd: float = 0.0
    execution_trace: List[str] = Field(default_factory=list)
    # How the penalty number was obtained: "contract_clause", "assumed_default_rate",
    # "not_quantifiable" or None (no penalty). Anything but "contract_clause" needs a human.
    penalty_basis: Optional[str] = None
    needs_commercial_review: bool = False
    # --- verdict-envelope details (filled by core/pipeline.py) ---
    exposure_min_usd: Optional[float] = None
    exposure_max_usd: Optional[float] = None
    envelope: Optional[Dict[str, Any]] = None
    brief: Optional[str] = None
    suggested_ruling: Optional[Dict[str, Any]] = None
    proof_id: Optional[str] = None
    trace_id: Optional[str] = None
    decided_by: str = "machine"
