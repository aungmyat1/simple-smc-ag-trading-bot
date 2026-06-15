"""
Risk management — position sizing and guard checks.
All functions are pure (no side effects); the runner calls them.
"""
from __future__ import annotations

from bot import config


def calc_position_size(
    account_usdt: float,
    entry_price: float,
    sl_price: float,
) -> float:
    """
    Qty (BTC) for a single trade.
    Risk = account × RISK_PER_TRADE, stop distance = entry − sl.
    Returns 0.0 if stop distance is non-positive or entry is zero.
    """
    if entry_price <= 0 or sl_price >= entry_price:
        return 0.0
    risk_usdt   = account_usdt * config.RISK_PER_TRADE
    stop_usdt   = entry_price - sl_price          # per BTC
    qty         = risk_usdt / stop_usdt
    return round(qty, 4)


def daily_loss_breached(equity: float, start_of_day_equity: float) -> bool:
    """True if today's loss exceeds MAX_DAILY_LOSS of starting equity."""
    if start_of_day_equity <= 0:
        return False
    return (equity - start_of_day_equity) / start_of_day_equity < -config.MAX_DAILY_LOSS


def drawdown_breached(equity: float, peak_equity: float) -> bool:
    """True if drawdown from peak exceeds MAX_DRAWDOWN."""
    if peak_equity <= 0:
        return False
    return (equity - peak_equity) / peak_equity < -config.MAX_DRAWDOWN


def trading_allowed(equity: float, peak: float, day_start: float) -> tuple[bool, str]:
    """
    Combined guard. Returns (ok, reason_string).
    ok=False → halt trading immediately.
    """
    if drawdown_breached(equity, peak):
        return False, f"MAX_DRAWDOWN breached: peak={peak:.2f} equity={equity:.2f}"
    if daily_loss_breached(equity, day_start):
        return False, f"DAILY_LOSS breached: day_start={day_start:.2f} equity={equity:.2f}"
    return True, "ok"
