"""
Bybit order execution via pybit SDK (HMAC-SHA256 auth).
Targets Bybit Demo Trading account (api.bybit.com, demo=True).

LIVE_TRADING guard: when LIVE_TRADING env var is 'false' (default),
place_order() logs the intent but does NOT send to the exchange.
The owner must manually set LIVE_TRADING=true to enable real orders.
"""
import logging
import os

from pybit.unified_trading import HTTP

log = logging.getLogger(__name__)


def _live() -> bool:
    return os.getenv("LIVE_TRADING", "false").lower() == "true"


def make_session(api_key: str, api_secret: str, demo: bool = True) -> HTTP:
    """
    Create an authenticated pybit session.
    demo=True → Bybit Demo Trading (api.bybit.com with demo account).
    demo=False → live (use with caution; LIVE_TRADING must also be true).
    """
    return HTTP(
        testnet=False,
        demo=demo,
        api_key=api_key,
        api_secret=api_secret,
    )


def get_balance(session: HTTP, coin: str = "USDT") -> float:
    """Return available USDT in the Unified account."""
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin=coin)
        for c in resp["result"]["list"][0]["coin"]:
            if c["coin"] == coin:
                # availableToWithdraw is empty in demo accounts; fall back to walletBalance
                val = c.get("availableToWithdraw") or c.get("walletBalance", "0")
                return float(val) if val else 0.0
        return 0.0
    except Exception as exc:
        log.error("get_balance failed: %s", exc)
        return 0.0


def get_position(session: HTTP, symbol: str) -> dict | None:
    """Return the open position dict for symbol, or None if flat."""
    try:
        resp = session.get_positions(category="linear", symbol=symbol)
        for pos in resp["result"]["list"]:
            if float(pos.get("size", 0)) != 0:
                return pos
        return None
    except Exception as exc:
        log.error("get_position failed: %s", exc)
        return None


def place_order(
    session: HTTP,
    symbol: str,
    side: str,
    qty: float,
    sl: float,
    tp: float,
) -> dict:
    """
    Place a market order with attached SL and TP.
    side: 'Buy' for long, 'Sell' for short.

    In PAPER mode (LIVE_TRADING != 'true') this logs the intent and returns a
    synthetic result — no real order is sent.
    """
    log.info(
        "ORDER %s %s qty=%s SL=%.2f TP=%.2f [mode=%s]",
        side, symbol, qty, sl, tp,
        "LIVE" if _live() else "PAPER",
    )

    if not _live():
        return {"orderId": "PAPER", "side": side, "qty": str(qty), "sl": sl, "tp": tp}

    try:
        resp = session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            stopLoss=str(round(sl, 2)),
            takeProfit=str(round(tp, 2)),
            slTriggerBy="LastPrice",
            tpTriggerBy="LastPrice",
            reduceOnly=False,
            timeInForce="IOC",
            positionIdx=0,
        )
        order_id = resp.get("result", {}).get("orderId", "?")
        log.info("Order accepted: orderId=%s", order_id)
        return resp.get("result", {})
    except Exception as exc:
        log.error("place_order failed: %s", exc)
        raise


def get_last_closed_pnl(session: HTTP, symbol: str) -> float | None:
    """
    Return the realized PnL of the most recent closed trade, or None if unavailable.
    Positive = win, negative = loss.
    """
    try:
        resp = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        items = resp.get("result", {}).get("list", [])
        if items:
            return float(items[0].get("closedPnl", 0))
        return None
    except Exception as exc:
        log.error("get_last_closed_pnl failed: %s", exc)
        return None


def close_position(session: HTTP, symbol: str) -> dict:
    """Close the open position at market (reduce-only)."""
    pos = get_position(session, symbol)
    if pos is None:
        log.info("close_position: no open position for %s", symbol)
        return {}

    close_side = "Sell" if pos["side"] == "Buy" else "Buy"
    qty        = pos["size"]

    if not _live():
        log.info("PAPER close_position: would close %s %s @ market", qty, symbol)
        return {"orderId": "PAPER-CLOSE", "qty": qty}

    try:
        resp = session.place_order(
            category="linear",
            symbol=symbol,
            side=close_side,
            orderType="Market",
            qty=qty,
            reduceOnly=True,
            timeInForce="IOC",
            positionIdx=0,
        )
        log.info("Position closed: %s qty=%s", symbol, qty)
        return resp.get("result", {})
    except Exception as exc:
        log.error("close_position failed: %s", exc)
        raise
