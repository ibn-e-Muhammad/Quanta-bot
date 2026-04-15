"""
test_strategy_analyzer.py — Unit tests for strategy degradation analysis

Tests: multi-strategy grouping, OPTIMAL/UNDERPERFORMING flags, edge cases.
"""

import pytest

from research.src.strategy_analyzer import analyze_strategies


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _trade(strategy: str, pnl: float) -> dict:
    return {"strategy_used": strategy, "pnl_usd": pnl}


# ===========================================================================
# Multi-Strategy
# ===========================================================================
class TestMultiStrategy:
    def test_two_strategies(self):
        trades = [
            _trade("Trend_Pullback", 100),
            _trade("Trend_Pullback", 50),
            _trade("Range", -30),
            _trade("Range", -20),
        ]
        result = analyze_strategies(trades)
        assert len(result) == 2

        # Sorted alphabetically: Range first, then Trend_Pullback
        assert result[0]["strategy_name"] == "Range"
        assert result[1]["strategy_name"] == "Trend_Pullback"

    def test_optimal_strategy(self):
        trades = [
            _trade("Trend_Pullback", 100),
            _trade("Trend_Pullback", 50),
            _trade("Trend_Pullback", -20),
        ]
        result = analyze_strategies(trades)
        assert len(result) == 1
        # win_rate = 2/3 * 100 = 66.67%, net_pnl = 130 → OPTIMAL
        assert result[0]["status"] == "OPTIMAL"
        assert result[0]["win_rate"] == 66.67
        assert result[0]["net_pnl"] == 130.0

    def test_underperforming_strategy(self):
        trades = [
            _trade("Range", -30),
            _trade("Range", -20),
            _trade("Range", 10),
            _trade("Range", -15),
            _trade("Range", -10),
        ]
        result = analyze_strategies(trades)
        assert len(result) == 1
        # win_rate = 1/5 * 100 = 20.0%, net_pnl = -65 → UNDERPERFORMING
        assert result[0]["status"] == "UNDERPERFORMING"
        assert result[0]["win_rate"] == 20.0

    def test_low_winrate_but_positive_pnl_is_optimal(self):
        """If win_rate < 40% but net_pnl >= 0, status is OPTIMAL."""
        trades = [
            _trade("Breakout", 500),  # 1 big win
            _trade("Breakout", -50),
            _trade("Breakout", -50),
            _trade("Breakout", -50),
        ]
        result = analyze_strategies(trades)
        # win_rate = 25%, net_pnl = 350 → net_pnl >= 0 → OPTIMAL
        assert result[0]["status"] == "OPTIMAL"


# ===========================================================================
# Edge Cases
# ===========================================================================
class TestEdgeCases:
    def test_empty_trades(self):
        assert analyze_strategies([]) == []

    def test_single_trade(self):
        trades = [_trade("Trend_Pullback", 100)]
        result = analyze_strategies(trades)
        assert len(result) == 1
        assert result[0]["win_rate"] == 100.0
        assert result[0]["status"] == "OPTIMAL"

    def test_missing_strategy_key(self):
        trades = [{"pnl_usd": 100}]
        result = analyze_strategies(trades)
        # Defaults to "Unknown"
        assert result[0]["strategy_name"] == "Unknown"
