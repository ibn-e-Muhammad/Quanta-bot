# Phase 7.3 Comprehensive Report (v27)

## Objective

Phase 7.3 upgraded ML from a static gate to a regime-adaptive, acceptance-stabilized execution layer while preserving:

- strategy logic
- risk model
- Phase 6.2 ranking
- ML model architecture and features

## Code Changes Implemented

### 1) Dynamic regime thresholds + adaptive clamp

Updated [research/src/historical_simulator.py](research/src/historical_simulator.py):

- Added regime threshold function:
  - trending & high vol: 0.50
  - trending & low vol: 0.52
  - ranging & high vol: 0.54
  - ranging & low vol: 0.56
- Regime computed per candidate from persisted inference features (`adx`, `atr_ratio`), using raw decimal ratio for high-vol check.
- Added rolling acceptance stabilizer:
  - decision deque size 100
  - warmup denominator rule: `max(1, len(deque))` until full
  - modifier update each buffer: +0.01 if acceptance > 0.45, -0.01 if < 0.25
  - clamp: [-0.02, +0.04]

### 2) ML influence amplifier

Updated [research/src/historical_simulator.py](research/src/historical_simulator.py):

- Applied pre-threshold score amplification:
  - `adjusted_score = raw_ml_prob + ((raw_ml_prob - 0.50) * 0.15)`
- Threshold comparison now uses `adjusted_score` vs dynamic threshold.

### 3) New Phase 7.3 metrics

Updated [research/src/historical_simulator.py](research/src/historical_simulator.py):

- `acceptance_by_regime`
- `avg_threshold_modifier`

### 4) Reporting extensions

Updated [research/src/capacity_correlation_audit.py](research/src/capacity_correlation_audit.py):

- Added `acceptance_by_regime` and `avg_threshold_modifier` to phase7 scaling rows.
- Added tier maps to `phase7_ml_metrics.json`:
  - `acceptance_by_regime_by_tier`
  - `avg_threshold_modifier_by_tier`

Updated [research/src/read_ecg.py](research/src/read_ecg.py):

- Added prints for:
  - `avg_threshold_modifier`
  - `acceptance_by_regime`

## Validation Run Executed

Re-ran Phase 7.2 validator using adaptive Phase 7.3 logic:

- Command target: [research/src/phase72_robustness_runner.py](research/src/phase72_robustness_runner.py)
- Output: [research/portfolio_backtests/v27/phase72_robustness_report.json](research/portfolio_backtests/v27/phase72_robustness_report.json)

## Robustness Outcome (Post-Phase 7.3)

From [research/portfolio_backtests/v27/phase72_robustness_report.json](research/portfolio_backtests/v27/phase72_robustness_report.json):

- Final classification: **OVERFIT_OR_FRAGILE**
- Tests passed: **3 / 6**
- Failed tests:
  - `test_2_rolling_stability`
  - `test_4_regime_performance`
  - `test_5_threshold_sweep`

### Per-test status

- Test 1 (Time OOS): **PASS**
- Test 2 (Rolling Stability): **FAIL**
  - PF std: **2.7884** (target <= 0.4)
- Test 3 (Cross-symbol): **PASS** ✅
  - Acceptance stability constraint now passed across volatility buckets.
- Test 4 (Regimes): **FAIL**
  - `LOW_VOLATILITY` PF: **0.8523** (< 1.1 floor)
- Test 5 (Threshold sweep): **FAIL**
  - Curve smoothness passed, but best PF remains isolated.
- Test 6 (Shuffle sanity): **PASS** ✅
  - PF drop: **0.4059**
  - NetPnL% drop: **49.5155**

## Delta vs prior state (Phase 7.2 static)

Net effect after Phase 7.3:

- Improved from **2/6** to **3/6** tests passed.
- Requested Test 3 target achieved (now PASS).
- Test 2 remains unstable and does not meet strict PF floor constraints.

## Key Interpretation

Phase 7.3 successfully improved acceptance stability and strengthened shuffle degradation signal, but did not eliminate rolling-window fragility. Under the current strict rubric, robustness is improved but still insufficient for "robust edge" classification.

## Artifacts

- Updated robustness JSON: [research/portfolio_backtests/v27/phase72_robustness_report.json](research/portfolio_backtests/v27/phase72_robustness_report.json)
- This report: [research/portfolio_backtests/v27/phase73_comprehensive_report.md](research/portfolio_backtests/v27/phase73_comprehensive_report.md)
