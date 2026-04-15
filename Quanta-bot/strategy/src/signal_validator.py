"""
signal_validator.py — Output Validation for Strategy Signals

Single public function: validate_signal()
Enforces SL/TP ordering, RR >= 1.5, and valid signal enums.
Forces HOLD on any violation. Pure function.
"""

from datetime import datetime, timezone

from . import config


def validate_signal(signal: dict) -> dict:
    """Validate and potentially override a strategy signal.

    Rules (per strategy-context.md §8):
        1. Signal must be BUY, SELL, or HOLD.
        2. BUY:  SL < ENTRY < TP
        3. SELL: TP < ENTRY < SL
        4. RR = abs(TP - ENTRY) / abs(ENTRY - SL) >= 1.5

    If any rule is violated → return HOLD with reason.
    If signal is HOLD → passthrough unchanged.
    """
    sig_type: str = signal.get("signal", "")

    # Rule 1: Valid signal enum
    if sig_type not in config.VALID_SIGNALS:
        return _hold_signal(signal, f"Invalid signal type: {sig_type}")

    # HOLD signals pass through — nothing to validate
    if sig_type == "HOLD":
        return signal

    entry: float | None = signal.get("suggested_entry")
    sl: float | None = signal.get("suggested_sl")
    tp: float | None = signal.get("suggested_tp")

    # Must have all three values for BUY/SELL
    if entry is None or sl is None or tp is None:
        return _hold_signal(signal, "Missing entry, SL, or TP values")

    # Rule 2 & 3: SL/TP ordering
    if sig_type == "BUY":
        if not (sl < entry < tp):
            return _hold_signal(
                signal,
                f"BUY validation failed: SL({sl}) < ENTRY({entry}) < TP({tp}) violated",
            )
    elif sig_type == "SELL":
        if not (tp < entry < sl):
            return _hold_signal(
                signal,
                f"SELL validation failed: TP({tp}) < ENTRY({entry}) < SL({sl}) violated",
            )

    # Rule 4: Risk/Reward ratio
    sl_distance: float = abs(entry - sl)
    if sl_distance == 0:
        return _hold_signal(signal, "SL distance is zero — cannot compute RR")

    rr: float = abs(tp - entry) / sl_distance
    if rr < config.MIN_RR_RATIO:
        return _hold_signal(
            signal,
            f"RR ratio {rr:.2f} below minimum {config.MIN_RR_RATIO}",
        )

    return signal


def _hold_signal(original: dict, reason: str) -> dict:
    """Build a HOLD signal preserving symbol and timestamp from the original."""
    return {
        "timestamp": original.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "symbol": original.get("symbol", "UNKNOWN"),
        "signal": "HOLD",
        "strategy_used": "None",
        "confidence_score": 0.0,
        "suggested_entry": None,
        "suggested_sl": None,
        "suggested_tp": None,
        "reason": reason,
    }
