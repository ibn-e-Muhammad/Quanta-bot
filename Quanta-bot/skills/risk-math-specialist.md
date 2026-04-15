# RISK MATH SPECIALIST — SYSTEM SKILL MODULE

---

## 1. PURPOSE

Defines strict mathematical rules for capital preservation and position sizing.

This module overrides all optimization instincts.

---

## 2. CORE RULE

> Profitability is irrelevant if risk is not bounded.

---

## 3. POSITION SIZING FORMULA (ABSOLUTE)

```text
position_size = (account_balance × risk_pct) ÷ stop_loss_distance
```

Where:

* risk_pct ∈ [0.01, 0.02]
* stop_loss_distance = |entry - SL|

---

## 4. RISK LIMITS

* max risk per trade = 2%
* min risk per trade = 1%
* max daily drawdown = 5%
* max consecutive loss reduction applies

---

## 5. INVALID TRADE CONDITIONS

Reject trade if ANY:

* SL is missing
* TP is missing
* SL distance = 0
* RR < 1.5
* leverage > 10x

---

## 6. DRAWDOWN LOGIC

If:

* equity ≤ 95% of daily start
  → HALT SYSTEM

If:

* equity ≤ 95% of peak
  → HALT SYSTEM

If:

* consecutive losses ≥ 3
  → HALT SYSTEM

---

## 7. LEVERAGE MODEL

```text
required_leverage = notional_value / account_balance
```

Rules:

* cap at 10x
* never exceed exchange limits
* never allow uncollateralized exposure

---

## 8. CAPITAL PHILOSOPHY

> Loss avoidance is a mathematical constraint, not a strategy preference.

---
