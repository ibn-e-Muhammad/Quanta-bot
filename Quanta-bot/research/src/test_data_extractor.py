"""
test_data_extractor.py — Unit tests for SQLite read-only query engine

Tests: valid DB with pnl_usd → trade list, empty DB → [], missing DB → [],
       DB without pnl_usd column → [].
"""

import sqlite3
from datetime import datetime, timezone
import pytest

from research.src.data_extractor import extract_trades


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_CREATE_TABLE_WITH_PNL = """
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

_CREATE_TABLE_WITHOUT_PNL = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    strategy_used TEXT,
    risk_usd REAL NOT NULL
);
"""


def _populate_db(db_path: str, with_pnl: bool = True, num_rows: int = 5):
    """Create and populate a test database."""
    conn = sqlite3.connect(db_path)
    if with_pnl:
        conn.execute(_CREATE_TABLE_WITH_PNL)
    else:
        conn.execute(_CREATE_TABLE_WITHOUT_PNL)

    now = datetime.now(timezone.utc).isoformat()
    for i in range(num_rows):
        if with_pnl:
            conn.execute(
                "INSERT INTO trades (timestamp, symbol, action, strategy_used, risk_usd, pnl_usd) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, "BTCUSDT", "BUY", "Trend_Pullback", 200.0, 50.0 if i % 2 == 0 else -30.0),
            )
        else:
            conn.execute(
                "INSERT INTO trades (timestamp, symbol, action, strategy_used, risk_usd) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, "BTCUSDT", "BUY", "Trend_Pullback", 200.0),
            )
    conn.commit()
    conn.close()


# ===========================================================================
# Valid Database
# ===========================================================================
class TestValidDatabase:
    def test_returns_trade_list(self, tmp_path):
        db = str(tmp_path / "test.sqlite")
        _populate_db(db, with_pnl=True, num_rows=5)
        trades = extract_trades(db)
        assert isinstance(trades, list)
        assert len(trades) == 5

    def test_trade_has_required_keys(self, tmp_path):
        db = str(tmp_path / "test.sqlite")
        _populate_db(db, with_pnl=True, num_rows=1)
        trades = extract_trades(db)
        assert len(trades) == 1
        trade = trades[0]
        for key in ("timestamp", "symbol", "action", "strategy_used", "risk_usd", "pnl_usd"):
            assert key in trade, f"Missing key: {key}"

    def test_pnl_values_correct(self, tmp_path):
        db = str(tmp_path / "test.sqlite")
        _populate_db(db, with_pnl=True, num_rows=2)
        trades = extract_trades(db)
        assert trades[0]["pnl_usd"] == 50.0   # i=0, even → win
        assert trades[1]["pnl_usd"] == -30.0   # i=1, odd → loss


# ===========================================================================
# Edge Cases
# ===========================================================================
class TestEdgeCases:
    def test_missing_db_returns_empty(self):
        trades = extract_trades("/nonexistent/path.sqlite")
        assert trades == []

    def test_empty_db_returns_empty(self, tmp_path):
        db = str(tmp_path / "empty.sqlite")
        conn = sqlite3.connect(db)
        conn.execute(_CREATE_TABLE_WITH_PNL)
        conn.commit()
        conn.close()
        trades = extract_trades(db)
        assert trades == []

    def test_db_without_pnl_column_returns_empty(self, tmp_path):
        db = str(tmp_path / "no_pnl.sqlite")
        _populate_db(db, with_pnl=False, num_rows=3)
        trades = extract_trades(db)
        assert trades == []

    def test_db_without_trades_table_returns_empty(self, tmp_path):
        db = str(tmp_path / "no_table.sqlite")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        trades = extract_trades(db)
        assert trades == []
