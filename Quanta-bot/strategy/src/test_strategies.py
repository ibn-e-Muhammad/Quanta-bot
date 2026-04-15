"""
test_strategies.py — Unit tests for all 3 strategies

Tests each strategy for BUY/SELL conditions plus edge cases:
volume too low, price not near EMA, ADX below threshold, etc.
"""

import pytest

from strategy.src.strategies import evaluate_trend, evaluate_range, evaluate_breakout


# ---------------------------------------------------------------------------
# Base state fixture builder
# ---------------------------------------------------------------------------
def _make_state(**overrides) -> dict:
    """Build a valid market state dict with optional overrides."""
    base = {
        "symbol": "BTCUSDT",
        "timestamp": "2026-04-16T00:00:00+00:00",
        "price": 64000.00,
        "ema_20": 64000.00,
        "ema_50": 63500.00,
        "vwap": 63950.00,
        "rsi": 45.0,
        "adx": 30.0,
        "atr": 450.00,
        "bb_lower": 63000.00,
        "bb_upper": 65000.00,
        "current_volume": 1200.0,
        "volume_sma_20": 850.0,
        "state": {
            "primary": "TRENDING_UP",
            "volatility": "NORMAL",
        },
        "support_level": 62500.00,
        "resistance_level": 65500.00,
    }
    # Apply overrides — support nested dict for "state"
    for key, val in overrides.items():
        if key == "primary":
            base["state"]["primary"] = val
        elif key == "volatility":
            base["state"]["volatility"] = val
        else:
            base[key] = val
    return base


# ===========================================================================
# Trend Strategy Tests
# ===========================================================================
class TestEvaluateTrend:
    def test_trending_up_buy(self):
        """All conditions met for TRENDING_UP → BUY."""
        state = _make_state(
            primary="TRENDING_UP",
            adx=30.0,
            ema_20=64000.0,
            ema_50=63500.0,
            price=64000.0,         # exactly at EMA_20
            current_volume=1100.0,
            volume_sma_20=850.0,   # 1100 >= 850 * 1.2 = 1020 ✓
        )
        result = evaluate_trend(state)
        assert result is not None
        assert result["signal"] == "BUY"
        assert result["strategy_used"] == "Trend_Pullback"
        assert result["suggested_sl"] < result["suggested_entry"]
        assert result["suggested_tp"] > result["suggested_entry"]

    def test_trending_down_sell(self):
        """All conditions met for TRENDING_DOWN → SELL."""
        state = _make_state(
            primary="TRENDING_DOWN",
            adx=30.0,
            ema_20=63500.0,
            ema_50=64000.0,
            price=63500.0,
            current_volume=1200.0,
            volume_sma_20=850.0,
        )
        result = evaluate_trend(state)
        assert result is not None
        assert result["signal"] == "SELL"
        assert result["strategy_used"] == "Trend_Pullback"
        assert result["suggested_sl"] > result["suggested_entry"]
        assert result["suggested_tp"] < result["suggested_entry"]

    def test_adx_below_threshold_returns_none(self):
        """ADX < 25 → no trend signal."""
        state = _make_state(primary="TRENDING_UP", adx=20.0)
        assert evaluate_trend(state) is None

    def test_volume_too_low_returns_none(self):
        """Volume below 1.2x SMA → no signal."""
        state = _make_state(
            primary="TRENDING_UP",
            current_volume=900.0,
            volume_sma_20=850.0,   # 900 < 850*1.2=1020
        )
        assert evaluate_trend(state) is None

    def test_price_too_far_from_ema(self):
        """Price more than 0.2% away from EMA_20 → no signal."""
        state = _make_state(
            primary="TRENDING_UP",
            price=65000.0,         # ~1.56% above EMA_20=64000
            ema_20=64000.0,
            current_volume=1100.0,
            volume_sma_20=850.0,
        )
        assert evaluate_trend(state) is None

    def test_ranging_state_returns_none(self):
        """RANGING state → trend strategy doesn't trigger."""
        state = _make_state(primary="RANGING")
        assert evaluate_trend(state) is None

    def test_sideways_state_returns_none(self):
        """SIDEWAYS state → trend strategy doesn't trigger."""
        state = _make_state(primary="SIDEWAYS")
        assert evaluate_trend(state) is None


# ===========================================================================
# Range Strategy Tests
# ===========================================================================
class TestEvaluateRange:
    def test_range_buy(self):
        """Price at BB lower + RSI oversold → BUY."""
        state = _make_state(
            primary="RANGING",
            adx=18.0,
            price=62900.0,         # <= bb_lower (63000)
            bb_lower=63000.0,
            rsi=25.0,
            vwap=63950.0,
        )
        result = evaluate_range(state)
        assert result is not None
        assert result["signal"] == "BUY"
        assert result["strategy_used"] == "Range"
        assert result["suggested_tp"] == 63950.0  # VWAP target

    def test_range_sell(self):
        """Price at BB upper + RSI overbought → SELL."""
        state = _make_state(
            primary="RANGING",
            adx=18.0,
            price=65100.0,         # >= bb_upper (65000)
            bb_upper=65000.0,
            rsi=75.0,
            vwap=63950.0,
        )
        result = evaluate_range(state)
        assert result is not None
        assert result["signal"] == "SELL"
        assert result["strategy_used"] == "Range"

    def test_not_ranging_returns_none(self):
        state = _make_state(primary="TRENDING_UP", adx=18.0)
        assert evaluate_range(state) is None

    def test_adx_above_threshold_returns_none(self):
        """ADX >= 25 → not a range market for this strategy."""
        state = _make_state(primary="RANGING", adx=26.0, price=63000.0, rsi=25.0)
        assert evaluate_range(state) is None

    def test_rsi_not_extreme_returns_none(self):
        """Price at BB lower but RSI not oversold → no signal."""
        state = _make_state(
            primary="RANGING", adx=18.0,
            price=63000.0, bb_lower=63000.0, rsi=45.0,
        )
        assert evaluate_range(state) is None

    def test_price_not_at_band_returns_none(self):
        """RSI oversold but price not at BB lower → no signal."""
        state = _make_state(
            primary="RANGING", adx=18.0,
            price=63500.0, bb_lower=63000.0, rsi=25.0,
        )
        assert evaluate_range(state) is None


# ===========================================================================
# Breakout Strategy Tests
# ===========================================================================
class TestEvaluateBreakout:
    def test_upside_breakout_buy(self):
        """Price above resistance with HIGH volatility + volume spike → BUY."""
        resistance = 65500.0
        state = _make_state(
            volatility="HIGH",
            adx=30.0,
            price=resistance * 1.002,       # above 0.1% threshold
            resistance_level=resistance,
            current_volume=2000.0,
            volume_sma_20=850.0,            # 2000 >= 850*2=1700 ✓
            atr=500.0,
        )
        result = evaluate_breakout(state)
        assert result is not None
        assert result["signal"] == "BUY"
        assert result["strategy_used"] == "Breakout"

    def test_downside_breakout_sell(self):
        """Price below support with HIGH volatility + volume spike → SELL."""
        support = 62500.0
        state = _make_state(
            volatility="HIGH",
            adx=30.0,
            price=support * 0.998,          # below 0.1% threshold
            support_level=support,
            current_volume=2000.0,
            volume_sma_20=850.0,
            atr=500.0,
        )
        result = evaluate_breakout(state)
        assert result is not None
        assert result["signal"] == "SELL"
        assert result["strategy_used"] == "Breakout"

    def test_normal_volatility_returns_none(self):
        """Volatility != HIGH → breakout doesn't trigger."""
        state = _make_state(volatility="NORMAL", adx=30.0, current_volume=2000.0)
        assert evaluate_breakout(state) is None

    def test_adx_below_threshold_returns_none(self):
        state = _make_state(volatility="HIGH", adx=20.0, current_volume=2000.0)
        assert evaluate_breakout(state) is None

    def test_volume_below_2x_returns_none(self):
        """Volume below 2x SMA → no breakout."""
        state = _make_state(
            volatility="HIGH", adx=30.0,
            current_volume=1500.0, volume_sma_20=850.0,  # 1500 < 850*2=1700
        )
        assert evaluate_breakout(state) is None

    def test_price_not_beyond_sr_returns_none(self):
        """Price within S/R zone → no breakout."""
        state = _make_state(
            volatility="HIGH", adx=30.0,
            current_volume=2000.0, volume_sma_20=850.0,
            price=64000.0,
            resistance_level=65500.0,
            support_level=62500.0,
        )
        assert evaluate_breakout(state) is None
