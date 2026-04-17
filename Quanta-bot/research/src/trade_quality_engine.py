def evaluate_trade_quality(row):
    """
    Phase 4.75: Filter Relaxation (De-choking Edge)
    Disabled: Strong Body & ATR Expansion Spike
    Active: EMA 200 Macro Trend Flow
    """
    signal = row['signal']
    close = row['close']
    ema_trend = row['ema_trend']
    
    # Macro Trend Alignment (200 EMA Flow) ACTIVE
    if signal == 1 and close <= ema_trend:
        return False
    if signal == -1 and close >= ema_trend:
        return False
        
    return True
