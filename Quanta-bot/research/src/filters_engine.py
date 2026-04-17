import pandas as pd
import numpy as np

def apply_filters(df, config):
    """
    Applies configurable execution safety filters to the signal dataframe.
    Returns: mapped dataframe, dictionary of impact reporting, and boolean flag for collapse
    """
    if 'filter_allowed' not in df.columns:
        df['filter_allowed'] = True
        
    stats = {}
    total_initial = len(df[df['signal'] != 0])
    if total_initial == 0:
        return df, stats, False
        
    # 1. Session Filter 
    if config.get("use_session_filter", False):
        hours = df['datetime_utc'].dt.hour
        session_mask = ((hours >= 7) & (hours <= 10)) | ((hours >= 12) & (hours <= 16))
        
        pre_count = len(df[(df['signal'] != 0) & df['filter_allowed']])
        df['filter_allowed'] = df['filter_allowed'] & session_mask
        post_count = len(df[(df['signal'] != 0) & df['filter_allowed']])
        
        stats['session_filter_removed'] = pre_count - post_count
        
    # 2. Chop/Low Volatility Filter 
    if config.get("use_volatility_filter", False):
        atr_pct = df['atr'] / df['close']
        rolling_200_vol = atr_pct.rolling(200).quantile(0.25)
        # Mask requires ATR to be larger than bottom quartile chop
        vol_mask = atr_pct > rolling_200_vol
        
        pre_count = len(df[(df['signal'] != 0) & df['filter_allowed']])
        df['filter_allowed'] = df['filter_allowed'] & vol_mask
        post_count = len(df[(df['signal'] != 0) & df['filter_allowed']])
        
        stats['volatility_filter_removed'] = pre_count - post_count

    # 3. Range Expansion Filter
    if config.get("use_range_expansion", False):
        candle_range = df['high'] - df['low']
        avg_range = candle_range.rolling(14).mean()
        range_mask = candle_range > avg_range
        
        pre_count = len(df[(df['signal'] != 0) & df['filter_allowed']])
        df['filter_allowed'] = df['filter_allowed'] & range_mask
        post_count = len(df[(df['signal'] != 0) & df['filter_allowed']])
        
        stats['range_filter_removed'] = pre_count - post_count

    # 4. Fake Breakout Filter
    if config.get("use_fake_breakout", False):
        # Reject trades where the body is unusually thin vs the entire tail variance
        body = (df['close'] - df['open']).abs()
        cr = df['high'] - df['low']
        fake_mask = body > (cr * 0.4) # Body must exceed minimum 40% of standard block length explicitly mapping breakouts safely
        
        pre_count = len(df[(df['signal'] != 0) & df['filter_allowed']])
        df['filter_allowed'] = df['filter_allowed'] & fake_mask
        post_count = len(df[(df['signal'] != 0) & df['filter_allowed']])
        
        stats['fake_breakout_removed'] = pre_count - post_count

    total_post = len(df[(df['signal'] != 0) & df['filter_allowed']])
    reduction_pct = ((total_initial - total_post) / total_initial) * 100 if total_initial > 0 else 0
    stats['total_removed_pct'] = reduction_pct
    
    warning_flag = False
    if reduction_pct > 60:
        warning_flag = True
        
    return df, stats, warning_flag
