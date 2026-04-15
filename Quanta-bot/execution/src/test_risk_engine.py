"""
test_risk_engine.py — Unit tests for RR validation, position sizing, leverage

Tests: BUY/SELL ordering, RR boundaries, position math, leverage capping.
"""

import pytest

from execution.src.risk_engine import validate_rr, compute_position_size, enforce_leverage


# ===========================================================================
# RR Validation
# ===========================================================================
class TestValidateRR:
    def test_valid_buy(self):
        # entry=64000, sl=63000, tp=66000 → RR=2.0
        valid, rr, reason = validate_rr(64000, 63000, 66000, "BUY")
        assert valid is True
        assert rr == 2.0
        assert reason == ""

    def test_valid_sell(self):
        # entry=64000, sl=65000, tp=62000 → RR=2.0
        valid, rr, reason = validate_rr(64000, 65000, 62000, "SELL")
        assert valid is True
        assert rr == 2.0

    def test_buy_rr_exactly_1_5(self):
        # entry=64000, sl=63000, tp=65500 → RR=1.5
        valid, rr, _ = validate_rr(64000, 63000, 65500, "BUY")
        assert valid is True
        assert rr == 1.5

    def test_buy_rr_below_1_5_rejects(self):
        # entry=64000, sl=63000, tp=65000 → RR=1.0
        valid, rr, reason = validate_rr(64000, 63000, 65000, "BUY")
        assert valid is False
        assert "RR ratio" in reason

    def test_buy_sl_above_entry_rejects(self):
        valid, _, reason = validate_rr(64000, 65000, 66000, "BUY")
        assert valid is False
        assert "ordering violated" in reason

    def test_sell_sl_below_entry_rejects(self):
        valid, _, reason = validate_rr(64000, 63000, 62000, "SELL")
        assert valid is False
        assert "ordering violated" in reason

    def test_zero_sl_distance_rejects(self):
        valid, _, reason = validate_rr(64000, 64000, 66000, "BUY")
        assert valid is False
        assert "ordering violated" in reason

    def test_invalid_signal_type(self):
        valid, _, reason = validate_rr(64000, 63000, 66000, "HOLD")
        assert valid is False
        assert "Invalid signal" in reason


# ===========================================================================
# Position Sizing
# ===========================================================================
class TestComputePositionSize:
    def test_basic_calculation(self):
        # balance=10000, entry=64000, sl=63000, losses=0
        # risk_pct=0.02, risk_usd=200, sl_dist=1000
        # size = 200/1000 = 0.2
        size, risk_usd, risk_pct = compute_position_size(10000, 64000, 63000, 0)
        assert size == 0.2
        assert risk_usd == 200.0
        assert risk_pct == 0.02

    def test_reduced_risk_after_2_losses(self):
        # consecutive_losses=2 → risk_pct=0.01
        size, risk_usd, risk_pct = compute_position_size(10000, 64000, 63000, 2)
        assert risk_pct == 0.01
        assert size == 0.1   # 100/1000
        assert risk_usd == 100.0

    def test_halt_at_3_losses(self):
        size, risk_usd, risk_pct = compute_position_size(10000, 64000, 63000, 3)
        assert size == 0.0
        assert risk_usd == 0.0

    def test_zero_sl_distance(self):
        size, _, _ = compute_position_size(10000, 64000, 64000, 0)
        assert size == 0.0

    def test_sell_position_sizing(self):
        # entry=64000, sl=65000 → sl_dist=1000 (same math)
        size, risk_usd, _ = compute_position_size(10000, 64000, 65000, 0)
        assert size == 0.2
        assert risk_usd == 200.0


# ===========================================================================
# Leverage Enforcement
# ===========================================================================
class TestEnforceLeverage:
    def test_within_limit(self):
        # size=0.2, entry=64000, balance=10000 → notional=12800, lev=1.28
        size, lev = enforce_leverage(0.2, 64000, 10000)
        assert size == 0.2
        assert lev == 1.28

    def test_at_limit(self):
        # size=1.5625, entry=64000, balance=10000 → notional=100000, lev=10.0
        size, lev = enforce_leverage(1.5625, 64000, 10000)
        assert lev == 10.0
        assert size == 1.5625

    def test_above_limit_caps(self):
        # size=2.0, entry=64000, balance=10000 → notional=128000, lev=12.8 → cap
        size, lev = enforce_leverage(2.0, 64000, 10000)
        assert lev == 10.0
        # capped: size = (10000*10)/64000 = 1.5625
        assert size == 1.562  # rounded to 3 decimals (banker's rounding)

    def test_zero_balance(self):
        size, lev = enforce_leverage(0.2, 64000, 0)
        assert size == 0.0
        assert lev == 0.0

    def test_zero_entry(self):
        size, lev = enforce_leverage(0.2, 0, 10000)
        assert size == 0.0
