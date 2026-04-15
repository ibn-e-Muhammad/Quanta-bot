"""
test_classifier.py — Unit tests for volatility & market state classification

Covers all 4 market states and all 3 volatility states with edge cases.
Zero live API calls.
"""

import pytest
from market_data.src.classifier import classify_volatility, classify_market_state


# ===========================================================================
# Volatility Classification Tests
# ===========================================================================
class TestClassifyVolatility:
    def test_high_volatility_bb_width_above_average(self):
        # BB_Width = (200 - 100) / 150 = 0.667
        # Average of history = 0.3 → 0.667 > 0.3 → HIGH
        result = classify_volatility(
            bb_upper=200.0,
            bb_lower=100.0,
            bb_middle=150.0,
            bb_width_history=[0.3] * 20,
            atr_current=10.0,
            atr_history=[10.0] * 14,
        )
        assert result == "HIGH"

    def test_low_volatility_atr_below_threshold(self):
        # BB_Width = (110 - 90) / 100 = 0.2
        # Average width = 0.3 → 0.2 < 0.3 → NOT HIGH
        # ATR_current = 5.0, avg(ATR) = 10.0, 5.0 < 10.0 * 0.8 = 8.0 → LOW
        result = classify_volatility(
            bb_upper=110.0,
            bb_lower=90.0,
            bb_middle=100.0,
            bb_width_history=[0.3] * 20,
            atr_current=5.0,
            atr_history=[10.0] * 14,
        )
        assert result == "LOW"

    def test_normal_volatility(self):
        # BB_Width = (110 - 90) / 100 = 0.2
        # Average width = 0.3 → 0.2 < 0.3 → NOT HIGH
        # ATR_current = 9.0, avg(ATR) = 10.0, 9.0 > 10.0 * 0.8 = 8.0 → NOT LOW
        # → NORMAL
        result = classify_volatility(
            bb_upper=110.0,
            bb_lower=90.0,
            bb_middle=100.0,
            bb_width_history=[0.3] * 20,
            atr_current=9.0,
            atr_history=[10.0] * 14,
        )
        assert result == "NORMAL"

    def test_zero_bb_middle_returns_low(self):
        # Edge case: bb_middle = 0 → division by zero guard → LOW
        result = classify_volatility(
            bb_upper=10.0,
            bb_lower=0.0,
            bb_middle=0.0,
            bb_width_history=[0.1] * 20,
            atr_current=5.0,
            atr_history=[10.0] * 14,
        )
        assert result == "LOW"

    def test_empty_history_defaults_to_normal(self):
        # Empty histories → can't compare → falls through to NORMAL
        # (ATR comparison also fails with empty history)
        result = classify_volatility(
            bb_upper=110.0,
            bb_lower=90.0,
            bb_middle=100.0,
            bb_width_history=[],
            atr_current=10.0,
            atr_history=[],
        )
        assert result == "NORMAL"

    def test_exact_boundary_atr(self):
        # ATR_current = 8.0, avg = 10.0 → threshold = 8.0
        # 8.0 < 8.0 is False → NOT LOW → NORMAL
        result = classify_volatility(
            bb_upper=105.0,
            bb_lower=95.0,
            bb_middle=100.0,
            bb_width_history=[0.2] * 20,
            atr_current=8.0,
            atr_history=[10.0] * 14,
        )
        assert result == "NORMAL"


# ===========================================================================
# Market State Classification Tests
# ===========================================================================
class TestClassifyMarketState:
    def test_trending_up(self):
        result = classify_market_state(adx_value=30.0, ema_20=100.0, ema_50=95.0, volatility="NORMAL")
        assert result == "TRENDING_UP"

    def test_trending_down(self):
        result = classify_market_state(adx_value=30.0, ema_20=90.0, ema_50=95.0, volatility="NORMAL")
        assert result == "TRENDING_DOWN"

    def test_ranging(self):
        result = classify_market_state(adx_value=20.0, ema_20=100.0, ema_50=100.0, volatility="NORMAL")
        assert result == "RANGING"

    def test_ranging_with_high_volatility(self):
        result = classify_market_state(adx_value=20.0, ema_20=100.0, ema_50=100.0, volatility="HIGH")
        assert result == "RANGING"

    def test_sideways(self):
        result = classify_market_state(adx_value=20.0, ema_20=100.0, ema_50=100.0, volatility="LOW")
        assert result == "SIDEWAYS"

    def test_exact_adx_25_trending_up(self):
        """ADX == 25 should still be TRENDING (>= 25 threshold)."""
        result = classify_market_state(adx_value=25.0, ema_20=101.0, ema_50=100.0, volatility="NORMAL")
        assert result == "TRENDING_UP"

    def test_exact_adx_25_trending_down(self):
        result = classify_market_state(adx_value=25.0, ema_20=99.0, ema_50=100.0, volatility="NORMAL")
        assert result == "TRENDING_DOWN"

    def test_adx_25_ema_equal_trending_down(self):
        """When EMA_20 == EMA_50 and ADX >= 25 → TRENDING_DOWN (EMA_20 < EMA_50 is False → else)."""
        result = classify_market_state(adx_value=25.0, ema_20=100.0, ema_50=100.0, volatility="NORMAL")
        assert result == "TRENDING_DOWN"

    def test_adx_just_below_25_ranging(self):
        result = classify_market_state(adx_value=24.9, ema_20=101.0, ema_50=100.0, volatility="NORMAL")
        assert result == "RANGING"
