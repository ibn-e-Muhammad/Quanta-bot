from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from market_data.src import indicators
from ml.model_inference import predict_trade_quality
from production.src.live_telemetry import LiveTelemetryStore, default_db_path
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
        self._running = False

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


class QuantaAdapter:
    def __init__(self, config_path: str, telemetry: LiveTelemetryStore) -> None:
        self.config_path = config_path
        self.telemetry = telemetry
        self._sim_for_parity = HistoricalSimulator(
            config_path=config_path,
            data_dir=str(_ROOT / "research" / "historical_data"),
            db_path=":memory:",
            config_override={},
        )
        self._initial_balance = 10000.0
        self._closes: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self._highs: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self._lows: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self._volumes: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self._notional: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))

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
        regime = classify_regime(row)
        if route_signal(regime) != "expansion_engine":
            return None

        sig = expansion_engine.generate_signal(row)
        if int(sig.get("signal", 0) or 0) == 0:
            return None

        side = "BUY" if int(sig["signal"]) > 0 else "SELL"
        bull_4h = (row["close_4h"] > row["ema_trend_4h"]) and (row["rsi_4h"] < 70)
        bear_4h = (row["close_4h"] < row["ema_trend_4h"]) and (row["rsi_4h"] > 30)
        if side == "BUY" and not bull_4h:
            return None
        if side == "SELL" and not bear_4h:
            return None

        row = dict(row)
        row["signal"] = 1 if side == "BUY" else -1
        row["target_sl"] = float(sig["sl"])
        row["target_tp1"] = float(sig["tp1"])
        row["target_tp2"] = float(sig.get("tp2", 0.0) or 0.0)
        row["strategy_used"] = sig.get("strategy", "expansion_engine")
        row["regime"] = regime
        row["exec_tf"] = "4h"

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
            "current_notional": current_notional,
            "baseline_average_notional": float(baseline),
        }

    async def run_gate_layer(self, candidate: dict[str, Any]) -> dict[str, Any]:
        row = candidate["row"]
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

    async def build_approved_trade(self, candidate: dict[str, Any]) -> ApprovedTrade:
        row = candidate["row"]
        entry_price = float(candidate["expected_price"])
        sl = float(row["target_sl"])
        tp1 = float(row["target_tp1"])

        risk_amount = self._initial_balance * RISK_PER_TRADE
        price_risk = abs(entry_price - sl)
        if price_risk <= 0:
            quantity = 0.0
        else:
            quantity = risk_amount / price_risk
            max_notional = self._initial_balance * MAX_NOTIONAL_MULT
            if quantity * entry_price > max_notional and entry_price > 0:
                quantity = max_notional / entry_price

        return ApprovedTrade(
            signal_id=str(candidate["signal_id"]),
            timestamp=str(candidate["timestamp"]),
            symbol=str(candidate["symbol"]),
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
            },
        )


class BinanceOrderManager:
    def __init__(self, api_key: str, api_secret: str, telemetry: LiveTelemetryStore) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.telemetry = telemetry
        self.base_url = "https://testnet.binancefuture.com"

    def _format_qty(self, qty: float) -> str:
        return f"{max(0.0, qty):.3f}".rstrip("0").rstrip(".") or "0"

    def _signed_request_sync(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        query = urllib.parse.urlencode(params, doseq=True)
        signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{self.base_url}{path}?{query}&signature={signature}"
        req = urllib.request.Request(url=url, method=method.upper())
        req.add_header("X-MBX-APIKEY", self.api_key)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}

    async def _signed_request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        return await asyncio.to_thread(self._signed_request_sync, method, path, params)

    async def place_market_entry(self, trade: ApprovedTrade) -> dict[str, Any]:
        params = {
            "symbol": trade.symbol,
            "side": "BUY" if trade.side.upper() == "BUY" else "SELL",
            "type": "MARKET",
            "quantity": self._format_qty(trade.quantity),
            "newClientOrderId": f"quanta-{trade.signal_id[:18]}",
        }
        return await self._signed_request("POST", "/fapi/v1/order", params)

    async def place_tp_sl_orders(self, trade: ApprovedTrade) -> list[dict[str, Any]]:
        close_side = "SELL" if trade.side.upper() == "BUY" else "BUY"
        tp = await self._signed_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": trade.symbol,
                "side": close_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": f"{trade.tp_price:.8f}",
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "newClientOrderId": f"quanta-tp-{trade.signal_id[:14]}",
            },
        )
        sl = await self._signed_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": trade.symbol,
                "side": close_side,
                "type": "STOP_MARKET",
                "stopPrice": f"{trade.sl_price:.8f}",
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "newClientOrderId": f"quanta-sl-{trade.signal_id[:14]}",
            },
        )
        return [tp, sl]

    async def execute_trade(self, trade: ApprovedTrade) -> dict[str, Any] | None:
        if await self.telemetry.has_execution_for_signal_async(trade.signal_id):
            print(f"[DUPLICATE DROP] signal already executed: {trade.signal_id}")
            return None

        start = time.perf_counter()
        entry = await self.place_market_entry(trade)
        exits = await self.place_tp_sl_orders(trade)
        latency_ms = int((time.perf_counter() - start) * 1000)

        expected_price = float(trade.expected_price)
        actual_fill_price = float(entry.get("avgPrice", expected_price) or expected_price)
        slippage_pct = ((actual_fill_price - expected_price) / expected_price * 100.0) if expected_price else 0.0

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
            positions = await self._signed_request("GET", "/fapi/v2/positionRisk", {})
            print(
                f"[RECONCILIATION] exchange_open_orders={len(open_orders) if isinstance(open_orders, list) else 0} "
                f"exchange_positions={len(positions) if isinstance(positions, list) else 0}"
            )
        except Exception as exc:
            print(f"[RECONCILIATION] failed: {exc}")


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

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "LiveOrchestrator":
        load_dotenv(env_path)
        api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
        api_secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
        if not api_key or not api_secret:
            raise RuntimeError("Missing BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET")

        config_path = str(_ROOT / "runtime" / "config" / "strategy_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        symbols = cfg.get("matrix", {}).get("watchlist", [])

        telemetry = LiveTelemetryStore(default_db_path())
        telemetry.initialize()
        streamer = BinanceDataStreamer(symbols=symbols, interval="4h")
        adapter = QuantaAdapter(config_path=config_path, telemetry=telemetry)
        order_manager = BinanceOrderManager(api_key=api_key, api_secret=api_secret, telemetry=telemetry)
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
        self.streamer.last_processed_candle = await self.telemetry.load_last_processed_open_times_async("4h")
        await self._seed_histories()
        await self.order_manager.reconcile_exchange_state()

        consumer_task = asyncio.create_task(self._consume_queue())
        try:
            await self.streamer.consume_forever(stop_after=stop_after)
        finally:
            self._running = False
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    async def _consume_queue(self) -> None:
        while self._running:
            snapshot = await self.streamer.queue.get()
            try:
                await self.handle_closed_candle(snapshot)
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

        approved = await self.adapter.evaluate_closed_candle(snapshot)
        if approved is None:
            return
        await self.order_manager.execute_trade(approved)

    async def run_websocket_smoke_check(self, timeout_s: int = 20) -> dict[str, Any]:
        return await self.streamer.websocket_smoke_check(timeout_s=timeout_s)

    async def shutdown(self) -> None:
        self._running = False
        await self.streamer.shutdown()


async def main() -> None:
    orchestrator = LiveOrchestrator.from_env()
    try:
        await orchestrator.run()
    finally:
        await orchestrator.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
