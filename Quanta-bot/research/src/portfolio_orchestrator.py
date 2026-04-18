import json
import time
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.historical_simulator import HistoricalSimulator
from research.src.capacity_correlation_audit import write_phase61_scaling_report, write_phase62_scaling_report

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
    
    capital_tiers = [
        ("TIER_10K", 10_000.0),
        ("TIER_100K", 100_000.0),
        ("TIER_1M", 1_000_000.0),
    ]

    tier_results = []
    start_time = time.time()

    for tier_name, initial_balance in capital_tiers:
        db_path = ver_dir / f"portfolio_results_{tier_name.lower()}.sqlite"
        tier_overrides = dict(overrides)
        tier_overrides["initial_balance"] = initial_balance

        print(f"\n[ORCHESTRATOR] Running {tier_name} | initial_balance=${initial_balance:,.0f}")
        sim = HistoricalSimulator(str(base_config), str(data_dir), str(db_path), config_override=tier_overrides)

        try:
            result = sim.run_portfolio_simulation(watchlist)
            result["tier_name"] = tier_name
            result["initial_balance"] = initial_balance
            tier_results.append(result)
            print(
                f"[ORCHESTRATOR] {tier_name} complete | trades={result.get('trade_count', 0)} "
                f"| generated={result.get('audit_metrics', {}).get('total_signals_generated', 0)} "
                f"| rejected={result.get('audit_metrics', {}).get('rejected_signals', 0)}"
            )
        except Exception as e:
            print(f"[ERROR] {tier_name} failed: {e}")

    duration = time.time() - start_time
    print(f"\n[ORCHESTRATOR] SIMULATION COMPLETE | Duration: {duration:.2f}s")

    report_paths_61 = write_phase61_scaling_report(tier_results, ver_dir)
    report_paths_62 = write_phase62_scaling_report(tier_results, ver_dir)

    print("[ORCHESTRATOR] Phase 6.1 artifacts generated:")
    for k, v in report_paths_61.items():
        print(f"  - {k}: {v}")

    print("[ORCHESTRATOR] Phase 6.2 artifacts generated:")
    for k, v in report_paths_62.items():
        print(f"  - {k}: {v}")

if __name__ == "__main__":
    run_portfolio_orchestrator()
