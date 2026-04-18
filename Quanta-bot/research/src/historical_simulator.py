import pandas as pd
import numpy as np
import sqlite3
import os
import json
import sys
import math
from pathlib import Path
from datetime import timedelta
from itertools import combinations

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.edge_compression_engine import apply_edge_compression
from research.src.filters_engine import apply_filters
from execution.src.risk_engine import RiskEngine

from research.src.trade_quality_engine import evaluate_trade_quality
from research.src.fee_filter import is_fee_viable

from research.src.regime_classifier import classify_regime
from research.src.strategy_router import route_signal
from research.src.strategies import breakout_engine, mean_reversion_engine, expansion_engine

from research.src.engine_governor import (
    initial_state, tick_state, record_trade, get_pf,
    ENGINE_PRIORITY
)

# ── Phase 6 Configuration ──────────────────────────────────────────────
EXEC_TIMEFRAMES     = ["4h"]
MACRO_TF            = "4h"
MIN_GAP_MINUTES     = 240        # 1 full 4H candle cooldown after any close
TF_MINUTES          = {"4h": 240}

RISK_PER_TRADE      = 0.005      # 0.5%
MAX_CONCURRENT      = 2          # strict prop-firm concurrency
DAILY_DD_CAP        = -0.015     # -1.5% daily realized DD lock
MAX_NOTIONAL_MULT   = 5.0
ATR_MIN_RATIO       = 0.003      # atr/price must exceed this (anti-fee-trap)


class HistoricalSimulator:
    def __init__(self, config_path, data_dir, db_path, config_override=None):
        self.config_path = config_path
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        self.config_override = config_override or {}

        self.data_dir  = data_dir
        self.db_path   = db_path
        self.ema_fast  = self.config.get("market_data", {}).get("ema_fast",  9)
        self.ema_slow  = self.config.get("market_data", {}).get("ema_slow",  24)
        self.ema_trend = self.config.get("market_data", {}).get("ema_trend", 200)

        self.filters_config = self.config_override.get("filters", {})
        self.collapse_warning = False
        self.filter_stats = {}
        self.daily_dd_breaches = 0
        self.initial_balance = float(self.config_override.get("initial_balance", 10000.0))
        self.audit_metrics = self._init_audit_metrics()

        self.setup_db()

    def _init_audit_metrics(self):
        return {
            "initial_balance": self.initial_balance,
            "final_balance": self.initial_balance,
            "total_signals_generated": 0,
            "total_signals_executed": 0,
            "rejected_concurrency_lock": 0,
            "rejected_symbol_open_lock": 0,
            "rejected_symbol_cooldown_lock": 0,
            "rejected_other": 0,
            "rejected_signals": 0,
            "duplicate_signal_rejection_rate": 0.0,
            "cluster_event_count": 0,
            "cluster_event_total_size": 0,
            "cluster_event_avg_size": 0.0,
            "same_candle_multi_symbol_activations": 0,
            "co_activation_pairs": {},
        }

    @staticmethod
    def _slippage_rate_from_notional(notional):
        if notional <= 50000:
            return 0.0002
        penalty_steps = math.floor((notional - 50000) / 50000)
        return 0.0002 + (penalty_steps * 0.0001)

    # ── DB ─────────────────────────────────────────────────────────────
    def setup_db(self):
        conn   = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historical_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, symbol TEXT, interval TEXT,
                regime TEXT, strategy_used TEXT, engine_state TEXT,
                signal_type TEXT,
                entry_price REAL, tp_price REAL, sl_price REAL, exit_price REAL,
                outcome INTEGER, duration_minutes INTEGER,
                rsi REAL, adx REAL, atr REAL,
                ema_9_24_dist REAL, ema_200_dist REAL, volatility_regime TEXT,
                hour_of_day INTEGER, day_of_week INTEGER, context_score REAL,
                notional_usd REAL, margin_used REAL,
                fees_paid REAL, slippage_paid REAL,
                net_pnl_usd REAL, running_balance REAL
            )
        ''')
        conn.commit(); conn.close()

    # ── Indicators ─────────────────────────────────────────────────────
    def calculate_indicators(self, df):
        if df is None or df.empty: return df
        c = df['close']; h = df['high']; l = df['low']

        df['close_prev']    = c.shift(1)
        df['ema_fast']      = c.ewm(span=self.ema_fast,  adjust=False).mean()
        df['ema_slow']      = c.ewm(span=self.ema_slow,  adjust=False).mean()
        df['ema_trend']     = c.ewm(span=self.ema_trend, adjust=False).mean()
        df['ema_50']        = c.ewm(span=50,             adjust=False).mean()
        df['ema_50_slope']  = df['ema_50'].diff(5)

        df['sma_20']        = c.rolling(20).mean()
        df['std_20']        = c.rolling(20).std()
        df['upper_bb']      = df['sma_20'] + 2 * df['std_20']
        df['lower_bb']      = df['sma_20'] - 2 * df['std_20']
        df['upper_bb_prev'] = df['upper_bb'].shift(1)
        df['lower_bb_prev'] = df['lower_bb'].shift(1)

        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = -delta.clip(upper=0).rolling(14).mean()
        df['rsi'] = 100 - 100 / (1 + gain / loss)

        tr  = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        df['atr']     = tr.rolling(14).mean()
        df['atr_sma'] = df['atr'].rolling(14).mean()

        plus_dm  = h.diff(); minus_dm = -l.diff()
        plus_dm  = np.where((plus_dm  > minus_dm) & (plus_dm  > 0), plus_dm,  0.0)
        minus_dm = np.where((minus_dm > plus_dm)  & (minus_dm > 0), minus_dm, 0.0)
        plus_di  = 100 * pd.Series(plus_dm,  index=df.index).rolling(14).mean() / df['atr']
        minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(14).mean() / df['atr']
        dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        df['adx'] = dx.rolling(14).mean()
        return df

    # ── Data loading ───────────────────────────────────────────────────
    def load_tf(self, symbol, tf):
        path = os.path.join(self.data_dir, f"{symbol}_{tf}_history.csv")
        if not os.path.exists(path): return None
        df = pd.read_csv(path)
        df['datetime_utc'] = pd.to_datetime(df['datetime_utc'])
        df = df.sort_values('datetime_utc').drop_duplicates('datetime_utc').reset_index(drop=True)
        return self.calculate_indicators(df)

    def load_all_tfs(self, symbol):
        needed = set(EXEC_TIMEFRAMES) | {MACRO_TF}
        return {tf: self.load_tf(symbol, tf) for tf in needed}

    # ── Signal generation ──────────────────────────────────────────────
    def generate_signals_for_tf(self, df_exec, df_macro, exec_tf):
        if df_exec is None or df_macro is None: return []
        df_m   = df_macro.add_suffix('_4h').rename(columns={'datetime_utc_4h': 'datetime_utc'})
        merged = pd.merge_asof(df_exec, df_m, on='datetime_utc', direction='backward').dropna()

        records = merged.to_dict('records')
        signals = []
        for i, row in enumerate(records):
            if i == 0: continue
            regime        = classify_regime(row)
            strategy_name = route_signal(regime)
            if strategy_name != "expansion_engine": continue

            sig_dict = expansion_engine.generate_signal(row)
            if sig_dict['signal'] == 0: continue

            bull_4h = (row['close_4h'] > row['ema_trend_4h']) and (row['rsi_4h'] < 70)
            bear_4h = (row['close_4h'] < row['ema_trend_4h']) and (row['rsi_4h'] > 30)
            if sig_dict['signal'] == 1  and not bull_4h: continue
            if sig_dict['signal'] == -1 and not bear_4h: continue

            row['signal']        = sig_dict['signal']
            row['regime']        = regime
            row['strategy_used'] = sig_dict['strategy']
            row['target_sl']     = sig_dict['sl']
            row['target_tp1']    = sig_dict['tp1']
            row['target_tp2']    = sig_dict['tp2']
            row['exec_tf']       = exec_tf
            signals.append(row)
        return signals

    # ── Portfolio run ──────────────────────────────────────────────────
    def run_portfolio_simulation(self, watchlist):
        self.audit_metrics = self._init_audit_metrics()
        self.price_data = {}
        all_signals     = []

        for symbol in watchlist:
            tf_data  = self.load_all_tfs(symbol)
            df_macro = tf_data.get(MACRO_TF)
            if df_macro is None: continue

            for exec_tf in EXEC_TIMEFRAMES:
                df_exec = tf_data.get(exec_tf)
                if df_exec is None: continue

                signals = self.generate_signals_for_tf(df_exec, df_macro, exec_tf)
                key = f"{symbol}_{exec_tf}"
                self.price_data[key] = {
                    'prices':  df_exec[['open', 'high', 'low', 'close', 'ema_fast', 'ema_slow', 'datetime_utc']].values,
                    'idx_map': {ts: i for i, ts in enumerate(df_exec['datetime_utc'])},
                }
                for r in signals:
                    r['symbol'] = symbol
                all_signals.extend(signals)

        all_signals.sort(key=lambda x: x['datetime_utc'])
        trades = self.simulate_portfolio_trades(all_signals, watchlist)
        self.save_trades(trades)

        audit_path = None
        if self.db_path != ":memory:":
            db_file = Path(self.db_path)
            audit_path = db_file.with_name(f"{db_file.stem}_phase61_audit.json")
            with open(audit_path, "w", encoding="utf-8") as f:
                json.dump(self.audit_metrics, f, indent=2)

        return {
            "db_path": self.db_path,
            "trade_count": len(trades),
            "audit_path": str(audit_path) if audit_path else None,
            "audit_metrics": self.audit_metrics,
        }

    # ── Core simulation loop ───────────────────────────────────────────
    def simulate_portfolio_trades(self, all_signals, watchlist, forward_window=200):
        current_balance   = self.initial_balance
        open_positions    = []
        outcomes          = []
        gov_log           = []

        engine_states     = {name: initial_state() for name in ENGINE_PRIORITY}
        risk_engine       = RiskEngine(self.config_path)

        # Daily DD tracking (keyed by UTC date of CLOSE, not open)
        daily_closed_pnl  = {}      # date → realized PnL
        # Symbol cooldown: symbol → earliest datetime allowed to re-enter
        symbol_cooldown   = {}

        # Correlation telemetry (same-candle valid activation pressure)
        current_ts = None
        symbols_at_ts = set()
        pair_counts = {}

        def flush_cluster_metrics():
            if len(symbols_at_ts) < 2:
                return
            cluster_size = len(symbols_at_ts)
            self.audit_metrics["cluster_event_count"] += 1
            self.audit_metrics["cluster_event_total_size"] += cluster_size
            self.audit_metrics["same_candle_multi_symbol_activations"] += cluster_size
            for a, b in combinations(sorted(symbols_at_ts), 2):
                key = f"{a}|{b}"
                pair_counts[key] = pair_counts.get(key, 0) + 1

        for row in all_signals:
            symbol   = row['symbol']
            ts       = row['datetime_utc']
            exec_tf  = row.get('exec_tf', '4h')
            tf_min   = TF_MINUTES.get(exec_tf, 240)

            if current_ts is None:
                current_ts = ts
            elif ts != current_ts:
                flush_cluster_metrics()
                symbols_at_ts = set()
                current_ts = ts

            strategy_used = row.get('strategy_used', 'none')
            if strategy_used not in ENGINE_PRIORITY:
                continue

            sig_val      = row['signal']
            entry_price  = row['close']
            atr          = row['atr']

            # Generated signals: passed engine-level validity only.
            if not evaluate_trade_quality(row):
                continue
            if entry_price == 0 or (atr / entry_price) < ATR_MIN_RATIO:
                continue

            self.audit_metrics["total_signals_generated"] += 1
            symbols_at_ts.add(symbol)

            # ── Expire closed positions & apply their PnL to close-date bucket ──
            still_open = []
            for pos in open_positions:
                if pos['exit_time'] <= ts:
                    # Trade closed — book realized PnL to its close date
                    close_date = pos['exit_time'].date()
                    daily_closed_pnl[close_date] = daily_closed_pnl.get(close_date, 0.0) + pos['net_pnl']
                else:
                    still_open.append(pos)
            open_positions = still_open

            # ── Daily DD lock — based on closed PnL on current UTC date ──
            today = ts.date()
            today_pnl = daily_closed_pnl.get(today, 0.0)
            if today_pnl <= (current_balance * DAILY_DD_CAP):
                self.daily_dd_breaches += 1 if today_pnl == (current_balance * DAILY_DD_CAP) else 0
                self.audit_metrics["rejected_other"] += 1
                continue   # blocked for the rest of this UTC day

            # ── Global concurrency limit ──
            if len(open_positions) >= MAX_CONCURRENT:
                self.audit_metrics["rejected_concurrency_lock"] += 1
                continue

            # ── Per-symbol cooldown (1 full 4H candle after any close) ──
            cooldown_until = symbol_cooldown.get(symbol)
            if cooldown_until is not None and ts < cooldown_until:
                self.audit_metrics["rejected_symbol_cooldown_lock"] += 1
                continue

            # ── Per-symbol: only one open at a time ──
            if any(p['symbol'] == symbol for p in open_positions):
                self.audit_metrics["rejected_symbol_open_lock"] += 1
                continue

            # ── Governor ──
            allow, risk_mult = tick_state(strategy_used, engine_states[strategy_used], gov_log)
            if not allow:
                self.audit_metrics["rejected_other"] += 1
                continue
            eng_state_label = engine_states[strategy_used]["state"]
            sl           = row['target_sl']
            tp1          = row['target_tp1']
            tp2          = row['target_tp2']
            regime       = row['regime']
            volat_regime = "HIGH" if atr > (entry_price * 0.02) else "NORMAL"

            # ── Fixed Notional Cap with risk recomputation ──
            effective_risk = RISK_PER_TRADE * risk_mult
            price_risk     = abs(entry_price - sl)
            if price_risk == 0:
                self.audit_metrics["rejected_other"] += 1
                continue

            risk_amount   = current_balance * effective_risk
            position_size = risk_amount / price_risk
            notional      = position_size * entry_price

            max_notional  = current_balance * MAX_NOTIONAL_MULT
            if notional > max_notional:
                notional      = max_notional
                position_size = notional / entry_price
                risk_amount   = position_size * price_risk   # adjusted downward

            if notional == 0:
                self.audit_metrics["rejected_other"] += 1
                continue
            margin_required = notional / 10.0
            if margin_required > current_balance:
                self.audit_metrics["rejected_other"] += 1
                continue

            if not is_fee_viable(entry_price, sl, notional):
                self.audit_metrics["rejected_other"] += 1
                continue

            entry_fee_rate = 0.0005   # expansion = taker
            slippage_rate  = self._slippage_rate_from_notional(notional)

            # ── Forward-window simulation ──
            key      = f"{symbol}_{exec_tf}"
            idx_map  = self.price_data[key]['idx_map']
            if ts not in idx_map: continue
            idx_start    = idx_map[ts]
            prices       = self.price_data[key]['prices']
            end_idx      = min(idx_start + forward_window, len(prices))
            slice_window = prices[idx_start + 1:end_idx]

            self.audit_metrics["total_signals_executed"] += 1

            outcome            = 0
            duration           = 0
            gross_pnl          = 0.0
            fees_paid          = notional * entry_fee_rate
            slippage_paid      = notional * slippage_rate
            exit_timestamp     = ts
            remaining_notional = notional
            current_sl         = sl
            final_exit_price   = entry_price
            tp1_hit            = False
            using_ema_trail    = (tp2 == 0)

            for j, f_row in enumerate(slice_window):
                f_high  = f_row[1]; f_low   = f_row[2]
                f_close = f_row[3]; f_ema24 = f_row[5]; f_ts = f_row[6]

                if sig_val == 1:
                    if f_low <= current_sl:
                        outcome = 0 if not tp1_hit else 1
                        raw_pct = (current_sl - entry_price) / entry_price
                        gross_pnl += raw_pct * remaining_notional
                        fees_paid += remaining_notional * entry_fee_rate
                        slippage_paid += remaining_notional * slippage_rate
                        remaining_notional = 0
                        duration = (j + 1) * tf_min
                        exit_timestamp = f_ts; final_exit_price = current_sl; break

                    if not tp1_hit and f_high >= tp1:
                        tp1_hit = True
                        tranche = notional * 0.5
                        raw_pct = (tp1 - entry_price) / entry_price
                        gross_pnl += raw_pct * tranche
                        fees_paid += tranche * entry_fee_rate
                        slippage_paid += tranche * slippage_rate
                        remaining_notional -= tranche
                        current_sl = entry_price * (1.0 + entry_fee_rate + slippage_rate)

                    if tp1_hit and using_ema_trail:
                        if f_close < f_ema24:
                            outcome = 1
                            raw_pct = (f_close - entry_price) / entry_price
                            gross_pnl += raw_pct * remaining_notional
                            fees_paid += remaining_notional * entry_fee_rate
                            slippage_paid += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * tf_min
                            exit_timestamp = f_ts; final_exit_price = f_close; break
                    elif tp1_hit:
                        if f_high >= tp2:
                            outcome = 2
                            raw_pct = (tp2 - entry_price) / entry_price
                            gross_pnl += raw_pct * remaining_notional
                            fees_paid += remaining_notional * entry_fee_rate
                            slippage_paid += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * tf_min
                            exit_timestamp = f_ts; final_exit_price = tp2; break

                else:
                    if f_high >= current_sl:
                        outcome = 0 if not tp1_hit else 1
                        raw_pct = (entry_price - current_sl) / entry_price
                        gross_pnl += raw_pct * remaining_notional
                        fees_paid += remaining_notional * entry_fee_rate
                        slippage_paid += remaining_notional * slippage_rate
                        remaining_notional = 0
                        duration = (j + 1) * tf_min
                        exit_timestamp = f_ts; final_exit_price = current_sl; break

                    if not tp1_hit and f_low <= tp1:
                        tp1_hit = True
                        tranche = notional * 0.5
                        raw_pct = (entry_price - tp1) / entry_price
                        gross_pnl += raw_pct * tranche
                        fees_paid += tranche * entry_fee_rate
                        slippage_paid += tranche * slippage_rate
                        remaining_notional -= tranche
                        current_sl = entry_price * (1.0 - (entry_fee_rate + slippage_rate))

                    if tp1_hit and using_ema_trail:
                        if f_close > f_ema24:
                            outcome = 1
                            raw_pct = (entry_price - f_close) / entry_price
                            gross_pnl += raw_pct * remaining_notional
                            fees_paid += remaining_notional * entry_fee_rate
                            slippage_paid += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * tf_min
                            exit_timestamp = f_ts; final_exit_price = f_close; break
                    elif tp1_hit:
                        if f_low <= tp2:
                            outcome = 2
                            raw_pct = (entry_price - tp2) / entry_price
                            gross_pnl += raw_pct * remaining_notional
                            fees_paid += remaining_notional * entry_fee_rate
                            slippage_paid += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * tf_min
                            exit_timestamp = f_ts; final_exit_price = tp2; break

            # Time-expired
            if remaining_notional > 0:
                end_row = slice_window[-1] if len(slice_window) > 0 else None
                if end_row is not None:
                    f_close = end_row[3]; f_ts = end_row[6]
                else:
                    f_close = entry_price; f_ts = ts
                outcome  = 3
                raw_pct  = (f_close - entry_price) / entry_price if sig_val == 1 else (entry_price - f_close) / entry_price
                gross_pnl += raw_pct * remaining_notional
                fees_paid += remaining_notional * entry_fee_rate
                slippage_paid += remaining_notional * slippage_rate
                exit_timestamp = f_ts; final_exit_price = f_close
                duration = min((j + 1) * tf_min, forward_window * tf_min) if len(slice_window) > 0 else 0

            net_pnl          = gross_pnl - fees_paid - slippage_paid
            current_balance += net_pnl
            risk_engine.update_pnl(net_pnl)
            record_trade(engine_states[strategy_used], net_pnl)

            # Register position with close-time PnL for daily DD tracking
            open_positions.append({
                "exit_time": exit_timestamp,
                "symbol":    symbol,
                "margin":    margin_required,
                "net_pnl":   net_pnl,
            })

            # Symbol cooldown: block this symbol for 1 full 4H candle after close
            symbol_cooldown[symbol] = exit_timestamp + timedelta(minutes=240)

            # Log
            ts_iso      = ts.isoformat()
            signal_type = "BUY" if sig_val == 1 else "SELL"
            ema_dist    = (row['ema_fast'] - row['ema_slow']) / row['ema_slow']
            ema200_dist = (entry_price - row['ema_trend']) / row['ema_trend']
            saved_tp2   = tp2 if tp2 != 0 else final_exit_price

            outcomes.append((
                ts_iso, symbol, exec_tf, regime, strategy_used, eng_state_label,
                signal_type,
                float(entry_price), float(saved_tp2), float(current_sl), float(final_exit_price), int(outcome),
                int(duration), float(row['rsi']), float(row['adx']), float(atr),
                float(ema_dist), float(ema200_dist), volat_regime, ts.hour, ts.dayofweek,
                float(row.get('context_score', 0)), float(notional), float(margin_required),
                float(fees_paid), float(slippage_paid), float(net_pnl), float(current_balance)
            ))

        flush_cluster_metrics()

        total_rejected = (
            self.audit_metrics["rejected_concurrency_lock"]
            + self.audit_metrics["rejected_symbol_open_lock"]
            + self.audit_metrics["rejected_symbol_cooldown_lock"]
        )
        self.audit_metrics["rejected_signals"] = total_rejected
        generated = self.audit_metrics["total_signals_generated"]
        self.audit_metrics["duplicate_signal_rejection_rate"] = (
            total_rejected / generated if generated else 0.0
        )
        cluster_count = self.audit_metrics["cluster_event_count"]
        self.audit_metrics["cluster_event_avg_size"] = (
            self.audit_metrics["cluster_event_total_size"] / cluster_count if cluster_count else 0.0
        )
        self.audit_metrics["co_activation_pairs"] = dict(
            sorted(pair_counts.items(), key=lambda x: x[1], reverse=True)
        )
        self.audit_metrics["final_balance"] = current_balance

        # Engine summary
        if gov_log:
            print("\n[GOVERNANCE LOG]")
            seen = {}
            for line in gov_log:
                for eng in ENGINE_PRIORITY:
                    if eng in line: seen[eng] = line
            for eng, line in seen.items(): print(f"  {line}")

        print("\n[ENGINE FINAL STATES]")
        for eng in ENGINE_PRIORITY:
            es = engine_states[eng]; pf = get_pf(es)
            ra = es['recovery_attempts']; rs = es['recovery_successes']
            print(f"  {eng}: state={es['state']} | trades={es['trades']} | PF={pf:.2f} "
                  f"| active={es['ticks_active']} cd={es['ticks_cooldown']} rec={es['ticks_recovery']} "
                  f"| recovery={rs}/{ra if ra>0 else 'N/A'}")

        print(f"\n[DAILY DD] Total breach days: {self.daily_dd_breaches}")
        print("\n[PHASE 6.1 AUDIT]")
        print(f"  Generated signals: {self.audit_metrics['total_signals_generated']}")
        print(f"  Executed signals : {self.audit_metrics['total_signals_executed']}")
        print(f"  Rejected (locks) : {self.audit_metrics['rejected_signals']}")
        print(f"  Dup reject rate  : {self.audit_metrics['duplicate_signal_rejection_rate']:.2%}")
        print(f"  Cluster events   : {self.audit_metrics['cluster_event_count']}")
        print(f"  Avg cluster size : {self.audit_metrics['cluster_event_avg_size']:.2f}")
        return outcomes

    # ── Persist ────────────────────────────────────────────────────────
    def save_trades(self, trades):
        if not trades: return
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.executemany('''
            INSERT INTO historical_trades (
                timestamp, symbol, interval, regime, strategy_used, engine_state,
                signal_type, entry_price, tp_price, sl_price, exit_price, outcome,
                duration_minutes, rsi, adx, atr, ema_9_24_dist, ema_200_dist,
                volatility_regime, hour_of_day, day_of_week,
                context_score, notional_usd, margin_used,
                fees_paid, slippage_paid, net_pnl_usd, running_balance
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', trades)
        conn.commit(); conn.close()


if __name__ == "__main__":
    pass
