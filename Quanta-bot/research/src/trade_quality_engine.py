def evaluate_trade_quality(row):
    """
    Phase 4.80: Hardened Institutional Logic
    Disabled: Strong Body
    Active: ATR Expansion Spike, EMA 200 Macro Trend Flow
    """
    signal = row['signal']
    close = row['close']
    ema_trend = row['ema_trend']
    atr = row['atr']
    atr_sma = row['atr_sma']
    
    # ATR Expansion Spike Filter ACTIVE
    # Re-enabled to ensure physical breakaway velocity compensates for static exit parameters definitively.
    if atr <= atr_sma:
        return False
        
    # Macro Trend Alignment (200 EMA Flow) ACTIVE
    if signal == 1 and close <= ema_trend:
        return False
    if signal == -1 and close >= ema_trend:
        return False
        
    return True
