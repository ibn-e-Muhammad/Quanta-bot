import sqlite3
import pandas as pd
import numpy as np


def generate_ecg_report(db_path):
    print(f"Reading ECG from {db_path}...\n")
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query("SELECT * FROM historical_trades ORDER BY timestamp ASC", conn)
    except Exception as e:
        print(f"Error reading database: {e}"); return
    finally:
        conn.close()

    if df.empty:
        print("[WARNING] NO TRADES TAKEN."); return

    # ── Portfolio totals ──────────────────────────────────────────────
    total_trades = len(df)
    wins         = len(df[df['outcome'] > 0])
    win_rate     = wins / total_trades * 100
    final_bal    = df['running_balance'].iloc[-1]
    net_pnl      = final_bal - 10000.0
    net_pnl_pct  = net_pnl / 10000.0 * 100

    df['peak']     = df['running_balance'].cummax()
    df['drawdown'] = (df['running_balance'] - df['peak']) / df['peak']
    max_dd         = df['drawdown'].min() * 100

    pos_pnl  = df[df['net_pnl_usd'] > 0]['net_pnl_usd']
    neg_pnl  = df[df['net_pnl_usd'] <= 0]['net_pnl_usd']
    pos_sum  = pos_pnl.sum()
    neg_sum  = abs(neg_pnl.sum())
    pf       = pos_sum / neg_sum if neg_sum > 0 else float('inf')
    max_win  = df['net_pnl_usd'].max()
    avg_win  = pos_pnl.mean() if len(pos_pnl) > 0 else 0
    avg_loss = neg_pnl.mean() if len(neg_pnl) > 0 else 0

    # Max consecutive losses
    outcomes_list  = df['outcome'].tolist()
    max_consec_loss = 0; cur = 0
    for o in outcomes_list:
        if o == 0: cur += 1; max_consec_loss = max(max_consec_loss, cur)
        else: cur = 0

    # Equity curve std (stability proxy)
    eq_std = df['running_balance'].pct_change().std() * 100

    is_pf_ok  = pf >= 1.10
    is_dd_ok  = abs(max_dd) <= 12.0
    is_vol_ok = 200 <= total_trades <= 500

    print("=================================================")
    print(" [PHASE 6] PROP FIRM SURVIVAL SYSTEM REPORT ")
    print("=================================================")
    print(" [PORTFOLIO METRICS]")
    print(f" Total Trades   : {total_trades} (200-500: {'Y' if is_vol_ok else 'N'})")
    print(f" Win Rate       : {win_rate:.2f}%  ({wins}W / {total_trades - wins}L)")
    print(f" Profit Factor  : {pf:.3f} (>= 1.10: {'Y' if is_pf_ok else 'N'})")
    print(f" Net PnL        : ${net_pnl:.2f} ({net_pnl_pct:.2f}%)")
    print(f" Max Drawdown   : {max_dd:.2f}% (<= 12%: {'Y' if is_dd_ok else 'N'})")
    print(f" Max Win Trade  : ${max_win:.2f}")
    print(f" Avg Win / Loss : ${avg_win:.2f} / ${avg_loss:.2f}")
    print("-------------------------------------------------")
    print(" [RISK METRICS]")
    print(f" Max Consec. Losses : {max_consec_loss}")
    print(f" Equity Curve Std   : {eq_std:.3f}% per trade")

    # Daily DD breach detection
    df['trade_date'] = pd.to_datetime(df['timestamp']).dt.date
    daily_pnl = df.groupby('trade_date')['net_pnl_usd'].sum()
    breach_days = (daily_pnl < -150).sum()   # -1.5% of 10k = -150
    print(f" Daily DD Breaches  : {breach_days} days")
    print("-------------------------------------------------")
    print(" [PER-STRATEGY]")
    for strat, grp in df.groupby('strategy_used'):
        st = len(grp); sw = len(grp[grp['outcome'] > 0])
        sp = grp[grp['net_pnl_usd'] > 0]['net_pnl_usd'].sum()
        sl = abs(grp[grp['net_pnl_usd'] <= 0]['net_pnl_usd'].sum())
        spf = f"{sp/sl:.2f}" if sl > 0 else "inf"
        print(f" * {strat}: {st} trades | WR: {sw/st*100:.1f}% | PF: {spf} | PnL: ${grp['net_pnl_usd'].sum():.2f}")
    print("-------------------------------------------------")
    print(" [ENGINE STATE DISTRIBUTION]")
    if 'engine_state' in df.columns:
        for strat, grp in df.groupby('strategy_used'):
            sc = grp['engine_state'].value_counts().to_dict()
            a = sc.get('ACTIVE', 0); c = sc.get('COOLDOWN', 0); r = sc.get('RECOVERY', 0)
            t = max(a + c + r, 1)
            print(f" * {strat}: ACTIVE={a}({a/t*100:.0f}%) COOLDOWN={c} RECOVERY={r}")
    print("-------------------------------------------------")
    print(" [PER-TIMEFRAME BREAKDOWN]")
    if 'interval' in df.columns:
        for tf, grp in df.groupby('interval'):
            tt = len(grp); tn = grp['net_pnl_usd'].sum(); tw = len(grp[grp['outcome'] > 0])
            print(f" * {tf}: {tt} trades | WR: {tw/tt*100:.1f}% | PnL: ${tn:.2f}")
    print("=================================================")

    # Pass/Fail
    passed = is_pf_ok and is_dd_ok and is_vol_ok
    if passed:
        print("[SUCCESS] PROP FIRM CRITERIA MET")
    else:
        flags = []
        if not is_pf_ok:  flags.append(f"PF {pf:.3f} < 1.10")
        if not is_dd_ok:  flags.append(f"MDD {max_dd:.1f}% > 12%")
        if not is_vol_ok: flags.append(f"Trades {total_trades} outside 200-500")
        print(f"[FAIL SAFE] NOT MET: {' | '.join(flags)}")
    print()


if __name__ == "__main__":
    generate_ecg_report(
        "D:/Code/Projects/Quanta Bot/Quanta-bot/research/portfolio_backtests/v18/portfolio_results.sqlite"
    )