import os
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

def evaluate_df(df):
    if df.empty:
        return 0, 0, 0, 0, 0
    df['is_buy'] = df['signal_type'] == 'BUY'
    
    df['pnl_raw'] = np.where(df['is_buy'], df['exit_price'] - df['entry_price'], df['entry_price'] - df['exit_price'])
    # Avoid zero division
    df['pnl_pct'] = df['pnl_raw'] / np.where(df['entry_price'] == 0, 1, df['entry_price'])
    
    n = len(df)
    w = len(df[df['outcome']==1])
    wr = w / n if n > 0 else 0
    gp = df[df['outcome']==1]['pnl_pct'].sum()
    gl = abs(df[df['outcome']==0]['pnl_pct'].sum())
    pf = gp / gl if gl != 0 else float('inf')
    
    w_m = df[df['outcome']==1]['pnl_pct'].mean() if w > 0 else 0
    l_m = df[df['outcome']==0]['pnl_pct'].mean() if (n-w) > 0 else 0
    exp = (wr * w_m) - ((1-wr) * abs(l_m))
    
    # Drawdown logic mapping
    df['risk_raw'] = np.where(df['is_buy'], df['entry_price'] - df['sl_price'], df['sl_price'] - df['entry_price'])
    df['risk_raw'] = df['risk_raw'].replace(0, np.nan)
    df['r_mult'] = df['pnl_raw'] / df['risk_raw']
    df['cum_r'] = df['r_mult'].fillna(0).cumsum()
    max_dd = (df['cum_r'].cummax() - df['cum_r']).max()
    if pd.isna(max_dd): max_dd = 0
    
    return n, wr, pf, exp, max_dd

def run_comparison():
    _ROOT = Path(__file__).resolve().parent.parent.parent
    v3_dir = _ROOT / "research" / "backtest_results_v3"
    
    if not v3_dir.exists():
        print("No V3 experiments found.")
        return
        
    exp_dirs = [d for d in v3_dir.iterdir() if d.is_dir() and d.name.startswith("EXP_")]
    results = []
    
    for ed in exp_dirs:
        db_files = list(ed.glob("*.sqlite"))
        all_trades = []
        positive_assets = 0
        
        for db in db_files:
            try:
                conn = sqlite3.connect(db)
                df = pd.read_sql_query("SELECT * FROM historical_trades", conn)
                conn.close()
                if not df.empty:
                    all_trades.append(df)
                    _, _, _, exp, _ = evaluate_df(df)
                    if exp > 0: positive_assets += 1
            except:
                pass
                
        if not all_trades:
            continue
            
        comb_df = pd.concat(all_trades, ignore_index=True)
        # Compute global Exp
        n, wr, pf, exp, max_dd = evaluate_df(comb_df)
        
        if "EXP_EXIT_" in ed.name: dim = "EXIT"
        elif "EXP_MTF_" in ed.name: dim = "MTF"
        elif "EXP_STRAT_" in ed.name: dim = "STRAT"
        elif "EXP_THRESH_" in ed.name: dim = "THRESH"
        else: dim = "UNKNOWN"
        
        results.append({
            "id": ed.name,
            "dim": dim,
            "Trades": n,
            "WR": wr,
            "PF": pf,
            "Exp": exp,
            "MaxDD": max_dd,
            "PosAssets": positive_assets,
            "TotalAssets": len(db_files)
        })
        
    res_df = pd.DataFrame(results)
    if res_df.empty:
        print("No trades found in any experiments.")
        return
        
    # Rank by Exp, then PF
    res_df = res_df.sort_values(by=["Exp", "PF"], ascending=[False, False])
    
    print("="*60)
    print("TOP EXPERIMENTS")
    print("="*60)
    
    disp = res_df.head(10).copy()
    disp['WR'] = disp['WR'].apply(lambda x: f"{x*100:.1f}%")
    disp['PF'] = disp['PF'].apply(lambda x: f"{x:.2f}")
    disp['Exp'] = disp['Exp'].apply(lambda x: f"{x*100:.3f}%")
    disp['MaxDD'] = disp['MaxDD'].apply(lambda x: f"{x:.1f}R")
    
    print(disp[['id', 'PF', 'Exp', 'WR', 'Trades', 'MaxDD', 'PosAssets']].to_string(index=False))
    
    print("\n"+"="*60)
    print("EDGE PATTERN SUMMARY")
    print("="*60)
    
    for dim in res_df['dim'].unique():
        sub = res_df[res_df['dim'] == dim]
        if sub.empty: continue
        best = sub.iloc[0]
        print(f"- {dim:<8}: Optimal Output -> {best['id']}")
        print(f"            (Stats: Expectancy {best['Exp']*100:.3f}%, PF {best['PF']:.2f}, Asset Win Ratio {best['PosAssets']}/{best['TotalAssets']})")

if __name__ == "__main__":
    run_comparison()
