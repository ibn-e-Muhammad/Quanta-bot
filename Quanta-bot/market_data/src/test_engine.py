"""
test_engine.py — Integration test for the full MSE pipeline

Tests:
  - Happy path with mocked Binance response → valid state written
  - API failure → SAFE MODE payload written
  - Data integrity failure → SAFE MODE payload written

ZERO live API calls — all Binance responses are mocked.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

import numpy as np

from market_data.src.engine import run_market_engine
from market_data.src.binance_client import SafeModeError, DataIntegrityError
from market_data.src.state_writer import build_safe_mode_payload
from market_data.src import config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_klines():
    """Generate 200 realistic mock kline dicts."""
    np.random.seed(123)
    klines = []
    base_price = 64000.0
    base_time = 1700000000000.0  # Some epoch ms

    for i in range(200):
        close = base_price + i * 10 + np.random.uniform(-50, 50)
        high = close + np.random.uniform(10, 100)
        low = close - np.random.uniform(10, 100)
        open_ = close + np.random.uniform(-30, 30)
        volume = np.random.uniform(50, 500)
        klines.append({
            "open_time": base_time + i * 3600000,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
    return klines


@pytest.fixture
def output_path(tmp_path):
    return tmp_path / "current_market_state.json"


# ===========================================================================
# Happy Path
# ===========================================================================
class TestEngineHappyPath:
    @patch("market_data.src.engine.fetch_klines")
    @patch("market_data.src.engine.config")
    def test_full_pipeline_produces_valid_state(
        self, mock_config, mock_fetch, mock_klines, tmp_path
    ):
        """Full pipeline with mocked data produces a valid state file."""
        mock_fetch.return_value = mock_klines
        mock_config.SYMBOL = "BTCUSDT"
        mock_config.INTERVAL = "1h"
        mock_config.CANDLE_LIMIT = 200
        state_path = tmp_path / "current_market_state.json"
        mock_config.STATE_FILE_PATH = state_path

        run_market_engine()

        assert state_path.exists()
        with open(state_path) as f:
            data = json.load(f)

        # Validate schema
        assert data["symbol"] == "BTCUSDT"
        assert data["price"] > 0
        assert data["state"]["primary"] in {
            "TRENDING_UP", "TRENDING_DOWN", "RANGING", "SIDEWAYS"
        }
        assert data["state"]["volatility"] in {"HIGH", "NORMAL", "LOW"}
        assert "timestamp" in data
        assert data["ema_20"] > 0
        assert data["ema_50"] > 0
        assert 0 <= data["rsi"] <= 100

    @patch("market_data.src.engine.fetch_klines")
    @patch("market_data.src.engine.config")
    def test_all_output_keys_present(
        self, mock_config, mock_fetch, mock_klines, tmp_path
    ):
        mock_fetch.return_value = mock_klines
        mock_config.SYMBOL = "BTCUSDT"
        mock_config.INTERVAL = "1h"
        mock_config.CANDLE_LIMIT = 200
        state_path = tmp_path / "current_market_state.json"
        mock_config.STATE_FILE_PATH = state_path

        run_market_engine()

        with open(state_path) as f:
            data = json.load(f)

        required_keys = [
            "symbol", "timestamp", "price", "ema_20", "ema_50", "vwap",
            "rsi", "adx", "atr", "bb_lower", "bb_upper", "current_volume",
            "volume_sma_20", "state", "support_level", "resistance_level",
        ]
        for key in required_keys:
            assert key in data, f"Missing output key: {key}"


# ===========================================================================
# Failure → SAFE MODE
# ===========================================================================
class TestEngineSafeMode:
    @patch("market_data.src.engine.fetch_klines")
    @patch("market_data.src.engine.config")
    def test_safe_mode_on_api_failure(
        self, mock_config, mock_fetch, tmp_path
    ):
        """SafeModeError from fetch_klines → SAFE MODE payload."""
        mock_fetch.side_effect = SafeModeError("Rate limited")
        mock_config.SYMBOL = "BTCUSDT"
        mock_config.INTERVAL = "1h"
        mock_config.CANDLE_LIMIT = 200
        state_path = tmp_path / "current_market_state.json"
        mock_config.STATE_FILE_PATH = state_path

        run_market_engine()

        assert state_path.exists()
        with open(state_path) as f:
            data = json.load(f)

        assert data["symbol"] == "UNKNOWN"
        assert data["price"] == 0.0
        assert data["state"]["primary"] == "SIDEWAYS"
        assert data["state"]["volatility"] == "LOW"

    @patch("market_data.src.engine.fetch_klines")
    @patch("market_data.src.engine.config")
    def test_safe_mode_on_data_integrity_error(
        self, mock_config, mock_fetch, tmp_path
    ):
        """DataIntegrityError → SAFE MODE payload."""
        mock_fetch.side_effect = DataIntegrityError("Missing timestamps")
        mock_config.SYMBOL = "BTCUSDT"
        mock_config.INTERVAL = "1h"
        mock_config.CANDLE_LIMIT = 200
        state_path = tmp_path / "current_market_state.json"
        mock_config.STATE_FILE_PATH = state_path

        run_market_engine()

        with open(state_path) as f:
            data = json.load(f)

        assert data["symbol"] == "UNKNOWN"
        assert data["price"] == 0.0

    @patch("market_data.src.engine.fetch_klines")
    @patch("market_data.src.engine.config")
    def test_safe_mode_on_unexpected_exception(
        self, mock_config, mock_fetch, tmp_path
    ):
        """Any unexpected exception → SAFE MODE payload."""
        mock_fetch.side_effect = RuntimeError("Network down")
        mock_config.SYMBOL = "BTCUSDT"
        mock_config.INTERVAL = "1h"
        mock_config.CANDLE_LIMIT = 200
        state_path = tmp_path / "current_market_state.json"
        mock_config.STATE_FILE_PATH = state_path

        run_market_engine()

        with open(state_path) as f:
            data = json.load(f)

        assert data["symbol"] == "UNKNOWN"
        assert data["price"] == 0.0
        assert data["state"]["primary"] == "SIDEWAYS"
