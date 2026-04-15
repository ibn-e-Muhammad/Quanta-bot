"""
test_confidence.py — Unit tests for confidence scoring

Tests: high/low ADX, volume ratios, RSI alignment, strategy-specific scoring,
clamping to [0,1].
"""

import pytest

from strategy.src.confidence import compute_confidence


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _make_state(**overrides) -> dict:
    base = {
        "symbol": "BTCUSDT",
        "price": 64000.00,
        "ema_20": 64000.00,
        "ema_50": 63500.00,
        "rsi": 45.0,
        "adx": 30.0,
        "atr": 450.00,
        "bb_lower": 63000.00,
        "bb_upper": 65000.00,
        "current_volume": 1200.0,
        "volume_sma_20": 850.0,
        "state": {"primary": "TRENDING_UP", "volatility": "NORMAL"},
        "support_level": 62500.00,
        "resistance_level": 65500.00,
    }
    for key, val in overrides.items():
        if key == "volatility":
            base["state"]["volatility"] = val
        else:
            base[key] = val
    return base


def _make_signal(signal: str = "BUY", strategy: str = "Trend_Pullback") -> dict:
    return {
        "signal": signal,
        "strategy_used": strategy,
        "suggested_entry": 64000.0,
        "suggested_sl": 63500.0,
        "suggested_tp": 65000.0,
    }


# ===========================================================================
# Tests
# ===========================================================================
class TestComputeConfidence:
    def test_returns_float_in_range(self):
        state = _make_state()
        signal = _make_signal()
        result = compute_confidence(state, signal)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_high_adx_gives_more_points(self):
        low_adx = _make_state(adx=10.0)
        high_adx = _make_state(adx=50.0)
        signal = _make_signal()

        low_score = compute_confidence(low_adx, signal)
        high_score = compute_confidence(high_adx, signal)
        assert high_score > low_score

    def test_high_volume_gives_more_points(self):
        low_vol = _make_state(current_volume=500.0, volume_sma_20=850.0)
        high_vol = _make_state(current_volume=2000.0, volume_sma_20=850.0)
        signal = _make_signal()

        low_score = compute_confidence(low_vol, signal)
        high_score = compute_confidence(high_vol, signal)
        assert high_score > low_score

    def test_buy_with_low_rsi_gets_more_rsi_points(self):
        low_rsi = _make_state(rsi=20.0)
        high_rsi = _make_state(rsi=60.0)
        signal = _make_signal("BUY")

        low_rsi_score = compute_confidence(low_rsi, signal)
        high_rsi_score = compute_confidence(high_rsi, signal)
        assert low_rsi_score > high_rsi_score

    def test_sell_with_high_rsi_gets_more_rsi_points(self):
        low_rsi = _make_state(rsi=30.0)
        high_rsi = _make_state(rsi=80.0)
        signal = _make_signal("SELL")

        low_rsi_score = compute_confidence(low_rsi, signal)
        high_rsi_score = compute_confidence(high_rsi, signal)
        assert high_rsi_score > low_rsi_score

    def test_breakout_high_volatility_gives_max_vol_points(self):
        state = _make_state(volatility="HIGH", adx=50.0, current_volume=2000.0)
        signal = _make_signal("BUY", "Breakout")
        result = compute_confidence(state, signal)
        # Breakout with HIGH vol = 20 volatility points vs normal
        assert result > 0.7  # should be high

    def test_trend_normal_volatility_gets_alignment(self):
        state = _make_state(volatility="NORMAL")
        signal = _make_signal("BUY", "Trend_Pullback")
        result = compute_confidence(state, signal)
        assert result > 0.5

    def test_clamped_at_one(self):
        """Even with extreme values, score should not exceed 1.0."""
        state = _make_state(
            adx=100.0,
            current_volume=10000.0,
            volume_sma_20=100.0,
            rsi=0.0,
            volatility="HIGH",
        )
        signal = _make_signal("BUY", "Breakout")
        result = compute_confidence(state, signal)
        assert result <= 1.0

    def test_zero_volume_sma_no_crash(self):
        """vol_sma = 0 should not crash."""
        state = _make_state(volume_sma_20=0.0)
        signal = _make_signal()
        result = compute_confidence(state, signal)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_range_buy_scoring(self):
        state = _make_state(
            price=63000.0,
            bb_lower=63000.0,
            volatility="NORMAL",
            adx=18.0,
            rsi=25.0,
        )
        signal = _make_signal("BUY", "Range")
        result = compute_confidence(state, signal)
        assert result > 0.3
