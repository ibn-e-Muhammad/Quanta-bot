"""
account_state.py — Parse & Validate Account State

Single public function: validate_account_state()
Returns validated dict or None on any validation failure.
Stdlib only.
"""

import math
from typing import Any

from . import config


# ---------------------------------------------------------------------------
# Required keys in the account state
# ---------------------------------------------------------------------------
_REQUIRED_KEYS: list[str] = [
    "account_balance", "daily_equity_start", "daily_peak_equity",
    "daily_trade_count", "consecutive_losses", "system_status",
]

_NUMERIC_KEYS: list[str] = [
    "account_balance", "daily_equity_start", "daily_peak_equity",
    "daily_trade_count", "consecutive_losses",
]


def validate_account_state(state: Any) -> dict | None:
    """Validate an account state dict.

    Returns
    -------
    dict | None
        Validated account state dict, or None if any validation fails.
    """
    if not isinstance(state, dict):
        return None

    # Check all required keys
    for key in _REQUIRED_KEYS:
        if key not in state:
            return None

    # Validate numeric fields
    for key in _NUMERIC_KEYS:
        val = state.get(key)
        if val is None:
            return None
        if not isinstance(val, (int, float)):
            return None
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return None

    # account_balance must be positive
    if state["account_balance"] <= 0:
        return None

    # system_status must be valid
    if state.get("system_status") not in config.VALID_SYSTEM_STATUSES:
        return None

    return state
