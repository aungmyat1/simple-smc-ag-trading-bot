"""
Position sizing and capital-protection guards.

Guards are pure functions — no side effects.
The runner reads their return values and halts accordingly.
"""
import logging

log = logging.getLogger(__name__)

# Bybit BTCUSDT perpetual contract minimums (kept in sync with executor.py)
BYBIT_MIN_QTY  = 0.001
BYBIT_QTY_STEP = 0.001


def calc_qty(
    balance: float,
    entry: float,
    sl: float,
    risk_pct: float = 0.01,
) -> float:
    """
    Return position size in BTC, snapped to BYBIT_QTY_STEP and floored at
    BYBIT_MIN_QTY.  Returns 0.0 if the stop distance is zero/negative.

    Bybit rejects orders below BYBIT_MIN_QTY with retCode=10001, so we snap
    up to the minimum rather than letting the caller send an invalid order.
    The resulting risk overshoot is at most BYBIT_MIN_QTY * stop_dist, which
    is negligible for any reasonable account size.
    """
    stop_dist = abs(entry - sl)
    if stop_dist <= 0:
        log.warning("Stop distance is zero; cannot size position")
        return 0.0

    risk_usdt = balance * risk_pct
    raw_qty   = risk_usdt / stop_dist

    # Snap to exchange step size
    steps = round(raw_qty / BYBIT_QTY_STEP)
    qty   = steps * BYBIT_QTY_STEP

    if qty < BYBIT_MIN_QTY:
        # Rounding UP to the minimum would silently breach risk_pct (the actual
        # dollar risk could be several multiples of the intended amount on a small
        # account).  Return 0.0 so the caller's qty <= 0 guard skips the trade.
        log.warning(
            "Computed qty %.4f is below exchange minimum %.3f — skipping trade "
            "(account too small or stop too wide for this risk_pct)",
            raw_qty, BYBIT_MIN_QTY,
        )
        return 0.0

    qty = round(qty, 3)

    log.debug(
        "Position size: balance=%.2f risk_usdt=%.2f stop_dist=%.4f raw=%.4f qty=%.3f",
        balance, risk_usdt, stop_dist, raw_qty, qty,
    )
    return qty


def daily_loss_breached(equity: float, day_start_equity: float, max_daily_loss: float) -> bool:
    """True if today's drawdown from day-open equity exceeds max_daily_loss (e.g. 0.02)."""
    if day_start_equity <= 0:
        return False
    return (equity - day_start_equity) / day_start_equity < -max_daily_loss


def drawdown_breached(equity: float, peak_equity: float, max_drawdown: float) -> bool:
    """True if drawdown from all-time peak equity exceeds max_drawdown (e.g. 0.10)."""
    if peak_equity <= 0:
        return False
    return (equity - peak_equity) / peak_equity < -max_drawdown


def consecutive_losses_breached(count: int, max_consecutive_losses: int) -> bool:
    """True if consecutive losses >= max_consecutive_losses. Resets to 0 on any win."""
    return count >= max_consecutive_losses


def trading_allowed(
    equity: float,
    peak_equity: float,
    day_start_equity: float,
    consecutive_losses: int,
    max_daily_loss: float,
    max_drawdown: float,
    max_consecutive_losses: int,
) -> tuple[bool, str]:
    """
    Combined guard. Returns (ok, reason).
    ok=False → halt immediately; do not enter any new position.
    """
    if drawdown_breached(equity, peak_equity, max_drawdown):
        return False, (
            f"MAX_DRAWDOWN breached: peak={peak_equity:.2f} equity={equity:.2f} "
            f"dd={100*(equity-peak_equity)/peak_equity:.1f}%"
        )
    if daily_loss_breached(equity, day_start_equity, max_daily_loss):
        return False, (
            f"DAILY_LOSS breached: day_start={day_start_equity:.2f} equity={equity:.2f} "
            f"loss={100*(equity-day_start_equity)/day_start_equity:.1f}%"
        )
    if consecutive_losses_breached(consecutive_losses, max_consecutive_losses):
        return False, f"CONSECUTIVE_LOSSES breached: {consecutive_losses} in a row"
    return True, "ok"
