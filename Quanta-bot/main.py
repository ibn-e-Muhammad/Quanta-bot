"""
main.py — Quanta-Elite System Orchestrator

The central nervous system. Controls deterministic flow through Rooms 1-4.
Zero calculation or logic inside this file; strictly coordination.
"""

import os
import sys
import time
import hmac
import hashlib
import json
import logging
import threading
import urllib.request
from urllib.error import URLError, HTTPError
from pathlib import Path


# Setup raw basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | [ORCHESTRATOR] | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Paths
_ROOT = Path(__file__).resolve().parent
ENV_PATH = _ROOT / ".env"

# Add parent directory to sys.path if not running as module
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Room imports
from strategy.src.state_reader import read_market_state
from market_data.src.engine import run_market_engine
from strategy.src.engine import run_strategy_engine
from execution.src.engine import run_execution_engine
from execution.src.risk_engine import RiskEngine
from research.src.engine import run_research_engine

# ===========================================================================
# MTF Gating Helper
# ===========================================================================
def check_mtf_gates(signal: dict, mtf_context: dict) -> dict:
    sig = signal.get("signal", "HOLD")

    c_4h = mtf_context.get("4h", {})
    c_1h = mtf_context.get("1h", {})

    p_4h = c_4h.get("price", 0)
    e200_4h = c_4h.get("ema_trend", 0)
    rsi_4h = c_4h.get("rsi", 50)
    
    e50_1h = c_1h.get("ema_confirm", 0)
    e200_1h = c_1h.get("ema_trend", 0)
    adx_1h = c_1h.get("adx", 0)

    bias_4h = "NEUTRAL"
    if p_4h > e200_4h and rsi_4h < 70: bias_4h = "BULLISH"
    elif p_4h < e200_4h and rsi_4h > 30: bias_4h = "BEARISH"

    conf_1h = "NOT CONFIRMED"
    if sig == "BUY" and e50_1h > e200_1h and adx_1h >= 20: conf_1h = "CONFIRMED"
    elif sig == "SELL" and e50_1h < e200_1h and adx_1h >= 20: conf_1h = "CONFIRMED"
    elif sig == "HOLD":
        if e50_1h > e200_1h and adx_1h >= 20: conf_1h = "BULLISH CONFIRMED"
        elif e50_1h < e200_1h and adx_1h >= 20: conf_1h = "BEARISH CONFIRMED"

    if sig == "HOLD":
        return {
            "pass": False,
            "4h": bias_4h,
            "1h": conf_1h,
            "reason": signal.get("reason", "HOLD")
        }

    is_pass = False
    fail_reason = "MTF_BIAS_MISMATCH"
    
    if sig == "BUY" and bias_4h == "BULLISH" and conf_1h == "CONFIRMED":
        is_pass = True
        fail_reason = ""
    elif sig == "SELL" and bias_4h == "BEARISH" and conf_1h == "CONFIRMED":
        is_pass = True
        fail_reason = ""

    return {
        "pass": is_pass,
        "4h": bias_4h,
        "1h": conf_1h,
        "reason": fail_reason or "MTF Alignment Confirmed"
    }

# ===========================================================================
# State Management
# ===========================================================================
SYSTEM_STATE = {
    "running": True,
    "cycle_id": 0,
    "paper_trading": True,
    "last_research_cycle": 0
}

# ===========================================================================
# Helpers
# ===========================================================================
def load_env(path: Path) -> dict:
    """Pure Python .env parser to avoid external dependencies.
    Returns a dict of key-value pairs.
    """
    env_vars = {}
    if not path.exists():
        logger.warning(f".env file not found at {path}. Using environment variables or defaults.")
        return env_vars

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Ignore comments and empty lines
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                env_vars[key.strip()] = val.strip().strip("'\"")
    return env_vars


def dispatch_binance_order(payload: dict, secret: str, endpoint_url: str) -> dict:
    """Mock dispatcher for live orders showing HMAC signing mechanics.
    Since we don't have a real Binance endpoint here, this simply logs.
    """
    if not secret:
        raise ValueError("Cannot dispatch live order: BINANCE_SECRET is missing.")
    
    # 1. Create query string
    query_string = urllib.parse.urlencode(payload)
    
    # 2. Hmac sha256 signature
    signature = hmac.new(
        secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    signed_payload = payload.copy()
    signed_payload["signature"] = signature

    logger.info(f"LIVE DISPATCH PREPARED: Payload signed. (Not actually sending HTTP request to avoid external deps)")
    # Actual dispatch would look like:
    # req = urllib.request.Request(endpoint_url, data=urllib.parse.urlencode(signed_payload).encode())
    # req.add_header("X-MBX-APIKEY", BINANCE_API_KEY)
    # with urllib.request.urlopen(req) as res:
    #     return json.loads(res.read())
    
    return {"status": "MOCKED_SUCCESS", "signed_keys": list(signed_payload.keys())}


def fetch_account_state() -> dict:
    """Fetches Binance account state. Mapped to Room 3's required structure."""
    # Hardcoded mock for now, this would call Binance /api/v3/account
    return {
        "status": "NORMAL",
        "account_balance": 10000.0,
        "daily_equity_start": 10000.0,
        "daily_peak_equity": 10000.0
    }

# ===========================================================================
# Matrix Configuration
# ===========================================================================
_STRATEGY_CONFIG_PATH = _ROOT / "runtime" / "config" / "strategy_config.json"

def _load_matrix() -> tuple[list[str], list[str]]:
    """Load watchlist and intervals from strategy_config.json matrix section."""
    try:
        with open(_STRATEGY_CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        matrix = cfg.get("matrix", {})
        watchlist = matrix.get("watchlist", ["BTCUSDT"])
        intervals = matrix.get("intervals", ["15m"])
        return watchlist, intervals
    except Exception as e:
        logger.warning(f"Failed to load matrix config: {e}. Using defaults.")
        return ["BTCUSDT"], ["15m"]

def main():
    logger.info("Initializing Quanta-Elite System Orchestrator...")
    
    # 1. Load Secrets
    env_vars = load_env(ENV_PATH)
    
    api_key = env_vars.get("BINANCE_API_KEY", "")
    secret_key = env_vars.get("BINANCE_SECRET", "")
    paper_env = env_vars.get("PAPER_TRADING", "true").lower()
    
    # Evaluate paper trading safety toggle
    SYSTEM_STATE["paper_trading"] = (paper_env == "true" or paper_env == "1")
    
    logger.info(f"PAPER TRADING MODE: {SYSTEM_STATE['paper_trading']}")
    if not SYSTEM_STATE["paper_trading"] and not secret_key:
        logger.error("FATAL: Live trading requires BINANCE_SECRET. Halting.")
        sys.exit(1)

    # 2. Load Matrix & Risk
    watchlist, intervals = _load_matrix()
    total_combinations = len(watchlist) * len(intervals)
    logger.info(f"Matrix loaded: {len(watchlist)} symbols × {len(intervals)} intervals = {total_combinations} state updates per cycle.")
    
    risk_engine = RiskEngine(str(_STRATEGY_CONFIG_PATH))

    logger.info("System Initialization Complete. Starting deterministic sequence.")

    # 3. Infinite Loop
    try:
        while SYSTEM_STATE["running"]:
            SYSTEM_STATE["cycle_id"] += 1
            cycle = SYSTEM_STATE["cycle_id"]
            logger.info(f"--- STARTING CYCLE {cycle} ---")
            
            # --- PHASE A & B: PERCEPTION & LOGIC (Rooms 1 & 2) ---
            potential_trades = []
            
            for symbol in watchlist:
                mtf_context = {}
                
                # Fetch all intervals for the MTF Context
                for interval in intervals:
                    try:
                        run_market_engine(symbol, interval)
                        state = read_market_state(symbol, interval)
                        if state:
                            mtf_context[interval] = state
                    except Exception as loop_e:
                        logger.warning(f"[{symbol}|{interval}] Market Engine encountered an issue: {loop_e}")
                
                # Exclusively pass ONLY 15m to strategy engine
                signal = run_strategy_engine(symbol, "15m")
                
                # MTF Gate Checks
                mtf_result = check_mtf_gates(signal, mtf_context)
                
                # X-Ray MTF Logging format requested by specification
                res_sig = signal.get("signal", "HOLD")
                final_action = "ACCEPTED" if mtf_result["pass"] else "REJECTED"
                
                print(f"[MTF SCAN] {symbol}")
                print(f"- 4H:  {mtf_result['4h']}")
                print(f"- 1H:  {mtf_result['1h']}")
                print(f"- 15M: {res_sig}")
                print(f"- FINAL: {final_action} ({mtf_result['reason']})")
                print("-" * 50)
                
                if mtf_result["pass"]:
                    # Scoring Upgrade: Add 1D trend MTF bonus if it aligns with 15m signal
                    mtf_bonus = 0.0
                    c_1d = mtf_context.get("1d", {})
                    if c_1d:
                        primary_1d = c_1d.get("state", {}).get("primary", "")
                        if res_sig == "BUY" and primary_1d == "TRENDING_UP":
                            mtf_bonus = 0.1
                        elif res_sig == "SELL" and primary_1d == "TRENDING_DOWN":
                            mtf_bonus = 0.1
                            
                    signal["composite_score"] += mtf_bonus
                    potential_trades.append(signal)

            # --- X-Ray Competition Logging ---
            if potential_trades:
                # Sort descending by composite_score
                potential_trades.sort(key=lambda s: s.get("composite_score", 0), reverse=True)
                
                ranking_strs = [f"{s.get('symbol')} ({s.get('composite_score', 0):.2f})" for s in potential_trades]
                winner_symbol = potential_trades[0].get("symbol")
                logger.info(f"[ORCHESTRATOR] Ranking: {', '.join(ranking_strs)}. Selecting {winner_symbol}.")
                
                best_signal = potential_trades[0]
            else:
                logger.info(f"[ORCHESTRATOR] Sweep Complete. 0 opportunities qualified. Entering 60s sleep.")
                time.sleep(60)
                continue

            # --- PHASE C: EXECUTION (Room 3) ---
            logger.info(f"[PHASE C] {best_signal.get('signal')} signal received for {best_signal.get('symbol')}. Fetching account state...")
            if SYSTEM_STATE["paper_trading"]:
                account_state = {
                    "account_balance": 10000.0,
                    "daily_equity_start": 10000.0,
                    "daily_peak_equity": 10000.0,
                    "daily_trade_count": 0,
                    "consecutive_losses": 0,
                    "system_status": "ACTIVE"
                }
            else:
                account_state = fetch_account_state()
            
            logger.info("[PHASE C] Running Execution Engine...")
            atr_val = mtf_context.get("15m", {}).get("atr", 0.0)
            execution_result = run_execution_engine(best_signal, account_state, atr=atr_val, risk_engine=risk_engine)
            
            if execution_result["action"] == "REJECT":
                reason = execution_result.get("rejection_reason", "Unknown")
                logger.info(f"[PHASE C] Trade REJECTED by Execution Engine: {reason}")
                
            elif execution_result["action"] == "EXECUTE":
                logger.info("[PHASE C] Trade APPROVED by Execution Engine. Preparing execution.")
                order_payload = execution_result.get("order_payload", {})
                
                if SYSTEM_STATE["paper_trading"]:
                    logger.info(f"⚠️ PAPER TRADE EXECUTED ⚠️ -> {order_payload}")
                else:
                    logger.info("🚨 LIVE TRADE DISPATCHING 🚨")
                    dispatch_binance_order(
                        payload=order_payload, 
                        secret=secret_key, 
                        endpoint_url="https://api.binance.com/api/v3/order"
                    )
            
            # --- PHASE D: RESEARCH (Room 4) ---
            if (cycle - SYSTEM_STATE["last_research_cycle"]) >= 100:
                logger.info("[PHASE D] 100 cycles reached. Triggering asynchronous Research Lab run...")
                SYSTEM_STATE["last_research_cycle"] = cycle
                # Run purely in background thread
                threading.Thread(target=run_research_engine, daemon=True).start()
            
            # Loop delay for sanity
            logger.info(f"--- CYCLE {cycle} COMPLETE ---")
            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down gracefully.")
        SYSTEM_STATE["running"] = False
    
    except Exception as e:
        logger.error(f"[FATAL] Unhandled exception in main loop: {e}", exc_info=True)
        SYSTEM_STATE["running"] = False
        sys.exit(1)


if __name__ == "__main__":
    main()
