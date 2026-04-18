def generate_signal(row):
    """
    Lightweight Expansion capturing sudden volatility mechanically.
    Taker Fill expectation intrinsically.
    """
    close = row['close']
    open_p = row['open']
    atr = row['atr']
    
    candle_size = abs(close - open_p)
    signal = 0
    
    # Breakout candle must be larger than local ATR structurally
    if candle_size > atr:
        if close > open_p:
            signal = 1
        else:
            signal = -1
            
    if signal == 1:
        # Tight Stop natively preventing whip-saw
        sl = close - (atr * 1.2)
        tp1 = close + (atr * 1.5)
        tp2 = close + (atr * 2.5) # Fast 2R natively
        return {"signal": 1, "sl": sl, "tp1": tp1, "tp2": tp2, "strategy": "expansion_engine"}
        
    elif signal == -1:
        sl = close + (atr * 1.2)
        tp1 = close - (atr * 1.5)
        tp2 = close - (atr * 2.5)
        return {"signal": -1, "sl": sl, "tp1": tp1, "tp2": tp2, "strategy": "expansion_engine"}
        
    return {"signal": 0, "sl": 0, "tp1": 0, "tp2": 0, "strategy": "none"}
