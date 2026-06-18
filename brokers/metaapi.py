"""
MetaAPI cloud SDK broker adapter.

Async adapter for VT Markets MT5 Demo via metaapi_cloud_sdk.
Used by both SMC_SNIPER and SESSION_TRADER strategies.

LIVE_TRADING guard: when live_trading=False (default), place_order()
and close_position() log the intent and return a dry-run OrderResult
without touching the exchange.

Environment vars (from .env):
    METAAPI_TOKEN       — MetaAPI auth token
    METAAPI_ACCOUNT_ID  — MT5 account GUID
    LIVE_TRADING        — "true" | "false" (default false)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .base import BaseBroker, OrderResult

log = logging.getLogger(__name__)

# Guard: import is optional so unit tests can run without the SDK installed
try:
    from metaapi_cloud_sdk import MetaApi  # type: ignore
    _SDK_AVAILABLE = True
except ImportError:
    MetaApi = None  # type: ignore
    _SDK_AVAILABLE = False


class MetaApiBroker(BaseBroker):
    """
    VT Markets MT5 via MetaAPI cloud SDK.

    Usage:
        broker = MetaApiBroker()
        await broker.connect()
        result = await broker.place_order("EURUSD", "Buy", 0.01, sl, tp, magic, comment)
        await broker.disconnect()

    Or use as async context manager:
        async with MetaApiBroker() as broker:
            ...
    """

    def __init__(
        self,
        token:      str | None = None,
        account_id: str | None = None,
        live_trading: bool | None = None,
    ) -> None:
        if live_trading is None:
            live_trading = os.getenv("LIVE_TRADING", "false").lower() == "true"
        super().__init__(live_trading=live_trading)

        self._token      = token      or os.getenv("METAAPI_TOKEN", "")
        self._account_id = account_id or os.getenv("METAAPI_ACCOUNT_ID", "")
        self._api        = None
        self._account    = None
        self._conn       = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if not _SDK_AVAILABLE:
            raise RuntimeError("metaapi_cloud_sdk not installed — pip install metaapi_cloud_sdk")
        if not self._token or not self._account_id:
            raise RuntimeError("METAAPI_TOKEN and METAAPI_ACCOUNT_ID must be set in .env")

        self._api     = MetaApi(self._token)
        self._account = await self._api.metatrader_account_api.get_account(self._account_id)

        if self._account.state not in ("DEPLOYED", "DEPLOYING"):
            await self._account.deploy()
            await self._account.wait_deployed()

        self._conn = self._account.get_rpc_connection()
        await self._conn.connect()
        await self._conn.wait_synchronized()
        log.info("MetaApiBroker: connected to account %s", self._account_id)

    async def disconnect(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
        if self._api:
            self._api.close()
            self._api = None
        log.info("MetaApiBroker: disconnected")

    async def __aenter__(self) -> "MetaApiBroker":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.disconnect()

    # ── BaseBroker interface ──────────────────────────────────────────────────

    async def place_order(
        self,
        symbol:  str,
        side:    str,
        qty:     float,
        sl:      float,
        tp:      float,
        magic:   int,
        comment: str,
        **kwargs: Any,
    ) -> OrderResult:
        if not self.live_trading:
            log.info(
                "[DRY-RUN] place_order %s %s qty=%.2f sl=%.5f tp=%.5f magic=%d",
                side, symbol, qty, sl, tp, magic,
            )
            return OrderResult(
                success=True,
                order_id=f"DRY-{magic}-{symbol}",
                fill_price=kwargs.get("entry_hint"),
                metadata={"dry_run": True},
            )

        try:
            self._assert_live()
            conn = self._require_conn()
            order_type = "ORDER_TYPE_BUY" if side == "Buy" else "ORDER_TYPE_SELL"

            result = await conn.create_market_order(
                symbol=symbol,
                volume=qty,
                type=order_type,
                stop_loss=sl,
                take_profit=tp,
                options={
                    "magic":   magic,
                    "comment": comment[:31],   # MT5 comment is max 31 chars
                },
            )
            log.info("MetaApiBroker: placed %s %s | id=%s", side, symbol, result.get("orderId"))
            return OrderResult(
                success=True,
                order_id=str(result.get("orderId", "")),
                fill_price=result.get("openPrice"),
                metadata=result,
            )
        except Exception as exc:
            log.error("MetaApiBroker.place_order(%s %s): %s", side, symbol, exc)
            return OrderResult(success=False, error=str(exc))

    async def close_position(
        self,
        symbol: str,
        magic:  int,
        **kwargs: Any,
    ) -> OrderResult:
        if not self.live_trading:
            log.info("[DRY-RUN] close_position %s magic=%d", symbol, magic)
            return OrderResult(success=True, order_id=f"DRY-CLOSE-{magic}", metadata={"dry_run": True})

        try:
            self._assert_live()
            pos = await self.get_position(symbol, magic)
            if pos is None:
                return OrderResult(success=False, error=f"No open position for {symbol} magic={magic}")

            conn   = self._require_conn()
            result = await conn.close_position_by_id(pos["id"])
            log.info("MetaApiBroker: closed %s magic=%d", symbol, magic)
            return OrderResult(success=True, order_id=str(result.get("orderId", "")), metadata=result)
        except Exception as exc:
            log.error("MetaApiBroker.close_position(%s magic=%d): %s", symbol, magic, exc)
            return OrderResult(success=False, error=str(exc))

    async def get_position(self, symbol: str, magic: int) -> dict | None:
        try:
            conn      = self._require_conn()
            positions = await conn.get_positions()
            for p in positions:
                if p.get("symbol") == symbol and p.get("magic") == magic:
                    return {
                        "id":          p.get("id"),
                        "symbol":      p.get("symbol"),
                        "side":        "Buy" if p.get("type") == "POSITION_TYPE_BUY" else "Sell",
                        "qty":         p.get("volume"),
                        "entry_price": p.get("openPrice"),
                        "sl":          p.get("stopLoss"),
                        "tp":          p.get("takeProfit"),
                        "magic":       p.get("magic"),
                        "profit":      p.get("profit"),
                    }
            return None
        except Exception as exc:
            log.error("MetaApiBroker.get_position(%s magic=%d): %s", symbol, magic, exc)
            return None

    async def get_balance(self) -> float:
        try:
            conn  = self._require_conn()
            info  = await conn.get_account_information()
            return float(info.get("equity", info.get("balance", 0.0)))
        except Exception as exc:
            log.error("MetaApiBroker.get_balance: %s", exc)
            return 0.0

    async def is_connected(self) -> bool:
        if self._conn is None:
            return False
        try:
            info = await self._conn.get_account_information()
            return bool(info)
        except Exception:
            return False

    # ── helpers ───────────────────────────────────────────────────────────────

    def _require_conn(self):
        if self._conn is None:
            raise RuntimeError("MetaApiBroker not connected — call await broker.connect() first")
        return self._conn
