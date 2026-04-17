import sqlite3
import pandas as pd

def generate_ecg_report(db_path):
    print(f"Reading ECG from {db_path}...\n")
    
    # Connect to the SQLite database
    conn = sqlite3.connect(db_path)
    
    try:
        df = pd.read_sql_query("SELECT * FROM historical_trades ORDER BY timestamp ASC", conn)
    except Exception as e:
        print(f"Error reading database: {e}")
        return
    finally:
        conn.close()

    if df.empty:
        print("[WARNING] NO TRADES TAKEN. The filters might be too restrictive or the data didn't load.")
        return

    # --- Core Metrics Calculation ---
    total_trades = len(df)
    wins = len(df[df['outcome'] > 0])
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    
    total_fees = df['fees_paid'].sum()
    total_slippage = df['slippage_paid'].sum()
    
    starting_balance = 10000.0
    final_balance = df['running_balance'].iloc[-1]
    net_pnl = final_balance - starting_balance
    gross_pnl = df['net_pnl_usd'].sum() + total_fees + total_slippage
    
    df['peak'] = df['running_balance'].cummax()
    df['drawdown'] = (df['running_balance'] - df['peak']) / df['peak']
    max_dd = df['drawdown'].min() * 100

    # Phase 4.75 Additions
    positive_trades = df[df['net_pnl_usd'] > 0]['net_pnl_usd'].sum()
    negative_trades = abs(df[df['net_pnl_usd'] <= 0]['net_pnl_usd'].sum())
    profit_factor = positive_trades / negative_trades if negative_trades > 0 else float('inf')
    
    df['is_win'] = df['net_pnl_usd'] > 0
    df['streak_group'] = (df['is_win'] != df['is_win'].shift()).cumsum()
    streaks = df.groupby(['is_win', 'streak_group']).size()
    max_winning_streak = streaks.loc[True].max() if True in streaks.index.get_level_values(0) else 0
    max_losing_streak = streaks.loc[False].max() if False in streaks.index.get_level_values(0) else 0

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    num_months = (df['timestamp'].max() - df['timestamp'].min()).days / 30.44
    monthly_avg_return_pct = ((net_pnl / starting_balance) * 100) / num_months if num_months > 0 else 0
    
    # Decisions
    net_pnl_pct = (net_pnl / starting_balance) * 100
    is_prop_firm_ready = (net_pnl_pct > 50) and (abs(max_dd) < 40) and (profit_factor >= 1.15)
    status_verdict = "STATUS: PROP FIRM READY" if is_prop_firm_ready else "STATUS: REQUIRES PHASE 4.80 (Adaptive Risk / Exit Optimization)"

    print("\n=================================================")
    print(" [REPORT] QUANTA BOT PHASE 4.80 : EXIT HARDENING & INSTITUTIONAL SIZING ")
    print("=================================================")
    print(f"Total Trades Taken   : {total_trades}")
    print(f"Win Rate             : {win_rate:.2f}% ({wins} W / {total_trades - wins} L)")
    print(f"Profit Factor        : {profit_factor:.2f}")
    print("-------------------------------------------------")
    print(f"Gross PnL (Frictionless): ${gross_pnl:.2f}")
    print(f"Total Fees Paid      : -${total_fees:.2f}")
    print(f"Total Slippage Paid  : -${total_slippage:.2f}")
    print(f"Net PnL (Reality)    : ${net_pnl:.2f} ({net_pnl_pct:.2f}%)")
    print("-------------------------------------------------")
    print(f"Starting Balance     : ${starting_balance:.2f}")
    print(f"Final Balance        : ${final_balance:.2f}")
    print(f"Maximum Drawdown     : {max_dd:.2f}%")
    print(f"Monthly Avg Return % : {monthly_avg_return_pct:.2f}%")
    print(f"Max Winning Streak   : {max_winning_streak}")
    print(f"Max Losing Streak    : {max_losing_streak}")
    print("=================================================")
    
    if total_trades < 700:
        print("[WARNING] Under-trading - system still too restrictive")
    elif total_trades > 2000:
        print("[WARNING] Over-trading - possible noise reintroduction")
    
    print("=================================================\n")

if __name__ == "__main__":
    generate_ecg_report("D:/Code/Projects/Quanta Bot/Quanta-bot/research/portfolio_backtests/v7/portfolio_results.sqlite")