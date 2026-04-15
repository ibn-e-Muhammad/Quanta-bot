"""
test_snapshot_writer.py — Unit tests for atomic JSON snapshot writer

Tests: atomic write, validation, zero-state fallback.
"""

import json
import os
import pytest

from research.src.snapshot_writer import write_snapshot, build_zero_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _valid_snapshot() -> dict:
    return {
        "timestamp": "2026-04-16T00:00:00+00:00",
        "total_trades": 10,
        "global_win_rate": 60.0,
        "average_rr": 1.85,
        "current_drawdown_pct": 2.0,
        "strategy_performance": [
            {"strategy_name": "Trend_Pullback", "win_rate": 65.0, "net_pnl": 300.0, "status": "OPTIMAL"},
        ],
    }


# ===========================================================================
# Write Tests
# ===========================================================================
class TestWriteSnapshot:
    def test_creates_file(self, tmp_path):
        path = str(tmp_path / "snapshot.json")
        write_snapshot(_valid_snapshot(), path)
        assert os.path.exists(path)

    def test_correct_content(self, tmp_path):
        path = str(tmp_path / "snapshot.json")
        snap = _valid_snapshot()
        write_snapshot(snap, path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["total_trades"] == 10
        assert loaded["global_win_rate"] == 60.0

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "snapshot.json")
        write_snapshot(_valid_snapshot(), path)
        assert os.path.exists(path)

    def test_overwrites_existing(self, tmp_path):
        path = str(tmp_path / "snapshot.json")
        write_snapshot(_valid_snapshot(), path)
        # Write again with different data
        snap2 = _valid_snapshot()
        snap2["total_trades"] = 20
        write_snapshot(snap2, path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["total_trades"] == 20


# ===========================================================================
# Validation & Fallback
# ===========================================================================
class TestValidation:
    def test_invalid_win_rate_writes_zero_state(self, tmp_path):
        path = str(tmp_path / "snapshot.json")
        snap = _valid_snapshot()
        snap["global_win_rate"] = 150.0  # Invalid: > 100
        write_snapshot(snap, path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["total_trades"] == 0
        assert loaded["global_win_rate"] == 0.0

    def test_negative_drawdown_writes_zero_state(self, tmp_path):
        path = str(tmp_path / "snapshot.json")
        snap = _valid_snapshot()
        snap["current_drawdown_pct"] = -5.0
        write_snapshot(snap, path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["total_trades"] == 0

    def test_missing_key_writes_zero_state(self, tmp_path):
        path = str(tmp_path / "snapshot.json")
        snap = _valid_snapshot()
        del snap["total_trades"]
        write_snapshot(snap, path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["total_trades"] == 0


# ===========================================================================
# Zero State
# ===========================================================================
class TestZeroState:
    def test_zero_state_structure(self):
        zero = build_zero_state()
        assert zero["total_trades"] == 0
        assert zero["global_win_rate"] == 0.0
        assert zero["average_rr"] == 0.0
        assert zero["current_drawdown_pct"] == 0.0
        assert zero["strategy_performance"] == []
        assert "timestamp" in zero
