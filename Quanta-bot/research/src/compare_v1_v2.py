import pandas as pd
import sqlite3
import numpy as np
from pathlib import Path

def aggregate_trades(db_dir):
    db_files = list(db_dir.glob("*_trades.sqlite"))
    all_trades = []
    
    for db_file in db_files:
        conn = sqlite3.connect(db_file)
        try:
            df = pd.read_sql_query("SELECT * FROM historical_trades", conn)
            if not df.empty:
                all_trades.append(df)
        except Exception:
            pass
        finally:
            conn.close()
            
    if not all_trades:
        return pd.DataFrame()
        
    df = pd.concat(all_trades, ignore_index=True)
    
    df['is_buy'] = df['signal_type'] == 'BUY'
    df['pnl_raw'] = np.where(df['is_buy'], df['exit_price'] - df['entry_price'], df['entry_price'] - df['exit_price'])
    df['pnl_pct'] = df['pnl_raw'] / df['entry_price']
    
    return df

def run_comparison():
    _ROOT = Path(__file__).resolve().parent.parent.parent
    v1_dir = _ROOT / "research" / "backtest_results"
    v2_dir = _ROOT / "research" / "backtest_results_v2"
    
    df1 = aggregate_trades(v1_dir)
    df2 = aggregate_trades(v2_dir)
    
    if df1.empty:
        print("V1 is empty.")
        return
        
    def evaluate(df):
        if df.empty:
            return 0, 0, 0, 0, 0
        n = len(df)
        w = len(df[df['outcome']==1])
        wr = w / n if n > 0 else 0
        
        gp = df[df['outcome']==1]['pnl_pct'].sum()
        gl = abs(df[df['outcome']==0]['pnl_pct'].sum())
        pf = gp / gl if gl != 0 else float('inf')
        
        w_m = df[df['outcome']==1]['pnl_pct'].mean() if w > 0 else 0
        l_m = df[df['outcome']==0]['pnl_pct'].mean() if (n-w) > 0 else 0
        exp = (wr * w_m) - ((1-wr) * abs(l_m))
        
        df['risk_raw'] = np.where(df['is_buy'], df['entry_price'] - df['sl_price'], df['sl_price'] - df['entry_price'])
        df['risk_raw'] = df['risk_raw'].replace(0, np.nan)
        df['r_multiple'] = df['pnl_raw'] / df['risk_raw']
        df['cum_r'] = df['r_multiple'].fillna(0).cumsum()
        df['peak_r'] = df['cum_r'].cummax()
        max_dd = (df['peak_r'] - df['cum_r']).max()
        
        return n, wr, pf, exp, max_dd
        
    n1, wr1, pf1, exp1, dd1 = evaluate(df1)
    n2, wr2, pf2, exp2, dd2 = evaluate(df2)
    
    reduction = ((n1 - n2) / n1) * 100 if n1 else 0
    delta = exp2 - exp1
    
    print("=== EDGE COMPRESSION RESULTS ===")
    print("\nBefore Filtering (V1 Baseline):")
    print(f"* Trades     : {n1}")
    print(f"* Win Rate   : {wr1*100:.2f}%")
    print(f"* PF         : {pf1:.2f}")
    print(f"* Expectancy : {exp1*100:.3f}%")
    print(f"* Max DD     : {dd1:.1f}R")
    
    print("\nAfter Filtering (V2 Edge Engine):")
    print(f"* Trades     : {n2}")
    print(f"* Win Rate   : {wr2*100:.2f}%")
    print(f"* PF         : {pf2:.2f}")
    print(f"* Expectancy : {exp2*100:.3f}%")
    print(f"* Max DD     : {dd2:.1f}R")
    
    print("\nTrade Reduction:")
    print(f"* {reduction:.1f}% reduction in trades")
    
    print("\nEdge Improvement:")
    print(f"* Expectancy delta : {delta*100:+.3f}%")
    
if __name__ == "__main__":
    run_comparison()
