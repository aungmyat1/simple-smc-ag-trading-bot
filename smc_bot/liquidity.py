"""
5M liquidity sweep detection.

A sweep occurs when a bar's wick pierces a prior swing level
and the bar CLOSES back on the opposite side — confirming the stop-hunt.

For a LONG setup: sweep of a prior short-term swing low.
For a SHORT setup: sweep of a prior short-term swing high.

Returns the MOST RECENT sweep in the scan window.
"""
import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _swing_highs(high: np.ndarray, n: int) -> list[int]:
    result = []
    for i in range(n, len(high) - n):
        if high[i] == np.max(high[i - n : i + n + 1]):
            result.append(i)
    return result


def _swing_lows(low: np.ndarray, n: int) -> list[int]:
    result = []
    for i in range(n, len(low) - n):
        if low[i] == np.min(low[i - n : i + n + 1]):
            result.append(i)
    return result


def get_sweep(
    df: pd.DataFrame,
    bias: str,
    lookback: int = 30,
    swing_n: int = 3,
) -> dict | None:
    """
    Scan the last `lookback` 5M bars for a liquidity sweep matching the bias.

    Returns:
        {
          'bar_idx':      int,    # index in df of the sweep bar
          'swept_level':  float,  # the swing level that was swept
          'wick_extreme': float,  # the bar's wick tip (low for long, high for short)
        }
    or None if no sweep found.
    """
    n     = len(df)
    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values

    # Only look at confirmed swing points within the scan window
    scan_start = max(swing_n * 2 + 1, n - lookback)

    if bias == "bullish":
        sl_idxs = _swing_lows(low, swing_n)
        candidates = [i for i in sl_idxs if scan_start <= i < n - 1]

        # Search from most-recent swing low backward to find latest sweep
        for sl_idx in reversed(candidates):
            level = low[sl_idx]
            for k in range(sl_idx + 1, n):
                if low[k] < level and close[k] > level:
                    log.debug(
                        "Bullish sweep at bar %d | level=%.2f wick=%.2f",
                        k, level, low[k],
                    )
                    return {
                        "bar_idx":      k,
                        "swept_level":  level,
                        "wick_extreme": low[k],
                    }

    elif bias == "bearish":
        sh_idxs = _swing_highs(high, swing_n)
        candidates = [i for i in sh_idxs if scan_start <= i < n - 1]

        for sh_idx in reversed(candidates):
            level = high[sh_idx]
            for k in range(sh_idx + 1, n):
                if high[k] > level and close[k] < level:
                    log.debug(
                        "Bearish sweep at bar %d | level=%.2f wick=%.2f",
                        k, level, high[k],
                    )
                    return {
                        "bar_idx":      k,
                        "swept_level":  level,
                        "wick_extreme": high[k],
                    }

    return None
