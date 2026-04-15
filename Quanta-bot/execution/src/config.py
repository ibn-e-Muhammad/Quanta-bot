"""
config.py — Execution Engine Configuration

Centralized constants for paths, Layer 0 risk limits, and valid enums.
All limits sourced directly from /rules/trading-limits.md.
Zero external dependencies. Stdlib only.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent  # Quanta-bot/
RUNTIME_DIR: Path = _PROJECT_ROOT / "runtime"
TRADE_JOURNAL_PATH: Path = RUNTIME_DIR / "trade_journal.sqlite"
DECISION_LOG_PATH: Path = RUNTIME_DIR / "decision_log.md"

# ---------------------------------------------------------------------------
# Layer 0 Hard Limits (from /rules/trading-limits.md)
# ---------------------------------------------------------------------------
MAX_RISK_PCT: float = 0.02                    # 2% max risk per trade
MIN_RISK_PCT: float = 0.01                    # 1% min risk per trade
REDUCED_RISK_PCT: float = 0.01                # Reduced risk after 2 consec losses
MIN_RR_RATIO: float = 1.5                     # Minimum risk/reward ratio
MAX_LEVERAGE: float = 10.0                    # Absolute max leverage
MAX_DAILY_TRADES: int = 5                     # Max trades per day
MAX_CONSECUTIVE_LOSSES_HALT: int = 3          # Halt after 3 consecutive losses
CONSECUTIVE_LOSS_REDUCE_THRESHOLD: int = 2    # Reduce risk after 2 consec losses
DAILY_DRAWDOWN_FACTOR: float = 0.95           # Halt if balance <= start * 0.95
PEAK_DRAWDOWN_FACTOR: float = 0.95            # Halt if balance <= peak * 0.95
QUANTITY_PRECISION: int = 3                   # Decimal places for position size

# ---------------------------------------------------------------------------
# Valid enums
# ---------------------------------------------------------------------------
VALID_SIGNALS: set[str] = {"BUY", "SELL", "HOLD"}
VALID_ACTIONS: set[str] = {"EXECUTE", "REJECT"}
VALID_SYSTEM_STATUSES: set[str] = {"ACTIVE", "HALTED"}
