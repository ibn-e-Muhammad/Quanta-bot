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


def run_strategy_engine(symbol: str, interval: str, state_path: str | None = None) -> dict:
    """Execute the full Strategy Engine pipeline once (stateless).

    Parameters
    ----------
    symbol : str
        The watchlist symbol, e.g., "BTCUSDT".
    interval : str
        The timeframe interval, e.g., "15m".
    state_path : str | None
        Path to market state JSON. Defaults to using config.get_state_path.

    Returns
    -------
    dict
        A valid signal dict per strategy-context.md §7 output schema.
    """
    try:
        # ---- Step 1: Read market state ----
        state: dict | None = read_market_state(symbol, interval, state_path)

        if state is None:
            return _hold("Data validation failure or missing market state file.", symbol=symbol, interval=interval)

        # ---- Step 2: Market filter ----
        primary: str = state["state"]["primary"]
        volatility: str = state["state"]["volatility"]

        if primary == "SIDEWAYS" or volatility == "LOW":
            return _hold(
                f"Market conditions unfavorable: primary={primary}, volatility={volatility}",
                state=state,
            )

        # ---- Step 3: Strategy evaluation (priority: Breakout > Trend > Range) ----
        hold_reasons = []
        
        signal: dict | str | None = evaluate_breakout(state)
        if isinstance(signal, str):
            hold_reasons.append(signal)
            signal = None

        if signal is None:
            signal = evaluate_trend(state)
            if isinstance(signal, str):
                hold_reasons.append(signal)
                signal = None

        if signal is None:
            signal = evaluate_range(state)
            if isinstance(signal, str):
                hold_reasons.append(signal)
                signal = None

        if signal is None:
            reason = " | ".join(hold_reasons) if hold_reasons else "No strategy conditions met."
            return _hold(reason, state=state, interval=interval)

        # ---- Step 4: Confidence scoring ----
        confidence: float = compute_confidence(state, signal)
        signal["confidence_score"] = round(confidence, 3)

        # ---- Step 4b: Composite Scoring ----
        entry = signal["suggested_entry"]
        sl = signal["suggested_sl"]
        tp = signal["suggested_tp"]
        adx = state["adx"]

        rr_ratio = 0.0
        if signal["signal"] == "BUY" and entry > sl:
            rr_ratio = (tp - entry) / (entry - sl)
        elif signal["signal"] == "SELL" and sl > entry:
            rr_ratio = (entry - tp) / (sl - entry)

        # Normalize metrics for scoring
        norm_confidence = confidence  # already 0-1
        norm_rr = min(rr_ratio / 5.0, 1.0)  # cap 5.0 at 1.0
        norm_adx = min(adx / 100.0, 1.0)  # adx is 0-100

        composite_score = (
            (norm_confidence * config.WEIGHT_CONFIDENCE)
            + (norm_rr * config.WEIGHT_RR)
            + (norm_adx * config.WEIGHT_ADX)
        )
        signal["interval"] = interval
        signal["composite_score"] = round(composite_score, 4)

        # ---- Step 5: Output validation ----
        signal = validate_signal(signal)

        return signal

    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[SE] HOLD — unexpected error: {exc}", file=sys.stderr)
        return _hold(f"Internal error: {exc}", symbol=symbol, interval=interval)


def _hold(reason: str, *, state: dict | None = None, symbol: str = "UNKNOWN", interval: str = "UNKNOWN") -> dict:
    """Build a HOLD signal."""
    # Attempt to resolve symbol
    final_symbol = symbol
    if state and "symbol" in state:
        final_symbol = state["symbol"]
        
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": final_symbol,
        "interval": interval,
        "signal": "HOLD",
        "strategy_used": "None",
        "confidence_score": 0.0,
        "composite_score": 0.0,
        "suggested_entry": None,
        "suggested_sl": None,
        "suggested_tp": None,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Allow direct execution: python -m strategy.src.engine
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    result: dict = run_strategy_engine("BTCUSDT", "15m")
    import json
    print(json.dumps(result, indent=2))
