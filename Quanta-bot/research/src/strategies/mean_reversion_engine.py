def generate_signal(row):
    """
    Maker-only Mean Reversion (Swarm)

    Indicators:
    - Bollinger Bands (20, 2.0 SD + 3.0 SD)
    - RSI (14)

    Long: close <= lower_band (2.0/3.0 SD) AND RSI < 35
    Short: close >= upper_band (2.0/3.0 SD) AND RSI > 65
    Exit: mean (SMA-20)

    Funding filter (live):
    - Long only if funding_rate > 0
    - Short only if funding_rate < 0
    - If funding_rate is 0 or missing (backtest placeholder), do not block.
    """
    close = float(row.get("close", 0.0) or 0.0)
    mean = float(row.get("sma_20", 0.0) or 0.0)
    std = float(row.get("std_20", 0.0) or 0.0)
    rsi = float(row.get("rsi", 50.0) or 50.0)
    atr = float(row.get("atr", 0.0) or 0.0)
    funding_rate = row.get("funding_rate", 0.0)

    if close <= 0 or mean <= 0 or std <= 0:
        return {"signal": 0, "sl": 0, "tp1": 0, "tp2": 0, "strategy": "none"}

    lower_2 = mean - (2.0 * std)
    upper_2 = mean + (2.0 * std)
    lower_3 = mean - (3.0 * std)
    upper_3 = mean + (3.0 * std)

    allow_funding_filter = funding_rate not in (None, 0, 0.0)

    signal = 0
    if close <= lower_3 and rsi < 35:
        if (not allow_funding_filter) or float(funding_rate) > 0:
            signal = 1
            entry_band = lower_3
    elif close <= lower_2 and rsi < 35:
        if (not allow_funding_filter) or float(funding_rate) > 0:
            signal = 1
            entry_band = lower_2
    elif close >= upper_3 and rsi > 65:
        if (not allow_funding_filter) or float(funding_rate) < 0:
            signal = -1
            entry_band = upper_3
    elif close >= upper_2 and rsi > 65:
        if (not allow_funding_filter) or float(funding_rate) < 0:
            signal = -1
            entry_band = upper_2

    if signal == 1:
        sl = entry_band - (atr * 1.5)
        tp1 = mean
        tp2 = mean
        return {
            "signal": 1,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "strategy": "mean_reversion_engine",
            "suggested_entry": entry_band,
        }

    if signal == -1:
        sl = entry_band + (atr * 1.5)
        tp1 = mean
        tp2 = mean
        return {
            "signal": -1,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "strategy": "mean_reversion_engine",
            "suggested_entry": entry_band,
        }

    return {"signal": 0, "sl": 0, "tp1": 0, "tp2": 0, "strategy": "none"}
