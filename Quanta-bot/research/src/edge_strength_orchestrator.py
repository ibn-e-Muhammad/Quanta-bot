import json
import time
from pathlib import Path
import sqlite3
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.historical_simulator import HistoricalSimulator
from research.src.portfolio_risk_controller import build_portfolio_timeline

def run_orchestrator():
    base_config = _ROOT / "runtime" / "config" / "strategy_config.json"
    data_dir = _ROOT / "research" / "historical_data"
    res_dir = _ROOT / "research" / "edge_strength_results"
    res_dir.mkdir(parents=True, exist_ok=True)
    
    with open(base_config, "r") as f:
        conf = json.load(f)
    watchlist = conf.get("matrix", {}).get("watchlist", [])
    
    print("[ORCHESTRATOR] Preloading Global Timeline Matrix for Risk Controller...")
    all_dfs = {}
    
    sim_override = {
        "strategy_type": {"name": "breakout"},
        "mtf_mode": {"name": "fast", "use_4h": False, "use_1h": True}
    }
    
    for sym in watchlist:
        s = HistoricalSimulator(str(base_config), str(data_dir), ":memory:", config_override=sim_override)
        dfs = s.load_data(sym)
        if not any(dfs[k] is None for k in dfs):
            merged = s.align_mtf(dfs)
            sigs = s.generate_signals(merged)
            all_dfs[sym] = sigs
            
    portfolio_map = build_portfolio_timeline(all_dfs)
    print(f"[ORCHESTRATOR] Portfolio Risk Map Built: {len(portfolio_map)} timestamps.")
    
    experiments = [
        {"id": "EXP_45_BASELINE", "ds": False, "aa": False, "f": {}},
        {"id": "EXP_45_FILTERS_SESSION", "ds": False, "aa": False, "f": {"use_session_filter": True}},
        {"id": "EXP_45_FILTERS_VOL", "ds": False, "aa": False, "f": {"use_volatility_filter": True}},
        {"id": "EXP_45_FILTERS_RANGE", "ds": False, "aa": False, "f": {"use_range_expansion": True}},
        {"id": "EXP_45_FILTERS_FAKE", "ds": False, "aa": False, "f": {"use_fake_breakout": True}},
        {"id": "EXP_45_FILTERS_ALL", "ds": False, "aa": False, "f": {"use_session_filter": True, "use_volatility_filter": True, "use_range_expansion": True, "use_fake_breakout": True}},
        {"id": "EXP_45_SIZING_ONLY", "ds": True, "aa": False, "f": {}},
        {"id": "EXP_45_ALLOC_ONLY", "ds": False, "aa": True, "f": {}},
        {"id": "EXP_45_SIZING_AND_ALLOC", "ds": True, "aa": True, "f": {}},
        {"id": "EXP_45_COMBINED_ALL", "ds": True, "aa": True, "f": {"use_session_filter": True, "use_volatility_filter": True, "use_range_expansion": True, "use_fake_breakout": True}}
    ]

    log_dir = _ROOT / "research" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    for exp in experiments:
        exp_id = exp["id"]
        exp_dir = res_dir / exp_id
        exp_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"{exp_id}.log"
        
        print(f">> Executing {exp_id}...")
        
        overrides = {
            "strategy_type": {"name": "breakout"},
            "mtf_mode": {"name": "fast", "use_4h": False, "use_1h": True},
            "signal_threshold": 3,
            "exit_profile": {"name": "fixed_2R", "tp_rr": 2.0, "partial": False},
            "global_portfolio_map": portfolio_map,
            "use_dynamic_sizing": exp["ds"],
            "use_adaptive_allocator": exp["aa"],
            "filters": exp["f"]
        }
        
        with open(log_path, "w") as lf:
            lf.write(f"=== {exp_id} ===\n\n")
            
            for sym in watchlist:
                db_path = exp_dir / f"{sym}.sqlite"
                if db_path.exists():
                    try: db_path.unlink()
                    except: pass
                
                sim = HistoricalSimulator(str(base_config), str(data_dir), str(db_path), config_override=overrides)
                try: 
                    sim.run_simulation(sym)
                    if sim.collapse_warning:
                        lf.write(f"[{sym}] [WARNING] Filter too aggressive - possible overfitting or edge destruction\n")
                    if exp["f"]:
                        lf.write(f"[{sym}] Filter Report: {sim.filter_stats}\n")
                except Exception as e:
                    lf.write(f"[{sym}] ERROR: {e}\n")

if __name__ == "__main__":
    run_orchestrator()
