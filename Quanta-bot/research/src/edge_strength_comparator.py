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
    df['position_size'] = df.get('position_size', 1.0)
    df['pnl_pct'] = (df['pnl_raw'] / np.where(df['entry_price'] == 0, 1, df['entry_price'])) * df['position_size']
    
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
    # Multiply internal risk evaluation by base dynamically sized entry fraction
    df['risk_raw'] = df['risk_raw'].replace(0, np.nan) 
    df['r_mult'] = df['pnl_pct'] / (df['risk_raw'] / np.where(df['entry_price'] == 0, 1, df['entry_price']))
    df['cum_r'] = df['r_mult'].fillna(0).cumsum()
    max_dd = (df['cum_r'].cummax() - df['cum_r']).max()
    if pd.isna(max_dd): max_dd = 0
    
    return n, wr, pf, exp, max_dd

def run_comparison():
    _ROOT = Path(__file__).resolve().parent.parent.parent
    v4_dir = _ROOT / "research" / "edge_strength_results"
    
    if not v4_dir.exists():
        print("No outputs found.")
        return
        
    exp_dirs = [d for d in v4_dir.iterdir() if d.is_dir() and d.name.startswith("EXP_45")]
    results_map = {}
    
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
                
        if not all_trades: continue
        comb_df = pd.concat(all_trades, ignore_index=True)
        n, wr, pf, exp, max_dd = evaluate_df(comb_df)
        
        results_map[ed.name] = {
            "Trades": n, "WR": wr*100, "PF": pf, "Exp": exp*100, "MaxDD": max_dd, "PosAssets": positive_assets
        }

    print("="*60)
    print("EDGE STRENGTH REPORT")
    print("="*60)
    
    base = results_map.get("EXP_45_BASELINE", None)
    if not base:
        print("Baseline missing.")
        return
        
    print(f"BASELINE: PF={base['PF']:.2f} | Exp={base['Exp']:.3f}% | WR={base['WR']:.1f}% | Trades={base['Trades']} | Assets={base['PosAssets']}/13")
    print("-"*60)
    
    for k, v in results_map.items():
        if k == "EXP_45_BASELINE": continue
        print(f"{k[:25]:<25}: PF={v['PF']:.2f} | Exp={v['Exp']:.3f}% | WR={v['WR']:.1f}% | Trades={v['Trades']} | Assets={v['PosAssets']}/13")
        
    print("\n="*60)
    print("NET EXPECTANCY DELTA LOGIC")
    print("="*60)
    
    def log_delta(name_key):
        val = results_map.get(name_key)
        if val:
            delta = val['Exp'] - base['Exp']
            print(f"- {name_key[:22]:<22}: {delta:+.3f}% Exp Delta")
            
    log_delta("EXP_45_FILTERS_SESSION")
    log_delta("EXP_45_FILTERS_VOL")
    log_delta("EXP_45_FILTERS_RANGE")
    log_delta("EXP_45_FILTERS_FAKE")
    log_delta("EXP_45_FILTERS_ALL")
    log_delta("EXP_45_SIZING_ONLY")
    log_delta("EXP_45_ALLOC_ONLY")
    log_delta("EXP_45_SIZING_AND_ALLOC")
    log_delta("EXP_45_COMBINED_ALL")

    c = results_map.get("EXP_45_COMBINED_ALL")
    if c:
        print("\n============================================================")
        print("VERDICT")
        print("============================================================")
        
        if c['PosAssets'] >= 11 and c['Exp'] > 0 and c['Trades'] > (base['Trades']*0.5):
            print("SAFE FOR PAPER TRADING: Config achieved positive alignment cleanly preventing statistical collapse constraints natively.")
        else:
            print("NEEDS REVISION: Configuration violated trade decay boundaries or critically failed asset stability limits.")

if __name__ == "__main__":
    run_comparison()
