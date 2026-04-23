from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from market_data.src import indicators
from ml.model_inference import predict_trade_quality
from production.src.live_telemetry import LiveTelemetryStore, default_db_path
from production.src.notifications import send_alert_async
from research.src.historical_simulator import (
    ML_THRESHOLD,
    MAX_NOTIONAL_MULT,
    RISK_PER_TRADE,
    HistoricalSimulator,
)
from research.src.regime_classifier import classify_regime
from research.src.strategies import expansion_engine
from research.src.strategy_router import route_signal


@dataclass
class ApprovedTrade:
    """Approved trade envelope produced by QuantaAdapter for order placement."""

    signal_id: str
    timestamp: str
    symbol: str
    side: str
    expected_price: float
    quantity: float
    tp_price: float
    sl_price: float
    strategy_name: str
    metadata: dict[str, Any]


class BinanceDataStreamer:
    """Consumes Binance Futures Testnet websocket events and emits closed 4h candles."""

    def __init__(self, symbols: list[str], interval: str = "4h") -> None:
        self.symbols = symbols
        self.interval = interval
        self.ws_url = "wss://stream.binancefuture.com/stream"
        self.rest_url = "https://testnet.binancefuture.com"
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
        self.last_processed_candle: dict[str, int] = {}
        self._last_boundary_check_hour: tuple[int, int] | None = None
        self._force_next_catchup = True  # Force first check after boot to ignore DB filters

    def _stream_names(self) -> list[str]:
        suffix = f"kline_{self.interval}"
        return [f"{s.lower()}@{suffix}" for s in self.symbols]

    async def connect(self) -> None:
        return None

    async def subscribe(self) -> None:
        return None

    def _seed_klines_sync(self, symbol: str, limit: int = 260) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(
            {
                "symbol": symbol.upper(),
                "interval": self.interval,
                "limit": int(limit),
            }
        )
        url = f"{self.rest_url}/fapi/v1/klines?{query}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        candles: list[dict[str, Any]] = []
        for k in payload:
            candles.append(
                {
                    "symbol": symbol.upper(),
                    "timestamp": str(int(k[6])),
                    "open_time_ms": int(k[0]),
                    "close_time_ms": int(k[6]),
                    "interval": self.interval,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "source_event": {"seed": True},
                }
            )
        return candles

    async def fetch_seed_klines(self, symbol: str, limit: int = 260) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._seed_klines_sync, symbol, limit)

    async def consume_forever(self, stop_after: int | None = None) -> None:
        try:
            import websockets
        except Exception as exc:
            raise RuntimeError("websockets package missing. Install websockets.") from exc

        self._running = True
        attempt = 0
        emitted = 0

        while self._running:
            streams = self._stream_names()
            query = urllib.parse.urlencode({"streams": "/".join(streams)})
            ws_url = f"{self.ws_url}?{query}"
            try:
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                    attempt = 0
                    print("[RECONNECT] websocket connected and subscribed")
                    async for raw in ws:
                        message = json.loads(raw)
                        event = message.get("data", message)
                        snapshot = await self.handle_kline_event(event)
                        if snapshot is None:
                            continue
                        await self.queue.put(snapshot)
                        emitted += 1
                        if stop_after is not None and emitted >= stop_after:
                            self._running = False
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                sleep_s = min(60, 2 ** min(attempt, 6))
                print(f"[RECONNECT] websocket error: {exc}. retry in {sleep_s}s")
                await asyncio.sleep(sleep_s)

    async def websocket_smoke_check(self, timeout_s: int = 20) -> dict[str, Any]:
        try:
            import websockets
        except Exception as exc:
            return {"ok": False, "error": f"websockets_missing: {exc}"}

        streams = self._stream_names()
        query = urllib.parse.urlencode({"streams": "/".join(streams[: min(3, len(streams))])})
        ws_url = f"{self.ws_url}?{query}"

        start = time.perf_counter()
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                message = json.loads(raw)
                event = message.get("data", message)
                symbol = str(event.get("s") or event.get("k", {}).get("s") or "")
                return {
                    "ok": True,
                    "elapsed_ms": elapsed_ms,
                    "symbol": symbol,
                    "event_type": event.get("e", "unknown"),
                }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def handle_kline_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize one kline payload and return closed-candle snapshot or None.

        Rules:
        - Process only interval == `self.interval`.
        - Process only when kline['x'] is True.
        - Drop duplicates using `self.last_processed_candle[symbol]`.
        """
        k = event.get("k", {})
        if k.get("i") != self.interval:
            return None
        if not bool(k.get("x", False)):
            return None

        symbol = str(event.get("s") or k.get("s") or "").upper()
        open_time = int(k.get("t", 0) or 0)
        if not symbol or open_time <= 0:
            return None

        last = self.last_processed_candle.get(symbol)
        if last is not None and open_time <= last:
            print(f"[DUPLICATE DROP] symbol={symbol} open_time={open_time}")
            return None

        self.last_processed_candle[symbol] = open_time
        return {
            "symbol": symbol,
            "timestamp": str(k.get("T")),
            "open_time_ms": open_time,
            "close_time_ms": int(k.get("T", 0) or 0),
            "interval": k.get("i"),
            "open": float(k.get("o", 0.0) or 0.0),
            "high": float(k.get("h", 0.0) or 0.0),
            "low": float(k.get("l", 0.0) or 0.0),
            "close": float(k.get("c", 0.0) or 0.0),
            "volume": float(k.get("v", 0.0) or 0.0),
            "source_event": event,
        }

    async def shutdown(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Generic boundary REST catch-up (heartbeat guard)
    # Supports any interval: 15m, 1h, 4h, etc.
    # ------------------------------------------------------------------

    @staticmethod
    def _interval_minutes(interval: str) -> int:
        """Parse Binance interval string to minutes."""
        units = {"m": 1, "h": 60, "d": 1440, "w": 10080}
        suffix = interval[-1]
        return int(interval[:-1]) * units.get(suffix, 60)

    def _compute_boundaries(self) -> list[tuple[int, int]]:
        """Return all (hour, minute) boundary tuples for the active interval."""
        step = self._interval_minutes(self.interval)
        boundaries = []
        for total_min in range(0, 1440, step):
            boundaries.append((total_min // 60, total_min % 60))
        return boundaries

    def _grace_seconds(self) -> int:
        """Scale grace period by interval: 45s for 15m, 120s for 4h."""
        mins = self._interval_minutes(self.interval)
        if mins <= 15:
            return 45
        elif mins <= 60:
            return 60
        return 120

    async def boundary_catchup_loop(self) -> None:
        """Periodically check if we crossed a candle boundary without a WS event."""
        poll_s = max(15, self._interval_minutes(self.interval) * 60 // 15)  # poll ~4x per interval
        poll_s = min(poll_s, 60)
        while self._running:
            try:
                await asyncio.sleep(poll_s)
                print(f"[HEARTBEAT] {len(self.symbols)} assets monitored | interval={self.interval} | queue={self.queue.qsize()}")
                await self._check_boundary_catchup()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[CATCHUP] error in boundary loop: {exc}")

    async def _check_boundary_catchup(self) -> None:
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        boundaries = self._compute_boundaries()
        grace = self._grace_seconds()

        # Find the most recent boundary we should have data for
        current_total = now.hour * 60 + now.minute
        boundary_hm: tuple[int, int] | None = None
        for h, m in sorted(boundaries, key=lambda x: x[0] * 60 + x[1], reverse=True):
            if current_total >= h * 60 + m:
                boundary_hm = (h, m)
                break
        if boundary_hm is None:
            # Wrapped from previous day
            boundary_hm = boundaries[-1] if boundaries else (0, 0)

        # Don't re-check the same boundary
        if boundary_hm == self._last_boundary_check_hour:
            return

        # Compute elapsed since boundary
        boundary_time = now.replace(hour=boundary_hm[0], minute=boundary_hm[1], second=0, microsecond=0)
        if boundary_time > now:
            boundary_time -= _dt.timedelta(days=1)
        elapsed = (now - boundary_time).total_seconds()

        if elapsed < grace:
            return  # too early, WS might still deliver

        # Mark as checked
        self._last_boundary_check_hour = boundary_hm
        boundary_ms = int(boundary_time.timestamp() * 1000)

        catchup_count = 0
        is_force = self._force_next_catchup
        self._force_next_catchup = False

        for symbol in self.symbols:
            last = self.last_processed_candle.get(symbol, 0)
            if not is_force and last >= boundary_ms:
                continue
            try:
                candles = await self.fetch_seed_klines(symbol, limit=2)
                for c in candles:
                    ot = int(c.get("open_time_ms", 0))
                    ct = int(c.get("close_time_ms", 0))
                    if ot > 0 and ct > 0 and ct <= int(now.timestamp() * 1000):
                        prev = self.last_processed_candle.get(symbol, 0)
                        if ot > prev:
                            self.last_processed_candle[symbol] = ot
                            c["source_event"] = {"catchup": True, "boundary": f"{boundary_hm[0]:02d}:{boundary_hm[1]:02d}"}
                            await self.queue.put(c)
                            catchup_count += 1
            except Exception as exc:
                print(f"[CATCHUP] REST pull failed for {symbol}: {exc}")

        if catchup_count > 0:
            print(f"[CATCHUP] injected {catchup_count} missed candles for boundary {boundary_hm[0]:02d}:{boundary_hm[1]:02d} UTC")


class QuantaAdapter:
    def __init__(
        self,
        config_path: str,
        telemetry: LiveTelemetryStore,
        config_override: dict | None = None,
        order_manager: "BinanceOrderManager | None" = None,
        active_interval: str = "4h",
    ) -> None:
        self.config_path = config_path
        self.telemetry = telemetry
        self._config_override = config_override or {}
        self._order_manager = order_manager
        self._active_interval = active_interval
        self._sim_for_parity = HistoricalSimulator(
            config_path=config_path,
            data_dir=str(_ROOT / "research" / "historical_data"),
            db_path=":memory:",
            config_override=self._config_override,
        )
        self._initial_balance = float(self._config_override.get("initial_balance", 10000.0))
        self._closes: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self._highs: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self._lows: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self._volumes: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self._notional: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        # Smoke-test bypass flags
        self._bypass_regime = bool(self._config_override.get("bypass_regime_filter", False))
        self._bypass_trend = bool(self._config_override.get("bypass_trend_filter", False))
        self._bypass_signal = bool(self._config_override.get("bypass_signal_trigger", False))
        if self._bypass_regime:
            print("[BYPASS] Regime filter DISABLED via config")
        if self._bypass_trend:
            print("[BYPASS] Trend filter DISABLED via config")
        if self._bypass_signal:
            print("[BYPASS] Signal trigger DISABLED via config (FORCING TRADES)")

    async def ingest_seed_candles(self, symbol: str, candles: list[dict[str, Any]]) -> None:
        for c in candles:
            self._append_bar(symbol, c)

    def _append_bar(self, symbol: str, snapshot: dict[str, Any]) -> None:
        close = float(snapshot.get("close", 0.0) or 0.0)
        high = float(snapshot.get("high", close) or close)
        low = float(snapshot.get("low", close) or close)
        volume = float(snapshot.get("volume", 0.0) or 0.0)
        self._closes[symbol].append(close)
        self._highs[symbol].append(high)
        self._lows[symbol].append(low)
        self._volumes[symbol].append(volume)
        self._notional[symbol].append(close * volume)

    async def evaluate_closed_candle(self, snapshot: dict[str, Any]) -> ApprovedTrade | None:
        symbol = str(snapshot["symbol"]).upper()
        self._append_bar(symbol, snapshot)
        row = await self.build_parity_row(snapshot)
        if row is None:
            return None

        snapshot_id = await self.telemetry.insert_market_snapshot_async(
            {
                **snapshot,
                "adx": row.get("adx"),
                "atr": row.get("atr"),
                "regime_features": {
                    "ema_fast": row.get("ema_fast"),
                    "ema_slow": row.get("ema_slow"),
                    "ema_trend": row.get("ema_trend"),
                    "atr_sma": row.get("atr_sma"),
                    "rsi": row.get("rsi"),
                },
            }
        )

        candidate = await self.run_signal_layer(row)
        if candidate is None:
            # Signal layer rejected — reason already printed by run_signal_layer
            return None

        candidate["snapshot_id"] = snapshot_id
        gate_result = await self.run_gate_layer(candidate)

        await self.telemetry.insert_signal_async(
            {
                "signal_id": candidate["signal_id"],
                "timestamp": candidate["timestamp"],
                "symbol": candidate["symbol"],
                "strategy_name": candidate["strategy_name"],
                "signal_side": candidate["side"],
                "raw_score": candidate["raw_ml_prob"],
                "priority_rank": 1,
                "snapshot_id": snapshot_id,
                "metadata": {
                    "threshold": candidate["effective_threshold"],
                    "adjusted_score": candidate["adjusted_score"],
                    "gate": gate_result,
                },
            }
        )

        await self.telemetry.insert_gate_evaluation_async(
            {
                "signal_id": candidate["signal_id"],
                "timestamp": candidate["timestamp"],
                "symbol": candidate["symbol"],
                "microstructure_regime": gate_result.get("regime"),
                "risk_pressure": gate_result.get("risk_pressure"),
                "ml_prob": candidate["raw_ml_prob"],
                "ml_adjusted": candidate["adjusted_score"],
                "threshold_applied": candidate["effective_threshold"],
                "final_decision": "EXECUTE" if candidate["accepted"] else "VETO",
                "veto_reason": None if candidate["accepted"] else candidate["veto_reason"],
                "details": {
                    "gate_reason": gate_result.get("reason"),
                    "gate_scores": gate_result.get("scores", {}),
                },
            }
        )

        if not candidate["accepted"]:
            veto = str(candidate.get("veto_reason", "unknown"))
            ml_adj = candidate.get("adjusted_score", 0)
            thr = candidate.get("effective_threshold", 0)
            side = candidate.get("side", "?")
            if "ml_threshold" in veto:
                reason_text = f"ML Confidence ({ml_adj:.4f}) below threshold ({thr:.4f})"
            else:
                reason_text = f"Regime Gate Veto ({veto})"
            print(f"[AUDIT] {symbol} | Signal: {side} | Status: REJECTED | Reason: {reason_text}")
            await self.telemetry.insert_rejection_async({
                "timestamp": candidate.get("timestamp"),
                "symbol": symbol,
                "side": side,
                "stage": "gate",
                "reason": reason_text,
                "details": {"ml_adjusted": ml_adj, "threshold": thr, "veto_reason": veto},
            })
            return None
        return await self.build_approved_trade(candidate)

    async def build_parity_row(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        symbol = str(snapshot["symbol"]).upper()
        closes = list(self._closes[symbol])
        highs = list(self._highs[symbol])
        lows = list(self._lows[symbol])
        if len(closes) < 220:
            return None

        atr_val = indicators.atr(highs, lows, closes, 14)
        adx_val = indicators.adx(highs, lows, closes, 14)
        rsi_val = indicators.rsi(closes, 14)
        ema_fast = indicators.ema(closes, 9)[-1]
        ema_slow = indicators.ema(closes, 24)[-1]
        ema_trend = indicators.ema(closes, 200)[-1]

        tr_values: list[float] = []
        for i in range(1, len(closes)):
            tr_values.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
        atr_sma = sum(tr_values[-14:]) / 14.0 if len(tr_values) >= 14 else atr_val

        return {
            "datetime_utc": str(snapshot.get("timestamp")),
            "symbol": symbol,
            "open": float(snapshot.get("open", 0.0) or 0.0),
            "high": float(snapshot.get("high", 0.0) or 0.0),
            "low": float(snapshot.get("low", 0.0) or 0.0),
            "close": float(snapshot.get("close", 0.0) or 0.0),
            "volume": float(snapshot.get("volume", 0.0) or 0.0),
            "open_time_ms": int(snapshot.get("open_time_ms", 0) or 0),
            "close_time_ms": int(snapshot.get("close_time_ms", 0) or 0),
            "atr": float(atr_val),
            "adx": float(adx_val),
            "rsi": float(rsi_val),
            "atr_sma": float(atr_sma),
            "ema_fast": float(ema_fast),
            "ema_slow": float(ema_slow),
            "ema_trend": float(ema_trend),
            "close_4h": float(snapshot.get("close", 0.0) or 0.0),
            "ema_trend_4h": float(ema_trend),
            "rsi_4h": float(rsi_val),
        }

    async def run_signal_layer(self, row: dict[str, Any]) -> dict[str, Any] | None:
        symbol = str(row.get("symbol", "?"))
        regime = classify_regime(row)
        routed = route_signal(regime)
        if routed != "expansion_engine":
            if self._bypass_regime:
                print(f"[BYPASS] {symbol} | Regime={regime} overridden to EXPANSION")
                regime = "EXPANSION"
            else:
                adx = float(row.get("adx", 0))
                print(f"[AUDIT] {symbol} | Signal: NONE | Status: FILTERED | Reason: Regime={regime}, routed={routed} (ADX={adx:.1f})")
                await self.telemetry.insert_rejection_async({
                    "timestamp": str(row.get("datetime_utc", "")),
                    "symbol": symbol, "side": None, "stage": "signal_regime",
                    "reason": f"Regime={regime}, routed to {routed} not expansion_engine",
                    "details": {"adx": adx, "regime": regime},
                })
                return None

        sig = expansion_engine.generate_signal(row)
        if int(sig.get("signal", 0) or 0) == 0:
            if self._bypass_signal:
                side_val = 1 if row['close'] > row['open'] else -1
                atr = row['atr']
                close = row['close']
                print(f"[BYPASS] {symbol} | Forcing signal {side_val} (no breakout detected)")
                sig = {
                    "signal": side_val,
                    "sl": close - (atr * 1.2) if side_val == 1 else close + (atr * 1.2),
                    "tp1": close + (atr * 1.5) if side_val == 1 else close - (atr * 1.5),
                    "tp2": 0,
                    "strategy": "expansion_engine_BYPASS"
                }
            else:
                adx = float(row.get("adx", 0))
                print(f"[AUDIT] {symbol} | Signal: NONE | Status: FILTERED | Reason: No breakout candle (ADX={adx:.1f})")
                await self.telemetry.insert_rejection_async({
                    "timestamp": str(row.get("datetime_utc", "")),
                    "symbol": symbol, "side": None, "stage": "signal_breakout",
                    "reason": f"No breakout candle detected (ADX={adx:.1f})",
                    "details": {"adx": adx},
                })
                return None

        side = "BUY" if int(sig["signal"]) > 0 else "SELL"
        if not self._bypass_trend:
            bull_4h = (row["close_4h"] > row["ema_trend_4h"]) and (row["rsi_4h"] < 70)
            bear_4h = (row["close_4h"] < row["ema_trend_4h"]) and (row["rsi_4h"] > 30)
            if side == "BUY" and not bull_4h:
                print(f"[AUDIT] {symbol} | Signal: LONG | Status: FILTERED | Reason: Trend filter failed (close < EMA or RSI > 70)")
                await self.telemetry.insert_rejection_async({
                    "timestamp": str(row.get("datetime_utc", "")),
                    "symbol": symbol, "side": "BUY", "stage": "signal_trend",
                    "reason": "Trend filter: close < EMA_trend or RSI > 70",
                })
                return None
            if side == "SELL" and not bear_4h:
                print(f"[AUDIT] {symbol} | Signal: SHORT | Status: FILTERED | Reason: Trend filter failed (close > EMA or RSI < 30)")
                await self.telemetry.insert_rejection_async({
                    "timestamp": str(row.get("datetime_utc", "")),
                    "symbol": symbol, "side": "SELL", "stage": "signal_trend",
                    "reason": "Trend filter: close > EMA_trend or RSI < 30",
                })
                return None
        else:
            print(f"[BYPASS] {symbol} | Signal: {side} | Trend filter skipped")

        row = dict(row)
        row["signal"] = 1 if side == "BUY" else -1
        row["target_sl"] = float(sig["sl"])
        row["target_tp1"] = float(sig["tp1"])
        row["target_tp2"] = float(sig.get("tp2", 0.0) or 0.0)
        row["strategy_used"] = sig.get("strategy", "expansion_engine")
        row["regime"] = regime
        row["exec_tf"] = self._active_interval

        symbol = str(row["symbol"])
        current_notional = float(row["close"] * row.get("volume", 0.0))
        hist_notional = list(self._notional[symbol])
        baseline = sum(hist_notional[-20:-1]) / max(1, len(hist_notional[-20:-1])) if len(hist_notional) > 1 else 0.0
        signal_seed = f"{symbol}|{row['open_time_ms']}|{side}|{row['strategy_used']}"
        signal_id = hashlib.sha1(signal_seed.encode("utf-8")).hexdigest()

        return {
            "signal_id": signal_id,
            "timestamp": str(row["datetime_utc"]),
            "symbol": symbol,
            "side": side,
            "strategy_name": str(row["strategy_used"]),
            "expected_price": float(row["close"]),
            "row": row,
            "ts": pd.Timestamp(int(row["open_time_ms"]), unit="ms", tz="UTC"),
            "current_notional": current_notional,
            "baseline_average_notional": float(baseline),
        }

    async def run_gate_layer(self, candidate: dict[str, Any]) -> dict[str, Any]:
        row = candidate["row"]
        if str(candidate.get("strategy_name")) == "mean_reversion_engine":
            candidate["raw_ml_prob"] = 1.0
            candidate["adjusted_score"] = 1.0
            candidate["effective_threshold"] = 0.0
            candidate["accepted"] = True
            candidate["veto_reason"] = None
            print("[ML VETO] ML Veto Bypassed for Mean Reversion Strategy.")
            return {
                "risk_pressure": 0.0,
                "regime": "SAFE",
                "trade_allowed": True,
                "ml_penalty": 0.0,
                "reason": "ml_veto_bypassed_mean_reversion",
                "scores": {},
            }

        raw_ml_prob = float(predict_trade_quality(self._sim_for_parity._build_ml_features(candidate)))
        adjusted_score = float(self._sim_for_parity._amplify_ml_score(raw_ml_prob))

        adx_val = float(row.get("adx", 0.0) or 0.0)
        atr_ratio_val = float(row.get("atr", 0.0) or 0.0) / float(row.get("close", 1.0) or 1.0)
        base_threshold = float(self._sim_for_parity._get_regime_threshold(adx_val, atr_ratio_val))
        baseline_offset = float(self._sim_for_parity.ml_threshold) - float(ML_THRESHOLD)
        effective_threshold = max(0.0, min(1.0, base_threshold + baseline_offset))

        gate = self._sim_for_parity._evaluate_market_regime_gate(candidate)
        if not bool(gate.get("trade_allowed", True)):
            candidate["raw_ml_prob"] = raw_ml_prob
            candidate["adjusted_score"] = adjusted_score
            candidate["effective_threshold"] = effective_threshold
            candidate["accepted"] = False
            candidate["veto_reason"] = str(gate.get("reason", "gate_veto"))
            return gate

        warning_penalty = float(gate.get("ml_penalty", 0.0) or 0.0)
        if warning_penalty > 0.0:
            adjusted_score = max(0.0, adjusted_score - warning_penalty)

        candidate["raw_ml_prob"] = raw_ml_prob
        candidate["adjusted_score"] = adjusted_score
        candidate["effective_threshold"] = effective_threshold
        if adjusted_score < effective_threshold:
            candidate["accepted"] = False
            candidate["veto_reason"] = "ml_threshold_veto"
        else:
            candidate["accepted"] = True
            candidate["veto_reason"] = None
        return gate

    async def _fetch_live_balance(self) -> float | None:
        """Fetch live wallet balance from exchange. Returns None on failure (fail-closed)."""
        if self._order_manager is None:
            print("[WARNING] No order manager wired. Cannot fetch live balance.")
            return None
        try:
            return await self._order_manager.fetch_wallet_balance()
        except Exception as exc:
            print(f"[WARNING] Live balance fetch exception: {exc}")
            return None

    async def build_approved_trade(self, candidate: dict[str, Any]) -> ApprovedTrade | None:
        row = candidate["row"]
        entry_price = float(candidate["expected_price"])
        sl = float(row["target_sl"])
        tp1 = float(row["target_tp1"])
        symbol = str(candidate["symbol"])

        live_balance = await self._fetch_live_balance()
        if live_balance is None:
            print(
                f"[WARNING] Cannot fetch live balance from exchange. "
                f"Trade sizing aborted for safety. symbol={symbol}"
            )
            return None

        risk_amount = live_balance * self._sim_for_parity.risk_per_trade
        max_notional = live_balance * self._sim_for_parity.max_notional_mult

        price_risk = abs(entry_price - sl)
        if price_risk <= 0:
            quantity = 0.0
        else:
            quantity = risk_amount / price_risk
            if quantity * entry_price > max_notional and entry_price > 0:
                quantity = max_notional / entry_price

        print(
            f"[LIVE BALANCE] {symbol} | wallet=${live_balance:,.2f} | "
            f"risk_amount=${risk_amount:,.2f} | max_notional=${max_notional:,.2f} | "
            f"qty={quantity:.4f} | entry=${entry_price:,.4f}"
        )

        return ApprovedTrade(
            signal_id=str(candidate["signal_id"]),
            timestamp=str(candidate["timestamp"]),
            symbol=symbol,
            side=str(candidate["side"]),
            expected_price=entry_price,
            quantity=float(quantity),
            tp_price=tp1,
            sl_price=sl,
            strategy_name=str(candidate.get("strategy_name", "expansion_engine")),
            metadata={
                "raw_ml_prob": candidate.get("raw_ml_prob"),
                "adjusted_score": candidate.get("adjusted_score"),
                "threshold": candidate.get("effective_threshold"),
                "open_time_ms": row.get("open_time_ms"),
                "close_time_ms": row.get("close_time_ms"),
                "live_balance": live_balance,
            },
        )


class BinanceOrderManager:
    def __init__(self, api_key: str, api_secret: str, telemetry: LiveTelemetryStore, max_concurrent: int = 10, trailing_callback_rate: float = 2.0) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.telemetry = telemetry
        self.base_url = "https://testnet.binancefuture.com"
        self._cached_balance: float | None = None
        self._balance_cache_ts: float = 0.0
        self._balance_cache_ttl: float = 10.0  # seconds
        # Price/quantity precision caches (populated on first use)
        self._price_precision: dict[str, int] = {}
        self._qty_precision: dict[str, int] = {}
        self._exchange_info_loaded = False
        # Margin guard
        self._max_concurrent = max_concurrent
        self._blacklisted_symbols: set[str] = set()
        # V2 trailing stop
        self._trailing_callback_rate = trailing_callback_rate

    async def fetch_wallet_balance(self) -> float | None:
        """Fetch live Futures wallet balance. Returns None on failure (fail-closed)."""
        now = time.time()
        if self._cached_balance is not None and (now - self._balance_cache_ts) < self._balance_cache_ttl:
            return self._cached_balance
        try:
            data = await self._signed_request("GET", "/fapi/v2/account", {})
            if isinstance(data, dict):
                raw = data.get("totalWalletBalance")
                if raw is not None:
                    balance = float(raw)
                    self._cached_balance = balance
                    self._balance_cache_ts = now
                    if getattr(self, "_peak_balance", None) is None or balance > self._peak_balance:
                        self._peak_balance = balance
                    return balance
        except Exception as exc:
            print(f"[WARNING] Binance balance API error: {exc}")
        # Return last known good value if available, else None
        return self._cached_balance

    def _format_qty(self, qty: float, symbol: str = "") -> str:
        prec = self._qty_precision.get(symbol.upper(), 3)
        return f"{max(0.0, qty):.{prec}f}".rstrip("0").rstrip(".") or "0"

    def _format_price(self, symbol: str, price: float) -> str:
        prec = self._price_precision.get(symbol.upper(), 2)
        return f"{price:.{prec}f}"

    def _fetch_exchange_info_sync(self) -> None:
        """Fetch pricePrecision and quantityPrecision for all futures symbols."""
        if self._exchange_info_loaded:
            return
        try:
            url = f"{self.base_url}/fapi/v1/exchangeInfo"
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for s in data.get("symbols", []):
                sym = s.get("symbol", "").upper()
                self._price_precision[sym] = int(s.get("pricePrecision", 2))
                self._qty_precision[sym] = int(s.get("quantityPrecision", 3))
            self._exchange_info_loaded = True
            print(f"[EXCHANGE INFO] Loaded precision for {len(self._price_precision)} symbols")
        except Exception as exc:
            print(f"[WARNING] Failed to fetch exchangeInfo: {exc}. Using default precision.")

    async def _ensure_exchange_info(self) -> None:
        if not self._exchange_info_loaded:
            await asyncio.to_thread(self._fetch_exchange_info_sync)

    def _signed_request_sync(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        query = urllib.parse.urlencode(params, doseq=True)
        signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{self.base_url}{path}?{query}&signature={signature}"
        req = urllib.request.Request(url=url, method=method.upper())
        req.add_header("X-MBX-APIKEY", self.api_key)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            for attempt in range(2):
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        payload = resp.read().decode("utf-8")
                        return json.loads(payload) if payload else {}
                except urllib.error.HTTPError as err:
                    if err.code == 408 and attempt == 0:
                        print(f"[RETRY] HTTP 408 Timeout on {path}. Retrying in 1s...")
                        time.sleep(1)
                        continue
                    raise
        except urllib.error.HTTPError as err:
            error_body = ""
            try:
                error_body = err.read().decode("utf-8")
            except Exception:
                pass
            print(f"[BINANCE ERROR] {method} {path} | HTTP {err.code} | {error_body}")
            raise RuntimeError(f"Binance API {err.code}: {error_body}") from err

    async def _signed_request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        return await asyncio.to_thread(self._signed_request_sync, method, path, params)

    async def place_market_entry(self, trade: ApprovedTrade) -> dict[str, Any]:
        await self._ensure_exchange_info()
        params = {
            "symbol": trade.symbol,
            "side": "BUY" if trade.side.upper() == "BUY" else "SELL",
            "type": "MARKET",
            "quantity": self._format_qty(trade.quantity, trade.symbol),
            "newClientOrderId": f"quanta-{trade.signal_id[:18]}",
        }
        return await self._signed_request("POST", "/fapi/v1/order", params)

    async def place_tp_sl_orders(self, trade: ApprovedTrade) -> list[dict[str, Any]]:
        close_side = "SELL" if trade.side.upper() == "BUY" else "BUY"
        results: list[dict[str, Any]] = []

        # Take Profit — V2 Trailing Stop via Algo Order API
        try:
            tp = await self._signed_request(
                "POST",
                "/fapi/v1/algoOrder",
                {
                    "algoType": "CONDITIONAL",
                    "symbol": trade.symbol,
                    "side": close_side,
                    "type": "TRAILING_STOP_MARKET",
                    "quantity": self._format_qty(trade.quantity, trade.symbol),
                    "activationPrice": self._format_price(trade.symbol, trade.tp_price),
                    "callbackRate": str(self._trailing_callback_rate),
                    "workingType": "MARK_PRICE",
                    "reduceOnly": "true",
                    "newClientOrderId": f"quanta-ts-{trade.signal_id[:14]}",
                },
            )
            results.append(tp)
            print(
                f"[BRACKET] {trade.symbol} Trailing Stop placed at "
                f"{self._format_price(trade.symbol, trade.tp_price)} "
                f"(callback={self._trailing_callback_rate}%)"
            )
        except Exception as exc:
            print(f"[WARNING] {trade.symbol} Trailing Stop FAILED: {exc}")
            results.append({"error": str(exc), "type": "TS"})

        # Stop Loss — via Algo Order API (reduceOnly to prevent reverse)
        try:
            sl = await self._signed_request(
                "POST",
                "/fapi/v1/algoOrder",
                {
                    "algoType": "CONDITIONAL",
                    "symbol": trade.symbol,
                    "side": close_side,
                    "type": "STOP_MARKET",
                    "quantity": self._format_qty(trade.quantity, trade.symbol),
                    "triggerPrice": self._format_price(trade.symbol, trade.sl_price),
                    "workingType": "MARK_PRICE",
                    "reduceOnly": "true",
                    "newClientOrderId": f"quanta-sl-{trade.signal_id[:14]}",
                },
            )
            results.append(sl)
            print(f"[BRACKET] {trade.symbol} SL placed at {self._format_price(trade.symbol, trade.sl_price)}")
        except Exception as exc:
            print(f"[WARNING] {trade.symbol} SL order FAILED: {exc}")
            results.append({"error": str(exc), "type": "SL"})

        return results

    async def _count_active_positions(self) -> int:
        """Count non-zero positions on the exchange."""
        try:
            all_positions = await self._signed_request("GET", "/fapi/v2/positionRisk", {})
            if not isinstance(all_positions, list):
                return 0
            return sum(1 for p in all_positions if float(p.get("positionAmt", 0)) != 0.0)
        except Exception as exc:
            print(f"[WARNING] Failed to count active positions: {exc}")
            return 0

    async def execute_trade(self, trade: ApprovedTrade) -> dict[str, Any] | None:
        # Auto-blacklist check
        if trade.symbol in self._blacklisted_symbols:
            print(f"[BLACKLIST] {trade.symbol} | Skipping — symbol blacklisted")
            return None

        if await self.telemetry.has_execution_for_signal_async(trade.signal_id):
            print(f"[DUPLICATE DROP] signal already executed: {trade.signal_id}")
            return None

        # Pre-flight margin guard
        active_count = await self._count_active_positions()
        if active_count >= self._max_concurrent:
            print(
                f"[MARGIN GUARD] {trade.symbol} | Skipped — "
                f"{active_count} active positions (max={self._max_concurrent})"
            )
            return None

        start = time.perf_counter()

        # Step 1: Entry order
        try:
            entry = await self.place_market_entry(trade)
            print(
                f"[EXECUTION] {trade.symbol} | Entry {trade.side} | "
                f"status={entry.get('status', 'UNKNOWN')} | "
                f"orderId={entry.get('orderId', 'N/A')} | "
                f"avgPrice={entry.get('avgPrice', 'N/A')}"
            )
        except Exception as exc:
            err_str = str(exc)
            # Auto-blacklist delisted symbols
            if "-4140" in err_str:
                self._blacklisted_symbols.add(trade.symbol)
                print(f"[BLACKLIST] {trade.symbol} | Symbol not available on Testnet. Skipping permanently.")
            else:
                print(f"[EXECUTION FAILED] {trade.symbol} | Entry {trade.side} | {exc}")
            return None

        # Step 2 & 3: TP + SL bracket orders
        try:
            exits = await self.place_tp_sl_orders(trade)
        except Exception as exc:
            print(f"[WARNING] {trade.symbol} | Bracket orders failed: {exc}")
            exits = [{"error": str(exc)}]

        latency_ms = int((time.perf_counter() - start) * 1000)

        expected_price = float(trade.expected_price)
        actual_fill_price = float(entry.get("avgPrice", expected_price) or expected_price)
        slippage_pct = ((abs(actual_fill_price - expected_price)) / expected_price * 100.0) if expected_price else 0.0

        if latency_ms > 2000:
            send_alert_async(
                f"Execution latency spike detected: {latency_ms}ms",
                level="WARNING",
                details={"Symbol": trade.symbol, "Latency": f"{latency_ms}ms"}
            )
        if slippage_pct > 0.2:
            send_alert_async(
                f"Slippage spike detected: {slippage_pct:.3f}%",
                level="WARNING",
                details={"Symbol": trade.symbol, "Slippage": f"{slippage_pct:.3f}%"}
            )

        execution_payload = {
            "execution_id": f"exec-{uuid.uuid4().hex}",
            "signal_id": trade.signal_id,
            "timestamp": trade.timestamp,
            "symbol": trade.symbol,
            "side": trade.side,
            "expected_price": expected_price,
            "actual_fill_price": actual_fill_price,
            "slippage_pct": slippage_pct,
            "latency_ms": latency_ms,
            "exchange_order_id": entry.get("orderId"),
            "order_status": entry.get("status", "UNKNOWN"),
            "raw_exchange": {
                "entry": entry,
                "exits": exits,
            },
        }
        await self.telemetry.insert_execution_async(execution_payload)
        return execution_payload

    async def reconcile_exchange_state(self) -> None:
        try:
            open_orders = await self._signed_request("GET", "/fapi/v1/openOrders", {})
            all_positions = await self._signed_request("GET", "/fapi/v2/positionRisk", {})
            # Filter out zero-size positions (Binance returns all symbols)
            active_positions = [
                p for p in (all_positions if isinstance(all_positions, list) else [])
                if float(p.get("positionAmt", 0)) != 0.0
            ]
            active_symbols = {str(p.get("symbol", "")) for p in active_positions}
            print(
                f"[RECONCILIATION] exchange_open_orders={len(open_orders) if isinstance(open_orders, list) else 0} "
                f"exchange_active_positions={len(active_positions)}"
            )

            # Cancel orphaned algo orders for symbols with no active position
            orphan_count = 0
            if isinstance(open_orders, list):
                for order in open_orders:
                    sym = str(order.get("symbol", ""))
                    if sym and sym not in active_symbols:
                        try:
                            algo_id = order.get("algoId") or order.get("orderId")
                            if algo_id:
                                await self._signed_request("DELETE", "/fapi/v1/algoOrder", {
                                    "algoOrderId": algo_id,
                                    "symbol": sym,
                                })
                                orphan_count += 1
                        except Exception as cancel_exc:
                            print(f"[ORPHAN CLEANUP] Failed to cancel order for {sym}: {cancel_exc}")
            if orphan_count > 0:
                print(f"[ORPHAN CLEANUP] Cancelled {orphan_count} orphaned order(s) on boot")
        except Exception as exc:
            print(f"[RECONCILIATION] failed: {exc}")

    async def _orphan_sweeper_loop(self, interval_s: int = 900) -> None:
        """Background task: cancel algo orders for symbols with zero position every 15 min."""
        while True:
            try:
                await asyncio.sleep(interval_s)
                all_positions = await self._signed_request("GET", "/fapi/v2/positionRisk", {})
                if not isinstance(all_positions, list):
                    continue
                active_symbols = {
                    str(p.get("symbol", ""))
                    for p in all_positions
                    if float(p.get("positionAmt", 0)) != 0.0
                }

                # Fetch open algo orders
                open_orders = await self._signed_request("GET", "/fapi/v1/openOrders", {})
                if not isinstance(open_orders, list):
                    continue

                orphan_count = 0
                for order in open_orders:
                    sym = str(order.get("symbol", ""))
                    if sym and sym not in active_symbols:
                        try:
                            algo_id = order.get("algoId") or order.get("orderId")
                            if algo_id:
                                await self._signed_request("DELETE", "/fapi/v1/algoOrder", {
                                    "algoOrderId": algo_id,
                                    "symbol": sym,
                                })
                                orphan_count += 1
                        except Exception as cancel_exc:
                            print(f"[ORPHAN SWEEP] Failed to cancel order for {sym}: {cancel_exc}")

                if orphan_count > 0:
                    print(f"[ORPHAN SWEEP] Cancelled {orphan_count} orphaned order(s)")

                # RISK LAYER: Background Monitor
                # 1. Max positions
                if len(active_symbols) > self._max_concurrent:
                    send_alert_async(f"Risk Breach: Active positions ({len(active_symbols)}) exceeds max ({self._max_concurrent})", level="CRITICAL")
                
                # 2. Drawdown %
                if getattr(self, "_peak_balance", None) and self._cached_balance:
                    dd_pct = (self._peak_balance - self._cached_balance) / self._peak_balance * 100.0
                    if dd_pct >= 10.0:
                        send_alert_async(f"Risk Breach: Live Drawdown hit {dd_pct:.2f}%", level="CRITICAL", details={"Peak": self._peak_balance, "Current": self._cached_balance})

                # 3. Consecutive Losses
                income = await self._signed_request("GET", "/fapi/v1/income", {"incomeType": "REALIZED_PNL", "limit": 20})
                if isinstance(income, list):
                    consec_losses = 0
                    for event in reversed(income):
                        if float(event.get("income", 0)) < 0:
                            consec_losses += 1
                        else:
                            break
                    if consec_losses >= 5:
                        send_alert_async(f"Risk Breach: {consec_losses} consecutive realized losses detected", level="CRITICAL")

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[ORPHAN SWEEP] Error during sweep: {exc}")


class LiveOrchestrator:
    def __init__(
        self,
        config_path: str,
        telemetry: LiveTelemetryStore,
        streamer: BinanceDataStreamer,
        adapter: QuantaAdapter,
        order_manager: BinanceOrderManager,
    ) -> None:
        self.config_path = config_path
        self.telemetry = telemetry
        self.streamer = streamer
        self.adapter = adapter
        self.order_manager = order_manager
        self._running = False
        self._batch_tracker: dict[int, dict[str, Any]] = {}  # open_time_ms -> {count, signals, top_adx, ...}

    @classmethod
    def from_env(cls, env_path: str = ".env", smoke_config_path: str | None = None) -> "LiveOrchestrator":
        load_dotenv(env_path)
        api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
        api_secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
        if not api_key or not api_secret:
            raise RuntimeError("Missing BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET")

        config_override: dict[str, Any] = {}
        if smoke_config_path:
            with open(smoke_config_path, "r", encoding="utf-8") as f:
                config_override = json.load(f)
            label = config_override.get("label", "CUSTOM")
            balance = config_override.get("initial_balance", "default")
            risk = config_override.get("risk_per_trade", "default")
            ml_thr = config_override.get("ml_confidence_threshold", "default")
            interval = config_override.get("interval", "4h")
            print(f"[SMOKE CONFIG] Loaded: {label} | balance=${balance} | risk={risk} | ml_threshold={ml_thr} | interval={interval}")

        active_interval = config_override.get("interval", "4h")

        config_path = str(_ROOT / "runtime" / "config" / "strategy_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        symbols = cfg.get("matrix", {}).get("watchlist", [])

        telemetry = LiveTelemetryStore(default_db_path())
        telemetry.initialize()
        order_manager = BinanceOrderManager(
            api_key=api_key, api_secret=api_secret, telemetry=telemetry,
            max_concurrent=int(config_override.get("max_concurrent", 10)),
            trailing_callback_rate=float(config_override.get("trailing_callback_rate", 2.0)),
        )
        streamer = BinanceDataStreamer(symbols=symbols, interval=active_interval)
        adapter = QuantaAdapter(
            config_path=config_path,
            telemetry=telemetry,
            config_override=config_override,
            order_manager=order_manager,
            active_interval=active_interval,
        )
        return cls(config_path, telemetry, streamer, adapter, order_manager)

    async def _seed_histories(self) -> None:
        for symbol in self.streamer.symbols:
            try:
                candles = await self.streamer.fetch_seed_klines(symbol, limit=260)
                await self.adapter.ingest_seed_candles(symbol, candles)
            except Exception as exc:
                print(f"[RECONNECT] seed load failed for {symbol}: {exc}")

    async def run(self, stop_after: int | None = None) -> None:
        self._running = True
        await self.telemetry.initialize_async()
        self.streamer.last_processed_candle = await self.telemetry.load_last_processed_open_times_async(self.streamer.interval)
        await self._seed_histories()
        await self.order_manager.reconcile_exchange_state()

        # Immediate one-time catchup trigger
        await self.streamer._check_boundary_catchup()

        consumer_task = asyncio.create_task(self._consume_queue())
        catchup_task = asyncio.create_task(self.streamer.boundary_catchup_loop())
        sweeper_task = asyncio.create_task(self.order_manager._orphan_sweeper_loop())
        try:
            await self.streamer.consume_forever(stop_after=stop_after)
        finally:
            self._running = False
            consumer_task.cancel()
            catchup_task.cancel()
            sweeper_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass
            try:
                await catchup_task
            except asyncio.CancelledError:
                pass
            try:
                await sweeper_task
            except asyncio.CancelledError:
                pass

    async def _consume_queue(self) -> None:
        while self._running:
            snapshot = await self.streamer.queue.get()
            try:
                await self.handle_closed_candle(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                symbol = snapshot.get("symbol", "UNKNOWN")
                err_msg = f"Consumer loop crash on {symbol}: {exc}"
                print(f"[CRIT] {err_msg}")
                send_alert_async(err_msg, level="ERROR", details={"Symbol": symbol, "Exception": str(exc)})
                traceback.print_exc()
            finally:
                self.streamer.queue.task_done()

    async def handle_closed_candle(self, snapshot: dict[str, Any]) -> None:
        symbol = str(snapshot.get("symbol", ""))
        interval = str(snapshot.get("interval", "4h"))
        open_time_ms = int(snapshot.get("open_time_ms", 0) or 0)
        close_time_ms = int(snapshot.get("close_time_ms", 0) or 0)

        claimed = await self.telemetry.claim_candle_processing_async(
            symbol=symbol,
            interval=interval,
            open_time_ms=open_time_ms,
            close_time_ms=close_time_ms,
        )
        if not claimed:
            print(f"[DUPLICATE DROP] already processed in DB symbol={symbol} open_time={open_time_ms}")
            return

        # Track batch for [PULSE] summary
        if open_time_ms not in self._batch_tracker:
            self._batch_tracker[open_time_ms] = {
                "count": 0, "signals": 0, "top_adx_symbol": "", "top_adx": 0.0,
                "total_symbols": len(self.streamer.symbols),
            }
        batch = self._batch_tracker[open_time_ms]
        batch["count"] += 1

        # Track top ADX across all symbols for this batch
        row = await self.adapter.build_parity_row(snapshot)
        if row is not None:
            adx_val = float(row.get("adx", 0) or 0)
            if adx_val > batch["top_adx"]:
                batch["top_adx"] = adx_val
                batch["top_adx_symbol"] = symbol

        approved = await self.adapter.evaluate_closed_candle(snapshot)
        if approved is not None:
            batch["signals"] += 1
            await self.order_manager.execute_trade(approved)

        # If batch is ~complete, emit [PULSE]
        if batch["count"] >= batch["total_symbols"]:
            if batch["signals"] == 0:
                print(
                    f'[PULSE] {batch["count"]} assets evaluated. Zero signals. '
                    f'Market chopping. Top ADX: {batch["top_adx_symbol"]} ({batch["top_adx"]:.1f}).'
                )
            else:
                print(
                    f'[PULSE] {batch["count"]} assets evaluated. '
                    f'{batch["signals"]} signal(s) generated. '
                    f'Top ADX: {batch["top_adx_symbol"]} ({batch["top_adx"]:.1f}).'
                )
            del self._batch_tracker[open_time_ms]

    async def run_websocket_smoke_check(self, timeout_s: int = 20) -> dict[str, Any]:
        return await self.streamer.websocket_smoke_check(timeout_s=timeout_s)

    async def shutdown(self) -> None:
        self._running = False
        await self.streamer.shutdown()


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Quanta Live Orchestrator")
    parser.add_argument("--config", default=None, help="Path to smoke test config JSON override")
    args = parser.parse_args()
    orchestrator = LiveOrchestrator.from_env(smoke_config_path=args.config)
    try:
        await orchestrator.run()
    finally:
        await orchestrator.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
