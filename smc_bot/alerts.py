"""
Telegram alert delivery.

Sends a plain-text message to the configured chat.
Failures are logged but never re-raised — alerts must not crash the bot.

Usage:
    from smc_bot import alerts
    alerts.send("GUARD HALT — daily loss breached")
"""
import logging
import os

import requests

log = logging.getLogger(__name__)

_TIMEOUT = 5  # seconds


def send(msg: str) -> None:
    """Fire-and-forget Telegram message. Silently drops if env vars not set."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat  = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.debug("Telegram not configured — skipping alert: %s", msg[:80])
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg},
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            log.warning("Telegram returned %d: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("Telegram alert failed (non-fatal): %s", exc)
