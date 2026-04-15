"""
config.py — Strategy Engine Configuration

Centralized constants for paths, valid states, and strategy thresholds.
Zero external dependencies. Stdlib only.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Project paths (relative to the Quanta-bot root)
# ---------------------------------------------------------------------------
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent  # Quanta-bot/
RUNTIME_DIR: Path = _PROJECT_ROOT / "runtime"
STATE_FILE_PATH: Path = RUNTIME_DIR / "current_market_state.json"

# ---------------------------------------------------------------------------
# Valid enum values
# ---------------------------------------------------------------------------
VALID_PRIMARY_STATES: set[str] = {"TRENDING_UP", "TRENDING_DOWN", "RANGING", "SIDEWAYS"}
VALID_VOLATILITY_STATES: set[str] = {"HIGH", "NORMAL", "LOW"}
VALID_SIGNALS: set[str] = {"BUY", "SELL", "HOLD"}

# ---------------------------------------------------------------------------
# Strategy thresholds (from strategy-context.md)
# ---------------------------------------------------------------------------
VOLUME_CONFIRM_MULTIPLIER: float = 1.2       # Trend: volume >= vol_sma * 1.2
BREAKOUT_VOLUME_MULTIPLIER: float = 2.0      # Breakout: volume >= vol_sma * 2.0
EMA_PROXIMITY_PCT: float = 0.002             # ±0.2% of EMA_20
MIN_RR_RATIO: float = 1.5                    # Minimum risk/reward ratio
BREAKOUT_PRICE_THRESHOLD: float = 0.001      # 0.1% beyond S/R level
ADX_TREND_THRESHOLD: float = 25.0            # ADX >= 25 = trend valid
RSI_OVERSOLD: float = 30.0                   # RSI <= 30 = oversold
RSI_OVERBOUGHT: float = 70.0                 # RSI >= 70 = overbought
