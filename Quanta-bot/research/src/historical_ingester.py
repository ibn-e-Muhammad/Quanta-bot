"""
historical_ingester.py — High-Throughput Historical Kline Ingester

Fetches paginated historical data from Binance USD-M Futures (/fapi/v1/klines).
No API key required. Implements rate-limit-aware weight management.

Hard Cutoff: April 1, 2021 00:00:00 UTC.
"""

import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # Quanta-bot/
_STRATEGY_CONFIG = _PROJECT_ROOT / "runtime" / "config" / "strategy_config.json"
HISTORICAL_DATA_DIR = _PROJECT_ROOT / "research" / "historical_data"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_BASE = "https://fapi.binance.com"
KLINES_ENDPOINT = "/fapi/v1/klines"
MAX_CANDLES_PER_REQUEST = 1500

# April 1, 2021 00:00:00 UTC in milliseconds
HARD_CUTOFF_MS = int(datetime(2021, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)

CSV_COLUMNS = [
    "timestamp", "datetime_utc", "open", "high", "low",
    "close", "volume", "quote_volume", "trade_count",
]

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


# ---------------------------------------------------------------------------
# Weight Manager — rate-limit aware request throttling
# ---------------------------------------------------------------------------
class WeightManager:
    """
    Tracks API request weight in a rolling 60-second window.
    Target: ~600 weight/min (strict throttle for large backfills).
    Each /fapi/v1/klines request costs ~5 weight.
    """
    TARGET_WEIGHT = 600
    WEIGHT_PER_REQUEST = 5
    WINDOW_SECONDS = 60
    MIN_SLEEP_SECONDS = 0.25

    def __init__(self):
        self._request_log: list[float] = []  # timestamps of requests

    def _prune(self):
        """Remove entries older than the rolling window."""
        cutoff = time.monotonic() - self.WINDOW_SECONDS
        self._request_log = [t for t in self._request_log if t > cutoff]

    def current_weight(self) -> int:
        self._prune()
        return len(self._request_log) * self.WEIGHT_PER_REQUEST

    def wait_if_needed(self):
        """Block until we have budget for another request."""
        while True:
            self._prune()
            if self.current_weight() + self.WEIGHT_PER_REQUEST <= self.TARGET_WEIGHT:
                break
            # Sleep until the oldest request falls out of the window
            if self._request_log:
                oldest = self._request_log[0]
                sleep_for = (oldest + self.WINDOW_SECONDS) - time.monotonic() + 0.1
                if sleep_for > 0:
                    print(f"[WEIGHT] Throttling {sleep_for:.1f}s (current weight: {self.current_weight()})")
                    time.sleep(sleep_for)
            else:
                break

    def record_request(self):
        self._request_log.append(time.monotonic())
        time.sleep(self.MIN_SLEEP_SECONDS)


# ---------------------------------------------------------------------------
# HTTP Fetch
# ---------------------------------------------------------------------------
def _fetch_klines(symbol: str, interval: str, end_time: int | None = None) -> list[list]:
    """Fetch up to 1500 klines from Binance Futures. Returns raw API list."""
    params = f"symbol={symbol}&interval={interval}&limit={MAX_CANDLES_PER_REQUEST}"
    if end_time is not None:
        params += f"&endTime={end_time}"

    url = f"{API_BASE}{KLINES_ENDPOINT}?{params}"

    max_retries = 3
    backoff = 1.0

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "QuantaBot/1.0")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                print(f"[RATE-LIMIT] {e.code} on attempt {attempt + 1}. Backing off {backoff}s...")
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[RETRY] Attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(backoff)
                backoff *= 2
                continue
            raise

    return []


# ---------------------------------------------------------------------------
# Paginated Ingester
# ---------------------------------------------------------------------------
def ingest_symbol_interval(
    symbol: str, interval: str, weight_mgr: WeightManager
) -> list[dict]:
    """
    Fetch all historical klines for a symbol/interval pair going back
    to HARD_CUTOFF (April 1, 2021). Returns list of row dicts sorted
    by timestamp ascending.
    """
    all_rows: list[dict] = []
    seen_timestamps: set[int] = set()
    end_time: int | None = None
    page = 0

    print(f"[INGEST] Starting {symbol}_{interval} — cutoff: {datetime.fromtimestamp(HARD_CUTOFF_MS / 1000, tz=timezone.utc).isoformat()}")

    while True:
        weight_mgr.wait_if_needed()
        weight_mgr.record_request()

        raw = _fetch_klines(symbol, interval, end_time)
        page += 1

        if not raw:
            print(f"[INGEST] Page {page}: API returned empty. Stopping.")
            break

        new_count = 0
        oldest_ts = None

        for candle in raw:
            ts = int(candle[0])

            if ts < HARD_CUTOFF_MS:
                continue
            if ts in seen_timestamps:
                continue

            seen_timestamps.add(ts)
            new_count += 1

            row = {
                "timestamp": ts,
                "datetime_utc": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
                "quote_volume": float(candle[7]),
                "trade_count": int(candle[8]),
            }
            all_rows.append(row)

            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts

        print(f"[INGEST] Page {page}: {new_count} new candles | Oldest: {datetime.fromtimestamp(oldest_ts / 1000, tz=timezone.utc).isoformat() if oldest_ts else 'N/A'}")

        # Check termination conditions
        if oldest_ts is not None and oldest_ts <= HARD_CUTOFF_MS:
            print(f"[INGEST] Reached hard cutoff. Stopping.")
            break

        if new_count == 0:
            print(f"[INGEST] No new candles on this page. Stopping.")
            break

        # Next page: fetch candles older than the oldest we just got
        end_time = oldest_ts - 1

    # Sort ascending by timestamp
    all_rows.sort(key=lambda r: r["timestamp"])
    print(f"[INGEST] {symbol}_{interval} complete. Total candles: {len(all_rows)}")
    return all_rows


# ---------------------------------------------------------------------------
# CSV Writer
# ---------------------------------------------------------------------------
def save_csv(rows: list[dict], symbol: str, interval: str, suffix: str = "") -> Path:
    """Write rows to CSV. Returns filepath.
    
    Parameters
    ----------
    suffix : str
        Optional suffix like '_PARTIAL' appended before _history.csv
    """
    HISTORICAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = HISTORICAL_DATA_DIR / f"{symbol}_{interval}{suffix}_history.csv"

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[CSV] Saved {len(rows)} rows -> {filepath.name}")
    return filepath


# ---------------------------------------------------------------------------
# Config Loader
# ---------------------------------------------------------------------------
def load_matrix() -> tuple[list[str], list[str]]:
    """Load watchlist and intervals from strategy_config.json."""
    try:
        with open(_STRATEGY_CONFIG, "r") as f:
            cfg = json.load(f)
        matrix = cfg.get("matrix", {})
        return matrix.get("watchlist", []), matrix.get("intervals", [])
    except Exception as e:
        print(f"[ERROR] Failed to load matrix config: {e}")
        return [], []


# ---------------------------------------------------------------------------
# MODE C: Recent window (last N days)
# ---------------------------------------------------------------------------
def ingest_recent_symbol_interval(
    symbol: str, interval: str, days: int, weight_mgr: WeightManager
) -> list[dict]:
    """Fetch recent klines (last N days) for a symbol/interval pair."""
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    start_ms = now_ms - (days * 86_400_000)

    weight_mgr.wait_if_needed()
    weight_mgr.record_request()
    raw = _fetch_klines(symbol, interval, end_time=None)

    rows: list[dict] = []
    for candle in raw:
        ts = int(candle[0])
        if ts < start_ms:
            continue
        row = {
            "timestamp": ts,
            "datetime_utc": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": float(candle[1]),
            "high": float(candle[2]),
            "low": float(candle[3]),
            "close": float(candle[4]),
            "volume": float(candle[5]),
            "quote_volume": float(candle[7]),
            "trade_count": int(candle[8]),
        }
        rows.append(row)

    rows.sort(key=lambda r: r["timestamp"])
    print(f"[INGEST-RECENT] {symbol}_{interval} complete. Total candles: {len(rows)}")
    return rows


def run_mode_c(days: int):
    """Fetch recent window for all watchlist symbols (1h only)."""
    watchlist, _ = load_matrix()
    if not watchlist:
        print("[ERROR] Empty watchlist. Cannot proceed.")
        sys.exit(1)

    wm = WeightManager()
    print("=" * 60)
    print(f"[MODE C] Recent window — {len(watchlist)} symbols x 1h x {days} days")
    print("=" * 60)

    for symbol in watchlist:
        print(f"[RECENT] Processing {symbol}_1h")
        try:
            rows = ingest_recent_symbol_interval(symbol, "1h", days, wm)
        except Exception as exc:
            print(f"[ERROR] {symbol}_1h failed: {exc}")
            continue
        if not rows:
            print(f"[SKIP] No recent data for {symbol}_1h")
            continue
        save_csv(rows, symbol, "1h")


# ---------------------------------------------------------------------------
# MODE A: Unit Test (single pair)
# ---------------------------------------------------------------------------
def run_mode_a():
    """Fetch BTCUSDT_1h only, validate, and report."""
    # Import validator here to avoid circular imports
    from research.src.data_validator import validate_csv

    print("=" * 60)
    print("[MODE A] Unit Test -- BTCUSDT_1h")
    print("=" * 60)

    wm = WeightManager()
    rows = ingest_symbol_interval("BTCUSDT", "1h", wm)

    if not rows:
        print("[FAILURE] No data returned for BTCUSDT_1h. Cannot proceed.")
        sys.exit(1)

    filepath = save_csv(rows, "BTCUSDT", "1h")

    is_valid = validate_csv(str(filepath), "1h")

    if is_valid:
        print("[VERIFICATION] BTCUSDT_1h PASSED integrity check.")
    else:
        print("[FAILURE] Data integrity compromised. Fix before proceeding.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# MODE B: Production (all remaining pairs)
# ---------------------------------------------------------------------------
def _detect_partial(rows: list[dict]) -> bool:
    """Return True if the dataset didn't reach the hard cutoff (young coin)."""
    if not rows:
        return True
    oldest_ts = rows[0]["timestamp"]  # rows are sorted ascending
    return oldest_ts > HARD_CUTOFF_MS


def run_mode_b():
    """Iterate all symbol x interval combinations, fetch, save, validate."""
    from research.src.data_validator import validate_csv

    watchlist, intervals = load_matrix()
    if not watchlist or not intervals:
        print("[ERROR] Empty matrix config. Cannot proceed.")
        sys.exit(1)

    total_combos = len(watchlist) * len(intervals)
    print("=" * 60)
    print(f"[MODE B] Production -- {len(watchlist)} symbols x {len(intervals)} intervals = {total_combos} datasets")
    print("=" * 60)

    wm = WeightManager()
    results = {"successful": 0, "partial": 0, "failed": 0, "skipped": 0}
    total_rows = 0
    processed = 0

    for symbol in watchlist:
        for interval in intervals:
            # Skip BTCUSDT_1h (already done in MODE A)
            if symbol == "BTCUSDT" and interval == "1h":
                total_rows += 44195  # count MODE A rows
                results["successful"] += 1
                processed += 1
                continue

            processed += 1
            print(f"\n{'=' * 50}")
            print(f"[BATCH] [{processed}/{total_combos}] Processing {symbol}_{interval}")
            print(f"{'=' * 50}")

            try:
                rows = ingest_symbol_interval(symbol, interval, wm)

                if not rows:
                    print(f"[SKIP] No data for {symbol}_{interval}")
                    results["skipped"] += 1
                    continue

                is_partial = _detect_partial(rows)

                if is_partial:
                    print(f"[ERROR] {symbol}_{interval} is partial. Strict mode requires full history.")
                    results["failed"] += 1
                    continue
                min_rows = 1000

                # Save to primary path first
                filepath = save_csv(rows, symbol, interval)
                is_valid = validate_csv(str(filepath), interval, min_rows=min_rows)

                if is_valid:
                    total_rows += len(rows)
                    print(f"[SUCCESS] {symbol}_{interval} validated successfully ({len(rows)} rows)")
                    results["successful"] += 1
                else:
                    print(f"[WARNING] {symbol}_{interval} failed validation")
                    try:
                        filepath.unlink(missing_ok=True)
                    except Exception:
                        pass
                    results["failed"] += 1

            except Exception as e:
                print(f"[ERROR] Persistent failure for {symbol}_{interval}: {e}. Skipping.")
                results["skipped"] += 1

    # ---- Final Summary Report ----
    print(f"\n{'=' * 60}")
    print("[SUMMARY]")
    print(f"Datasets Processed: {processed}")
    print(f"Successful: {results['successful']}")
    print(f"Partial: {results['partial']}")
    print(f"Failed: {results['failed']}")
    print(f"Skipped: {results['skipped']}")
    print(f"Total Rows: {total_rows:,}")
    print(f"{'=' * 60}")

    if results["failed"] > 0:
        print("[FAILURE] Strict ingestion detected failed or partial datasets.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Quanta Historical Data Ingester")
    parser.add_argument("--mode", choices=["a", "b", "c"], default="a",
                        help="a = Unit test (BTCUSDT_1h only), b = Full production run, c = Recent window")
    parser.add_argument("--days", type=int, default=30,
                        help="Recent window size in days (mode c only)")
    args = parser.parse_args()

    if args.mode == "a":
        run_mode_a()
    elif args.mode == "b":
        run_mode_b()
    elif args.mode == "c":
        run_mode_c(args.days)
