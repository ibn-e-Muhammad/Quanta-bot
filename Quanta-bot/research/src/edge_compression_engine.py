import pandas as pd
import numpy as np

def apply_edge_compression(df, threshold=4):
    """
    Computes a context_score for each signal row.
    Returns the dataframe with 'context_score' and 'trade_allowed' columns.
    """
    scores = np.zeros(len(df))
    
    # 2.1 Time-Based Edge (HIGH PRIORITY)
    # If hour_of_day is in [11, 15, 19] -> score += 2
    hours = df['datetime_utc'].dt.hour
    scores += np.where(hours.isin([11, 15, 19]), 2, 0)
    
    # 2.2 Volatility Filter (CRITICAL)
    # If high: -5, If normal: +1
    # We define High ATR as ATR > 2% of Price
    atr_pct = df['atr'] / df['close']
    vol_high = atr_pct > 0.02
    scores += np.where(vol_high, -5, 1)
    
    # 2.3 EMA Distance (ENTRY QUALITY)
    # < 0.015: +2 | 0.015 - 0.025: +1 | else: -1
    ema_200_dist = (df['close'] - df['ema_trend']).abs() / df['ema_trend']
    scores += np.where(ema_200_dist < 0.015, 2, 
              np.where((ema_200_dist >= 0.015) & (ema_200_dist <= 0.025), 1, -1))
    
    # 2.4 ADX Contribution (LOW WEIGHT)
    # ADX 20-30: +1
    scores += np.where((df['adx'] >= 20) & (df['adx'] <= 30), 1, 0)
    
    # 2.5 Signal Direction Validity
    bull_4h_trend = df['close_4h'] > df['ema_trend_4h']
    bear_4h_trend = df['close_4h'] < df['ema_trend_4h']
    
    trend_align = ((df['signal'] == 1) & bull_4h_trend) | ((df['signal'] == -1) & bear_4h_trend)
    scores += np.where(trend_align, 1, 0)
    
    df['context_score'] = scores
    df['trade_allowed'] = scores >= threshold
    
    return df
