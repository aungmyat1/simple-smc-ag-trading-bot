"""
1H market structure — identify bias from swing point sequence.

Bullish bias:  Higher High (HH) + Higher Low (HL) — latest structural break is up.
Bearish bias:  Lower Low (LL) + Lower High (LH) — latest structural break is down.
Neutral:       mixed or insufficient swing history.
"""
import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _swing_highs(high: np.ndarray, n: int) -> list[int]:
    """
    Return indices where high[i] is the maximum in the [i-n, i+n] window.
    Excludes the last n bars (not yet confirmed by right-side bars).
    """
    result = []
    for i in range(n, len(high) - n):
        if high[i] == np.max(high[i - n : i + n + 1]):
            result.append(i)
    return result


def _swing_lows(low: np.ndarray, n: int) -> list[int]:
    """Return indices where low[i] is the minimum in the [i-n, i+n] window."""
    result = []
    for i in range(n, len(low) - n):
        if low[i] == np.min(low[i - n : i + n + 1]):
            result.append(i)
    return result


def get_bias(df: pd.DataFrame, swing_n: int = 5) -> str:
    """
    Classify the 1H trend direction.

    HH+HL → 'bullish'  (implies the most recent BOS was a break above a swing high)
    LL+LH → 'bearish'  (implies the most recent BOS was a break below a swing low)
    otherwise → 'neutral'
    """
    high = df["high"].values
    low  = df["low"].values

    sh = _swing_highs(high, swing_n)
    sl = _swing_lows(low, swing_n)

    if len(sh) < 2 or len(sl) < 2:
        log.debug("Insufficient swing history (sh=%d sl=%d)", len(sh), len(sl))
        return "neutral"

    hh = high[sh[-1]] > high[sh[-2]]
    hl = low[sl[-1]]  > low[sl[-2]]
    lh = high[sh[-1]] < high[sh[-2]]
    ll = low[sl[-1]]  < low[sl[-2]]

    if hh and hl:
        log.debug(
            "Bias BULLISH — SH: %.2f > %.2f, SL: %.2f > %.2f",
            high[sh[-1]], high[sh[-2]], low[sl[-1]], low[sl[-2]],
        )
        return "bullish"

    if lh and ll:
        log.debug(
            "Bias BEARISH — SH: %.2f < %.2f, SL: %.2f < %.2f",
            high[sh[-1]], high[sh[-2]], low[sl[-1]], low[sl[-2]],
        )
        return "bearish"

    return "neutral"
