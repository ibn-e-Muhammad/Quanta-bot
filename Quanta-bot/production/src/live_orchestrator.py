from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from production.src.live_telemetry import LiveTelemetryStore, default_db_path

# Parity imports from hardened research stack (no logic duplication).
from research.src.historical_simulator import HistoricalSimulator
from research.src.strategies import expansion_engine


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
    """Consumes Binance Futures Testnet kline websocket events and emits closed 4h candles."""

    def __init__(self, symbols: list[str], interval: str = "4h") -> None:
        self.symbols = symbols
        self.interval = interval
        self.ws_url = "wss://stream.binancefuture.com/stream"
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        # Idempotency guard for duplicate x=True events after reconnects.
        self.last_processed_candle: dict[str, int] = {}

    async def connect(self) -> None:
        """Initialize websocket client resources.

        Note: Stub only. Implementation will create the underlying websocket client.
        """
        return None

    async def subscribe(self) -> None:
        """Subscribe to `<symbol>@kline_4h` streams for all configured symbols."""
        return None

    async def consume_forever(self) -> None:
        """Consume websocket frames and publish closed-kline events to queue."""
        return None

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

        symbol = str(event.get("s") or k.get("s") or "")
        open_time = int(k.get("t", 0) or 0)
        if not symbol or open_time <= 0:
            return None

        last = self.last_processed_candle.get(symbol)
        if last is not None and open_time <= last:
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


class QuantaAdapter:
    """Adapter wrapper that reuses hardened research logic for live decisioning.

    This class does not reimplement strategy/ML math. It is responsible for:
    - converting live candle snapshots into the parity input shape,
    - delegating signal + lock + gate + ML decisions to existing logic,
    - returning an ApprovedTrade or None.
    """

    def __init__(self, config_path: str, telemetry: LiveTelemetryStore) -> None:
        self.config_path = config_path
        self.telemetry = telemetry
        self._sim_for_parity = HistoricalSimulator(
            config_path=config_path,
            data_dir=str(_ROOT / "research" / "historical_data"),
            db_path=":memory:",
            config_override={},
        )

    async def evaluate_closed_candle(self, snapshot: dict[str, Any]) -> ApprovedTrade | None:
        """Run parity signal->gate pipeline and return ApprovedTrade when executable.

        Stub contract:
        1) Persist market snapshot telemetry.
        2) Build row compatible with hardened strategy functions.
        3) Run expansion signal generation + Phase 6/7 adapter calls.
        4) Persist signal + gate telemetry.
        """
        return None

    async def build_parity_row(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Convert websocket candle payload into research-row structure for parity calls."""
        return {
            "datetime_utc": snapshot.get("timestamp"),
            "open": snapshot.get("open"),
            "high": snapshot.get("high"),
            "low": snapshot.get("low"),
            "close": snapshot.get("close"),
            "volume": snapshot.get("volume"),
        }

    async def run_signal_layer(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Delegate raw signal generation to existing strategy module."""
        _ = expansion_engine
        return None

    async def run_gate_layer(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Delegate Phase 7.4 + 7.3 gate evaluation using parity methods."""
        return {
            "final_decision": "VETO",
            "veto_reason": "stub_not_implemented",
        }

    async def build_approved_trade(self, candidate: dict[str, Any]) -> ApprovedTrade:
        """Create ApprovedTrade object from final candidate payload."""
        return ApprovedTrade(
            signal_id=str(candidate.get("signal_id")),
            timestamp=str(candidate.get("timestamp")),
            symbol=str(candidate.get("symbol")),
            side=str(candidate.get("side", "BUY")),
            expected_price=float(candidate.get("expected_price", 0.0)),
            quantity=float(candidate.get("quantity", 0.0)),
            tp_price=float(candidate.get("tp_price", 0.0)),
            sl_price=float(candidate.get("sl_price", 0.0)),
            strategy_name=str(candidate.get("strategy_name", "expansion_engine")),
            metadata=dict(candidate.get("metadata", {})),
        )


class BinanceOrderManager:
    """Places market entries and protective exits on Binance Futures Testnet."""

    def __init__(self, api_key: str, api_secret: str, telemetry: LiveTelemetryStore) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.telemetry = telemetry
        self.base_url = "https://testnet.binancefuture.com"

    async def place_market_entry(self, trade: ApprovedTrade) -> dict[str, Any]:
        """Place market entry order and return exchange response payload."""
        return {
            "status": "NEW",
            "clientOrderId": f"quanta-{uuid.uuid4().hex[:12]}",
        }

    async def place_tp_sl_orders(self, trade: ApprovedTrade) -> list[dict[str, Any]]:
        """Place TP/SL orders based on approved convexity exit settings."""
        return []

    async def execute_trade(self, trade: ApprovedTrade) -> dict[str, Any]:
        """Execute full lifecycle: market entry + TP/SL + telemetry write contract."""
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
        self.telemetry.insert_execution(execution_payload)
        return execution_payload


class LiveOrchestrator:
    """Async coordinator for BinanceDataStreamer, QuantaAdapter, and BinanceOrderManager."""

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
        """Factory: load credentials, universe, telemetry store, and class dependencies."""
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
        return cls(
            config_path=config_path,
            telemetry=telemetry,
            streamer=streamer,
            adapter=adapter,
            order_manager=order_manager,
        )

    async def run(self) -> None:
        """Start streamer and process closed-candle events until cancelled."""
        self._running = True
        await self.streamer.connect()
        await self.streamer.subscribe()
        await self.streamer.consume_forever()

    async def handle_closed_candle(self, snapshot: dict[str, Any]) -> None:
        """Run adapter and execute approved trades for one closed 4h candle snapshot."""
        approved = await self.adapter.evaluate_closed_candle(snapshot)
        if approved is None:
            return
        await self.order_manager.execute_trade(approved)

    async def shutdown(self) -> None:
        """Shutdown orchestrator resources gracefully."""
        self._running = False


async def main() -> None:
    """Async entrypoint for future live run wiring."""
    orchestrator = LiveOrchestrator.from_env()
    try:
        await orchestrator.run()
    finally:
        await orchestrator.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
