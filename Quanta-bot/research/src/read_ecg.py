import sqlite3
import pandas as pd
import numpy as np

def generate_ecg_report(db_path):
    print(f"Reading ECG from {db_path}...\n")

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM historical_trades ORDER BY timestamp ASC", conn
        )
    except Exception as e:
        print(f"Error reading database: {e}"); return
    finally:
        conn.close()

    if df.empty:
        print("[WARNING] NO TRADES TAKEN."); return

    # ---- Portfolio totals ----
    total_trades = len(df)
    wins         = len(df[df['outcome'] > 0])
    win_rate     = wins / total_trades * 100
    starting_bal = 10000.0
    final_bal    = df['running_balance'].iloc[-1]
    net_pnl      = final_bal - starting_bal
    net_pnl_pct  = net_pnl / starting_bal * 100

    df['peak']     = df['running_balance'].cummax()
    df['drawdown'] = (df['running_balance'] - df['peak']) / df['peak']
    max_dd         = df['drawdown'].min() * 100

    pos_sum = df[df['net_pnl_usd'] > 0]['net_pnl_usd'].sum()
    neg_sum = abs(df[df['net_pnl_usd'] <= 0]['net_pnl_usd'].sum())
    pf      = pos_sum / neg_sum if neg_sum > 0 else float('inf')

    is_pf_ok  = pf >= 1.15
    is_dd_ok  = abs(max_dd) <= 12.0
    is_vol_ok = total_trades >= 800

    print("=================================================")
    print(" [PHASE 5.2] ADAPTIVE ENGINE GOVERNANCE REPORT ")
    print("=================================================")
    print(" [PORTFOLIO METRICS]")
    print(f" Trades         : {total_trades} (>= 800: {'Y' if is_vol_ok else 'N'})")
    print(f" Win Rate       : {win_rate:.2f}%  ({wins}W / {total_trades - wins}L)")
    print(f" Profit Factor  : {pf:.3f} (>= 1.15: {'Y' if is_pf_ok else 'N'})")
    print(f" Net PnL        : ${net_pnl:.2f} ({net_pnl_pct:.2f}%)")
    print(f" Max Drawdown   : {max_dd:.2f}% (<= 12%: {'Y' if is_dd_ok else 'N'})")
    print("-------------------------------------------------")

    print(" [PER-STRATEGY METRICS]")
    for strat, grp in df.groupby('strategy_used'):
        st  = len(grp)
        sw  = len(grp[grp['outcome'] > 0])
        swr = sw / st * 100
        sp  = grp[grp['net_pnl_usd'] > 0]['net_pnl_usd'].sum()
        sl  = abs(grp[grp['net_pnl_usd'] <= 0]['net_pnl_usd'].sum())
        spf = sp / sl if sl > 0 else float('inf')
        sn  = grp['net_pnl_usd'].sum()
        pct = st / total_trades * 100
        warn = " [WARNING >70%]" if pct > 70 else ""
        print(f" * {strat}: {st} trades ({pct:.1f}%) | WR: {swr:.1f}% | PF: {spf:.2f} | PnL: ${sn:.2f}{warn}")

    print("-------------------------------------------------")
    print(" [PER-REGIME METRICS]")
    for rgm, grp in df.groupby('regime'):
        rt = len(grp)
        rn = grp['net_pnl_usd'].sum()
        print(f" * {rgm}: {rt} trades | PnL: ${rn:.2f}")

    print("-------------------------------------------------")
    print(" [ENGINE STATE DISTRIBUTION]")
    if 'engine_state' in df.columns:
        for strat, grp in df.groupby('strategy_used'):
            state_counts = grp['engine_state'].value_counts().to_dict()
            active   = state_counts.get('ACTIVE', 0)
            cooldown = state_counts.get('COOLDOWN', 0)
            recovery = state_counts.get('RECOVERY', 0)
            total_g  = active + cooldown + recovery
            print(f" * {strat}: ACTIVE={active} ({active/max(total_g,1)*100:.0f}%) "
                  f"COOLDOWN={cooldown} RECOVERY={recovery}")
    else:
        print(" (engine_state column not available in this dataset)")

    print("=================================================")
    if not (is_pf_ok and is_dd_ok and is_vol_ok):
        print("[FAIL SAFE] SYSTEM REQUIREMENTS NOT MET")
    else:
        print("[SUCCESS] ALL PHASE 5.2 CRITERIA MET")
    print()

if __name__ == "__main__":
    generate_ecg_report(
        "D:/Code/Projects/Quanta Bot/Quanta-bot/research/portfolio_backtests/v12/portfolio_results.sqlite"
    )