"""
Liquidity pool detection for TP placement.

The workflow targets TP at "previous highs / liquidity" (BSL for longs)
and "previous lows / liquidity" (SSL for shorts) — not a fixed R multiple.

Equal highs: two or more confirmed swing highs within `tolerance` of each other.
Equal lows:  two or more confirmed swing lows within `tolerance` of each other.

These clusters represent stop-orders pooled above/below visible price levels,
which institutional players hunt.  They are the natural TP targets in SMC.

get_tp_level() returns the nearest cluster ABOVE entry for longs (or below for
shorts) that provides at least `min_r` reward, falling back to None so the
caller can use a fixed 2R as a floor.
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

from .structure import _swing_highs, _swing_lows

log = logging.getLogger(__name__)


def _cluster_levels(values: list[float], tolerance: float) -> list[float]:
    """
    Group close values into clusters; return the average of each cluster.
    A new cluster starts when a value differs from the running cluster mean
    by more than tolerance (as a fraction of price).
    """
    if not values:
        return []
    values = sorted(values)
    clusters: list[list[float]] = [[values[0]]]
    for v in values[1:]:
        ref = sum(clusters[-1]) / len(clusters[-1])
        if abs(v - ref) / ref <= tolerance:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    # Only keep clusters with 2+ swing points (genuine liquidity pools)
    return [sum(c) / len(c) for c in clusters if len(c) >= 2]


def get_bsl_levels(
    df: pd.DataFrame,
    swing_n: int = 5,
    tolerance: float = 0.002,
) -> list[float]:
    """
    Return buy-side liquidity (BSL) levels — clusters of equal swing highs.
    These are TP targets for long trades.
    Returned in ascending order.
    """
    sh = _swing_highs(df["high"].values, swing_n)
    raw = [float(df["high"].values[i]) for i in sh]
    levels = _cluster_levels(raw, tolerance)
    log.debug("BSL levels: %s", [f"{l:.2f}" for l in levels])
    return levels


def get_ssl_levels(
    df: pd.DataFrame,
    swing_n: int = 5,
    tolerance: float = 0.002,
) -> list[float]:
    """
    Return sell-side liquidity (SSL) levels — clusters of equal swing lows.
    These are TP targets for short trades.
    Returned in ascending order.
    """
    sl = _swing_lows(df["low"].values, swing_n)
    raw = [float(df["low"].values[i]) for i in sl]
    levels = _cluster_levels(raw, tolerance)
    log.debug("SSL levels: %s", [f"{l:.2f}" for l in levels])
    return levels


def get_tp_level(
    df: pd.DataFrame,
    bias: str,
    entry: float,
    stop_dist: float,
    swing_n: int = 5,
    tolerance: float = 0.002,
    min_r: float = 1.5,
) -> Optional[float]:
    """
    Return the nearest liquidity-pool TP at or above min_r reward from entry.

    Long:  nearest BSL (equal highs cluster) above entry with reward ≥ min_r.
    Short: nearest SSL (equal lows cluster) below entry with reward ≥ min_r.
    Returns None if no qualifying pool found (caller falls back to fixed R).
    """
    if stop_dist <= 0:
        return None

    if bias == "bullish":
        levels = get_bsl_levels(df, swing_n, tolerance)
        # nearest cluster above entry that gives ≥ min_r
        for lvl in sorted(levels):
            if lvl > entry and (lvl - entry) / stop_dist >= min_r:
                log.debug("BSL TP=%.2f (%.2fR)", lvl, (lvl - entry) / stop_dist)
                return lvl

    elif bias == "bearish":
        levels = get_ssl_levels(df, swing_n, tolerance)
        for lvl in sorted(levels, reverse=True):
            if lvl < entry and (entry - lvl) / stop_dist >= min_r:
                log.debug("SSL TP=%.2f (%.2fR)", lvl, (entry - lvl) / stop_dist)
                return lvl

    return None
