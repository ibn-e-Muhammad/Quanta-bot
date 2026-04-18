# Phase 7.1 Comprehensive Report (v27)

## 1) Executive Summary

Phase 7.1 is complete and validated on `v27`.

Key outcomes:

- ML layer is now **leak-resistant**, **fail-open**, and **runtime-safe**.
- Strategy/risk/concurrency core logic remained unchanged (ML is additive veto only).
- Performance improved materially vs locked baseline (`v24 phase62`) across all tiers.
- No runtime ML fallback or inference errors occurred in the v27 run.

---

## 2) Scope and Constraints Preserved

The following were intentionally preserved:

- Entry/exit strategy math
- ADX/ATR and position sizing behavior
- Drawdown governor and lock semantics
- Phase 6.2 priority ranking logic

Phase 7.1 changed only:

- Dataset hygiene and leakage controls
- Model training constraints and guardrails
- Inference contract (strict feature order + fail-open safety)
- Reporting/audit metrics for ML behavior

---

## 3) Phase 7.1 Technical Changes

### 3.1 Dataset and leakage hardening

- Training source: **executed trades from `v24` / `tier_100k`**.
- Target label: `net_pnl_usd > 0`.
- Leakage fixes:
  - Removed post-trade leakage features and realized-cost dependencies.
  - `cost_estimate` now derived deterministically from notional + slippage schedule + taker fee assumptions.
  - Removed `score` from ML features for stricter online/offline parity.

### 3.2 Feature contract and preprocessing

Persisted feature schema (`feature_config.json`):

- `trade_direction`
- `atr_value`
- `adx_value`
- `ema_distance`
- `cost_estimate`
- `volatility_regime`
- `candle_range`
- `trend_strength`
- `hour_of_day`
- `day_of_week`

Preprocessing:

- Standard-style scaling with persisted per-feature mean/std.
- Inference enforces strict feature order alignment against persisted config.

### 3.3 Model constraints and publish safety

Model:

- `RandomForestClassifier`
- Constrained capacity (`n_estimators=150`, `max_depth=5`, `min_samples_leaf=12`, `random_state=42`)
- Time-ordered split (70/30), no shuffle
- Optional class weighting only when needed

Guardrails (must pass before artifact publish):

- Accuracy not unrealistically high
- ROC-AUC not unrealistically high
- Probability distribution not collapsed (std floor)
- Probability mean remains in sane range

Artifact publication:

- Atomic replacement path used after successful validation checks.

### 3.4 Runtime inference safety

- `ML_THRESHOLD = 0.52`
- Veto-only filter applied before execution.
- Fail-open policy: if artifacts/inference fail, default to accept (`prob=1.0`) and count events.
- Runtime counters added:
  - `ml_fallback_count`
  - `ml_inference_error_count`
  - score distribution stats (`min/max/mean/std`)

---

## 4) Training Validation (Phase 7.1)

From `ml/artifacts/training_metrics.json`:

- Accuracy: **0.5419**
- Precision: **0.5542**
- Recall: **0.5750**
- ROC-AUC: **0.5570**
- Mean predicted probability: **0.5061**
- Probability std dev: **0.0801**
- Train rows: **360**
- Test rows: **155**
- Status: **passed**
- Fail reasons: **none**

Interpretation:

- Metrics are realistic (no overfit signature).
- Probability spread is healthy (not degenerate).
- Guardrails passed and artifacts were safely published.

---

## 5) v27 Portfolio Outcomes (Phase 7.1 Active)

From `v27/phase7_scaling_matrix.json`:

| Tier      | Trades | Win Rate % |     PF | NetPnL % | MaxDD % | ML Acceptance | Filtered | Fallback | Inference Errors |
| --------- | -----: | ---------: | -----: | -------: | ------: | ------------: | -------: | -------: | ---------------: |
| TIER_10K  |    321 |     64.486 | 2.2578 | 126.9227 | -3.5450 |        0.3763 |      532 |        0 |                0 |
| TIER_100K |    321 |     64.486 | 2.2578 | 126.9227 | -3.5450 |        0.3763 |      532 |        0 |                0 |
| TIER_1M   |    320 |     64.375 | 2.1397 | 115.1274 | -3.8039 |        0.3760 |      531 |        0 |                0 |

ML score separation (quality signal):

- Avg executed score: ~**0.5744–0.5747**
- Avg rejected score: ~**0.4369**
- Executed > Rejected by ~**0.1375** points across tiers.

Distribution sanity:

- Mean score ~**0.4887**
- Std ~**0.0855**
- Min/Max approximately **0.222 / 0.717**

---

## 6) Impact vs Locked Baseline (v24 Phase62)

From `v27/phase7_ml_report.json` (`ml_impact_vs_v24_phase62`):

| Tier      |    Δ PF | Δ NetPnL % | Δ MaxDD % |
| --------- | ------: | ---------: | --------: |
| TIER_10K  | +0.9860 |   +88.5017 |   +2.3383 |
| TIER_100K | +0.9860 |   +88.5017 |   +2.3383 |
| TIER_1M   | +0.9060 |   +82.4957 |   +2.1833 |

Notes:

- Positive Δ MaxDD % indicates less negative drawdown (shallower drawdowns).
- Improvements are consistent across all capital tiers.

---

## 7) Acceptance Criteria Status

| Criterion                                                      | Status                   |
| -------------------------------------------------------------- | ------------------------ |
| No strategy/risk/governor logic drift beyond ML veto insertion | PASS                     |
| Leakage-resistant dataset build                                | PASS                     |
| Strict feature-order training/inference contract               | PASS                     |
| Anti-overfit guardrails enforced and passed                    | PASS                     |
| Fail-open runtime behavior implemented                         | PASS                     |
| Runtime reliability (fallback/errors) in v27                   | PASS (0 / 0)             |
| Non-trivial acceptance (not all-pass or all-block)             | PASS (~37.6% acceptance) |
| Executed-vs-rejected ML score separation                       | PASS                     |
| Tiered run completed with artifacts                            | PASS                     |

---

## 8) Produced Artifacts (v27)

- `phase7_scaling_matrix.json`
- `phase7_ml_metrics.json`
- `phase7_ml_report.json`
- Standard tier DB outputs (`portfolio_backtest_tier_10k.db`, `...100k.db`, `...1m.db`)

Model artifacts:

- `ml/artifacts/model.pkl`
- `ml/artifacts/feature_config.json`
- `ml/artifacts/training_metrics.json`

---

## 9) Final Conclusion

Phase 7.1 objectives were met. The ML filter is now robust against prior leakage/overfitting failure modes, preserves baseline strategy mechanics, operates fail-open safely, and improves portfolio-level performance metrics versus the locked `v24 phase62` baseline across all tiers.
