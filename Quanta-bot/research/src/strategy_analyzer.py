"""
strategy_analyzer.py — Per-Strategy Performance & Degradation Flags

Single public function: analyze_strategies()
Groups trades by strategy, computes per-strategy metrics, flags underperformers.
Pure function — no file I/O.
"""

from . import config


def analyze_strategies(trades: list[dict]) -> list[dict]:
    """Analyze per-strategy performance and flag degradation.

    Parameters
    ----------
    trades : list[dict]
        List of trade dicts with keys: strategy_used, pnl_usd.

    Returns
    -------
    list[dict]
        List of {"strategy_name", "win_rate", "net_pnl", "status"} dicts.
    """
    if not trades:
        return []

    # Group trades by strategy
    groups: dict[str, list[dict]] = {}
    for trade in trades:
        strategy: str = trade.get("strategy_used", "Unknown")
        if strategy not in groups:
            groups[strategy] = []
        groups[strategy].append(trade)

    results: list[dict] = []
    for strategy_name, strat_trades in sorted(groups.items()):
        total: int = len(strat_trades)
        wins: int = sum(1 for t in strat_trades if t.get("pnl_usd", 0.0) > 0)
        net_pnl: float = sum(t.get("pnl_usd", 0.0) for t in strat_trades)

        win_rate: float = (wins / total) * 100.0 if total > 0 else 0.0
        win_rate = round(win_rate, 2)
        net_pnl = round(net_pnl, 2)

        # Degradation check per research-context.md §3 Step 3
        if win_rate < config.STRATEGY_UNDERPERFORM_WIN_RATE and net_pnl < 0:
            status: str = "UNDERPERFORMING"
        else:
            status = "OPTIMAL"

        results.append({
            "strategy_name": strategy_name,
            "win_rate": win_rate,
            "net_pnl": net_pnl,
            "status": status,
        })

    return results
