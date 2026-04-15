# LAYER 0: GLOBAL TRADING LIMITS & RISK HANDCUFFS

---

## 1. SUPREME DIRECTIVE

This document is the absolute law of the Quanta-Elite system.
The `/execution` engine is the **sole enforcement authority** and MUST:

- Validate every incoming signal from `/strategy`
- Modify or reject any rule violations
- Log all enforcement actions

> SURVIVE FIRST. PROFIT SECOND.

---

## 2. DEFINITIONS (MANDATORY FOR CONSISTENCY)

- **Account Balance:** Total equity including unrealized PnL.
- **Risk Per Trade (%):** Amount lost if stop-loss is hit ÷ total account balance.
- **R-Multiple:** Reward ÷ Risk (TP distance ÷ SL distance).
- **Daily Equity Start:** Account balance at the start of the trading day.
- **Daily Peak Equity:** Highest equity reached during the current trading day.

---

## 3. HARD CONSTRAINTS (NON-NEGOTIABLE)

### 3.1 Risk Per Trade

- MUST be between **1% and 2%**.
- If strategy exceeds:
  - Execution MUST scale position size DOWN.
  - OR reject the trade if scaling is not possible.

### 3.2 Risk/Reward Ratio

- Minimum RR = **1.5**.
- Preferred range: **1.5 → 3.0**.
- If RR < 1.5:
  - Execution MUST reject the trade.

### 3.3 Leverage Limits

- Absolute max: **10x**.
- Preferred range: **3x → 10x**.
- If exceeded:
  - Execution MUST reduce leverage OR reject trade.

### 3.4 Trade Frequency

- Max trades per day: **5**.
- If limit reached:
  - All further signals MUST be rejected.

### 3.5 Mandatory Stop-Loss

- EVERY trade MUST include:
  - Entry price
  - Stop-loss
  - Take-profit
- Missing any → automatic rejection.

---

## 4. POSITION SIZING RULE (CRITICAL)

Execution MUST compute position size using:
`Position Size = (Account Balance × Risk %) ÷ Stop-Loss Distance`

Constraints:

- Size must NOT exceed available margin.
- Must comply with leverage limits.
- Must be recalculated for every single trade.

---

## 5. DRAWDOWN & CIRCUIT BREAKERS

### 5.1 Daily Drawdown Limit

- Max daily loss: **5% of Daily Equity Start**
- **Trigger Condition:** `(Current Equity ≤ Daily Equity Start × 0.95)`
- **Action:** - Immediately HALT all trading.
  - Reject ALL incoming signals.
  - Log system state as: `"HALTED_DAILY_DRAWDOWN"`.

### 5.2 Peak-to-Trough Drawdown Protection

- **Trigger Condition:** `(Current Equity ≤ Daily Peak Equity × 0.95)`
- **Action:** - HALT trading for the rest of the session.
  - Prevent profit give-back.

### 5.3 Consecutive Loss Control

- After **2 consecutive losses**: Reduce position size by **50%**.
- After **3 consecutive losses**: HALT trading for the session.

### 5.4 Revenge Trading Lockout

- After any stopped-out trade: Enforce cooldown of **15 minutes**.
- **Action:** Reject any signal during the cooldown window.

---

## 6. EXECUTION OVERRIDE LOGIC (MANDATORY BEHAVIOR)

Execution MUST behave as follows:

- Strategy requests invalid leverage → Adjust to valid range OR reject.
- Strategy provides no stop-loss → Reject.
- Strategy violates RR constraint → Reject.
- Strategy exceeds risk % → Scale position size OR reject.
- System in HALTED state → Reject ALL signals regardless of quality.

---

## 7. STATE TRACKING REQUIREMENTS

Execution MUST track the following in real-time by querying its own `/runtime/trade_journal.sqlite` database:

- Daily trade count
- Consecutive losses
- Current equity
- Daily equity start
- Daily peak equity
- Cooldown timers

---

## 8. FAILURE SAFETY RULES

- If any required data is missing → REJECT trade.
- If calculation fails → REJECT trade.
- If runtime state is inconsistent → HALT system.

> When in doubt, DO NOTHING.

---

## FINAL PRINCIPLE

> The system is designed to avoid catastrophic loss, not to chase maximum profit.
> Any trade that threatens survival MUST NOT be executed.
