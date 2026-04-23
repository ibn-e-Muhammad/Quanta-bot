"""
Quanta Glass Window -- Institutional Live Dashboard
====================================================
Strictly READ-ONLY. Pulls from live_telemetry.sqlite and Binance Testnet API.
Auto-refreshes via st.fragment for flicker-free updates.

Launch:
    cd Quanta-bot/production
    streamlit run src/dashboard.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

# No longer using st_autorefresh - refactored to st.fragment for flicker-free updates

from live_telemetry import default_db_path

# ═══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

TESTNET_BASE = "https://testnet.binancefuture.com"
REFRESH_INTERVAL_MS = 4000
INITIAL_BALANCE = 5000.0

_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _ROOT / ".env"

# ═══════════════════════════════════════════════════════════════════════
# DESIGN SYSTEM - CSS
# ═══════════════════════════════════════════════════════════════════════

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --bg-primary: #0a0e17;
    --bg-card: rgba(16, 22, 36, 0.85);
    --bg-card-hover: rgba(22, 30, 48, 0.9);
    --border-subtle: rgba(99, 115, 155, 0.15);
    --border-glow: rgba(56, 189, 248, 0.25);
    --text-primary: #e2e8f0;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --accent-cyan: #38bdf8;
    --accent-emerald: #34d399;
    --accent-rose: #fb7185;
    --accent-amber: #fbbf24;
    --accent-violet: #a78bfa;
    --gradient-hero: linear-gradient(135deg, #0ea5e9 0%, #8b5cf6 50%, #ec4899 100%);
    --gradient-card: linear-gradient(145deg, rgba(16,22,36,0.95), rgba(12,17,28,0.95));
    --glass-bg: rgba(15, 23, 42, 0.6);
    --glass-border: rgba(148, 163, 184, 0.1);
}

/* Global resets */
.stApp {
    background: var(--bg-primary) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

.stApp > header { background: transparent !important; }

/* Metric cards */
[data-testid="stMetric"] {
    background: var(--gradient-card) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 16px !important;
    padding: 20px 24px !important;
    backdrop-filter: blur(20px) !important;
    box-shadow: 0 4px 24px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.04) !important;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
}
[data-testid="stMetric"]:hover {
    border-color: var(--border-glow) !important;
    box-shadow: 0 8px 32px rgba(56,189,248,0.1), inset 0 1px 0 rgba(255,255,255,0.06) !important;
    transform: translateY(-2px) !important;
}
[data-testid="stMetricLabel"] {
    color: var(--text-muted) !important;
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.08em !important;
}
[data-testid="stMetricValue"] {
    color: var(--text-primary) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-weight: 700 !important;
    font-size: 1.65rem !important;
}
[data-testid="stMetricDelta"] > div {
    font-family: 'JetBrains Mono', monospace !important;
    font-weight: 600 !important;
}

/* Dataframes */
[data-testid="stDataFrame"], .stDataFrame {
    border-radius: 12px !important;
    overflow: hidden !important;
    border: 1px solid var(--glass-border) !important;
}
[data-testid="stDataFrame"] table {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.82rem !important;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px !important;
    background: rgba(15, 23, 42, 0.5) !important;
    border-radius: 12px !important;
    padding: 4px !important;
    border: 1px solid var(--glass-border) !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px !important;
    color: var(--text-secondary) !important;
    font-weight: 500 !important;
    padding: 8px 20px !important;
    font-size: 0.85rem !important;
}
.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, rgba(56,189,248,0.15), rgba(139,92,246,0.15)) !important;
    color: var(--accent-cyan) !important;
    border: 1px solid rgba(56,189,248,0.2) !important;
}

/* Section headers */
.section-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 28px 0 16px 0;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border-subtle);
}
.section-header .icon {
    width: 40px;
    height: 40px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.2rem;
    flex-shrink: 0;
}
.section-header .title {
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.01em;
}
.section-header .subtitle {
    font-size: 0.78rem;
    color: var(--text-muted);
    font-weight: 400;
    margin-top: 2px;
}

/* Hero banner */
.hero-banner {
    background: linear-gradient(135deg, rgba(14,165,233,0.08) 0%, rgba(139,92,246,0.08) 50%, rgba(236,72,153,0.06) 100%);
    border: 1px solid rgba(56,189,248,0.12);
    border-radius: 20px;
    padding: 24px 32px;
    margin-bottom: 24px;
    backdrop-filter: blur(20px);
}
.hero-title {
    font-size: 1.8rem;
    font-weight: 800;
    background: var(--gradient-hero);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.02em;
    margin-bottom: 4px;
}
.hero-sub {
    color: var(--text-secondary);
    font-size: 0.85rem;
    font-weight: 400;
}

/* Status pills */
.pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    font-family: 'JetBrains Mono', monospace;
}
.pill-live { background: rgba(52,211,153,0.15); color: #34d399; border: 1px solid rgba(52,211,153,0.3); }
.pill-safe { background: rgba(52,211,153,0.12); color: #34d399; }
.pill-warning { background: rgba(251,191,36,0.12); color: #fbbf24; }
.pill-notrade { background: rgba(251,113,133,0.12); color: #fb7185; }

/* Heartbeat pulse */
.heartbeat-dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #34d399;
    box-shadow: 0 0 8px rgba(52,211,153,0.6);
    animation: pulse 2s ease-in-out infinite;
    margin-right: 8px;
    vertical-align: middle;
}
@keyframes pulse {
    0%, 100% { opacity: 1; box-shadow: 0 0 8px rgba(52,211,153,0.6); }
    50% { opacity: 0.5; box-shadow: 0 0 16px rgba(52,211,153,0.3); }
}

/* Gate bar chart */
.gate-bar-container {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 6px 0;
}
.gate-bar {
    height: 28px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.72rem;
    font-weight: 700;
    font-family: 'JetBrains Mono', monospace;
    color: white;
    min-width: 40px;
    transition: all 0.3s ease;
}
.gate-bar:hover { filter: brightness(1.2); transform: scaleY(1.05); }
.gate-bar-safe { background: linear-gradient(135deg, #059669, #34d399); }
.gate-bar-warning { background: linear-gradient(135deg, #d97706, #fbbf24); }
.gate-bar-veto { background: linear-gradient(135deg, #e11d48, #fb7185); }

/* Divider */
.glass-divider {
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--glass-border), transparent);
    margin: 24px 0;
}
</style>
"""


# ═══════════════════════════════════════════════════════════════════════
# DB HELPERS (read-only)
# ═══════════════════════════════════════════════════════════════════════

def _resolve_db_path() -> str:
    return str(Path(default_db_path()).resolve())


def _resolve_db_uri() -> str:
    return f"file:{Path(_resolve_db_path()).as_posix()}?mode=ro"


def _read_sql(query: str, params: tuple | None = None) -> pd.DataFrame:
    uri = _resolve_db_uri()
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.execute("PRAGMA query_only = ON;")
            return pd.read_sql_query(query, conn, params=params)
    except Exception:
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════
# BINANCE TESTNET API HELPERS (read-only, HMAC-signed)
# ═══════════════════════════════════════════════════════════════════════

def _load_env_keys() -> tuple[str, str]:
    """Load API keys from .env file."""
    api_key, api_secret = "", ""
    if _ENV_PATH.exists():
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k == "BINANCE_TESTNET_API_KEY":
                        api_key = v
                    elif k == "BINANCE_TESTNET_API_SECRET":
                        api_secret = v
    return api_key, api_secret


def _signed_get(path: str, params: dict | None = None) -> dict | list | None:
    """Perform read-only signed GET against Binance Futures Testnet."""
    api_key, api_secret = _load_env_keys()
    if not api_key or not api_secret:
        return None
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    query = urllib.parse.urlencode(params, doseq=True)
    signature = hmac.new(
        api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    url = f"{TESTNET_BASE}{path}?{query}&signature={signature}"
    req = urllib.request.Request(url=url, method="GET")
    req.add_header("X-MBX-APIKEY", api_key)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


@st.cache_data(ttl=4)
def fetch_account_info() -> dict[str, Any]:
    """Fetch Binance Testnet Futures account info."""
    data = _signed_get("/fapi/v2/account")
    if not data or not isinstance(data, dict):
        return {}
    return data


@st.cache_data(ttl=4)
def fetch_open_positions() -> list[dict[str, Any]]:
    """Fetch all position risk data from testnet."""
    data = _signed_get("/fapi/v2/positionRisk")
    if not data or not isinstance(data, list):
        return []
    return [p for p in data if float(p.get("positionAmt", 0)) != 0]


@st.cache_data(ttl=4)
def fetch_open_orders() -> list[dict[str, Any]]:
    """Fetch all open orders from testnet."""
    data = _signed_get("/fapi/v1/openOrders")
    if not data or not isinstance(data, list):
        return []
    return data


# ═══════════════════════════════════════════════════════════════════════
# DATA LOADERS (Telemetry DB)
# ═══════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=4)
def load_funnel_metrics() -> dict[str, int]:
    df = _read_sql("""
    WITH latest_gate AS (
        SELECT g.signal_id, g.final_decision
        FROM gate_evaluations g
        INNER JOIN (
            SELECT signal_id, MAX(gate_eval_id) AS mx
            FROM gate_evaluations GROUP BY signal_id
        ) x ON x.signal_id = g.signal_id AND x.mx = g.gate_eval_id
    )
    SELECT
        (SELECT COUNT(*) FROM market_snapshots) AS candles,
        (SELECT COUNT(*) FROM signals_generated) AS signals,
        (SELECT COUNT(*) FROM latest_gate WHERE final_decision = 'EXECUTE') AS approvals,
        (SELECT COUNT(*) FROM latest_gate WHERE final_decision = 'VETO') AS vetoes,
        (SELECT COUNT(*) FROM live_executions) AS fills
    """)
    if df.empty:
        return {"candles": 0, "signals": 0, "approvals": 0, "vetoes": 0, "fills": 0}
    return df.iloc[0].to_dict()


@st.cache_data(ttl=4)
def load_gate_regime_breakdown() -> pd.DataFrame:
    return _read_sql("""
    WITH latest_gate AS (
        SELECT g.*
        FROM gate_evaluations g
        INNER JOIN (
            SELECT signal_id, MAX(gate_eval_id) AS mx
            FROM gate_evaluations GROUP BY signal_id
        ) x ON x.signal_id = g.signal_id AND x.mx = g.gate_eval_id
    )
    SELECT
        COALESCE(microstructure_regime, 'UNKNOWN') AS regime,
        final_decision,
        COUNT(*) AS cnt,
        ROUND(AVG(risk_pressure), 4) AS avg_risk_pressure,
        ROUND(AVG(ml_prob), 4) AS avg_ml_prob,
        ROUND(AVG(ml_adjusted), 4) AS avg_ml_adjusted,
        ROUND(AVG(threshold_applied), 4) AS avg_threshold
    FROM latest_gate
    GROUP BY regime, final_decision
    ORDER BY regime, final_decision
    """)


@st.cache_data(ttl=4)
def load_gate_decision_counts() -> dict[str, int]:
    """Return SAFE/WARNING/NO_TRADE counts from gate details."""
    df = _read_sql("""
    SELECT details_json FROM gate_evaluations
    WHERE details_json IS NOT NULL
    ORDER BY gate_eval_id DESC LIMIT 200
    """)
    safe = warning = no_trade = 0
    if not df.empty:
        for row in df.itertuples():
            try:
                details = json.loads(row.details_json)
                regime = str(details.get("gate_reason", "") or details.get("reason", "")).upper()
                scores = details.get("gate_scores", {}) or details.get("scores", {})
                risk_p = float(scores.get("risk_pressure", details.get("risk_pressure", 0.0)) or 0.0)
                # Classification: matches gate thresholds
                if "NO_TRADE" in regime or "BLOCKED" in regime:
                    no_trade += 1
                elif "WARNING" in regime or risk_p > 0.40:
                    warning += 1
                else:
                    safe += 1
            except Exception:
                safe += 1
    return {"SAFE": safe, "WARNING": warning, "NO_TRADE": no_trade}


@st.cache_data(ttl=4)
def load_recent_signals() -> pd.DataFrame:
    return _read_sql("""
    WITH latest_gate AS (
        SELECT g.*
        FROM gate_evaluations g
        INNER JOIN (
            SELECT signal_id, MAX(gate_eval_id) AS mx
            FROM gate_evaluations GROUP BY signal_id
        ) x ON x.signal_id = g.signal_id AND x.mx = g.gate_eval_id
    )
    SELECT
        rs.timestamp,
        rs.symbol,
        rs.signal_side AS side,
        COALESCE(rs.strategy_name, 'N/A') AS strategy,
        ROUND(COALESCE(rs.raw_score, 0), 4) AS raw_score,
        COALESCE(lg.microstructure_regime, '-') AS regime,
        ROUND(COALESCE(lg.risk_pressure, 0), 4) AS risk_pressure,
        ROUND(COALESCE(lg.ml_prob, 0), 4) AS ml_prob,
        ROUND(COALESCE(lg.ml_adjusted, 0), 4) AS ml_adjusted,
        ROUND(COALESCE(lg.threshold_applied, 0), 4) AS threshold,
        COALESCE(lg.final_decision, 'PENDING') AS decision,
        COALESCE(lg.veto_reason, '-') AS veto_reason
    FROM signals_generated rs
    LEFT JOIN latest_gate lg ON lg.signal_id = rs.signal_id
    ORDER BY rs.timestamp DESC
    LIMIT 30
    """)


@st.cache_data(ttl=4)
def load_recent_executions() -> pd.DataFrame:
    return _read_sql("""
    SELECT
        le.timestamp,
        le.symbol,
        le.side,
        ROUND(le.expected_price, 4) AS expected_price,
        ROUND(le.actual_fill_price, 4) AS fill_price,
        ROUND(le.slippage_pct, 6) AS slippage_pct,
        le.latency_ms,
        le.order_status AS status
    FROM live_executions le
    ORDER BY le.timestamp DESC
    LIMIT 20
    """)


@st.cache_data(ttl=4)
def load_heartbeat() -> dict[str, Any]:
    """Get last DB write timestamps for heartbeat check."""
    snap = _read_sql("SELECT MAX(created_at) AS ts FROM market_snapshots")
    sig = _read_sql("SELECT MAX(created_at) AS ts FROM signals_generated")
    gate = _read_sql("SELECT MAX(created_at) AS ts FROM gate_evaluations")
    exe = _read_sql("SELECT MAX(created_at) AS ts FROM live_executions")
    candle = _read_sql("SELECT MAX(processed_at) AS ts FROM processed_candles")
    return {
        "last_snapshot": snap.iloc[0]["ts"] if not snap.empty and snap.iloc[0]["ts"] else None,
        "last_signal": sig.iloc[0]["ts"] if not sig.empty and sig.iloc[0]["ts"] else None,
        "last_gate": gate.iloc[0]["ts"] if not gate.empty and gate.iloc[0]["ts"] else None,
        "last_execution": exe.iloc[0]["ts"] if not exe.empty and exe.iloc[0]["ts"] else None,
        "last_candle": candle.iloc[0]["ts"] if not candle.empty and candle.iloc[0]["ts"] else None,
    }


@st.cache_data(ttl=4)
def load_execution_stats() -> dict[str, float]:
    df = _read_sql("""
    SELECT
        COUNT(*) AS total,
        COALESCE(AVG(latency_ms), 0) AS avg_latency,
        COALESCE(AVG(slippage_pct), 0) AS avg_slippage,
        COALESCE(MAX(latency_ms), 0) AS max_latency
    FROM live_executions
    """)
    if df.empty:
        return {"total": 0, "avg_latency": 0, "avg_slippage": 0, "max_latency": 0}
    return df.iloc[0].to_dict()


# ═══════════════════════════════════════════════════════════════════════
# RENDER HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _section_header(icon: str, title: str, subtitle: str, color: str = "#38bdf8") -> None:
    st.markdown(f"""
    <div class="section-header">
        <div class="icon" style="background: {color}22; color: {color};">{icon}</div>
        <div>
            <div class="title">{title}</div>
            <div class="subtitle">{subtitle}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _glass_divider() -> None:
    st.markdown('<div class="glass-divider"></div>', unsafe_allow_html=True)


def _pnl_color(val: float) -> str:
    return "var(--accent-emerald)" if val >= 0 else "var(--accent-rose)"


# ═══════════════════════════════════════════════════════════════════════
# RENDER SECTIONS
# ═══════════════════════════════════════════════════════════════════════

def render_hero() -> None:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    st.markdown(f"""
    <div class="hero-banner">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
                <div class="hero-title">Quanta Glass Window</div>
                <div class="hero-sub">Institutional Control Room &mdash; Phase 10 Sniper v1 Production &mdash; Binance Futures Testnet &mdash; 4H Grid</div>
            </div>
            <div style="text-align:right;">
                <span class="pill pill-live"><span class="heartbeat-dot"></span> LIVE</span>
                <div style="color: var(--text-muted); font-size: 0.75rem; margin-top: 6px; font-family: 'JetBrains Mono', monospace;">{now_str}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


@st.fragment(run_every=5)
def render_account_hud() -> None:
    _section_header("🛡️", "Layer 4: Risk Strategy (Survival)", "Account drawdown, margin constraints, and session safety", "#38bdf8")

    account = fetch_account_info()
    if not account:
        st.info("Waiting for Binance Testnet API response...")
        return

    balance = float(account.get("totalWalletBalance", 0))
    equity = float(account.get("totalMarginBalance", 0))
    unrealized = float(account.get("totalUnrealizedProfit", 0))
    margin_used = float(account.get("totalInitialMargin", 0))
    avail_balance = float(account.get("availableBalance", 0))
    margin_pct = (margin_used / equity * 100.0) if equity > 0 else 0.0
    session_pnl = equity - INITIAL_BALANCE
    session_pnl_pct = (session_pnl / INITIAL_BALANCE * 100.0) if INITIAL_BALANCE > 0 else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Wallet Balance", f"${balance:,.2f}", delta=f"{session_pnl:+,.2f}")
    c2.metric("Total Equity", f"${equity:,.2f}", delta=f"{session_pnl_pct:+.2f}%")
    c3.metric("Unrealized PnL", f"${unrealized:,.2f}")
    c4.metric("Margin Used", f"${margin_used:,.2f}", delta=f"{margin_pct:.1f}%")
    c5.metric("Available", f"${avail_balance:,.2f}")


@st.fragment(run_every=5)
def render_positions_grid() -> None:
    _section_header("📡", "Layer 3: Trade Lifecycle", "Live open positions and conditional trailing/stop activation states", "#34d399")

    positions = fetch_open_positions()
    if not positions:
        st.markdown("""
        <div style="text-align:center; padding: 40px; color: var(--text-muted); font-size: 0.9rem;">
            No open positions. Waiting for next candle close...
        </div>
        """, unsafe_allow_html=True)
        return

    rows = []
    for p in positions:
        amt = float(p.get("positionAmt", 0))
        entry = float(p.get("entryPrice", 0))
        mark = float(p.get("markPrice", 0))
        upnl = float(p.get("unRealizedProfit", 0))
        notional = abs(float(p.get("notional", 0)))
        leverage = p.get("leverage", "1")
        upnl_pct = (upnl / (abs(amt) * entry) * 100.0) if (amt != 0 and entry > 0) else 0.0
        side = "LONG" if amt > 0 else "SHORT"
        rows.append({
            "Symbol": p.get("symbol", ""),
            "Side": side,
            "Size": abs(amt),
            "Leverage": f"{leverage}x",
            "Entry": f"${entry:,.4f}",
            "Mark Price": f"${mark:,.4f}",
            "Notional": f"${notional:,.2f}",
            "uPnL ($)": f"${upnl:+,.2f}",
            "uPnL (%)": f"{upnl_pct:+.2f}%",
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "uPnL ($)": st.column_config.TextColumn("uPnL ($)"),
            "uPnL (%)": st.column_config.TextColumn("uPnL (%)"),
        },
    )

    open_orders = fetch_open_orders()
    if open_orders:
        st.caption(f"Open orders on exchange: {len(open_orders)}")


@st.fragment(run_every=5)
def render_intelligence_feed() -> None:
    _section_header("🧠", "Layer 1: Decision Layer", "Bot Brain: ML Probabilities, Risk Pressure, and Gate Decisions", "#a78bfa")

    tab_gates, tab_regime, tab_signals = st.tabs(["Gate Statistics", "Regime Breakdown", "Signal Trace"])

    with tab_gates:
        gate_counts = load_gate_decision_counts()
        total = sum(gate_counts.values())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Evaluations", total)
        c2.metric("SAFE", gate_counts["SAFE"])
        c3.metric("WARNING", gate_counts["WARNING"])
        c4.metric("NO_TRADE", gate_counts["NO_TRADE"])

        if total > 0:
            safe_pct = gate_counts["SAFE"] / total * 100
            warn_pct = gate_counts["WARNING"] / total * 100
            nt_pct = gate_counts["NO_TRADE"] / total * 100
            st.markdown(f"""
            <div style="margin:16px 0 8px 0; font-size:0.78rem; color:var(--text-muted); font-weight:600; text-transform:uppercase; letter-spacing:0.08em;">
                Gate Classification Distribution (last 200)
            </div>
            <div class="gate-bar-container">
                <div class="gate-bar gate-bar-safe" style="width:{max(safe_pct, 3)}%; flex-grow:0;">{gate_counts['SAFE']}</div>
                <div class="gate-bar gate-bar-warning" style="width:{max(warn_pct, 3)}%; flex-grow:0;">{gate_counts['WARNING']}</div>
                <div class="gate-bar gate-bar-veto" style="width:{max(nt_pct, 3)}%; flex-grow:0;">{gate_counts['NO_TRADE']}</div>
            </div>
            <div style="display:flex; gap:24px; margin-top:8px; font-size:0.72rem; color:var(--text-muted); font-family:'JetBrains Mono', monospace;">
                <span style="color:#34d399;">■ SAFE {safe_pct:.1f}%</span>
                <span style="color:#fbbf24;">■ WARNING {warn_pct:.1f}%</span>
                <span style="color:#fb7185;">■ NO_TRADE {nt_pct:.1f}%</span>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("""
        <div style="margin-top:16px; padding:12px 16px; background: rgba(251,191,36,0.06); border:1px solid rgba(251,191,36,0.15); border-radius:10px; font-size:0.8rem; color:var(--accent-amber);">
            <strong>Smoke Test Override Active:</strong> gate_safe_threshold=2.0, ml_threshold=0.0, interval=15m, balance=LIVE &mdash; all signals should classify as SAFE
        </div>
        """, unsafe_allow_html=True)

    with tab_regime:
        regime_df = load_gate_regime_breakdown()
        if regime_df.empty:
            st.info("No gate evaluation data yet")
        else:
            st.dataframe(regime_df, use_container_width=True, hide_index=True)

    with tab_signals:
        signals_df = load_recent_signals()
        if signals_df.empty:
            st.info("No signal data yet -- waiting for first candle close")
        else:
            st.dataframe(
                signals_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "ml_prob": st.column_config.NumberColumn("ML Prob", format="%.4f"),
                    "ml_adjusted": st.column_config.NumberColumn("ML Adj", format="%.4f"),
                    "risk_pressure": st.column_config.NumberColumn("Risk Pr", format="%.4f"),
                    "threshold": st.column_config.NumberColumn("Threshold", format="%.4f"),
                },
            )


@st.fragment(run_every=5)
def render_trade_ledger() -> None:
    _section_header("📋", "Layer 2: Execution Health", "Market Reality: Fill prices, slippage, and latency metrics", "#f472b6")

    exec_df = load_recent_executions()
    exec_stats = load_execution_stats()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Fills", int(exec_stats.get("total", 0)))
    c2.metric("Avg Latency", f"{exec_stats.get('avg_latency', 0):.0f} ms")
    c3.metric("Avg Slippage", f"{exec_stats.get('avg_slippage', 0):.6f}%")
    c4.metric("Max Latency", f"{exec_stats.get('max_latency', 0):.0f} ms")

    if exec_df.empty:
        st.markdown("""
        <div style="text-align:center; padding:32px; color:var(--text-muted); font-size:0.85rem;">
            No executions yet. Waiting for gate approvals to trigger fills...
        </div>
        """, unsafe_allow_html=True)
    else:
        st.dataframe(exec_df, use_container_width=True, hide_index=True)


@st.cache_data(ttl=4)
def load_recent_rejections() -> pd.DataFrame:
    return _read_sql("""
    SELECT
        timestamp, symbol, side, stage, reason
    FROM signal_rejections
    ORDER BY created_at DESC
    LIMIT 30
    """)


@st.fragment(run_every=5)
def render_rejections_feed() -> None:
    _section_header("🚫", "Recent Rejections", "Inner monologue -- why trades were filtered or vetoed", "#fb7185")

    rej_df = load_recent_rejections()
    if rej_df.empty:
        st.markdown("""
        <div style="text-align:center; padding:32px; color:var(--text-muted); font-size:0.85rem;">
            No rejections logged yet.
        </div>
        """, unsafe_allow_html=True)
    else:
        st.dataframe(
            rej_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "stage": st.column_config.TextColumn("Stage"),
                "reason": st.column_config.TextColumn("Reason", width="large"),
            },
        )


@st.fragment(run_every=5)
def render_heartbeat() -> None:
    _section_header("💓", "System Heartbeat", "Last activity timestamps from the telemetry truth engine", "#f97316")

    hb = load_heartbeat()
    funnel = load_funnel_metrics()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Candles Processed", funnel.get("candles", 0))
    c2.metric("Signals Generated", funnel.get("signals", 0))
    c3.metric("Gate Approvals", funnel.get("approvals", 0))
    c4.metric("Live Fills", funnel.get("fills", 0))

    last_ts = None
    for key in ["last_candle", "last_snapshot", "last_signal", "last_gate", "last_execution"]:
        ts = hb.get(key)
        if ts and (last_ts is None or ts > last_ts):
            last_ts = ts

    if last_ts:
        st.markdown(f"""
        <div style="display:flex; align-items:center; gap:10px; margin-top:12px; padding:12px 18px;
                    background: rgba(52,211,153,0.06); border: 1px solid rgba(52,211,153,0.15); border-radius:10px;">
            <span class="heartbeat-dot"></span>
            <span style="color:var(--accent-emerald); font-family:'JetBrains Mono', monospace; font-size:0.82rem; font-weight:600;">
                Last Activity: {last_ts}
            </span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="display:flex; align-items:center; gap:10px; margin-top:12px; padding:12px 18px;
                    background: rgba(251,191,36,0.06); border: 1px solid rgba(251,191,36,0.15); border-radius:10px;">
            <span style="color:var(--accent-amber); font-family:'JetBrains Mono', monospace; font-size:0.82rem; font-weight:600;">
                Awaiting first data...
            </span>
        </div>
        """, unsafe_allow_html=True)

    with st.expander("Detailed Timestamps", expanded=False):
        for label, key in [
            ("Last Candle Processed", "last_candle"),
            ("Last Market Snapshot", "last_snapshot"),
            ("Last Signal Generated", "last_signal"),
            ("Last Gate Evaluation", "last_gate"),
            ("Last Execution", "last_execution"),
        ]:
            ts = hb.get(key) or "N/A"
            st.markdown(f"**{label}:** `{ts}`")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    st.set_page_config(
        page_title="Quanta Glass Window",
        page_icon="💎",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    # Sections are now self-refreshing via @st.fragment
    render_hero()
    render_account_hud()
    _glass_divider()
    render_positions_grid()
    _glass_divider()
    render_intelligence_feed()
    _glass_divider()
    render_trade_ledger()
    _glass_divider()
    render_rejections_feed()
    _glass_divider()
    render_heartbeat()

    # Footer
    st.markdown(f"""
    <div style="text-align:center; padding:32px 0 16px; color:var(--text-muted); font-size:0.7rem; font-family:'JetBrains Mono', monospace;">
        QUANTA GLASS WINDOW v10.0 &bull; READ-ONLY &bull; FAIL-CLOSED &bull; Telemetry: {_resolve_db_path()}
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
