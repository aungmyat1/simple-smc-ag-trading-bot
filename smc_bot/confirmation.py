"""
5M Change of Character (CHoCH) confirmation.

After a liquidity sweep, a CHoCH confirms the structural shift:
  - Bullish CHoCH: the current 5M bar's close breaks above the reference swing high
                   that existed before/around the sweep bar.
  - Bearish CHoCH: the current 5M bar's close breaks below the reference swing low.

'Reference level' = the highest high (bullish) or lowest low (bearish) in the
N bars leading up to and including the sweep bar.
"""
import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def get_choch(
    df: pd.DataFrame,
    bias: str,
    sweep: dict,
    lookback: int = 10,
) -> bool:
    """
    Return True if CHoCH is confirmed on the current (last) bar.

    sweep: dict returned by liquidity.get_sweep()
    lookback: number of bars before the sweep to define the reference level
    """
    n         = len(df)
    sweep_bar = sweep["bar_idx"]

    # The sweep must not be the current bar (need a subsequent close to confirm)
    if sweep_bar >= n - 1:
        return False

    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    last  = close[-1]

    ref_start = max(0, sweep_bar - lookback)

    if bias == "bullish":
        ref_level = float(np.max(high[ref_start : sweep_bar + 1]))
        confirmed = last > ref_level
        if confirmed:
            log.debug("Bullish CHoCH: close=%.2f > ref_high=%.2f", last, ref_level)
        return confirmed

    if bias == "bearish":
        ref_level = float(np.min(low[ref_start : sweep_bar + 1]))
        confirmed = last < ref_level
        if confirmed:
            log.debug("Bearish CHoCH: close=%.2f < ref_low=%.2f", last, ref_level)
        return confirmed

    return False
