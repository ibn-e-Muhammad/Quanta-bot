# Phase 8.1 & 8.2 — Live Orchestrator Architecture & Telemetry DB

## Status

Approved architecture has been drafted and added in `production/` as requested.

Created files:

- [production/src/live_telemetry.py](production/src/live_telemetry.py)
- [production/src/live_orchestrator.py](production/src/live_orchestrator.py)
- [production/src/**init**.py](production/src/__init__.py)

---

## 1) Telemetry Truth Engine Schema (SQLite)

Implemented in [production/src/live_telemetry.py](production/src/live_telemetry.py) via `LiveTelemetryStore.schema_sql()`.

### `market_snapshots`

Purpose: canonical closed-candle and feature snapshot per symbol.

Columns:

- `snapshot_id` (PK)
- `timestamp`, `symbol`, `interval`
- `open`, `high`, `low`, `close`, `volume`
- `adx`, `atr`
- `regime_features_json`
- `source_event_json`
- `created_at`

Critical idempotency rule implemented:

- `UNIQUE(symbol, timestamp)` ✅

Index:

- `idx_market_snapshots_symbol_ts` on `(symbol, timestamp DESC)`

### `signals_generated`

Purpose: all generated candidate signals and ranking metadata.

Columns:

- `signal_id` (PK)
- `timestamp`, `symbol`
- `strategy_name`, `signal_side`
- `raw_score`, `priority_rank`
- `snapshot_id` (FK → `market_snapshots.snapshot_id`)
- `metadata_json`, `created_at`

Index:

- `idx_signals_generated_symbol_ts`

### `gate_evaluations`

Purpose: full gate truth record (microstructure + ML + final decision).

Columns:

- `gate_eval_id` (PK)
- `signal_id` (FK → `signals_generated.signal_id`)
- `timestamp`, `symbol`
- `microstructure_regime`, `risk_pressure`
- `ml_prob`, `ml_adjusted`, `threshold_applied`
- `final_decision` (`EXECUTE`/`VETO`)
- `veto_reason`
- `details_json`, `created_at`

Indexes:

- `idx_gate_evaluations_signal`
- `idx_gate_evaluations_symbol_ts`

### `live_executions`

Purpose: execution quality + fill latency/slippage truth.

Columns:

- `execution_id` (PK)
- `signal_id` (FK)
- `timestamp`, `symbol`, `side`
- `expected_price`, `actual_fill_price`
- `slippage_pct`, `latency_ms`
- `exchange_order_id`, `order_status`
- `raw_exchange_json`, `created_at`

Indexes:

- `idx_live_executions_symbol_ts`
- `idx_live_executions_signal`

### `positions`

Purpose: live position lifecycle state.

Columns:

- `position_id` (PK)
- `signal_id` (FK)
- `symbol`
- `entry_price`, `quantity`
- `tp_price`, `sl_price`
- `status`, `pnl`
- `opened_at`, `closed_at`
- `details_json`
- `created_at`, `updated_at`

Index:

- `idx_positions_symbol_status`

---

## 2) Async Class Architecture (Adapter Pattern)

Implemented as stubs in [production/src/live_orchestrator.py](production/src/live_orchestrator.py).

### `BinanceDataStreamer`

Responsibilities:

- connect/subscribe to Binance Futures websocket
- parse kline payloads
- enforce **4h closed-candle only** trigger (`k['x'] == True`)
- apply in-memory duplicate suppression per symbol

Critical idempotency implemented:

- `self.last_processed_candle: dict[str, int]`
- duplicate rule: drop if `open_time_ms <= last_processed_candle[symbol]`

Primary methods:

- `connect()`
- `subscribe()`
- `consume_forever()`
- `handle_kline_event(event)`

### `QuantaAdapter`

Responsibilities:

- wrap hardened research logic (no strategy rewrite)
- convert live snapshot into parity row format
- run signal/gate adapter flow
- emit `ApprovedTrade` when executable

Parity imports included:

- `HistoricalSimulator` from research stack
- `expansion_engine` strategy module

Primary methods:

- `evaluate_closed_candle(snapshot)`
- `build_parity_row(snapshot)`
- `run_signal_layer(row)`
- `run_gate_layer(candidate)`
- `build_approved_trade(candidate)`

### `BinanceOrderManager`

Responsibilities:

- place market entry on Binance Testnet REST
- measure slippage and latency
- place TP/SL orders
- persist execution telemetry

Primary methods:

- `place_market_entry(trade)`
- `place_tp_sl_orders(trade)`
- `execute_trade(trade)`

### `LiveOrchestrator`

Responsibilities:

- wire streamer + adapter + order manager
- load credentials from `.env`
- load symbol universe from active config
- initialize telemetry DB
- run async lifecycle

Primary methods:

- `from_env(env_path=".env")`
- `run()`
- `handle_closed_candle(snapshot)`
- `shutdown()`

---

## 3) Credentials / Universe / Trigger Rules (as requested)

### Credentials

- Uses `python-dotenv` (`load_dotenv`) in orchestrator factory.
- Expected env vars:
  - `BINANCE_TESTNET_API_KEY`
  - `BINANCE_TESTNET_API_SECRET`
- No hardcoded secrets.

### Symbol universe

- Loaded dynamically from active config:
  - [runtime/config/strategy_config.json](runtime/config/strategy_config.json)
- Watchlist path used:
  - `matrix.watchlist`

### 4h close trigger

- Strategy pipeline intended to run only when websocket kline indicates closure:
  - `k['x'] == True`
  - interval check: `k['i'] == '4h'`
- No local-time close guessing.

---

## 4) Telemetry API Surface

Implemented helper methods in [production/src/live_telemetry.py](production/src/live_telemetry.py):

- `initialize()`
- `insert_market_snapshot()`
- `insert_signal()`
- `insert_gate_evaluation()`
- `insert_execution()`
- `upsert_position()`
- `default_db_path()`

DB target path default:

- `production/runtime/live_telemetry.sqlite`

---

## 5) What is intentionally NOT implemented yet

Per instruction, this draft contains schema + stubs only (no heavy runtime logic):

- no full websocket client implementation
- no full Binance REST signing/placement implementation
- no full adapter execution internals
- no Streamlit/UI

---

## 6) Quality and Risk Notes

### Strong alignment with production objective

- Adapter architecture preserves hardened research logic.
- Deterministic telemetry capture creates full replay/audit trail.
- Idempotency is enforced both at DB and in-memory event layer.

### Remaining integration tasks (next phase)

- wire real Binance websocket and REST clients
- bind adapter methods directly to existing Phase 6/7 functions
- implement exactly-once event handling across reconnects/restarts
- integrate persistent heartbeat/recovery policy

---

## 7) Conclusion

Phase 8.1/8.2 draft deliverables are complete for review:

- exact telemetry SQL schema with required tables + constraints
- async class stubs with docstrings and method signatures
- credential, universe, and closed-candle trigger requirements enforced in architecture
- parity-first Adapter pattern preserved without rewriting strategy/ML logic
