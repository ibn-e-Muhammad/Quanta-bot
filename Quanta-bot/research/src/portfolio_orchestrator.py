import json
import time
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.historical_simulator import HistoricalSimulator

def get_next_version(base_dir, prefix="v"):
    """
    Finds the next incremental version number in a directory.
    Example: backtest_v1, backtest_v2...
    """
    if not base_dir.exists():
        return 1
    
    versions = []
    for item in base_dir.iterdir():
        if item.is_dir() and item.name.startswith(prefix):
            try:
                num = int(item.name[len(prefix):])
                versions.append(num)
            except ValueError:
                continue
    return max(versions) + 1 if versions else 1

def run_portfolio_orchestrator():
    base_config = _ROOT / "runtime" / "config" / "strategy_config.json"
    data_dir = _ROOT / "research" / "historical_data"
    
    # New dedicated results directory
    res_parent = _ROOT / "research" / "portfolio_backtests"
    res_parent.mkdir(parents=True, exist_ok=True)
    
    # Dynamic Versioning
    v_num = get_next_version(res_parent)
    ver_dir = res_parent / f"v{v_num}"
    ver_dir.mkdir()
    
    print(f"[ORCHESTRATOR] Starting Portfolio Simulation: Version {v_num}")
    print(f"[ORCHESTRATOR] Output Directory: {ver_dir}")
    
    with open(base_config, "r") as f:
        conf = json.load(f)
    watchlist = conf.get("matrix", {}).get("watchlist", [])
    
    # Primary Experiment Configuration (Phase 4.5.2 Standard)
    overrides = {
        "strategy_type": {"name": "breakout"},
        "mtf_mode": {"name": "fast", "use_4h": False, "use_1h": True},
        "signal_threshold": 3,
        "exit_profile": {"name": "fixed_2R", "tp_rr": 2.0, "partial": False},
        "filters": {
            "use_session_filter": True,
            "use_volatility_filter": True,
            "use_range_expansion": True,
            "use_fake_breakout": True
        }
    }
    
    # Destination DB mapping
    db_path = ver_dir / "portfolio_results.sqlite"
    
    # Execution
    sim = HistoricalSimulator(str(base_config), str(data_dir), str(db_path), config_override=overrides)
    
    start_time = time.time()
    try:
        sim.run_portfolio_simulation(watchlist)
        duration = time.time() - start_time
        print(f"\n[ORCHESTRATOR] SIMULATION COMPLETE")
        print(f"Duration: {duration:.2f}s")
        print(f"Results saved to: {db_path}")
    except Exception as e:
        print(f"\n[ERROR] Portfolio Simulation Failed: {e}")

if __name__ == "__main__":
    run_portfolio_orchestrator()
