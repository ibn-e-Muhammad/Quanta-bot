import pandas as pd
import numpy as np
import sqlite3
import os
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research.src.edge_compression_engine import apply_edge_compression
from research.src.filters_engine import apply_filters
from execution.src.risk_engine import RiskEngine

from research.src.trade_quality_engine import evaluate_trade_quality
from research.src.fee_filter import is_fee_viable

# Phase 5 — Regime / Strategy Engines
from research.src.regime_classifier import classify_regime
from research.src.strategy_router import route_signal
from research.src.strategies import breakout_engine, mean_reversion_engine, expansion_engine

# Phase 5.2 — Engine Governance
from research.src.engine_governor import (
    initial_state, tick_state, record_trade, get_pf,
    BASE_RISK, ENGINE_PRIORITY
)


class HistoricalSimulator:
    def __init__(self, config_path, data_dir, db_path, config_override=None):
        self.config_path = config_path
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        self.config_override = config_override or {}

        self.data_dir = data_dir
        self.db_path = db_path
        self.ema_fast = self.config.get("market_data", {}).get("ema_fast", 9)
        self.ema_slow = self.config.get("market_data", {}).get("ema_slow", 24)
        self.ema_trend = self.config.get("market_data", {}).get("ema_trend", 200)

        self.filters_config = self.config_override.get("filters", {})
        self.collapse_warning = False
        self.filter_stats = {}

        self.setup_db()

    # ------------------------------------------------------------------
    # DB Setup
    # ------------------------------------------------------------------
    def setup_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historical_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                interval TEXT,
                regime TEXT,
                strategy_used TEXT,
                engine_state TEXT,
                signal_type TEXT,
                entry_price REAL,
                tp_price REAL,
                sl_price REAL,
                exit_price REAL,
                outcome INTEGER,
                duration_minutes INTEGER,
                rsi REAL,
                adx REAL,
                atr REAL,
                ema_9_24_dist REAL,
                ema_200_dist REAL,
                volatility_regime TEXT,
                hour_of_day INTEGER,
                day_of_week INTEGER,
                context_score REAL,
                notional_usd REAL,
                margin_used REAL,
                fees_paid REAL,
                slippage_paid REAL,
                net_pnl_usd REAL,
                running_balance REAL
            )
        ''')
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------
    def calculate_indicators(self, df):
        if df is None or df.empty:
            return df
        c = df['close']
        h = df['high']
        l = df['low']

        df['close_prev'] = c.shift(1)

        df['ema_fast']  = c.ewm(span=self.ema_fast,  adjust=False).mean()
        df['ema_slow']  = c.ewm(span=self.ema_slow,  adjust=False).mean()
        df['ema_trend'] = c.ewm(span=self.ema_trend, adjust=False).mean()
        df['ema_50']    = c.ewm(span=50,             adjust=False).mean()
        df['ema_50_slope'] = df['ema_50'].diff(5)

        df['sma_20']    = c.rolling(20).mean()
        df['std_20']    = c.rolling(20).std()
        df['upper_bb']  = df['sma_20'] + 2 * df['std_20']
        df['lower_bb']  = df['sma_20'] - 2 * df['std_20']
        df['upper_bb_prev'] = df['upper_bb'].shift(1)
        df['lower_bb_prev'] = df['lower_bb'].shift(1)

        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = -delta.clip(upper=0).rolling(14).mean()
        df['rsi'] = 100 - 100 / (1 + gain / loss)

        tr  = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        df['atr']     = tr.rolling(14).mean()
        df['atr_sma'] = df['atr'].rolling(14).mean()

        plus_dm  = h.diff()
        minus_dm = -l.diff()
        plus_dm  = np.where((plus_dm  > minus_dm) & (plus_dm  > 0), plus_dm,  0.0)
        minus_dm = np.where((minus_dm > plus_dm)  & (minus_dm > 0), minus_dm, 0.0)
        plus_di  = 100 * pd.Series(plus_dm,  index=df.index).rolling(14).mean() / df['atr']
        minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(14).mean() / df['atr']
        dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        df['adx'] = dx.rolling(14).mean()

        return df

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def load_data(self, symbol):
        dfs = {}
        for interval in ["1h", "4h"]:
            path = os.path.join(self.data_dir, f"{symbol}_{interval}_history.csv")
            if not os.path.exists(path):
                dfs[interval] = None
                continue
            df = pd.read_csv(path)
            df['datetime_utc'] = pd.to_datetime(df['datetime_utc'])
            df = df.sort_values('datetime_utc').drop_duplicates('datetime_utc').reset_index(drop=True)
            df = self.calculate_indicators(df)
            dfs[interval] = df
        return dfs

    def align_mtf(self, dfs):
        df_4h = dfs['4h'].add_suffix('_4h').rename(columns={'datetime_utc_4h': 'datetime_utc'})
        return pd.merge_asof(dfs['1h'], df_4h, on='datetime_utc', direction='backward').dropna().copy()

    # ------------------------------------------------------------------
    # Signal generation (regime-aware)
    # ------------------------------------------------------------------
    def generate_signals(self, df):
        records = df.to_dict('records')
        results = []

        for i, row in enumerate(records):
            row['signal'] = 0
            row['regime'] = "NONE"
            row['strategy_used'] = "none"
            row['target_sl'] = 0
            row['target_tp1'] = 0
            row['target_tp2'] = 0

            if i == 0:
                results.append(row)
                continue

            regime        = classify_regime(row)
            strategy_name = route_signal(regime)

            bull_4h = (row['close_4h'] > row['ema_trend_4h']) and (row['rsi_4h'] < 70)
            bear_4h = (row['close_4h'] < row['ema_trend_4h']) and (row['rsi_4h'] > 30)

            sig_dict = {"signal": 0, "sl": 0, "tp1": 0, "tp2": 0, "strategy": "none"}

            if strategy_name == "breakout_engine":
                sig_dict = breakout_engine.generate_signal(row)
            elif strategy_name == "mean_reversion_engine":
                sig_dict = mean_reversion_engine.generate_signal(row)
            elif strategy_name == "expansion_engine":
                sig_dict = expansion_engine.generate_signal(row)

            final_signal = sig_dict['signal']
            if final_signal == 1  and not bull_4h: final_signal = 0
            if final_signal == -1 and not bear_4h: final_signal = 0

            row['signal']       = final_signal
            row['regime']       = regime
            row['strategy_used']= sig_dict['strategy']
            row['target_sl']    = sig_dict['sl']
            row['target_tp1']   = sig_dict['tp1']
            row['target_tp2']   = sig_dict['tp2']
            results.append(row)

        return pd.DataFrame(results)

    # ------------------------------------------------------------------
    # Portfolio simulation orchestration
    # ------------------------------------------------------------------
    def run_portfolio_simulation(self, watchlist):
        self.price_data = {}
        all_signals = []

        for symbol in watchlist:
            dfs = self.load_data(symbol)
            if any(dfs[k] is None for k in dfs):
                continue

            df_merged  = self.align_mtf(dfs)
            df_signals = self.generate_signals(df_merged)

            threshold  = self.config_override.get("signal_threshold", 3)
            df_scored  = apply_edge_compression(df_signals, threshold=threshold)
            df_filtered, filter_stats, collapse_warning = apply_filters(df_scored, self.filters_config)

            self.filter_stats[symbol] = filter_stats
            if collapse_warning:
                self.collapse_warning = True

            self.price_data[symbol] = {
                'prices':  df_filtered[['open', 'high', 'low', 'close', 'ema_fast', 'ema_slow', 'datetime_utc']].values,
                'idx_map': {ts: i for i, ts in enumerate(df_filtered['datetime_utc'])},
            }

            signals_only = df_filtered[
                (df_filtered['signal'] != 0) &
                (df_filtered['trade_allowed'] == True) &
                (df_filtered['filter_allowed'] == True)
            ].copy()

            records = signals_only.to_dict('records')
            for r in records:
                r['symbol'] = symbol
            all_signals.extend(records)

        all_signals.sort(key=lambda x: x['datetime_utc'])
        trades = self.simulate_portfolio_trades(all_signals, watchlist)
        self.save_trades(trades)

    # ------------------------------------------------------------------
    # Core chronological execution loop  (Phase 5.2 governor integrated)
    # ------------------------------------------------------------------
    def simulate_portfolio_trades(self, all_signals, watchlist, forward_window=200):
        current_balance  = 10000.0
        open_positions   = []
        outcomes         = []
        gov_log          = []          # governance log lines

        # Per-engine state records
        engine_states = {name: initial_state() for name in ENGINE_PRIORITY}

        risk_engine   = RiskEngine(self.config_path)
        current_date  = None
        daily_counts  = {sym: 0 for sym in watchlist}

        for row in all_signals:
            symbol = row['symbol']
            ts     = row['datetime_utc']

            trade_date = ts.date()
            if current_date != trade_date:
                current_date = trade_date
                risk_engine.daily_pnl   = 0.0
                risk_engine.circuit_broken = False
                daily_counts = {sym: 0 for sym in watchlist}

            open_positions = [t for t in open_positions if t['exit_time'] > ts]

            if len(open_positions) >= 5:
                continue
            if daily_counts[symbol] >= 5:
                continue
            if not evaluate_trade_quality(row):
                continue

            strategy_used = row['strategy_used']
            if strategy_used not in ENGINE_PRIORITY:
                continue

            # ---- Governor tick ----------------------------------------
            allow, risk_mult = tick_state(strategy_used, engine_states[strategy_used], gov_log)
            if not allow:
                continue

            eng_state_label = engine_states[strategy_used]["state"]

            # ---- Trade math -------------------------------------------
            sig_val      = row['signal']
            entry_price  = row['close']
            atr          = row['atr']
            sl           = row['target_sl']
            tp1          = row['target_tp1']
            tp2          = row['target_tp2']
            regime       = row['regime']
            volat_regime = "HIGH" if atr > (entry_price * 0.02) else "NORMAL"

            # Risk amount is dynamic based on governor state
            effective_risk = 0.015 * risk_mult  # Phase 5.4: 1.5% base risk
            notional = risk_engine.calculate_position_size(
                current_balance, effective_risk, entry_price, sl, atr
            )

            # Hard leverage ceiling — 5x
            max_notional = current_balance * 5.0
            if notional > max_notional:
                notional = max_notional

            if notional == 0:
                continue
            margin_required = notional / 10.0
            if margin_required > current_balance:
                continue

            if not is_fee_viable(entry_price, sl, notional):
                continue

            daily_counts[symbol] += 1

            # Maker vs Taker fee routing
            entry_fee_rate = 0.0002 if strategy_used == "mean_reversion_engine" else 0.0005
            slippage_rate  = 0.0002

            # ---- Price-window forward simulation ----------------------
            idx_map   = self.price_data[symbol]['idx_map']
            idx_start = idx_map[ts]
            prices    = self.price_data[symbol]['prices']
            end_idx   = min(idx_start + forward_window, len(prices))
            slice_window = prices[idx_start + 1:end_idx]

            outcome           = 0
            duration          = 0
            gross_pnl         = 0.0
            fees_paid         = notional * entry_fee_rate
            slippage_paid     = notional * slippage_rate
            exit_timestamp    = ts
            remaining_notional = notional
            current_sl        = sl
            final_exit_price  = entry_price
            tp1_hit           = False

            for j, f_row in enumerate(slice_window):
                f_high  = f_row[1]
                f_low   = f_row[2]
                f_close = f_row[3]
                f_ema24 = f_row[5]   # ema_slow = EMA 24
                f_ts    = f_row[6]

                # Determine if this is a fat-tail runner (tp2==0 sentinel)
                using_ema_trail = (tp2 == 0)

                if sig_val == 1:
                    if f_low <= current_sl:
                        outcome = 0 if not tp1_hit else 1
                        raw_pct = (current_sl - entry_price) / entry_price
                        gross_pnl      += raw_pct * remaining_notional
                        fees_paid      += remaining_notional * entry_fee_rate
                        slippage_paid  += remaining_notional * slippage_rate
                        remaining_notional = 0
                        duration = (j + 1) * 60
                        exit_timestamp   = f_ts
                        final_exit_price = current_sl
                        break

                    if not tp1_hit and f_high >= tp1:
                        tp1_hit = True
                        tranche  = notional * 0.5
                        raw_pct  = (tp1 - entry_price) / entry_price
                        gross_pnl      += raw_pct * tranche
                        fees_paid      += tranche * entry_fee_rate
                        slippage_paid  += tranche * slippage_rate
                        remaining_notional -= tranche
                        # Fee-adjusted breakeven for runner tranche
                        current_sl = entry_price * (1.0 + entry_fee_rate + slippage_rate)

                    # After TP1: fat-tail runner uses EMA-24 trailing close stop
                    if tp1_hit and using_ema_trail:
                        if f_close < f_ema24:  # Close below EMA 24 → exit runner
                            outcome = 1
                            raw_pct = (f_close - entry_price) / entry_price
                            gross_pnl      += raw_pct * remaining_notional
                            fees_paid      += remaining_notional * entry_fee_rate
                            slippage_paid  += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * 60
                            exit_timestamp   = f_ts
                            final_exit_price = f_close
                            break
                    elif tp1_hit and not using_ema_trail:
                        if f_high >= tp2:
                            outcome = 2
                            raw_pct = (tp2 - entry_price) / entry_price
                            gross_pnl      += raw_pct * remaining_notional
                            fees_paid      += remaining_notional * entry_fee_rate
                            slippage_paid  += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * 60
                            exit_timestamp   = f_ts
                            final_exit_price = tp2
                            break

                else:  # SHORT
                    if f_high >= current_sl:
                        outcome = 0 if not tp1_hit else 1
                        raw_pct = (entry_price - current_sl) / entry_price
                        gross_pnl      += raw_pct * remaining_notional
                        fees_paid      += remaining_notional * entry_fee_rate
                        slippage_paid  += remaining_notional * slippage_rate
                        remaining_notional = 0
                        duration = (j + 1) * 60
                        exit_timestamp   = f_ts
                        final_exit_price = current_sl
                        break

                    if not tp1_hit and f_low <= tp1:
                        tp1_hit = True
                        tranche  = notional * 0.5
                        raw_pct  = (entry_price - tp1) / entry_price
                        gross_pnl      += raw_pct * tranche
                        fees_paid      += tranche * entry_fee_rate
                        slippage_paid  += tranche * slippage_rate
                        remaining_notional -= tranche
                        current_sl = entry_price * (1.0 - (entry_fee_rate + slippage_rate))

                    # After TP1: fat-tail runner uses EMA-24 trailing close stop
                    if tp1_hit and using_ema_trail:
                        if f_close > f_ema24:  # Close above EMA 24 → exit runner
                            outcome = 1
                            raw_pct = (entry_price - f_close) / entry_price
                            gross_pnl      += raw_pct * remaining_notional
                            fees_paid      += remaining_notional * entry_fee_rate
                            slippage_paid  += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * 60
                            exit_timestamp   = f_ts
                            final_exit_price = f_close
                            break
                    elif tp1_hit and not using_ema_trail:
                        if f_low <= tp2:
                            outcome = 2
                            raw_pct = (entry_price - tp2) / entry_price
                            gross_pnl      += raw_pct * remaining_notional
                            fees_paid      += remaining_notional * entry_fee_rate
                            slippage_paid  += remaining_notional * slippage_rate
                            remaining_notional = 0
                            duration = (j + 1) * 60
                            exit_timestamp   = f_ts
                            final_exit_price = tp2
                            break

            # Time-expired close
            if remaining_notional > 0:
                end_row  = slice_window[-1] if slice_window.size > 0 else (0, 0, 0, entry_price, 0, 0, ts)
                f_close  = end_row[3]
                f_ts     = end_row[6]
                outcome  = 3
                raw_pct  = (f_close - entry_price) / entry_price if sig_val == 1 else (entry_price - f_close) / entry_price
                gross_pnl      += raw_pct * remaining_notional
                fees_paid      += remaining_notional * entry_fee_rate
                slippage_paid  += remaining_notional * slippage_rate
                exit_timestamp   = f_ts
                final_exit_price = f_close
                duration = min((j + 1) * 60, forward_window * 60) if slice_window.size > 0 else 0

            open_positions.append({"exit_time": exit_timestamp, "margin": margin_required})
            net_pnl         = gross_pnl - fees_paid - slippage_paid
            current_balance += net_pnl
            risk_engine.update_pnl(net_pnl)

            # Feed outcome into governor for future PF-based decisions
            record_trade(engine_states[strategy_used], net_pnl)

            ts_iso      = ts.isoformat()
            signal_type = "BUY" if sig_val == 1 else "SELL"
            ema_dist    = (row['ema_fast'] - row['ema_slow']) / row['ema_slow']
            ema200_dist = (entry_price - row['ema_trend']) / row['ema_trend']

            trade = (
                ts_iso, symbol, "1h", regime, strategy_used, eng_state_label,
                signal_type,
                float(entry_price), float(tp2), float(current_sl), float(final_exit_price), int(outcome),
                int(duration), float(row['rsi']), float(row['adx']), float(atr),
                float(ema_dist), float(ema200_dist), volat_regime, ts.hour, ts.dayofweek,
                float(row.get('context_score', 0)), float(notional), float(margin_required),
                float(fees_paid), float(slippage_paid), float(net_pnl), float(current_balance)
            )
            outcomes.append(trade)

        # Print governance log summary
        if gov_log:
            print("\n[GOVERNANCE LOG SUMMARY]")
            last_events = {}
            for line in gov_log:
                for eng in ENGINE_PRIORITY:
                    if eng in line:
                        last_events[eng] = line
            for eng, line in last_events.items():
                print(f"  {line}")

        # Final engine state report
        print("\n[ENGINE FINAL STATES]")
        for eng in ENGINE_PRIORITY:
            es  = engine_states[eng]
            pf  = get_pf(es)
            ra  = es['recovery_attempts']
            rs  = es['recovery_successes']
            rr  = f"{rs}/{ra}" if ra > 0 else "N/A"
            print(f"  {eng}: state={es['state']} | trades={es['trades']} | PF={pf:.2f} "
                  f"| active={es['ticks_active']} cd={es['ticks_cooldown']} rec={es['ticks_recovery']} "
                  f"| recovery={rr}")

        return outcomes

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    def save_trades(self, trades):
        if not trades:
            return
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
        conn.commit()
        conn.close()


if __name__ == "__main__":
    pass
