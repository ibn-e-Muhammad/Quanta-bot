import aiohttp
import asyncio
import json
import os
import traceback

async def _send_discord_webhook(url: str, payload: dict) -> None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=5.0) as response:
                if response.status not in (200, 204):
                    text = await response.text()
                    print(f"[DISCORD ALERT ERROR] HTTP {response.status}: {text}")
    except Exception as exc:
        print(f"[DISCORD ALERT ERROR] Failed to send webhook: {exc}")

def send_alert_async(message: str, level: str = "INFO", details: dict | None = None) -> None:
    """
    Fire-and-forget background task to send an alert to Discord.
    Does NOT block the main event loop.
    """
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        return  # Silently skip if no webhook is configured

    # Map levels to Discord side-bar colors
    colors = {
        "INFO": 3447003,       # Blue
        "WARNING": 16776960,   # Yellow
        "ERROR": 15158332,     # Red
        "CRITICAL": 15158332,  # Red
        "SUCCESS": 3066993,    # Green
    }

    embed = {
        "title": f"[{level}] Quanta Bot Alert",
        "description": message,
        "color": colors.get(level.upper(), 3447003),
    }

    if details:
        fields = []
        for k, v in details.items():
            fields.append({"name": str(k), "value": str(v), "inline": True})
        embed["fields"] = fields

    payload = {"embeds": [embed]}

    # Schedule the coroutine in the background safely
    asyncio.create_task(_send_discord_webhook(url, payload))
