from typing import List, Optional

import pandas as pd

from agents.data_trust import build_views
from core.schemas import TelemetryMetrics


def parse_telemetry(
    contract_id: str,
    telemetry_df: pd.DataFrame,
    severities: Optional[List[str]] = None,
    scenario: str = "strict",
) -> TelemetryMetrics:
    """
    Deterministic aggregation of one contract's telemetry.

    `severities` restricts the repair-time metric to the events the contract
    actually covers (e.g. ["critical"]). Rows that cannot be trusted are
    dropped and reported; with no usable rows the metrics stay None so the
    gate escalates instead of guessing. `scenario` picks the strict or the
    inclusive data view (see agents/data_trust.py).
    """
    return build_views(contract_id, telemetry_df, severities)[scenario]
