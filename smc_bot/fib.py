"""
Fibonacci discount/premium filter.

For each setup, the 50% level of the current swing range divides the range
into discount (lower half) and premium (upper half).

  Long  setups require price ≤ midpoint  (discount zone).
  Short setups require price ≥ midpoint  (premium zone).

Using the most recent confirmed swing high AND swing low — not the all-time
range, just the recent structural range that defines the current bias.
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

from .structure import _swing_highs, _swing_lows

log = logging.getLogger(__name__)


def get_fib_midpoint(df: pd.DataFrame, bias: str, swing_n: int = 5) -> Optional[float]:
    """
    Return the 50% Fibonacci level between the last confirmed swing low and
    swing high on df (typically the 1H chart).

    For a bullish setup (HH+HL): midpoint of recent swing_low → swing_high.
    For a bearish setup (LL+LH): same range, but we want price to be in premium.
    Returns None if there are insufficient swing points.
    """
    if bias == "neutral":
        return None

    high = df["high"].values
    low  = df["low"].values

    sh = _swing_highs(high, swing_n)
    sl = _swing_lows(low, swing_n)

    if not sh or not sl:
        return None

    swing_high = float(high[sh[-1]])
    swing_low  = float(low[sl[-1]])

    if swing_high <= swing_low:
        return None

    midpoint = (swing_high + swing_low) / 2.0
    log.debug(
        "Fib midpoint=%.2f  (swing_low=%.2f → swing_high=%.2f, range=%.2f%%)",
        midpoint, swing_low, swing_high,
        (swing_high - swing_low) / swing_low * 100,
    )
    return midpoint


def in_discount(price: float, midpoint: float) -> bool:
    """Long filter: price is below the 50% level (discount zone)."""
    return price <= midpoint


def in_premium(price: float, midpoint: float) -> bool:
    """Short filter: price is above the 50% level (premium zone)."""
    return price >= midpoint


def fib_filter(price: float, bias: str, midpoint: Optional[float]) -> bool:
    """
    Return True if price satisfies the discount/premium condition for the bias.
    If midpoint is None, passes through (insufficient data to filter).
    """
    if midpoint is None:
        log.debug("Fib midpoint unavailable — filter bypassed")
        return True
    if bias == "bullish":
        ok = in_discount(price, midpoint)
    elif bias == "bearish":
        ok = in_premium(price, midpoint)
    else:
        return False
    log.debug(
        "Fib filter: price=%.2f  mid=%.2f  bias=%s  %s",
        price, midpoint, bias, "PASS" if ok else "FAIL",
    )
    return ok
