"""
Shared primitives for the SMC signal layer (PROPOSE-ONLY).

ATR + swing-point (fractal) detection. No execution, no exchange SDK imports —
so tests/test_ast_guard.py stays green.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range. Expects lowercase open/high/low/close columns."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift()
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def swing_points(df: pd.DataFrame, n: int) -> tuple[list[int], list[int]]:
    """
    Fractal swings. Returns (high_idx, low_idx) — positional indices of confirmed
    swing highs and lows. A bar i is a swing high if its high is the strict max of
    the [i-n, i+n] window (and symmetrically for lows). Requires 2n+1 bars around i,
    so the last n bars are never confirmed (no lookahead leakage).
    """
    highs = df["high"].values
    lows = df["low"].values
    hi, lo = [], []
    for i in range(n, len(df) - n):
        win_h = highs[i - n : i + n + 1]
        win_l = lows[i - n : i + n + 1]
        if highs[i] == win_h.max() and (win_h.argmax() == n):
            hi.append(i)
        if lows[i] == win_l.min() and (win_l.argmin() == n):
            lo.append(i)
    return hi, lo
