"""
indicators.py — Pure-Function Technical Indicator Calculations

All functions are pure: take lists/arrays of floats, return computed values.
No side effects, no state. Uses numpy for vectorized math.
Raises ValueError if input length < required period.
"""

import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_length(values, period: int, name: str) -> None:
    if len(values) < period:
        raise ValueError(
            f"{name} requires at least {period} data points, got {len(values)}"
        )


# ---------------------------------------------------------------------------
# Moving Averages
# ---------------------------------------------------------------------------
def ema(closes: list[float], period: int) -> list[float]:
    """Exponential Moving Average.

    Returns a list the same length as *closes*.
    The first (period-1) values use an expanding EMA seed equal to the SMA
    of the first *period* data points.
    """
    _validate_length(closes, period, "EMA")
    arr = np.array(closes, dtype=np.float64)
    multiplier = 2.0 / (period + 1)
    result = np.empty_like(arr)

    # Seed with SMA of first `period` values
    result[0] = arr[0]
    for i in range(1, len(arr)):
        if i < period:
            # expanding average until we have enough data
            result[i] = arr[: i + 1].mean()
        elif i == period - 1:
            result[i] = arr[:period].mean()
        else:
            result[i] = (arr[i] - result[i - 1]) * multiplier + result[i - 1]

    # Correct: once we have the SMA seed, use standard EMA from there
    seed = arr[:period].mean()
    result[period - 1] = seed
    for i in range(period, len(arr)):
        result[i] = (arr[i] - result[i - 1]) * multiplier + result[i - 1]

    return result.tolist()


def sma(values: list[float], period: int) -> list[float]:
    """Simple Moving Average.

    Returns a list the same length as *values*. The first (period-1) entries
    are partial (expanding) averages; from index (period-1) onward the true
    rolling SMA is returned.
    """
    _validate_length(values, period, "SMA")
    arr = np.array(values, dtype=np.float64)
    result = np.empty_like(arr)

    cumsum = np.cumsum(arr)
    # Partial (expanding) averages for indices 0..period-2
    for i in range(period - 1):
        result[i] = cumsum[i] / (i + 1)
    # True rolling SMA from period-1 onward
    result[period - 1] = cumsum[period - 1] / period
    for i in range(period, len(arr)):
        result[i] = (cumsum[i] - cumsum[i - period]) / period

    return result.tolist()


# ---------------------------------------------------------------------------
# RSI (Wilder's Smoothing)
# ---------------------------------------------------------------------------
def rsi(closes: list[float], period: int = 14) -> float:
    """Relative Strength Index — returns the latest value only."""
    _validate_length(closes, period + 1, "RSI")
    arr = np.array(closes, dtype=np.float64)
    deltas = np.diff(arr)

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Initial average (SMA of first `period` changes)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    # Wilder's smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# ADX (via +DI / -DI)
# ---------------------------------------------------------------------------
def adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float:
    """Average Directional Index — returns the latest value only."""
    min_len = period * 2 + 1  # Need enough data for smoothing
    _validate_length(highs, min_len, "ADX")
    _validate_length(lows, min_len, "ADX")
    _validate_length(closes, min_len, "ADX")

    h = np.array(highs, dtype=np.float64)
    l = np.array(lows, dtype=np.float64)
    c = np.array(closes, dtype=np.float64)
    n = len(c)

    tr = np.empty(n - 1)
    plus_dm = np.empty(n - 1)
    minus_dm = np.empty(n - 1)

    for i in range(1, n):
        idx = i - 1
        tr[idx] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        up_move = h[i] - h[i - 1]
        down_move = l[i - 1] - l[i]
        plus_dm[idx] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[idx] = down_move if (down_move > up_move and down_move > 0) else 0.0

    # Wilder's smoothing for TR, +DM, -DM
    atr_val = tr[:period].sum()
    plus_dm_smooth = plus_dm[:period].sum()
    minus_dm_smooth = minus_dm[:period].sum()

    dx_values = []

    for i in range(period, len(tr)):
        atr_val = atr_val - atr_val / period + tr[i]
        plus_dm_smooth = plus_dm_smooth - plus_dm_smooth / period + plus_dm[i]
        minus_dm_smooth = minus_dm_smooth - minus_dm_smooth / period + minus_dm[i]

        plus_di = 100.0 * plus_dm_smooth / atr_val if atr_val != 0 else 0.0
        minus_di = 100.0 * minus_dm_smooth / atr_val if atr_val != 0 else 0.0

        di_sum = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum != 0 else 0.0
        dx_values.append(dx)

    if len(dx_values) < period:
        raise ValueError("Not enough data to compute ADX")

    # ADX = Wilder's smoothing of DX
    adx_val = np.mean(dx_values[:period])
    for i in range(period, len(dx_values)):
        adx_val = (adx_val * (period - 1) + dx_values[i]) / period

    return float(adx_val)


# ---------------------------------------------------------------------------
# ATR (Wilder's)
# ---------------------------------------------------------------------------
def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float:
    """Average True Range — returns the latest value only."""
    _validate_length(highs, period + 1, "ATR")
    _validate_length(lows, period + 1, "ATR")
    _validate_length(closes, period + 1, "ATR")

    h = np.array(highs, dtype=np.float64)
    l = np.array(lows, dtype=np.float64)
    c = np.array(closes, dtype=np.float64)

    tr_values = []
    for i in range(1, len(c)):
        tr_values.append(
            max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        )

    # Wilder's smoothing
    atr_val = np.mean(tr_values[:period])
    for i in range(period, len(tr_values)):
        atr_val = (atr_val * (period - 1) + tr_values[i]) / period

    return float(atr_val)


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------
def bollinger_bands(
    closes: list[float], period: int = 20, std_dev: float = 2.0
) -> tuple[float, float, float]:
    """Bollinger Bands — returns (lower, middle, upper) for the latest bar."""
    _validate_length(closes, period, "Bollinger Bands")
    arr = np.array(closes, dtype=np.float64)

    window = arr[-period:]
    middle = float(window.mean())
    sd = float(window.std(ddof=0))  # population std dev (standard for BB)

    lower = middle - std_dev * sd
    upper = middle + std_dev * sd
    return (lower, middle, upper)


# ---------------------------------------------------------------------------
# VWAP (session-anchored)
# ---------------------------------------------------------------------------
def vwap(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> float:
    """Session-anchored Volume Weighted Average Price — returns latest."""
    n = len(closes)
    if n == 0:
        raise ValueError("VWAP requires at least 1 data point")

    h = np.array(highs, dtype=np.float64)
    l = np.array(lows, dtype=np.float64)
    c = np.array(closes, dtype=np.float64)
    v = np.array(volumes, dtype=np.float64)

    typical_price = (h + l + c) / 3.0
    cum_tp_vol = np.cumsum(typical_price * v)
    cum_vol = np.cumsum(v)

    if cum_vol[-1] == 0:
        raise ValueError("VWAP: total volume is zero")

    return float(cum_tp_vol[-1] / cum_vol[-1])


# ---------------------------------------------------------------------------
# Support / Resistance (rolling min/max)
# ---------------------------------------------------------------------------
def support_resistance(
    lows: list[float],
    highs: list[float],
    period: int = 50,
) -> tuple[float, float]:
    """Returns (support, resistance) = (rolling min of lows, rolling max of highs)."""
    _validate_length(lows, period, "Support/Resistance")
    _validate_length(highs, period, "Support/Resistance")

    support = float(min(lows[-period:]))
    resistance = float(max(highs[-period:]))
    return (support, resistance)
