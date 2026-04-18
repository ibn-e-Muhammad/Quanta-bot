import sqlite3
import pandas as pd
import numpy as np

def generate_ecg_report(db_path):
    print(f"Reading ECG from {db_path}...\n")
    
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query("SELECT * FROM historical_trades ORDER BY timestamp ASC", conn)
    except Exception as e:
        print(f"Error reading database: {e}")
        return
    finally: conn.close()

    if df.empty:
        print("[WARNING] NO TRADES TAKEN. The filters might be too restrictive or the data didn't load.")
        return

    # Total Metrics
    total_trades = len(df)
    wins = len(df[df['outcome'] > 0])
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    starting_balance = 10000.0
    final_balance = df['running_balance'].iloc[-1]
    net_pnl = final_balance - starting_balance
    df['peak'] = df['running_balance'].cummax()
    df['drawdown'] = (df['running_balance'] - df['peak']) / df['peak']
    max_dd = df['drawdown'].min() * 100
    pos_trades = df[df['net_pnl_usd'] > 0]['net_pnl_usd'].sum()
    neg_trades = abs(df[df['net_pnl_usd'] <= 0]['net_pnl_usd'].sum())
    pf = pos_trades / neg_trades if neg_trades > 0 else float('inf')

    # Status Validation
    is_pf_valid = pf >= 1.15
    is_dd_valid = abs(max_dd) <= 12.0
    is_vol_valid = total_trades >= 800

    print("=================================================")
    print(" [REPORT] PHASE 5 : MULTI-STRATEGY REGIME SYSTEM ")
    print("=================================================")
    print(" [PORTFOLIO METRICS]")
    print(f" Total Trades Taken   : {total_trades} (>= 800: {'Y' if is_vol_valid else 'N'})")
    print(f" Total Win Rate       : {win_rate:.2f}% ({wins}W / {total_trades - wins}L)")
    print(f" Total Profit Factor  : {pf:.2f} (>= 1.15: {'Y' if is_pf_valid else 'N'})")
    print(f" Total PnL (Reality)  : ${net_pnl:.2f}")
    print(f" Maximum Drawdown     : {max_dd:.2f}% (<= 12%: {'Y' if is_dd_valid else 'N'})")
    print("-------------------------------------------------")
    
    print(" [PER-STRATEGY METRICS]")
    strat_grouped = df.groupby('strategy_used')
    for strat, group in strat_grouped:
        s_total = len(group)
        s_wins = len(group[group['outcome'] > 0])
        s_wr = (s_wins / s_total) * 100
        s_net = group['net_pnl_usd'].sum()
        s_pos = group[group['net_pnl_usd'] > 0]['net_pnl_usd'].sum()
        s_neg = abs(group[group['net_pnl_usd'] <= 0]['net_pnl_usd'].sum())
        s_pf = s_pos / s_neg if s_neg > 0 else float('inf')
        s_pct = (s_total / total_trades) * 100
        
        # Dominance Warning
        s_warning = ""
        if s_pct > 70: s_warning = "[WARNING: >70% DOMINANCE]"
        
        print(f" * {strat}: {s_total} Trades ({s_pct:.1f}%) | WR: {s_wr:.1f}% | PF: {s_pf:.2f} | PnL: ${s_net:.2f} {s_warning}")
        
    print("-------------------------------------------------")
    print(" [PER-REGIME METRICS]")
    regime_grouped = df.groupby('regime')
    for rgm, group in regime_grouped:
        r_total = len(group)
        r_net = group['net_pnl_usd'].sum()
        print(f" * Regime {rgm}: {r_total} Trades | PnL: ${r_net:.2f}")

    print("=================================================\n")
    if not (is_pf_valid and is_dd_valid and is_vol_valid):
        print("[FAIL SAFE] SYSTEM REQUIREMENTS NOT MET")

if __name__ == "__main__":
    generate_ecg_report("D:/Code/Projects/Quanta Bot/Quanta-bot/research/portfolio_backtests/v9/portfolio_results.sqlite")