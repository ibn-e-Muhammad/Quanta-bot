"""
config.py — Market State Engine Configuration

Centralized constants for symbol, interval, candle limits, and file paths.
Reads SYMBOL and INTERVAL from environment variables with sane defaults.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Project paths (relative to the Quanta-bot root)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # Quanta-bot/
RUNTIME_DIR = _PROJECT_ROOT / "runtime"
STATE_FILE_PATH = RUNTIME_DIR / "current_market_state.json"

# ---------------------------------------------------------------------------
# Binance API
# ---------------------------------------------------------------------------
API_BASE_URL = "https://api.binance.com"
KLINES_ENDPOINT = "/api/v3/klines"

# ---------------------------------------------------------------------------
# Market parameters (overridable via env vars)
# ---------------------------------------------------------------------------
SYMBOL: str = os.environ.get("SYMBOL", "BTCUSDT")
INTERVAL: str = os.environ.get("INTERVAL", "1h")
CANDLE_LIMIT: int = int(os.environ.get("CANDLE_LIMIT", "200"))

# Minimum candle limit required for EMA-200 + volume SMA reliability
assert CANDLE_LIMIT >= 200, "CANDLE_LIMIT must be >= 200"

# ---------------------------------------------------------------------------
# Rate-limit / retry settings
# ---------------------------------------------------------------------------
MAX_RETRIES: int = 3
BACKOFF_BASE_SECONDS: float = 1.0  # 1s → 2s → 4s  (exponential)
