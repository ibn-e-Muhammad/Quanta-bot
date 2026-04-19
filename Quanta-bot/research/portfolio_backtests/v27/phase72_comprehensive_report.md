# Phase 7.2 Comprehensive Robustness Report (v27)

## Scope

This phase was run as **validation-only**.

Locked constraints preserved:

- Strategy logic unchanged
- Risk model unchanged
- ML architecture unchanged
- Feature set unchanged
- Baseline threshold unchanged (`ML_THRESHOLD = 0.52`)

Primary artifact:

- [research/portfolio_backtests/v27/phase72_robustness_report.json](research/portfolio_backtests/v27/phase72_robustness_report.json)

Supporting implementation:

- [research/src/phase72_robustness_runner.py](research/src/phase72_robustness_runner.py)
- [research/src/historical_simulator.py](research/src/historical_simulator.py)

---

## Validation Setup

- Source timeline: `2021-04-11 20:00:00+00:00` to `2026-04-14 16:00:00+00:00`
- Source universe: [research/portfolio_backtests/v27/portfolio_results_tier_100k.sqlite](research/portfolio_backtests/v27/portfolio_results_tier_100k.sqlite)
- Model artifacts:
  - [ml/artifacts/model.pkl](ml/artifacts/model.pkl)
  - [ml/artifacts/feature_config.json](ml/artifacts/feature_config.json)
  - [ml/artifacts/training_metrics.json](ml/artifacts/training_metrics.json)

---

## Test Results Summary

| Test                                 | Result | Key reason                                                                    |
| ------------------------------------ | ------ | ----------------------------------------------------------------------------- |
| Test 1 — Time OOS                    | PASS   | PF and drawdown constraints passed in all 3 segments                          |
| Test 2 — Rolling 90D / 30D           | FAIL   | PF variance too high, worst window PF below 1.2, catastrophic windows present |
| Test 3 — Cross-Symbol Generalization | FAIL   | PF passed, but acceptance stability failed all buckets (outside ±15% global)  |
| Test 4 — Regime Performance          | PASS   | ≥2 regimes with PF ≥ 1.6 and no regime collapse                               |
| Test 5 — Threshold Sweep             | FAIL   | Best PF isolated and trade-count collapse at high threshold                   |
| Test 6 — Shuffle Sanity              | FAIL   | Degradation exists but not strong enough per strict pass thresholds           |

---

## Test-by-Test Detail

### Test 1 — Time-Based OOS (PASS)

Pass criteria: PF ≥ 1.5 all segments, MaxDD ≤ 10% all segments.

| Segment | Trades | Win Rate % |     PF | NetPnL % | MaxDD % | Acceptance |
| ------- | -----: | ---------: | -----: | -------: | ------: | ---------: |
| A       |     70 |      65.71 | 2.8007 |  25.5064 | -2.3303 |     0.3057 |
| B       |    135 |      68.89 | 2.6258 |  42.9446 | -3.0487 |     0.4054 |
| C       |    116 |      58.62 | 1.8302 |  24.1857 | -3.5450 |     0.4042 |

Assessment: OOS remains profitable across chronological splits, with weaker but still acceptable late-cycle PF.

### Test 2 — Rolling Window Stability (FAIL)

Configuration: 90-day windows, 30-day step, 58 windows.

Key metrics:

- PF mean: **3.2976**
- PF std: **2.6105** (criterion ≤ 0.4) ❌
- Worst PF window: **0.8063** (criterion ≥ 1.2) ❌
- Catastrophic windows (PF < 1.0): **2** (criterion: none) ❌

Assessment: Results are highly path-dependent across rolling windows.

### Test 3 — Cross-Symbol Generalization (FAIL)

Bucketing method: average ATR percentile (bottom/middle/top 33%).
Global acceptance baseline: **0.3763**. Allowed deviation band: ±0.0564.

| Group       | Trades |     PF | NetPnL % | MaxDD % | Acceptance | Δ vs Global | Within ±15%? |
| ----------- | -----: | -----: | -------: | ------: | ---------: | ----------: | ------------ |
| LOW_VOL_ALT |     89 | 2.7858 |  31.2755 | -2.0980 |     0.2557 |     -0.1206 | No           |
| MID_VOL     |    153 | 2.1021 |  40.7383 | -5.0430 |     0.4554 |     +0.0790 | No           |
| HIGH_VOL    |    189 | 1.7481 |  38.6763 | -4.3553 |     0.5192 |     +0.1429 | No           |

Assessment: PF generalizes, but acceptance-rate stability does not. This indicates symbol-cluster sensitivity in ML gating behavior.

### Test 4 — Regime-Based Performance (PASS)

Axis used exactly as requested (persisted inference-time fields):

- TRENDING: `adx > 25`
- RANGING: `adx <= 25`
- HIGH_VOLATILITY: `atr/close > 0.02`
- LOW_VOLATILITY: `atr/close <= 0.02`

| Regime          | Trades | Win Rate % |     PF | NetPnL % | Acceptance |
| --------------- | -----: | ---------: | -----: | -------: | ---------: |
| TRENDING        |    283 |      63.60 | 2.2166 | 108.8088 |     0.3582 |
| RANGING         |     76 |      57.89 | 1.7413 |  13.4883 |     0.5938 |
| HIGH_VOLATILITY |    280 |      66.79 | 2.4093 | 115.6247 |     0.3733 |
| LOW_VOLATILITY  |     86 |      56.98 | 1.3926 |  11.6329 |     0.5181 |

Assessment: No collapse in ranging; multiple regimes exceed PF 1.6.

### Test 5 — Threshold Sensitivity Sweep (FAIL)

Thresholds: 0.48, 0.50, 0.52, 0.55, 0.60.

| Threshold | Trades |     PF | NetPnL % | MaxDD % | Acceptance |
| --------: | -----: | -----: | -------: | ------: | ---------: |
|      0.48 |    411 | 1.7309 |  98.3720 | -4.5571 |     0.5415 |
|      0.50 |    356 | 2.1023 | 132.3257 | -4.0488 |     0.4461 |
|      0.52 |    321 | 2.2578 | 126.9227 | -3.5450 |     0.3763 |
|      0.55 |    249 | 2.6944 | 114.3018 | -4.5577 |     0.2583 |
|      0.60 |    128 | 2.9560 |  49.0720 | -2.5691 |     0.1149 |

Summary checks:

- Max adjacent PF jump: 0.4366 (smoothness criterion pass)
- Near-best count within 95% of best PF: 1 (isolated optimum) ❌
- Adjacent trade retention includes 0.5141 (< 0.6) ❌

Assessment: Higher thresholds improve PF but at sharp participation loss. Sensitivity profile is not robust enough under strict criteria.

### Test 6 — Shuffle Monte Carlo Sanity (FAIL)

`SHUFFLE_ML` executed in-simulator and restricted to in-buffer probability reassignment.

Baseline vs Shuffle:

- Baseline PF: **2.2578**
- Shuffle PF: **2.0176**
- PF drop: **0.2402** (required: ≥0.3 or PF in 1.0–1.2) ❌
- Baseline NetPnL%: **126.9227**
- Shuffle NetPnL%: **95.8639**
- NetPnL drop: **31.0588** (required: ≥20) ✅

Assessment: Degradation exists, but the PF deterioration was not strong enough for the strict pass definition.

---

## Aggregated Robustness Diagnostics

- PF distribution: min 0.8063, max 11.7322, mean 2.9444, std 2.1487
- NetPnL% distribution: min -0.6386, max 132.3257, mean 17.4103, std 32.9273
- MaxDD% distribution: min -5.0430, max 0.0, mean -1.7792, std 1.1581

Worst-case highlights:

- Worst PF case: `rolling_w014` (PF 0.8063)
- Worst NetPnL case: `rolling_w058` (-0.6386%)
- Worst drawdown case: `symbol_group_mid_vol` (-5.0430%)

Acceptance stability:

- Baseline global acceptance: 0.3763
- Observed acceptance range: 0.0769 to 0.5938
- Std of acceptance: 0.1144

---

## Final Verdict

**Classification: ❌ OVERFIT_OR_FRAGILE**

Reason:

- Passed: 2/6 tests
- Failed tests:
  - `test_2_rolling_stability`
  - `test_3_cross_symbol_generalization`
  - `test_5_threshold_sweep`
  - `test_6_shuffle_sanity`

Interpretation:

- The system shows real edge in OOS segmentation and regime slices.
- However, strict robustness criteria reveal instability across rolling windows, symbol buckets (acceptance behavior), threshold sensitivity at higher thresholds, and insufficiently strong degradation under shuffled assignment.
- Under the requested hard rules, this should be treated as **non-robust / fragile** at this stage.

---

## Generated Artifacts

- Main JSON: [research/portfolio_backtests/v27/phase72_robustness_report.json](research/portfolio_backtests/v27/phase72_robustness_report.json)
- Detailed run DBs: [research/portfolio_backtests/v27/phase72_runs](research/portfolio_backtests/v27/phase72_runs)
- This report: [research/portfolio_backtests/v27/phase72_comprehensive_report.md](research/portfolio_backtests/v27/phase72_comprehensive_report.md)
