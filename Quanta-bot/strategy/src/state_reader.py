"""
state_reader.py — Read & Validate Market State JSON

Single public function: read_market_state()
Returns a validated dict or None on any validation failure.
Uses only stdlib: json, math. No numpy, no requests.
"""

import json
import math
from pathlib import Path
from typing import Any

from . import config


# ---------------------------------------------------------------------------
# Required keys in the market state JSON
# ---------------------------------------------------------------------------
_NUMERIC_KEYS: list[str] = [
    "price", "ema_20", "ema_50", "vwap", "rsi", "adx", "atr",
    "bb_lower", "bb_upper", "current_volume", "volume_sma_20",
    "support_level", "resistance_level",
]

_REQUIRED_KEYS: list[str] = [
    "symbol", "timestamp", *_NUMERIC_KEYS, "state",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def read_market_state(path: str | None = None) -> dict | None:
    """Read and validate the market state JSON.

    Parameters
    ----------
    path : str | None
        Path to the JSON file. Defaults to config.STATE_FILE_PATH.

    Returns
    -------
    dict | None
        Validated market state dict, or None if any validation fails.
    """
    file_path: str = path if path is not None else str(config.STATE_FILE_PATH)

    try:
        with open(file_path, "r") as f:
            state: dict = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if _validate(state):
        return state
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate(state: Any) -> bool:
    """Return True if state passes all validation rules."""
    if not isinstance(state, dict):
        return False

    # Check all required top-level keys exist
    for key in _REQUIRED_KEYS:
        if key not in state:
            return False

    # Validate numeric fields: not None, not NaN, not Inf
    for key in _NUMERIC_KEYS:
        val = state.get(key)
        if val is None:
            return False
        if not isinstance(val, (int, float)):
            return False
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return False

    # price > 0
    if state["price"] <= 0:
        return False

    # Validate nested state dict
    inner = state.get("state")
    if not isinstance(inner, dict):
        return False
    if inner.get("primary") not in config.VALID_PRIMARY_STATES:
        return False
    if inner.get("volatility") not in config.VALID_VOLATILITY_STATES:
        return False

    return True
