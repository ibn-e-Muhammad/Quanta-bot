import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.historical_simulator import HistoricalSimulator
from research.src.capacity_correlation_audit import (
    write_phase61_scaling_report,
    write_phase62_scaling_report,
    write_phase7_ml_report,
)


def main():
    base_config = _ROOT / "runtime" / "config" / "strategy_config.json"
    data_dir = _ROOT / "research" / "historical_data"
    ver_dir = _ROOT / "research" / "portfolio_backtests" / "v27"
    ver_dir.mkdir(parents=True, exist_ok=True)

    with open(base_config, "r", encoding="utf-8") as f:
        conf = json.load(f)
    watchlist = conf.get("matrix", {}).get("watchlist", [])

    overrides = {
        "strategy_type": {"name": "breakout"},
        "mtf_mode": {"name": "fast", "use_4h": False, "use_1h": True},
        "signal_threshold": 3,
        "exit_profile": {"name": "fixed_2R", "tp_rr": 2.0, "partial": False},
        "filters": {
            "use_session_filter": True,
            "use_volatility_filter": True,
            "use_range_expansion": True,
            "use_fake_breakout": True,
        },
    }

    capital_tiers = [
        ("TIER_10K", 10_000.0),
        ("TIER_100K", 100_000.0),
        ("TIER_1M", 1_000_000.0),
    ]

    tier_results = []
    for tier_name, initial_balance in capital_tiers:
        db_path = ver_dir / f"portfolio_results_{tier_name.lower()}.sqlite"
        if db_path.exists():
            db_path.unlink()

        tier_overrides = dict(overrides)
        tier_overrides["initial_balance"] = initial_balance

        print(f"[PHASE74_BASELINE] Running {tier_name} -> {db_path.name}")
        sim = HistoricalSimulator(str(base_config), str(data_dir), str(db_path), config_override=tier_overrides)
        result = sim.run_portfolio_simulation(watchlist)
        result["tier_name"] = tier_name
        result["initial_balance"] = initial_balance
        tier_results.append(result)

    write_phase61_scaling_report(tier_results, ver_dir)
    write_phase62_scaling_report(tier_results, ver_dir)
    report_paths_7 = write_phase7_ml_report(tier_results, ver_dir)

    print("[PHASE74_BASELINE] Phase 7 artifacts:")
    for k, v in report_paths_7.items():
        print(f"  - {k}: {v}")


if __name__ == "__main__":
    main()
