# MARKET STATE ENGINE — PYTHON ARCHITECTURAL PLAN

> **Agent:** Market Data Agent (Room 1)
> **Date:** 2026-04-16
> **Status:** ✅ IMPLEMENTED & TESTED — 67/67 tests passing

---

## 0. DIRECTIVES ACKNOWLEDGED

| Document                    | Key Takeaway                                                                                                                            |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `antigravity-map.md`        | Survive first. Strict modularity. MSE owns `/runtime/current_market_state.json`. Data flow is one-way forward. All writes atomic.       |
| `data-context.md`           | MSE = measurement only. Ingest → Compute → Classify → Write. SAFE MODE on any failure.                                                  |
| `trading-limits.md`         | Layer 0 law — read-only awareness for MSE; enforcement is Execution's job.                                                              |
| `technical-analysis-pro.md` | Indicators are state classifiers, not predictors. Deterministic rules only.                                                             |
| `binance-api-expert.md`     | Stateless, idempotent, retry-safe. Only `/api/v3/klines` needed. Exponential backoff on 429. Discard entire batch on integrity failure. |

---

## 1. FILE STRUCTURE

All source code lives in `/market_data/src/`. Tests live alongside as `test_*.py`.

```
market_data/
├── data-context.md           # (existing) Room spec
└── src/
    ├── __init__.py            # Package init
    ├── config.py              # Symbol, interval, candle limit, API base URL
    ├── binance_client.py      # Binance REST client (klines only)
    ├── indicators.py          # Pure-function indicator calculations
    ├── classifier.py          # Volatility + market-state classification
    ├── state_writer.py        # Atomic JSON write to /runtime/
    ├── engine.py              # Orchestrator: fetch → compute → classify → write
    ├── test_indicators.py     # Unit tests for indicator math
    ├── test_classifier.py     # Unit tests for classification logic
    ├── test_state_writer.py   # Unit tests for atomic write + validation
    └── test_engine.py         # Integration test with mocked Binance response
```

---

## 2. MODULE-BY-MODULE PLAN

### Step 1 — `config.py`

- Constants: `SYMBOL`, `INTERVAL`, `CANDLE_LIMIT` (≥ 200), `API_BASE_URL`.
- Reads `SYMBOL` and `INTERVAL` from environment variables with sane defaults (`BTCUSDT`, `1h`).
- Defines path constants: `RUNTIME_DIR`, `STATE_FILE_PATH`.

### Step 2 — `binance_client.py`

- **Single public function:** `fetch_klines(symbol, interval, limit) → list[dict]`
- Uses `requests` (sync HTTP) to call `/api/v3/klines`.
- **Rate-limit handling:** Exponential backoff on HTTP 429 (1s → 2s → 4s → 8s). After 3 consecutive 429s → raise `SafeModeError`.
- **Validation (per `binance-api-expert.md`):**
  - Every candle must have: open, high, low, close, volume, timestamp.
  - No null fields, no missing timestamps, chronological order.
  - If ANY validation fails → raise `DataIntegrityError` (discard entire batch).
- **Returns:** List of dicts `{open_time, open, high, low, close, volume}` with all values cast to `float`.
- **No state retained between calls.**

### Step 3 — `indicators.py`

All functions are **pure** — they take a list/array of floats and return computed values. No side effects.

| Function             | Signature                                                 | Notes                                    |
| -------------------- | --------------------------------------------------------- | ---------------------------------------- |
| `ema`                | `(closes: list[float], period: int) → list[float]`        | Standard EMA formula                     |
| `sma`                | `(values: list[float], period: int) → list[float]`        | Simple moving average                    |
| `rsi`                | `(closes: list[float], period: int=14) → float`           | Wilder's smoothing, returns latest value |
| `adx`                | `(highs, lows, closes, period=14) → float`                | Standard ADX via +DI/-DI, returns latest |
| `atr`                | `(highs, lows, closes, period=14) → float`                | Wilder's ATR, returns latest             |
| `bollinger_bands`    | `(closes, period=20, std_dev=2) → (lower, middle, upper)` | Returns latest band values               |
| `vwap`               | `(highs, lows, closes, volumes) → float`                  | Session-anchored VWAP                    |
| `support_resistance` | `(lows, highs, period=50) → (support, resistance)`        | Rolling min/max                          |

- **Library choice:** `numpy` for vectorized math. No `pandas` or `ta-lib` dependency (keeps it deterministic and minimal).
- **Edge cases:** Functions raise `ValueError` if input length < required period.

### Step 4 — `classifier.py`

Two pure functions:

#### `classify_volatility(bb_upper, bb_lower, bb_middle, bb_width_history, atr, atr_history) → str`

- `BB_Width = (BB_Upper - BB_Lower) / BB_Middle`
- If `BB_Width > mean(bb_width_history[-20:])` → `"HIGH"`
- Else if `ATR < mean(atr_history[-14:]) * 0.8` → `"LOW"`
- Else → `"NORMAL"`

#### `classify_market_state(adx, ema_20, ema_50, volatility) → str`

- `ADX >= 25 AND EMA_20 > EMA_50` → `"TRENDING_UP"`
- `ADX >= 25 AND EMA_20 < EMA_50` → `"TRENDING_DOWN"`
- `ADX < 25 AND volatility != "LOW"` → `"RANGING"`
- Else → `"SIDEWAYS"`

### Step 5 — `state_writer.py`

- **`validate_state(state: dict) → bool`**
  - `price > 0`
  - No `NaN` or `None` values
  - `state.primary` ∈ `{TRENDING_UP, TRENDING_DOWN, RANGING, SIDEWAYS}`
  - `state.volatility` ∈ `{HIGH, NORMAL, LOW}`

- **`write_state(state: dict, output_path: str) → None`**
  - Serialize to JSON.
  - Write to a temp file in the same directory.
  - Call `validate_state()`.
  - Atomic rename (`os.replace`) temp → target.
  - On any failure → write SAFE MODE payload instead.

- **`build_safe_mode_payload() → dict`**
  - Returns the exact SAFE MODE JSON from `data-context.md` Section 7.

### Step 6 — `engine.py`

The top-level orchestrator for Room 1. Single public function:

#### `run_market_engine() → None`

```
1. Load config (symbol, interval, limit)
2. TRY:
   a. klines = binance_client.fetch_klines(...)
   b. Extract OHLCV arrays from klines
   c. Compute ALL indicators via indicators.py
   d. Compute BB_Width history + ATR history (last 20/14 candles)
   e. volatility = classifier.classify_volatility(...)
   f. primary = classifier.classify_market_state(...)
   g. Assemble output dict per Section 4 schema
   h. state_writer.write_state(output_dict, STATE_FILE_PATH)
3. EXCEPT (SafeModeError, DataIntegrityError, any Exception):
   → state_writer.write_state(build_safe_mode_payload(), STATE_FILE_PATH)
   → Log error to stderr
```

- **No memory between calls.** Each invocation is stateless.
- **No trade logic.** No reading from `/strategy/`, `/execution/`, or `/rules/`.

---

## 3. DEPENDENCY LIST

| Package    | Purpose                          | Install                |
| ---------- | -------------------------------- | ---------------------- |
| `requests` | HTTP client for Binance REST API | `pip install requests` |
| `numpy`    | Vectorized indicator math        | `pip install numpy`    |
| `pytest`   | Test runner                      | `pip install pytest`   |

No other dependencies. No `pandas`, no `ta-lib`, no `ccxt`.

---

## 4. VERIFICATION PLAN

### 4.1 Unit Tests (Automated — `pytest`)

| Test File              | What It Covers                                                                       | Run Command                                      |
| ---------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------ |
| `test_indicators.py`   | EMA/SMA/RSI/ADX/ATR/BB/VWAP/S&R against known reference values                       | `pytest market_data/src/test_indicators.py -v`   |
| `test_classifier.py`   | All 4 market states + all 3 volatility states with edge cases                        | `pytest market_data/src/test_classifier.py -v`   |
| `test_state_writer.py` | Atomic write, validation pass/fail, safe mode payload correctness                    | `pytest market_data/src/test_state_writer.py -v` |
| `test_engine.py`       | Full pipeline with mocked `fetch_klines` response (happy path + failure → safe mode) | `pytest market_data/src/test_engine.py -v`       |

**Run all:** `pytest market_data/src/ -v`

> **ZERO live API calls in tests.** All Binance responses are mocked with fixture data.

### 4.2 Manual Smoke Test (Post-Implementation)

1. Set env var `SYMBOL=BTCUSDT` and `INTERVAL=1h`.
2. Run `python -m market_data.src.engine` from the project root.
3. Verify `/runtime/current_market_state.json` is created with valid schema.
4. Disconnect internet → re-run → verify SAFE MODE payload is written.

---

## 5. BOUNDARIES ENFORCED

- ✅ Code lives ONLY in `market_data/src/`.
- ✅ Output ONLY to `/runtime/current_market_state.json`.
- ✅ No reads from `/strategy/`, `/execution/`, `/rules/`, or `/runtime/decision_log.md`.
- ✅ No trade signals, no position sizing, no predictions.
- ✅ Stateless between invocations.
- ✅ SAFE MODE on any failure — corrupt data = no opportunity.

---

> **AWAITING YOUR APPROVAL BEFORE WRITING ANY PYTHON CODE.**

---

---

# STRATEGY ENGINE — PYTHON ARCHITECTURAL PLAN

> **Agent:** Strategy Agent (Room 2)
> **Date:** 2026-04-16
> **Status:** ✅ IMPLEMENTED & TESTED — 67/67 tests passing

---

## 0. DIRECTIVES ACKNOWLEDGED

| Document                    | Key Takeaway                                                                                                                                     |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `antigravity-map.md`        | Strategy CANNOT execute trades, access broker APIs, or override risk rules. Output format: `{signal, confidence, reason}`. Data flow is one-way. |
| `antigravity-map.md` §10    | Python 3.11+, strict type hints, `numpy` ONLY for math. FORBIDDEN: `requests`, `pandas`, `ccxt`, any network library.                            |
| `strategy-context.md`       | Read-only access to `/runtime/current_market_state.json`. Three strategies: Trend Pullback, Range, Breakout. Default = HOLD.                     |
| `trading-limits.md`         | RR ≥ 1.5 (awareness only — enforcement is Execution's job). Strategy self-validates RR before output.                                            |
| `technical-analysis-pro.md` | Indicators are state classifiers, not predictors. RSI alone never triggers. Volume must confirm.                                                 |
| `risk-math-specialist.md`   | Position sizing is NOT Strategy's job. SL/TP suggestions are advisory. Execution owns final risk math.                                           |

---

## 1. FILE STRUCTURE

All source code lives in `/strategy/src/`. Tests live alongside as `test_*.py`.

```
strategy/
├── strategy-context.md            # (existing) Room spec
└── src/
    ├── __init__.py                 # Package init
    ├── config.py                   # Paths, valid states, constants
    ├── state_reader.py             # Read + validate market state JSON
    ├── strategies.py               # Pure-function strategy logic (Trend, Range, Breakout)
    ├── confidence.py               # Confidence scoring logic
    ├── signal_validator.py         # Output validation (SL<E<TP, RR≥1.5, etc.)
    ├── engine.py                   # Orchestrator: read → filter → evaluate → validate → output
    ├── test_state_reader.py        # Unit tests for JSON parsing + validation
    ├── test_strategies.py          # Unit tests for all 3 strategies + edge cases
    ├── test_confidence.py          # Unit tests for confidence scoring
    ├── test_signal_validator.py    # Unit tests for output validation + RR check
    └── test_engine.py              # Integration test with fixture market states
```

---

## 2. MODULE-BY-MODULE PLAN

### Step 1 — `config.py`

- Path constant: `STATE_FILE_PATH` → `/runtime/current_market_state.json` (resolved from project root).
- Enum-like constants for valid states: `VALID_PRIMARY_STATES`, `VALID_VOLATILITY_STATES`, `VALID_SIGNALS`.
- Strategy-specific thresholds (extracted from `strategy-context.md` for maintainability):
  - `VOLUME_CONFIRM_MULTIPLIER = 1.2`
  - `BREAKOUT_VOLUME_MULTIPLIER = 2.0`
  - `EMA_PROXIMITY_PCT = 0.002` (±0.2%)
  - `MIN_RR_RATIO = 1.5`
  - `BREAKOUT_PRICE_THRESHOLD = 0.001` (0.1% beyond S/R)
- **Zero network imports. Zero external dependencies.**

### Step 2 — `state_reader.py`

- **Single public function:** `read_market_state(path: str | None = None) → dict | None`
- Opens and parses `/runtime/current_market_state.json`.
- **Validation (per strategy-context.md §2):**
  - All required keys must exist: `symbol`, `timestamp`, `price`, `ema_20`, `ema_50`, `vwap`, `rsi`, `adx`, `atr`, `bb_lower`, `bb_upper`, `current_volume`, `volume_sma_20`, `state` (with `primary` and `volatility`), `support_level`, `resistance_level`.
  - No `None` or `NaN` values in numeric fields.
  - `state.primary` ∈ `{TRENDING_UP, TRENDING_DOWN, RANGING, SIDEWAYS}`.
  - `state.volatility` ∈ `{HIGH, NORMAL, LOW}`.
  - `price > 0`.
- **If ANY validation fails → return `None`** (caller produces HOLD).
- **Uses only:** `json` (stdlib), `math.isnan` (stdlib). No `numpy`, no `requests`.

### Step 3 — `strategies.py`

Three pure functions. Each takes a validated market state `dict` and returns a `dict | None` (signal dict or `None` if strategy doesn't trigger).

#### `evaluate_trend(state: dict) → dict | None`

Per `strategy-context.md` §3, Steps 2:

```
IF state.primary == TRENDING_UP:
    IF adx >= 25 AND ema_20 > ema_50 AND volume >= volume_sma_20 * 1.2:
        IF price within ±0.2% of ema_20:
            → BUY signal
            → entry = price
            → sl = ema_50 * 0.99
            → tp = price + (atr * 2)
            → strategy_used = "Trend_Pullback"

IF state.primary == TRENDING_DOWN:
    IF adx >= 25 AND ema_20 < ema_50 AND volume >= volume_sma_20 * 1.2:
        IF price within ±0.2% of ema_20:
            → SELL signal
            → entry = price
            → sl = ema_50 * 1.01
            → tp = price - (atr * 2)
            → strategy_used = "Trend_Pullback"
```

#### `evaluate_range(state: dict) → dict | None`

Per `strategy-context.md` §4:

```
IF state.primary == RANGING AND adx < 25:
    IF price <= bb_lower AND rsi <= 30:
        → BUY signal
        → entry = price
        → sl = bb_lower * 0.99
        → tp = vwap
        → strategy_used = "Range"

    IF price >= bb_upper AND rsi >= 70:
        → SELL signal
        → entry = price
        → sl = bb_upper * 1.01
        → tp = vwap
        → strategy_used = "Range"
```

#### `evaluate_breakout(state: dict) → dict | None`

Per `strategy-context.md` §5:

```
IF state.volatility == HIGH AND adx >= 25 AND volume >= volume_sma_20 * 2.0:
    IF price > resistance_level * 1.001:
        → BUY signal
        → entry = price
        → sl = resistance_level * 0.99
        → tp = price + (atr * 3)
        → strategy_used = "Breakout"

    IF price < support_level * 0.999:
        → SELL signal
        → entry = price
        → sl = support_level * 1.01
        → tp = price - (atr * 3)
        → strategy_used = "Breakout"
```

- **All functions are pure.** No side effects, no file I/O, no state retained.
- **Return `None`** if no conditions match.

### Step 4 — `confidence.py`

- **Single public function:** `compute_confidence(state: dict, signal: dict) → float`
- Returns a value in `[0.0, 1.0]` representing signal strength.
- **Scoring logic (additive, normalized):**

| Factor                                         | Points | Max |
| ---------------------------------------------- | ------ | --- |
| ADX strength: `min(adx / 50, 1.0) * 30`        | 0–30   | 30  |
| Volume confirmation: `vol / (vol_sma * 1.2)`   | 0–20   | 20  |
| RSI alignment (BUY: RSI<50=good, SELL: RSI>50) | 0–15   | 15  |
| Price proximity to entry level (EMA or BB)     | 0–15   | 15  |
| Volatility state alignment (HIGH for breakout) | 0–20   | 20  |

- `confidence_score = total_points / 100.0`, clamped to `[0.0, 1.0]`.
- **Pure function.** No file I/O. Uses only arithmetic.

### Step 5 — `signal_validator.py`

- **Single public function:** `validate_signal(signal: dict) → dict`
- Enforces output validation rules (per `strategy-context.md` §8):
  1. **Signal enum:** Must be `BUY`, `SELL`, or `HOLD`.
  2. **Long rule (BUY):** `SL < ENTRY < TP` — if violated → force HOLD.
  3. **Short rule (SELL):** `TP < ENTRY < SL` — if violated → force HOLD.
  4. **Risk/Reward rule:** `RR = abs(TP - ENTRY) / abs(ENTRY - SL) >= 1.5` — if violated → force HOLD.

- If any validation fails → return a `HOLD` signal with `reason` explaining the failure.
- If signal is `HOLD` → pass through unchanged (no SL/TP/entry to validate).
- **Pure function.** No side effects.

### Step 6 — `engine.py`

The top-level orchestrator for Room 2. Single public function:

#### `run_strategy_engine() → dict`

```
1. Read market state via state_reader.read_market_state()
2. IF state is None → return HOLD (data validation failure)
3. MARKET FILTER:
   IF state.primary == SIDEWAYS OR state.volatility == LOW:
     → return HOLD (market conditions unfavorable)
4. STRATEGY EVALUATION (priority order):
   a. signal = strategies.evaluate_breakout(state)   # highest priority: rare, volatile
   b. IF signal is None: signal = strategies.evaluate_trend(state)
   c. IF signal is None: signal = strategies.evaluate_range(state)
   d. IF signal is None: return HOLD (no conditions met)
5. Compute confidence: confidence.compute_confidence(state, signal)
6. Attach confidence_score to signal dict
7. Validate signal: signal_validator.validate_signal(signal)
8. Return final signal dict
```

- **Strategy priority:** Breakout > Trend > Range. Rationale: breakout conditions are the most specific and volatile — if they trigger, they take precedence.
- **Return value is always a valid output dict** per §7 schema.
- **No file writes.** Strategy does NOT write to `/runtime/`. The orchestrator (future `main.py`) consumes this return value.
- **No network calls.** Only reads one JSON file.

---

## 3. DEPENDENCY LIST

| Package  | Purpose                     |
| -------- | --------------------------- |
| `json`   | Parse market state (stdlib) |
| `math`   | `isnan` checks (stdlib)     |
| `pytest` | Test runner                 |

**No `numpy`.** Strategy performs only simple arithmetic (+, -, \*, /), comparisons, and min/max. No indicator computation — that's Room 1's job.

**No `requests`, no network libraries.** Room 2 has zero network dependencies.

---

## 4. OUTPUT SCHEMA (STRICT)

Every call to `run_strategy_engine()` returns exactly:

```json
{
  "timestamp": "ISO8601",
  "symbol": "BTCUSDT",
  "signal": "BUY | SELL | HOLD",
  "strategy_used": "Trend_Pullback | Range | Breakout | None",
  "confidence_score": 0.0,
  "suggested_entry": 0.0,
  "suggested_sl": 0.0,
  "suggested_tp": 0.0,
  "reason": "string"
}
```

For `HOLD` signals: `suggested_entry`, `suggested_sl`, `suggested_tp` are all `null`.

---

## 5. VERIFICATION PLAN

### 5.1 Unit Tests (Automated — `pytest`)

| Test File                  | What It Covers                                                                               |
| -------------------------- | -------------------------------------------------------------------------------------------- |
| `test_state_reader.py`     | Valid JSON → parsed dict, missing keys → None, NaN values → None, invalid enums → None       |
| `test_strategies.py`       | All 3 strategies × BUY/SELL + edge cases: volume too low, price not near EMA, ADX < 25, etc. |
| `test_confidence.py`       | High/low ADX, volume ratios, RSI alignment, clamping to [0,1]                                |
| `test_signal_validator.py` | SL/TP ordering for BUY/SELL, RR < 1.5 → HOLD, HOLD passthrough, invalid signal types         |
| `test_engine.py`           | Full pipeline with fixture market states: TRENDING_UP→BUY, SIDEWAYS→HOLD, failure→HOLD       |

**Run all:** `pytest strategy/src/ -v`

> **ZERO file writes in tests.** State JSON is provided as fixtures / tmp files.

### 5.2 Integration Checks

1. Create a fixture `current_market_state.json` with known TRENDING_UP data → verify BUY output.
2. Feed SAFE MODE payload (from Room 1) → verify HOLD output with reason "Data validation failure."
3. Feed SIDEWAYS state → verify HOLD output with reason "market conditions unfavorable."

---

## 6. BOUNDARIES ENFORCED

- ✅ Code lives ONLY in `strategy/src/`.
- ✅ Reads ONLY from `/runtime/current_market_state.json`.
- ✅ Does NOT write to any file (output is a return value).
- ✅ Does NOT access `/rules/`, `/execution/`, `/market_data/`, or `/research/`.
- ✅ Does NOT import `requests`, `numpy`, `pandas`, or any network/exchange library.
- ✅ Does NOT calculate position size or leverage.
- ✅ Does NOT place trades or call any external API.
- ✅ Stateless between invocations.
- ✅ HOLD on any failure or uncertainty.

---

> **AWAITING YOUR APPROVAL BEFORE WRITING ANY PYTHON CODE.**

---

---

# EXECUTION ENGINE — PYTHON ARCHITECTURAL PLAN

> **Agent:** Execution Agent (Room 3)
> **Date:** 2026-04-16
> **Status:** ✅ IMPLEMENTED & TESTED — 70/70 tests passing

---

## 0. DIRECTIVES ACKNOWLEDGED

| Document                  | Key Takeaway                                                                                                                       |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `antigravity-map.md`      | Execution is final authority. Can MODIFY or REJECT strategy signals. Writes to `trade_journal.sqlite` and `decision_log.md`.       |
| `antigravity-map.md` §10  | Python 3.11+, strict type hints, `sqlite3` stdlib ONLY (no SQLAlchemy). `pytest` for testing. Mock all external calls.             |
| `execution-context.md`    | Gatekeeper pipeline: Triage → Circuit Breakers → RR → Position Sizing → Leverage → Output. Dual input: signal + account state.     |
| `trading-limits.md`       | Layer 0 law: 1-2% risk, RR≥1.5, max 10x leverage, 5 trades/day, 5% daily drawdown halt, consecutive loss rules. **ENFORCED HERE.** |
| `risk-math-specialist.md` | Position size = (balance × risk%) / SL distance. Risk [1%,2%]. Max leverage 10x. SL distance = 0 → reject.                         |
| `binance-api-expert.md`   | Order format reference for broker payloads. Stateless, idempotent.                                                                 |

---

## 1. FILE STRUCTURE

All source code lives in `/execution/src/`. Tests live alongside as `test_*.py`.

```
execution/
├── execution-context.md             # (existing) Room spec
└── src/
    ├── __init__.py                   # Package init
    ├── config.py                     # Constants, risk limits, paths
    ├── signal_intake.py              # Parse + validate strategy signal
    ├── account_state.py              # Parse + validate account state (from SQLite or dict)
    ├── circuit_breakers.py           # Drawdown, trade count, consecutive loss checks
    ├── risk_engine.py                # RR validation, position sizing, leverage enforcement
    ├── order_builder.py              # Build broker-ready order payload
    ├── trade_logger.py               # SQLite writer for trade_journal + decision log appender
    ├── engine.py                     # Orchestrator: intake → breakers → risk → order → log
    ├── test_signal_intake.py         # Unit tests for signal parsing
    ├── test_circuit_breakers.py      # Unit tests for all circuit breaker rules
    ├── test_risk_engine.py           # Unit tests for RR, position sizing, leverage
    ├── test_order_builder.py         # Unit tests for order payload building
    ├── test_trade_logger.py          # Unit tests for SQLite write + decision log append
    └── test_engine.py               # Integration test with fixture signals + account states
```

---

## 2. MODULE-BY-MODULE PLAN

### Step 1 — `config.py`

- Path constants: `RUNTIME_DIR`, `TRADE_JOURNAL_PATH`, `DECISION_LOG_PATH`.
- Layer 0 hard limits (from `trading-limits.md`):
  - `MAX_RISK_PCT = 0.02` (2%)
  - `MIN_RISK_PCT = 0.01` (1%)
  - `REDUCED_RISK_PCT = 0.01` (after 2 consecutive losses)
  - `MIN_RR_RATIO = 1.5`
  - `MAX_LEVERAGE = 10.0`
  - `MAX_DAILY_TRADES = 5`
  - `MAX_CONSECUTIVE_LOSSES_HALT = 3`
  - `CONSECUTIVE_LOSS_REDUCE_THRESHOLD = 2`
  - `DAILY_DRAWDOWN_FACTOR = 0.95` (halt if equity ≤ start × 0.95)
  - `PEAK_DRAWDOWN_FACTOR = 0.95` (halt if equity ≤ peak × 0.95)
- Valid enums: `VALID_SIGNALS`, `VALID_ACTIONS`.
- **Zero external dependencies.**

### Step 2 — `signal_intake.py`

- **Single public function:** `validate_signal(signal: dict) → dict | None`
- Checks:
  - All required keys exist: `timestamp`, `symbol`, `signal`, `strategy_used`, `confidence_score`, `suggested_entry`, `suggested_sl`, `suggested_tp`, `reason`.
  - `signal` ∈ `{BUY, SELL, HOLD}`.
  - If signal is `BUY` or `SELL`: `entry`, `sl`, `tp` must be non-null floats > 0.
- Returns validated dict or `None` on failure.
- **Uses only stdlib.**

### Step 3 — `account_state.py`

- **Single public function:** `validate_account_state(state: dict) → dict | None`
- Checks:
  - Required keys: `account_balance`, `daily_equity_start`, `daily_peak_equity`, `daily_trade_count`, `consecutive_losses`, `system_status`.
  - `account_balance > 0`.
  - `system_status` ∈ `{ACTIVE, HALTED}`.
  - No None/NaN in numeric fields.
- Returns validated dict or `None` on failure.

### Step 4 — `circuit_breakers.py`

- **Single public function:** `check_circuit_breakers(account: dict) → tuple[bool, str]`
- Returns `(passed: bool, reason: str)`. If `passed` is `False`, the trade must be rejected + system halted.
- **Rules enforced (per `trading-limits.md` §5 and `execution-context.md` §3 Step 2):**

| Check              | Condition                             | Action                                      |
| ------------------ | ------------------------------------- | ------------------------------------------- |
| System halted      | `system_status == "HALTED"`           | REJECT — system already halted              |
| Daily drawdown     | `balance ≤ daily_equity_start × 0.95` | REJECT + HALT (`HALTED_DAILY_DRAWDOWN`)     |
| Peak drawdown      | `balance ≤ daily_peak_equity × 0.95`  | REJECT + HALT (`HALTED_PEAK_DRAWDOWN`)      |
| Trade limit        | `daily_trade_count >= 5`              | REJECT (`MAX_TRADES_REACHED`)               |
| Consecutive losses | `consecutive_losses >= 3`             | REJECT + HALT (`HALTED_CONSECUTIVE_LOSSES`) |

- **Pure function.** No file I/O.

### Step 5 — `risk_engine.py`

Three public functions:

#### `validate_rr(entry: float, sl: float, tp: float, signal: str) → tuple[bool, float, str]`

- Returns `(valid, rr_ratio, reason)`.
- **BUY:** `SL < ENTRY < TP`, then `RR = (TP-ENTRY) / (ENTRY-SL)`.
- **SELL:** `TP < ENTRY < SL`, then `RR = (ENTRY-TP) / (SL-ENTRY)`.
- If `SL distance == 0` → reject.
- If `RR < 1.5` → reject.

#### `compute_position_size(balance: float, entry: float, sl: float, consecutive_losses: int) → tuple[float, float, float]`

- Returns `(position_size_coins, risk_usd, risk_pct)`.
- **Formula:** `position_size = (balance × risk_pct) / abs(entry - sl)`
- **Risk adjustment:** `risk_pct = 0.02` normally, `0.01` if `consecutive_losses >= 2`.
- If `consecutive_losses >= 3` → returns `(0, 0, 0)` (should be caught by circuit breakers, but defense-in-depth).

#### `enforce_leverage(position_size: float, entry: float, balance: float) → tuple[float, float]`

- Returns `(final_position_size, leverage_used)`.
- `required_leverage = (position_size × entry) / balance`.
- If `required_leverage > 10` → cap at 10x, recompute: `final_size = (balance × 10) / entry`.
- Returns the final position size and actual leverage used.

- **All pure functions.** No side effects. Uses only arithmetic.

### Step 6 — `order_builder.py`

- **Single public function:** `build_order(signal: dict, position_size: float, leverage: float) → dict`
- Assembles the broker-ready payload per `execution-context.md` §4.1:

```json
{
  "symbol": "BTCUSDT",
  "side": "BUY",
  "type": "MARKET",
  "quantity": 1.25,
  "leverage": 5.0,
  "reduce_only": false,
  "oco_sl": 63200.0,
  "oco_tp": 66000.0
}
```

- Rounds `quantity` to a sensible precision (configurable, default 3 decimal places).
- Maps `signal.signal` ("BUY"/"SELL") → `side`.
- **Pure function.**

### Step 7 — `trade_logger.py`

Two public functions:

#### `log_trade(trade_data: dict, db_path: str | None = None) → None`

- Opens (or creates) SQLite database at `db_path` (default: `config.TRADE_JOURNAL_PATH`).
- Creates table if not exists:

```sql
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
```

- Inserts one row per trade.
- **Uses `sqlite3` stdlib only.** No SQLAlchemy.

#### `log_decision(decision: dict, log_path: str | None = None) → None`

- Appends a structured decision entry to `/runtime/decision_log.md`.
- Format:

```
---
### [TIMESTAMP] — [ACTION] — [SYMBOL]
- **Signal:** BUY/SELL/HOLD/REJECT
- **Strategy:** Trend_Pullback
- **Size:** 1.25 coins
- **Leverage:** 5.0x
- **RR:** 2.39
- **Risk USD:** $640.00
- **Reason:** [reason string]
---
```

- Append-only. Never overwrites existing content.

### Step 8 — `engine.py`

Top-level orchestrator. Single public function:

#### `run_execution_engine(signal: dict, account_state: dict) → dict`

```
1. Validate signal via signal_intake.validate_signal()
   → If None: return REJECT("Invalid signal input")

2. SIGNAL TRIAGE:
   → If signal == "HOLD": log NO_ACTION, return REJECT("Signal is HOLD")

3. Validate account state via account_state.validate_account_state()
   → If None: return REJECT("Invalid account state")

4. CIRCUIT BREAKERS via circuit_breakers.check_circuit_breakers()
   → If failed: log REJECT + halt_flag, return REJECT(reason)

5. RR VALIDATION via risk_engine.validate_rr()
   → If failed: log REJECT, return REJECT(reason)

6. POSITION SIZING via risk_engine.compute_position_size()
   → If size <= 0: return REJECT("Position size is zero or negative")

7. LEVERAGE ENFORCEMENT via risk_engine.enforce_leverage()

8. BUILD ORDER via order_builder.build_order()

9. LOG TRADE via trade_logger.log_trade()

10. LOG DECISION via trade_logger.log_decision()

11. Return execution result dict:
    {
      "action": "EXECUTE",
      "order": { broker payload },
      "risk_summary": { position_size, leverage, rr, risk_usd, risk_pct },
      "reason": "Trade approved"
    }
```

- On ANY exception → log REJECT with error, return safe REJECT dict.
- **Does NOT call external APIs.** Order building is formatting only — actual broker execution is deferred to the future orchestrator.

---

## 3. DEPENDENCY LIST

| Package   | Purpose                         |
| --------- | ------------------------------- |
| `sqlite3` | Trade journal database (stdlib) |
| `json`    | Signal/state parsing (stdlib)   |
| `math`    | NaN/Inf checks (stdlib)         |
| `pytest`  | Test runner                     |

**No `numpy`, no `requests`, no `pandas`.** Execution performs only arithmetic + SQLite writes.

---

## 4. OUTPUT SCHEMA

### 4.1 Successful Execution

```json
{
  "action": "EXECUTE",
  "order": {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "MARKET",
    "quantity": 1.25,
    "leverage": 5.0,
    "reduce_only": false,
    "oco_sl": 63200.0,
    "oco_tp": 66000.0
  },
  "risk_summary": {
    "position_size": 1.25,
    "leverage": 5.0,
    "rr_ratio": 2.39,
    "risk_usd": 640.0,
    "risk_pct": 0.02
  },
  "reason": "Trade approved — Trend_Pullback BUY"
}
```

### 4.2 Rejection

```json
{
  "action": "REJECT",
  "order": null,
  "risk_summary": null,
  "reason": "RR ratio 1.2 below minimum 1.5"
}
```

---

## 5. VERIFICATION PLAN

### 5.1 Unit Tests (Automated — `pytest`)

| Test File                  | What It Covers                                                                          |
| -------------------------- | --------------------------------------------------------------------------------------- |
| `test_signal_intake.py`    | Valid BUY/SELL/HOLD parsing, missing keys → None, null entry on BUY → None              |
| `test_circuit_breakers.py` | All 5 breaker conditions: halted, daily DD, peak DD, trade count, consecutive losses    |
| `test_risk_engine.py`      | RR validation (BUY/SELL ordering, boundary 1.5), position sizing math, leverage capping |
| `test_order_builder.py`    | Correct broker payload shape, side mapping, quantity rounding                           |
| `test_trade_logger.py`     | SQLite table creation, row insertion, decision log append                               |
| `test_engine.py`           | Full pipeline: valid signal → EXECUTE, HOLD → REJECT, circuit breaker → REJECT + HALT   |

**Run all:** `pytest execution/src/ -v`

> **ZERO live API calls in tests.** SQLite uses temp paths. Decision log uses temp files.

### 5.2 Integration Checks

1. Feed a valid BUY signal + healthy account → verify EXECUTE output + SQLite row + decision log entry.
2. Feed HOLD signal → verify REJECT(NO_ACTION) with no SQLite write.
3. Trigger daily drawdown → verify REJECT + HALT flag.
4. Feed signal with RR < 1.5 → verify REJECT.
5. Verify leverage capping: signal requiring 15x → capped to 10x + position scaled.

---

## 6. BOUNDARIES ENFORCED

- ✅ Code lives ONLY in `execution/src/`.
- ✅ Reads signal dict from Room 2 (passed as argument, not file I/O).
- ✅ Reads account state dict (passed as argument or queried from `trade_journal.sqlite`).
- ✅ Writes ONLY to `/runtime/trade_journal.sqlite` and appends to `/runtime/decision_log.md`.
- ✅ Does NOT modify `/runtime/current_market_state.json`.
- ✅ Does NOT access `/market_data/`, `/strategy/`, or `/research/`.
- ✅ Does NOT generate trade ideas or modify strategy logic.
- ✅ Does NOT call external APIs (order building is formatting only).
- ✅ Enforces ALL `/rules/trading-limits.md` constraints without exception.
- ✅ REJECT on any failure, ambiguity, or missing data.

---

> **AWAITING YOUR APPROVAL BEFORE WRITING ANY PYTHON CODE.**

---

---

# RESEARCH LAB — PYTHON ARCHITECTURAL PLAN

> **Agent:** Research Agent (Room 4)
> **Date:** 2026-04-16
> **Status:** ✅ IMPLEMENTED & TESTED — 50/50 tests passing

---

## 0. DIRECTIVES ACKNOWLEDGED

| Document                    | Key Takeaway                                                                                                                                         |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `antigravity-map.md`        | Research is read-only analyst. Cannot execute trades, alter strategy logic, or modify risk rules. Runs asynchronously.                               |
| `antigravity-map.md` §10    | Python 3.11+, strict type hints, `sqlite3` stdlib ONLY. No `pandas`, `SQLAlchemy`. `pytest` for testing.                                             |
| `research-context.md`       | Reads `trade_journal.sqlite` (PRAGMA query_only), computes win rate/RR/drawdown/strategy performance, writes `performance_snapshot.json` atomically. |
| `execution-context.md` §4.2 | Room 3's `trades` table schema provides the input data. **Note: `pnl_usd` column is required by Room 4 but not yet written by Room 3.**              |

---

> [!IMPORTANT]
> **Schema Gap:** `research-context.md` expects a `pnl_usd` column in the `trades` table. Room 3 currently does NOT write this column because PnL is only known after a trade is closed (post-fill, not at order time). The Research Lab will:
>
> 1. Check for `pnl_usd` column existence at query time.
> 2. If absent → treat all trades as having `pnl_usd = 0.0` and output the zero-state payload.
> 3. This column will be populated by a future trade lifecycle manager or manual backfill.
>
> **This is a safe, forward-compatible design — Room 4 will work correctly both with and without the column.**

---

## 1. FILE STRUCTURE

All source code lives in `/research/src/`. Tests live alongside as `test_*.py`.

```
research/
├── research-context.md               # (existing) Room spec
└── src/
    ├── __init__.py                    # Package init
    ├── config.py                      # Paths, constants, thresholds
    ├── data_extractor.py              # SQLite read-only query engine
    ├── metrics.py                     # Pure metric computation functions
    ├── strategy_analyzer.py           # Per-strategy performance + degradation flags
    ├── snapshot_writer.py             # Atomic JSON writer for performance_snapshot.json
    ├── engine.py                      # Orchestrator: extract → compute → analyze → write
    ├── test_data_extractor.py         # Unit tests for SQLite queries
    ├── test_metrics.py                # Unit tests for metric math
    ├── test_strategy_analyzer.py      # Unit tests for degradation logic
    ├── test_snapshot_writer.py        # Unit tests for atomic write + validation
    └── test_engine.py                 # Integration test with fixture SQLite DB
```

---

## 2. MODULE-BY-MODULE PLAN

### Step 1 — `config.py`

- Path constants: `TRADE_JOURNAL_PATH`, `SNAPSHOT_PATH`.
- Thresholds:
  - `LOOKBACK_DAYS = 30` (query last 30 operational days)
  - `STRATEGY_UNDERPERFORM_WIN_RATE = 40.0` (flag if < 40%)
- **Zero external dependencies.**

### Step 2 — `data_extractor.py`

- **Single public function:** `extract_trades(db_path: str | None = None) → list[dict]`
- Opens SQLite with `PRAGMA query_only = ON` to prevent any writes or locks.
- Queries `trades` table for rows from last 30 days.
- Checks if `pnl_usd` column exists (`PRAGMA table_info(trades)`):
  - If yes → includes `pnl_usd` in query.
  - If no → returns empty list (triggers zero-state snapshot).
- Returns list of trade dicts or empty list on any error.
- **Failure handling:** database missing, locked, corrupted, or empty → returns `[]`.

### Step 3 — `metrics.py`

Three pure functions:

#### `compute_win_rate(trades: list[dict]) → float`

- `winning = count where pnl_usd > 0`
- `total = len(trades)`
- If `total == 0` → return `0.0`
- Returns `(winning / total) * 100`, clamped to `[0.0, 100.0]`.

#### `compute_average_rr(trades: list[dict]) → float`

- Separate winning trades (`pnl_usd > 0`) and losing trades (`pnl_usd <= 0`).
- `avg_win = mean(winning pnl values)`, `avg_loss = mean(abs(losing pnl values))`.
- If no losses or no wins → return `0.0`.
- Returns `avg_win / avg_loss`.

#### `compute_drawdown_pct(trades: list[dict]) → float`

- Walk trades chronologically, tracking cumulative equity curve.
- `peak = max equity seen`, `current = final equity`.
- `drawdown = ((peak - current) / peak) * 100`.
- Clamped to `>= 0.0`.
- If no trades → return `0.0`.

### Step 4 — `strategy_analyzer.py`

- **Single public function:** `analyze_strategies(trades: list[dict]) → list[dict]`
- Groups trades by `strategy_used`.
- For each strategy:
  - `win_rate = (wins / total) * 100`
  - `net_pnl = sum(pnl_usd)`
  - `status = "UNDERPERFORMING"` if `win_rate < 40.0 AND net_pnl < 0` else `"OPTIMAL"`
- Returns list of `{"strategy_name", "win_rate", "net_pnl", "status"}` dicts.
- **Pure function.** No file I/O.

### Step 5 — `snapshot_writer.py`

- **Single public function:** `write_snapshot(snapshot: dict, path: str | None = None) → None`
- **Validation before write:**
  - `global_win_rate` ∈ `[0.0, 100.0]`
  - `current_drawdown_pct >= 0.0`
  - `total_trades >= 0`
- **Atomic write:** `tempfile.mkstemp()` → `json.dump()` → `os.replace()` (same pattern as Room 1).
- **On validation failure:** writes zero-state payload instead.

### Step 6 — `engine.py`

Orchestrator. Single public function:

#### `run_research_engine(db_path: str | None = None, snapshot_path: str | None = None) → dict`

```
1. Extract trades via data_extractor.extract_trades()
2. IF trades is empty → write zero-state snapshot, return it
3. Compute metrics:
   a. win_rate = metrics.compute_win_rate(trades)
   b. avg_rr = metrics.compute_average_rr(trades)
   c. drawdown = metrics.compute_drawdown_pct(trades)
4. Analyze strategies via strategy_analyzer.analyze_strategies(trades)
5. Assemble snapshot dict
6. Write via snapshot_writer.write_snapshot()
7. Return snapshot dict
```

- On ANY exception → write zero-state payload and return it.
- **No network calls.** Reads one SQLite DB, writes one JSON file.

---

## 3. DEPENDENCY LIST

| Package    | Purpose                               |
| ---------- | ------------------------------------- |
| `sqlite3`  | Read trade journal (stdlib)           |
| `json`     | Write snapshot (stdlib)               |
| `math`     | NaN checks (stdlib)                   |
| `tempfile` | Atomic write (stdlib)                 |
| `os`       | `os.replace` for atomic swap (stdlib) |
| `pytest`   | Test runner                           |

**No `numpy`, no `pandas`, no `requests`.** Research performs only arithmetic + SQL queries.

---

## 4. OUTPUT SCHEMA (STRICT)

```json
{
  "timestamp": "ISO8601",
  "total_trades": 42,
  "global_win_rate": 57.14,
  "average_rr": 1.85,
  "current_drawdown_pct": 2.3,
  "strategy_performance": [
    {
      "strategy_name": "Trend_Pullback",
      "win_rate": 62.5,
      "net_pnl": 450.0,
      "status": "OPTIMAL"
    },
    {
      "strategy_name": "Range",
      "win_rate": 35.0,
      "net_pnl": -120.0,
      "status": "UNDERPERFORMING"
    }
  ]
}
```

**Zero-state (failure/empty):**

```json
{
  "timestamp": "ISO8601",
  "total_trades": 0,
  "global_win_rate": 0.0,
  "average_rr": 0.0,
  "current_drawdown_pct": 0.0,
  "strategy_performance": []
}
```

---

## 5. VERIFICATION PLAN

### 5.1 Unit Tests (Automated — `pytest`)

| Test File                   | What It Covers                                                                         |
| --------------------------- | -------------------------------------------------------------------------------------- |
| `test_data_extractor.py`    | Valid DB → trade list, empty DB → [], missing DB → [], column check for `pnl_usd`      |
| `test_metrics.py`           | Win rate math, average RR (with/without losses), drawdown calculation, zero-trade edge |
| `test_strategy_analyzer.py` | Multi-strategy grouping, OPTIMAL/UNDERPERFORMING flags, single-strategy edge case      |
| `test_snapshot_writer.py`   | Atomic write, validation clamping, zero-state fallback                                 |
| `test_engine.py`            | Full pipeline: populated DB → valid snapshot, empty DB → zero-state, corrupt DB → zero |

**Run all:** `pytest research/src/ -v`

> **ZERO network calls.** SQLite and JSON use temp paths.

### 5.2 Integration Checks

1. Create fixture SQLite with 10 mixed-PnL trades → verify correct win rate, RR, drawdown.
2. Empty SQLite → verify zero-state snapshot.
3. Missing SQLite → verify zero-state snapshot.
4. Strategy with win_rate < 40% and negative PnL → verify `UNDERPERFORMING` flag.

---

## 6. BOUNDARIES ENFORCED

- ✅ Code lives ONLY in `research/src/`.
- ✅ Reads ONLY from `/runtime/trade_journal.sqlite` (PRAGMA query_only = ON).
- ✅ Writes ONLY to `/runtime/performance_snapshot.json` (atomic).
- ✅ Does NOT modify `trade_journal.sqlite` or `current_market_state.json`.
- ✅ Does NOT access `/rules/`, `/execution/` code, `/strategy/` code, or `/market_data/` code.
- ✅ Does NOT execute trades or call external APIs.
- ✅ Runs asynchronously — does not block the execution loop.
- ✅ Zero-state output on any failure.

---

> **AWAITING YOUR APPROVAL BEFORE WRITING ANY PYTHON CODE.**

---

---

# SYSTEM ORCHESTRATOR — PYTHON ARCHITECTURAL PLAN

> **Agent:** Orchestrator Agent (Phase 5)
> **Date:** 2026-04-16
> **Status:** ⏳ AWAITING HUMAN REVIEW — NO CODE WRITTEN

---

## 0. DIRECTIVES ACKNOWLEDGED

| Document                  | Key Takeaway                                                                                           |
| ------------------------- | ------------------------------------------------------------------------------------------------------ |
| `orchestrator-context.md` | `main.py` operates the deterministic loop (Room 1 → Room 2 → Room 3). Room 4 runs asynchronously.      |
| `orchestrator-context.md` | Safety first: maintaining `SYSTEM_STATE`, strict `paper_trading` toggle, and global exception halting. |
| `antigravity-map.md` §10  | Pure Python 3.11+. The Orchestrator does NO calculation, simply coordinates.                           |

---

## 1. FILE STRUCTURE

The orchestrator sits at the project root.

```
Quanta-bot/
├── main.py                   # Central Nervous System loop
├── .env                      # API keys (loaded by main.py ONLY)
├── market_data/src/...       # Room 1
├── strategy/src/...          # Room 2
├── execution/src/...         # Room 3
└── research/src/...          # Room 4
```

---

## 2. MODULE DESIGN: `main.py`

### 2.1 State Management

`main.py` will define a global mutable state dictionary:

```python
SYSTEM_STATE = {
    "running": True,
    "cycle_id": 0,
    "paper_trading": True,               # CRITICAL TOGGLE
    "last_research_cycle": 0             # Track when Room 4 last ran
}
```

### 2.2 Environment/Secrets Loader

- A pure-Python `.env` helper to stay within stdlib limits/avoid dependencies.
- It will parse lines formatted as `KEY=VALUE`, ignoring comments and empty lines.
- Loads `BINANCE_API_KEY` and `BINANCE_SECRET` strictly into local variables, NOT leaking into other modules.
- Evaluates `PAPER_TRADING=True/False` from the `.env` file (defaults to `True` if missing for safety).

### 2.3 The Execution Loop (`while SYSTEM_STATE["running"]:`)

Every iteration executes the deterministic pipeline:

#### **PHASE A — PERCEPTION (Room 1)**

- `from market_data.src.engine import run_market_engine`
- `run_market_engine(...)`
- No return required. `current_market_state.json` is updated downstream.

#### **PHASE B — LOGIC (Room 2)**

- `from strategy.src.engine import run_strategy_engine`
- `signal = run_strategy_engine(...)`
- If `signal["action"] == "HOLD"`:
  - Log hold to console.
  - Increment `cycle_id`.
  - `continue` (restart loop).

#### **PHASE C — EXECUTION (Room 3)**

- Fetch mocked/live Binance account state (via a simple Binance client helper mapping to Room 3's required dict structure).
- `from execution.src.engine import run_execution_engine`
- `result = run_execution_engine(signal, account_state)`
- If `result["action"] == "REJECT"`:
  - Log the rejection reason. `continue`.
- If `result["action"] == "EXECUTE"`:
  - **PAPER TRADING CHECK:**
    - If `SYSTEM_STATE["paper_trading"] == True` → Console print: `⚠️ PAPER TRADE EXECUTED: [Order Details]`. Do NOT send to exchange.
    - If `False` → Sign payload with `BINANCE_SECRET` using `hmac` (stdlib), dispatch to Binance testnet/mainnet, log HTTP response.

#### **PHASE D — RESEARCH (Room 4)**

- Non-critical path. Checked every iteration:
  - `if (SYSTEM_STATE["cycle_id"] - last_research_cycle) >= 100:`
    - Trigger `run_research_engine` asynchronously via `threading.Thread`.
    - Update `last_research_cycle`.
    - Does not block the main loop.

#### **CRITICAL: GLOBAL ERROR HANDLER**

The entire body of the `while` loop is wrapped in a `try...except Exception as e`:

- On `Exception`:
  - Print stack trace + `[FATAL] Unhandled exception in loop. Halting system.`
  - `SYSTEM_STATE["running"] = False`
  - Break gracefully to safely detach and close the process.

---

## 3. DEPENDENCY AWARENESS

- `threading` for Room 4.
- `json`, `os`, `time`, `logging`, `hmac`, `hashlib` for execution prep.
- `urllib.request` (stdlib) for Binance API dispatch.
- It will import the top-level `engine` functions from Rooms 1-4.

---

> **AWAITING YOUR APPROVAL BEFORE WRITING ANY PYTHON CODE.**
