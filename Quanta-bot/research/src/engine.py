"""
engine.py — Room 4 Orchestrator

Single public function: run_research_engine()
Pipeline: Extract → Compute → Analyze → Snapshot

Read-only. No trade execution. No network calls.
"""

import sys
from datetime import datetime, timezone

from .data_extractor import extract_trades
from .metrics import compute_win_rate, compute_average_rr, compute_drawdown_pct
from .strategy_analyzer import analyze_strategies
from .snapshot_writer import write_snapshot, build_zero_state


def run_research_engine(
    db_path: str | None = None,
    snapshot_path: str | None = None,
) -> dict:
    """Execute the full Research Lab pipeline once.

    Parameters
    ----------
    db_path : str | None
        Override SQLite path (for testing).
    snapshot_path : str | None
        Override snapshot output path (for testing).

    Returns
    -------
    dict
        Performance snapshot dict.
    """
    try:
        # ---- Step 1: Extract trades ----
        trades: list[dict] = extract_trades(db_path)

        if not trades:
            snapshot: dict = build_zero_state()
            write_snapshot(snapshot, snapshot_path)
            return snapshot

        # ---- Step 2: Compute metrics ----
        win_rate: float = compute_win_rate(trades)
        avg_rr: float = compute_average_rr(trades)
        drawdown: float = compute_drawdown_pct(trades)

        # ---- Step 3: Analyze strategies ----
        strategy_perf: list[dict] = analyze_strategies(trades)

        # ---- Step 4: Assemble snapshot ----
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_trades": len(trades),
            "global_win_rate": win_rate,
            "average_rr": avg_rr,
            "current_drawdown_pct": drawdown,
            "strategy_performance": strategy_perf,
        }

        # ---- Step 5: Write snapshot ----
        write_snapshot(snapshot, snapshot_path)

        return snapshot

    except Exception as exc:
        print(f"[RL] Error — falling back to zero state: {exc}", file=sys.stderr)
        snapshot = build_zero_state()
        try:
            write_snapshot(snapshot, snapshot_path)
        except Exception:
            pass
        return snapshot
