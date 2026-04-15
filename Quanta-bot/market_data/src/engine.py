"""
engine.py — Room 1 Orchestrator

Single public function: run_market_engine()
Pipeline: Fetch → Compute → Classify → Write

Stateless between invocations. No trade logic. No cross-room reads.
On ANY failure → writes SAFE MODE payload.
"""

import sys
from datetime import datetime, timezone

from . import config
from .binance_client import fetch_klines, SafeModeError, DataIntegrityError
from . import indicators
from .classifier import classify_volatility, classify_market_state
from .state_writer import write_state, build_safe_mode_payload


def run_market_engine() -> None:
    """Execute the full MSE pipeline once (stateless)."""
    output_path = str(config.STATE_FILE_PATH)

    try:
        # ---- Step 1: Fetch klines ----
        klines = fetch_klines(config.SYMBOL, config.INTERVAL, config.CANDLE_LIMIT)

        # ---- Step 2: Extract OHLCV arrays ----
        opens = [k["open"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]

        # ---- Step 3: Compute ALL indicators ----
        ema_20_series = indicators.ema(closes, 20)
        ema_50_series = indicators.ema(closes, 50)
        rsi_val = indicators.rsi(closes, 14)
        adx_val = indicators.adx(highs, lows, closes, 14)
        atr_val = indicators.atr(highs, lows, closes, 14)
        bb_lower, bb_middle, bb_upper = indicators.bollinger_bands(closes, 20, 2.0)
        vwap_val = indicators.vwap(highs, lows, closes, volumes)
        support_val, resistance_val = indicators.support_resistance(lows, highs, 50)
        volume_sma_20 = indicators.sma(volumes, 20)

        # Latest values
        ema_20_latest = ema_20_series[-1]
        ema_50_latest = ema_50_series[-1]
        volume_sma_latest = volume_sma_20[-1]
        current_price = closes[-1]
        current_volume = volumes[-1]

        # ---- Step 4: Compute BB_Width history + ATR history ----
        bb_width_history = []
        for i in range(19, len(closes)):
            window = closes[i - 19 : i + 1]
            bl, bm, bu = indicators.bollinger_bands(window, 20, 2.0)
            if bm != 0:
                bb_width_history.append((bu - bl) / bm)

        atr_history = []
        for i in range(14, len(closes)):
            window_h = highs[: i + 1]
            window_l = lows[: i + 1]
            window_c = closes[: i + 1]
            atr_history.append(indicators.atr(window_h, window_l, window_c, 14))

        # ---- Step 5: Classify ----
        volatility = classify_volatility(
            bb_upper, bb_lower, bb_middle,
            bb_width_history, atr_val, atr_history,
        )
        primary = classify_market_state(adx_val, ema_20_latest, ema_50_latest, volatility)

        # ---- Step 6: Assemble output (per Section 4 schema) ----
        state = {
            "symbol": config.SYMBOL,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price": current_price,
            "ema_20": round(ema_20_latest, 2),
            "ema_50": round(ema_50_latest, 2),
            "vwap": round(vwap_val, 2),
            "rsi": round(rsi_val, 1),
            "adx": round(adx_val, 1),
            "atr": round(atr_val, 2),
            "bb_lower": round(bb_lower, 2),
            "bb_upper": round(bb_upper, 2),
            "current_volume": round(current_volume, 2),
            "volume_sma_20": round(volume_sma_latest, 2),
            "state": {
                "primary": primary,
                "volatility": volatility,
            },
            "support_level": round(support_val, 2),
            "resistance_level": round(resistance_val, 2),
        }

        # ---- Step 7: Atomic write ----
        write_state(state, output_path)

    except (SafeModeError, DataIntegrityError) as exc:
        print(f"[MSE] SAFE MODE — {exc}", file=sys.stderr)
        write_state(build_safe_mode_payload(), output_path)

    except Exception as exc:
        print(f"[MSE] SAFE MODE — unexpected error: {exc}", file=sys.stderr)
        write_state(build_safe_mode_payload(), output_path)


# ---------------------------------------------------------------------------
# Allow direct execution: python -m market_data.src.engine
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_market_engine()
    print(f"[MSE] State written to {config.STATE_FILE_PATH}")
