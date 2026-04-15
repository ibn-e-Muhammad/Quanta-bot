"""
engine.py — Room 2 Orchestrator

Single public function: run_strategy_engine()
Pipeline: Read → Filter → Evaluate → Validate → Output

Stateless between invocations. No trade execution. No file writes.
No network calls. Returns a signal dict.
"""

import sys
from datetime import datetime, timezone

from . import config
from .state_reader import read_market_state
from .strategies import evaluate_breakout, evaluate_trend, evaluate_range
from .confidence import compute_confidence
from .signal_validator import validate_signal


def run_strategy_engine(state_path: str | None = None) -> dict:
    """Execute the full Strategy Engine pipeline once (stateless).

    Parameters
    ----------
    state_path : str | None
        Path to market state JSON. Defaults to config.STATE_FILE_PATH.

    Returns
    -------
    dict
        A valid signal dict per strategy-context.md §7 output schema.
    """
    try:
        # ---- Step 1: Read market state ----
        state: dict | None = read_market_state(state_path)

        if state is None:
            return _hold("Data validation failure or missing market state file.")

        # ---- Step 2: Market filter ----
        primary: str = state["state"]["primary"]
        volatility: str = state["state"]["volatility"]

        if primary == "SIDEWAYS" or volatility == "LOW":
            return _hold(
                f"Market conditions unfavorable: primary={primary}, volatility={volatility}",
                state=state,
            )

        # ---- Step 3: Strategy evaluation (priority: Breakout > Trend > Range) ----
        signal: dict | None = evaluate_breakout(state)

        if signal is None:
            signal = evaluate_trend(state)

        if signal is None:
            signal = evaluate_range(state)

        if signal is None:
            return _hold("No strategy conditions met.", state=state)

        # ---- Step 4: Confidence scoring ----
        confidence: float = compute_confidence(state, signal)
        signal["confidence_score"] = round(confidence, 3)

        # ---- Step 5: Output validation ----
        signal = validate_signal(signal)

        return signal

    except Exception as exc:
        print(f"[SE] HOLD — unexpected error: {exc}", file=sys.stderr)
        return _hold(f"Internal error: {exc}")


def _hold(reason: str, *, state: dict | None = None) -> dict:
    """Build a HOLD signal."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": state["symbol"] if state else "UNKNOWN",
        "signal": "HOLD",
        "strategy_used": "None",
        "confidence_score": 0.0,
        "suggested_entry": None,
        "suggested_sl": None,
        "suggested_tp": None,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Allow direct execution: python -m strategy.src.engine
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    result: dict = run_strategy_engine()
    import json
    print(json.dumps(result, indent=2))
