"""
engine.py — Room 3 Orchestrator

Single public function: run_execution_engine()
Pipeline: Intake → Triage → Circuit Breakers → RR → Position Sizing →
          Leverage → Order → Log → Output

Stateless between invocations. No external API calls.
"""

import sys
from datetime import datetime, timezone

from . import config
from .signal_intake import validate_signal
from .account_state import validate_account_state
from .circuit_breakers import check_circuit_breakers
from .risk_engine import validate_rr, compute_position_size, enforce_leverage
from .order_builder import build_order
from .trade_logger import log_trade, log_decision


def run_execution_engine(
    signal: dict,
    account_state: dict,
    *,
    db_path: str | None = None,
    log_path: str | None = None,
) -> dict:
    """Execute the full Execution Engine pipeline once (stateless).

    Parameters
    ----------
    signal : dict
        Strategy signal from Room 2.
    account_state : dict
        Current account state.
    db_path : str | None
        Override SQLite path (for testing).
    log_path : str | None
        Override decision log path (for testing).

    Returns
    -------
    dict
        Execution result: {"action": "EXECUTE"|"REJECT", "order", "risk_summary", "reason"}
    """
    try:
        # ---- Step 1: Validate signal ----
        validated_signal: dict | None = validate_signal(signal)
        if validated_signal is None:
            return _reject("Invalid signal input — missing or malformed fields")

        # ---- Step 2: Signal triage ----
        sig_type: str = validated_signal["signal"]
        if sig_type == "HOLD":
            _log_decision_safe(
                action="NO_ACTION", signal_type="HOLD",
                symbol=validated_signal.get("symbol", "UNKNOWN"),
                reason="Signal is HOLD — no action taken",
                log_path=log_path,
            )
            return _reject("Signal is HOLD — no action taken")

        # ---- Step 3: Validate account state ----
        validated_account: dict | None = validate_account_state(account_state)
        if validated_account is None:
            return _reject("Invalid account state — missing or malformed fields")

        # ---- Step 4: Circuit breakers ----
        breaker_passed, breaker_reason = check_circuit_breakers(validated_account)
        if not breaker_passed:
            _log_decision_safe(
                action="REJECT", signal_type=sig_type,
                symbol=validated_signal["symbol"],
                reason=breaker_reason,
                log_path=log_path,
            )
            return _reject(breaker_reason)

        # ---- Step 5: RR validation ----
        entry: float = validated_signal["suggested_entry"]
        sl: float = validated_signal["suggested_sl"]
        tp: float = validated_signal["suggested_tp"]

        rr_valid, rr_ratio, rr_reason = validate_rr(entry, sl, tp, sig_type)
        if not rr_valid:
            _log_decision_safe(
                action="REJECT", signal_type=sig_type,
                symbol=validated_signal["symbol"],
                reason=rr_reason,
                log_path=log_path,
            )
            return _reject(rr_reason)

        # ---- Step 6: Position sizing ----
        balance: float = validated_account["account_balance"]
        consec_losses: int = validated_account["consecutive_losses"]

        pos_size, risk_usd, risk_pct = compute_position_size(
            balance, entry, sl, consec_losses,
        )

        if pos_size <= 0:
            return _reject("Position size is zero or negative — trade rejected")

        # ---- Step 7: Leverage enforcement ----
        final_size, leverage_used = enforce_leverage(pos_size, entry, balance)

        if final_size <= 0:
            return _reject("Final position size is zero after leverage cap")

        # ---- Step 8: Build order ----
        order: dict = build_order(validated_signal, final_size, leverage_used)

        # ---- Step 9: Log trade to SQLite ----
        ts: str = datetime.now(timezone.utc).isoformat()
        trade_record: dict = {
            "timestamp": ts,
            "symbol": validated_signal["symbol"],
            "action": sig_type,
            "size": final_size,
            "leverage_used": leverage_used,
            "entry_price": entry,
            "sl_price": sl,
            "tp_price": tp,
            "risk_usd": risk_usd,
            "strategy_used": validated_signal.get("strategy_used", ""),
            "confidence_score": validated_signal.get("confidence_score", 0.0),
            "reason": validated_signal.get("reason", ""),
        }
        log_trade(trade_record, db_path=db_path)

        # ---- Step 10: Log decision ----
        decision_entry: dict = {
            "timestamp": ts,
            "action": "EXECUTE",
            "symbol": validated_signal["symbol"],
            "signal": sig_type,
            "strategy_used": validated_signal.get("strategy_used", ""),
            "size": final_size,
            "leverage": leverage_used,
            "rr": rr_ratio,
            "risk_usd": risk_usd,
            "reason": f"Trade approved — {validated_signal.get('strategy_used', '')} {sig_type}",
        }
        log_decision(decision_entry, log_path=log_path)

        # ---- Step 11: Return result ----
        return {
            "action": "EXECUTE",
            "order": order,
            "risk_summary": {
                "position_size": final_size,
                "leverage": leverage_used,
                "rr_ratio": rr_ratio,
                "risk_usd": risk_usd,
                "risk_pct": risk_pct,
            },
            "reason": f"Trade approved — {validated_signal.get('strategy_used', '')} {sig_type}",
        }

    except Exception as exc:
        print(f"[EE] REJECT — unexpected error: {exc}", file=sys.stderr)
        return _reject(f"Internal error: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _reject(reason: str) -> dict:
    """Build a REJECT result."""
    return {
        "action": "REJECT",
        "order": None,
        "risk_summary": None,
        "reason": reason,
    }


def _log_decision_safe(
    *,
    action: str,
    signal_type: str,
    symbol: str,
    reason: str,
    log_path: str | None = None,
) -> None:
    """Log decision without raising on I/O errors."""
    try:
        log_decision(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "symbol": symbol,
                "signal": signal_type,
                "strategy_used": "None",
                "size": 0.0,
                "leverage": 0.0,
                "rr": 0.0,
                "risk_usd": 0.0,
                "reason": reason,
            },
            log_path=log_path,
        )
    except Exception:
        pass  # Decision log failure must not crash the engine
