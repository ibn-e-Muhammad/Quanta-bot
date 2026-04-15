"""
strategies.py — Pure-Function Strategy Logic

Three strategies: Trend Pullback, Range, Breakout.
Each function takes a validated market state dict and returns a signal dict or None.
All functions are pure — no side effects, no file I/O, no state retained.
"""

from datetime import datetime, timezone

from . import config


# ---------------------------------------------------------------------------
# Trend Pullback Strategy (strategy-context.md §3)
# ---------------------------------------------------------------------------
def evaluate_trend(state: dict) -> dict | None:
    """Evaluate trend-following pullback entry.

    TRENDING_UP  → BUY when price pulls back to EMA_20
    TRENDING_DOWN → SELL when price rallies to EMA_20
    """
    primary: str = state["state"]["primary"]
    adx: float = state["adx"]
    ema_20: float = state["ema_20"]
    ema_50: float = state["ema_50"]
    price: float = state["price"]
    volume: float = state["current_volume"]
    vol_sma: float = state["volume_sma_20"]
    atr: float = state["atr"]

    # --- TRENDING UP ---
    if primary == "TRENDING_UP":
        if (
            adx >= config.ADX_TREND_THRESHOLD
            and ema_20 > ema_50
            and volume >= vol_sma * config.VOLUME_CONFIRM_MULTIPLIER
        ):
            # Price within ±0.2% of EMA_20
            if abs(price - ema_20) / ema_20 <= config.EMA_PROXIMITY_PCT:
                return _build_signal(
                    state=state,
                    signal="BUY",
                    strategy="Trend_Pullback",
                    entry=price,
                    sl=ema_50 * 0.99,
                    tp=price + (atr * 2),
                    reason=f"Trend pullback BUY: price near EMA_20, ADX={adx:.1f}, volume confirmed",
                )

    # --- TRENDING DOWN ---
    if primary == "TRENDING_DOWN":
        if (
            adx >= config.ADX_TREND_THRESHOLD
            and ema_20 < ema_50
            and volume >= vol_sma * config.VOLUME_CONFIRM_MULTIPLIER
        ):
            if abs(price - ema_20) / ema_20 <= config.EMA_PROXIMITY_PCT:
                return _build_signal(
                    state=state,
                    signal="SELL",
                    strategy="Trend_Pullback",
                    entry=price,
                    sl=ema_50 * 1.01,
                    tp=price - (atr * 2),
                    reason=f"Trend pullback SELL: price near EMA_20, ADX={adx:.1f}, volume confirmed",
                )

    return None


# ---------------------------------------------------------------------------
# Range Strategy (strategy-context.md §4)
# ---------------------------------------------------------------------------
def evaluate_range(state: dict) -> dict | None:
    """Evaluate mean-reversion range entries at Bollinger Band extremes."""
    primary: str = state["state"]["primary"]
    adx: float = state["adx"]
    price: float = state["price"]
    rsi: float = state["rsi"]
    bb_lower: float = state["bb_lower"]
    bb_upper: float = state["bb_upper"]
    vwap: float = state["vwap"]

    if primary != "RANGING" or adx >= config.ADX_TREND_THRESHOLD:
        return None

    # --- BUY ZONE ---
    if price <= bb_lower and rsi <= config.RSI_OVERSOLD:
        return _build_signal(
            state=state,
            signal="BUY",
            strategy="Range",
            entry=price,
            sl=bb_lower * 0.99,
            tp=vwap,
            reason=f"Range BUY: price at BB lower, RSI={rsi:.1f} oversold",
        )

    # --- SELL ZONE ---
    if price >= bb_upper and rsi >= config.RSI_OVERBOUGHT:
        return _build_signal(
            state=state,
            signal="SELL",
            strategy="Range",
            entry=price,
            sl=bb_upper * 1.01,
            tp=vwap,
            reason=f"Range SELL: price at BB upper, RSI={rsi:.1f} overbought",
        )

    return None


# ---------------------------------------------------------------------------
# Breakout Strategy (strategy-context.md §5)
# ---------------------------------------------------------------------------
def evaluate_breakout(state: dict) -> dict | None:
    """Evaluate breakout entries beyond support/resistance with volume spike."""
    volatility: str = state["state"]["volatility"]
    adx: float = state["adx"]
    price: float = state["price"]
    volume: float = state["current_volume"]
    vol_sma: float = state["volume_sma_20"]
    atr: float = state["atr"]
    resistance: float = state["resistance_level"]
    support: float = state["support_level"]

    if (
        volatility != "HIGH"
        or adx < config.ADX_TREND_THRESHOLD
        or volume < vol_sma * config.BREAKOUT_VOLUME_MULTIPLIER
    ):
        return None

    # --- UPSIDE BREAKOUT ---
    if price > resistance * (1 + config.BREAKOUT_PRICE_THRESHOLD):
        return _build_signal(
            state=state,
            signal="BUY",
            strategy="Breakout",
            entry=price,
            sl=resistance * 0.99,
            tp=price + (atr * 3),
            reason=f"Breakout BUY: price above resistance {resistance:.2f}, volume spike confirmed",
        )

    # --- DOWNSIDE BREAKOUT ---
    if price < support * (1 - config.BREAKOUT_PRICE_THRESHOLD):
        return _build_signal(
            state=state,
            signal="SELL",
            strategy="Breakout",
            entry=price,
            sl=support * 1.01,
            tp=price - (atr * 3),
            reason=f"Breakout SELL: price below support {support:.2f}, volume spike confirmed",
        )

    return None


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------
def _build_signal(
    *,
    state: dict,
    signal: str,
    strategy: str,
    entry: float,
    sl: float,
    tp: float,
    reason: str,
) -> dict:
    """Build a standardized signal dict."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": state["symbol"],
        "signal": signal,
        "strategy_used": strategy,
        "confidence_score": 0.0,  # Filled later by confidence module
        "suggested_entry": round(entry, 2),
        "suggested_sl": round(sl, 2),
        "suggested_tp": round(tp, 2),
        "reason": reason,
    }
