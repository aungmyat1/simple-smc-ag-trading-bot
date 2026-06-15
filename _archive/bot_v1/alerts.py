"""
Telegram alert dispatcher.
send() is a no-op (logs only) if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID are not set.
"""
from __future__ import annotations

import logging

import requests

from bot import config

log = logging.getLogger(__name__)

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send(message: str) -> None:
    """Send a Telegram message. Silently skips if credentials are missing."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.info("[ALERT-NOCRED] %s", message)
        return

    url = _TELEGRAM_URL.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id":    config.TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Telegram alert failed: %s", exc)
