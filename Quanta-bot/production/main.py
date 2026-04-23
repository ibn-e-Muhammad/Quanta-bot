from __future__ import annotations

import asyncio
import logging
import signal
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from src.live_orchestrator import LiveOrchestrator


def configure_logging(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("quanta.production")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(fmt)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)

    logger.addHandler(stdout_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def api_status_check() -> bool:
    url = "https://testnet.binancefuture.com/fapi/v1/ping"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return int(resp.status) == 200
    except Exception:
        return False


async def run_headless(logger: logging.Logger) -> None:
    load_dotenv()
    logger.info("Boot Sequence | .env loaded")

    import argparse
    parser = argparse.ArgumentParser(description="Quanta Production Runner")
    parser.add_argument("--config", default=None, help="Path to smoke test config JSON override")
    args, _ = parser.parse_known_args()

    orchestrator = LiveOrchestrator.from_env(smoke_config_path=args.config)
    symbols = len(orchestrator.streamer.symbols)
    logger.info("Boot Sequence | symbols loaded=%s", symbols)
    logger.info("Boot Sequence | telemetry_db=%s", orchestrator.telemetry.db_path)
    logger.info("Boot Sequence | api_status=%s", "OK" if api_status_check() else "UNREACHABLE")

    stop_event = asyncio.Event()

    def _request_stop() -> None:
        if not stop_event.is_set():
            logger.info("Shutdown signal received")
            stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows event loop may not support all signal handlers.
            pass

    run_task = asyncio.create_task(orchestrator.run(), name="orchestrator-run")
    stop_wait_task = asyncio.create_task(stop_event.wait(), name="stop-wait")

    done, _ = await asyncio.wait({run_task, stop_wait_task}, return_when=asyncio.FIRST_COMPLETED)

    if stop_wait_task in done and not run_task.done():
        run_task.cancel()

    try:
        await run_task
    except asyncio.CancelledError:
        logger.info("Orchestrator task cancelled")
    finally:
        await orchestrator.shutdown()
        logger.info("Shutdown complete")


def main() -> int:
    root = Path(__file__).resolve().parent
    logger = configure_logging(root / "production.log")
    logger.info("Boot Sequence | starting headless runtime")
    try:
        asyncio.run(run_headless(logger))
        return 0
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, exiting cleanly")
        return 0
    except Exception as exc:
        logger.exception("Fatal runtime error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
