import pandas as pd
import numpy as np
import sqlite3
import os
from pathlib import Path

def calculate_consecutive(series, condition_val):
    is_condition = series == condition_val
    return is_condition.groupby((~is_condition).cumsum()).sum().max()

def run_performance_audit():
    _ROOT = Path(__file__).resolve().parent.parent.parent
    db_path = _ROOT / "runtime" / "training_journal.sqlite"

    if not db_path.exists():
        print(f"[ERROR] Database {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM historical_trades", conn)
    conn.close()

    if df.empty:
        print("[ERROR] No trades found in the database. Run simulator first.")
        return

    print("=" * 60)
    print("[X] SNIPER PERFORMANCE REPORT (EDGE DISCOVERY v1)")
    print("=" * 60)
    
    # Pre-compute PnL and Risk
    df['is_buy'] = df['signal_type'] == 'BUY'
    
    # Precise trade evaluation values
    df['pnl_raw'] = np.where(df['is_buy'], df['exit_price'] - df['entry_price'], df['entry_price'] - df['exit_price'])
    df['risk_raw'] = np.where(df['is_buy'], df['entry_price'] - df['sl_price'], df['sl_price'] - df['entry_price'])
    
    # Guard against zero-division in case risk is 0 somehow
    df['risk_raw'] = df['risk_raw'].replace(0, np.nan)
    df['r_multiple'] = df['pnl_raw'] / df['risk_raw']
    df['pnl_pct'] = df['pnl_raw'] / df['entry_price']
    
    # -------------------------------------------------------------
    # STEP 2: CORE PERFORMANCE METRICS
    # -------------------------------------------------------------
    total_trades = len(df)
    winners = df[df['outcome'] == 1]
    losers = df[df['outcome'] == 0]
    win_rate = len(winners) / total_trades if total_trades else 0
    
    gross_profit = winners['pnl_pct'].sum()
    gross_loss = abs(losers['pnl_pct'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')
    
    avg_win = winners['pnl_pct'].mean() if not winners.empty else 0
    avg_loss = losers['pnl_pct'].mean() if not losers.empty else 0
    loss_rate = 1 - win_rate
    expectancy = (win_rate * avg_win) - (loss_rate * abs(avg_loss))
    
    avg_r = df['r_multiple'].dropna().mean()
    
    avg_dur_win = winners['duration_minutes'].mean()
    avg_dur_loss = losers['duration_minutes'].mean()
    
    print("\n[1] CORE METRICS")
    print(f"Total Trades     : {total_trades}")
    print(f"Win Rate         : {win_rate*100:.2f}%")
    print(f"Profit Factor    : {profit_factor:.2f}")
    print(f"Expectancy/Trade : {expectancy*100:.3f}% (of asset price)")
    print(f"Avg R-Multiple   : {avg_r:.2f}R")
    print(f"Avg Duration (W) : {avg_dur_win:.1f} mins")
    print(f"Avg Duration (L) : {avg_dur_loss:.1f} mins")

    # -------------------------------------------------------------
    # STEP 3: EDGE DISCOVERY
    # -------------------------------------------------------------
    
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
        
        return pd.Series({
            'Trades': n, 
            'WinRate': f"{wr*100:.1f}%", 
            'ProfitFactor': f"{pf:.2f}", 
            'Expectancy': f"{exp*100:.3f}%"
        })

    print("\n[3.1] TIME-BASED ANALYSIS (Hour of Day UTC)")
    hour_stats = df.groupby('hour_of_day').apply(evaluate_group, include_groups=False)
    # Sort top 5 periods
    hour_stats['PF_num'] = hour_stats['ProfitFactor'].replace('inf', 999).astype(float)
    print(hour_stats.sort_values('PF_num', ascending=False).drop(columns=['PF_num']).head(8).to_string())

    print("\n[3.2] VOLATILITY REGIME ANALYSIS")
    vol_stats = df.groupby('volatility_regime').apply(evaluate_group, include_groups=False)
    print(vol_stats.to_string())

    print("\n[3.3] TREND DISTANCE (EMA 200)")
    mean_dist_w = winners['ema_200_dist'].abs().mean() * 100
    mean_dist_l = losers['ema_200_dist'].abs().mean() * 100
    print(f"Avg EMA200 Distance (Winners): {mean_dist_w:.2f}%")
    print(f"Avg EMA200 Distance (Losers) : {mean_dist_l:.2f}%")

    print("\n[3.4] ADX STRENGTH BUCKETING")
    df['adx_bucket'] = pd.cut(df['adx'], bins=[0, 20, 30, 100], labels=['Weak (<20)', 'Moderate (20-30)', 'Strong (>30)'])
    adx_stats = df.groupby('adx_bucket', observed=True).apply(evaluate_group, include_groups=False)
    print(adx_stats.to_string())

    print("\n[3.5] ATR VOLATILITY PERCENTILES")
    df['atr_bucket'] = pd.qcut(df['atr'], q=3, labels=['Low', 'Medium', 'High'])
    atr_stats = df.groupby('atr_bucket', observed=True).apply(evaluate_group, include_groups=False)
    print(atr_stats.to_string())

    # -------------------------------------------------------------
    # STEP 4: SETUP FILTER DISCOVERY
    # -------------------------------------------------------------
    print("\n[4] BEST PERFORMING SETUP COMBINATIONS")
    combo_stats = df.groupby(['adx_bucket', 'atr_bucket'], observed=True).apply(evaluate_group, include_groups=False).reset_index()
    combo_stats['ProfitFactor_Num'] = combo_stats['ProfitFactor'].replace('inf', 999).astype(float)
    top_combos = combo_stats[combo_stats['Trades'] >= 10] # Require minimum sample size
    top_combos = top_combos.sort_values('ProfitFactor_Num', ascending=False).head(3)
    
    if top_combos.empty:
        print("Not enough varied trades to form significant combined segments.")
    else:
        for idx, row in top_combos.iterrows():
            print(f">> ADX: {row['adx_bucket']:<15} | ATR: {row['atr_bucket']:<6} -> PF: {row['ProfitFactor']}, WinRate: {row['WinRate']}, Expectancy: {row['Expectancy']} (n={row['Trades']})")

    # -------------------------------------------------------------
    # STEP 5: RISK & CONSISTENCY ANALYSIS
    # -------------------------------------------------------------
    print("\n[5] RISK & CONSISTENCY OBSERVATIONS")
    max_consec_wins = calculate_consecutive(df['outcome'], 1)
    max_consec_losses = calculate_consecutive(df['outcome'], 0)
    
    # Drawdown Approx based on R cumulative
    df['cum_r'] = df['r_multiple'].fillna(0).cumsum()
    df['peak_r'] = df['cum_r'].cummax()
    df['drawdown_r'] = df['peak_r'] - df['cum_r']
    max_dd_r = df['drawdown_r'].max()

    print(f"Max Consecutive Wins   : {int(max_consec_wins)}")
    print(f"Max Consecutive Losses : {int(max_consec_losses)}")
    print(f"Max Drawdown (R-Units) : {max_dd_r:.2f}R")
    
    print("\n" + "="*60)

if __name__ == "__main__":
    run_performance_audit()
