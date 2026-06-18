"""
Abstract broker interface.

Every broker adapter (MetaAPI, Bybit, paper) implements this interface.
Strategies never import from specific adapters — only from this base.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrderResult:
    """Result returned by every broker after an order attempt."""
    success:    bool
    order_id:   str | None = None
    error:      str | None = None
    fill_price: float | None = None
    metadata:   dict = field(default_factory=dict)


class BaseBroker(ABC):
    """
    Abstract base for all broker adapters.

    Implementations must be async-safe (all methods are async).
    LIVE_TRADING guard is enforced here — concrete adapters inherit it
    and cannot bypass it without explicitly setting live_trading=True.
    """

    def __init__(self, live_trading: bool = False) -> None:
        self.live_trading = live_trading

    # ── required interface ────────────────────────────────────────────────────

    @abstractmethod
    async def place_order(
        self,
        symbol:   str,
        side:     str,          # "Buy" | "Sell"
        qty:      float,
        sl:       float,
        tp:       float,
        magic:    int,
        comment:  str,
        **kwargs: Any,
    ) -> OrderResult:
        """
        Place a market order with SL and TP.

        Returns OrderResult. Never raises — catch internally and return
        OrderResult(success=False, error=str(exc)) on failure.

        If live_trading=False, MUST log and return a dry-run result
        without sending to the exchange.
        """
        ...

    @abstractmethod
    async def close_position(
        self,
        symbol:   str,
        magic:    int,
        **kwargs: Any,
    ) -> OrderResult:
        """Close the open position for (symbol, magic)."""
        ...

    @abstractmethod
    async def get_position(
        self,
        symbol: str,
        magic:  int,
    ) -> dict | None:
        """
        Return the open position dict or None if flat.

        Minimum keys expected: {symbol, side, qty, entry_price, sl, tp, magic}
        """
        ...

    @abstractmethod
    async def get_balance(self) -> float:
        """Return account equity / balance in account currency."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """Return True if the broker connection is live and responsive."""
        ...

    # ── shared guard ──────────────────────────────────────────────────────────

    def _assert_live(self) -> None:
        """Raise RuntimeError if live_trading is disabled."""
        if not self.live_trading:
            raise RuntimeError(
                "Broker live_trading=False — this is a dry-run environment. "
                "Set LIVE_TRADING=true in .env and confirm manually to enable."
            )
