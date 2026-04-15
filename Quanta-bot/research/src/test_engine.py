"""
test_engine.py — Integration tests for the full Research Lab pipeline

Tests:
  - Populated DB → valid snapshot with correct metrics
  - Empty DB → zero-state snapshot
  - Missing DB → zero-state snapshot
  - Strategy degradation flagged correctly
"""

import json
import sqlite3
from datetime import datetime, timezone
import pytest

from research.src.engine import run_research_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_CREATE_TABLE = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    strategy_used TEXT,
    risk_usd REAL NOT NULL,
    pnl_usd REAL NOT NULL
);
"""


def _insert_trade(conn, strategy: str, pnl: float, action: str = "BUY"):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, action, strategy_used, risk_usd, pnl_usd) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now, "BTCUSDT", action, strategy, 200.0, pnl),
    )


def _build_populated_db(db_path: str):
    """Create a DB with 10 mixed trades across 2 strategies."""
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_TABLE)
    # Trend_Pullback: 4 wins, 1 loss → win_rate=80%, net_pnl=220
    _insert_trade(conn, "Trend_Pullback", 100.0)
    _insert_trade(conn, "Trend_Pullback", 50.0)
    _insert_trade(conn, "Trend_Pullback", 80.0)
    _insert_trade(conn, "Trend_Pullback", 40.0)
    _insert_trade(conn, "Trend_Pullback", -50.0)
    # Range: 1 win, 4 losses → win_rate=20%, net_pnl=-170
    _insert_trade(conn, "Range", 30.0)
    _insert_trade(conn, "Range", -50.0)
    _insert_trade(conn, "Range", -60.0)
    _insert_trade(conn, "Range", -40.0)
    _insert_trade(conn, "Range", -50.0)
    conn.commit()
    conn.close()


# ===========================================================================
# Happy Path
# ===========================================================================
class TestEngineHappyPath:
    def test_populated_db_produces_valid_snapshot(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        snap = str(tmp_path / "snapshot.json")
        _build_populated_db(db)
        result = run_research_engine(db_path=db, snapshot_path=snap)
        assert result["total_trades"] == 10
        assert result["global_win_rate"] == 50.0  # 5/10

    def test_snapshot_file_created(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        snap = str(tmp_path / "snapshot.json")
        _build_populated_db(db)
        run_research_engine(db_path=db, snapshot_path=snap)
        with open(snap) as f:
            loaded = json.load(f)
        assert loaded["total_trades"] == 10

    def test_has_all_output_keys(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        snap = str(tmp_path / "snapshot.json")
        _build_populated_db(db)
        result = run_research_engine(db_path=db, snapshot_path=snap)
        required = [
            "timestamp", "total_trades", "global_win_rate",
            "average_rr", "current_drawdown_pct", "strategy_performance",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_average_rr_computed(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        snap = str(tmp_path / "snapshot.json")
        _build_populated_db(db)
        result = run_research_engine(db_path=db, snapshot_path=snap)
        assert result["average_rr"] > 0

    def test_drawdown_computed(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        snap = str(tmp_path / "snapshot.json")
        _build_populated_db(db)
        result = run_research_engine(db_path=db, snapshot_path=snap)
        assert result["current_drawdown_pct"] >= 0.0


# ===========================================================================
# Strategy Degradation
# ===========================================================================
class TestStrategyDegradation:
    def test_underperforming_flagged(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        snap = str(tmp_path / "snapshot.json")
        _build_populated_db(db)
        result = run_research_engine(db_path=db, snapshot_path=snap)
        perf = result["strategy_performance"]
        range_strat = next(s for s in perf if s["strategy_name"] == "Range")
        assert range_strat["status"] == "UNDERPERFORMING"

    def test_optimal_flagged(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        snap = str(tmp_path / "snapshot.json")
        _build_populated_db(db)
        result = run_research_engine(db_path=db, snapshot_path=snap)
        perf = result["strategy_performance"]
        trend_strat = next(s for s in perf if s["strategy_name"] == "Trend_Pullback")
        assert trend_strat["status"] == "OPTIMAL"


# ===========================================================================
# Zero-State Cases
# ===========================================================================
class TestZeroState:
    def test_empty_db_produces_zero_state(self, tmp_path):
        db = str(tmp_path / "empty.sqlite")
        snap = str(tmp_path / "snapshot.json")
        conn = sqlite3.connect(db)
        conn.execute(_CREATE_TABLE)
        conn.commit()
        conn.close()
        result = run_research_engine(db_path=db, snapshot_path=snap)
        assert result["total_trades"] == 0
        assert result["global_win_rate"] == 0.0
        assert result["strategy_performance"] == []

    def test_missing_db_produces_zero_state(self, tmp_path):
        snap = str(tmp_path / "snapshot.json")
        result = run_research_engine(
            db_path="/nonexistent/journal.sqlite",
            snapshot_path=snap,
        )
        assert result["total_trades"] == 0

    def test_corrupt_db_produces_zero_state(self, tmp_path):
        db = str(tmp_path / "corrupt.sqlite")
        snap = str(tmp_path / "snapshot.json")
        with open(db, "w") as f:
            f.write("not a sqlite database")
        result = run_research_engine(db_path=db, snapshot_path=snap)
        assert result["total_trades"] == 0
