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

# Bybit BTCUSDT perpetual contract constraints
BYBIT_MIN_QTY  = 0.001   # minimum order size in BTC
BYBIT_QTY_STEP = 0.001   # quantity increment


class PositionStateUnknownError(RuntimeError):
    """Raised by get_position() when the API call fails.

    Callers MUST NOT treat this as 'no position open'. Silently returning None
    on API failure would cause the bot to place a duplicate order against an
    already-open position (ghost duplicate risk). Instead, skip the cycle and
    wait for the next poll when the exchange is reachable again.
    """


def _live() -> bool:
    return os.getenv("LIVE_TRADING", "false").lower() == "true"


def _assert_ok(resp: dict, context: str) -> None:
    """Raise RuntimeError if the Bybit API response indicates failure."""
    ret_code = resp.get("retCode", -1)
    if ret_code != 0:
        msg = resp.get("retMsg", "no message")
        raise RuntimeError(f"Bybit API error [{context}] retCode={ret_code}: {msg}")


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
    """Return the open position dict for symbol, or None if flat.

    Raises PositionStateUnknownError on any API failure so the caller can skip
    the cycle rather than incorrectly assuming the account is flat.
    """
    try:
        resp = session.get_positions(category="linear", symbol=symbol)
        for pos in resp["result"]["list"]:
            if float(pos.get("size", 0)) != 0:
                return pos
        return None
    except Exception as exc:
        log.error("get_position failed: %s", exc)
        raise PositionStateUnknownError(
            f"Cannot determine position state for {symbol}: {exc}"
        ) from exc


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

    Raises RuntimeError if:
    - Bybit returns retCode != 0 (API-level error, not a network error)
    - The response has no orderId (malformed success response)

    In PAPER mode (LIVE_TRADING != 'true') this logs the intent and returns a
    synthetic result — no real order is sent.
    """
    log.info(
        "ORDER %s %s qty=%s SL=%.2f TP=%.2f [mode=%s]",
        side, symbol, qty, sl, tp,
        "LIVE" if _live() else "PAPER",
    )

    if not _live():
        return {
            "orderId": f"PAPER-{side[:1]}-{round(sl,0):.0f}",
            "side": side, "qty": str(qty), "sl": sl, "tp": tp,
        }

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

    _assert_ok(resp, "place_order")

    order_id = resp.get("result", {}).get("orderId", "")
    if not order_id:
        raise RuntimeError(
            f"place_order: retCode=0 but no orderId in response: {resp}"
        )

    log.info("Order confirmed: orderId=%s", order_id)
    return resp["result"]


def get_last_closed_pnl(
    session: HTTP,
    symbol: str,
    entry_time: str = "",
) -> float | None:
    """
    Return the realized PnL of the trade that closed after entry_time.

    entry_time: ISO-8601 UTC string (e.g. "2026-06-15T12:00:00+00:00").
                When provided, only records with updatedTime > entry_time are
                considered, preventing stale PnL from a previous trade from
                poisoning the consecutive-loss counter.

    Returns None if no matching record is found (too soon after close; caller
    should treat as unknown — do not reset or increment the counter).
    Positive = win, negative = loss.
    """
    try:
        resp = session.get_closed_pnl(category="linear", symbol=symbol, limit=5)
        _assert_ok(resp, "get_closed_pnl")
        items = resp.get("result", {}).get("list", [])
        if not items:
            return None

        if entry_time:
            # Parse entry epoch (ms) from ISO string
            from datetime import datetime, timezone
            try:
                entry_dt = datetime.fromisoformat(entry_time)
                entry_ms = int(entry_dt.timestamp() * 1000)
            except Exception:
                entry_ms = 0

            for item in items:
                try:
                    updated_ms = int(item.get("updatedTime", 0))
                except (ValueError, TypeError):
                    continue
                if updated_ms > entry_ms:
                    pnl = float(item.get("closedPnl", 0))
                    log.debug(
                        "Matched closed PnL: orderId=%s pnl=%.4f updatedTime=%d",
                        item.get("orderId", "?"), pnl, updated_ms,
                    )
                    return pnl
            # No record newer than entry — exchange hasn't indexed it yet
            log.debug("get_closed_pnl: no record newer than entry_time=%s", entry_time)
            return None

        return float(items[0].get("closedPnl", 0))

    except RuntimeError:
        raise
    except Exception as exc:
        log.error("get_last_closed_pnl failed: %s", exc)
        return None


def place_reduce_only_limit(
    session: HTTP,
    symbol: str,
    side: str,
    qty: float,
    price: float,
) -> dict:
    """
    Place a reduce-only GTC limit order for partial close (TP1).

    side: 'Sell' to close a long, 'Buy' to close a short.
    Bybit auto-cancels reduce-only orders when the position reaches zero,
    so no manual cancellation is needed if SL fires first.

    In PAPER mode returns a synthetic result without touching the exchange.
    """
    log.info(
        "REDUCE-ONLY LIMIT %s %s qty=%s price=%.2f [mode=%s]",
        side, symbol, qty, price,
        "LIVE" if _live() else "PAPER",
    )

    if not _live():
        return {
            "orderId": f"PAPER-ROL-{side[:1]}-{round(price,0):.0f}",
            "side": side, "qty": str(qty), "price": price, "reduceOnly": True,
        }

    resp = session.place_order(
        category="linear",
        symbol=symbol,
        side=side,
        orderType="Limit",
        qty=str(qty),
        price=str(round(price, 2)),
        reduceOnly=True,
        timeInForce="GTC",
        positionIdx=0,
    )

    _assert_ok(resp, "place_reduce_only_limit")

    order_id = resp.get("result", {}).get("orderId", "")
    if not order_id:
        raise RuntimeError(
            f"place_reduce_only_limit: retCode=0 but no orderId: {resp}"
        )

    log.info("TP1 limit confirmed: orderId=%s price=%.2f qty=%s", order_id, price, qty)
    return resp["result"]


def set_trading_stop(
    session: HTTP,
    symbol: str,
    sl: float | None = None,
    tp: float | None = None,
) -> dict:
    """
    Amend the position-level SL and/or TP in-flight (breakeven move after TP1).

    Bybit's set_trading_stop modifies the active position stop — it does NOT
    create a new order.  Pass sl=entry_price to move the stop to breakeven.
    Pass sl=0 or tp=0 to clear that level.

    In PAPER mode logs the intent and returns a synthetic result.
    """
    log.info(
        "SET_TRADING_STOP %s sl=%s tp=%s [mode=%s]",
        symbol, sl, tp,
        "LIVE" if _live() else "PAPER",
    )

    if not _live():
        return {"symbol": symbol, "sl": sl, "tp": tp, "paper": True}

    kwargs: dict = {
        "category": "linear",
        "symbol": symbol,
        "positionIdx": 0,
    }
    if sl is not None:
        kwargs["stopLoss"]    = str(round(sl, 2))
        kwargs["slTriggerBy"] = "LastPrice"
    if tp is not None:
        kwargs["takeProfit"]  = str(round(tp, 2))
        kwargs["tpTriggerBy"] = "LastPrice"

    resp = session.set_trading_stop(**kwargs)
    _assert_ok(resp, "set_trading_stop")
    log.info("Trading stop amended: sl=%s tp=%s", sl, tp)
    return resp.get("result", {})


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
    _assert_ok(resp, "close_position")
    log.info("Position closed: %s qty=%s", symbol, qty)
    return resp.get("result", {})
