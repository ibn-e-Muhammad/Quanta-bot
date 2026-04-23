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
from .order_builder import build_order
from .trade_logger import log_trade, log_decision


def run_execution_engine(
    signal: dict,
    account_state: dict,
    atr: float,
    risk_engine,
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
        balance: float = validated_account["account_balance"]
        if not risk_engine.check_circuit_breaker(balance):
            breaker_reason = "Daily Loss Lock Triggered"
            _log_decision_safe(
                action="REJECT", signal_type=sig_type,
                symbol=validated_signal["symbol"],
                reason=breaker_reason,
                log_path=log_path,
            )
            return _reject(breaker_reason)

        # ---- Step 5: ATR-Scaled Position sizing ----
        entry: float = validated_signal["suggested_entry"]
        sl: float = validated_signal["suggested_sl"]
        tp: float = validated_signal["suggested_tp"]

        risk_per_trade = risk_engine.config.get("strategy_thresholds", {}).get("risk_per_trade", 0.02)
        
        final_size = risk_engine.calculate_position_size(
            balance=balance,
            risk_per_trade=risk_per_trade,
            entry_price=entry,
            stop_loss_price=sl,
            atr=atr
        )

        if final_size <= 0:
            return _reject("Final position size is zero after ATR scaling and leverage caps.")

        notional = final_size * entry
        leverage_used = (notional / balance) if balance else 0.0
        risk_usd = balance * risk_engine.margin_fraction
        risk_pct = risk_engine.margin_fraction

        # ---- Step 6: Build order ----
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
            "risk_usd": risk_usd,
            "rr": 0.0,
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
