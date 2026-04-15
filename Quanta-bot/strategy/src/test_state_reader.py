"""
test_state_reader.py — Unit tests for market state JSON reading & validation

Tests: valid JSON parsing, missing keys, NaN values, invalid enums, bad price.
"""

import json
import math
import os
import pytest

from strategy.src.state_reader import read_market_state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def valid_state_data() -> dict:
    """A valid market state dict matching Room 1 output schema."""
    return {
        "symbol": "BTCUSDT",
        "timestamp": "2026-04-16T00:00:00+00:00",
        "price": 64000.50,
        "ema_20": 63800.00,
        "ema_50": 63500.00,
        "vwap": 63950.00,
        "rsi": 45.5,
        "adx": 28.5,
        "atr": 450.00,
        "bb_lower": 63000.00,
        "bb_upper": 65000.00,
        "current_volume": 1250.5,
        "volume_sma_20": 850.0,
        "state": {
            "primary": "TRENDING_UP",
            "volatility": "HIGH",
        },
        "support_level": 62500.00,
        "resistance_level": 65500.00,
    }


def _write_state(tmp_path, data: dict) -> str:
    path = str(tmp_path / "state.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


# ===========================================================================
# Happy Path
# ===========================================================================
class TestReadMarketStateValid:
    def test_valid_json_returns_dict(self, tmp_path, valid_state_data):
        path = _write_state(tmp_path, valid_state_data)
        result = read_market_state(path)
        assert result is not None
        assert result["symbol"] == "BTCUSDT"
        assert result["price"] == 64000.50

    def test_all_keys_preserved(self, tmp_path, valid_state_data):
        path = _write_state(tmp_path, valid_state_data)
        result = read_market_state(path)
        assert result is not None
        for key in valid_state_data:
            assert key in result


# ===========================================================================
# File Errors
# ===========================================================================
class TestReadMarketStateFileErrors:
    def test_missing_file_returns_none(self):
        result = read_market_state("/nonexistent/path.json")
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("{invalid json")
        result = read_market_state(path)
        assert result is None


# ===========================================================================
# Validation Failures
# ===========================================================================
class TestReadMarketStateValidation:
    def test_missing_key_returns_none(self, tmp_path, valid_state_data):
        del valid_state_data["price"]
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_missing_state_key_returns_none(self, tmp_path, valid_state_data):
        del valid_state_data["state"]
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_none_numeric_value_returns_none(self, tmp_path, valid_state_data):
        valid_state_data["rsi"] = None
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_nan_value_returns_none(self, tmp_path, valid_state_data):
        valid_state_data["atr"] = float("nan")
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_inf_value_returns_none(self, tmp_path, valid_state_data):
        valid_state_data["ema_20"] = float("inf")
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_zero_price_returns_none(self, tmp_path, valid_state_data):
        valid_state_data["price"] = 0.0
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_negative_price_returns_none(self, tmp_path, valid_state_data):
        valid_state_data["price"] = -100.0
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_invalid_primary_state_returns_none(self, tmp_path, valid_state_data):
        valid_state_data["state"]["primary"] = "BULLISH"
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_invalid_volatility_returns_none(self, tmp_path, valid_state_data):
        valid_state_data["state"]["volatility"] = "EXTREME"
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_string_numeric_value_returns_none(self, tmp_path, valid_state_data):
        valid_state_data["adx"] = "twenty"
        path = _write_state(tmp_path, valid_state_data)
        assert read_market_state(path) is None

    def test_safe_mode_payload_returns_none(self, tmp_path):
        """Room 1 SAFE MODE payload has price=0 → should return None."""
        safe = {
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
        path = _write_state(tmp_path, safe)
        assert read_market_state(path) is None
