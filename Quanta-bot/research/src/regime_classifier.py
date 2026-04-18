def classify_regime(row):
    """
    Classifies the market regime deterministically using Volatility & Trend flow.
    Returns: TRENDING, CHOPPY, EXPANSION, LOW_VOL
    """
    atr = row['atr']
    atr_sma = row['atr_sma']
    ema_50 = row['ema_50']
    ema_50_slope = row['ema_50_slope']
    close = row['close']
    ema_trend = row['ema_trend']
    
    # LOW_VOL: Compressed deeply
    if atr < (atr_sma * 0.8):
        return "LOW_VOL"
        
    # EXPANSION: Sudden burst physically
    if atr > (atr_sma * 1.2):
        return "EXPANSION"
        
    # TRENDING: Measurable institutional slope dynamically
    # 0.05% slope per 5 periods is an objective standard
    slope_pct = abs(ema_50_slope) / ema_50 * 100
    if slope_pct > 0.05:
        # Check alignment specifically
        if (close > ema_trend and ema_50_slope > 0) or (close < ema_trend and ema_50_slope < 0):
            return "TRENDING"
            
    # Default to choppy structurally
    return "CHOPPY"
