import json
import time
from pathlib import Path

# Important: SysPath mapping since executing via command-line relative root
import sys
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.historical_simulator import HistoricalSimulator

def orchestrate_mutations():
    matrix_path = _ROOT / "research" / "configs" / "experiment_matrix.json"
    with open(matrix_path, "r") as f:
        matrix = json.load(f)
        
    base_config_path = _ROOT / "runtime" / "config" / "strategy_config.json"
    with open(base_config_path, "r") as f:
        base_conf = json.load(f)
        
    watchlist = base_conf.get("matrix", {}).get("watchlist", [])
    
    v3_dir = _ROOT / "research" / "backtest_results_v3"
    v3_dir.mkdir(parents=True, exist_ok=True)
    
    log_dir = _ROOT / "research" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    base_mtf = matrix["mtf_modes"][0]
    base_strat = matrix["strategy_types"][0]
    base_thresh = 4
    
    experiments = []
    
    # 1. Exit Profiles
    for ep in matrix["exit_profiles"]:
        exp_id = f"EXP_EXIT_{ep['name']}_{int(time.time())}"
        experiments.append({
            "id": exp_id,
            "dim": "EXIT_PROFILE",
            "var": ep['name'],
            "override": {
                "exit_profile": ep,
                "mtf_mode": base_mtf,
                "strategy_type": base_strat,
                "signal_threshold": base_thresh
            }
        })
        
    # 2. MTF Modes
    base_exit = matrix["exit_profiles"][0]
    for mm in matrix["mtf_modes"]:
        if mm['name'] == base_mtf['name']: continue
        exp_id = f"EXP_MTF_{mm['name']}_{int(time.time())}"
        experiments.append({
            "id": exp_id,
            "dim": "MTF_MODE",
            "var": mm['name'],
            "override": {
                "exit_profile": base_exit,
                "mtf_mode": mm,
                "strategy_type": base_strat,
                "signal_threshold": base_thresh
            }
        })
        
    # 3. Strategy Types
    for st in matrix["strategy_types"]:
        if st['name'] == base_strat['name']: continue
        exp_id = f"EXP_STRAT_{st['name']}_{int(time.time())}"
        experiments.append({
            "id": exp_id,
            "dim": "STRATEGY_TYPE",
            "var": st['name'],
            "override": {
                "exit_profile": base_exit,
                "mtf_mode": base_mtf,
                "strategy_type": st,
                "signal_threshold": base_thresh
            }
        })
        
    # 4. Thresholds
    for th in matrix["signal_thresholds"]:
        if th == base_thresh: continue
        exp_id = f"EXP_THRESH_{th}_{int(time.time())}"
        experiments.append({
            "id": exp_id,
            "dim": "SIGNAL_THRESHOLD",
            "var": str(th),
            "override": {
                "exit_profile": base_exit,
                "mtf_mode": base_mtf,
                "strategy_type": base_strat,
                "signal_threshold": th
            }
        })
        
    # Run Experiments
    for exp in experiments:
        exp_id = exp["id"]
        exp_dir = v3_dir / exp_id
        exp_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"{exp_id}.log"
        
        with open(log_path, "w") as lf:
            lf.write("="*50 + "\n")
            lf.write(f"EXPERIMENT START\nID: {exp_id}\n")
            lf.write(f"Dimension: {exp['dim']}\nVariant: {exp['var']}\n")
            lf.write("="*50 + "\n\n")
            
            print(f"\n[ORCHESTRATOR] Running {exp_id}...")
            
            for symbol in watchlist:
                db_path = exp_dir / f"{symbol}.sqlite"
                if db_path.exists(): 
                    try: db_path.unlink()
                    except: pass
                
                sim = HistoricalSimulator(
                    config_path=str(base_config_path),
                    data_dir=str(_ROOT / "research" / "historical_data"),
                    db_path=str(db_path),
                    config_override=exp["override"]
                )
                
                try: sim.run_simulation(symbol)
                except Exception as e: print(f"Error on {symbol}: {e}")
                
                # Fetch minimal logs dynamically
                try:
                    conn = __import__('sqlite3').connect(db_path)
                    df = __import__('pandas').read_sql_query("SELECT * FROM historical_trades", conn)
                    conn.close()
                    
                    if df.empty:
                        lf.write(f"[{symbol}] Trades: 0\n")
                    else:
                        n = len(df)
                        df['is_buy'] = df['signal_type'] == 'BUY'
                        # Prevent numpy vectorization crashes by explicitly loading package
                        import numpy as np
                        df['pnl_raw'] = np.where(df['is_buy'], df['exit_price'] - df['entry_price'], df['entry_price'] - df['exit_price'])
                        df['pnl_pct'] = df['pnl_raw'] / df['entry_price']
                        
                        w = len(df[df['outcome']==1])
                        wr = w / n if n > 0 else 0
                        gp = df[df['outcome']==1]['pnl_pct'].sum()
                        gl = abs(df[df['outcome']==0]['pnl_pct'].sum())
                        pf = gp / gl if gl != 0 else float('inf')
                        w_m = df[df['outcome']==1]['pnl_pct'].mean() if w > 0 else 0
                        l_m = df[df['outcome']==0]['pnl_pct'].mean() if (n-w) > 0 else 0
                        exp_val = (wr * w_m) - ((1-wr) * abs(l_m))
                        
                        lf.write(f"[{symbol}] Trades: {n} | WinRate: {wr*100:.1f}% | PF: {pf:.2f} | Exp: {exp_val*100:.3f}%\n")
                except:
                    lf.write(f"[{symbol}] Trades: Error Reading DB\n")
                    
if __name__ == "__main__":
    orchestrate_mutations()
