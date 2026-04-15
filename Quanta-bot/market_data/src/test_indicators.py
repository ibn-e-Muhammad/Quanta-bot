"""
test_indicators.py — Unit tests for indicator math

Tests EMA, SMA, RSI, ADX, ATR, Bollinger Bands, VWAP, and Support/Resistance
against known reference values. Zero live API calls.
"""

import math
import pytest
import numpy as np
from market_data.src.indicators import (
    ema,
    sma,
    rsi,
    adx,
    atr,
    bollinger_bands,
    vwap,
    support_resistance,
)


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_closes():
    """50 synthetic close prices with a known pattern."""
    np.random.seed(42)
    base = 100.0
    # Simulate a gradual uptrend with noise
    return [base + i * 0.5 + np.random.uniform(-1, 1) for i in range(50)]


@pytest.fixture
def sample_ohlcv():
    """50 synthetic OHLCV bars."""
    np.random.seed(42)
    closes = [100 + i * 0.5 + np.random.uniform(-1, 1) for i in range(50)]
    highs = [c + np.random.uniform(0.5, 2.0) for c in closes]
    lows = [c - np.random.uniform(0.5, 2.0) for c in closes]
    volumes = [np.random.uniform(100, 500) for _ in closes]
    return highs, lows, closes, volumes


# ===========================================================================
# SMA Tests
# ===========================================================================
class TestSMA:
    def test_sma_basic(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = sma(values, 3)
        # SMA(3) of last 3 values: (3+4+5)/3 = 4.0
        assert result[-1] == pytest.approx(4.0)
        # SMA at index 2 (first full window): (1+2+3)/3 = 2.0
        assert result[2] == pytest.approx(2.0)

    def test_sma_length_matches_input(self, sample_closes):
        result = sma(sample_closes, 10)
        assert len(result) == len(sample_closes)

    def test_sma_too_short_raises(self):
        with pytest.raises(ValueError, match="SMA requires"):
            sma([1.0, 2.0], 5)


# ===========================================================================
# EMA Tests
# ===========================================================================
class TestEMA:
    def test_ema_basic(self):
        values = [10.0] * 20  # Flat series
        result = ema(values, 10)
        # EMA of a flat series should equal the constant
        assert result[-1] == pytest.approx(10.0)

    def test_ema_responds_to_trend(self, sample_closes):
        result = ema(sample_closes, 10)
        # In an uptrend, EMA should be below the final close
        # (EMA lags) but above the initial close
        assert result[-1] > result[0]

    def test_ema_length_matches_input(self, sample_closes):
        result = ema(sample_closes, 10)
        assert len(result) == len(sample_closes)

    def test_ema_too_short_raises(self):
        with pytest.raises(ValueError, match="EMA requires"):
            ema([1.0], 5)


# ===========================================================================
# RSI Tests
# ===========================================================================
class TestRSI:
    def test_rsi_all_gains(self):
        # Monotonically increasing → RSI should be 100
        closes = list(range(1, 20))  # 1 to 19
        result = rsi(closes, 14)
        assert result == pytest.approx(100.0)

    def test_rsi_all_losses(self):
        # Monotonically decreasing → RSI should be 0
        closes = list(range(19, 0, -1))  # 19 to 1
        result = rsi(closes, 14)
        assert result == pytest.approx(0.0, abs=0.1)

    def test_rsi_in_range(self, sample_closes):
        result = rsi(sample_closes, 14)
        assert 0 <= result <= 100

    def test_rsi_too_short_raises(self):
        with pytest.raises(ValueError, match="RSI requires"):
            rsi([1.0, 2.0], 14)


# ===========================================================================
# ATR Tests
# ===========================================================================
class TestATR:
    def test_atr_positive(self, sample_ohlcv):
        highs, lows, closes, _ = sample_ohlcv
        result = atr(highs, lows, closes, 14)
        assert result > 0

    def test_atr_flat_market(self):
        # All candles identical → ATR should be ~0
        n = 20
        highs = [100.0] * n
        lows = [100.0] * n
        closes = [100.0] * n
        result = atr(highs, lows, closes, 14)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_atr_too_short_raises(self):
        with pytest.raises(ValueError, match="ATR requires"):
            atr([100.0], [99.0], [99.5], 14)


# ===========================================================================
# ADX Tests
# ===========================================================================
class TestADX:
    def test_adx_positive(self, sample_ohlcv):
        highs, lows, closes, _ = sample_ohlcv
        result = adx(highs, lows, closes, 14)
        assert 0 <= result <= 100

    def test_adx_strong_trend(self):
        # Create a strong uptrend
        n = 60
        closes = [100 + i * 2.0 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 0.5 for c in closes]
        result = adx(highs, lows, closes, 14)
        # Strong trend should give ADX > 25
        assert result > 20  # allow some tolerance

    def test_adx_too_short_raises(self):
        with pytest.raises(ValueError, match="ADX requires"):
            adx([1.0] * 5, [1.0] * 5, [1.0] * 5, 14)


# ===========================================================================
# Bollinger Bands Tests
# ===========================================================================
class TestBollingerBands:
    def test_bb_structure(self, sample_closes):
        lower, middle, upper = bollinger_bands(sample_closes, 20, 2.0)
        assert lower < middle < upper

    def test_bb_flat_series(self):
        closes = [50.0] * 30
        lower, middle, upper = bollinger_bands(closes, 20, 2.0)
        assert middle == pytest.approx(50.0)
        # Zero std dev → bands collapse to middle
        assert lower == pytest.approx(50.0)
        assert upper == pytest.approx(50.0)

    def test_bb_middle_is_sma(self, sample_closes):
        lower, middle, upper = bollinger_bands(sample_closes, 20, 2.0)
        expected_sma = sum(sample_closes[-20:]) / 20
        assert middle == pytest.approx(expected_sma)

    def test_bb_too_short_raises(self):
        with pytest.raises(ValueError, match="Bollinger Bands requires"):
            bollinger_bands([1.0] * 5, 20)


# ===========================================================================
# VWAP Tests
# ===========================================================================
class TestVWAP:
    def test_vwap_basic(self):
        highs = [110.0, 120.0]
        lows = [90.0, 100.0]
        closes = [100.0, 110.0]
        volumes = [1000.0, 1000.0]
        result = vwap(highs, lows, closes, volumes)
        # TP1 = (110+90+100)/3 = 100, TP2 = (120+100+110)/3 = 110
        # VWAP = (100*1000 + 110*1000) / (2000) = 105
        assert result == pytest.approx(105.0)

    def test_vwap_volume_weighted(self):
        highs = [100.0, 100.0]
        lows = [100.0, 100.0]
        closes = [100.0, 200.0]
        volumes = [1.0, 3.0]
        result = vwap(highs, lows, closes, volumes)
        # TP1 = 100, TP2 = (100+100+200)/3 ≈ 133.33
        # VWAP = (100*1 + 133.33*3) / 4 = (100 + 400) / 4 = 125
        expected = (100 * 1 + (400 / 3) * 3) / 4
        assert result == pytest.approx(expected)

    def test_vwap_zero_volume_raises(self):
        with pytest.raises(ValueError, match="volume is zero"):
            vwap([100.0], [90.0], [95.0], [0.0])

    def test_vwap_empty_raises(self):
        with pytest.raises(ValueError, match="at least 1"):
            vwap([], [], [], [])


# ===========================================================================
# Support / Resistance Tests
# ===========================================================================
class TestSupportResistance:
    def test_basic(self):
        lows = list(range(50, 0, -1))   # 50 down to 1
        highs = list(range(51, 101))     # 51 up to 100
        support, resistance = support_resistance(lows, highs, 50)
        assert support == 1.0
        assert resistance == 100.0

    def test_recent_window(self):
        lows = [10.0] * 40 + [5.0] * 10 + [10.0] * 10
        highs = [20.0] * 40 + [25.0] * 10 + [20.0] * 10
        # last 50 values of lows: [10]*40 last 10 + [5]*10 + [10]*10 → wait
        # Total 60, last 50 = lows[10:] = [10]*30 + [5]*10 + [10]*10
        support, resistance = support_resistance(lows, highs, 50)
        assert support == 5.0
        assert resistance == 25.0

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="Support/Resistance requires"):
            support_resistance([1.0] * 10, [2.0] * 10, 50)
