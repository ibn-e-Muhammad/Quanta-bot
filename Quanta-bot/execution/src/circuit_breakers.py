"""
circuit_breakers.py — Drawdown, Trade Count & Consecutive Loss Checks

Single public function: check_circuit_breakers()
Returns (passed, reason). Pure function — no file I/O.
"""

from . import config


def check_circuit_breakers(account: dict) -> tuple[bool, str]:
    """Check all circuit breaker conditions against the account state.

    Parameters
    ----------
    account : dict
        Validated account state dict.

    Returns
    -------
    tuple[bool, str]
        (True, "") if all checks pass.
        (False, reason) if any breaker trips — trade must be REJECTED.
    """
    balance: float = account["account_balance"]
    daily_start: float = account["daily_equity_start"]
    daily_peak: float = account["daily_peak_equity"]
    trade_count: int = account["daily_trade_count"]
    consec_losses: int = account["consecutive_losses"]
    status: str = account["system_status"]

    # 1. System already halted
    if status == "HALTED":
        return False, "System is HALTED — all signals rejected"

    # 2. Daily drawdown: balance <= daily_equity_start * 0.95
    if balance <= daily_start * config.DAILY_DRAWDOWN_FACTOR:
        return False, "HALTED_DAILY_DRAWDOWN: equity fell 5% below daily start"

    # 3. Peak-to-trough drawdown: balance <= daily_peak_equity * 0.95
    if balance <= daily_peak * config.PEAK_DRAWDOWN_FACTOR:
        return False, "HALTED_PEAK_DRAWDOWN: equity fell 5% below daily peak"

    # 4. Trade frequency limit
    if trade_count >= config.MAX_DAILY_TRADES:
        return False, f"MAX_TRADES_REACHED: {trade_count} trades today (limit {config.MAX_DAILY_TRADES})"

    # 5. Consecutive loss halt
    if consec_losses >= config.MAX_CONSECUTIVE_LOSSES_HALT:
        return False, f"HALTED_CONSECUTIVE_LOSSES: {consec_losses} consecutive losses (limit {config.MAX_CONSECUTIVE_LOSSES_HALT})"

    return True, ""
