"""
config.py — Market State Engine Configuration

Centralized constants for symbol, interval, candle limits, and file paths.
Reads heuristics from /runtime/config/strategy_config.json.
"""

import os
import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Project paths (relative to the Quanta-bot root)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # Quanta-bot/
RUNTIME_DIR = _PROJECT_ROOT / "runtime"
MARKET_STATES_DIR = RUNTIME_DIR / "market_states"
STRATEGY_CONFIG_PATH = RUNTIME_DIR / "config" / "strategy_config.json"

def get_state_path(symbol: str, interval: str = "15m") -> Path:
    return MARKET_STATES_DIR / f"{symbol}_{interval}.json"

# ---------------------------------------------------------------------------
# Binance API
# ---------------------------------------------------------------------------
API_BASE_URL = "https://fapi.binance.com"
KLINES_ENDPOINT = "/fapi/v1/klines"

# ---------------------------------------------------------------------------
# Market parameters (overridable via config/env)
# ---------------------------------------------------------------------------
try:
    with open(STRATEGY_CONFIG_PATH, "r") as f:
        _strat_config = json.load(f)
        _md_config = _strat_config.get("market_data", {})
except Exception:
    _md_config = {}

SYMBOL: str = os.environ.get("SYMBOL", "BTCUSDT")
INTERVAL: str = _md_config.get("timeframe", os.environ.get("INTERVAL", "15m"))
CANDLE_LIMIT: int = _md_config.get("limit", int(os.environ.get("CANDLE_LIMIT", "200")))

EMA_FAST: int = _md_config.get("ema_fast", 9)
EMA_SLOW: int = _md_config.get("ema_slow", 24)
EMA_CONFIRM: int = _md_config.get("ema_confirm", 50)
EMA_TREND: int = _md_config.get("ema_trend", 200)
RSI_PERIOD: int = _md_config.get("rsi_period", 14)
ADX_PERIOD: int = _md_config.get("adx_period", 14)
ATR_PERIOD: int = _md_config.get("atr_period", 14)
BB_WINDOW: int = _md_config.get("bb_window", 20)

# Minimum candle limit required for EMA-200 + volume SMA reliability
assert CANDLE_LIMIT >= EMA_TREND, f"CANDLE_LIMIT must be >= {EMA_TREND}"

# ---------------------------------------------------------------------------
# Rate-limit / retry settings
# ---------------------------------------------------------------------------
MAX_RETRIES: int = 3
BACKOFF_BASE_SECONDS: float = 1.0  # 1s → 2s → 4s  (exponential)
