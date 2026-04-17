import pandas as pd
import numpy as np

def build_portfolio_timeline(all_dfs):
    """
    Parses a dictionary of {symbol: raw_df} calculating the true time-indexed global alignment matrix.
    Returns: dictionary mapped as {timestamp_string: portfolio_risk_multiplier}
    """
    # Isolate strictly the signal bounds natively mapping timelines
    # all_dfs expects pre-signal mapped dataframes natively mapping logic
    
    time_states = {}
    
    # Iterate across everything safely parsing alignments via dictionary frequency
    for sym, df in all_dfs.items():
        if df is None or df.empty: continue
        
        active = df[df['signal'] != 0][['datetime_utc', 'signal', 'atr', 'close']].copy()
        active['volat_high'] = (active['atr'] / active['close']) > 0.02
        
        for row in active.itertuples():
            ts = row.datetime_utc.isoformat()
            if ts not in time_states:
                time_states[ts] = {'bulls': 0, 'bears': 0, 'high_vols': 0, 'total': 0}
            
            time_states[ts]['total'] += 1
            if row.signal == 1: time_states[ts]['bulls'] += 1
            else: time_states[ts]['bears'] += 1
            if row.volat_high: time_states[ts]['high_vols'] += 1

    # Extract dynamic risk factors purely
    portfolio_risk_map = {}
    
    for ts, data in time_states.items():
        tot = data['total']
        multiplier = 1.0
        if tot == 0:
            portfolio_risk_map[ts] = multiplier
            continue
            
        # Directional Check (>60% alignment)
        if (data['bulls'] / tot) > 0.60 or (data['bears'] / tot) > 0.60:
            multiplier *= 0.5
            
        # Volatility Check (>50% High Vol)
        if (data['high_vols'] / tot) > 0.50:
            multiplier *= 0.5
            
        portfolio_risk_map[ts] = multiplier
        
    return portfolio_risk_map
