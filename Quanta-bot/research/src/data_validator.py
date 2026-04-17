"""
data_validator.py — Strict CSV Integrity Validator

Validates historical kline CSV files for structure, data sanity,
sorting, duplicates, time continuity, and minimum data requirements.

Returns True only if ALL checks pass. Zero tolerance for bad data.
"""

import csv
import math
from pathlib import Path


# ---------------------------------------------------------------------------
# Interval → expected millisecond gap
# ---------------------------------------------------------------------------
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

REQUIRED_COLUMNS = [
    "timestamp", "datetime_utc", "open", "high", "low",
    "close", "volume", "quote_volume", "trade_count",
]

MINIMUM_ROWS = 1000


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def validate_csv(filepath: str, interval: str, min_rows: int = MINIMUM_ROWS) -> bool:
    """
    Validate a historical kline CSV file.

    Parameters
    ----------
    filepath : str
        Absolute path to the CSV file.
    interval : str
        The kline interval (e.g., "5m", "1h", "1d").
    min_rows : int
        Minimum row count required (default 1000, use 500 for young coins).

    Returns
    -------
    bool
        True if ALL validation checks pass, False otherwise.
    """
    path = Path(filepath)
    errors: list[str] = []

    print(f"[VALIDATOR] Checking {path.name}...")

    # ---- Check 0: File exists ----
    if not path.exists():
        print(f"[ERROR] File not found: {filepath}")
        return False

    # ---- Read CSV ----
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            rows = list(reader)
    except Exception as e:
        print(f"[ERROR] Failed to read CSV: {e}")
        return False

    # ---- Check 1: Structure Validation ----
    for col in REQUIRED_COLUMNS:
        if col not in headers:
            errors.append(f"Missing required column: '{col}'")

    if errors:
        _report(errors)
        return False

    # ---- Check 2: Minimum Data Requirement ----
    if len(rows) < min_rows:
        errors.append(f"Insufficient rows: {len(rows)} (minimum: {min_rows})")
        _report(errors)
        return False

    # ---- Check 3: Data Sanity ----
    numeric_positive = ["open", "high", "low", "close"]
    numeric_non_negative = ["volume", "quote_volume"]

    for i, row in enumerate(rows):
        # Check for nulls/empty
        for col in REQUIRED_COLUMNS:
            val = row.get(col, "")
            if val is None or val.strip() == "":
                errors.append(f"Row {i}: Null/empty value in column '{col}'")
                if len(errors) > 20:
                    errors.append("... (truncated, too many errors)")
                    _report(errors)
                    return False

        # Positive price checks
        for col in numeric_positive:
            try:
                v = float(row[col])
                if v <= 0 or math.isnan(v) or math.isinf(v):
                    errors.append(f"Row {i}: {col} = {row[col]} (must be > 0, finite)")
            except (ValueError, TypeError):
                errors.append(f"Row {i}: {col} = '{row[col]}' is not a valid number")

        # Non-negative checks
        for col in numeric_non_negative:
            try:
                v = float(row[col])
                if v < 0 or math.isnan(v) or math.isinf(v):
                    errors.append(f"Row {i}: {col} = {row[col]} (must be >= 0, finite)")
            except (ValueError, TypeError):
                errors.append(f"Row {i}: {col} = '{row[col]}' is not a valid number")

        # trade_count integer check
        try:
            tc = int(row["trade_count"])
            if tc < 0:
                errors.append(f"Row {i}: trade_count = {tc} (must be >= 0)")
        except (ValueError, TypeError):
            errors.append(f"Row {i}: trade_count = '{row['trade_count']}' is not a valid integer")

        if len(errors) > 50:
            errors.append("... (truncated, too many data sanity errors)")
            _report(errors)
            return False

    if errors:
        _report(errors)
        return False

    # ---- Check 4: Parse timestamps ----
    timestamps: list[int] = []
    for i, row in enumerate(rows):
        try:
            ts = int(row["timestamp"])
            timestamps.append(ts)
        except (ValueError, TypeError):
            errors.append(f"Row {i}: timestamp = '{row['timestamp']}' is not a valid integer")

    if errors:
        _report(errors)
        return False

    # ---- Check 5: Sorting (strictly increasing) ----
    for i in range(1, len(timestamps)):
        if timestamps[i] <= timestamps[i - 1]:
            errors.append(
                f"Row {i}: Timestamp not strictly increasing "
                f"({timestamps[i]} <= {timestamps[i - 1]})"
            )
            if len(errors) > 20:
                errors.append("... (truncated)")
                break

    if errors:
        _report(errors)
        return False

    # ---- Check 6: Duplicate timestamps ----
    seen: set[int] = set()
    for i, ts in enumerate(timestamps):
        if ts in seen:
            errors.append(f"Row {i}: Duplicate timestamp detected ({ts})")
            if len(errors) > 20:
                errors.append("... (truncated)")
                break
        seen.add(ts)

    if errors:
        _report(errors)
        return False

    # ---- Check 7: Time Continuity ----
    expected_gap = INTERVAL_MS.get(interval)
    if expected_gap is None:
        errors.append(f"Unknown interval '{interval}' — cannot validate continuity")
        _report(errors)
        return False

    gap_errors = 0
    for i in range(1, len(timestamps) - 1):
        actual_gap = timestamps[i] - timestamps[i - 1]
        if actual_gap != expected_gap:
            gap_errors += 1
            if gap_errors <= 10:
                errors.append(
                    f"Row {i}: Missing candle gap — expected {expected_gap}ms, "
                    f"got {actual_gap}ms (delta: {actual_gap - expected_gap}ms)"
                )

    if gap_errors > 10:
        errors.append(f"... and {gap_errors - 10} more continuity errors")

    if errors:
        _report(errors)
        return False

    # ---- All checks passed ----
    print(f"[VALIDATOR] {path.name} — ALL CHECKS PASSED ({len(rows)} rows)")
    return True


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------
def _report(errors: list[str]):
    """Print all accumulated errors."""
    print(f"[VALIDATOR] FAILED — {len(errors)} error(s):")
    for err in errors:
        print(f"  [ERROR] {err}")
