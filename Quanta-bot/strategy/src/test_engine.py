"""
test_engine.py — Integration tests for the full Strategy Engine pipeline

Tests:
  - TRENDING_UP state with valid pullback → BUY signal
  - SIDEWAYS state → HOLD
  - SAFE MODE payload → HOLD (data validation failure)
  - No conditions met → HOLD
  - Breakout priority over trend
  - Missing file → HOLD
"""

import json
import pytest

from strategy.src.engine import run_strategy_engine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _write_state(tmp_path, data: dict) -> str:
    path = str(tmp_path / "current_market_state.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def _trending_up_state() -> dict:
    """TRENDING_UP state with all conditions for a Trend Pullback BUY.

    RR calculation:
        entry = 64000, sl = 63800*0.99 = 63162, tp = 64000+(1000*2) = 66000
        RR = (66000-64000)/(64000-63162) = 2000/838 ≈ 2.39 ✓
    """
    return {
        "symbol": "BTCUSDT",
        "timestamp": "2026-04-16T00:00:00+00:00",
        "price": 64000.00,
        "ema_20": 64000.00,
        "ema_50": 63800.00,
        "vwap": 63950.00,
        "rsi": 45.0,
        "adx": 30.0,
        "atr": 1000.00,
        "bb_lower": 63000.00,
        "bb_upper": 65000.00,
        "current_volume": 1200.0,
        "volume_sma_20": 850.0,
        "state": {
            "primary": "TRENDING_UP",
            "volatility": "NORMAL",
        },
        "support_level": 62500.00,
        "resistance_level": 65500.00,
    }


def _sideways_state() -> dict:
    return {
        "symbol": "BTCUSDT",
        "timestamp": "2026-04-16T00:00:00+00:00",
        "price": 64000.00,
        "ema_20": 64000.00,
        "ema_50": 63900.00,
        "vwap": 63950.00,
        "rsi": 50.0,
        "adx": 15.0,
        "atr": 200.00,
        "bb_lower": 63500.00,
        "bb_upper": 64500.00,
        "current_volume": 500.0,
        "volume_sma_20": 600.0,
        "state": {
            "primary": "SIDEWAYS",
            "volatility": "LOW",
        },
        "support_level": 63000.00,
        "resistance_level": 65000.00,
    }


def _safe_mode_payload() -> dict:
    return {
        "symbol": "UNKNOWN",
        "timestamp": "2026-04-16T00:00:00+00:00",
        "price": 0.0,
        "ema_20": 0.0, "ema_50": 0.0, "vwap": 0.0,
        "rsi": 50.0, "adx": 0.0, "atr": 0.0,
        "bb_lower": 0.0, "bb_upper": 0.0,
        "current_volume": 0.0, "volume_sma_20": 0.0,
        "state": {"primary": "SIDEWAYS", "volatility": "LOW"},
        "support_level": 0.0, "resistance_level": 0.0,
    }


def _breakout_state() -> dict:
    """HIGH volatility breakout state."""
    resistance = 65500.0
    return {
        "symbol": "BTCUSDT",
        "timestamp": "2026-04-16T00:00:00+00:00",
        "price": resistance * 1.002,
        "ema_20": 65000.00,
        "ema_50": 64500.00,
        "vwap": 65000.00,
        "rsi": 55.0,
        "adx": 35.0,
        "atr": 600.00,
        "bb_lower": 64000.00,
        "bb_upper": 66000.00,
        "current_volume": 2000.0,
        "volume_sma_20": 850.0,
        "state": {
            "primary": "TRENDING_UP",
            "volatility": "HIGH",
        },
        "support_level": 63000.00,
        "resistance_level": resistance,
    }


# ===========================================================================
# Integration Tests
# ===========================================================================
class TestEngineHappyPath:
    def test_trending_up_produces_buy(self, tmp_path):
        path = _write_state(tmp_path, _trending_up_state())
        result = run_strategy_engine(path)
        assert result["signal"] == "BUY"
        assert result["strategy_used"] == "Trend_Pullback"
        assert result["confidence_score"] > 0
        assert result["suggested_entry"] is not None
        assert result["suggested_sl"] is not None
        assert result["suggested_tp"] is not None

    def test_output_has_all_required_keys(self, tmp_path):
        path = _write_state(tmp_path, _trending_up_state())
        result = run_strategy_engine(path)
        required = [
            "timestamp", "symbol", "signal", "strategy_used",
            "confidence_score", "suggested_entry", "suggested_sl",
            "suggested_tp", "reason",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_breakout_produces_buy(self, tmp_path):
        path = _write_state(tmp_path, _breakout_state())
        result = run_strategy_engine(path)
        assert result["signal"] == "BUY"
        assert result["strategy_used"] == "Breakout"


class TestEngineHoldCases:
    def test_sideways_produces_hold(self, tmp_path):
        path = _write_state(tmp_path, _sideways_state())
        result = run_strategy_engine(path)
        assert result["signal"] == "HOLD"
        assert "unfavorable" in result["reason"].lower() or "SIDEWAYS" in result["reason"]

    def test_safe_mode_payload_produces_hold(self, tmp_path):
        path = _write_state(tmp_path, _safe_mode_payload())
        result = run_strategy_engine(path)
        assert result["signal"] == "HOLD"
        assert "validation" in result["reason"].lower() or "missing" in result["reason"].lower()

    def test_missing_file_produces_hold(self):
        result = run_strategy_engine("/nonexistent/path.json")
        assert result["signal"] == "HOLD"

    def test_no_conditions_met_produces_hold(self, tmp_path):
        """TRENDING_UP but price too far from EMA and no breakout → HOLD."""
        state = _trending_up_state()
        state["price"] = 66000.0  # Far from EMA_20=64000, not beyond resistance
        path = _write_state(tmp_path, state)
        result = run_strategy_engine(path)
        assert result["signal"] == "HOLD"
        assert "No strategy" in result["reason"] or "conditions" in result["reason"].lower()


class TestEngineBreakoutPriority:
    def test_breakout_takes_priority_over_trend(self, tmp_path):
        """If both breakout and trend conditions are met, breakout wins."""
        state = _breakout_state()
        # Also make it near EMA_20 for potential trend signal
        state["ema_20"] = state["price"]
        path = _write_state(tmp_path, state)
        result = run_strategy_engine(path)
        assert result["strategy_used"] == "Breakout"
