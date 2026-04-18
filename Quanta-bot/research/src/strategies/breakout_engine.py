def generate_signal(row):
    """
    Existing Breakout Logic completely isolated.
    Taker Fill expectation safely modeled.
    """
    close = row['close']
    upper_bb = row['upper_bb']
    lower_bb = row['lower_bb']
    prev_close = row['close_prev']
    prev_upper = row['upper_bb_prev']
    prev_lower = row['lower_bb_prev']
    atr = row['atr']
    
    signal = 0
    if close > upper_bb and prev_close <= prev_upper: 
        signal = 1
    elif close < lower_bb and prev_close >= prev_lower: 
        signal = -1
        
    if signal == 1:
        sl = close - (atr * 2)
        tp1 = close + (atr * 2) # 1R Target safely
        tp2 = close + (atr * 4) # 2R TP intrinsically
        return {"signal": 1, "sl": sl, "tp1": tp1, "tp2": tp2, "strategy": "breakout_engine"}
        
    elif signal == -1:
        sl = close + (atr * 2)
        tp1 = close - (atr * 2)
        tp2 = close - (atr * 4)
        return {"signal": -1, "sl": sl, "tp1": tp1, "tp2": tp2, "strategy": "breakout_engine"}
        
    return {"signal": 0, "sl": 0, "tp1": 0, "tp2": 0, "strategy": "none"}
