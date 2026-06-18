"""
Trade record and CSV/JSONL log writer.

Each strategy writes to its own directory:
  reports/smc/trades.csv      (SMC_SNIPER)
  reports/session/trades.csv  (SESSION_TRADER)

One TradeRecord per completed trade.  Appended on close, never rewritten.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_STRATEGY_DIRS: dict[str, str] = {
    "SMC_SNIPER":      "reports/smc",
    "SESSION_TRADER":  "reports/session",
}


@dataclass
class TradeRecord:
    """One completed trade."""
    strategy:    str
    symbol:      str
    side:        str
    magic:       int
    open_time:   str           # ISO UTC
    close_time:  str           # ISO UTC
    entry_price: float
    close_price: float
    sl:          float
    tp:          float
    qty:         float
    pnl:         float
    pnl_r:       float         # PnL in R-multiples (pnl / |entry-sl|/qty)
    setup:       str
    comment:     str
    metadata:    dict = field(default_factory=dict)

    @classmethod
    def now_utc(cls) -> str:
        return datetime.now(timezone.utc).isoformat()


class TradeLog:
    """
    Append-only trade log for a single strategy.

    Writes both CSV (human readable) and JSONL (machine readable).
    """

    def __init__(self, strategy_name: str, base_dir: str = ".") -> None:
        rel = _STRATEGY_DIRS.get(strategy_name, f"reports/{strategy_name.lower()}")
        out = Path(base_dir) / rel
        out.mkdir(parents=True, exist_ok=True)

        self._csv_path  = out / "trades.csv"
        self._jsonl_path = out / "trades.jsonl"
        self._strategy  = strategy_name
        self._ensure_csv_header()

    def append(self, record: TradeRecord) -> None:
        self._append_csv(record)
        self._append_jsonl(record)
        log.info(
            "TradeLog[%s] %s %s %s pnl=%.2f (%.2fR)",
            record.strategy, record.side, record.symbol,
            record.close_time[:10], record.pnl, record.pnl_r,
        )

    def _ensure_csv_header(self) -> None:
        if not self._csv_path.exists():
            with open(self._csv_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self._fieldnames())
                w.writeheader()

    def _append_csv(self, record: TradeRecord) -> None:
        row = {k: v for k, v in asdict(record).items() if k != "metadata"}
        with open(self._csv_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self._fieldnames())
            w.writerow(row)

    def _append_jsonl(self, record: TradeRecord) -> None:
        with open(self._jsonl_path, "a") as f:
            f.write(json.dumps(asdict(record)) + "\n")

    @staticmethod
    def _fieldnames() -> list[str]:
        return [
            "strategy", "symbol", "side", "magic",
            "open_time", "close_time",
            "entry_price", "close_price", "sl", "tp", "qty",
            "pnl", "pnl_r", "setup", "comment",
        ]
