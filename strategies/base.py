"""
Base classes for all trading strategies.

Every strategy returns a TradeSignal dataclass or None.
The runner routes signals to the appropriate broker; strategies
never touch the broker or risk layer directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class TradeSignal:
    """Fully-specified trade instruction produced by a strategy."""
    symbol:     str         # "EURUSD" | "GBPUSD"
    side:       str         # "Buy" | "Sell"
    entry:      float       # market entry price (informational; broker uses market)
    sl:         float       # stop-loss price
    tp:         float       # take-profit price (final runner)
    tp1:        float       # first partial TP price (close tp1_pct here)
    tp1_pct:    float       # fraction to close at tp1 (0.75 for session; 0.50 for SMC)
    strategy:   str         # "SMC_SNIPER" | "SESSION_TRADER"
    setup:      str         # "sweep" | "range" | "trend" | "london_sweep" | etc.
    magic:      int         # unique MT5 magic number
    comment:    str         # order comment for MT5 position tagging
    r_dist:     float       # |entry − sl| in price units
    metadata:   dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)


class BaseStrategy(ABC):
    """
    Abstract base for SMC Sniper and Session Trader.

    Concrete implementations receive HTF + LTF DataFrames and
    return a TradeSignal or None. No side effects are permitted —
    no orders, no file writes, no HTTP calls.
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    # ── required interface ─────────────────────────────────────────────────────

    @abstractmethod
    def generate_signal(
        self,
        symbol:  str,
        df_htf:  pd.DataFrame,
        df_ltf:  pd.DataFrame,
    ) -> TradeSignal | None:
        """
        Analyse market data and return a signal or None.

        Both DataFrames must have columns: ts, open, high, low, close, volume.
        ts values must be UTC-aware Timestamps.
        """
        ...

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """Unique identifier string — used as the 'strategy' field in TradeSignal."""
        ...

    @abstractmethod
    def magic_number(self, symbol: str) -> int:
        """Return the unique MT5 magic number for (strategy, symbol)."""
        ...

    # ── shared helpers ─────────────────────────────────────────────────────────

    def _comment(self, symbol: str) -> str:
        return f"{self.strategy_name}_{symbol}"

    def _sl_r(self, entry: float, sl: float) -> float:
        return abs(entry - sl)

    def _tp_from_r(self, entry: float, sl: float, r_mult: float, side: str) -> float:
        r = self._sl_r(entry, sl)
        return entry + r * r_mult if side == "Buy" else entry - r * r_mult
