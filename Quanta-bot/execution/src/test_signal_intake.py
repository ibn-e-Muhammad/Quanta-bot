"""
test_signal_intake.py — Unit tests for strategy signal validation

Tests: valid BUY/SELL/HOLD parsing, missing keys, null entry on BUY, etc.
"""

import pytest

from execution.src.signal_intake import validate_signal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _valid_buy() -> dict:
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


def _valid_hold() -> dict:
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


# ===========================================================================
# Valid Signals
# ===========================================================================
class TestValidSignals:
    def test_valid_buy(self):
        assert validate_signal(_valid_buy()) is not None

    def test_valid_sell(self):
        sig = _valid_buy()
        sig["signal"] = "SELL"
        assert validate_signal(sig) is not None

    def test_valid_hold(self):
        assert validate_signal(_valid_hold()) is not None

    def test_hold_with_null_prices(self):
        """HOLD signals may have null entry/sl/tp."""
        result = validate_signal(_valid_hold())
        assert result is not None
        assert result["signal"] == "HOLD"


# ===========================================================================
# Invalid Signals
# ===========================================================================
class TestInvalidSignals:
    def test_not_a_dict(self):
        assert validate_signal("not a dict") is None
        assert validate_signal(None) is None

    def test_missing_key(self):
        sig = _valid_buy()
        del sig["signal"]
        assert validate_signal(sig) is None

    def test_invalid_signal_type(self):
        sig = _valid_buy()
        sig["signal"] = "INVALID"
        assert validate_signal(sig) is None

    def test_null_entry_on_buy(self):
        sig = _valid_buy()
        sig["suggested_entry"] = None
        assert validate_signal(sig) is None

    def test_null_sl_on_sell(self):
        sig = _valid_buy()
        sig["signal"] = "SELL"
        sig["suggested_sl"] = None
        assert validate_signal(sig) is None

    def test_zero_entry(self):
        sig = _valid_buy()
        sig["suggested_entry"] = 0.0
        assert validate_signal(sig) is None

    def test_negative_tp(self):
        sig = _valid_buy()
        sig["suggested_tp"] = -100.0
        assert validate_signal(sig) is None

    def test_nan_entry(self):
        sig = _valid_buy()
        sig["suggested_entry"] = float("nan")
        assert validate_signal(sig) is None

    def test_inf_sl(self):
        sig = _valid_buy()
        sig["suggested_sl"] = float("inf")
        assert validate_signal(sig) is None
