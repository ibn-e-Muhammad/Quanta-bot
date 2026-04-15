"""
trade_logger.py — SQLite Trade Journal & Decision Log Appender

Two public functions: log_trade() and log_decision().
Uses sqlite3 stdlib only. No SQLAlchemy.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config


# ---------------------------------------------------------------------------
# SQL Schema
# ---------------------------------------------------------------------------
_CREATE_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    size REAL NOT NULL,
    leverage_used REAL NOT NULL,
    entry_price REAL NOT NULL,
    sl_price REAL NOT NULL,
    tp_price REAL NOT NULL,
    risk_usd REAL NOT NULL,
    strategy_used TEXT,
    confidence_score REAL,
    reason TEXT
);
"""

_INSERT_SQL: str = """
INSERT INTO trades (
    timestamp, symbol, action, size, leverage_used,
    entry_price, sl_price, tp_price, risk_usd,
    strategy_used, confidence_score, reason
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


def log_trade(trade_data: dict, db_path: str | None = None) -> None:
    """Write a trade record to the SQLite trade journal.

    Parameters
    ----------
    trade_data : dict
        Must contain keys: timestamp, symbol, action, size, leverage_used,
        entry_price, sl_price, tp_price, risk_usd, strategy_used,
        confidence_score, reason.
    db_path : str | None
        Path to SQLite database. Defaults to config.TRADE_JOURNAL_PATH.
    """
    path: str = db_path if db_path is not None else str(config.TRADE_JOURNAL_PATH)

    # Ensure parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn: sqlite3.Connection = sqlite3.connect(path)
    try:
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute(_INSERT_SQL, (
            trade_data["timestamp"],
            trade_data["symbol"],
            trade_data["action"],
            trade_data["size"],
            trade_data["leverage_used"],
            trade_data["entry_price"],
            trade_data["sl_price"],
            trade_data["tp_price"],
            trade_data["risk_usd"],
            trade_data.get("strategy_used", ""),
            trade_data.get("confidence_score", 0.0),
            trade_data.get("reason", ""),
        ))
        conn.commit()
    finally:
        conn.close()


def log_decision(decision: dict, log_path: str | None = None) -> None:
    """Append a structured decision entry to the decision log.

    Parameters
    ----------
    decision : dict
        Must contain keys: timestamp, action, symbol, signal, strategy_used,
        size, leverage, rr, risk_usd, reason.
    log_path : str | None
        Path to decision log file. Defaults to config.DECISION_LOG_PATH.
    """
    path: str = log_path if log_path is not None else str(config.DECISION_LOG_PATH)

    # Ensure parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    ts: str = decision.get("timestamp", datetime.now(timezone.utc).isoformat())
    action: str = decision.get("action", "UNKNOWN")
    symbol: str = decision.get("symbol", "UNKNOWN")
    signal: str = decision.get("signal", "UNKNOWN")
    strategy: str = decision.get("strategy_used", "None")
    size: float = decision.get("size", 0.0)
    leverage: float = decision.get("leverage", 0.0)
    rr: float = decision.get("rr", 0.0)
    risk_usd: float = decision.get("risk_usd", 0.0)
    reason: str = decision.get("reason", "")

    entry_text: str = (
        f"\n---\n"
        f"### {ts} — {action} — {symbol}\n"
        f"- **Signal:** {signal}\n"
        f"- **Strategy:** {strategy}\n"
        f"- **Size:** {size} coins\n"
        f"- **Leverage:** {leverage}x\n"
        f"- **RR:** {rr}\n"
        f"- **Risk USD:** ${risk_usd:.2f}\n"
        f"- **Reason:** {reason}\n"
        f"---\n"
    )

    with open(path, "a", encoding="utf-8") as f:
        f.write(entry_text)
