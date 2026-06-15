"""
Position sizing — 1% risk per trade, no averaging/martingale.

qty (BTC) = (balance × risk_pct) / |entry − sl|
"""
import logging

log = logging.getLogger(__name__)


def calc_qty(
    balance: float,
    entry: float,
    sl: float,
    risk_pct: float = 0.01,
) -> float:
    """
    Return position size in BTC rounded to 4 decimal places.
    Returns 0.0 if the stop distance is zero or negative.
    """
    stop_dist = abs(entry - sl)
    if stop_dist <= 0:
        log.warning("Stop distance is zero; cannot size position")
        return 0.0
    risk_usdt = balance * risk_pct
    qty = round(risk_usdt / stop_dist, 4)
    log.debug(
        "Position size: balance=%.2f risk_usdt=%.2f stop_dist=%.4f qty=%.4f",
        balance, risk_usdt, stop_dist, qty,
    )
    return qty
