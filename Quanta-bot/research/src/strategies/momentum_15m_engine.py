def generate_signal(row):
    """
    Phase R1: 15m Momentum Ignition Engine
    Status: EXPERIMENTAL (Isolated from Production)
    
    Logic: Trades the transition from quiet to expansion (inefficiency),
    anchored by 1H directional bias.
    """
    close = row.get('close', 0.0)
    
    # 15m Microstructure 
    atr = row.get('atr', 0.0)
    rolling_atr_mean = row.get('atr_mean', atr) 
    volume = row.get('volume', 0.0)
    rolling_vol_mean = row.get('volume_mean', volume)
    adx_15m = row.get('adx', 0.0)
    
    # 1H Macro Context
    htf_adx = row.get('htf_adx', 0.0)
    htf_trend = row.get('htf_trend', 0) # 1 for UP, -1 for DOWN

    signal = 0

    # LAYER 1: 1H Bias (Prevent chop trading)
    if htf_adx > 20:
        
        # LAYER 2: 15m Setup (Ignition Trigger)
        # 1. Volatility Expansion (Price is violently moving)
        vol_expansion = atr > (rolling_atr_mean * 1.2)
        
        # 2. Volume Confirmation (Institutions are involved)
        volu_expansion = volume > (rolling_vol_mean * 1.5)
        
        # 3. Momentum Confirmation
        mom_confirmation = adx_15m > 18
        
        if vol_expansion and volu_expansion and mom_confirmation:
            # LAYER 3: Directional Alignment
            if htf_trend == 1:
                signal = 1
            elif htf_trend == -1:
                signal = -1

    # LAYER 4: Risk Model (Strict ATR Multiples)
    if signal == 1:
        sl_dist = atr * 1.5
        sl = close - sl_dist
        tp1 = close + (sl_dist * 1.5)  # 1.5R Target
        tp2 = close + (sl_dist * 2.0)  # 2.0R Target
        return {"signal": 1, "sl": sl, "tp1": tp1, "tp2": tp2, "strategy": "momentum_15m"}
        
    elif signal == -1:
        sl_dist = atr * 1.5
        sl = close + sl_dist
        tp1 = close - (sl_dist * 1.5)  # 1.5R Target
        tp2 = close - (sl_dist * 2.0)  # 2.0R Target
        return {"signal": -1, "sl": sl, "tp1": tp1, "tp2": tp2, "strategy": "momentum_15m"}
        
    return {"signal": 0, "sl": 0, "tp1": 0, "tp2": 0, "strategy": "none"}
