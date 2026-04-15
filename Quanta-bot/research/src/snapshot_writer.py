"""
snapshot_writer.py — Atomic JSON Writer for Performance Snapshots

Single public function: write_snapshot()
Validates before writing. Uses tempfile + os.replace for atomicity.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config


def write_snapshot(snapshot: dict, path: str | None = None) -> None:
    """Atomically write a performance snapshot to JSON.

    Validates the snapshot before writing. On validation failure,
    writes the zero-state payload instead.

    Parameters
    ----------
    snapshot : dict
        Performance snapshot dict.
    path : str | None
        Output path. Defaults to config.SNAPSHOT_PATH.
    """
    file_path: str = path if path is not None else str(config.SNAPSHOT_PATH)

    if not _validate(snapshot):
        snapshot = build_zero_state()

    # Ensure parent directory exists
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: temp file → os.replace
    dir_name: str = str(Path(file_path).parent)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp_path, file_path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def build_zero_state() -> dict:
    """Build the zero-state (empty/failure) snapshot."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_trades": 0,
        "global_win_rate": 0.0,
        "average_rr": 0.0,
        "current_drawdown_pct": 0.0,
        "strategy_performance": [],
    }


def _validate(snapshot: dict) -> bool:
    """Validate snapshot before writing."""
    if not isinstance(snapshot, dict):
        return False

    # Required keys
    required = [
        "timestamp", "total_trades", "global_win_rate",
        "average_rr", "current_drawdown_pct", "strategy_performance",
    ]
    for key in required:
        if key not in snapshot:
            return False

    # global_win_rate in [0.0, 100.0]
    wr = snapshot.get("global_win_rate", -1)
    if not isinstance(wr, (int, float)) or wr < 0.0 or wr > 100.0:
        return False

    # current_drawdown_pct >= 0.0
    dd = snapshot.get("current_drawdown_pct", -1)
    if not isinstance(dd, (int, float)) or dd < 0.0:
        return False

    # total_trades >= 0
    tt = snapshot.get("total_trades", -1)
    if not isinstance(tt, int) or tt < 0:
        return False

    return True
