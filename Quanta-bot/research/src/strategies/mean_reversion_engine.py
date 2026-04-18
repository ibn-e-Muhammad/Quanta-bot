def generate_signal(row):
    """
    Mean Reversion strategy. Exploits flat boundaries natively.
    Maker Fill expectation natively returning TP locally inside bands.
    """
    close = row['close']
    upper = row['upper_bb']
    lower = row['lower_bb']
    mean = row['sma_20']
    atr = row['atr']
    
    # Trigger if we hit boundaries organically
    signal = 0
    if close <= lower:
        signal = 1
    elif close >= upper:
        signal = -1
        
    if signal == 1:
        # Tight SL natively avoiding Taker bleed safely
        sl = close - (atr * 1.5)
        # Target middle mean explicitly netting logical maker hits dynamically
        tp1 = close + abs(mean - close) * 0.5
        tp2 = mean # Full TP structurally at the mean line
        return {"signal": 1, "sl": sl, "tp1": tp1, "tp2": tp2, "strategy": "mean_reversion_engine"}
        
    elif signal == -1:
        sl = close + (atr * 1.5)
        tp1 = close - abs(close - mean) * 0.5
        tp2 = mean
        return {"signal": -1, "sl": sl, "tp1": tp1, "tp2": tp2, "strategy": "mean_reversion_engine"}
        
    return {"signal": 0, "sl": 0, "tp1": 0, "tp2": 0, "strategy": "none"}
