import pandas as pd
import numpy as np
import sqlite3
import os
import glob
from pathlib import Path

def run_portfolio_audit():
    _ROOT = Path(__file__).resolve().parent.parent.parent
    result_dir = _ROOT / "research" / "backtest_results"
    
    if not result_dir.exists():
        print("No backtest results found.")
        return
        
    db_files = list(result_dir.glob("*_trades.sqlite"))
    if not db_files:
        print("No sqlite DBs found in backtest_results.")
        return
        
    all_trades = []
    
    # 2.1 Data Aggregation Layer
    for db_file in db_files:
        conn = sqlite3.connect(db_file)
        df = pd.read_sql_query("SELECT * FROM historical_trades", conn)
        conn.close()
        if not df.empty:
            all_trades.append(df)
            
    if not all_trades:
        print("All databases are empty.")
        return
        
    combined_df = pd.concat(all_trades, ignore_index=True)
    
    # Pre-compute unified edge metrics
    combined_df['is_buy'] = combined_df['signal_type'] == 'BUY'
    combined_df['pnl_raw'] = np.where(combined_df['is_buy'], combined_df['exit_price'] - combined_df['entry_price'], combined_df['entry_price'] - combined_df['exit_price'])
    combined_df['risk_raw'] = np.where(combined_df['is_buy'], combined_df['entry_price'] - combined_df['sl_price'], combined_df['sl_price'] - combined_df['entry_price'])
    combined_df['risk_raw'] = combined_df['risk_raw'].replace(0, np.nan)
    combined_df['r_multiple'] = combined_df['pnl_raw'] / combined_df['risk_raw']
    combined_df['pnl_pct'] = combined_df['pnl_raw'] / combined_df['entry_price']
    
    def evaluate_group(group):
        n = len(group)
        w = len(group[group['outcome'] == 1])
        wr = w / n if n > 0 else 0
        gp = group[group['outcome'] == 1]['pnl_pct'].sum()
        gl = abs(group[group['outcome'] == 0]['pnl_pct'].sum())
        pf = gp / gl if gl != 0 else float('inf')
        
        w_mean = group[group['outcome'] == 1]['pnl_pct'].mean() if w > 0 else 0
        l_mean = group[group['outcome'] == 0]['pnl_pct'].mean() if (n-w) > 0 else 0
        l_rate = 1 - wr
        exp = (wr * w_mean) - (l_rate * abs(l_mean))
        
        is_condition = group['outcome'] == 0
        consec_losses = is_condition.groupby((~is_condition).cumsum()).sum().max()
        if pd.isna(consec_losses):
            consec_losses = 0
            
        group['cum_r'] = group['r_multiple'].fillna(0).cumsum()
        group['peak_r'] = group['cum_r'].cummax()
        max_dd = (group['peak_r'] - group['cum_r']).max()
        if pd.isna(max_dd):
            max_dd = 0
            
        avg_r = group['r_multiple'].mean()
        if pd.isna(avg_r):
            avg_r = 0
            
        return pd.Series({
            'Trades': n,
            'WinRate': wr,
            'PF': pf,
            'Exp': exp,
            'AvgR': avg_r,
            'MaxDD': max_dd,
            'MaxLossStreak': consec_losses
        })

    # 2.2 Core Asset-Level Metrics
    asset_stats = combined_df.groupby('symbol').apply(evaluate_group, include_groups=False).reset_index()
    
    # Stability Score Calculation
    def calc_stability(row):
        trade_pen = min(1.0, row['Trades']/100.0) 
        score = (row['Exp'] * 10000) - (row['MaxDD'] * 5) - (row['MaxLossStreak'] * 2)
        return score * trade_pen
        
    asset_stats['Stability'] = asset_stats.apply(calc_stability, axis=1)
    asset_stats = asset_stats.sort_values('Stability', ascending=False)
    
    # 3.1 Time-of-Day
    time_stats = combined_df.groupby('hour_of_day').apply(evaluate_group, include_groups=False)
    
    # 3.2 Volatility
    vol_stats = combined_df.groupby('volatility_regime').apply(evaluate_group, include_groups=False)
    
    # 3.3 ADX
    combined_df['adx_bucket'] = pd.cut(combined_df['adx'], bins=[0, 20, 30, 100], labels=['Weak (<20)', 'Moderate (20-30)', 'Strong (>30)'])
    adx_stats = combined_df.groupby('adx_bucket', observed=True).apply(evaluate_group, include_groups=False)
    
    # 3.4 EMA Distance
    w_df = combined_df[combined_df['outcome'] == 1]
    l_df = combined_df[combined_df['outcome'] == 0]
    ema_dist_w = w_df['ema_200_dist'].abs().mean() * 100 if not w_df.empty else 0
    ema_dist_l = l_df['ema_200_dist'].abs().mean() * 100 if not l_df.empty else 0
    
    # 3.5 Edge Consistency Matrix
    conditions = [
        ('Time: 03:00', combined_df['hour_of_day'] == 3),
        ('Time: 11:00', combined_df['hour_of_day'] == 11),
        ('Vol: NORMAL', combined_df['volatility_regime'] == 'NORMAL'),
        ('Vol: HIGH', combined_df['volatility_regime'] == 'HIGH'),
        ('ADX: Weak(<20)', combined_df['adx_bucket'] == 'Weak (<20)'),
        ('ADX: Mod(20-30)', combined_df['adx_bucket'] == 'Moderate (20-30)'),
        ('ADX: Strong(>30)', combined_df['adx_bucket'] == 'Strong (>30)')
    ]
    
    symbols = combined_df['symbol'].unique()
    matrix_data = []
    
    for c_name, mask in conditions:
        row_data = {'Condition': c_name}
        cons_score = 0
        total_syms = 0
        for sym in symbols:
            sym_mask = combined_df['symbol'] == sym
            sub = combined_df[mask & sym_mask]
            
            if len(sub) < 5:
                val = 0
                label = "N/A"
            else:
                wr = len(sub[sub['outcome']==1]) / len(sub)
                w_m = sub[sub['outcome']==1]['pnl_pct'].mean() if len(sub[sub['outcome']==1])>0 else 0
                l_m = abs(sub[sub['outcome']==0]['pnl_pct'].mean()) if len(sub[sub['outcome']==0])>0 else 0
                exp = (wr * w_m) - ((1-wr) * l_m)
                
                if exp > 0.0005: 
                    val = 1
                    label = "Win"
                elif exp < -0.0005: 
                    val = -1
                    label = "Loss"
                else: 
                    val = 0
                    label = "Neut"
            row_data[sym] = label
            cons_score += val
            if label != "N/A": 
                total_syms += 1
                
        row_data['Consistency'] = f"{cons_score}/{total_syms}"
        matrix_data.append(row_data)
        
    matrix_df = pd.DataFrame(matrix_data)
    
    # Overall System Expectancy
    tot_w = len(combined_df[combined_df['outcome']==1])
    overall_wr = tot_w / len(combined_df) if len(combined_df) else 0
    w_m_o = combined_df[combined_df['outcome']==1]['pnl_pct'].mean()
    l_m_o = abs(combined_df[combined_df['outcome']==0]['pnl_pct'].mean())
    overall_exp = (overall_wr * w_m_o) - ((1-overall_wr) * l_m_o)
    
    print("="*60)
    print("SECTION A -- PORTFOLIO SUMMARY")
    print("="*60)
    best_asset = asset_stats.iloc[0]['symbol'] if not asset_stats.empty else "N/A"
    worst_asset = asset_stats.iloc[-1]['symbol'] if not asset_stats.empty else "N/A"
    print(f"Total Portfolio Trades : {len(combined_df)}")
    print(f"Overall Expectancy     : {overall_exp*100:.3f}%")
    print(f"Overall Win Rate       : {overall_wr*100:.2f}%")
    print(f"Best Performing Asset  : {best_asset}")
    print(f"Worst Performing Asset : {worst_asset}\n")

    print("="*60)
    print("SECTION B -- ASSET RANKINGS")
    print("="*60)
    
    asset_disp = asset_stats.copy()
    asset_disp['WinRate'] = asset_disp['WinRate'].apply(lambda x: f"{x*100:.1f}%")
    asset_disp['Exp'] = asset_disp['Exp'].apply(lambda x: f"{x*100:.3f}%")
    asset_disp['PF'] = asset_disp['PF'].apply(lambda x: f"{x:.2f}")
    asset_disp['AvgR'] = asset_disp['AvgR'].apply(lambda x: f"{x:.2f}R")
    asset_disp['MaxDD'] = asset_disp['MaxDD'].apply(lambda x: f"{x:.1f}R")
    asset_disp['Stability'] = asset_disp['Stability'].apply(lambda x: f"{x:.1f}")
    print(asset_disp[['symbol', 'Trades', 'WinRate', 'PF', 'Exp', 'MaxDD', 'MaxLossStreak', 'Stability']].to_string(index=False))
    
    print("\n" + "="*60)
    print("SECTION C -- UNIVERSAL EDGE FINDINGS")
    print("="*60)
    print("1. Time-Based Edge (Top 3 Hours):")
    t_sort = time_stats.sort_values('Exp', ascending=False)
    t_disp = t_sort.copy()
    t_disp['WinRate'] = t_disp['WinRate'].apply(lambda x: f"{x*100:.1f}%")
    t_disp['Exp'] = t_disp['Exp'].apply(lambda x: f"{x*100:.3f}%")
    print(t_disp[['Trades', 'WinRate', 'PF', 'Exp']].head(3).to_string())
    
    print("\n2. Volatility Regime Edge:")
    v_disp = vol_stats.copy()
    v_disp['WinRate'] = v_disp['WinRate'].apply(lambda x: f"{x*100:.1f}%")
    v_disp['Exp'] = v_disp['Exp'].apply(lambda x: f"{x*100:.3f}%")
    print(v_disp[['Trades', 'WinRate', 'PF', 'Exp']].to_string())
    
    print("\n3. ADX Strength Edge:")
    a_disp = adx_stats.copy()
    a_disp['WinRate'] = a_disp['WinRate'].apply(lambda x: f"{x*100:.1f}%")
    a_disp['Exp'] = a_disp['Exp'].apply(lambda x: f"{x*100:.3f}%")
    print(a_disp[['Trades', 'WinRate', 'PF', 'Exp']].to_string())
    
    print(f"\n4. EMA Distance Edge:\nAvg Distance - Winners: {ema_dist_w:.2f}% | Losers: {ema_dist_l:.2f}%")
    
    print("\n5. EDGE CONSISTENCY MATRIX:")
    print(matrix_df.to_string(index=False))
    
    print("\n" + "="*60)
    print("SECTION D -- STRATEGY VIABILITY VERDICT")
    print("="*60)
    pos_assets = len(asset_stats[asset_stats['Exp'] > 0])
    tot_assets = len(asset_stats)
    pos_ratio = pos_assets / tot_assets if tot_assets else 0
    
    if overall_exp < 0 and pos_ratio <= 0.2:
        print("[X] VERDICT: Non-viable (negative across assets)")
        print("Reason: Systematic expectancy drain across the portfolio. Market noise dominates.")
    elif overall_exp < 0.001 and pos_ratio <= 0.5:
        print("[!] VERDICT: Conditional edge (asset-specific only)")
        print("Reason: Edge exists, but is fragmented and localized to specific asset idiosyncrasies.")
    elif pos_ratio > 0.5 and overall_exp > 0:
        if pos_ratio >= 0.8:
            print("[***] VERDICT: Strong universal edge")
            print("Reason: Expectancy is robust across >80% of independent isolated assets.")
        else:
            print("[OK] VERDICT: Weak universal edge")
            print("Reason: Net positive portfolio, surviving majority of market conditions.")
    else:
        print("[-] VERDICT: Inconclusive")
        print("Reason: Statistical distribution does not map explicitly to predefined edge criteria.")

if __name__ == "__main__":
    run_portfolio_audit()
