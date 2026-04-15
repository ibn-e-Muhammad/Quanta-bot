"""
config.py — Research Lab Configuration

Paths, constants, and thresholds.
Zero external dependencies. Stdlib only.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent  # Quanta-bot/
RUNTIME_DIR: Path = _PROJECT_ROOT / "runtime"
TRADE_JOURNAL_PATH: Path = RUNTIME_DIR / "trade_journal.sqlite"
SNAPSHOT_PATH: Path = RUNTIME_DIR / "performance_snapshot.json"

# ---------------------------------------------------------------------------
# Analysis thresholds
# ---------------------------------------------------------------------------
LOOKBACK_DAYS: int = 30                          # Query last N operational days
STRATEGY_UNDERPERFORM_WIN_RATE: float = 40.0     # Flag if win rate < 40%
