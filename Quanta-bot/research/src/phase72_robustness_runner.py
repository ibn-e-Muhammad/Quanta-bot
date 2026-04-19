import argparse
import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path
from statistics import mean, pstdev

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.historical_simulator import HistoricalSimulator

BASE_OVERRIDES = {
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

BASE_ML_THRESHOLD = 0.52


def _load_watchlist(config_path: Path):
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("matrix", {}).get("watchlist", [])


def _timeline_bounds(source_db: Path):
    conn = sqlite3.connect(str(source_db))
    try:
        row = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM historical_trades"
        ).fetchone()
    finally:
        conn.close()

    if not row or row[0] is None or row[1] is None:
        raise ValueError(f"No timeline found in {source_db}")

    start = pd.to_datetime(row[0], errors="coerce")
    end = pd.to_datetime(row[1], errors="coerce")
    if pd.isna(start) or pd.isna(end):
        raise ValueError("Invalid timeline bounds in source DB")
    return start, end


def _group_symbols_by_atr(source_db: Path):
    conn = sqlite3.connect(str(source_db))
    try:
        df = pd.read_sql_query(
            "SELECT symbol, AVG(atr) AS avg_atr FROM historical_trades GROUP BY symbol",
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        raise ValueError("Cannot build symbol buckets: no rows in historical_trades")

    df["avg_atr"] = pd.to_numeric(df["avg_atr"], errors="coerce").fillna(0.0)
    df = df.sort_values("avg_atr", ascending=True).reset_index(drop=True)

    symbols_sorted = df["symbol"].tolist()
    buckets = np.array_split(symbols_sorted, 3)

    low = list(buckets[0])
    mid = list(buckets[1])
    high = list(buckets[2])

    return {
        "LOW_VOL_ALT": low,
        "MID_VOL": mid,
        "HIGH_VOL": high,
    }, df.to_dict("records")


def _extract_metrics(result, scenario_name, extra=None):
    metrics = result.get("audit_metrics", {})
    payload = {
        "scenario": scenario_name,
        "db_path": result.get("db_path"),
        "trade_count": int(result.get("trade_count", 0)),
        "win_rate": float(metrics.get("win_rate", 0.0)),
        "profit_factor": float(metrics.get("profit_factor", 0.0)),
        "net_pnl_pct": float(metrics.get("net_pnl_pct", 0.0)),
        "max_drawdown_pct": float(metrics.get("max_drawdown_pct", 0.0)),
        "acceptance_rate": float(metrics.get("ml_acceptance_rate", 0.0)),
        "ml_filtered_trades": int(metrics.get("ml_filtered_trades", 0)),
        "ml_candidates_scored": int(metrics.get("ml_candidates_scored", 0)),
        "ml_fallback_count": int(metrics.get("ml_fallback_count", 0)),
        "ml_inference_error_count": int(metrics.get("ml_inference_error_count", 0)),
    }
    if extra:
        payload.update(extra)
    return payload


def _run_simulation_case(
    case_name,
    output_dir: Path,
    watchlist,
    base_config: Path,
    data_dir: Path,
    initial_balance=100000.0,
    ml_threshold=BASE_ML_THRESHOLD,
    simulation_start=None,
    simulation_end=None,
    symbol_subset=None,
    regime_filter=None,
    shuffle_ml=False,
    shuffle_seed=42,
):
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / f"{case_name}.sqlite"
    if db_path.exists():
        db_path.unlink()

    overrides = dict(BASE_OVERRIDES)
    overrides["initial_balance"] = float(initial_balance)
    overrides["ml_threshold"] = float(ml_threshold)
    if simulation_start is not None:
        overrides["simulation_start"] = str(pd.Timestamp(simulation_start).isoformat())
    if simulation_end is not None:
        overrides["simulation_end"] = str(pd.Timestamp(simulation_end).isoformat())
    if symbol_subset:
        overrides["symbol_subset"] = list(symbol_subset)
    if regime_filter:
        overrides["regime_filter"] = str(regime_filter)
    overrides["shuffle_ml"] = bool(shuffle_ml)
    overrides["shuffle_seed"] = int(shuffle_seed)

    sim = HistoricalSimulator(str(base_config), str(data_dir), str(db_path), config_override=overrides)
    result = sim.run_portfolio_simulation(
        watchlist,
        simulation_start=simulation_start,
        simulation_end=simulation_end,
        symbol_subset=symbol_subset,
    )
    return result


def _safe_std(values):
    finite_vals = [float(v) for v in values if np.isfinite(float(v))]
    if len(finite_vals) <= 1:
        return 0.0
    return float(pstdev(finite_vals))


def _normalize_pf_for_stats(values, fallback_cap=5.0):
    finite_vals = [float(v) for v in values if np.isfinite(float(v))]
    cap = max(finite_vals) if finite_vals else float(fallback_cap)
    normalized = []
    for v in values:
        fv = float(v)
        if np.isfinite(fv):
            normalized.append(fv)
        else:
            normalized.append(cap)
    return normalized


def _calc_distribution(values):
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0, "non_finite_count": 0}
    vals = [float(v) for v in values]
    finite_vals = [v for v in vals if np.isfinite(v)]
    non_finite_count = len(vals) - len(finite_vals)
    if not finite_vals:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0, "non_finite_count": non_finite_count}
    return {
        "min": float(min(finite_vals)),
        "max": float(max(finite_vals)),
        "mean": float(mean(finite_vals)),
        "std": float(_safe_std(finite_vals)),
        "non_finite_count": non_finite_count,
    }


def _classify_system(test_results):
    passed = [k for k, v in test_results.items() if bool(v.get("passed", False))]
    failed = [k for k, v in test_results.items() if not bool(v.get("passed", False))]

    if len(failed) == 0:
        return "ROBUST_EDGE"

    if "test_1_time_oos" in failed or "test_6_shuffle_sanity" in failed:
        return "OVERFIT_OR_FRAGILE"

    if len(failed) <= 2:
        return "CONDITIONAL_EDGE"

    return "OVERFIT_OR_FRAGILE"


def run_phase72(output_version="v27"):
    base_config = _ROOT / "runtime" / "config" / "strategy_config.json"
    data_dir = _ROOT / "research" / "historical_data"
    output_dir = _ROOT / "research" / "portfolio_backtests" / output_version
    source_db = output_dir / "portfolio_results_tier_100k.sqlite"

    if not source_db.exists():
        raise FileNotFoundError(f"Missing source DB for validation: {source_db}")

    watchlist = _load_watchlist(base_config)
    timeline_start, timeline_end = _timeline_bounds(source_db)

    runs_dir = output_dir / "phase72_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    tests = {}
    all_scenarios = []

    # Baseline full-timeline run for cross-test consistency and test6 comparison.
    baseline_raw = _run_simulation_case(
        case_name="baseline_threshold_052",
        output_dir=runs_dir,
        watchlist=watchlist,
        base_config=base_config,
        data_dir=data_dir,
        ml_threshold=BASE_ML_THRESHOLD,
    )
    baseline = _extract_metrics(
        baseline_raw,
        "baseline_threshold_052",
        extra={
            "threshold": BASE_ML_THRESHOLD,
            "window_start": str(timeline_start),
            "window_end": str(timeline_end),
        },
    )
    all_scenarios.append(baseline)

    # TEST 1 — Time-Based OOS
    full_span = timeline_end - timeline_start
    segment_a_end = timeline_start + (full_span * 0.33)
    segment_b_end = timeline_start + (full_span * 0.66)

    segment_defs = [
        ("SEGMENT_A", timeline_start, segment_a_end),
        ("SEGMENT_B", segment_a_end, segment_b_end),
        ("SEGMENT_C", segment_b_end, timeline_end + timedelta(seconds=1)),
    ]

    seg_results = []
    for seg_name, seg_start, seg_end in segment_defs:
        raw = _run_simulation_case(
            case_name=f"time_oos_{seg_name.lower()}",
            output_dir=runs_dir,
            watchlist=watchlist,
            base_config=base_config,
            data_dir=data_dir,
            ml_threshold=BASE_ML_THRESHOLD,
            simulation_start=seg_start,
            simulation_end=seg_end,
        )
        row = _extract_metrics(
            raw,
            f"time_oos_{seg_name.lower()}",
            extra={
                "segment": seg_name,
                "window_start": str(seg_start),
                "window_end": str(seg_end),
            },
        )
        seg_results.append(row)
        all_scenarios.append(row)

    test1_pass = all(
        (r["profit_factor"] >= 1.5) and (abs(r["max_drawdown_pct"]) <= 10.0)
        for r in seg_results
    )
    tests["test_1_time_oos"] = {
        "name": "Time-Based Out-of-Sample",
        "segments": seg_results,
        "criteria": {
            "pf_min_all_segments": 1.5,
            "maxdd_abs_max_all_segments": 10.0,
        },
        "passed": bool(test1_pass),
    }

    # TEST 2 — Rolling 90D windows with 30D step.
    window_days = 90
    step_days = 30
    rolling_results = []
    ws = timeline_start
    idx = 1
    while ws + timedelta(days=window_days) <= (timeline_end + timedelta(seconds=1)):
        we = ws + timedelta(days=window_days)
        raw = _run_simulation_case(
            case_name=f"rolling_w{idx:03d}",
            output_dir=runs_dir,
            watchlist=watchlist,
            base_config=base_config,
            data_dir=data_dir,
            ml_threshold=BASE_ML_THRESHOLD,
            simulation_start=ws,
            simulation_end=we,
        )
        row = _extract_metrics(
            raw,
            f"rolling_w{idx:03d}",
            extra={
                "window_index": idx,
                "window_start": str(ws),
                "window_end": str(we),
                "window_days": window_days,
            },
        )
        rolling_results.append(row)
        all_scenarios.append(row)
        ws = ws + timedelta(days=step_days)
        idx += 1

    rolling_pfs = [r["profit_factor"] for r in rolling_results]
    rolling_pfs_for_stats = _normalize_pf_for_stats(rolling_pfs)
    worst_window = min(rolling_results, key=lambda x: x["profit_factor"], default=None)
    best_window = max(rolling_results, key=lambda x: x["profit_factor"], default=None)
    catastrophic_windows = [r for r in rolling_results if r["profit_factor"] < 1.0]

    pf_std = _safe_std(rolling_pfs_for_stats)
    worst_pf = worst_window["profit_factor"] if worst_window else 0.0
    test2_pass = (pf_std <= 0.4) and (worst_pf >= 1.2) and (len(catastrophic_windows) == 0)

    tests["test_2_rolling_stability"] = {
        "name": "Rolling Window Stability",
        "window_definition": {
            "window_days": window_days,
            "step_days": step_days,
            "window_count": len(rolling_results),
        },
        "windows": rolling_results,
        "summary": {
            "pf_mean": float(mean(rolling_pfs_for_stats)) if rolling_pfs_for_stats else 0.0,
            "pf_std": float(pf_std),
            "worst_pf_window": worst_window,
            "best_pf_window": best_window,
            "catastrophic_window_count": len(catastrophic_windows),
        },
        "criteria": {
            "pf_std_max": 0.4,
            "worst_pf_min": 1.2,
            "no_window_pf_below": 1.0,
        },
        "passed": bool(test2_pass),
    }

    # TEST 3 — Cross-Symbol Generalization (ATR percentile buckets)
    symbol_groups, symbol_atr_table = _group_symbols_by_atr(source_db)
    group_results = []
    global_acceptance = baseline["acceptance_rate"]
    acceptance_tolerance = global_acceptance * 0.15

    for group_name, symbols in symbol_groups.items():
        raw = _run_simulation_case(
            case_name=f"symbol_group_{group_name.lower()}",
            output_dir=runs_dir,
            watchlist=watchlist,
            base_config=base_config,
            data_dir=data_dir,
            ml_threshold=BASE_ML_THRESHOLD,
            symbol_subset=symbols,
        )
        row = _extract_metrics(
            raw,
            f"symbol_group_{group_name.lower()}",
            extra={"group": group_name, "symbols": symbols},
        )
        row["acceptance_delta_vs_global"] = row["acceptance_rate"] - global_acceptance
        row["acceptance_within_tolerance"] = (
            abs(row["acceptance_delta_vs_global"]) <= acceptance_tolerance
            if global_acceptance > 0
            else row["acceptance_rate"] == 0
        )
        group_results.append(row)
        all_scenarios.append(row)

    pf_ok = all(r["profit_factor"] >= 1.4 for r in group_results)
    collapse_ok = all(r["profit_factor"] >= 1.2 for r in group_results)
    acc_ok = all(r["acceptance_within_tolerance"] for r in group_results)

    tests["test_3_cross_symbol_generalization"] = {
        "name": "Cross-Symbol Generalization",
        "symbol_bucket_method": "avg_atr_percentiles_top_mid_bottom_33pct",
        "symbol_atr_table": symbol_atr_table,
        "global_acceptance_rate": global_acceptance,
        "acceptance_tolerance_abs": acceptance_tolerance,
        "groups": group_results,
        "criteria": {
            "pf_min_all_groups": 1.4,
            "no_group_pf_below": 1.2,
            "acceptance_stability_within_pct_of_global": 15.0,
        },
        "passed": bool(pf_ok and collapse_ok and acc_ok),
    }

    # TEST 4 — Regime-Based Performance
    regime_names = ["TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY"]
    regime_results = []
    for regime_name in regime_names:
        raw = _run_simulation_case(
            case_name=f"regime_{regime_name.lower()}",
            output_dir=runs_dir,
            watchlist=watchlist,
            base_config=base_config,
            data_dir=data_dir,
            ml_threshold=BASE_ML_THRESHOLD,
            regime_filter=regime_name,
        )
        row = _extract_metrics(
            raw,
            f"regime_{regime_name.lower()}",
            extra={"regime": regime_name},
        )
        regime_results.append(row)
        all_scenarios.append(row)

    regimes_pf_ge_16 = [r for r in regime_results if r["profit_factor"] >= 1.6]
    no_collapse = all(r["profit_factor"] >= 1.1 for r in regime_results)
    ranging_row = next((r for r in regime_results if r["regime"] == "RANGING"), None)
    ranging_ok = bool(ranging_row and ranging_row["profit_factor"] >= 1.1)

    tests["test_4_regime_performance"] = {
        "name": "Regime-Based Performance",
        "regime_axis": {
            "trending": "adx > 25",
            "ranging": "adx <= 25",
            "high_volatility": "atr/close > 0.02",
            "low_volatility": "atr/close <= 0.02",
        },
        "regimes": regime_results,
        "criteria": {
            "at_least_n_regimes_pf_ge": {"n": 2, "pf": 1.6},
            "no_regime_pf_below": 1.1,
            "ranging_no_collapse_pf_min": 1.1,
        },
        "passed": bool((len(regimes_pf_ge_16) >= 2) and no_collapse and ranging_ok),
    }

    # TEST 5 — Threshold Sensitivity Sweep
    thresholds = [0.48, 0.50, 0.52, 0.55, 0.60]
    threshold_results = [baseline]

    for th in thresholds:
        if abs(th - BASE_ML_THRESHOLD) < 1e-9:
            continue
        raw = _run_simulation_case(
            case_name=f"threshold_{str(th).replace('.', '')}",
            output_dir=runs_dir,
            watchlist=watchlist,
            base_config=base_config,
            data_dir=data_dir,
            ml_threshold=th,
        )
        row = _extract_metrics(raw, f"threshold_{th:.2f}", extra={"threshold": th})
        threshold_results.append(row)
        all_scenarios.append(row)

    threshold_results = sorted(threshold_results, key=lambda x: float(x.get("threshold", BASE_ML_THRESHOLD)))
    threshold_pfs = [r["profit_factor"] for r in threshold_results]
    threshold_pfs_for_stats = _normalize_pf_for_stats(threshold_pfs)
    threshold_trades = [r["trade_count"] for r in threshold_results]

    pf_adj_deltas = [
        abs(threshold_pfs_for_stats[i] - threshold_pfs_for_stats[i - 1])
        for i in range(1, len(threshold_pfs_for_stats))
    ]
    max_pf_jump = max(pf_adj_deltas) if pf_adj_deltas else 0.0
    smooth_pf_curve = max_pf_jump <= 0.5

    best_pf = max(threshold_pfs) if threshold_pfs else 0.0
    near_best = [r for r in threshold_results if r["profit_factor"] >= (best_pf * 0.95)]
    not_isolated = len(near_best) >= 2

    trade_retention = []
    abrupt_trade_collapse = False
    for i in range(1, len(threshold_trades)):
        prev_trades = threshold_trades[i - 1]
        cur_trades = threshold_trades[i]
        ratio = (cur_trades / prev_trades) if prev_trades > 0 else 0.0
        trade_retention.append(ratio)
        if ratio < 0.60:
            abrupt_trade_collapse = True

    test5_pass = smooth_pf_curve and not_isolated and (not abrupt_trade_collapse)

    tests["test_5_threshold_sweep"] = {
        "name": "Threshold Sensitivity Sweep",
        "thresholds": threshold_results,
        "summary": {
            "max_adjacent_pf_jump": max_pf_jump,
            "best_pf": best_pf,
            "near_best_count_within_95pct": len(near_best),
            "trade_retention_adjacent": trade_retention,
            "abrupt_trade_collapse": abrupt_trade_collapse,
        },
        "criteria": {
            "smooth_pf_curve_max_adjacent_jump": 0.5,
            "best_pf_not_isolated": True,
            "trade_count_no_adjacent_drop_below_ratio": 0.60,
        },
        "passed": bool(test5_pass),
    }

    # TEST 6 — Randomization / Shuffle sanity
    shuffle_raw = _run_simulation_case(
        case_name="shuffle_ml_sanity",
        output_dir=runs_dir,
        watchlist=watchlist,
        base_config=base_config,
        data_dir=data_dir,
        ml_threshold=BASE_ML_THRESHOLD,
        shuffle_ml=True,
        shuffle_seed=42,
    )
    shuffle_metrics = _extract_metrics(
        shuffle_raw,
        "shuffle_ml_sanity",
        extra={"shuffle_ml": True, "shuffle_seed": 42},
    )
    all_scenarios.append(shuffle_metrics)

    pf_drop = baseline["profit_factor"] - shuffle_metrics["profit_factor"]
    pnl_drop = baseline["net_pnl_pct"] - shuffle_metrics["net_pnl_pct"]
    expected_pf_band = 1.0 <= shuffle_metrics["profit_factor"] <= 1.2
    degradation_clear = (
        (shuffle_metrics["profit_factor"] < baseline["profit_factor"]) and
        (shuffle_metrics["net_pnl_pct"] < baseline["net_pnl_pct"]) and
        ((pf_drop >= 0.3) or expected_pf_band) and
        (pnl_drop >= 20.0)
    )

    tests["test_6_shuffle_sanity"] = {
        "name": "Randomization Monte Carlo Sanity",
        "baseline": baseline,
        "shuffle_run": shuffle_metrics,
        "comparison": {
            "pf_drop": pf_drop,
            "net_pnl_pct_drop": pnl_drop,
            "shuffle_pf_in_expected_band_1_0_to_1_2": expected_pf_band,
        },
        "criteria": {
            "clear_degradation_vs_real_model": True,
            "pf_drop_min_or_expected_band": {"min_drop": 0.3, "expected_band": [1.0, 1.2]},
            "net_pnl_pct_drop_min": 20.0,
        },
        "passed": bool(degradation_clear),
    }

    # Aggregate + verdict
    pf_values = [r["profit_factor"] for r in all_scenarios]
    pnl_values = [r["net_pnl_pct"] for r in all_scenarios]
    dd_values = [r["max_drawdown_pct"] for r in all_scenarios]
    acc_values = [r["acceptance_rate"] for r in all_scenarios]

    worst_pf_case = min(all_scenarios, key=lambda x: x["profit_factor"], default={})
    worst_pnl_case = min(all_scenarios, key=lambda x: x["net_pnl_pct"], default={})
    worst_dd_case = min(all_scenarios, key=lambda x: x["max_drawdown_pct"], default={})

    acceptance_stability = {
        "global_acceptance_baseline": baseline["acceptance_rate"],
        "distribution": _calc_distribution(acc_values),
        "min_case": min(all_scenarios, key=lambda x: x["acceptance_rate"], default={}),
        "max_case": max(all_scenarios, key=lambda x: x["acceptance_rate"], default={}),
    }

    verdict = _classify_system(tests)

    tests_passed = sum(1 for t in tests.values() if t.get("passed"))
    tests_total = len(tests)

    report = {
        "phase": "7.2_robustness_validation",
        "baseline_reference": {
            "version": output_version,
            "source_db": str(source_db),
            "model_artifacts": {
                "model": str(_ROOT / "ml" / "artifacts" / "model.pkl"),
                "feature_config": str(_ROOT / "ml" / "artifacts" / "feature_config.json"),
                "training_metrics": str(_ROOT / "ml" / "artifacts" / "training_metrics.json"),
            },
            "constraints": {
                "strategy_logic_changed": False,
                "risk_logic_changed": False,
                "ml_architecture_changed": False,
                "feature_set_changed": False,
                "baseline_threshold": BASE_ML_THRESHOLD,
            },
        },
        "timeline": {
            "start": str(timeline_start),
            "end": str(timeline_end),
        },
        "tests": tests,
        "aggregated_metrics": {
            "pf_distribution": _calc_distribution(pf_values),
            "net_pnl_pct_distribution": _calc_distribution(pnl_values),
            "max_drawdown_pct_distribution": _calc_distribution(dd_values),
            "worst_case_analysis": {
                "worst_pf_case": worst_pf_case,
                "worst_net_pnl_case": worst_pnl_case,
                "worst_drawdown_case": worst_dd_case,
            },
            "acceptance_stability": acceptance_stability,
        },
        "final_verdict": {
            "classification": verdict,
            "tests_passed": tests_passed,
            "tests_total": tests_total,
            "failed_tests": [name for name, data in tests.items() if not data.get("passed")],
        },
    }

    report_path = output_dir / "phase72_robustness_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"[PHASE72] Report written: {report_path}")
    print(f"[PHASE72] Final verdict: {verdict} ({tests_passed}/{tests_total} tests passed)")

    return report_path


def main():
    parser = argparse.ArgumentParser(description="Run Phase 7.2 robustness validation suite")
    parser.add_argument(
        "--version",
        default="v27",
        help="Backtest version folder under research/portfolio_backtests (default: v27)",
    )
    args = parser.parse_args()
    run_phase72(output_version=args.version)


if __name__ == "__main__":
    main()
