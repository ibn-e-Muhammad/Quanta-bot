"""
Sweep Orchestrator — Prop Firm Parameter Sweep Runner
=====================================================
Reads sweep_matrix.json, generates cartesian product of parameter sets,
runs HistoricalSimulator per tier per combination, and prints grouped
short-form KPI tables for instant comparison.

Usage:
    python research/src/run_prop_firm_sweep.py                   # full sweep
    python research/src/run_prop_firm_sweep.py --dry-run          # preview combinations
    python research/src/run_prop_firm_sweep.py --config path.json # custom matrix
    python research/src/run_prop_firm_sweep.py --tiers TIER_10K   # single tier
"""

import argparse
import json
import sys
import time
from itertools import product
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.historical_simulator import HistoricalSimulator

# ── Helpers ────────────────────────────────────────────────────────────

CAPITAL_TIERS = {
    "TIER_10K": 10_000.0,
    "TIER_100K": 100_000.0,
    "TIER_1M": 1_000_000.0,
}

DEFAULT_TIERS = ["TIER_10K", "TIER_100K"]


def _load_sweep_matrix(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_combinations(matrix):
    """Build cartesian product from sweep_grid, each entry merged with defaults."""
    defaults = dict(matrix.get("defaults", {}))
    grid = matrix.get("sweep_grid", {})

    if not grid:
        return [defaults]

    keys = sorted(grid.keys())
    values = [grid[k] for k in keys]

    combos = []
    for vals in product(*values):
        merged = dict(defaults)
        for k, v in zip(keys, vals):
            merged[k] = v
        combos.append(merged)
    return combos


def _param_slug(combo, grid_keys):
    """Short descriptor for folder naming, e.g. risk0.005_ml0.52."""
    parts = []
    short_names = {
        "risk_per_trade": "risk",
        "ml_confidence_threshold": "ml",
        "gate_safe_threshold": "gsafe",
        "gate_no_trade_threshold": "gnt",
        "gate_warning_ml_penalty": "gpen",
        "gate_trend_override_min_trend": "gtrend",
        "gate_trend_override_max_vol": "gvol",
        "vol_factor_high": "vfh",
        "daily_dd_cap": "ddcap",
        "max_concurrent": "conc",
        "max_notional_mult": "notmul",
        "atr_min_ratio": "atrmin",
    }
    for k in sorted(grid_keys):
        label = short_names.get(k, k[:6])
        val = combo.get(k, "?")
        parts.append(f"{label}{val}")
    return "_".join(parts)


def _next_sweep_version(backtests_root):
    """Find next sequential version number under portfolio_backtests/."""
    existing = []
    if backtests_root.exists():
        for p in backtests_root.iterdir():
            if p.is_dir() and p.name.startswith("sweep_v"):
                try:
                    num = int(p.name.split("_v")[1].split("_")[0])
                    existing.append(num)
                except (ValueError, IndexError):
                    pass
    return max(existing, default=0) + 1


def _extract_kpi(result):
    """Pull short-form KPI from simulation result audit_metrics."""
    am = result.get("audit_metrics", {})
    return {
        "net_pnl_pct": am.get("net_pnl_pct", 0.0),
        "max_dd_pct": am.get("max_drawdown_pct", 0.0),
        "win_rate": am.get("win_rate", 0.0),
        "total_trades": am.get("total_signals_executed", 0),
        "profit_factor": am.get("profit_factor", 0.0),
    }


def _fmt_pf(v):
    import numpy as np
    return "inf" if np.isinf(v) else f"{v:.3f}"


def _print_run_kpi_header():
    print()
    print("=" * 100)
    print(f"  {'Tier':<12} {'NetPnL%':>9} {'MaxDD%':>9} {'WR%':>7} {'Trades':>7} {'PF':>8}")
    print("-" * 100)


def _print_run_kpi_row(tier_name, kpi):
    print(
        f"  {tier_name:<12} "
        f"{kpi['net_pnl_pct']:>9.2f} "
        f"{kpi['max_dd_pct']:>9.2f} "
        f"{kpi['win_rate']:>7.2f} "
        f"{kpi['total_trades']:>7} "
        f"{_fmt_pf(kpi['profit_factor']):>8}"
    )


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run prop-firm parameter sweeps against the historical simulator."
    )
    parser.add_argument(
        "--config",
        default="",
        help="Path to sweep_matrix.json. Default: research/configs/sweep_matrix.json",
    )
    parser.add_argument(
        "--tiers",
        default="",
        help="Comma-separated tiers (TIER_10K,TIER_100K,TIER_1M). Default: TIER_10K,TIER_100K",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned combinations and exit without running simulations.",
    )
    args = parser.parse_args()

    # ── Config ─────────────────────────────────────────────────────────
    if args.config:
        config_path = Path(args.config).resolve()
    else:
        config_path = _ROOT / "research" / "configs" / "sweep_matrix.json"

    if not config_path.exists():
        print(f"[SWEEP] ERROR: Config not found: {config_path}")
        sys.exit(1)

    matrix = _load_sweep_matrix(config_path)
    combos = _build_combinations(matrix)
    grid_keys = sorted(matrix.get("sweep_grid", {}).keys())

    # ── Tiers ──────────────────────────────────────────────────────────
    if args.tiers:
        tier_names = [t.strip().upper() for t in args.tiers.split(",") if t.strip()]
    else:
        tier_names = list(DEFAULT_TIERS)

    for t in tier_names:
        if t not in CAPITAL_TIERS:
            print(f"[SWEEP] ERROR: Unknown tier '{t}'. Valid: {list(CAPITAL_TIERS.keys())}")
            sys.exit(1)

    tiers = [(name, CAPITAL_TIERS[name]) for name in tier_names]

    # ── Paths ──────────────────────────────────────────────────────────
    base_config = _ROOT / "runtime" / "config" / "strategy_config.json"
    data_dir = _ROOT / "research" / "historical_data"
    backtests_root = _ROOT / "research" / "portfolio_backtests"

    with open(base_config, "r", encoding="utf-8") as f:
        strategy_conf = json.load(f)
    watchlist = strategy_conf.get("matrix", {}).get("watchlist", [])

    print(f"[SWEEP] Config     : {config_path.name}")
    print(f"[SWEEP] Tiers      : {', '.join(tier_names)}")
    print(f"[SWEEP] Combos     : {len(combos)}")
    print(f"[SWEEP] Total runs : {len(combos) * len(tiers)}")
    print()

    if args.dry_run:
        print("[SWEEP] DRY RUN -- parameter combinations:\n")
        for i, combo in enumerate(combos, 1):
            slug = _param_slug(combo, grid_keys)
            changed = {k: combo[k] for k in grid_keys}
            print(f"  #{i:>3}  slug={slug}")
            for k, v in sorted(changed.items()):
                print(f"        {k}: {v}")
            print()
        print("[SWEEP] DRY RUN complete. No simulations executed.")
        return

    # ── Execute sweeps ─────────────────────────────────────────────────
    version_base = _next_sweep_version(backtests_root)
    all_results = []

    for combo_idx, combo in enumerate(combos, 1):
        slug = _param_slug(combo, grid_keys)
        folder_name = f"sweep_v{version_base + combo_idx - 1}_{slug}"
        out_dir = backtests_root / folder_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save the config snapshot for reproducibility
        snapshot_path = out_dir / "sweep_config_snapshot.json"
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(combo, f, indent=2)

        changed = {k: combo[k] for k in grid_keys}
        print(f"[SWEEP] -- Run #{combo_idx}/{len(combos)} -- {slug}")
        for k, v in sorted(changed.items()):
            print(f"         {k}: {v}")
        _print_run_kpi_header()

        combo_results = {"slug": slug, "folder": folder_name, "params": changed, "tiers": {}}

        for tier_name, initial_balance in tiers:
            db_path = out_dir / f"portfolio_results_{tier_name.lower()}.sqlite"
            if db_path.exists():
                db_path.unlink()

            override = dict(combo)
            override["initial_balance"] = initial_balance
            # Map ml_confidence_threshold -> ml_threshold (the key the simulator reads)
            if "ml_confidence_threshold" in override:
                override["ml_threshold"] = override.pop("ml_confidence_threshold")

            t0 = time.time()
            sim = HistoricalSimulator(
                str(base_config), str(data_dir), str(db_path),
                config_override=override,
            )
            result = sim.run_portfolio_simulation(watchlist)
            elapsed = time.time() - t0

            kpi = _extract_kpi(result)
            _print_run_kpi_row(tier_name, kpi)
            combo_results["tiers"][tier_name] = kpi
            combo_results["tiers"][tier_name]["elapsed_s"] = round(elapsed, 1)

        print(f"{'=' * 100}\n")
        all_results.append(combo_results)

    # ── Final comparison table ─────────────────────────────────────────
    print()
    print("=" * 120)
    print("  SWEEP COMPARISON TABLE")
    print("=" * 120)

    header_parts = [f"{'#':>3}", f"{'Slug':<40}"]
    for tier_name in tier_names:
        header_parts.append(f"{'NetPnL%':>9} {'MaxDD%':>9} {'WR%':>7} {'Trades':>7} {'PF':>8}  |")
    header_line = "  ".join(header_parts)

    tier_header_parts = [f"{'':>3}", f"{'':40}"]
    for tier_name in tier_names:
        centered = f"-- {tier_name} --"
        tier_header_parts.append(f"{centered:^44}|")
    tier_header_line = "  ".join(tier_header_parts)

    print(tier_header_line)
    print(header_line)
    print("-" * 120)

    for i, r in enumerate(all_results, 1):
        row_parts = [f"{i:>3}", f"{r['slug']:<40}"]
        for tier_name in tier_names:
            kpi = r["tiers"].get(tier_name, {})
            row_parts.append(
                f"{kpi.get('net_pnl_pct', 0.0):>9.2f} "
                f"{kpi.get('max_dd_pct', 0.0):>9.2f} "
                f"{kpi.get('win_rate', 0.0):>7.2f} "
                f"{kpi.get('total_trades', 0):>7} "
                f"{_fmt_pf(kpi.get('profit_factor', 0.0)):>8}  |"
            )
        print("  ".join(row_parts))

    print("=" * 120)
    print(f"\n[SWEEP] Complete. {len(all_results)} parameter sets x {len(tiers)} tiers = {len(all_results) * len(tiers)} runs.")
    print(f"[SWEEP] Results saved under: {backtests_root}")


if __name__ == "__main__":
    main()
