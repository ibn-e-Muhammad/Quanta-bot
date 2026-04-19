# Phase 7.4 Comprehensive Report (v27)

## Scope Completed

Phase 7.4 (Market Regime Gate / Microstructure Survival Layer) is implemented and integrated as a deterministic pre-trade governor above ML thresholding.

No changes were made to:

- strategy logic
- risk sizing model
- `risk_engine.py`
- ML model architecture

## Files Updated

- [research/src/historical_simulator.py](research/src/historical_simulator.py)
- [research/src/capacity_correlation_audit.py](research/src/capacity_correlation_audit.py)
- [research/src/read_ecg.py](research/src/read_ecg.py)

## What Was Implemented

### 1) Deterministic Market Regime Gate

Added pre-ML gate object generation per candidate:

- `vol_score = min(1.0, (atr/close)/0.05)`
- `trend_score = min(1.0, adx/50.0)`
- `volume_score = min(1.0, current_notional / baseline_average_notional)`
  - baseline uses a strictly shifted rolling baseline (20-period mean shifted by 1)
- `spread_score = min(1.0, ((high-low)/close)/0.02)`

Risk pressure:

- `risk_pressure = 0.35*vol_score + 0.25*spread_score + 0.25*(1-volume_score) + 0.15*(1-trend_score)`

Classification:

- SAFE: `< 0.40` => allowed
- WARNING: `0.40 to <0.65` => allowed + ML penalty `0.05`
- NO_TRADE: `>= 0.65` => absolute veto

Trend override (applied after classification):

- if `trend_score > 0.60` and `vol_score < 0.80`:
  - `NO_TRADE -> WARNING`
  - `WARNING -> SAFE`

### 2) WARNING penalty integration

WARNING applies a deterministic subtraction of `0.05` from the Phase 7.3 adjusted ML score before threshold comparison.

### 3) Starvation guard metrics

New audit metrics include:

- SAFE / WARNING / NO_TRADE counts
- warning penalty count
- trend override count
- average risk pressure
- veto share of otherwise ML-valid setups (`phase74_veto_share_ml_valid`)
- reason breakdown

These are included in phase7 artifact outputs and printed in ECG.

## Baseline Simulation Run on v27

Executed baseline simulation on v27 for all tiers and regenerated phase artifacts.

Main outputs:

- [research/portfolio_backtests/v27/phase7_scaling_matrix.json](research/portfolio_backtests/v27/phase7_scaling_matrix.json)
- [research/portfolio_backtests/v27/phase7_ml_metrics.json](research/portfolio_backtests/v27/phase7_ml_metrics.json)
- [research/portfolio_backtests/v27/phase7_ml_report.json](research/portfolio_backtests/v27/phase7_ml_report.json)

ECG run with Phase 7.4 display:

- [research/src/read_ecg.py](research/src/read_ecg.py)

## Phase 7.4 Baseline Results (v27)

### TIER_100K (representative)

- Trades: `281`
- PF: `2.085`
- NetPnL%: `97.56%`
- MaxDD%: `-3.53%`

Phase 7.4 microstructure metrics:

- SAFE/WARN/NO_TRADE: `406 / 415 / 82`
- Avg Risk Pressure: `0.5323`
- Warning Penalties: `415`
- Trend Overrides: `340`
- **Veto Share (ML-valid): `7.59%`** ✅ within requested 5–10% guard band

## Phase 7.4 Reporting Deltas (vs v24 phase62 baseline)

From ECG delta block after v27 rerun:

- TIER_100K: `ΔPF +0.8128`, `ΔNetPnL% +59.14`, `ΔMaxDD% +2.35`
- TIER_10K: `ΔPF +0.8129`, `ΔNetPnL% +59.15`, `ΔMaxDD% +2.35`
- TIER_1M: `ΔPF +0.7638`, `ΔNetPnL% +55.65`, `ΔMaxDD% +1.30`

## Judgment: Will this improve Quanta-bot output/value?

Short answer: **Yes, conditionally.**

### Why it adds value

- Deterministic, explainable veto logic reduces execution in adverse microstructure states.
- Avoids lookahead by using current candle + shifted historical baseline only.
- Warning-zone penalty introduces controlled caution rather than full blocking.
- Veto share currently lands in target zone (7.59%), suggesting no immediate starvation.

### Risks to monitor

- Acceptance rate dropped materially (~24%), so opportunity cost is non-trivial.
- Overly strict gate in certain environments could cap upside if market transitions quickly.
- Thresholds (`0.40/0.65`) may still need calibration by regime family.

### Practical conclusion

Phase 7.4 is likely to improve robustness and risk-adjusted behavior of Quanta-bot, but final benefit depends on follow-up robustness deltas (especially rolling-window floor behavior). The current baseline indicates healthy veto control without breaching starvation limits.
