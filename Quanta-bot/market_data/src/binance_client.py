"""
binance_client.py — Stateless Binance REST Client

Single public function: fetch_klines()
- Calls /api/v3/klines
- Exponential backoff on HTTP 429
- Full candle integrity validation per binance-api-expert.md
- Returns list[dict] with all values cast to float
"""

import time
import requests

from . import config


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------
class SafeModeError(Exception):
    """Raised when repeated rate-limits force a SAFE MODE transition."""


class DataIntegrityError(Exception):
    """Raised when candle data fails validation — entire batch discarded."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_klines(
    symbol: str = config.SYMBOL,
    interval: str = config.INTERVAL,
    limit: int = config.CANDLE_LIMIT,
) -> list[dict]:
    """
    Fetch kline/candlestick data from Binance.

    Returns
    -------
    list[dict]
        Each dict: {open_time, open, high, low, close, volume}
        All numeric values are float.

    Raises
    ------
    SafeModeError   – 3 consecutive HTTP-429 responses.
    DataIntegrityError – Any validation failure on the response.
    """
    url = f"{config.API_BASE_URL}{config.KLINES_ENDPOINT}"
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    # ---- Rate-limit aware fetch with exponential backoff ----
    consecutive_429 = 0
    backoff = config.BACKOFF_BASE_SECONDS

    while True:
        resp = requests.get(url, params=params, timeout=30)

        if resp.status_code == 429:
            consecutive_429 += 1
            if consecutive_429 >= config.MAX_RETRIES:
                raise SafeModeError(
                    f"Rate-limited {consecutive_429} consecutive times — entering SAFE MODE"
                )
            time.sleep(backoff)
            backoff *= 2
            continue

        resp.raise_for_status()
        break

    raw: list = resp.json()

    # ---- Validate & transform ----
    return _validate_and_transform(raw)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _validate_and_transform(raw_klines: list) -> list[dict]:
    """Validate every candle and cast to dict[str, float]."""

    if not raw_klines:
        raise DataIntegrityError("Empty kline response from Binance")

    candles: list[dict] = []
    prev_ts: float | None = None

    for idx, k in enumerate(raw_klines):
        # Binance kline array indices:
        #  0=open_time 1=open 2=high 3=low 4=close 5=volume ...
        if len(k) < 6:
            raise DataIntegrityError(f"Candle {idx}: fewer than 6 fields")

        open_time, open_, high, low, close, volume = k[0], k[1], k[2], k[3], k[4], k[5]

        # Null / None check
        for name, val in [
            ("open_time", open_time),
            ("open", open_),
            ("high", high),
            ("low", low),
            ("close", close),
            ("volume", volume),
        ]:
            if val is None:
                raise DataIntegrityError(f"Candle {idx}: {name} is null")

        ts = float(open_time)

        # Chronological order check
        if prev_ts is not None and ts <= prev_ts:
            raise DataIntegrityError(
                f"Candle {idx}: timestamp {ts} is not after previous {prev_ts}"
            )
        prev_ts = ts

        candles.append(
            {
                "open_time": ts,
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
            }
        )

    return candles
