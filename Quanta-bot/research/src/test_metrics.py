"""
test_metrics.py — Unit tests for metric computation functions

Tests: win rate, average RR, drawdown, zero-trade edge cases.
"""

import pytest

from research.src.metrics import compute_win_rate, compute_average_rr, compute_drawdown_pct


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _trades(*pnls: float) -> list[dict]:
    """Build a list of trade dicts from PnL values."""
    return [{"pnl_usd": pnl} for pnl in pnls]


# ===========================================================================
# Win Rate
# ===========================================================================
class TestComputeWinRate:
    def test_all_wins(self):
        assert compute_win_rate(_trades(100, 50, 200)) == 100.0

    def test_all_losses(self):
        assert compute_win_rate(_trades(-100, -50, -200)) == 0.0

    def test_mixed(self):
        # 3 out of 5 winning
        assert compute_win_rate(_trades(100, -50, 200, -30, 10)) == 60.0

    def test_empty(self):
        assert compute_win_rate([]) == 0.0

    def test_zero_pnl_counts_as_loss(self):
        # 0.0 is NOT a win
        assert compute_win_rate(_trades(0.0, 100.0)) == 50.0

    def test_single_win(self):
        assert compute_win_rate(_trades(10.0)) == 100.0

    def test_single_loss(self):
        assert compute_win_rate(_trades(-10.0)) == 0.0


# ===========================================================================
# Average RR
# ===========================================================================
class TestComputeAverageRR:
    def test_basic(self):
        # avg_win = 150, avg_loss = 50 → RR = 3.0
        result = compute_average_rr(_trades(100, 200, -50, -50))
        assert result == 3.0

    def test_equal_wins_and_losses(self):
        # avg_win = 100, avg_loss = 100 → RR = 1.0
        result = compute_average_rr(_trades(100, -100))
        assert result == 1.0

    def test_no_wins(self):
        assert compute_average_rr(_trades(-100, -200)) == 0.0

    def test_no_losses(self):
        assert compute_average_rr(_trades(100, 200)) == 0.0

    def test_empty(self):
        assert compute_average_rr([]) == 0.0


# ===========================================================================
# Drawdown
# ===========================================================================
class TestComputeDrawdownPct:
    def test_no_drawdown(self):
        # Equity: 100, 200, 300 → peak=300, current=300, dd=0%
        assert compute_drawdown_pct(_trades(100, 100, 100)) == 0.0

    def test_basic_drawdown(self):
        # Equity: 100, 200, 150 → peak=200, current=150
        # dd = ((200-150)/200)*100 = 25.0%
        result = compute_drawdown_pct(_trades(100, 100, -50))
        assert result == 25.0

    def test_full_drawdown(self):
        # Equity: 100, 0 → peak=100, current=0
        # dd = 100%
        result = compute_drawdown_pct(_trades(100, -100))
        assert result == 100.0

    def test_only_losses(self):
        # Equity: -50, -100 → peak never above 0 → dd=0.0
        result = compute_drawdown_pct(_trades(-50, -50))
        assert result == 0.0

    def test_empty(self):
        assert compute_drawdown_pct([]) == 0.0

    def test_recovery_reduces_drawdown(self):
        # Equity: 100, 50, 80 → peak=100, current=80
        # dd = ((100-80)/100)*100 = 20.0%
        result = compute_drawdown_pct(_trades(100, -50, 30))
        assert result == 20.0
