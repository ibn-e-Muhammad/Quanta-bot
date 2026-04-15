"""
metrics.py — Pure Metric Computation Functions

Three public functions: compute_win_rate, compute_average_rr, compute_drawdown_pct.
All pure functions — no file I/O, no side effects.
"""


def compute_win_rate(trades: list[dict]) -> float:
    """Compute global win rate as a percentage.

    Returns (winning_trades / total_trades) * 100, clamped to [0, 100].
    Returns 0.0 if no trades.
    """
    total: int = len(trades)
    if total == 0:
        return 0.0

    winning: int = sum(1 for t in trades if t.get("pnl_usd", 0.0) > 0)
    rate: float = (winning / total) * 100.0
    return max(0.0, min(100.0, round(rate, 2)))


def compute_average_rr(trades: list[dict]) -> float:
    """Compute average risk/reward ratio from realized PnL.

    RR = mean(winning PnL) / mean(abs(losing PnL)).
    Returns 0.0 if no wins or no losses.
    """
    wins: list[float] = [t["pnl_usd"] for t in trades if t.get("pnl_usd", 0.0) > 0]
    losses: list[float] = [t["pnl_usd"] for t in trades if t.get("pnl_usd", 0.0) <= 0]

    if not wins or not losses:
        return 0.0

    avg_win: float = sum(wins) / len(wins)
    avg_loss: float = sum(abs(l) for l in losses) / len(losses)

    if avg_loss == 0:
        return 0.0

    return round(avg_win / avg_loss, 2)


def compute_drawdown_pct(trades: list[dict]) -> float:
    """Compute current drawdown percentage from cumulative equity curve.

    Walks trades chronologically, tracking peak equity.
    drawdown = ((peak - current) / peak) * 100
    Returns 0.0 if no trades or peak is zero.
    """
    if not trades:
        return 0.0

    equity: float = 0.0
    peak: float = 0.0

    for trade in trades:
        pnl: float = trade.get("pnl_usd", 0.0)
        equity += pnl
        if equity > peak:
            peak = equity

    if peak <= 0:
        return 0.0

    drawdown: float = ((peak - equity) / peak) * 100.0
    return max(0.0, round(drawdown, 2))
