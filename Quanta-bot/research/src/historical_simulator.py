import pandas as pd
import numpy as np
import sqlite3
import os
import json
import sys
import math
from collections import deque, defaultdict
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
from research.src.position_sizing import calculate_dynamic_position_size

from research.src.regime_classifier import classify_regime
from research.src.strategy_router import route_signal
from research.src.strategies import breakout_engine, mean_reversion_engine, expansion_engine, momentum_15m_engine

from research.src.engine_governor import (
    initial_state, tick_state, record_trade, get_pf,
    ENGINE_PRIORITY
)
from ml.model_inference import (
    predict_trade_quality,
    get_ml_runtime_metrics,
    reset_ml_runtime_metrics,
)

# ── Phase 6 Configuration ──────────────────────────────────────────────
EXEC_TIMEFRAMES     = ["4h"]
MACRO_TF            = "4h"
MIN_GAP_MINUTES     = 240        # 1 full 4H candle cooldown after any close
TF_MINUTES          = {"4h": 240}

RISK_PER_TRADE      = 0.005      # 0.5% (Phase 7.4 baseline)
MAX_CONCURRENT      = 2          # strict prop-firm concurrency
DAILY_DD_CAP        = -0.015     # -1.5% daily realized DD lock
MAX_NOTIONAL_MULT   = 5.0
ATR_MIN_RATIO       = 0.003      # atr/price must exceed this (anti-fee-trap)
ML_THRESHOLD        = 0.52


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
        self.ml_threshold = float(
            self.config_override.get("ml_threshold",
            self.config_override.get("ml_confidence_threshold", ML_THRESHOLD))
        )
        self.simulation_start = self._parse_optional_timestamp(self.config_override.get("simulation_start"))
        self.simulation_end = self._parse_optional_timestamp(self.config_override.get("simulation_end"))
        symbol_subset = self.config_override.get("symbol_subset")
        self.symbol_subset = set(symbol_subset) if symbol_subset else None
        self.regime_filter = self.config_override.get("regime_filter")
        self.shuffle_ml = bool(self.config_override.get("shuffle_ml", False))
        self.shuffle_seed = int(self.config_override.get("shuffle_seed", 42))
        self.audit_metrics = self._init_audit_metrics()

        self.exec_timeframes = self.config_override.get("exec_timeframes", EXEC_TIMEFRAMES)
        self.macro_tf = self.config_override.get("macro_tf", MACRO_TF)
        self.tf_minutes = dict(TF_MINUTES)
        self.tf_minutes.update(self.config_override.get("tf_minutes", {}))
        self.strategy_name = str(self.config_override.get("strategy_name", "expansion_engine"))
        self.taker_fee_rate = float(self.config_override.get("taker_fee_rate", 0.0005))
        self.slippage_rate_cfg = self.config_override.get("slippage_rate")
        self.risk_per_trade = float(self.config_override.get("risk_per_trade", RISK_PER_TRADE))

        # ── Sweep-configurable dials (Phase 7.4 Gold Standard defaults) ──
        self.daily_dd_cap = float(self.config_override.get("daily_dd_cap", DAILY_DD_CAP))
        self.max_concurrent = int(self.config_override.get("max_concurrent", MAX_CONCURRENT))
        self.max_notional_mult = float(self.config_override.get("max_notional_mult", MAX_NOTIONAL_MULT))
        self.atr_min_ratio = float(self.config_override.get("atr_min_ratio", ATR_MIN_RATIO))
        self.vol_factor_high = float(self.config_override.get("vol_factor_high", 0.5))
        self.gate_safe_threshold = float(self.config_override.get("gate_safe_threshold", 0.40))
        self.gate_no_trade_threshold = float(self.config_override.get("gate_no_trade_threshold", 0.65))
        self.gate_warning_ml_penalty = float(self.config_override.get("gate_warning_ml_penalty", 0.05))
        self.gate_trend_override_min_trend = float(self.config_override.get("gate_trend_override_min_trend", 0.60))
        self.gate_trend_override_max_vol = float(self.config_override.get("gate_trend_override_max_vol", 0.80))

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
            "rejected_low_priority": 0,
            "rejected_locks": 0,
            "rejected_other": 0,
            "rejected_signals": 0,
            "duplicate_signal_rejection_rate": 0.0,
            "avg_executed_score": 0.0,
            "avg_rejected_score": 0.0,
            "avg_all_signal_score": 0.0,
            "selection_quality_ratio": 0.0,
            "ml_filtered_trades": 0,
            "ml_candidates_scored": 0,
            "avg_ml_score_executed": 0.0,
            "avg_ml_score_rejected": 0.0,
            "ml_acceptance_rate": 0.0,
            "ml_fallback_count": 0,
            "ml_inference_error_count": 0,
            "acceptance_by_regime": {},
            "avg_threshold_modifier": 0.0,
            "phase74_safe_count": 0,
            "phase74_warning_count": 0,
            "phase74_no_trade_count": 0,
            "phase74_warning_penalty_count": 0,
            "phase74_trend_override_count": 0,
            "phase74_avg_risk_pressure": 0.0,
            "phase74_veto_share_ml_valid": 0.0,
            "phase74_blocked_ml_valid_count": 0,
            "phase74_ml_valid_candidates": 0,
            "phase74_reason_breakdown": {},
            "ml_score_distribution": {
                "min": 0.0,
                "max": 0.0,
                "mean": 0.0,
                "std": 0.0,
            },
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_pnl_pct": 0.0,
            "max_drawdown_pct": 0.0,
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

    def _effective_slippage_rate(self, notional):
        if self.slippage_rate_cfg is not None:
            return float(self.slippage_rate_cfg)
        return float(self._slippage_rate_from_notional(notional))

    @staticmethod
    def _normalize_buffer_feature(values):
        if not values:
            return []
        v_min = min(values)
        v_max = max(values)
        if v_max == v_min:
            return [0.5 for _ in values]
        return [(v - v_min) / (v_max - v_min) for v in values]

    @staticmethod
    def _parse_optional_timestamp(value):
        if value is None:
            return None
        ts = pd.to_datetime(value, errors="coerce", utc=False)
        if pd.isna(ts):
            return None
        return ts

    @staticmethod
    def _matches_regime_filter(regime_filter, row):
        if not regime_filter:
            return True

        name = str(regime_filter).upper().strip()
        adx = float(row.get("adx", 0.0) or 0.0)
        close_val = float(row.get("close", 0.0) or 0.0)
        atr = float(row.get("atr", 0.0) or 0.0)
        atr_ratio = (atr / close_val) if close_val else 0.0

        if name == "TRENDING":
            return adx > 25.0
        if name == "RANGING":
            return adx <= 25.0
        if name == "HIGH_VOLATILITY":
            return atr_ratio > 0.02
        if name == "LOW_VOLATILITY":
            return atr_ratio <= 0.02
        return True

    def _estimate_notional_for_scoring(self, current_balance, entry_price, sl_price, risk_mult):
        price_risk = abs(entry_price - sl_price)
        if entry_price <= 0 or price_risk <= 0:
            return 0.0
        effective_risk = self.risk_per_trade * risk_mult
        risk_amount = current_balance * effective_risk
        position_size = risk_amount / price_risk
        notional = position_size * entry_price
        max_notional = current_balance * self.max_notional_mult
        return max(0.0, min(notional, max_notional))

    def _build_ml_features(self, candidate):
        import pandas as _pd
        row = candidate["row"]
        ts = candidate.get("ts")
        if ts is None:
            # Fallback: reconstruct from open_time_ms or use epoch zero
            ot = row.get("open_time_ms", candidate.get("open_time_ms", 0))
            ts = _pd.Timestamp(int(ot), unit="ms", tz="UTC") if ot else _pd.Timestamp(0, unit="ms", tz="UTC")
        ema_trend_val = float(row.get("ema_trend", 0.0) or 0.0)
        close_val = float(row.get("close", 0.0) or 0.0)
        ema_distance = abs((close_val - ema_trend_val) / ema_trend_val) if ema_trend_val else 0.0
        candle_range = abs(close_val - float(row.get("target_sl", close_val))) / close_val if close_val else 0.0
        ema_slow_val = float(row.get("ema_slow", 0.0) or 0.0)
        ema_fast_val = float(row.get("ema_fast", 0.0) or 0.0)
        trend_strength = abs((ema_fast_val - ema_slow_val) / ema_slow_val) if ema_slow_val else 0.0
        atr_ratio_live = (float(row.get("atr", 0.0)) / close_val) if close_val else 0.0
        if atr_ratio_live < 0.008:
            volatility_regime = 0.0
        elif atr_ratio_live < 0.02:
            volatility_regime = 1.0
        else:
            volatility_regime = 2.0

        return {
            "trade_direction": 1.0 if row.get("signal") == 1 else -1.0,
            "atr_value": float(row.get("atr", 0.0)),
            "adx_value": float(row.get("adx", 0.0)),
            "ema_distance": float(ema_distance),
            "cost_estimate": float(candidate.get("estimated_cost", 0.0)),
            "volatility_regime": volatility_regime,
            "candle_range": float(candle_range),
            "trend_strength": float(trend_strength),
            "hour_of_day": float(ts.hour),
            "day_of_week": float(ts.dayofweek),
        }

    @staticmethod
    def _get_regime_threshold(adx, atr_ratio):
        is_trending = float(adx) > 25.0
        is_high_vol = float(atr_ratio) > 1.0

        if is_trending and is_high_vol:
            return 0.50
        if is_trending and (not is_high_vol):
            return 0.52
        if (not is_trending) and is_high_vol:
            return 0.54
        return 0.56

    @staticmethod
    def _get_regime_key(adx, atr_ratio):
        is_trending = float(adx) > 25.0
        is_high_vol = float(atr_ratio) > 1.0
        if is_trending and is_high_vol:
            return "TRENDING_HIGH_VOL"
        if is_trending and (not is_high_vol):
            return "TRENDING_LOW_VOL"
        if (not is_trending) and is_high_vol:
            return "RANGING_HIGH_VOL"
        return "RANGING_LOW_VOL"

    @staticmethod
    def _amplify_ml_score(raw_ml_prob):
        adjusted = float(raw_ml_prob) + ((float(raw_ml_prob) - 0.50) * 0.15)
        return max(0.0, min(1.0, adjusted))

    def _evaluate_market_regime_gate(self, candidate):
        row = candidate["row"]
        close_price = float(row.get("close", 0.0) or 0.0)
        atr_value = float(row.get("atr", 0.0) or 0.0)
        adx_value = float(row.get("adx", 0.0) or 0.0)
        high_val = float(row.get("high", close_price) or close_price)
        low_val = float(row.get("low", close_price) or close_price)

        if close_price <= 0:
            return {
                "risk_pressure": 1.0,
                "regime": "NO_TRADE",
                "trade_allowed": False,
                "ml_penalty": None,
                "reason": "invalid_close_price",
                "scores": {
                    "vol_score": 1.0,
                    "trend_score": 0.0,
                    "volume_score": 0.0,
                    "spread_score": 1.0,
                },
            }

        atr_ratio = atr_value / close_price
        vol_score = min(1.0, (atr_ratio / 0.05))
        trend_score = min(1.0, (adx_value / 50.0))

        current_notional = float(candidate.get("current_notional", 0.0) or 0.0)
        baseline_average_notional = float(candidate.get("baseline_average_notional", 0.0) or 0.0)
        if baseline_average_notional <= 0:
            volume_score = 1.0
        else:
            volume_score = min(1.0, (current_notional / baseline_average_notional))

        spread_ratio = (high_val - low_val) / close_price if close_price else 0.0
        spread_score = min(1.0, (spread_ratio / 0.02))

        risk_pressure = (
            (0.35 * vol_score)
            + (0.25 * spread_score)
            + (0.25 * (1.0 - volume_score))
            + (0.15 * (1.0 - trend_score))
        )

        if risk_pressure < self.gate_safe_threshold:
            regime = "SAFE"
            trade_allowed = True
            ml_penalty = 0.0
            reason = "safe_low_pressure"
        elif risk_pressure < self.gate_no_trade_threshold:
            regime = "WARNING"
            trade_allowed = True
            ml_penalty = self.gate_warning_ml_penalty
            reason = "warning_elevated_pressure"
        else:
            regime = "NO_TRADE"
            trade_allowed = False
            ml_penalty = None
            reason = "no_trade_high_pressure"

        override_applied = False
        if trend_score > self.gate_trend_override_min_trend and vol_score < self.gate_trend_override_max_vol:
            if regime == "NO_TRADE":
                regime = "WARNING"
                trade_allowed = True
                ml_penalty = self.gate_warning_ml_penalty
                reason = "trend_override_no_trade_to_warning"
                override_applied = True
            elif regime == "WARNING":
                regime = "SAFE"
                trade_allowed = True
                ml_penalty = 0.0
                reason = "trend_override_warning_to_safe"
                override_applied = True

        return {
            "risk_pressure": float(risk_pressure),
            "regime": regime,
            "trade_allowed": bool(trade_allowed),
            "ml_penalty": ml_penalty,
            "reason": reason,
            "override_applied": override_applied,
            "scores": {
                "vol_score": float(vol_score),
                "trend_score": float(trend_score),
                "volume_score": float(volume_score),
                "spread_score": float(spread_score),
            },
        }

    def _score_signal_buffer(self, signal_buffer):
        if not signal_buffer:
            return
        atr_vals = [s["atr_ratio"] for s in signal_buffer]
        adx_vals = [s["adx"] for s in signal_buffer]
        ema_vals = [s["price_distance_from_ema"] for s in signal_buffer]
        cost_vals = [s["estimated_cost"] for s in signal_buffer]

        norm_atr = self._normalize_buffer_feature(atr_vals)
        norm_adx = self._normalize_buffer_feature(adx_vals)
        norm_ema = self._normalize_buffer_feature(ema_vals)
        norm_cost = self._normalize_buffer_feature(cost_vals)

        for i, s in enumerate(signal_buffer):
            score = (
                (norm_atr[i] * 0.4)
                + (norm_adx[i] * 0.3)
                + (norm_ema[i] * 0.2)
                - (norm_cost[i] * 0.1)
            )
            s["score"] = score

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
        candidates = [
            os.path.join(self.data_dir, "processed", f"{symbol}_{tf}_features.csv"),
            os.path.join(self.data_dir, "processed", f"{symbol}_{tf}_raw.csv"),
            os.path.join(self.data_dir, f"{symbol}_{tf}_history.csv"),
            os.path.join(self.data_dir, "raw", f"{symbol}_{tf}_raw.csv"),
        ]
        path = next((p for p in candidates if os.path.exists(p)), None)
        if path is None:
            return None
        df = pd.read_csv(path)
        df['datetime_utc'] = pd.to_datetime(df['datetime_utc'])
        df = df.sort_values('datetime_utc').drop_duplicates('datetime_utc').reset_index(drop=True)
        return self.calculate_indicators(df)

    def load_all_tfs(self, symbol):
        needed = set(self.exec_timeframes) | {self.macro_tf}
        return {tf: self.load_tf(symbol, tf) for tf in needed}

    # ── Signal generation ──────────────────────────────────────────────
    def generate_signals_for_tf(self, df_exec, df_macro, exec_tf):
        if df_exec is None: return []
        if self.strategy_name == "mean_reversion_engine":
            merged = df_exec.copy()
        elif self.strategy_name == "momentum_15m":
            macro = df_macro[["datetime_utc", "adx", "ema_fast", "ema_slow"]].copy()
            macro["htf_trend"] = np.where(
                macro["ema_fast"] > macro["ema_slow"],
                1,
                np.where(macro["ema_fast"] < macro["ema_slow"], -1, 0),
            )
            macro = macro.rename(columns={"adx": "htf_adx"})
            merged = pd.merge_asof(
                df_exec.sort_values("datetime_utc"),
                macro[["datetime_utc", "htf_adx", "htf_trend"]].sort_values("datetime_utc"),
                on="datetime_utc",
                direction="backward",
            ).dropna()
        else:
            if df_macro is None:
                return []
            df_m = df_macro.add_suffix('_4h').rename(columns={'datetime_utc_4h': 'datetime_utc'})
            merged = pd.merge_asof(df_exec, df_m, on='datetime_utc', direction='backward').dropna()

        records = merged.to_dict('records')
        signals = []
        for i, row in enumerate(records):
            if i == 0: continue

            if self.strategy_name == "momentum_15m":
                sig_dict = momentum_15m_engine.generate_signal(row)
                if sig_dict['signal'] == 0:
                    continue
                row['signal'] = sig_dict['signal']
                row['regime'] = 'MOMENTUM_15M'
                row['strategy_used'] = sig_dict['strategy']
                row['target_sl'] = sig_dict['sl']
                row['target_tp1'] = sig_dict['tp1']
                row['target_tp2'] = sig_dict['tp2']
                row['exec_tf'] = exec_tf
                signals.append(row)
                continue

            if self.strategy_name == "mean_reversion_engine":
                sig_dict = mean_reversion_engine.generate_signal(row)
                if sig_dict['signal'] == 0:
                    continue
                row['signal'] = sig_dict['signal']
                row['regime'] = 'MEAN_REVERSION'
                row['strategy_used'] = sig_dict['strategy']
                row['target_sl'] = sig_dict['sl']
                row['target_tp1'] = sig_dict['tp1']
                row['target_tp2'] = sig_dict['tp2']
                row['exec_tf'] = exec_tf
                if 'suggested_entry' in sig_dict:
                    row['suggested_entry'] = sig_dict['suggested_entry']
                signals.append(row)
                continue

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
    def run_portfolio_simulation(self, watchlist, simulation_start=None, simulation_end=None, symbol_subset=None):
        self.audit_metrics = self._init_audit_metrics()
        reset_ml_runtime_metrics()
        self.price_data = {}
        all_signals     = []

        start_ts = self._parse_optional_timestamp(simulation_start)
        end_ts = self._parse_optional_timestamp(simulation_end)
        if start_ts is None:
            start_ts = self.simulation_start
        if end_ts is None:
            end_ts = self.simulation_end

        if symbol_subset is None:
            symbol_subset = self.symbol_subset
        elif symbol_subset:
            symbol_subset = set(symbol_subset)

        if symbol_subset:
            watchlist = [s for s in watchlist if s in symbol_subset]

        for symbol in watchlist:
            tf_data  = self.load_all_tfs(symbol)
            df_macro = tf_data.get(self.macro_tf)
            if df_macro is None: continue

            for exec_tf in self.exec_timeframes:
                df_exec = tf_data.get(exec_tf)
                if df_exec is None: continue

                signals = self.generate_signals_for_tf(df_exec, df_macro, exec_tf)
                key = f"{symbol}_{exec_tf}"
                if "volume" in df_exec.columns:
                    vol_series = pd.to_numeric(df_exec["volume"], errors="coerce").fillna(0.0)
                    notional_series = pd.to_numeric(df_exec["close"], errors="coerce").fillna(0.0) * vol_series
                else:
                    notional_series = pd.Series(0.0, index=df_exec.index)
                baseline_notional = notional_series.rolling(20, min_periods=5).mean().shift(1)
                baseline_notional = baseline_notional.fillna(notional_series.expanding().mean().shift(1))
                baseline_notional = baseline_notional.fillna(0.0)

                self.price_data[key] = {
                    'prices':  df_exec[['open', 'high', 'low', 'close', 'ema_fast', 'ema_slow', 'datetime_utc']].values,
                    'idx_map': {ts: i for i, ts in enumerate(df_exec['datetime_utc'])},
                    'notional_map': {
                        ts: float(v) for ts, v in zip(df_exec['datetime_utc'], notional_series.tolist())
                    },
                    'baseline_notional_map': {
                        ts: float(v) for ts, v in zip(df_exec['datetime_utc'], baseline_notional.tolist())
                    },
                }
                for r in signals:
                    r['symbol'] = symbol
                all_signals.extend(signals)

        all_signals.sort(key=lambda x: x['datetime_utc'])
        if start_ts is not None:
            all_signals = [s for s in all_signals if s["datetime_utc"] >= start_ts]
        if end_ts is not None:
            all_signals = [s for s in all_signals if s["datetime_utc"] < end_ts]

        trades = self.simulate_portfolio_trades(all_signals, watchlist)
        self.save_trades(trades)

        audit_path = None
        if self.db_path != ":memory:":
            db_file = Path(self.db_path)
            audit_path = db_file.with_name(f"{db_file.stem}_phase61_audit.json")
            with open(audit_path, "w", encoding="utf-8") as f:
                json.dump(self.audit_metrics, f, indent=2)
            phase62_audit_path = db_file.with_name(f"{db_file.stem}_phase62_audit.json")
            with open(phase62_audit_path, "w", encoding="utf-8") as f:
                json.dump(self.audit_metrics, f, indent=2)
            phase7_audit_path = db_file.with_name(f"{db_file.stem}_phase7_audit.json")
            with open(phase7_audit_path, "w", encoding="utf-8") as f:
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

        daily_closed_pnl  = {}
        symbol_cooldown   = {}
        pair_counts       = {}

        executed_scores = []
        rejected_scores = []
        all_buffered_scores = []
        ml_executed_probs = []
        ml_rejected_probs = []
        all_ml_probs = []
        recent_ml_decisions = deque(maxlen=100)
        global_threshold_modifier = 0.0
        threshold_modifier_values = []
        regime_stats = defaultdict(lambda: {"candidates_scored": 0, "accepted": 0})
        phase74_risk_pressures = []
        phase74_reason_counts = defaultdict(int)
        shuffle_rng = np.random.default_rng(self.shuffle_seed) if self.shuffle_ml else None

        def flush_cluster_metrics(symbols_at_ts):
            if len(symbols_at_ts) < 2:
                return
            cluster_size = len(symbols_at_ts)
            self.audit_metrics["cluster_event_count"] += 1
            self.audit_metrics["cluster_event_total_size"] += cluster_size
            self.audit_metrics["same_candle_multi_symbol_activations"] += cluster_size
            for a, b in combinations(sorted(symbols_at_ts), 2):
                key = f"{a}|{b}"
                pair_counts[key] = pair_counts.get(key, 0) + 1

        idx = 0
        n = len(all_signals)
        while idx < n:
            ts = all_signals[idx]["datetime_utc"]
            decisions_this_buffer = []
            batch_rows = []
            while idx < n and all_signals[idx]["datetime_utc"] == ts:
                batch_rows.append(all_signals[idx])
                idx += 1

            still_open = []
            for pos in open_positions:
                if pos["exit_time"] <= ts:
                    close_date = pos["exit_time"].date()
                    daily_closed_pnl[close_date] = daily_closed_pnl.get(close_date, 0.0) + pos["net_pnl"]
                else:
                    still_open.append(pos)
            open_positions = still_open

            signal_buffer = []
            symbols_at_ts = set()
            today = ts.date()
            today_pnl = daily_closed_pnl.get(today, 0.0)
            daily_dd_blocked = today_pnl <= (current_balance * self.daily_dd_cap)

            for row in batch_rows:
                symbol = row["symbol"]
                strategy_used = row.get("strategy_used", "none")

                if not self._matches_regime_filter(self.regime_filter, row):
                    continue

                entry_price = row["close"]
                atr = row["atr"]

                if not evaluate_trade_quality(row):
                    continue
                if entry_price == 0 or (atr / entry_price) < self.atr_min_ratio:
                    continue

                self.audit_metrics["total_signals_generated"] += 1
                symbols_at_ts.add(symbol)

                if daily_dd_blocked:
                    self.daily_dd_breaches += 1 if today_pnl == (current_balance * DAILY_DD_CAP) else 0
                    self.audit_metrics["rejected_other"] += 1
                    continue

                if strategy_used in ENGINE_PRIORITY:
                    allow, risk_mult = tick_state(strategy_used, engine_states[strategy_used], gov_log)
                    if not allow:
                        self.audit_metrics["rejected_other"] += 1
                        continue
                    eng_state_label = engine_states[strategy_used]["state"]
                else:
                    risk_mult = 1.0
                    eng_state_label = "N/A"

                sl = row["target_sl"]
                notional_est = self._estimate_notional_for_scoring(current_balance, entry_price, sl, risk_mult)
                estimated_cost = (self.taker_fee_rate + self._effective_slippage_rate(notional_est))
                ema_distance = abs((entry_price - row["ema_trend"]) / row["ema_trend"]) if row["ema_trend"] else 0.0

                signal_buffer.append({
                    "row": row,
                    "ts": ts,
                    "symbol": symbol,
                    "exec_tf": row.get("exec_tf", "4h"),
                    "tf_min": self.tf_minutes.get(row.get("exec_tf", "4h"), 240),
                    "strategy_used": strategy_used,
                    "risk_mult": risk_mult,
                    "eng_state_label": eng_state_label,
                    "atr_ratio": atr / entry_price,
                    "adx": row.get("adx", 0.0),
                    "price_distance_from_ema": ema_distance,
                    "estimated_cost": estimated_cost,
                    "current_notional": float(self.price_data.get(f"{symbol}_{row.get('exec_tf', '4h')}", {}).get("notional_map", {}).get(ts, 0.0)),
                    "baseline_average_notional": float(self.price_data.get(f"{symbol}_{row.get('exec_tf', '4h')}", {}).get("baseline_notional_map", {}).get(ts, 0.0)),
                })

            flush_cluster_metrics(symbols_at_ts)
            if not signal_buffer:
                continue

            self._score_signal_buffer(signal_buffer)
            signal_buffer.sort(key=lambda s: (-s.get("score", 0.0), s["symbol"]))

            for s in signal_buffer:
                all_buffered_scores.append(s.get("score", 0.0))

            slots_available_start = max(0, self.max_concurrent - len(open_positions))
            executed_this_ts = 0
            shuffled_prob_map = {}
            if self.shuffle_ml and slots_available_start > 0:
                pre_ml_candidates = []
                for candidate in signal_buffer:
                    symbol = candidate["symbol"]
                    ts = candidate["ts"]
                    cooldown_until = symbol_cooldown.get(symbol)
                    if cooldown_until is not None and ts < cooldown_until:
                        continue
                    if any(p["symbol"] == symbol for p in open_positions):
                        continue
                    if slots_available_start <= 0 or len(open_positions) >= self.max_concurrent:
                        continue
                    pre_ml_candidates.append(candidate)

                if pre_ml_candidates:
                    pre_ml_probs = [
                        float(predict_trade_quality(self._build_ml_features(c)))
                        for c in pre_ml_candidates
                    ]
                    if len(pre_ml_probs) > 1 and shuffle_rng is not None:
                        shuffle_rng.shuffle(pre_ml_probs)
                    shuffled_prob_map = {
                        id(c): p for c, p in zip(pre_ml_candidates, pre_ml_probs)
                    }

            for candidate in signal_buffer:
                row = candidate["row"]
                symbol = candidate["symbol"]
                score = candidate.get("score", 0.0)
                ts = candidate["ts"]
                exec_tf = candidate["exec_tf"]
                tf_min = candidate["tf_min"]
                strategy_used = candidate["strategy_used"]
                risk_mult = candidate["risk_mult"]
                eng_state_label = candidate["eng_state_label"]

                cooldown_until = symbol_cooldown.get(symbol)
                if cooldown_until is not None and ts < cooldown_until:
                    self.audit_metrics["rejected_symbol_cooldown_lock"] += 1
                    rejected_scores.append(score)
                    continue

                if any(p["symbol"] == symbol for p in open_positions):
                    self.audit_metrics["rejected_symbol_open_lock"] += 1
                    rejected_scores.append(score)
                    continue

                if slots_available_start <= 0 or len(open_positions) >= self.max_concurrent:
                    self.audit_metrics["rejected_concurrency_lock"] += 1
                    rejected_scores.append(score)
                    continue

                if executed_this_ts >= slots_available_start:
                    self.audit_metrics["rejected_low_priority"] += 1
                    rejected_scores.append(score)
                    continue

                if self.shuffle_ml:
                    raw_ml_prob = shuffled_prob_map.get(id(candidate))
                    if raw_ml_prob is None:
                        raw_ml_prob = float(predict_trade_quality(self._build_ml_features(candidate)))
                else:
                    raw_ml_prob = float(predict_trade_quality(self._build_ml_features(candidate)))

                adx_val = float(row.get("adx", 0.0) or 0.0)
                atr_ratio_val = float(candidate.get("atr_ratio", 0.0) or 0.0)
                regime_key = self._get_regime_key(adx_val, atr_ratio_val)
                regime_stats[regime_key]["candidates_scored"] += 1

                base_threshold = self._get_regime_threshold(adx_val, atr_ratio_val)
                baseline_offset = float(self.ml_threshold) - float(ML_THRESHOLD)
                effective_threshold = base_threshold + baseline_offset + global_threshold_modifier
                effective_threshold = max(0.0, min(1.0, effective_threshold))
                threshold_modifier_values.append(global_threshold_modifier)

                adjusted_score = self._amplify_ml_score(raw_ml_prob)

                gate = self._evaluate_market_regime_gate(candidate)
                phase74_risk_pressures.append(float(gate.get("risk_pressure", 0.0)))
                gate_reason = str(gate.get("reason", "unknown"))
                phase74_reason_counts[gate_reason] += 1
                if bool(gate.get("override_applied", False)):
                    self.audit_metrics["phase74_trend_override_count"] += 1

                gate_regime = gate.get("regime", "SAFE")
                if gate_regime == "SAFE":
                    self.audit_metrics["phase74_safe_count"] += 1
                elif gate_regime == "WARNING":
                    self.audit_metrics["phase74_warning_count"] += 1
                else:
                    self.audit_metrics["phase74_no_trade_count"] += 1

                would_pass_without_gate = adjusted_score >= effective_threshold
                if would_pass_without_gate:
                    self.audit_metrics["phase74_ml_valid_candidates"] += 1

                if not gate.get("trade_allowed", True):
                    if would_pass_without_gate:
                        self.audit_metrics["phase74_blocked_ml_valid_count"] += 1
                    self.audit_metrics["ml_filtered_trades"] += 1
                    rejected_scores.append(score)
                    ml_rejected_probs.append(raw_ml_prob)
                    continue

                warning_penalty = float(gate.get("ml_penalty", 0.0) or 0.0)
                if warning_penalty > 0:
                    self.audit_metrics["phase74_warning_penalty_count"] += 1
                    adjusted_score = max(0.0, adjusted_score - warning_penalty)

                self.audit_metrics["ml_candidates_scored"] += 1
                all_ml_probs.append(float(raw_ml_prob))

                if adjusted_score < effective_threshold:
                    self.audit_metrics["ml_filtered_trades"] += 1
                    rejected_scores.append(score)
                    ml_rejected_probs.append(raw_ml_prob)
                    decisions_this_buffer.append(0)
                    continue

                regime_stats[regime_key]["accepted"] += 1
                decisions_this_buffer.append(1)
                ml_executed_probs.append(raw_ml_prob)

                sig_val = row["signal"]
                entry_price = row["close"]
                atr = row["atr"]
                sl = row["target_sl"]
                tp1 = row["target_tp1"]
                tp2 = row["target_tp2"]
                regime = row["regime"]
                volat_regime = "HIGH" if atr > (entry_price * 0.02) else "NORMAL"

                _, _, vol_factor = calculate_dynamic_position_size(
                    adx=float(row.get("adx", 0.0) or 0.0),
                    volatility_regime=volat_regime,
                    portfolio_factor=1.0,
                    asset_tier_factor=1.0,
                    vol_factor_high=self.vol_factor_high,
                )

                effective_risk = self.risk_per_trade * risk_mult * float(vol_factor)
                price_risk = abs(entry_price - sl)
                if price_risk == 0:
                    self.audit_metrics["rejected_other"] += 1
                    continue

                risk_amount = current_balance * effective_risk
                position_size = risk_amount / price_risk
                notional = position_size * entry_price

                max_notional = current_balance * self.max_notional_mult
                if notional > max_notional:
                    notional = max_notional
                    position_size = notional / entry_price

                if notional == 0:
                    self.audit_metrics["rejected_other"] += 1
                    continue

                margin_required = notional / 10.0
                if margin_required > current_balance:
                    self.audit_metrics["rejected_other"] += 1
                    continue

                if not is_fee_viable(
                    entry_price,
                    sl,
                    notional,
                    taker_fee_rate=self.taker_fee_rate,
                    slippage_rate=self._effective_slippage_rate(notional),
                ):
                    self.audit_metrics["rejected_other"] += 1
                    continue

                entry_fee_rate = self.taker_fee_rate
                slippage_rate = self._effective_slippage_rate(notional)

                key = f"{symbol}_{exec_tf}"
                idx_map = self.price_data[key]["idx_map"]
                if ts not in idx_map:
                    self.audit_metrics["rejected_other"] += 1
                    continue

                idx_start = idx_map[ts]
                prices = self.price_data[key]["prices"]
                end_idx = min(idx_start + forward_window, len(prices))
                slice_window = prices[idx_start + 1:end_idx]

                self.audit_metrics["total_signals_executed"] += 1
                executed_this_ts += 1
                executed_scores.append(score)

                outcome = 0
                duration = 0
                gross_pnl = 0.0
                fees_paid = notional * entry_fee_rate
                slippage_paid = notional * slippage_rate
                exit_timestamp = ts
                remaining_notional = notional
                current_sl = sl
                final_exit_price = entry_price
                tp1_hit = False
                using_ema_trail = (tp2 == 0)

                for j, f_row in enumerate(slice_window):
                    f_high = f_row[1]; f_low = f_row[2]
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

                if remaining_notional > 0:
                    end_row = slice_window[-1] if len(slice_window) > 0 else None
                    if end_row is not None:
                        f_close = end_row[3]; f_ts = end_row[6]
                    else:
                        f_close = entry_price; f_ts = ts
                    outcome = 3
                    raw_pct = (f_close - entry_price) / entry_price if sig_val == 1 else (entry_price - f_close) / entry_price
                    gross_pnl += raw_pct * remaining_notional
                    fees_paid += remaining_notional * entry_fee_rate
                    slippage_paid += remaining_notional * slippage_rate
                    exit_timestamp = f_ts; final_exit_price = f_close
                    duration = min((j + 1) * tf_min, forward_window * tf_min) if len(slice_window) > 0 else 0

                net_pnl = gross_pnl - fees_paid - slippage_paid
                current_balance += net_pnl
                risk_engine.update_pnl(net_pnl)
                if strategy_used in engine_states:
                    record_trade(engine_states[strategy_used], net_pnl)

                open_positions.append({
                    "exit_time": exit_timestamp,
                    "symbol": symbol,
                    "margin": margin_required,
                    "net_pnl": net_pnl,
                })
                symbol_cooldown[symbol] = exit_timestamp + timedelta(minutes=tf_min)

                ts_iso = ts.isoformat()
                signal_type = "BUY" if sig_val == 1 else "SELL"
                ema_dist = (row["ema_fast"] - row["ema_slow"]) / row["ema_slow"]
                ema200_dist = (entry_price - row["ema_trend"]) / row["ema_trend"]
                saved_tp2 = tp2 if tp2 != 0 else final_exit_price
                row["context_score"] = score

                outcomes.append((
                    ts_iso, symbol, exec_tf, regime, strategy_used, eng_state_label,
                    signal_type,
                    float(entry_price), float(saved_tp2), float(current_sl), float(final_exit_price), int(outcome),
                    int(duration), float(row["rsi"]), float(row["adx"]), float(atr),
                    float(ema_dist), float(ema200_dist), volat_regime, ts.hour, ts.dayofweek,
                    float(row.get("context_score", 0)), float(notional), float(margin_required),
                    float(fees_paid), float(slippage_paid), float(net_pnl), float(current_balance)
                ))

            if decisions_this_buffer:
                recent_ml_decisions.extend(decisions_this_buffer)
                denom = 100 if len(recent_ml_decisions) >= 100 else max(1, len(recent_ml_decisions))
                current_acceptance = (sum(recent_ml_decisions) / float(denom)) if denom else 0.0
                if current_acceptance > 0.45:
                    global_threshold_modifier += 0.01
                elif current_acceptance < 0.25:
                    global_threshold_modifier -= 0.01
                global_threshold_modifier = max(-0.02, min(0.04, global_threshold_modifier))

        total_locks = (
            self.audit_metrics["rejected_concurrency_lock"]
            + self.audit_metrics["rejected_symbol_open_lock"]
            + self.audit_metrics["rejected_symbol_cooldown_lock"]
        )
        self.audit_metrics["rejected_locks"] = total_locks
        total_rejected = total_locks + self.audit_metrics["rejected_low_priority"] + self.audit_metrics["ml_filtered_trades"]
        self.audit_metrics["rejected_signals"] = total_rejected

        generated = self.audit_metrics["total_signals_generated"]
        self.audit_metrics["duplicate_signal_rejection_rate"] = (
            total_rejected / generated if generated else 0.0
        )

        self.audit_metrics["avg_executed_score"] = (
            sum(executed_scores) / len(executed_scores) if executed_scores else 0.0
        )
        self.audit_metrics["avg_rejected_score"] = (
            sum(rejected_scores) / len(rejected_scores) if rejected_scores else 0.0
        )
        self.audit_metrics["avg_all_signal_score"] = (
            sum(all_buffered_scores) / len(all_buffered_scores) if all_buffered_scores else 0.0
        )
        avg_all = self.audit_metrics["avg_all_signal_score"]
        self.audit_metrics["selection_quality_ratio"] = (
            self.audit_metrics["avg_executed_score"] / avg_all if avg_all else 0.0
        )

        self.audit_metrics["avg_ml_score_executed"] = (
            sum(ml_executed_probs) / len(ml_executed_probs) if ml_executed_probs else 0.0
        )
        self.audit_metrics["avg_ml_score_rejected"] = (
            sum(ml_rejected_probs) / len(ml_rejected_probs) if ml_rejected_probs else 0.0
        )
        ml_total = self.audit_metrics["ml_candidates_scored"]
        ml_accepted = ml_total - self.audit_metrics["ml_filtered_trades"]
        self.audit_metrics["ml_acceptance_rate"] = (ml_accepted / ml_total) if ml_total else 0.0
        runtime_ml = get_ml_runtime_metrics()
        self.audit_metrics["ml_fallback_count"] = int(runtime_ml.get("ml_fallback_count", 0))
        self.audit_metrics["ml_inference_error_count"] = int(runtime_ml.get("ml_inference_error_count", 0))
        self.audit_metrics["avg_threshold_modifier"] = (
            sum(threshold_modifier_values) / len(threshold_modifier_values)
            if threshold_modifier_values else 0.0
        )
        self.audit_metrics["phase74_avg_risk_pressure"] = (
            sum(phase74_risk_pressures) / len(phase74_risk_pressures)
            if phase74_risk_pressures else 0.0
        )
        ml_valid_candidates = int(self.audit_metrics.get("phase74_ml_valid_candidates", 0))
        blocked_ml_valid = int(self.audit_metrics.get("phase74_blocked_ml_valid_count", 0))
        self.audit_metrics["phase74_veto_share_ml_valid"] = (
            (blocked_ml_valid / ml_valid_candidates) if ml_valid_candidates else 0.0
        )
        self.audit_metrics["phase74_reason_breakdown"] = dict(
            sorted(phase74_reason_counts.items(), key=lambda x: x[1], reverse=True)
        )

        acceptance_by_regime = {}
        for k, v in regime_stats.items():
            scored = int(v.get("candidates_scored", 0))
            accepted = int(v.get("accepted", 0))
            acceptance_by_regime[k] = {
                "candidates_scored": scored,
                "accepted": accepted,
                "acceptance_rate": (accepted / scored) if scored else 0.0,
            }
        self.audit_metrics["acceptance_by_regime"] = acceptance_by_regime

        if all_ml_probs:
            probs_arr = np.array(all_ml_probs, dtype=float)
            self.audit_metrics["ml_score_distribution"] = {
                "min": float(np.min(probs_arr)),
                "max": float(np.max(probs_arr)),
                "mean": float(np.mean(probs_arr)),
                "std": float(np.std(probs_arr, ddof=0)),
            }

        if outcomes:
            pnl_series = [float(t[26]) for t in outcomes]
            bal_series = [self.initial_balance]
            bal_series.extend(float(t[27]) for t in outcomes)
            wins = sum(1 for p in pnl_series if p > 0)
            losses = len(pnl_series) - wins
            self.audit_metrics["win_rate"] = (wins / len(pnl_series) * 100.0) if pnl_series else 0.0
            pos_sum = sum(p for p in pnl_series if p > 0)
            neg_sum = abs(sum(p for p in pnl_series if p <= 0))
            self.audit_metrics["profit_factor"] = (pos_sum / neg_sum) if neg_sum > 0 else float("inf")
            net_pnl_total = sum(pnl_series)
            self.audit_metrics["net_pnl_pct"] = (net_pnl_total / self.initial_balance * 100.0) if self.initial_balance else 0.0

            running = np.array(bal_series, dtype=float)
            peaks = np.maximum.accumulate(running)
            dd = (running - peaks) / peaks
            self.audit_metrics["max_drawdown_pct"] = float(np.min(dd) * 100.0)

        cluster_count = self.audit_metrics["cluster_event_count"]
        self.audit_metrics["cluster_event_avg_size"] = (
            self.audit_metrics["cluster_event_total_size"] / cluster_count if cluster_count else 0.0
        )
        self.audit_metrics["co_activation_pairs"] = dict(
            sorted(pair_counts.items(), key=lambda x: x[1], reverse=True)
        )
        self.audit_metrics["final_balance"] = current_balance

        if gov_log:
            print("\n[GOVERNANCE LOG]")
            seen = {}
            for line in gov_log:
                for eng in ENGINE_PRIORITY:
                    if eng in line:
                        seen[eng] = line
            for eng, line in seen.items():
                print(f"  {line}")

        print("\n[ENGINE FINAL STATES]")
        for eng in ENGINE_PRIORITY:
            es = engine_states[eng]
            pf = get_pf(es)
            ra = es["recovery_attempts"]
            rs = es["recovery_successes"]
            print(f"  {eng}: state={es['state']} | trades={es['trades']} | PF={pf:.2f} "
                  f"| active={es['ticks_active']} cd={es['ticks_cooldown']} rec={es['ticks_recovery']} "
                  f"| recovery={rs}/{ra if ra>0 else 'N/A'}")

        print(f"\n[DAILY DD] Total breach days: {self.daily_dd_breaches}")
        print("\n[PHASE 6.2 AUDIT]")
        print(f"  Generated signals: {self.audit_metrics['total_signals_generated']}")
        print(f"  Executed signals : {self.audit_metrics['total_signals_executed']}")
        print(f"  Rejected (total) : {self.audit_metrics['rejected_signals']}")
        print(f"  Rejected (locks) : {self.audit_metrics['rejected_locks']}")
        print(f"  Rejected (low pr): {self.audit_metrics['rejected_low_priority']}")
        print(f"  Dup reject rate  : {self.audit_metrics['duplicate_signal_rejection_rate']:.2%}")
        print(f"  Avg exec score   : {self.audit_metrics['avg_executed_score']:.4f}")
        print(f"  Avg rej score    : {self.audit_metrics['avg_rejected_score']:.4f}")
        print(f"  Selection quality: {self.audit_metrics['selection_quality_ratio']:.4f}")
        print(f"  Cluster events   : {self.audit_metrics['cluster_event_count']}")
        print(f"  Avg cluster size : {self.audit_metrics['cluster_event_avg_size']:.2f}")

        print("\n[PHASE 7 ML METRICS]")
        print(f"  ML threshold      : {self.ml_threshold:.2f}")
        print(f"  ML scored         : {self.audit_metrics['ml_candidates_scored']}")
        print(f"  ML filtered trades: {self.audit_metrics['ml_filtered_trades']}")
        print(f"  ML accept rate    : {self.audit_metrics['ml_acceptance_rate']:.2%}")
        print(f"  Avg ML exec prob  : {self.audit_metrics['avg_ml_score_executed']:.4f}")
        print(f"  Avg ML rej prob   : {self.audit_metrics['avg_ml_score_rejected']:.4f}")
        print(f"  Avg threshold mod : {self.audit_metrics['avg_threshold_modifier']:.4f}")
        print(f"  Phase7.4 risk avg : {self.audit_metrics['phase74_avg_risk_pressure']:.4f}")
        print(f"  Phase7.4 SAFE/WARN/NO: {self.audit_metrics['phase74_safe_count']}/{self.audit_metrics['phase74_warning_count']}/{self.audit_metrics['phase74_no_trade_count']}")
        print(f"  Phase7.4 veto share  : {self.audit_metrics['phase74_veto_share_ml_valid']:.2%}")
        print(f"  ML fallback count : {self.audit_metrics['ml_fallback_count']}")
        print(f"  ML infer err count: {self.audit_metrics['ml_inference_error_count']}")
        if self.audit_metrics.get("acceptance_by_regime"):
            print("  Acceptance/regime :")
            for rk, rv in sorted(self.audit_metrics["acceptance_by_regime"].items()):
                print(
                    f"    - {rk}: scored={rv.get('candidates_scored', 0)} "
                    f"accepted={rv.get('accepted', 0)} "
                    f"rate={rv.get('acceptance_rate', 0.0):.2%}"
                )
        ml_dist = self.audit_metrics.get("ml_score_distribution", {})
        print(
            f"  ML prob dist      : min={ml_dist.get('min', 0.0):.4f} "
            f"max={ml_dist.get('max', 0.0):.4f} mean={ml_dist.get('mean', 0.0):.4f} std={ml_dist.get('std', 0.0):.4f}"
        )
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
