"""
Phase 5.2 — Adaptive Engine Governance Layer
State machine: ACTIVE → COOLDOWN → RECOVERY → ACTIVE
"""

# --- Hysteresis thresholds ---
ENGINE_GOOD_PF    = 1.05
ENGINE_BAD_PF     = 0.90
RECOVERY_TRADES   = 10
COOLDOWN_TRADES   = 20
MIN_TRADES        = 30

BASE_RISK         = 0.0075

# Execution priority (highest = tried first per timestamp)
ENGINE_PRIORITY = ["expansion_engine", "breakout_engine", "mean_reversion_engine"]


def initial_state():
    """Return a fresh engine performance / state record."""
    return {
        "state": "ACTIVE",
        "cooldown_remaining": 0,
        "recovery_trades": 0,
        "trades": 0,
        "gross_wins": 0.0,
        "gross_losses": 0.0,
        # Counters for reporting
        "ticks_active": 0,
        "ticks_cooldown": 0,
        "ticks_recovery": 0,
        "recovery_attempts": 0,
        "recovery_successes": 0,
    }


def get_pf(engine_state):
    losses = engine_state["gross_losses"]
    if losses == 0:
        return float("inf")
    return engine_state["gross_wins"] / losses


def record_trade(engine_state, pnl_usd):
    """Update win/loss buckets after a trade closes."""
    engine_state["trades"] += 1
    if pnl_usd > 0:
        engine_state["gross_wins"] += pnl_usd
    else:
        engine_state["gross_losses"] += abs(pnl_usd)


def tick_state(engine_name, engine_state, log_lines):
    """
    Advance the state machine by one trade-slot.
    Must be called BEFORE deciding whether to trade.
    Returns (allow_trade: bool, risk_multiplier: float).
    """
    state   = engine_state["state"]
    trades  = engine_state["trades"]
    pf      = get_pf(engine_state)

    if state == "ACTIVE":
        engine_state["ticks_active"] += 1
        # Evaluate whether engine has degraded past threshold
        if trades >= MIN_TRADES and pf < ENGINE_BAD_PF:
            engine_state["state"] = "COOLDOWN"
            engine_state["cooldown_remaining"] = COOLDOWN_TRADES
            log_lines.append(
                f"[ENGINE COOLDOWN] {engine_name} | PF: {pf:.2f} | Duration: {COOLDOWN_TRADES} trades"
            )
            return False, 0.0

        # Risk scaling while ACTIVE
        if pf >= 1.10:
            risk_mult = 1.0
        elif pf >= 1.00:
            risk_mult = 0.75
        else:
            risk_mult = 0.5

        log_lines.append(
            f"[ENGINE ACTIVE] {engine_name} | PF: {pf:.2f} | Risk: {BASE_RISK * risk_mult * 100:.2f}%"
        )
        return True, risk_mult

    elif state == "COOLDOWN":
        engine_state["ticks_cooldown"] += 1
        engine_state["cooldown_remaining"] -= 1
        if engine_state["cooldown_remaining"] <= 0:
            engine_state["state"] = "RECOVERY"
            engine_state["recovery_trades"] = RECOVERY_TRADES
            engine_state["recovery_attempts"] += 1
            log_lines.append(
                f"[ENGINE RECOVERY] {engine_name} | Grace trades: {RECOVERY_TRADES}"
            )
        return False, 0.0

    else:  # RECOVERY
        engine_state["ticks_recovery"] += 1
        engine_state["recovery_trades"] -= 1

        if engine_state["recovery_trades"] <= 0:
            pf_now = get_pf(engine_state)
            if pf_now >= ENGINE_BAD_PF:
                engine_state["state"] = "ACTIVE"
                engine_state["recovery_successes"] += 1
                log_lines.append(
                    f"[ENGINE RECOVERED] {engine_name} | PF: {pf_now:.2f} -> ACTIVE"
                )
            else:
                engine_state["state"] = "COOLDOWN"
                engine_state["cooldown_remaining"] = COOLDOWN_TRADES
                log_lines.append(
                    f"[ENGINE FAILED RECOVERY] {engine_name} | PF: {pf_now:.2f} -> COOLDOWN"
                )
        # During RECOVERY: always allow trading at 50% risk; PF checks suppressed
        return True, 0.5
