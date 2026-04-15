"""
test_circuit_breakers.py — Unit tests for all circuit breaker rules

Tests: system halted, daily drawdown, peak drawdown, max trades, consecutive losses.
"""

import pytest

from execution.src.circuit_breakers import check_circuit_breakers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _healthy_account(**overrides) -> dict:
    base = {
        "account_balance": 10000.0,
        "daily_equity_start": 10000.0,
        "daily_peak_equity": 10200.0,
        "daily_trade_count": 2,
        "consecutive_losses": 0,
        "system_status": "ACTIVE",
    }
    base.update(overrides)
    return base


# ===========================================================================
# All Pass
# ===========================================================================
class TestCircuitBreakersPass:
    def test_healthy_account_passes(self):
        passed, reason = check_circuit_breakers(_healthy_account())
        assert passed is True
        assert reason == ""

    def test_boundary_balance_passes(self):
        """Balance exactly at 95.01% of start → passes."""
        account = _healthy_account(
            account_balance=9501.0,
            daily_equity_start=10000.0,
            daily_peak_equity=9501.0,  # peak = current to avoid peak breaker
        )
        passed, _ = check_circuit_breakers(account)
        assert passed is True

    def test_four_trades_passes(self):
        account = _healthy_account(daily_trade_count=4)
        passed, _ = check_circuit_breakers(account)
        assert passed is True

    def test_two_consecutive_losses_passes(self):
        """2 losses triggers risk reduction, NOT halt."""
        account = _healthy_account(consecutive_losses=2)
        passed, _ = check_circuit_breakers(account)
        assert passed is True


# ===========================================================================
# System Halted
# ===========================================================================
class TestSystemHalted:
    def test_halted_system_rejects(self):
        account = _healthy_account(system_status="HALTED")
        passed, reason = check_circuit_breakers(account)
        assert passed is False
        assert "HALTED" in reason


# ===========================================================================
# Daily Drawdown
# ===========================================================================
class TestDailyDrawdown:
    def test_exactly_5pct_drawdown_rejects(self):
        """Balance exactly at 95% of start → rejects (<=)."""
        account = _healthy_account(
            account_balance=9500.0,
            daily_equity_start=10000.0,
            daily_peak_equity=9500.0,
        )
        passed, reason = check_circuit_breakers(account)
        assert passed is False
        assert "DAILY_DRAWDOWN" in reason

    def test_greater_than_5pct_drawdown_rejects(self):
        account = _healthy_account(
            account_balance=9400.0,
            daily_equity_start=10000.0,
            daily_peak_equity=9400.0,
        )
        passed, reason = check_circuit_breakers(account)
        assert passed is False


# ===========================================================================
# Peak Drawdown
# ===========================================================================
class TestPeakDrawdown:
    def test_peak_drawdown_rejects(self):
        """Balance dropped 5% from peak → rejects."""
        account = _healthy_account(
            account_balance=9690.0,      # 9690 <= 10200 * 0.95 = 9690
            daily_equity_start=9000.0,   # No daily DD trigger
            daily_peak_equity=10200.0,
        )
        passed, reason = check_circuit_breakers(account)
        assert passed is False
        assert "PEAK_DRAWDOWN" in reason


# ===========================================================================
# Trade Frequency
# ===========================================================================
class TestTradeFrequency:
    def test_five_trades_rejects(self):
        account = _healthy_account(daily_trade_count=5)
        passed, reason = check_circuit_breakers(account)
        assert passed is False
        assert "MAX_TRADES" in reason

    def test_six_trades_rejects(self):
        account = _healthy_account(daily_trade_count=6)
        passed, reason = check_circuit_breakers(account)
        assert passed is False


# ===========================================================================
# Consecutive Losses
# ===========================================================================
class TestConsecutiveLosses:
    def test_three_losses_rejects(self):
        account = _healthy_account(consecutive_losses=3)
        passed, reason = check_circuit_breakers(account)
        assert passed is False
        assert "CONSECUTIVE_LOSSES" in reason

    def test_four_losses_rejects(self):
        account = _healthy_account(consecutive_losses=4)
        passed, reason = check_circuit_breakers(account)
        assert passed is False
