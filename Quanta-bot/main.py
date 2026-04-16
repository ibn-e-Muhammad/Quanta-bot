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
from market_data.src.engine import run_market_engine
from strategy.src.engine import run_strategy_engine
from execution.src.engine import run_execution_engine
from research.src.engine import run_research_engine

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
# The Infinite Loop
# ===========================================================================
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

    logger.info("System Initialization Complete. Starting deterministic sequence.")

    # 2. Infinite Loop
    try:
        while SYSTEM_STATE["running"]:
            SYSTEM_STATE["cycle_id"] += 1
            cycle = SYSTEM_STATE["cycle_id"]
            logger.info(f"--- STARTING CYCLE {cycle} ---")
            
            # --- PHASE A: PERCEPTION (Room 1) ---
            logger.info("[PHASE A] Running Market Engine...")
            try:
                run_market_engine()
            except Exception as loop_e:
                # In testing, if Room 1 is mocked, it might fail. Only halt if we want strict execution.
                logger.warning(f"Market Engine encountered an issue: {loop_e}")
                # For this proof of concept, we won't crash if Market Engine doesn't have live feeds.
            
            # --- PHASE B: LOGIC (Room 2) ---
            logger.info("[PHASE B] Running Strategy Engine...")
            signal = run_strategy_engine()
            if signal.get("signal", "HOLD") == "HOLD":
                logger.info(f"[PHASE B] HOLD signal received. Reason: {signal.get('reason', 'Unknown')}. Cycle complete.")
                # Give CPU a breather
                time.sleep(10)
                continue
            
            # --- PHASE C: EXECUTION (Room 3) ---
            logger.info(f"[PHASE C] {signal.get('signal')} signal received. Fetching account state...")
            account_state = fetch_account_state()
            
            logger.info("[PHASE C] Running Execution Engine...")
            execution_result = run_execution_engine(signal, account_state)
            
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
            time.sleep(2)
            
            # For demonstration, we break after 1 cycle to prevent infinite hanging
            # Remove this in production.
            break

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down gracefully.")
        SYSTEM_STATE["running"] = False
    
    except Exception as e:
        logger.error(f"[FATAL] Unhandled exception in main loop: {e}", exc_info=True)
        SYSTEM_STATE["running"] = False
        sys.exit(1)


if __name__ == "__main__":
    main()
