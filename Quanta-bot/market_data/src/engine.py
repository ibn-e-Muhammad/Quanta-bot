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
from .binance_client import fetch_klines, fetch_funding_rate, SafeModeError, DataIntegrityError
from . import indicators
from .classifier import classify_volatility, classify_market_state
from .state_writer import write_state, build_safe_mode_payload


def run_market_engine(symbol: str, interval: str) -> None:
    """Execute the full MSE pipeline once (stateless)."""
    output_path = str(config.get_state_path(symbol, interval))

    try:
        # ---- Step 1: Fetch klines ----
        klines = fetch_klines(symbol, interval, config.CANDLE_LIMIT)
        funding_rate = fetch_funding_rate(symbol)

        # ---- Step 2: Extract OHLCV arrays ----
        opens = [k["open"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]

        # ---- Step 3: Compute ALL indicators ----
        ema_fast_series = indicators.ema(closes, config.EMA_FAST)
        ema_slow_series = indicators.ema(closes, config.EMA_SLOW)
        ema_confirm_series = indicators.ema(closes, config.EMA_CONFIRM)
        ema_trend_series = indicators.ema(closes, config.EMA_TREND)
        rsi_val = indicators.rsi(closes, config.RSI_PERIOD)
        adx_val = indicators.adx(highs, lows, closes, config.ADX_PERIOD)
        atr_val = indicators.atr(highs, lows, closes, config.ATR_PERIOD)
        bb_lower, bb_middle, bb_upper = indicators.bollinger_bands(closes, config.BB_WINDOW, 2.0)
        vwap_val = indicators.vwap(highs, lows, closes, volumes)
        support_val, resistance_val = indicators.support_resistance(lows, highs, config.EMA_SLOW)
        volume_sma_20 = indicators.sma(volumes, config.EMA_FAST)

        # Latest values
        ema_fast_latest = ema_fast_series[-1]
        ema_slow_latest = ema_slow_series[-1]
        ema_confirm_latest = ema_confirm_series[-1]
        ema_trend_latest = ema_trend_series[-1]
        volume_sma_latest = volume_sma_20[-1]
        current_price = closes[-1]
        current_volume = volumes[-1]

        # ---- Step 4: Compute BB_Width history + ATR history ----
        bb_width_history = []
        bb_offset = config.BB_WINDOW - 1
        for i in range(bb_offset, len(closes)):
            window = closes[i - bb_offset : i + 1]
            bl, bm, bu = indicators.bollinger_bands(window, config.BB_WINDOW, 2.0)
            if bm != 0:
                bb_width_history.append((bu - bl) / bm)

        atr_history = []
        for i in range(config.ATR_PERIOD, len(closes)):
            window_h = highs[: i + 1]
            window_l = lows[: i + 1]
            window_c = closes[: i + 1]
            atr_history.append(indicators.atr(window_h, window_l, window_c, config.ATR_PERIOD))

        # ---- Step 5: Classify ----
        volatility = classify_volatility(
            bb_upper, bb_lower, bb_middle,
            bb_width_history, atr_val, atr_history,
        )
        primary = classify_market_state(adx_val, ema_fast_latest, ema_slow_latest, volatility)

        # ---- Step 6: Assemble output (per Section 4 schema) ----
        state = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price": current_price,
            "ema_fast": round(ema_fast_latest, 2),
            "ema_slow": round(ema_slow_latest, 2),
            "ema_confirm": round(ema_confirm_latest, 2),
            "ema_trend": round(ema_trend_latest, 2),
            "vwap": round(vwap_val, 2),
            "rsi": round(rsi_val, 1),
            "adx": round(adx_val, 1),
            "atr": round(atr_val, 2),
            "bb_lower": round(bb_lower, 2),
            "bb_upper": round(bb_upper, 2),
            "funding_rate": round(funding_rate, 6),
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
        write_state(build_safe_mode_payload(symbol), output_path)

    except Exception as exc:
        print(f"[MSE] SAFE MODE — unexpected error: {exc}", file=sys.stderr)
        write_state(build_safe_mode_payload(symbol), output_path)


# ---------------------------------------------------------------------------
# Allow direct execution: python -m market_data.src.engine
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_market_engine(config.SYMBOL, config.INTERVAL)
    print(f"[MSE] State written to {config.get_state_path(config.SYMBOL, config.INTERVAL)}")
