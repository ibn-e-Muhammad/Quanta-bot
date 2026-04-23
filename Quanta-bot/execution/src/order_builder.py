"""
order_builder.py — Build Broker-Ready Order Payload

Single public function: build_order()
Formats the order per execution-context.md §4.1. Pure function.
"""

from . import config


def build_order(signal: dict, position_size: float, leverage: float) -> dict:
    """Build a broker-ready order payload.

    Parameters
    ----------
    signal : dict
        Validated strategy signal with entry, sl, tp.
    position_size : float
        Final position size in coins (after leverage enforcement).
    leverage : float
        Final leverage to use.

    Returns
    -------
    dict
        Broker-ready order payload.
    """
    entry_price = float(signal.get("suggested_entry") or 0.0)

    return {
        "symbol": signal["symbol"],
        "side": signal["signal"],           # "BUY" or "SELL"
        "type": "LIMIT",
        "timeInForce": "GTX",              # Post-only: cancel if it would take
        "post_only": True,
        "price": round(entry_price, 4) if entry_price else None,
        "quantity": round(position_size, config.QUANTITY_PRECISION),
        "leverage": round(leverage, 1),
        "reduce_only": False,
        "oco_sl": round(signal["suggested_sl"], 2),
        "oco_tp": round(signal["suggested_tp"], 2),
    }
