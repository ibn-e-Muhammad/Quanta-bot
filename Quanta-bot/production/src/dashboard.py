from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from live_telemetry import default_db_path


def resolve_db_path() -> str:
    return str(Path(default_db_path()).resolve())


def resolve_db_uri() -> str:
    db_posix = Path(resolve_db_path()).as_posix()
    return f"file:{db_posix}?mode=ro"


def _read_sql(query: str, params: tuple | None = None) -> pd.DataFrame:
    uri = resolve_db_uri()
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only = ON;")
        return pd.read_sql_query(query, conn, params=params)


@st.cache_data(ttl=5)
def load_portfolio_health() -> pd.DataFrame:
    query = """
    SELECT
        COUNT(*) AS closed_positions,
        COALESCE(SUM(pnl), 0.0) AS total_pnl,
        COALESCE(
            100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
            0.0
        ) AS win_rate_pct
    FROM positions
    WHERE pnl IS NOT NULL
      AND (
        UPPER(COALESCE(status, '')) IN ('CLOSED', 'FILLED', 'EXITED')
        OR closed_at IS NOT NULL
      )
    """
    return _read_sql(query)


@st.cache_data(ttl=5)
def load_active_positions() -> pd.DataFrame:
    query = """
    SELECT
        symbol,
        entry_price,
        tp_price,
        sl_price,
        status,
        pnl,
        opened_at
    FROM positions
    WHERE closed_at IS NULL
      AND UPPER(COALESCE(status, 'OPEN')) NOT IN ('CLOSED', 'EXITED', 'CANCELLED')
    ORDER BY COALESCE(updated_at, opened_at, created_at) DESC
    """
    return _read_sql(query)


@st.cache_data(ttl=5)
def load_live_decision_feed() -> pd.DataFrame:
    query = """
    WITH latest_gate AS (
        SELECT g.*
        FROM gate_evaluations g
        INNER JOIN (
            SELECT signal_id, MAX(gate_eval_id) AS max_gate_eval_id
            FROM gate_evaluations
            GROUP BY signal_id
        ) x
          ON x.signal_id = g.signal_id
         AND x.max_gate_eval_id = g.gate_eval_id
    )
    SELECT
        le.timestamp,
        le.symbol,
        le.side,
        le.expected_price,
        le.actual_fill_price,
        le.slippage_pct,
        le.latency_ms,
        COALESCE(CAST(lg.ml_prob AS TEXT), 'N/A') AS ml_score,
        COALESCE(CAST(lg.risk_pressure AS TEXT), 'N/A') AS risk_pressure,
        COALESCE(lg.microstructure_regime, 'N/A') AS regime
    FROM live_executions le
    LEFT JOIN latest_gate lg
      ON lg.signal_id = le.signal_id
    ORDER BY le.timestamp DESC
    LIMIT 10
    """
    return _read_sql(query)


@st.cache_data(ttl=5)
def load_execution_telemetry() -> pd.DataFrame:
    query = """
    SELECT
        COUNT(*) AS executions,
        COALESCE(AVG(latency_ms), 0.0) AS avg_latency_ms,
        COALESCE(AVG(slippage_pct), 0.0) AS avg_slippage_pct
    FROM live_executions
    """
    return _read_sql(query)


@st.cache_data(ttl=5)
def load_veto_diagnostics() -> pd.DataFrame:
    query = """
    SELECT
        timestamp,
        symbol,
        COALESCE(microstructure_regime, 'N/A') AS microstructure_regime,
        COALESCE(CAST(ml_prob AS TEXT), 'N/A') AS ml_prob,
        COALESCE(veto_reason, 'N/A') AS veto_reason
    FROM gate_evaluations
    WHERE final_decision = 'VETO'
    ORDER BY timestamp DESC
    LIMIT 20
    """
    return _read_sql(query)


def render_portfolio_health() -> None:
    st.subheader("📊 Portfolio Health")
    df = load_portfolio_health()
    if df.empty:
        st.info("No data yet - Waiting for first 4H candle close")
        return

    row = df.iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Total PnL", f"{float(row['total_pnl']):.4f}")
    c2.metric("Win Rate", f"{float(row['win_rate_pct']):.2f}%")
    c3.metric("Closed Positions", int(row["closed_positions"]))


def render_active_positions() -> None:
    st.subheader("🟢 Active Positions")
    df = load_active_positions()
    if df.empty:
        st.info("No data yet - Waiting for first 4H candle close")
        df = pd.DataFrame(columns=["symbol", "entry_price", "tp_price", "sl_price", "status", "pnl", "opened_at"])
    st.dataframe(df, use_container_width=True)


def render_live_decision_feed() -> None:
    st.subheader("🧠 Live Decision Feed (The Brain)")
    df = load_live_decision_feed()
    if df.empty:
        st.info("No data yet - Waiting for first 4H candle close")
        df = pd.DataFrame(
            columns=[
                "timestamp",
                "symbol",
                "side",
                "expected_price",
                "actual_fill_price",
                "slippage_pct",
                "latency_ms",
                "ml_score",
                "risk_pressure",
                "regime",
            ]
        )
    st.dataframe(df, use_container_width=True)


def render_execution_telemetry() -> None:
    st.subheader("⏱️ Execution Telemetry")
    df = load_execution_telemetry()
    if df.empty:
        st.info("No data yet - Waiting for first 4H candle close")
        return

    row = df.iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Executions", int(row["executions"]))
    c2.metric("Avg Latency (ms)", f"{float(row['avg_latency_ms']):.2f}")
    c3.metric("Avg Slippage (%)", f"{float(row['avg_slippage_pct']):.6f}")


def render_wtf_panel() -> None:
    st.subheader("🚨 The WTF Panel (Gate Diagnostics)")
    df = load_veto_diagnostics()
    if df.empty:
        st.info("No data yet - Waiting for first 4H candle close")
        df = pd.DataFrame(columns=["timestamp", "symbol", "microstructure_regime", "ml_prob", "veto_reason"])
    st.dataframe(df, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Quanta Control Room", layout="wide")
    st.title("⚙️ Quanta Bot — Streamlit Control Room")
    st.caption("Read-only dashboard backed by live_telemetry.sqlite")
    st.caption(f"Telemetry DB: {resolve_db_path()}")

    render_portfolio_health()
    st.divider()
    render_active_positions()
    st.divider()
    render_live_decision_feed()
    st.divider()
    render_execution_telemetry()
    st.divider()
    render_wtf_panel()


if __name__ == "__main__":
    main()
