from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "runtime" / "config" / "strategy_config.json"
OUTPUT_PATH = ROOT / "research" / "portfolio_backtests" / "v27" / "phase74_verification_summary.md"


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _tier_dir(tier_name: str) -> Path:
    return ROOT / "research" / "portfolio_backtests" / "v27" / f"phase74_{tier_name.lower()}"


def _find_audit_file(tier_name: str) -> Path:
    tier_dir = _tier_dir(tier_name)
    matches = sorted(tier_dir.glob("*phase7_audit.json"))
    if not matches:
        raise FileNotFoundError(f"No phase7 audit file found for {tier_name} in {tier_dir}")
    return matches[0]


def _format_pct(value: float) -> str:
    return f"{value:.2f}%"


def _format_float(value: float) -> str:
    return f"{value:.4f}"


def _render_gate_block(metrics: dict[str, Any]) -> str:
    safe = int(metrics.get("phase74_safe_count", 0))
    warning = int(metrics.get("phase74_warning_count", 0))
    no_trade = int(metrics.get("phase74_no_trade_count", 0))
    total = safe + warning + no_trade

    safe_pct = (safe / total * 100.0) if total else 0.0
    warning_pct = (warning / total * 100.0) if total else 0.0
    no_trade_pct = (no_trade / total * 100.0) if total else 0.0

    return (
        "Gate Distribution (Phase 7.4)\n"
        f"- SAFE: {safe} ({safe_pct:.2f}%)\n"
        f"- WARNING: {warning} ({warning_pct:.2f}%)\n"
        f"- NO_TRADE: {no_trade} ({no_trade_pct:.2f}%)\n"
        f"- Warning penalties: {int(metrics.get('phase74_warning_penalty_count', 0))}\n"
        f"- Trend overrides: {int(metrics.get('phase74_trend_override_count', 0))}\n"
        f"- Avg risk pressure: {_format_float(float(metrics.get('phase74_avg_risk_pressure', 0.0)))}\n"
        f"- Veto share (ML-valid): {_format_pct(float(metrics.get('phase74_veto_share_ml_valid', 0.0)) * 100.0)}\n"
        f"- ML-valid candidates: {int(metrics.get('phase74_ml_valid_candidates', 0))}\n"
    )


def _render_tier_section(tier_name: str) -> str:
    audit_path = _find_audit_file(tier_name)
    metrics = _load_json(audit_path)

    win_rate = float(metrics.get("win_rate", 0.0))
    profit_factor = float(metrics.get("profit_factor", 0.0))
    net_pnl_pct = float(metrics.get("net_pnl_pct", 0.0))
    max_dd = float(metrics.get("max_drawdown_pct", 0.0))

    dd_ok = "PASS" if max_dd > -5.0 else "FAIL"

    section = [
        f"## {tier_name} Results",
        "",
        f"Audit source: {audit_path}",
        "",
        "Core KPIs",
        f"- Win rate: {_format_pct(win_rate)}",
        f"- Profit factor: {_format_float(profit_factor)}",
        f"- Net PnL %: {_format_pct(net_pnl_pct)}",
        f"- Max drawdown %: {_format_pct(max_dd)} ({dd_ok} vs -5.00%)",
        "",
        _render_gate_block(metrics).rstrip(),
        "",
        "Phase 7.4 reason breakdown",
        "- " + "\n- ".join(
            f"{k}: {v}" for k, v in metrics.get("phase74_reason_breakdown", {}).items()
        ),
        "",
    ]
    return "\n".join(section)


def main() -> None:
    config = _load_json(CONFIG_PATH)
    watchlist = config.get("matrix", {}).get("watchlist", [])
    intervals = config.get("matrix", {}).get("intervals", [])

    header = [
        "# Phase 7.4 Verification Summary — Strict Baseline Re-Run",
        "",
        "## Scope",
        f"- Watchlist size: {len(watchlist)}",
        f"- Intervals: {', '.join(intervals)}",
        "- Gate objective: Deterministic Market Regime Gate classification to keep Max DD < 5.0%",
        "",
    ]

    body = [
        _render_tier_section("TIER_10K"),
        _render_tier_section("TIER_100K"),
    ]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(header + body), encoding="utf-8")
    print(f"[SUMMARY] Wrote verification summary -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
