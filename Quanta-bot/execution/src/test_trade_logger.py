"""
test_trade_logger.py — Unit tests for SQLite trade journal & decision log

Tests: table creation, row insertion, decision log append.
"""

import sqlite3
import os
import pytest

from execution.src.trade_logger import log_trade, log_decision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _trade_data() -> dict:
    return {
        "timestamp": "2026-04-16T00:00:00+00:00",
        "symbol": "BTCUSDT",
        "action": "BUY",
        "size": 0.2,
        "leverage_used": 1.28,
        "entry_price": 64000.0,
        "sl_price": 63000.0,
        "tp_price": 66000.0,
        "risk_usd": 200.0,
        "strategy_used": "Trend_Pullback",
        "confidence_score": 0.75,
        "reason": "Trend pullback BUY",
    }


def _decision_data() -> dict:
    return {
        "timestamp": "2026-04-16T00:00:00+00:00",
        "action": "EXECUTE",
        "symbol": "BTCUSDT",
        "signal": "BUY",
        "strategy_used": "Trend_Pullback",
        "size": 0.2,
        "leverage": 1.28,
        "rr": 2.0,
        "risk_usd": 200.0,
        "reason": "Trade approved",
    }


# ===========================================================================
# SQLite Trade Journal
# ===========================================================================
class TestLogTrade:
    def test_creates_database_and_table(self, tmp_path):
        db = str(tmp_path / "test.sqlite")
        log_trade(_trade_data(), db_path=db)
        assert os.path.exists(db)

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        assert "trades" in tables

    def test_inserts_row(self, tmp_path):
        db = str(tmp_path / "test.sqlite")
        log_trade(_trade_data(), db_path=db)

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT COUNT(*) FROM trades")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 1

    def test_row_data_matches(self, tmp_path):
        db = str(tmp_path / "test.sqlite")
        data = _trade_data()
        log_trade(data, db_path=db)

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM trades WHERE id=1")
        row = cursor.fetchone()
        conn.close()

        assert row["symbol"] == "BTCUSDT"
        assert row["action"] == "BUY"
        assert row["size"] == 0.2
        assert row["entry_price"] == 64000.0
        assert row["risk_usd"] == 200.0

    def test_multiple_inserts(self, tmp_path):
        db = str(tmp_path / "test.sqlite")
        log_trade(_trade_data(), db_path=db)
        log_trade(_trade_data(), db_path=db)

        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT COUNT(*) FROM trades")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 2

    def test_creates_parent_directory(self, tmp_path):
        db = str(tmp_path / "nested" / "dir" / "test.sqlite")
        log_trade(_trade_data(), db_path=db)
        assert os.path.exists(db)


# ===========================================================================
# Decision Log
# ===========================================================================
class TestLogDecision:
    def test_creates_log_file(self, tmp_path):
        log = str(tmp_path / "decision_log.md")
        log_decision(_decision_data(), log_path=log)
        assert os.path.exists(log)

    def test_appends_content(self, tmp_path):
        log = str(tmp_path / "decision_log.md")
        log_decision(_decision_data(), log_path=log)

        with open(log, "r") as f:
            content = f.read()

        assert "EXECUTE" in content
        assert "BTCUSDT" in content
        assert "Trend_Pullback" in content
        assert "$200.00" in content

    def test_appends_without_overwriting(self, tmp_path):
        log = str(tmp_path / "decision_log.md")
        # Write initial content
        with open(log, "w") as f:
            f.write("# Existing Content\n")

        log_decision(_decision_data(), log_path=log)

        with open(log, "r") as f:
            content = f.read()

        assert "# Existing Content" in content
        assert "EXECUTE" in content

    def test_creates_parent_directory(self, tmp_path):
        log = str(tmp_path / "nested" / "decision_log.md")
        log_decision(_decision_data(), log_path=log)
        assert os.path.exists(log)
