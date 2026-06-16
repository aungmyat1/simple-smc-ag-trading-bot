"""
5M liquidity sweep detection + post-sweep displacement gate.

SWEEP: a bar's wick pierces a prior swing level and closes back on the other side.
  Long:  wick below swing low, close above it.
  Short: wick above swing high, close below it.

DISPLACEMENT (step 9 of the workflow): after the sweep, a STRONG candle in the
trade direction (≥ N×ATR) must appear before the CHoCH is considered valid.
This filters out weak, low-momentum moves.
"""
import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _atr14(df: pd.DataFrame) -> float:
    """ATR(14) Wilder EMA — scalar for the last bar."""
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"]  - prev).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(span=14, adjust=False).mean().iloc[-1])


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


def displacement_strength(
    df: pd.DataFrame,
    sweep_bar: int,
    bias: str,
    atr_mult_strong: float = 2.0,
) -> str:
    """
    Return 'Strong' if the best post-sweep displacement candle is ≥ atr_mult_strong × ATR,
    'Normal' otherwise (caller already confirmed ≥ 1.5 × ATR via check_displacement).
    """
    n     = len(df)
    atr   = _atr14(df)
    high  = df["high"].values
    low   = df["low"].values
    open_ = df["open"].values
    close = df["close"].values

    best = 0.0
    for i in range(sweep_bar + 1, n):
        if bias == "bullish" and close[i] > open_[i]:
            best = max(best, high[i] - low[i])
        elif bias == "bearish" and close[i] < open_[i]:
            best = max(best, high[i] - low[i])

    return "Strong" if (atr > 0 and best / atr >= atr_mult_strong) else "Normal"


def check_displacement(
    df: pd.DataFrame,
    sweep_bar: int,
    bias: str,
    atr_mult: float = 1.5,
) -> bool:
    """
    Step 9 — verify a strong displacement candle exists after the sweep.

    Looks at every bar from sweep_bar+1 to the current bar.  Returns True if
    at least one candle:
      • is in the trade direction (bullish body for long, bearish body for short)
      • has range ≥ atr_mult × ATR(14)

    This confirms institutional momentum drove price away from the swept level,
    not just noise.
    """
    n     = len(df)
    atr   = _atr14(df)
    high  = df["high"].values
    low   = df["low"].values
    open_ = df["open"].values
    close = df["close"].values

    for i in range(sweep_bar + 1, n):
        if (high[i] - low[i]) < atr_mult * atr:
            continue
        if bias == "bullish" and close[i] > open_[i]:
            log.debug("Displacement candle at bar %d (bullish, range=%.2f, atr=%.2f)", i, high[i]-low[i], atr)
            return True
        if bias == "bearish" and close[i] < open_[i]:
            log.debug("Displacement candle at bar %d (bearish, range=%.2f, atr=%.2f)", i, high[i]-low[i], atr)
            return True

    return False
