from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class LiveTelemetryStore:
    """SQLite-backed truth engine store for live orchestration telemetry."""

    db_path: str

    def connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with FK checks enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def initialize(self) -> None:
        """Create all telemetry tables and indexes if missing."""
        with self.connect() as conn:
            conn.executescript(self.schema_sql())
            conn.commit()

    @staticmethod
    def schema_sql() -> str:
        """Return canonical DDL for the Phase 8.1 telemetry truth engine."""
        return """
        CREATE TABLE IF NOT EXISTS market_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL DEFAULT '4h',
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            adx REAL,
            atr REAL,
            regime_features_json TEXT,
            source_event_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timestamp)
        );

        CREATE INDEX IF NOT EXISTS idx_market_snapshots_symbol_ts
            ON market_snapshots(symbol, timestamp DESC);

        CREATE TABLE IF NOT EXISTS signals_generated (
            signal_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy_name TEXT,
            signal_side TEXT,
            raw_score REAL,
            priority_rank INTEGER,
            snapshot_id INTEGER,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (snapshot_id) REFERENCES market_snapshots(snapshot_id)
        );

        CREATE INDEX IF NOT EXISTS idx_signals_generated_symbol_ts
            ON signals_generated(symbol, timestamp DESC);

        CREATE TABLE IF NOT EXISTS gate_evaluations (
            gate_eval_id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            microstructure_regime TEXT,
            risk_pressure REAL,
            ml_prob REAL,
            ml_adjusted REAL,
            threshold_applied REAL,
            final_decision TEXT NOT NULL CHECK (final_decision IN ('EXECUTE', 'VETO')),
            veto_reason TEXT,
            details_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (signal_id) REFERENCES signals_generated(signal_id)
        );

        CREATE INDEX IF NOT EXISTS idx_gate_evaluations_signal
            ON gate_evaluations(signal_id);

        CREATE INDEX IF NOT EXISTS idx_gate_evaluations_symbol_ts
            ON gate_evaluations(symbol, timestamp DESC);

        CREATE TABLE IF NOT EXISTS live_executions (
            execution_id TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            expected_price REAL,
            actual_fill_price REAL,
            slippage_pct REAL,
            latency_ms INTEGER,
            exchange_order_id TEXT,
            order_status TEXT,
            raw_exchange_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (signal_id) REFERENCES signals_generated(signal_id)
        );

        CREATE INDEX IF NOT EXISTS idx_live_executions_symbol_ts
            ON live_executions(symbol, timestamp DESC);

        CREATE INDEX IF NOT EXISTS idx_live_executions_signal
            ON live_executions(signal_id);

        CREATE TABLE IF NOT EXISTS positions (
            position_id TEXT PRIMARY KEY,
            signal_id TEXT,
            symbol TEXT NOT NULL,
            entry_price REAL,
            quantity REAL,
            tp_price REAL,
            sl_price REAL,
            status TEXT NOT NULL,
            pnl REAL,
            opened_at TEXT,
            closed_at TEXT,
            details_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY (signal_id) REFERENCES signals_generated(signal_id)
        );

        CREATE INDEX IF NOT EXISTS idx_positions_symbol_status
            ON positions(symbol, status);
        """

    def insert_market_snapshot(self, payload: dict[str, Any]) -> int | None:
        """Insert one market snapshot row; returns rowid or None if deduplicated."""
        sql = """
        INSERT OR IGNORE INTO market_snapshots (
            timestamp, symbol, interval, open, high, low, close, volume,
            adx, atr, regime_features_json, source_event_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            payload.get("timestamp"),
            payload.get("symbol"),
            payload.get("interval", "4h"),
            payload.get("open"),
            payload.get("high"),
            payload.get("low"),
            payload.get("close"),
            payload.get("volume", 0.0),
            payload.get("adx"),
            payload.get("atr"),
            json.dumps(payload.get("regime_features", {})),
            json.dumps(payload.get("source_event", {})),
        )
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid if cur.rowcount > 0 else None

    def insert_signal(self, payload: dict[str, Any]) -> None:
        """Insert a generated signal row."""
        sql = """
        INSERT OR REPLACE INTO signals_generated (
            signal_id, timestamp, symbol, strategy_name, signal_side,
            raw_score, priority_rank, snapshot_id, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            payload.get("signal_id"),
            payload.get("timestamp"),
            payload.get("symbol"),
            payload.get("strategy_name"),
            payload.get("signal_side"),
            payload.get("raw_score"),
            payload.get("priority_rank"),
            payload.get("snapshot_id"),
            json.dumps(payload.get("metadata", {})),
        )
        with self.connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def insert_gate_evaluation(self, payload: dict[str, Any]) -> None:
        """Insert one gate evaluation row for a signal."""
        sql = """
        INSERT INTO gate_evaluations (
            signal_id, timestamp, symbol, microstructure_regime, risk_pressure,
            ml_prob, ml_adjusted, threshold_applied, final_decision, veto_reason, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            payload.get("signal_id"),
            payload.get("timestamp"),
            payload.get("symbol"),
            payload.get("microstructure_regime"),
            payload.get("risk_pressure"),
            payload.get("ml_prob"),
            payload.get("ml_adjusted"),
            payload.get("threshold_applied"),
            payload.get("final_decision"),
            payload.get("veto_reason"),
            json.dumps(payload.get("details", {})),
        )
        with self.connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def insert_execution(self, payload: dict[str, Any]) -> None:
        """Insert one live execution record."""
        sql = """
        INSERT OR REPLACE INTO live_executions (
            execution_id, signal_id, timestamp, symbol, side,
            expected_price, actual_fill_price, slippage_pct, latency_ms,
            exchange_order_id, order_status, raw_exchange_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            payload.get("execution_id"),
            payload.get("signal_id"),
            payload.get("timestamp"),
            payload.get("symbol"),
            payload.get("side"),
            payload.get("expected_price"),
            payload.get("actual_fill_price"),
            payload.get("slippage_pct"),
            payload.get("latency_ms"),
            payload.get("exchange_order_id"),
            payload.get("order_status"),
            json.dumps(payload.get("raw_exchange", {})),
        )
        with self.connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def upsert_position(self, payload: dict[str, Any]) -> None:
        """Upsert one position lifecycle record."""
        sql = """
        INSERT INTO positions (
            position_id, signal_id, symbol, entry_price, quantity,
            tp_price, sl_price, status, pnl, opened_at, closed_at,
            details_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(position_id) DO UPDATE SET
            signal_id=excluded.signal_id,
            symbol=excluded.symbol,
            entry_price=excluded.entry_price,
            quantity=excluded.quantity,
            tp_price=excluded.tp_price,
            sl_price=excluded.sl_price,
            status=excluded.status,
            pnl=excluded.pnl,
            opened_at=excluded.opened_at,
            closed_at=excluded.closed_at,
            details_json=excluded.details_json,
            updated_at=CURRENT_TIMESTAMP
        """
        params = (
            payload.get("position_id"),
            payload.get("signal_id"),
            payload.get("symbol"),
            payload.get("entry_price"),
            payload.get("quantity"),
            payload.get("tp_price"),
            payload.get("sl_price"),
            payload.get("status"),
            payload.get("pnl"),
            payload.get("opened_at"),
            payload.get("closed_at"),
            json.dumps(payload.get("details", {})),
        )
        with self.connect() as conn:
            conn.execute(sql, params)
            conn.commit()


def default_db_path() -> str:
    """Return default production telemetry DB path."""
    root = Path(__file__).resolve().parents[2]
    db_dir = root / "production" / "runtime"
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "live_telemetry.sqlite")
