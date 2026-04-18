import json
import sqlite3
from pathlib import Path
import pandas as pd


def _safe_pct(numerator, denominator):
    return (numerator / denominator) if denominator else 0.0


def _diagnose_tier(row):
    rejection_rate = row.get("duplicate_signal_rejection_rate", 0.0)
    execution_rate = row.get("execution_rate", 0.0)

    if rejection_rate >= 0.35 or execution_rate <= 0.50:
        return "SEVERE_CAPACITY_PRESSURE"
    if rejection_rate >= 0.15 or execution_rate <= 0.70:
        return "MODERATE_CAPACITY_PRESSURE"
    return "HEALTHY_CAPACITY"


def write_phase61_scaling_report(tier_results, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scaling_matrix = []
    aggregate_pairs = {}

    for tier in tier_results:
        metrics = tier.get("audit_metrics", {})
        generated = metrics.get("total_signals_generated", 0)
        executed = metrics.get("total_signals_executed", 0)
        rejected = metrics.get("rejected_signals", 0)

        row = {
            "tier_name": tier.get("tier_name"),
            "initial_balance": tier.get("initial_balance"),
            "final_balance": metrics.get("final_balance"),
            "trade_count": tier.get("trade_count", 0),
            "win_rate": metrics.get("win_rate", 0.0),
            "profit_factor": metrics.get("profit_factor", 0.0),
            "net_pnl_pct": metrics.get("net_pnl_pct", 0.0),
            "max_drawdown_pct": metrics.get("max_drawdown_pct", 0.0),
            "total_signals_generated": generated,
            "total_signals_executed": executed,
            "rejected_signals": rejected,
            "duplicate_signal_rejection_rate": metrics.get("duplicate_signal_rejection_rate", 0.0),
            "execution_rate": _safe_pct(executed, generated),
            "cluster_event_count": metrics.get("cluster_event_count", 0),
            "cluster_event_avg_size": metrics.get("cluster_event_avg_size", 0.0),
            "same_candle_multi_symbol_activations": metrics.get("same_candle_multi_symbol_activations", 0),
        }
        row["capacity_diagnosis"] = _diagnose_tier(row)
        scaling_matrix.append(row)

        for pair, count in metrics.get("co_activation_pairs", {}).items():
            aggregate_pairs[pair] = aggregate_pairs.get(pair, 0) + int(count)

    top_5_pairs = sorted(aggregate_pairs.items(), key=lambda x: x[1], reverse=True)[:5]
    top_5_payload = [{"pair": p, "co_activations": c} for p, c in top_5_pairs]

    rejection_rates = [r["duplicate_signal_rejection_rate"] for r in scaling_matrix]
    avg_rejection_rate = sum(rejection_rates) / len(rejection_rates) if rejection_rates else 0.0

    global_diagnosis = "SCALABLE"
    if any(r["capacity_diagnosis"] == "SEVERE_CAPACITY_PRESSURE" for r in scaling_matrix):
        global_diagnosis = "SCALABILITY_BOTTLENECK"
    elif any(r["capacity_diagnosis"] == "MODERATE_CAPACITY_PRESSURE" for r in scaling_matrix):
        global_diagnosis = "SCALING_FRICTION_PRESENT"

    report = {
        "phase": "6.1_capacity_and_correlation_audit",
        "global_diagnosis": global_diagnosis,
        "average_duplicate_signal_rejection_rate": avg_rejection_rate,
        "scaling_matrix": scaling_matrix,
        "top_5_co_activation_pairs": top_5_payload,
        "tier_count": len(scaling_matrix),
    }

    matrix_path = output_dir / "phase61_scaling_matrix.json"
    correlation_path = output_dir / "phase61_correlation_summary.json"
    top_pairs_path = output_dir / "phase61_top5_coactivation_pairs.json"
    report_path = output_dir / "phase61_scalability_diagnosis.json"

    with open(matrix_path, "w", encoding="utf-8") as f:
        json.dump(scaling_matrix, f, indent=2)

    correlation_summary = {
        "cluster_event_count_by_tier": {
            r["tier_name"]: r["cluster_event_count"] for r in scaling_matrix
        },
        "cluster_event_avg_size_by_tier": {
            r["tier_name"]: r["cluster_event_avg_size"] for r in scaling_matrix
        },
        "same_candle_multi_symbol_activations_by_tier": {
            r["tier_name"]: r["same_candle_multi_symbol_activations"] for r in scaling_matrix
        },
        "top_5_co_activation_pairs": top_5_payload,
    }

    with open(correlation_path, "w", encoding="utf-8") as f:
        json.dump(correlation_summary, f, indent=2)

    with open(top_pairs_path, "w", encoding="utf-8") as f:
        json.dump(top_5_payload, f, indent=2)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return {
        "scaling_matrix": str(matrix_path),
        "correlation_summary": str(correlation_path),
        "top_5_pairs": str(top_pairs_path),
        "scalability_diagnosis": str(report_path),
    }


def write_phase62_scaling_report(tier_results, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scaling_matrix = []
    aggregate_pairs = {}

    for tier in tier_results:
        metrics = tier.get("audit_metrics", {})
        generated = metrics.get("total_signals_generated", 0)
        executed = metrics.get("total_signals_executed", 0)
        rejected = metrics.get("rejected_signals", 0)

        rejected_locks = metrics.get("rejected_locks", 0)
        rejected_low_priority = metrics.get("rejected_low_priority", 0)

        row = {
            "tier_name": tier.get("tier_name"),
            "initial_balance": tier.get("initial_balance"),
            "final_balance": metrics.get("final_balance"),
            "trade_count": tier.get("trade_count", 0),
            "total_signals_generated": generated,
            "total_signals_executed": executed,
            "rejected_signals": rejected,
            "rejected_locks": rejected_locks,
            "rejected_low_priority": rejected_low_priority,
            "rejected_locks_pct": _safe_pct(rejected_locks, rejected),
            "rejected_low_priority_pct": _safe_pct(rejected_low_priority, rejected),
            "duplicate_signal_rejection_rate": metrics.get("duplicate_signal_rejection_rate", 0.0),
            "execution_rate": _safe_pct(executed, generated),
            "avg_executed_score": metrics.get("avg_executed_score", 0.0),
            "avg_rejected_score": metrics.get("avg_rejected_score", 0.0),
            "avg_all_signal_score": metrics.get("avg_all_signal_score", 0.0),
            "selection_quality_ratio": metrics.get("selection_quality_ratio", 0.0),
            "cluster_event_count": metrics.get("cluster_event_count", 0),
            "cluster_event_avg_size": metrics.get("cluster_event_avg_size", 0.0),
            "same_candle_multi_symbol_activations": metrics.get("same_candle_multi_symbol_activations", 0),
        }
        row["capacity_diagnosis"] = _diagnose_tier(row)
        scaling_matrix.append(row)

        for pair, count in metrics.get("co_activation_pairs", {}).items():
            aggregate_pairs[pair] = aggregate_pairs.get(pair, 0) + int(count)

    top_5_pairs = sorted(aggregate_pairs.items(), key=lambda x: x[1], reverse=True)[:5]
    top_5_payload = [{"pair": p, "co_activations": c} for p, c in top_5_pairs]

    rejection_rates = [r["duplicate_signal_rejection_rate"] for r in scaling_matrix]
    avg_rejection_rate = sum(rejection_rates) / len(rejection_rates) if rejection_rates else 0.0

    quality_ratios = [r["selection_quality_ratio"] for r in scaling_matrix]
    avg_selection_quality = sum(quality_ratios) / len(quality_ratios) if quality_ratios else 0.0

    global_diagnosis = "SCALABLE"
    if any(r["capacity_diagnosis"] == "SEVERE_CAPACITY_PRESSURE" for r in scaling_matrix):
        global_diagnosis = "SCALABILITY_BOTTLENECK"
    elif any(r["capacity_diagnosis"] == "MODERATE_CAPACITY_PRESSURE" for r in scaling_matrix):
        global_diagnosis = "SCALING_FRICTION_PRESENT"

    report = {
        "phase": "6.2_signal_ranking_and_priority_execution",
        "global_diagnosis": global_diagnosis,
        "average_duplicate_signal_rejection_rate": avg_rejection_rate,
        "average_selection_quality_ratio": avg_selection_quality,
        "scaling_matrix": scaling_matrix,
        "top_5_co_activation_pairs": top_5_payload,
        "tier_count": len(scaling_matrix),
    }

    matrix_path = output_dir / "phase62_scaling_matrix.json"
    priority_metrics_path = output_dir / "phase62_priority_metrics.json"
    correlation_path = output_dir / "phase62_correlation_summary.json"
    top_pairs_path = output_dir / "phase62_top5_coactivation_pairs.json"
    report_path = output_dir / "phase62_scalability_diagnosis.json"

    with open(matrix_path, "w", encoding="utf-8") as f:
        json.dump(scaling_matrix, f, indent=2)

    priority_payload = {
        "avg_executed_score_by_tier": {
            r["tier_name"]: r["avg_executed_score"] for r in scaling_matrix
        },
        "avg_rejected_score_by_tier": {
            r["tier_name"]: r["avg_rejected_score"] for r in scaling_matrix
        },
        "selection_quality_ratio_by_tier": {
            r["tier_name"]: r["selection_quality_ratio"] for r in scaling_matrix
        },
        "rejection_breakdown_by_tier": {
            r["tier_name"]: {
                "rejected_locks": r["rejected_locks"],
                "rejected_low_priority": r["rejected_low_priority"],
                "rejected_locks_pct": r["rejected_locks_pct"],
                "rejected_low_priority_pct": r["rejected_low_priority_pct"],
            }
            for r in scaling_matrix
        },
    }
    with open(priority_metrics_path, "w", encoding="utf-8") as f:
        json.dump(priority_payload, f, indent=2)

    correlation_summary = {
        "cluster_event_count_by_tier": {
            r["tier_name"]: r["cluster_event_count"] for r in scaling_matrix
        },
        "cluster_event_avg_size_by_tier": {
            r["tier_name"]: r["cluster_event_avg_size"] for r in scaling_matrix
        },
        "same_candle_multi_symbol_activations_by_tier": {
            r["tier_name"]: r["same_candle_multi_symbol_activations"] for r in scaling_matrix
        },
        "top_5_co_activation_pairs": top_5_payload,
    }
    with open(correlation_path, "w", encoding="utf-8") as f:
        json.dump(correlation_summary, f, indent=2)

    with open(top_pairs_path, "w", encoding="utf-8") as f:
        json.dump(top_5_payload, f, indent=2)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return {
        "scaling_matrix": str(matrix_path),
        "priority_metrics": str(priority_metrics_path),
        "correlation_summary": str(correlation_path),
        "top_5_pairs": str(top_pairs_path),
        "scalability_diagnosis": str(report_path),
    }


def write_phase7_ml_report(tier_results, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scaling_matrix = []
    for tier in tier_results:
        metrics = tier.get("audit_metrics", {})
        row = {
            "tier_name": tier.get("tier_name"),
            "initial_balance": tier.get("initial_balance"),
            "final_balance": metrics.get("final_balance"),
            "trade_count": tier.get("trade_count", 0),
            "win_rate": metrics.get("win_rate", 0.0),
            "profit_factor": metrics.get("profit_factor", 0.0),
            "net_pnl_pct": metrics.get("net_pnl_pct", 0.0),
            "max_drawdown_pct": metrics.get("max_drawdown_pct", 0.0),
            "ml_filtered_trades": metrics.get("ml_filtered_trades", 0),
            "ml_candidates_scored": metrics.get("ml_candidates_scored", 0),
            "avg_ml_score_executed": metrics.get("avg_ml_score_executed", 0.0),
            "avg_ml_score_rejected": metrics.get("avg_ml_score_rejected", 0.0),
            "ml_acceptance_rate": metrics.get("ml_acceptance_rate", 0.0),
            "ml_fallback_count": metrics.get("ml_fallback_count", 0),
        }
        scaling_matrix.append(row)

    root = Path(__file__).resolve().parent.parent.parent
    baseline_path = root / "research" / "portfolio_backtests" / "v24" / "phase62_scaling_matrix.json"
    baseline_rows = {}
    if baseline_path.exists():
        try:
            with open(baseline_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for row in data:
                    tier_name = row.get("tier_name")
                    if tier_name:
                        baseline_rows[tier_name] = row
        except Exception:
            baseline_rows = {}

    baseline_perf = {}
    baseline_dir = baseline_path.parent
    if baseline_dir.exists():
        for db in baseline_dir.glob("*.sqlite"):
            tier_name = None
            stem = db.stem.lower()
            if "tier_10k" in stem:
                tier_name = "TIER_10K"
            elif "tier_100k" in stem:
                tier_name = "TIER_100K"
            elif "tier_1m" in stem:
                tier_name = "TIER_1M"
            if not tier_name:
                continue
            try:
                conn = sqlite3.connect(str(db))
                df = pd.read_sql_query("SELECT running_balance, net_pnl_usd FROM historical_trades ORDER BY timestamp ASC", conn)
                conn.close()
                if df.empty:
                    continue
                initial_balance = float(df["running_balance"].iloc[0] - df["net_pnl_usd"].iloc[0])
                pnl = pd.to_numeric(df["net_pnl_usd"], errors="coerce").fillna(0.0)
                pos = float(pnl[pnl > 0].sum())
                neg = float(abs(pnl[pnl <= 0].sum()))
                pf = (pos / neg) if neg > 0 else float("inf")
                final_balance = float(df["running_balance"].iloc[-1])
                net_pnl_pct = ((final_balance - initial_balance) / initial_balance * 100.0) if initial_balance else 0.0
                rb = pd.to_numeric(df["running_balance"], errors="coerce").ffill()
                peaks = rb.cummax()
                dd = (rb - peaks) / peaks
                max_dd_pct = float(dd.min() * 100.0)
                baseline_perf[tier_name] = {
                    "profit_factor": pf,
                    "net_pnl_pct": net_pnl_pct,
                    "max_drawdown_pct": max_dd_pct,
                }
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                continue

    impact_rows = []
    for row in scaling_matrix:
        base = baseline_rows.get(row["tier_name"], {})
        d_pf = None
        d_pnl = None
        d_dd = None
        base_pf = base.get("profit_factor")
        base_pnl = base.get("net_pnl_pct")
        base_dd = base.get("max_drawdown_pct")
        if base_pf is None and row["tier_name"] in baseline_perf:
            base_pf = baseline_perf[row["tier_name"]].get("profit_factor")
        if base_pnl is None and row["tier_name"] in baseline_perf:
            base_pnl = baseline_perf[row["tier_name"]].get("net_pnl_pct")
        if base_dd is None and row["tier_name"] in baseline_perf:
            base_dd = baseline_perf[row["tier_name"]].get("max_drawdown_pct")

        if base_pf is not None:
            d_pf = row["profit_factor"] - float(base_pf)
        if base_pnl is not None:
            d_pnl = row["net_pnl_pct"] - float(base_pnl)
        if base_dd is not None:
            d_dd = row["max_drawdown_pct"] - float(base_dd)

        impact_rows.append({
            "tier_name": row["tier_name"],
            "delta_profit_factor_vs_v24_phase62": d_pf,
            "delta_net_pnl_pct_vs_v24_phase62": d_pnl,
            "delta_max_drawdown_pct_vs_v24_phase62": d_dd,
        })

    report = {
        "phase": "7_ml_trade_quality_filter",
        "baseline_reference": str(baseline_path),
        "scaling_matrix": scaling_matrix,
        "ml_impact_vs_v24_phase62": impact_rows,
    }

    matrix_path = output_dir / "phase7_scaling_matrix.json"
    metrics_path = output_dir / "phase7_ml_metrics.json"
    report_path = output_dir / "phase7_ml_report.json"

    with open(matrix_path, "w", encoding="utf-8") as f:
        json.dump(scaling_matrix, f, indent=2)

    metrics_payload = {
        "avg_ml_score_executed_by_tier": {r["tier_name"]: r["avg_ml_score_executed"] for r in scaling_matrix},
        "avg_ml_score_rejected_by_tier": {r["tier_name"]: r["avg_ml_score_rejected"] for r in scaling_matrix},
        "ml_acceptance_rate_by_tier": {r["tier_name"]: r["ml_acceptance_rate"] for r in scaling_matrix},
        "ml_filtered_trades_by_tier": {r["tier_name"]: r["ml_filtered_trades"] for r in scaling_matrix},
        "ml_fallback_count_by_tier": {r["tier_name"]: r["ml_fallback_count"] for r in scaling_matrix},
    }
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics_payload, f, indent=2)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return {
        "scaling_matrix": str(matrix_path),
        "ml_metrics": str(metrics_path),
        "ml_report": str(report_path),
    }
