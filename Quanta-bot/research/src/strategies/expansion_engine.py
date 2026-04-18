def generate_signal(row):
    """
    Phase 5.4 — Convexity Protocol
    Expansion engine entry logic (unchanged).
    Exit profile is now ASYMMETRIC — handled inside the simulator.
    Returns sl for 1.5R Tranche-1; tp1 = 1.5R anchor; tp2 = None (trailing).
    """
    close  = row['close']
    open_p = row['open']
    atr    = row['atr']
    adx    = row.get('adx', 0)

    # Phase 6 — ADX Momentum Gate
    if adx < 22:
        return {"signal": 0, "sl": 0, "tp1": 0, "tp2": 0, "strategy": "none"}

    candle_size = abs(close - open_p)
    signal = 0

    # Breakout candle must exceed local ATR
    if candle_size > atr:
        if close > open_p:
            signal = 1
        else:
            signal = -1

    if signal == 1:
        sl  = close - (atr * 1.2)            # Tight stop
        tp1 = close + (atr * 1.5)            # Tranche-1: fee-payer at 1.5R
        # tp2 is the EMA-24 trailing stop — no fixed level, pass 0 as sentinel
        return {"signal": 1, "sl": sl, "tp1": tp1, "tp2": 0, "strategy": "expansion_engine"}

    elif signal == -1:
        sl  = close + (atr * 1.2)
        tp1 = close - (atr * 1.5)
        return {"signal": -1, "sl": sl, "tp1": tp1, "tp2": 0, "strategy": "expansion_engine"}

    return {"signal": 0, "sl": 0, "tp1": 0, "tp2": 0, "strategy": "none"}
