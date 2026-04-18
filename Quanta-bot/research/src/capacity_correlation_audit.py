import json
from pathlib import Path


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
