"""
risk_engine.py — RR Validation, Position Sizing & Leverage Enforcement

Three public functions: validate_rr, compute_position_size, enforce_leverage.
All pure functions — no side effects. Uses only arithmetic.
"""

from . import config


def validate_rr(
    entry: float, sl: float, tp: float, signal: str,
) -> tuple[bool, float, str]:
    """Validate risk/reward ratio for a trade.

    Returns
    -------
    tuple[bool, float, str]
        (valid, rr_ratio, reason).
    """
    # SL/TP ordering check
    if signal == "BUY":
        if not (sl < entry < tp):
            return (
                False, 0.0,
                f"BUY ordering violated: SL({sl}) < ENTRY({entry}) < TP({tp})",
            )
        risk_dist: float = entry - sl
        reward_dist: float = tp - entry

    elif signal == "SELL":
        if not (tp < entry < sl):
            return (
                False, 0.0,
                f"SELL ordering violated: TP({tp}) < ENTRY({entry}) < SL({sl})",
            )
        risk_dist = sl - entry
        reward_dist = entry - tp

    else:
        return False, 0.0, f"Invalid signal type for RR validation: {signal}"

    # Division by zero guard
    if risk_dist == 0:
        return False, 0.0, "SL distance is zero — cannot compute RR"

    rr: float = reward_dist / risk_dist

    if rr < config.MIN_RR_RATIO:
        return (
            False, round(rr, 2),
            f"RR ratio {rr:.2f} below minimum {config.MIN_RR_RATIO}",
        )

    return True, round(rr, 2), ""


def compute_position_size(
    balance: float,
    entry: float,
    sl: float,
    consecutive_losses: int,
) -> tuple[float, float, float]:
    """Compute position size in coins based on risk parameters.

    Returns
    -------
    tuple[float, float, float]
        (position_size_coins, risk_usd, risk_pct).
    """
    # Defense-in-depth: should be caught by circuit breakers
    if consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES_HALT:
        return 0.0, 0.0, 0.0

    # Risk percentage adjustment
    if consecutive_losses >= config.CONSECUTIVE_LOSS_REDUCE_THRESHOLD:
        risk_pct: float = config.REDUCED_RISK_PCT
    else:
        risk_pct = config.MAX_RISK_PCT

    sl_distance: float = abs(entry - sl)

    # Guard against zero SL distance
    if sl_distance == 0:
        return 0.0, 0.0, risk_pct

    risk_usd: float = balance * risk_pct
    position_size: float = risk_usd / sl_distance

    # Guard against negative or zero result
    if position_size <= 0:
        return 0.0, 0.0, risk_pct

    return round(position_size, config.QUANTITY_PRECISION), round(risk_usd, 2), risk_pct


def enforce_leverage(
    position_size: float, entry: float, balance: float,
) -> tuple[float, float]:
    """Enforce max leverage constraint, scaling position if needed.

    Returns
    -------
    tuple[float, float]
        (final_position_size, leverage_used).
    """
    if balance <= 0 or entry <= 0:
        return 0.0, 0.0

    notional: float = position_size * entry
    required_leverage: float = notional / balance

    if required_leverage > config.MAX_LEVERAGE:
        # Cap at max leverage and recompute position size
        capped_notional: float = balance * config.MAX_LEVERAGE
        position_size = capped_notional / entry
        position_size = round(position_size, config.QUANTITY_PRECISION)
        required_leverage = config.MAX_LEVERAGE

    return position_size, round(required_leverage, 2)
