"""
signal_intake.py — Parse & Validate Strategy Signal

Single public function: validate_signal()
Returns validated dict or None on any validation failure.
Stdlib only.
"""

import math
from typing import Any

from . import config


# ---------------------------------------------------------------------------
# Required keys in the strategy signal
# ---------------------------------------------------------------------------
_REQUIRED_KEYS: list[str] = [
    "timestamp", "symbol", "signal", "strategy_used",
    "confidence_score", "suggested_entry", "suggested_sl",
    "suggested_tp", "reason",
]


def validate_signal(signal: Any) -> dict | None:
    """Validate a strategy signal dict.

    Returns
    -------
    dict | None
        Validated signal dict, or None if any validation fails.
    """
    if not isinstance(signal, dict):
        return None

    # Check all required keys
    for key in _REQUIRED_KEYS:
        if key not in signal:
            return None

    # Signal type must be valid
    sig_type: str = signal.get("signal", "")
    if sig_type not in config.VALID_SIGNALS:
        return None

    # For BUY/SELL: entry, sl, tp must be non-null positive floats
    if sig_type in ("BUY", "SELL"):
        for key in ("suggested_entry", "suggested_sl", "suggested_tp"):
            val = signal.get(key)
            if val is None:
                return None
            if not isinstance(val, (int, float)):
                return None
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                return None
            if val <= 0:
                return None

    return signal
