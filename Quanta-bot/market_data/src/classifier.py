"""
classifier.py — Volatility & Market-State Classification

Two pure functions that implement the deterministic classification rules
defined in data-context.md. No side effects.
"""


def classify_volatility(
    bb_upper: float,
    bb_lower: float,
    bb_middle: float,
    bb_width_history: list[float],
    atr_current: float,
    atr_history: list[float],
) -> str:
    """Classify volatility as HIGH, NORMAL, or LOW.

    Rules (from data-context.md Section 3, Step 3):
        BB_Width = (BB_Upper - BB_Lower) / BB_Middle
        If BB_Width > mean(bb_width_history[-20:])  → HIGH
        Elif ATR < mean(atr_history[-14:]) * 0.8    → LOW
        Else                                        → NORMAL
    """
    if bb_middle == 0:
        return "LOW"  # defensive — avoid division by zero

    bb_width = (bb_upper - bb_lower) / bb_middle

    # Compare against the rolling average of the last 20 BB-width values
    recent_widths = bb_width_history[-20:] if len(bb_width_history) >= 20 else bb_width_history
    if recent_widths:
        avg_width = sum(recent_widths) / len(recent_widths)
        if bb_width > avg_width:
            return "HIGH"

    # Compare ATR against rolling 14-period mean * 0.8
    recent_atr = atr_history[-14:] if len(atr_history) >= 14 else atr_history
    if recent_atr:
        avg_atr = sum(recent_atr) / len(recent_atr)
        if atr_current < avg_atr * 0.8:
            return "LOW"

    return "NORMAL"


def classify_market_state(
    adx_value: float,
    ema_fast: float,
    ema_slow: float,
    volatility: str,
) -> str:
    """Classify market state as TRENDING_UP, TRENDING_DOWN, RANGING, or SIDEWAYS.

    Rules (from data-context.md Section 3, Step 4):
        ADX >= 25 AND EMA_FAST > EMA_SLOW   → TRENDING_UP
        ADX >= 25 AND EMA_FAST < EMA_SLOW   → TRENDING_DOWN
        ADX <  25 AND volatility != LOW  → RANGING
        Else                             → SIDEWAYS
    """
    if adx_value >= 25:
        if ema_fast > ema_slow:
            return "TRENDING_UP"
        else:
            return "TRENDING_DOWN"
    else:
        if volatility != "LOW":
            return "RANGING"
        else:
            return "SIDEWAYS"
