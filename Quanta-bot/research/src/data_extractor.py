"""
data_extractor.py — SQLite Read-Only Query Engine

Single public function: extract_trades()
Opens with PRAGMA query_only = ON. Returns list[dict] or [] on any error.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from . import config


def extract_trades(db_path: str | None = None) -> list[dict]:
    """Extract trades from the last LOOKBACK_DAYS from the trade journal.

    Parameters
    ----------
    db_path : str | None
        Path to SQLite database. Defaults to config.TRADE_JOURNAL_PATH.

    Returns
    -------
    list[dict]
        List of trade dicts with keys: timestamp, symbol, action,
        strategy_used, risk_usd, pnl_usd. Empty list on any failure.
    """
    path: str = db_path if db_path is not None else str(config.TRADE_JOURNAL_PATH)

    try:
        conn: sqlite3.Connection = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True,
        )
        conn.execute("PRAGMA query_only = ON;")
        conn.row_factory = sqlite3.Row

        # Check if pnl_usd column exists
        if not _has_column(conn, "trades", "pnl_usd"):
            conn.close()
            return []

        # Query trades from last N days
        cutoff: str = (
            datetime.now(timezone.utc) - timedelta(days=config.LOOKBACK_DAYS)
        ).isoformat()

        cursor = conn.execute(
            """
            SELECT timestamp, symbol, action, strategy_used,
                   risk_usd, pnl_usd
            FROM trades
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (cutoff,),
        )

        trades: list[dict] = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return trades

    except (sqlite3.Error, OSError):
        return []


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        columns: list[str] = [row["name"] for row in cursor.fetchall()]
        return column in columns
    except sqlite3.Error:
        return False
