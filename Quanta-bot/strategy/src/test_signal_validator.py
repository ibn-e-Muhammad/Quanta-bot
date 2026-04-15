"""
test_signal_validator.py — Unit tests for output validation

Tests: SL/TP ordering for BUY/SELL, RR < 1.5 → HOLD, HOLD passthrough,
invalid signal types, missing values.
"""

import pytest

from strategy.src.signal_validator import validate_signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _buy_signal(entry: float = 64000.0, sl: float = 63000.0, tp: float = 66000.0) -> dict:
    return {
        "timestamp": "2026-04-16T00:00:00+00:00",
        "symbol": "BTCUSDT",
        "signal": "BUY",
        "strategy_used": "Trend_Pullback",
        "confidence_score": 0.75,
        "suggested_entry": entry,
        "suggested_sl": sl,
        "suggested_tp": tp,
        "reason": "test",
    }


def _sell_signal(entry: float = 64000.0, sl: float = 65000.0, tp: float = 62000.0) -> dict:
    return {
        "timestamp": "2026-04-16T00:00:00+00:00",
        "symbol": "BTCUSDT",
        "signal": "SELL",
        "strategy_used": "Trend_Pullback",
        "confidence_score": 0.75,
        "suggested_entry": entry,
        "suggested_sl": sl,
        "suggested_tp": tp,
        "reason": "test",
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


# ===========================================================================
# Valid Signals Pass Through
# ===========================================================================
class TestValidSignals:
    def test_valid_buy_passes(self):
        sig = _buy_signal(entry=64000, sl=63000, tp=66000)
        # RR = 2000/1000 = 2.0 ✓
        result = validate_signal(sig)
        assert result["signal"] == "BUY"

    def test_valid_sell_passes(self):
        sig = _sell_signal(entry=64000, sl=65000, tp=62000)
        # RR = 2000/1000 = 2.0 ✓
        result = validate_signal(sig)
        assert result["signal"] == "SELL"

    def test_hold_passes_through(self):
        sig = _hold_signal()
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"
        assert result["reason"] == "No conditions met"


# ===========================================================================
# SL/TP Ordering Violations
# ===========================================================================
class TestSLTPOrdering:
    def test_buy_sl_above_entry_forced_hold(self):
        """BUY with SL > ENTRY → HOLD."""
        sig = _buy_signal(entry=64000, sl=65000, tp=66000)
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"
        assert "BUY validation failed" in result["reason"]

    def test_buy_tp_below_entry_forced_hold(self):
        """BUY with TP < ENTRY → HOLD."""
        sig = _buy_signal(entry=64000, sl=63000, tp=63500)
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"

    def test_sell_sl_below_entry_forced_hold(self):
        """SELL with SL < ENTRY → HOLD."""
        sig = _sell_signal(entry=64000, sl=63000, tp=62000)
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"
        assert "SELL validation failed" in result["reason"]

    def test_sell_tp_above_entry_forced_hold(self):
        """SELL with TP > ENTRY → HOLD."""
        sig = _sell_signal(entry=64000, sl=65000, tp=65500)
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"


# ===========================================================================
# Risk/Reward Violations
# ===========================================================================
class TestRiskReward:
    def test_rr_below_1_5_forced_hold(self):
        """RR < 1.5 → HOLD."""
        # entry=64000, sl=63000 (dist=1000), tp=65000 (dist=1000) → RR=1.0
        sig = _buy_signal(entry=64000, sl=63000, tp=65000)
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"
        assert "RR ratio" in result["reason"]

    def test_rr_exactly_1_5_passes(self):
        """RR == 1.5 → passes."""
        # entry=64000, sl=63000 (dist=1000), tp=65500 (dist=1500) → RR=1.5
        sig = _buy_signal(entry=64000, sl=63000, tp=65500)
        result = validate_signal(sig)
        assert result["signal"] == "BUY"

    def test_rr_above_1_5_passes(self):
        """RR > 1.5 → passes."""
        sig = _buy_signal(entry=64000, sl=63000, tp=66000)
        result = validate_signal(sig)
        assert result["signal"] == "BUY"

    def test_zero_sl_distance_forced_hold(self):
        """SL == ENTRY → cannot compute RR → HOLD."""
        sig = _buy_signal(entry=64000, sl=64000, tp=66000)
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"


# ===========================================================================
# Edge Cases
# ===========================================================================
class TestEdgeCases:
    def test_invalid_signal_type_forced_hold(self):
        sig = {"signal": "INVALID", "symbol": "BTCUSDT", "timestamp": "T"}
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"
        assert "Invalid signal type" in result["reason"]

    def test_missing_entry_forced_hold(self):
        sig = _buy_signal()
        sig["suggested_entry"] = None
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"
        assert "Missing" in result["reason"]

    def test_missing_sl_forced_hold(self):
        sig = _buy_signal()
        sig["suggested_sl"] = None
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"

    def test_missing_tp_forced_hold(self):
        sig = _buy_signal()
        sig["suggested_tp"] = None
        result = validate_signal(sig)
        assert result["signal"] == "HOLD"
