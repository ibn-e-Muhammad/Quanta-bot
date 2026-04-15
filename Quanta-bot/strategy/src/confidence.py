"""
confidence.py — Signal Confidence Scoring

Single public function: compute_confidence()
Returns a value in [0.0, 1.0] representing signal strength.
Pure function — no file I/O, no side effects. Uses only arithmetic.
"""


def compute_confidence(state: dict, signal: dict) -> float:
    """Compute a confidence score for the given signal.

    Scoring model (additive, 100-point scale normalized to [0, 1]):
        - ADX strength:              0–30 points
        - Volume confirmation:       0–20 points
        - RSI alignment:             0–15 points
        - Price proximity to entry:  0–15 points
        - Volatility alignment:      0–20 points
    """
    total: float = 0.0

    adx: float = state["adx"]
    volume: float = state["current_volume"]
    vol_sma: float = state["volume_sma_20"]
    rsi: float = state["rsi"]
    price: float = state["price"]
    volatility: str = state["state"]["volatility"]
    signal_type: str = signal["signal"]
    strategy: str = signal["strategy_used"]

    # ---- 1. ADX Strength (0–30) ----
    # Stronger trend/directional movement = higher confidence
    total += min(adx / 50.0, 1.0) * 30.0

    # ---- 2. Volume Confirmation (0–20) ----
    # How much volume exceeds the SMA threshold
    if vol_sma > 0:
        vol_ratio: float = volume / (vol_sma * 1.2)
        total += min(vol_ratio, 1.0) * 20.0

    # ---- 3. RSI Alignment (0–15) ----
    # BUY signals: lower RSI = more room to run upward
    # SELL signals: higher RSI = more room to fall
    if signal_type == "BUY":
        # RSI < 50 is favorable for BUY; RSI 0 = max points
        rsi_score: float = max(0.0, (50.0 - rsi) / 50.0)
        total += rsi_score * 15.0
    elif signal_type == "SELL":
        # RSI > 50 is favorable for SELL; RSI 100 = max points
        rsi_score = max(0.0, (rsi - 50.0) / 50.0)
        total += rsi_score * 15.0

    # ---- 4. Price Proximity to Entry Level (0–15) ----
    # Closer to the key level = better entry
    ema_20: float = state["ema_20"]
    bb_lower: float = state["bb_lower"]
    bb_upper: float = state["bb_upper"]

    if strategy == "Trend_Pullback" and ema_20 > 0:
        proximity: float = 1.0 - abs(price - ema_20) / ema_20
        total += max(0.0, proximity) * 15.0
    elif strategy == "Range":
        if signal_type == "BUY" and bb_lower > 0:
            proximity = 1.0 - abs(price - bb_lower) / bb_lower
            total += max(0.0, proximity) * 15.0
        elif signal_type == "SELL" and bb_upper > 0:
            proximity = 1.0 - abs(price - bb_upper) / bb_upper
            total += max(0.0, proximity) * 15.0
    elif strategy == "Breakout":
        # For breakouts, price distance beyond S/R is confirmation
        # Give full points if price is already past the level
        total += 15.0

    # ---- 5. Volatility Alignment (0–20) ----
    if strategy == "Breakout" and volatility == "HIGH":
        total += 20.0
    elif strategy == "Trend_Pullback" and volatility in ("NORMAL", "HIGH"):
        total += 15.0
    elif strategy == "Range" and volatility == "NORMAL":
        total += 15.0

    # Normalize to [0.0, 1.0]
    score: float = total / 100.0
    return max(0.0, min(1.0, score))
