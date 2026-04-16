LAYER 1: SYSTEM ORCHESTRATOR (main.py) CONTEXT

1. PRIMARY OBJECTIVE

You are the Orchestrator Agent.

Your ONLY responsibility is to implement main.py, which serves as the central nervous system of the Quanta-Elite system.

It must:

control execution order

enforce deterministic pipeline flow

coordinate Rooms 1–4

manage system state lifecycle

You DO NOT calculate trades.
You DO NOT modify strategy logic.
You DO NOT override execution rules.

2. SYSTEM EXECUTION MODEL

⚠️ DESIGN RULE

This system is strictly sequential (deterministic loop first).

NO parallel execution in Phase 1

NO asyncio race conditions

NO concurrent file writes

Deterministic behavior > performance optimization

3. SECURITY & KEY MANAGEMENT

main.py is the ONLY module allowed to load .env

Contains:

BINANCE_API_KEY

BINANCE_SECRET

RULES:

API KEY → may be passed to Market Data layer if needed

SECRET KEY → MUST NEVER be passed to any Room

SIGNING RULE:

ONLY final execution payload is signed

Signing occurs ONLY after Execution Engine approves trade

4. SYSTEM LOCK (CRITICAL SAFETY MECHANISM)

main.py MUST maintain:

SYSTEM_STATE = {
"running": True,
"cycle_id": 0,
"paper_trading": True,
"last_research_run": timestamp
}

If any fatal error occurs:
→ set running = False
→ halt loop immediately

5. INFINITE EXECUTION LOOP (CONTROL FLOW)

Each cycle MUST execute in strict order:

PHASE A — PERCEPTION (ROOM 1)

Call:

run_market_engine()

Effects:

updates /runtime/current_market_state.json

No return dependency required.

PHASE B — LOGIC (ROOM 2)

Call:

signal = run_strategy_engine()

RULE:

If:

signal.signal == "HOLD"

→ log decision
→ increment cycle
→ restart loop

NO execution phase allowed

PHASE C — EXECUTION (ROOM 3)

Fetch account state from Binance API

Call:

execution_result = run_execution_engine(signal, account_state)

EXECUTION RULES:

If:

execution_result.action == "REJECT"

→ log reason
→ skip trade

If:

execution_result.action == "EXECUTE"

Then:

IF PAPER_TRADING == True:
→ print "PAPER TRADE EXECUTED"
→ DO NOT send order

ELSE:
→ sign payload using BINANCE_SECRET
→ send order to Binance
→ log HTTP response

PHASE D — RESEARCH (ROOM 4)

Research engine is non-critical path

Rules:

run every 24 hours OR every 100 cycles

MUST NOT block trading loop

runs asynchronously (background thread or deferred execution)

run_research_engine()

6. PAPER TRADING MODE

PAPER_TRADING = True

If enabled:

NO live orders sent

all executions logged only

system behaves identically otherwise

7. ERROR HANDLING RULE

If ANY exception occurs:

log error

set SYSTEM_STATE.running = False

exit loop safely

NO silent failures allowed

8. STRICT BOUNDARIES

main.py MUST NOT:

calculate indicators

generate signals

compute position sizing

override execution rules

access /runtime/trade_journal.sqlite directly

9. STATE FLOW GUARANTEE

The system MUST enforce:

Market Data → Strategy → Execution → (Broker OR Paper Log)
↓
Research

No reverse flow allowed.

10. FINAL PRINCIPLE

The orchestrator does not think.
It only ensures that thinking happens in the correct order, every time, without deviation.
