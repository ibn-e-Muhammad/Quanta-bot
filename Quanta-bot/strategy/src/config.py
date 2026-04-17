"""
config.py — Strategy Engine Configuration

Centralized constants for paths, valid states, and strategy thresholds.
Zero external dependencies. Stdlib only.
"""

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Project paths (relative to the Quanta-bot root)
# ---------------------------------------------------------------------------
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent  # Quanta-bot/
RUNTIME_DIR: Path = _PROJECT_ROOT / "runtime"
MARKET_STATES_DIR: Path = RUNTIME_DIR / "market_states"
STRATEGY_CONFIG_PATH: Path = RUNTIME_DIR / "config" / "strategy_config.json"
SCORING_CONFIG_PATH: Path = RUNTIME_DIR / "config" / "scoring_config.json"

def get_state_path(symbol: str, interval: str = "15m") -> Path:
    return MARKET_STATES_DIR / f"{symbol}_{interval}.json"

# ---------------------------------------------------------------------------
# Valid enum values
# ---------------------------------------------------------------------------
VALID_PRIMARY_STATES: set[str] = {"TRENDING_UP", "TRENDING_DOWN", "RANGING", "SIDEWAYS"}
VALID_VOLATILITY_STATES: set[str] = {"HIGH", "NORMAL", "LOW"}
VALID_SIGNALS: set[str] = {"BUY", "SELL", "HOLD"}

# ---------------------------------------------------------------------------
# Strategy thresholds (from strategy-context.md)
# ---------------------------------------------------------------------------
try:
    with open(STRATEGY_CONFIG_PATH, "r") as f:
        _strat_config = json.load(f)
        _thresh = _strat_config.get("strategy_thresholds", {})
except Exception:
    _thresh = {}

try:
    with open(SCORING_CONFIG_PATH, "r") as f:
        _scoring_weights = json.load(f).get("weights", {})
except Exception:
    _scoring_weights = {}

VOLUME_CONFIRM_MULTIPLIER: float = _thresh.get("volume_trend_multiplier", 1.2)
BREAKOUT_VOLUME_MULTIPLIER: float = _thresh.get("volume_breakout_multiplier", 2.0)
EMA_PROXIMITY_PCT: float = _thresh.get("ema_proximity_pct", 0.002)
BREAKOUT_PRICE_THRESHOLD: float = _thresh.get("breakout_buffer_pct", 0.001)
ADX_TREND_THRESHOLD: float = _thresh.get("adx_min", 25.0)
RSI_OVERSOLD: float = _thresh.get("rsi_oversold", 30.0)
RSI_OVERBOUGHT: float = _thresh.get("rsi_overbought", 70.0)
MIN_RR_RATIO: float = _thresh.get("min_rr_ratio", 1.5)

WEIGHT_CONFIDENCE: float = _scoring_weights.get("confidence", 0.5)
WEIGHT_RR: float = _scoring_weights.get("rr", 0.3)
WEIGHT_ADX: float = _scoring_weights.get("adx", 0.2)
