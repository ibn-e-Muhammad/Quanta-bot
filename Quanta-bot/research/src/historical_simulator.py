import pandas as pd
import numpy as np
import sqlite3
import os
import json
import sys
from pathlib import Path
from datetime import timedelta

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.edge_compression_engine import apply_edge_compression
from research.src.filters_engine import apply_filters
from execution.src.risk_engine import RiskEngine

from research.src.trade_quality_engine import evaluate_trade_quality
from research.src.fee_filter import is_fee_viable

# Phase 5 — Regime/Strategy modules
from research.src.regime_classifier import classify_regime
from research.src.strategy_router import route_signal
from research.src.strategies import breakout_engine, mean_reversion_engine, expansion_engine

# Phase 5.2 — Engine Governance
from research.src.engine_governor import (
    initial_state, tick_state, record_trade, get_pf,
    ENGINE_PRIORITY
)

# Phase 5.5
EXEC_TIMEFRAMES  = ["15m", "1h", "4h"]
MACRO_TF         = "4h"
MTF_BASE_MINUTES = 15          # smallest TF candle duration in minutes
MIN_TIME_GAP     = 3           # candles of base TF → 45 minutes
MIN_GAP_MINUTES  = MIN_TIME_GAP * MTF_BASE_MINUTES  # 45 min

# TF candle sizes for duration math
TF_MINUTES = {"15m": 15, "1h": 60, "4h": 240}


class HistoricalSimulator:
    def __init__(self, config_path, data_dir, db_path, config_override=None):
        self.config_path = config_path
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        self.config_override = config_override or {}

        self.data_dir   = data_dir
        self.db_path    = db_path
        self.ema_fast   = self.config.get("market_data", {}).get("ema_fast",  9)
        self.ema_slow   = self.config.get("market_data", {}).get("ema_slow",  24)
        self.ema_trend  = self.config.get("market_data", {}).get("ema_trend", 200)

        self.filters_config = self.config_override.get("filters", {})
        self.collapse_warning = False
        self.filter_stats     = {}
        self.mtf_blocked      = 0
        self.mtf_executed     = 0

        self.setup_db()

    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    def load_tf(self, symbol, tf):
        path = os.path.join(self.data_dir, f"{symbol}_{tf}_history.csv")
        if not os.path.exists(path): return None
        df = pd.read_csv(path)
        df['datetime_utc'] = pd.to_datetime(df['datetime_utc'])
        df = df.sort_values('datetime_utc').drop_duplicates('datetime_utc').reset_index(drop=True)
        return self.calculate_indicators(df)

    def load_all_tfs(self, symbol):
        """Load every TF needed: all exec TFs + macro TF."""
        needed = set(EXEC_TIMEFRAMES) | {MACRO_TF}
        return {tf: self.load_tf(symbol, tf) for tf in needed}

    # ------------------------------------------------------------------
    def generate_signals_for_tf(self, df_exec, df_macro, exec_tf):
        """
        Merge exec TF with 4h macro, run expansion-only signal generation.
        Returns list of signal dicts.
        """
        if df_exec is None or df_macro is None: return []

        df_m = df_macro.add_suffix('_4h').rename(columns={'datetime_utc_4h': 'datetime_utc'})
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

            # 4H macro directional filter
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

    # ------------------------------------------------------------------
    def run_portfolio_simulation(self, watchlist):
        self.price_data  = {}   # keyed by (symbol, tf)
        all_signals      = []

        for symbol in watchlist:
            tf_data = self.load_all_tfs(symbol)
            df_macro = tf_data.get(MACRO_TF)
            if df_macro is None: continue

            for exec_tf in EXEC_TIMEFRAMES:
                df_exec = tf_data.get(exec_tf)
                if df_exec is None: continue

                signals = self.generate_signals_for_tf(df_exec, df_macro, exec_tf)

                # Store price window reference for this (symbol, exec_tf) pair
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

    # ------------------------------------------------------------------
    def simulate_portfolio_trades(self, all_signals, watchlist, forward_window=200):
        current_balance = 10000.0
        open_positions  = []    # {exit_time, symbol, margin}
        outcomes        = []
        gov_log         = []

        engine_states = {name: initial_state() for name in ENGINE_PRIORITY}
        risk_engine   = RiskEngine(self.config_path)
        current_date  = None
        daily_counts  = {sym: 0 for sym in watchlist}

        # Correlation guard: track last open timestamp per symbol
        last_open_ts  = {}     # symbol → datetime

        for row in all_signals:
            symbol   = row['symbol']
            ts       = row['datetime_utc']
            exec_tf  = row.get('exec_tf', '1h')

            trade_date = ts.date()
            if current_date != trade_date:
                current_date             = trade_date
                risk_engine.daily_pnl   = 0.0
                risk_engine.circuit_broken = False
                daily_counts             = {sym: 0 for sym in watchlist}

            open_positions = [t for t in open_positions if t['exit_time'] > ts]

            # --- Global concurrency & frequency guards ---
            if len(open_positions) >= 5: continue
            if daily_counts.get(symbol, 0) >= 5: continue
            if not evaluate_trade_quality(row): continue

            strategy_used = row.get('strategy_used', 'none')
            if strategy_used not in ENGINE_PRIORITY: continue

            # --- Phase 5.5 Correlation Guard ---
            last_ts = last_open_ts.get(symbol)
            if last_ts is not None:
                gap_minutes = (ts - last_ts).total_seconds() / 60
                if gap_minutes < MIN_GAP_MINUTES:
                    print(f"[MTF BLOCK] {symbol} {exec_tf} | Reason: recent {gap_minutes:.0f}m ago")
                    self.mtf_blocked += 1
                    continue

            # Also block if same symbol already has a live open trade
            sym_open = any(t['symbol'] == symbol for t in open_positions)
            if sym_open:
                print(f"[MTF BLOCK] {symbol} {exec_tf} | Reason: Active trade exists")
                self.mtf_blocked += 1
                continue

            # --- Governor tick ---
            allow, risk_mult = tick_state(strategy_used, engine_states[strategy_used], gov_log)
            if not allow: continue
            eng_state_label = engine_states[strategy_used]["state"]

            # --- Trade setup ---
            sig_val      = row['signal']
            entry_price  = row['close']
            atr          = row['atr']
            sl           = row['target_sl']
            tp1          = row['target_tp1']
            tp2          = row['target_tp2']
            regime       = row['regime']
            volat_regime = "HIGH" if atr > (entry_price * 0.02) else "NORMAL"

            # --- Phase 5.5 FIXED Notional Cap (re-compute actual risk) ---
            effective_risk = 0.005 * risk_mult   # 0.5% base risk
            price_risk     = abs(entry_price - sl)
            if price_risk == 0: continue

            risk_amount    = current_balance * effective_risk
            position_size  = risk_amount / price_risk
            notional       = position_size * entry_price

            max_notional   = current_balance * 5.0
            if notional > max_notional:
                notional       = max_notional
                position_size  = notional / entry_price
                # Risk adjusts DOWNWARD — never upward
                risk_amount    = position_size * price_risk

            if notional == 0: continue
            margin_required = notional / 10.0
            if margin_required > current_balance: continue

            if not is_fee_viable(entry_price, sl, notional): continue

            daily_counts[symbol] = daily_counts.get(symbol, 0) + 1
            last_open_ts[symbol] = ts

            tf_candle_min  = TF_MINUTES.get(exec_tf, 60)
            entry_fee_rate = 0.0005   # expansion = taker
            slippage_rate  = 0.0002

            print(f"[MTF EXECUTE] {symbol} | TF: {exec_tf} | Risk: {effective_risk*100:.2f}% | Notional: ${notional:.0f}")
            self.mtf_executed += 1

            # --- Forward-window price simulation ---
            key      = f"{symbol}_{exec_tf}"
            idx_map  = self.price_data[key]['idx_map']
            if ts not in idx_map: continue
            idx_start    = idx_map[ts]
            prices       = self.price_data[key]['prices']
            end_idx      = min(idx_start + forward_window, len(prices))
            slice_window = prices[idx_start + 1:end_idx]

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
                f_high  = f_row[1]
                f_low   = f_row[2]
                f_close = f_row[3]
                f_ema24 = f_row[5]
                f_ts    = f_row[6]

                if sig_val == 1:
                    if f_low <= current_sl:
                        outcome = 0 if not tp1_hit else 1
                        raw_pct = (current_sl - entry_price) / entry_price
                        gross_pnl     += raw_pct * remaining_notional
                        fees_paid     += remaining_notional * entry_fee_rate
                        slippage_paid += remaining_notional * slippage_rate
                        remaining_notional = 0
                        duration       = (j + 1) * tf_candle_min
                        exit_timestamp = f_ts; final_exit_price = current_sl; break

                    if not tp1_hit and f_high >= tp1:
                        tp1_hit  = True
                        tranche  = notional * 0.5
                        raw_pct  = (tp1 - entry_price) / entry_price
                        gross_pnl     += raw_pct * tranche
                        fees_paid     += tranche * entry_fee_rate
                        slippage_paid += tranche * slippage_rate
                        remaining_notional -= tranche
                        current_sl = entry_price * (1.0 + entry_fee_rate + slippage_rate)

                    if tp1_hit and using_ema_trail:
                        if f_close < f_ema24:
                            outcome = 1
                            raw_pct = (f_close - entry_price) / entry_price
                            gross_pnl     += raw_pct * remaining_notional
                            fees_paid     += remaining_notional * entry_fee_rate
                            slippage_paid += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * tf_candle_min
                            exit_timestamp = f_ts; final_exit_price = f_close; break
                    elif tp1_hit and not using_ema_trail:
                        if f_high >= tp2:
                            outcome = 2
                            raw_pct = (tp2 - entry_price) / entry_price
                            gross_pnl     += raw_pct * remaining_notional
                            fees_paid     += remaining_notional * entry_fee_rate
                            slippage_paid += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * tf_candle_min
                            exit_timestamp = f_ts; final_exit_price = tp2; break

                else:  # SHORT
                    if f_high >= current_sl:
                        outcome = 0 if not tp1_hit else 1
                        raw_pct = (entry_price - current_sl) / entry_price
                        gross_pnl     += raw_pct * remaining_notional
                        fees_paid     += remaining_notional * entry_fee_rate
                        slippage_paid += remaining_notional * slippage_rate
                        remaining_notional = 0
                        duration = (j + 1) * tf_candle_min
                        exit_timestamp = f_ts; final_exit_price = current_sl; break

                    if not tp1_hit and f_low <= tp1:
                        tp1_hit  = True
                        tranche  = notional * 0.5
                        raw_pct  = (entry_price - tp1) / entry_price
                        gross_pnl     += raw_pct * tranche
                        fees_paid     += tranche * entry_fee_rate
                        slippage_paid += tranche * slippage_rate
                        remaining_notional -= tranche
                        current_sl = entry_price * (1.0 - (entry_fee_rate + slippage_rate))

                    if tp1_hit and using_ema_trail:
                        if f_close > f_ema24:
                            outcome = 1
                            raw_pct = (entry_price - f_close) / entry_price
                            gross_pnl     += raw_pct * remaining_notional
                            fees_paid     += remaining_notional * entry_fee_rate
                            slippage_paid += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * tf_candle_min
                            exit_timestamp = f_ts; final_exit_price = f_close; break
                    elif tp1_hit and not using_ema_trail:
                        if f_low <= tp2:
                            outcome = 2
                            raw_pct = (entry_price - tp2) / entry_price
                            gross_pnl     += raw_pct * remaining_notional
                            fees_paid     += remaining_notional * entry_fee_rate
                            slippage_paid += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * tf_candle_min
                            exit_timestamp = f_ts; final_exit_price = tp2; break

            # Time-expired close
            if remaining_notional > 0:
                end_row  = slice_window[-1] if len(slice_window) > 0 else None
                if end_row is not None:
                    f_close = end_row[3]; f_ts = end_row[6]
                else:
                    f_close = entry_price; f_ts = ts
                outcome  = 3
                raw_pct  = (f_close - entry_price) / entry_price if sig_val == 1 else (entry_price - f_close) / entry_price
                gross_pnl     += raw_pct * remaining_notional
                fees_paid     += remaining_notional * entry_fee_rate
                slippage_paid += remaining_notional * slippage_rate
                exit_timestamp = f_ts; final_exit_price = f_close
                duration = min((j + 1) * tf_candle_min, forward_window * tf_candle_min) if len(slice_window) > 0 else 0

            open_positions.append({"exit_time": exit_timestamp, "symbol": symbol, "margin": margin_required})
            net_pnl          = gross_pnl - fees_paid - slippage_paid
            current_balance += net_pnl
            risk_engine.update_pnl(net_pnl)
            record_trade(engine_states[strategy_used], net_pnl)

            ts_iso      = ts.isoformat()
            signal_type = "BUY" if sig_val == 1 else "SELL"
            ema_dist    = (row['ema_fast'] - row['ema_slow']) / row['ema_slow']
            ema200_dist = (entry_price - row['ema_trend']) / row['ema_trend']
            saved_tp2   = tp2 if tp2 != 0 else final_exit_price

            trade = (
                ts_iso, symbol, exec_tf, regime, strategy_used, eng_state_label,
                signal_type,
                float(entry_price), float(saved_tp2), float(current_sl), float(final_exit_price), int(outcome),
                int(duration), float(row['rsi']), float(row['adx']), float(atr),
                float(ema_dist), float(ema200_dist), volat_regime, ts.hour, ts.dayofweek,
                float(row.get('context_score', 0)), float(notional), float(margin_required),
                float(fees_paid), float(slippage_paid), float(net_pnl), float(current_balance)
            )
            outcomes.append(trade)

        # Summary
        print(f"\n[MTF SUMMARY] Executed: {self.mtf_executed} | Blocked: {self.mtf_blocked} "
              f"| Block rate: {self.mtf_blocked / max(self.mtf_executed + self.mtf_blocked, 1) * 100:.1f}%")

        if gov_log:
            print("\n[GOVERNANCE LOG SUMMARY]")
            last_events = {}
            for line in gov_log:
                for eng in ENGINE_PRIORITY:
                    if eng in line: last_events[eng] = line
            for eng, line in last_events.items(): print(f"  {line}")

        print("\n[ENGINE FINAL STATES]")
        for eng in ENGINE_PRIORITY:
            es = engine_states[eng]
            pf = get_pf(es)
            ra = es['recovery_attempts']
            rs = es['recovery_successes']
            rr = f"{rs}/{ra}" if ra > 0 else "N/A"
            print(f"  {eng}: state={es['state']} | trades={es['trades']} | PF={pf:.2f} "
                  f"| active={es['ticks_active']} cd={es['ticks_cooldown']} rec={es['ticks_recovery']} "
                  f"| recovery={rr}")
        return outcomes

    # ------------------------------------------------------------------
    def save_trades(self, trades):
        if not trades: return
        conn   = sqlite3.connect(self.db_path)
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
