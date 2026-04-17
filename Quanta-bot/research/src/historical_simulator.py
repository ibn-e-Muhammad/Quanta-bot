import pandas as pd
import numpy as np
import sqlite3
import os
import time
import json
from pathlib import Path

# Core Modules
from edge_compression_engine import apply_edge_compression
from filters_engine import apply_filters
from position_sizing import calculate_dynamic_position_size
from asset_allocator import AdaptiveAssetAllocator

class HistoricalSimulator:
    """
    Hypothesis Engine heavily incorporating V3 config dynamic overrides
    and V4 execution transparency (Sizing, Risk Overlays).
    """
    def __init__(self, config_path, data_dir, db_path, config_override=None):
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        self.config_override = config_override or {}
        
        self.data_dir = data_dir
        self.db_path = db_path
        self.ema_fast = self.config.get("market_data", {}).get("ema_fast", 9)
        self.ema_slow = self.config.get("market_data", {}).get("ema_slow", 24)
        self.ema_trend = self.config.get("market_data", {}).get("ema_trend", 200)
        self.adx_min = self.config.get("strategy_thresholds", {}).get("adx_min", 20)
        self.rr_ratio = self.config.get("strategy_thresholds", {}).get("min_rr_ratio", 3.0)
        
        self.use_dynamic_sizing = self.config_override.get("use_dynamic_sizing", False)
        self.use_adaptive_allocator = self.config_override.get("use_adaptive_allocator", False)
        self.filters_config = self.config_override.get("filters", {})
        self.global_portfolio_map = self.config_override.get("global_portfolio_map", {})
        
        self.filter_stats = {}
        self.collapse_warning = False
        
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
                position_size REAL DEFAULT 1.0,
                adx_factor REAL DEFAULT 1.0,
                vol_factor REAL DEFAULT 1.0,
                portfolio_factor REAL DEFAULT 1.0
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
        for interval in ["15m", "1h", "4h"]:
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
        df_15m = dfs['15m']
        df_1h = dfs['1h']
        df_4h = dfs['4h']
        
        df_1h = df_1h.add_suffix('_1h').rename(columns={'datetime_utc_1h': 'datetime_utc'})
        df_4h = df_4h.add_suffix('_4h').rename(columns={'datetime_utc_4h': 'datetime_utc'})
        
        merged = pd.merge_asof(
            df_15m, df_1h,
            on='datetime_utc',
            direction='backward'
        )
        merged = pd.merge_asof(
            merged, df_4h,
            on='datetime_utc',
            direction='backward'
        )
        return merged.dropna().copy()

    def generate_signals(self, df):
        mtf_mode = self.config_override.get("mtf_mode", {"name": "fast", "use_4h": False, "use_1h": True})
        strategy_type = self.config_override.get("strategy_type", {"name": "breakout"})
        use_4h = mtf_mode.get("use_4h", False)
        use_1h = mtf_mode.get("use_1h", True)
        
        if use_4h == True:
            bull_4h = (df['close_4h'] > df['ema_trend_4h']) & (df['rsi_4h'] < 70)
            bear_4h = (df['close_4h'] < df['ema_trend_4h']) & (df['rsi_4h'] > 30)
        else:
            bull_4h = pd.Series(True, index=df.index)
            bear_4h = pd.Series(True, index=df.index)
            
        if use_1h == True:
            bull_1h = (df['ema_fast_1h'] > df['ema_slow_1h']) & (df['adx_1h'] >= self.adx_min)
            bear_1h = (df['ema_fast_1h'] < df['ema_slow_1h']) & (df['adx_1h'] >= self.adx_min)
        else:
            bull_1h = pd.Series(True, index=df.index)
            bear_1h = pd.Series(True, index=df.index)
            
        ema_fast_prev = df['ema_fast'].shift(1)
        ema_slow_prev = df['ema_slow'].shift(1)
        
        stype = strategy_type.get("name", "breakout")
        
        if stype == "trend_follow":
            bull_15m = (df['ema_fast'] > df['ema_slow']) & (ema_fast_prev <= ema_slow_prev)
            bear_15m = (df['ema_fast'] < df['ema_slow']) & (ema_fast_prev >= ema_slow_prev)
        elif stype == "mean_reversion":
            bull_15m = (df['close'] > df['lower_bb']) & (df['close'].shift(1) <= df['lower_bb'].shift(1)) & (df['rsi'] < 30)
            bear_15m = (df['close'] < df['upper_bb']) & (df['close'].shift(1) >= df['upper_bb'].shift(1)) & (df['rsi'] > 70)
        elif stype == "breakout":
            bull_15m = (df['close'] > df['upper_bb']) & (df['close'].shift(1) <= df['upper_bb'].shift(1))
            bear_15m = (df['close'] < df['lower_bb']) & (df['close'].shift(1) >= df['lower_bb'].shift(1))
        
        df['signal'] = 0
        df.loc[bull_4h & bull_1h & bull_15m, 'signal'] = 1
        df.loc[bear_4h & bear_1h & bear_15m, 'signal'] = -1
        
        return df

    def simulate_trades(self, df, target_symbol, forward_window=200):
        signals = df[(df['signal'] != 0) & (df['trade_allowed'] == True) & (df['filter_allowed'] == True)].copy()
        outcomes = []
        
        prices = df[['high', 'low', 'close', 'ema_fast']].values
        idx_map = {ts: i for i, ts in enumerate(df['datetime_utc'])}
        
        exit_profile = self.config_override.get("exit_profile", {"name": "fixed_2R", "tp_rr": 2.0, "partial": False})
        tp_name = exit_profile.get("name", "fixed_2R")
        ratio = exit_profile.get("tp_rr", 2.0)
        
        allocator = AdaptiveAssetAllocator(window_size=25)
        
        for row in signals.itertuples():
            sig_val = row.signal
            idx_start = idx_map[row.datetime_utc]
            
            entry_price = row.close
            atr = row.atr
            volat_regime = "HIGH" if atr > (entry_price * 0.02) else "NORMAL"
            
            ts_iso = row.datetime_utc.isoformat()
            port_factor = self.global_portfolio_map.get(ts_iso, 1.0)
            
            if self.use_dynamic_sizing:
                tier_factor = allocator.get_current_tier_multiplier() if self.use_adaptive_allocator else 1.0
                pos_size, adx_f, vol_f = calculate_dynamic_position_size(row.adx, volat_regime, port_factor, tier_factor)
            else:
                pos_size, adx_f, vol_f = 1.0, 1.0, 1.0
            
            if sig_val == 1:
                sl = entry_price - (atr * 2)
                tp = entry_price + (atr * 2 * ratio) if "fixed" in tp_name else 0
            else:
                sl = entry_price + (atr * 2)
                tp = entry_price - (atr * 2 * ratio) if "fixed" in tp_name else 0
                
            if "partial" in tp_name:
                ratio_arr = exit_profile.get("tp_rr", [1.5, 3.0])
                if sig_val == 1:
                    tp1 = entry_price + (atr * 2 * ratio_arr[0])
                    tp2 = entry_price + (atr * 2 * ratio_arr[1])
                else:
                    tp1 = entry_price - (atr * 2 * ratio_arr[0])
                    tp2 = entry_price - (atr * 2 * ratio_arr[1])
            
            outcome = 0
            exit_price = sl
            duration = 0
            end_idx = min(idx_start + forward_window, len(prices))
            slice_window = prices[idx_start+1:end_idx]
            tp1_hit = False
            
            for j, f_row in enumerate(slice_window):
                f_high = f_row[0]
                f_low = f_row[1]
                f_close = f_row[2]
                f_ema = f_row[3]
                
                if "trailing" in tp_name:
                    if tp_name == "atr_trailing":
                        if sig_val == 1: sl = max(sl, f_close - (atr * 2))
                        else: sl = min(sl, f_close + (atr * 2))
                    elif tp_name == "ema_trailing":
                        if sig_val == 1: sl = max(sl, f_ema - atr)
                        else: sl = min(sl, f_ema + atr)
                
                if sig_val == 1: 
                    if f_low <= sl:
                        outcome = 1 if tp1_hit else 0
                        exit_price = sl
                        duration = (j + 1) * 15
                        break
                    if "fixed" in tp_name and f_high >= tp:
                        outcome = 1
                        exit_price = tp
                        duration = (j + 1) * 15
                        break
                    if "partial" in tp_name:
                        if not tp1_hit and f_high >= tp1:
                            tp1_hit = True
                            sl = entry_price
                        if f_high >= tp2:
                            outcome = 1
                            exit_price = (tp1 + tp2) / 2.0
                            duration = (j + 1) * 15
                            break
                else: 
                    if f_high >= sl:
                        outcome = 1 if tp1_hit else 0
                        exit_price = sl
                        duration = (j + 1) * 15
                        break
                    if "fixed" in tp_name and f_low <= tp:
                        outcome = 1
                        exit_price = tp
                        duration = (j + 1) * 15
                        break
                    if "partial" in tp_name:
                        if not tp1_hit and f_low <= tp1:
                            tp1_hit = True
                            sl = entry_price
                        if f_low <= tp2:
                            outcome = 1
                            exit_price = (tp1 + tp2) / 2.0
                            duration = (j + 1) * 15
                            break
            
            signal_type = "BUY" if sig_val == 1 else "SELL"
            
            if self.use_adaptive_allocator:
                pnl_raw = (exit_price - entry_price) if sig_val == 1 else (entry_price - exit_price)
                allocator.add_trade(outcome, pnl_raw / entry_price)
            
            ema_dist = (row.ema_fast - row.ema_slow) / row.ema_slow
            ema200_dist = (entry_price - row.ema_trend) / row.ema_trend
            
            trade = (
                ts_iso, target_symbol, "15m", signal_type,
                float(entry_price), float(tp) if "fixed" in tp_name else 0.0, float(sl), float(exit_price), int(outcome),
                int(duration), float(row.rsi), float(row.adx), float(atr), 
                float(ema_dist), float(ema200_dist), volat_regime, row.datetime_utc.hour, row.datetime_utc.dayofweek,
                float(row.context_score), float(pos_size), float(adx_f), float(vol_f), float(port_factor)
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
                context_score, position_size, adx_factor, vol_factor, portfolio_factor
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', trades)
        conn.commit()
        conn.close()

    def run_simulation(self, target_symbol):
        dfs = self.load_data(target_symbol)
        if any(dfs[k] is None for k in dfs): return
        
        df_merged = self.align_mtf(dfs)
        df_signals = self.generate_signals(df_merged)
        
        signal_threshold = self.config_override.get("signal_threshold", 3)
        df_scored = apply_edge_compression(df_signals, threshold=signal_threshold)
        
        df_filtered, filter_stats, collapse_warning = apply_filters(df_scored, self.filters_config)
        self.filter_stats = filter_stats
        self.collapse_warning = collapse_warning
        
        trades = self.simulate_trades(df_filtered, target_symbol=target_symbol, forward_window=200)
        self.save_trades(trades)

if __name__ == "__main__":
    pass
