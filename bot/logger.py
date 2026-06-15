"""
Append-only trade journal.
Writes one row per closed trade to data/trades.csv.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from bot import config

log = logging.getLogger(__name__)

_COLUMNS = [
    "timestamp", "symbol", "side", "entry", "exit", "sl", "tp",
    "qty", "pnl_usdt", "pnl_r", "exit_reason",
]


def _ensure_header(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_COLUMNS)
            writer.writeheader()


def log_trade(
    entry:       float,
    exit_price:  float,
    sl:          float,
    tp:          float,
    qty:         float,
    exit_reason: str,
    side:        str = "long",
) -> None:
    """Record a completed trade."""
    _ensure_header(config.TRADES_CSV)

    risk_per_unit = entry - sl if side == "long" else sl - entry
    pnl_usdt      = (exit_price - entry) * qty if side == "long" else (entry - exit_price) * qty
    pnl_r         = pnl_usdt / (risk_per_unit * qty) if risk_per_unit > 0 else 0.0

    row = {
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "symbol":      config.SYMBOL,
        "side":        side,
        "entry":       round(entry, 2),
        "exit":        round(exit_price, 2),
        "sl":          round(sl, 2),
        "tp":          round(tp, 2),
        "qty":         round(qty, 6),
        "pnl_usdt":    round(pnl_usdt, 4),
        "pnl_r":       round(pnl_r, 4),
        "exit_reason": exit_reason,
    }
    with open(config.TRADES_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writerow(row)
    log.info("Trade logged: %s", row)
