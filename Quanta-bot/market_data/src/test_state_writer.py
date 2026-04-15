"""
test_state_writer.py — Unit tests for atomic write + validation + SAFE MODE

Tests:
  - validate_state pass / fail cases
  - Atomic write creates valid JSON
  - Invalid state triggers SAFE MODE fallback
  - SAFE MODE payload correctness
"""

import json
import math
import os
import tempfile
import pytest

from market_data.src.state_writer import (
    validate_state,
    write_state,
    build_safe_mode_payload,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def valid_state():
    """A minimal valid state dict."""
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


@pytest.fixture
def output_path(tmp_path):
    return str(tmp_path / "current_market_state.json")


# ===========================================================================
# validate_state Tests
# ===========================================================================
class TestValidateState:
    def test_valid_state_passes(self, valid_state):
        assert validate_state(valid_state) is True

    def test_zero_price_fails(self, valid_state):
        valid_state["price"] = 0.0
        assert validate_state(valid_state) is False

    def test_negative_price_fails(self, valid_state):
        valid_state["price"] = -1.0
        assert validate_state(valid_state) is False

    def test_nan_value_fails(self, valid_state):
        valid_state["rsi"] = float("nan")
        assert validate_state(valid_state) is False

    def test_inf_value_fails(self, valid_state):
        valid_state["atr"] = float("inf")
        assert validate_state(valid_state) is False

    def test_none_value_fails(self, valid_state):
        valid_state["ema_20"] = None
        assert validate_state(valid_state) is False

    def test_invalid_primary_state_fails(self, valid_state):
        valid_state["state"]["primary"] = "BULLISH"
        assert validate_state(valid_state) is False

    def test_invalid_volatility_fails(self, valid_state):
        valid_state["state"]["volatility"] = "EXTREME"
        assert validate_state(valid_state) is False

    def test_missing_state_key_fails(self, valid_state):
        del valid_state["state"]
        assert validate_state(valid_state) is False

    def test_all_valid_primary_values(self, valid_state):
        for ps in ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "SIDEWAYS"]:
            valid_state["state"]["primary"] = ps
            assert validate_state(valid_state) is True

    def test_all_valid_volatility_values(self, valid_state):
        for vs in ["HIGH", "NORMAL", "LOW"]:
            valid_state["state"]["volatility"] = vs
            assert validate_state(valid_state) is True


# ===========================================================================
# write_state Tests
# ===========================================================================
class TestWriteState:
    def test_writes_valid_json(self, valid_state, output_path):
        write_state(valid_state, output_path)
        assert os.path.exists(output_path)
        with open(output_path) as f:
            data = json.load(f)
        assert data["price"] == 64000.50
        assert data["state"]["primary"] == "TRENDING_UP"

    def test_invalid_state_triggers_safe_mode(self, valid_state, output_path):
        valid_state["price"] = -1.0  # Invalid
        write_state(valid_state, output_path)
        with open(output_path) as f:
            data = json.load(f)
        # Should be a SAFE MODE payload
        assert data["symbol"] == "UNKNOWN"
        assert data["price"] == 0.0
        assert data["state"]["primary"] == "SIDEWAYS"
        assert data["state"]["volatility"] == "LOW"

    def test_atomic_no_partial_write(self, valid_state, output_path):
        """File should either be fully written or not exist."""
        write_state(valid_state, output_path)
        with open(output_path) as f:
            content = f.read()
        # Should be valid JSON — no truncation
        data = json.loads(content)
        assert "symbol" in data

    def test_creates_parent_directory(self, valid_state, tmp_path):
        nested_path = str(tmp_path / "nested" / "dir" / "state.json")
        write_state(valid_state, nested_path)
        assert os.path.exists(nested_path)


# ===========================================================================
# SAFE MODE Payload Tests
# ===========================================================================
class TestSafeModePayload:
    def test_payload_structure(self):
        safe = build_safe_mode_payload()
        assert safe["symbol"] == "UNKNOWN"
        assert safe["price"] == 0.0
        assert safe["rsi"] == 50.0
        assert safe["state"]["primary"] == "SIDEWAYS"
        assert safe["state"]["volatility"] == "LOW"

    def test_payload_has_all_required_keys(self):
        safe = build_safe_mode_payload()
        required_keys = [
            "symbol", "timestamp", "price", "ema_20", "ema_50", "vwap",
            "rsi", "adx", "atr", "bb_lower", "bb_upper", "current_volume",
            "volume_sma_20", "state", "support_level", "resistance_level",
        ]
        for key in required_keys:
            assert key in safe, f"Missing key: {key}"

    def test_payload_is_valid_json(self):
        safe = build_safe_mode_payload()
        json_str = json.dumps(safe)
        reparsed = json.loads(json_str)
        assert reparsed == safe

    def test_payload_has_iso_timestamp(self):
        safe = build_safe_mode_payload()
        assert "T" in safe["timestamp"]  # Basic ISO8601 check
