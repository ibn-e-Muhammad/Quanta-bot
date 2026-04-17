"""
state_writer.py — Atomic JSON Writer for /runtime/current_market_state.json

Validates the state dict, writes atomically via temp-file + os.replace,
and falls back to SAFE MODE on any failure.
"""

import json
import math
import os
import tempfile
from datetime import datetime, timezone

from . import config


# ---------------------------------------------------------------------------
# Valid enum sets
# ---------------------------------------------------------------------------
VALID_PRIMARY_STATES = {"TRENDING_UP", "TRENDING_DOWN", "RANGING", "SIDEWAYS"}
VALID_VOLATILITY_STATES = {"HIGH", "NORMAL", "LOW"}


# ---------------------------------------------------------------------------
# SAFE MODE payload (from data-context.md Section 7)
# ---------------------------------------------------------------------------
def build_safe_mode_payload(symbol: str = "UNKNOWN") -> dict:
    """Return the exact SAFE MODE JSON payload."""
    return {
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "price": 0.0,
        "ema_fast": 0.0,
        "ema_slow": 0.0,
        "ema_confirm": 0.0,
        "ema_trend": 0.0,
        "vwap": 0.0,
        "rsi": 50.0,
        "adx": 0.0,
        "atr": 0.0,
        "bb_lower": 0.0,
        "bb_upper": 0.0,
        "current_volume": 0.0,
        "volume_sma_20": 0.0,
        "state": {
            "primary": "SIDEWAYS",
            "volatility": "LOW",
        },
        "support_level": 0.0,
        "resistance_level": 0.0,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_state(state: dict) -> bool:
    """Return True if the state dict passes all validation rules.

    Rules (per data-context.md Section 6):
        - price > 0
        - No NaN or None values
        - state.primary ∈ {TRENDING_UP, TRENDING_DOWN, RANGING, SIDEWAYS}
        - state.volatility ∈ {HIGH, NORMAL, LOW}
    """
    try:
        # price > 0
        if state.get("price", 0) <= 0:
            return False

        # Check for None / NaN in numeric fields
        numeric_keys = [
            "price", "ema_fast", "ema_slow", "ema_confirm", "ema_trend", "vwap", "rsi", "adx", "atr",
            "bb_lower", "bb_upper", "current_volume", "volume_sma_20",
            "support_level", "resistance_level",
        ]
        for key in numeric_keys:
            val = state.get(key)
            if val is None:
                return False
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                return False

        # Validate state classification enums
        inner = state.get("state", {})
        if inner.get("primary") not in VALID_PRIMARY_STATES:
            return False
        if inner.get("volatility") not in VALID_VOLATILITY_STATES:
            return False

        return True

    except Exception:
        return False


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------
def write_state(state: dict, output_path: str | None = None) -> None:
    """Atomically write *state* to the output JSON file.

    Procedure:
        1. Serialize to JSON.
        2. Write to a temp file in the same directory.
        3. Validate with validate_state().
        4. Atomic rename (os.replace) temp → target.
        5. On ANY failure → write SAFE MODE payload instead.
    """
    if output_path is None:
        raise ValueError("output_path must be provided to write_state")

    target = os.path.abspath(output_path)
    target_dir = os.path.dirname(target)
    os.makedirs(target_dir, exist_ok=True)

    try:
        # Validate first
        if not validate_state(state):
            raise ValueError("State validation failed — entering SAFE MODE")

        _atomic_write(state, target, target_dir)

    except Exception:
        # SAFE MODE fallback — guaranteed write
        safe = build_safe_mode_payload(state.get("symbol", "UNKNOWN"))
        _atomic_write(safe, target, target_dir)


def _atomic_write(data: dict, target: str, target_dir: str) -> None:
    """Write JSON data atomically via temp file + os.replace."""
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, target)
    except Exception:
        # Clean up temp file if rename failed
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
