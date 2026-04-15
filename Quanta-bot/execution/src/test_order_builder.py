"""
test_order_builder.py — Unit tests for broker order payload building

Tests: correct structure, side mapping, quantity rounding.
"""

import pytest

from execution.src.order_builder import build_order


def _signal(side: str = "BUY") -> dict:
    return {
        "symbol": "BTCUSDT",
        "signal": side,
        "suggested_entry": 64000.0,
        "suggested_sl": 63000.0,
        "suggested_tp": 66000.0,
    }


class TestBuildOrder:
    def test_buy_order_structure(self):
        order = build_order(_signal("BUY"), 0.2, 1.28)
        assert order["symbol"] == "BTCUSDT"
        assert order["side"] == "BUY"
        assert order["type"] == "MARKET"
        assert order["quantity"] == 0.2
        assert order["leverage"] == 1.3  # rounded to 1 decimal
        assert order["reduce_only"] is False
        assert order["oco_sl"] == 63000.0
        assert order["oco_tp"] == 66000.0

    def test_sell_order_side(self):
        order = build_order(_signal("SELL"), 0.5, 3.0)
        assert order["side"] == "SELL"

    def test_quantity_rounding(self):
        order = build_order(_signal(), 0.12345678, 2.5)
        assert order["quantity"] == 0.123  # 3 decimal places

    def test_all_required_keys_present(self):
        order = build_order(_signal(), 1.0, 5.0)
        required = ["symbol", "side", "type", "quantity", "leverage",
                     "reduce_only", "oco_sl", "oco_tp"]
        for key in required:
            assert key in order, f"Missing key: {key}"

    def test_sl_tp_rounding(self):
        sig = _signal()
        sig["suggested_sl"] = 63000.12345
        sig["suggested_tp"] = 66000.98765
        order = build_order(sig, 1.0, 5.0)
        assert order["oco_sl"] == 63000.12
        assert order["oco_tp"] == 66000.99  # rounded to 2 decimals
