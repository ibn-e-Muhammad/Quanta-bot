def classify_regime(row):
    """
    Classifies the market regime deterministically using Volatility & Trend flow.
    Returns: TRENDING, CHOPPY, EXPANSION, LOW_VOL
    """
    atr = row['atr']
    atr_sma = row['atr_sma']
    ema_fast = row['ema_fast']
    ema_slow = row['ema_slow']
    ema_trend = row['ema_trend']
    
    # LOW_VOL: Compressed deeply
    if atr < (atr_sma * 0.8):
        return "LOW_VOL"
        
    # EXPANSION: Sudden burst physically
    if atr > (atr_sma * 1.2):
        return "EXPANSION"
        
    # TRENDING: Phase 5.1 Alignment sequence (9 > 24 > 200) natively removing Slope Lag structurally
    if (ema_fast > ema_slow and ema_slow > ema_trend) or (ema_fast < ema_slow and ema_slow < ema_trend):
        return "TRENDING"
            
    # Default to choppy structurally
    return "CHOPPY"
