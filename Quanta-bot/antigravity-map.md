# QUANTA-ELITE: MASTER ROUTER & AGENT CONSTITUTION

## 1. CORE DIRECTIVES

- **Survive First:** Risk management supersedes all logic. The `/rules` directory is ABSOLUTE LAW. No module may override it.
- **Execution is Final Authority:** Strategy suggests. Execution decides. Any violation of risk constraints must be corrected or rejected by the execution layer.
- **Strict Modularity:** Agents operate ONLY within their assigned workspace. Cross-room edits are forbidden unless explicitly authorized.
- **No Hallucination:** If a required API, dataset, or spec is missing → STOP and request human input.
- **Deterministic Behavior First:** Before adding intelligence (ML), ensure predictable, testable behavior.

## 2. SYSTEM ORCHESTRATION (THE MISSING BRAIN)

A central orchestrator (`main.py` or `orchestrator.py` in the root) MUST control all flows. No module may independently trigger trades outside this loop.

**Execution Loop:**

1. Fetch & update market data
2. Write current state → `/runtime/current_market_state.json`
3. Run strategy engine → generate trade signal
4. Pass signal to execution layer
5. Execution validates against `/rules`
6. Log final decision → `/runtime/decision_log.md`
7. Store trade (if any) → `/runtime/trade_journal.sqlite`
8. (Optional) Trigger research module

## 3. WORKSPACE ROUTING MAP & I/O OWNERSHIP

### ⚖️ `/rules/` (Layer 0 — The Handcuffs)

- **Purpose:** Contains non-negotiable constraints (e.g., max risk per trade, leverage caps, daily loss limits).
- **Access:** MUST be read by Strategy (for awareness) and Execution (for strict enforcement).

### 📊 `/market_data/` (Room 1 — Market State Engine)

- **Responsibilities:** Data ingestion (REST/WebSocket), indicator computation, market classification (trend, range, volatility).
- **Output Ownership:** ONLY this module may write to `/runtime/current_market_state.json`.

### 🧠 `/strategy/` (Room 2 — Strategy Engine & Selector)

- **Responsibilities:** Pure trading logic (NO API calls), entry/exit signal generation.
- **Strict Rule:** Strategy CANNOT execute trades, access broker APIs, or override risk rules.
- **Output Format:** `{"signal": "BUY" | "SELL" | "HOLD", "confidence": float, "reason": "string"}`

### 🛡️ `/execution/` (Room 3 — Risk Management & Execution Engine)

- **Responsibilities:** Position sizing, risk validation, leverage enforcement, order formatting & routing.
- **Authority:** The Gatekeeper. Can MODIFY or REJECT strategy signals to enforce `/rules/trading-limits.md`.
- **Output Ownership:** Writes to `/runtime/trade_journal.sqlite` and appends to `/runtime/decision_log.md` (final decisions only).

### 🧪 `/research/` (Room 4 — Performance Analyzer & Learning Lab)

- **Responsibilities:** Backtesting, performance analysis, parameter tuning suggestions.
- **Strict Rule:** SANDBOXED ZONE. Cannot directly modify live strategy code or influence live execution automatically.
- **Output Ownership:** Writes to `/research/backtest_results/` and `/runtime/performance_snapshot.json`.

## 4. STATE MANAGEMENT RULES (CRITICAL)

- No shared file may have multiple writers.
- All writes must be atomic.
- Logs must be append-only.
- Never overwrite historical data.
- _Violation of these rules = undefined system behavior (silent failure)._

## 5. STANDARD OPERATING PROCEDURE (SOP)

Every agent MUST follow this loop before coding:

1. **Grounding:** Read `antigravity-map.md`.
2. **Contextualize:** Read room-specific `*-context.md`.
3. **Equip:** Load relevant files from `/skills/`.
4. **Plan:** Write a step-by-step plan in `/runtime/decision_log.md`.
5. **Execute:** Implement code in the room's `src/` directory.
6. **Verify:** Write Pytest tests (NO live API calls).

## 6. TESTING & SIMULATION LAYER

- Implement paper trading mode before live deployment.
- Mock all broker API responses.
- Simulate latency, slippage, and partial fills.
- **Rule:** If it hasn’t survived simulation, it doesn’t touch real money.

## 7. DATA FLOW CONTRACT

`market_data` → `runtime` → `strategy` → `execution` → `runtime` → `research`
_(No reverse shortcuts allowed.)_

## 8. FILE NAMING CONVENTIONS

- **Code:** `module_name.py`
- **Tests:** `test_module_name.py`
- **Data:** `YYYY-MM-DD-dataset.json` or `YYYY-MM-DD-trades.csv`

## 9. FUTURE EXTENSIONS (LOCKED UNTIL STABLE)

- Multi-strategy weighting
- Reinforcement learning
- Auto strategy mutation
- Cross-market arbitrage
  _(Do not implement until Phase 1 base execution is validated.)_

## 10. TECH STACK CONTRACT (STRICT ENFORCEMENT)
No agent is permitted to deviate from this exact technology stack to prevent dependency bloat and ensure deterministic execution.

* **Language:** Python 3.11+ (Strict type hinting required for all functions).
* **Math & Indicators:** `numpy` ONLY. (FORBIDDEN: `pandas`, `ta-lib`, `pandas-ta`).
* **Network & API:** `requests` for REST calls; standard `websockets` for streaming. (FORBIDDEN: `python-binance`, `ccxt`, or any third-party exchange wrappers).
* **Database:** `sqlite3` built-in standard library ONLY. (FORBIDDEN: `SQLAlchemy`, `Django ORM`).
* **Testing:** `pytest` (Must mock all external network calls).

> **FINAL PRINCIPLE:** A profitable system is not the one that wins the most trades. It is the one that survives the longest.
