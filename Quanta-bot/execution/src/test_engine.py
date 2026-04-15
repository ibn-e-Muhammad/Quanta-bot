"""
test_engine.py — Integration tests for the full Execution Engine pipeline

Tests:
  - Valid BUY signal + healthy account → EXECUTE
  - HOLD signal → REJECT (no action)
  - Circuit breaker tripped → REJECT
  - RR < 1.5 → REJECT
  - Leverage capping works
  - Missing signal → REJECT
  - SQLite row + decision log entry created
"""

import json
import sqlite3
import pytest

from execution.src.engine import run_execution_engine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _valid_buy_signal() -> dict:
    return {
        "timestamp": "2026-04-16T00:00:00+00:00",
        "symbol": "BTCUSDT",
        "signal": "BUY",
        "strategy_used": "Trend_Pullback",
        "confidence_score": 0.75,
        "suggested_entry": 64000.0,
        "suggested_sl": 63000.0,
        "suggested_tp": 66000.0,
        "reason": "Trend pullback BUY",
    }


def _hold_signal() -> dict:
    return {
        "timestamp": "2026-04-16T00:00:00+00:00",
        "symbol": "BTCUSDT",
        "signal": "HOLD",
        "strategy_used": "None",
        "confidence_score": 0.0,
        "suggested_entry": None,
        "suggested_sl": None,
        "suggested_tp": None,
        "reason": "No conditions met",
    }


def _healthy_account() -> dict:
    return {
        "account_balance": 10000.0,
        "daily_equity_start": 10000.0,
        "daily_peak_equity": 10200.0,
        "daily_trade_count": 2,
        "consecutive_losses": 0,
        "system_status": "ACTIVE",
    }


# ===========================================================================
# Happy Path
# ===========================================================================
class TestEngineHappyPath:
    def test_valid_buy_produces_execute(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        log = str(tmp_path / "decision_log.md")
        result = run_execution_engine(
            _valid_buy_signal(), _healthy_account(),
            db_path=db, log_path=log,
        )
        assert result["action"] == "EXECUTE"
        assert result["order"] is not None
        assert result["risk_summary"] is not None
        assert result["order"]["side"] == "BUY"
        assert result["order"]["symbol"] == "BTCUSDT"

    def test_execute_has_all_output_keys(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        log = str(tmp_path / "decision_log.md")
        result = run_execution_engine(
            _valid_buy_signal(), _healthy_account(),
            db_path=db, log_path=log,
        )
        assert "action" in result
        assert "order" in result
        assert "risk_summary" in result
        assert "reason" in result

    def test_sqlite_row_created(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        log = str(tmp_path / "decision_log.md")
        run_execution_engine(
            _valid_buy_signal(), _healthy_account(),
            db_path=db, log_path=log,
        )
        conn = sqlite3.connect(db)
        cursor = conn.execute("SELECT COUNT(*) FROM trades")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 1

    def test_decision_log_created(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        log = str(tmp_path / "decision_log.md")
        run_execution_engine(
            _valid_buy_signal(), _healthy_account(),
            db_path=db, log_path=log,
        )
        with open(log, "r") as f:
            content = f.read()
        assert "EXECUTE" in content
        assert "BTCUSDT" in content

    def test_valid_sell_produces_execute(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        log = str(tmp_path / "decision_log.md")
        sig = _valid_buy_signal()
        sig["signal"] = "SELL"
        sig["suggested_sl"] = 65000.0
        sig["suggested_tp"] = 62000.0
        result = run_execution_engine(
            sig, _healthy_account(),
            db_path=db, log_path=log,
        )
        assert result["action"] == "EXECUTE"
        assert result["order"]["side"] == "SELL"


# ===========================================================================
# Rejection Cases
# ===========================================================================
class TestEngineRejections:
    def test_hold_signal_rejects(self, tmp_path):
        log = str(tmp_path / "decision_log.md")
        result = run_execution_engine(
            _hold_signal(), _healthy_account(),
            log_path=log,
        )
        assert result["action"] == "REJECT"
        assert "HOLD" in result["reason"]

    def test_invalid_signal_rejects(self):
        result = run_execution_engine(
            {"bad": "signal"}, _healthy_account(),
        )
        assert result["action"] == "REJECT"
        assert "Invalid signal" in result["reason"]

    def test_invalid_account_rejects(self, tmp_path):
        log = str(tmp_path / "decision_log.md")
        result = run_execution_engine(
            _valid_buy_signal(), {"bad": "account"},
            log_path=log,
        )
        assert result["action"] == "REJECT"
        assert "account state" in result["reason"].lower()

    def test_circuit_breaker_daily_drawdown_rejects(self, tmp_path):
        log = str(tmp_path / "decision_log.md")
        account = _healthy_account()
        account["account_balance"] = 9400.0  # 6% below start
        result = run_execution_engine(
            _valid_buy_signal(), account,
            log_path=log,
        )
        assert result["action"] == "REJECT"
        assert "DRAWDOWN" in result["reason"]

    def test_circuit_breaker_max_trades_rejects(self, tmp_path):
        log = str(tmp_path / "decision_log.md")
        account = _healthy_account()
        account["daily_trade_count"] = 5
        result = run_execution_engine(
            _valid_buy_signal(), account,
            log_path=log,
        )
        assert result["action"] == "REJECT"
        assert "MAX_TRADES" in result["reason"]

    def test_rr_below_1_5_rejects(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        log = str(tmp_path / "decision_log.md")
        sig = _valid_buy_signal()
        sig["suggested_tp"] = 65000.0  # RR = 1000/1000 = 1.0
        result = run_execution_engine(
            sig, _healthy_account(),
            db_path=db, log_path=log,
        )
        assert result["action"] == "REJECT"
        assert "RR ratio" in result["reason"]


# ===========================================================================
# Risk Adjustments
# ===========================================================================
class TestEngineRiskAdjustments:
    def test_consecutive_losses_reduce_risk(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        log = str(tmp_path / "decision_log.md")
        account = _healthy_account()
        account["consecutive_losses"] = 2  # Triggers 1% risk
        result = run_execution_engine(
            _valid_buy_signal(), account,
            db_path=db, log_path=log,
        )
        assert result["action"] == "EXECUTE"
        assert result["risk_summary"]["risk_pct"] == 0.01

    def test_leverage_capping(self, tmp_path):
        db = str(tmp_path / "journal.sqlite")
        log = str(tmp_path / "decision_log.md")
        account = _healthy_account()
        account["account_balance"] = 500.0       # Very small balance
        account["daily_equity_start"] = 500.0     # Match balance to avoid DD
        account["daily_peak_equity"] = 500.0      # Match balance to avoid peak DD
        sig = _valid_buy_signal()
        sig["suggested_entry"] = 64000.0
        sig["suggested_sl"] = 63990.0  # Tiny SL distance = huge position
        sig["suggested_tp"] = 64020.0  # TP distance = 20, RR = 20/10 = 2.0
        result = run_execution_engine(
            sig, account,
            db_path=db, log_path=log,
        )
        assert result["action"] == "EXECUTE"
        assert result["risk_summary"]["leverage"] <= 10.0
