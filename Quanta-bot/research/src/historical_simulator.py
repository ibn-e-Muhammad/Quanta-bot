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

# Phase 4.80 Injected Modules
from research.src.trade_quality_engine import evaluate_trade_quality
from research.src.fee_filter import is_fee_viable
from research.src.exit_optimizer import get_dynamic_exit_targets

class HistoricalSimulator:
    """
    Phase 4.80: Institutional Sizing & Exit Hardening Protocol
    """
    def __init__(self, config_path, data_dir, db_path, config_override=None):
        self.config_path = config_path # Store original path for RiskEngine
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        self.config_override = config_override or {}
        
        self.data_dir = data_dir
        self.db_path = db_path
        self.ema_fast = self.config.get("market_data", {}).get("ema_fast", 9)
        self.ema_slow = self.config.get("market_data", {}).get("ema_slow", 24)
        self.ema_trend = self.config.get("market_data", {}).get("ema_trend", 200)
        self.adx_min = self.config.get("strategy_thresholds", {}).get("adx_min", 20)
        
        self.filters_config = self.config_override.get("filters", {})
        self.collapse_warning = False
        self.filter_stats = {}
        
        self.setup_db()
        
    def setup_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historical_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                interval TEXT,
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

    def calculate_indicators(self, df):
        if df is None or df.empty: return df
        c = df['close']
        h = df['high']
        l = df['low']
        
        df['ema_fast'] = c.ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = c.ewm(span=self.ema_slow, adjust=False).mean()
        df['ema_trend'] = c.ewm(span=self.ema_trend, adjust=False).mean()
        
        df['sma_20'] = c.rolling(20).mean()
        df['std_20'] = c.rolling(20).std()
        df['upper_bb'] = df['sma_20'] + (2 * df['std_20'])
        df['lower_bb'] = df['sma_20'] - (2 * df['std_20'])
        
        delta = c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = -delta.clip(upper=0).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        tr1 = h - l
        tr2 = (h - c.shift()).abs()
        tr3 = (l - c.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(14).mean()
        df['atr_sma'] = df['atr'].rolling(14).mean() # NEW ATR EXPANSION MATH
        
        plus_dm = h.diff()
        minus_dm = -l.diff()
        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
        
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).mean() / df['atr'])
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).mean() / df['atr'])
        dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        df['adx'] = dx.rolling(14).mean()
        
        return df

    def load_data(self, symbol):
        dfs = {}
        for interval in ["1h", "4h"]:
            path = os.path.join(self.data_dir, f"{symbol}_{interval}_history.csv")
            if not os.path.exists(path):
                dfs[interval] = None
                continue
            df = pd.read_csv(path)
            df['datetime_utc'] = pd.to_datetime(df['datetime_utc'])
            df = df.sort_values('datetime_utc').drop_duplicates(subset=['datetime_utc']).reset_index(drop=True)
            df = self.calculate_indicators(df)
            dfs[interval] = df
        return dfs

    def align_mtf(self, dfs):
        df_1h = dfs['1h']
        df_4h = dfs['4h']
        
        df_4h = df_4h.add_suffix('_4h').rename(columns={'datetime_utc_4h': 'datetime_utc'})
        
        merged = pd.merge_asof(df_1h, df_4h, on='datetime_utc', direction='backward')
        return merged.dropna().copy()

    def generate_signals(self, df):
        strategy_type = self.config_override.get("strategy_type", {"name": "breakout"})
        
        # Phase 4.70 Protocol: 4H Macro Filter
        bull_4h = (df['close_4h'] > df['ema_trend_4h']) & (df['rsi_4h'] < 70)
        bear_4h = (df['close_4h'] < df['ema_trend_4h']) & (df['rsi_4h'] > 30)
            
        ema_fast_prev = df['ema_fast'].shift(1)
        ema_slow_prev = df['ema_slow'].shift(1)
        
        stype = strategy_type.get("name", "breakout")
        
        # Phase 4.70 Protocol: 1H Base Execution
        if stype == "trend_follow":
            bull_1h = (df['ema_fast'] > df['ema_slow']) & (ema_fast_prev <= ema_slow_prev)
            bear_1h = (df['ema_fast'] < df['ema_slow']) & (ema_fast_prev >= ema_slow_prev)
        elif stype == "mean_reversion":
            bull_1h = (df['close'] > df['lower_bb']) & (df['close'].shift(1) <= df['lower_bb'].shift(1)) & (df['rsi'] < 30)
            bear_1h = (df['close'] < df['upper_bb']) & (df['close'].shift(1) >= df['upper_bb'].shift(1)) & (df['rsi'] > 70)
        elif stype == "breakout":
            bull_1h = (df['close'] > df['upper_bb']) & (df['close'].shift(1) <= df['upper_bb'].shift(1))
            bear_1h = (df['close'] < df['lower_bb']) & (df['close'].shift(1) >= df['lower_bb'].shift(1))
        
        df['signal'] = 0
        df.loc[bull_4h & bull_1h, 'signal'] = 1
        df.loc[bear_4h & bear_1h, 'signal'] = -1
        
        return df

    def run_portfolio_simulation(self, watchlist):
        self.price_data = {}
        all_signals = []
        
        for symbol in watchlist:
            dfs = self.load_data(symbol)
            if any(dfs[k] is None for k in dfs): continue
            
            df_merged = self.align_mtf(dfs)
            df_signals = self.generate_signals(df_merged)
            
            signal_threshold = self.config_override.get("signal_threshold", 3)
            df_scored = apply_edge_compression(df_signals, threshold=signal_threshold)
            df_filtered, filter_stats, collapse_warning = apply_filters(df_scored, self.filters_config)
            
            self.filter_stats[symbol] = filter_stats
            if collapse_warning: self.collapse_warning = True
            
            # Store price references WITH ALL REQUIRED FIELDS
            self.price_data[symbol] = {
                'prices': df_filtered[['open', 'high', 'low', 'close', 'ema_fast', 'ema_slow', 'datetime_utc']].values,
                'idx_map': {ts: i for i, ts in enumerate(df_filtered['datetime_utc'])}
            }
            
            signals_only = df_filtered[(df_filtered['signal'] != 0) & 
                                       (df_filtered['trade_allowed'] == True) & 
                                       (df_filtered['filter_allowed'] == True)].copy()
            
            records = signals_only.to_dict('records')
            for r in records: r['symbol'] = symbol
            all_signals.extend(records)
            
        all_signals.sort(key=lambda x: x['datetime_utc'])
        trades = self.simulate_portfolio_trades(all_signals, watchlist)
        self.save_trades(trades)

    def simulate_portfolio_trades(self, all_signals, watchlist, forward_window=200):
        current_balance = 10000.0
        open_positions = []
        outcomes = []
        
        risk_engine = RiskEngine(self.config_path)
        current_date = None
        
        daily_trade_counts = {sym: 0 for sym in watchlist}
        
        for row in all_signals:
            symbol = row['symbol']
            ts = row['datetime_utc']
            
            trade_date = ts.date()
            if current_date != trade_date:
                current_date = trade_date
                risk_engine.daily_pnl = 0.0
                risk_engine.circuit_broken = False
                daily_trade_counts = {sym: 0 for sym in watchlist}
            
            open_positions = [t for t in open_positions if t['exit_time'] > ts]
            
            # 2. Concurrency && Frequency Governors
            if len(open_positions) >= 5: continue
            if daily_trade_counts[symbol] >= 5: continue
            
            # 3. Macro Trade Quality Filtration
            if not evaluate_trade_quality(row): continue
            
            sig_val = row['signal']
            entry_price = row['close']
            atr = row['atr']
            volat_regime = "HIGH" if atr > (entry_price * 0.02) else "NORMAL"
            
            if sig_val == 1: sl = entry_price - (atr * 2)
            else: sl = entry_price + (atr * 2)
                
            risk_per_trade = 0.015  # Phase 4.80: Institutional 1.5% Base
            notional = risk_engine.calculate_position_size(
                balance=current_balance, 
                risk_per_trade=risk_per_trade, 
                entry_price=entry_price, 
                stop_loss_price=sl, 
                atr=atr
            )
            
            # Phase 4.80: Exact Institutional Leverage Capping Limits
            max_allowable_notional = current_balance * 5.0
            if notional > max_allowable_notional:
                notional = max_allowable_notional
            
            if notional == 0: continue
            margin_required = notional / 10.0
            if margin_required > current_balance: continue
            
            # 4. Feasibility Friction Sentry
            if not is_fee_viable(entry_price, sl, notional): continue
            
            daily_trade_counts[symbol] += 1
            
            # 5. Extract Dynamic TP1 & TP2 explicitly
            tp1, tp2 = get_dynamic_exit_targets(entry_price, sl, sig_val)
            
            idx_map = self.price_data[symbol]['idx_map']
            idx_start = idx_map[ts]
            prices = self.price_data[symbol]['prices']
            
            end_idx = min(idx_start + forward_window, len(prices))
            slice_window = prices[idx_start+1:end_idx]
            
            outcome = 0
            duration = 0
            gross_pnl = 0.0
            fees_paid = notional * 0.0005 # Native entry initialization
            slippage_paid = notional * 0.0002 # Native entry initialization
            exit_timestamp = ts
            
            remaining_notional = notional
            current_sl = sl
            final_exit_price = entry_price
            tp1_hit = False
            
            # Map Execution Simulation Tranches strictly avoiding Adaptive Exits natively
            for j, f_row in enumerate(slice_window): # 0:o, 1:h, 2:l, 3:c, 4:ema_fast, 5:ema_slow, 6:ts
                f_high = f_row[1]
                f_low = f_row[2]
                f_close = f_row[3]
                f_ts = f_row[6]
                
                if sig_val == 1:
                    # Trailing Structure logic cleanly protecting Capital Bounds
                    if f_low <= current_sl:
                        outcome = 0 if not tp1_hit else 1
                        raw_pct = (current_sl - entry_price) / entry_price
                        gross_pnl += (raw_pct * remaining_notional)
                        fees_paid += remaining_notional * 0.0005
                        slippage_paid += remaining_notional * 0.0002
                        remaining_notional = 0
                        duration = (j + 1) * 60
                        exit_timestamp = f_ts
                        final_exit_price = current_sl
                        break
                        
                    if not tp1_hit and f_high >= tp1:
                        tp1_hit = True
                        tranche = notional * 0.5
                        raw_pct = (tp1 - entry_price) / entry_price
                        gross_pnl += (raw_pct * tranche)
                        fees_paid += tranche * 0.0005
                        slippage_paid += tranche * 0.0002
                        remaining_notional -= tranche
                        current_sl = entry_price * 1.0015 # Phase 4.80: Fee-Adjusted Breakeven mathematically buffered
                        
                    if f_high >= tp2:
                        outcome = 2
                        raw_pct = (tp2 - entry_price) / entry_price
                        gross_pnl += (raw_pct * remaining_notional)
                        fees_paid += remaining_notional * 0.0005
                        slippage_paid += remaining_notional * 0.0002
                        remaining_notional = 0
                        duration = (j + 1) * 60
                        exit_timestamp = f_ts
                        final_exit_price = tp2
                        break
                
                else: # Short Logic
                    if f_high >= current_sl:
                        outcome = 0 if not tp1_hit else 1
                        raw_pct = (entry_price - current_sl) / entry_price
                        gross_pnl += (raw_pct * remaining_notional)
                        fees_paid += remaining_notional * 0.0005
                        slippage_paid += remaining_notional * 0.0002
                        remaining_notional = 0
                        duration = (j + 1) * 60
                        exit_timestamp = f_ts
                        final_exit_price = current_sl
                        break
                        
                    if not tp1_hit and f_low <= tp1:
                        tp1_hit = True
                        tranche = notional * 0.5
                        raw_pct = (entry_price - tp1) / entry_price
                        gross_pnl += (raw_pct * tranche)
                        fees_paid += tranche * 0.0005
                        slippage_paid += tranche * 0.0002
                        remaining_notional -= tranche
                        current_sl = entry_price * 0.9985 # Phase 4.80: Fee-Adjusted Breakeven mathematically buffered
                        
                    if f_low <= tp2:
                        outcome = 2
                        raw_pct = (entry_price - tp2) / entry_price
                        gross_pnl += (raw_pct * remaining_notional)
                        fees_paid += remaining_notional * 0.0005
                        slippage_paid += remaining_notional * 0.0002
                        remaining_notional = 0
                        duration = (j + 1) * 60
                        exit_timestamp = f_ts
                        final_exit_price = tp2
                        break
                        
            if remaining_notional > 0:
                end_row = slice_window[-1] if slice_window.size > 0 else (0,0,0,entry_price,0,0,ts)
                f_close = end_row[3]
                f_ts = end_row[6]
                outcome = 3 
                
                if sig_val == 1: raw_pct = (f_close - entry_price) / entry_price
                else: raw_pct = (entry_price - f_close) / entry_price
                
                gross_pnl += (raw_pct * remaining_notional)
                fees_paid += remaining_notional * 0.0005
                slippage_paid += remaining_notional * 0.0002
                exit_timestamp = f_ts
                final_exit_price = f_close
                duration = min((j + 1) * 60, forward_window * 60) if slice_window.size > 0 else 0
            
            open_positions.append({"exit_time": exit_timestamp, "margin": margin_required})
            net_pnl = gross_pnl - fees_paid - slippage_paid
            current_balance += net_pnl
            risk_engine.update_pnl(net_pnl)
            
            signal_type = "BUY" if sig_val == 1 else "SELL"
            ts_iso = ts.isoformat()
            ema_dist = (row['ema_fast'] - row['ema_slow']) / row['ema_slow']
            ema200_dist = (entry_price - row['ema_trend']) / row['ema_trend']
            
            trade = (
                ts_iso, symbol, "1h", signal_type,
                float(entry_price), float(tp2), float(current_sl), float(final_exit_price), int(outcome),
                int(duration), float(row['rsi']), float(row['adx']), float(atr), 
                float(ema_dist), float(ema200_dist), volat_regime, ts.hour, ts.dayofweek,
                float(row['context_score']), float(notional), float(margin_required), 
                float(fees_paid), float(slippage_paid), float(net_pnl), float(current_balance)
            )
            outcomes.append(trade)
            
        return outcomes

    def save_trades(self, trades):
        if not trades: return
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.executemany('''
            INSERT INTO historical_trades (
                timestamp, symbol, interval, signal_type, entry_price, tp_price, sl_price, exit_price, outcome,
                duration_minutes, rsi, adx, atr, ema_9_24_dist, ema_200_dist, volatility_regime, hour_of_day, day_of_week, 
                context_score, notional_usd, margin_used, fees_paid, slippage_paid, net_pnl_usd, running_balance
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', trades)
        conn.commit()
        conn.close()

if __name__ == "__main__":
    pass
