"""
Bybit order placement via pybit SDK.
When LIVE_TRADING=False (default), all writes are simulated and logged only.
When LIVE_TRADING=True, real orders are placed on the configured account.
"""
from __future__ import annotations

import logging

from pybit.unified_trading import HTTP

from bot import config

log = logging.getLogger(__name__)

_session: HTTP | None = None


def _get_session() -> HTTP:
    global _session
    if _session is None:
        _session = HTTP(
            testnet=config.BYBIT_TESTNET,
            api_key=config.BYBIT_API_KEY,
            api_secret=config.BYBIT_API_SECRET,
        )
    return _session


def get_balance() -> float:
    """Return USDT wallet balance (unified account)."""
    sess = _get_session()
    resp = sess.get_wallet_balance(accountType="UNIFIED", coin="USDT")
    coins = resp["result"]["list"][0]["coin"]
    for c in coins:
        if c["coin"] == "USDT":
            return float(c["walletBalance"])
    return 0.0


def get_open_position() -> dict | None:
    """Return the open BTCUSDT position dict, or None if flat."""
    sess = _get_session()
    resp = sess.get_positions(category="linear", symbol=config.SYMBOL)
    for pos in resp["result"]["list"]:
        if float(pos.get("size", 0)) != 0:
            return pos
    return None


def place_long(qty: float, sl: float, tp: float) -> dict:
    """
    Open a long position with SL and TP attached.
    No-op (returns simulated result) when LIVE_TRADING=False.
    """
    if not config.LIVE_TRADING:
        log.info("[PAPER] LONG qty=%.4f sl=%.2f tp=%.2f", qty, sl, tp)
        return {"orderId": "PAPER", "qty": qty, "sl": sl, "tp": tp}

    sess = _get_session()
    resp = sess.place_order(
        category="linear",
        symbol=config.SYMBOL,
        side="Buy",
        orderType="Market",
        qty=str(qty),
        leverage=str(config.LEVERAGE),
        stopLoss=str(round(sl, 2)),
        takeProfit=str(round(tp, 2)),
        slTriggerBy="LastPrice",
        tpTriggerBy="LastPrice",
        positionIdx=0,
        timeInForce="IOC",
    )
    log.info("LONG placed: %s", resp)
    return resp["result"]


def close_position() -> dict:
    """
    Market-close the open BTCUSDT position.
    No-op when LIVE_TRADING=False.
    """
    if not config.LIVE_TRADING:
        log.info("[PAPER] CLOSE position")
        return {"orderId": "PAPER-CLOSE"}

    pos = get_open_position()
    if pos is None:
        return {}

    sess  = _get_session()
    qty   = pos["size"]
    side  = "Sell" if pos["side"] == "Buy" else "Buy"
    resp  = sess.place_order(
        category="linear",
        symbol=config.SYMBOL,
        side=side,
        orderType="Market",
        qty=qty,
        reduceOnly=True,
        timeInForce="IOC",
        positionIdx=0,
    )
    log.info("CLOSE placed: %s", resp)
    return resp["result"]
