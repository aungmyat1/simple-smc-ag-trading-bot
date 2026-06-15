"""
1H Point of Interest (POI) detection.

Bullish POIs (for long setups):
  - Bullish Order Block: last bearish candle before a bullish displacement (≥ N×ATR)
  - Bullish FVG: high[i-2] < low[i]  (gap filled by price = entry zone)

Bearish POIs (for short setups):
  - Bearish Order Block: last bullish candle before a bearish displacement
  - Bearish FVG: low[i-2] > high[i]

A zone is a dict: {kind: 'OB'|'FVG', low: float, high: float}
"""
import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _atr14(df: pd.DataFrame) -> float:
    """ATR(14) using Wilder's EMA, returned as a scalar for the last bar."""
    prev = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"]  - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.ewm(span=14, adjust=False).mean().iloc[-1])


def get_pois(
    df: pd.DataFrame,
    bias: str,
    ob_lookback: int = 50,
    fvg_lookback: int = 30,
    displacement_atr: float = 1.5,
) -> list[dict]:
    """
    Return all POI zones on the 1H chart for the given bias direction.
    Zones are not deduplicated — price_in_poi() picks the first match.
    """
    zones: list[dict] = []
    n      = len(df)
    atr    = _atr14(df)
    high   = df["high"].values
    low    = df["low"].values
    open_  = df["open"].values
    close  = df["close"].values

    if bias == "bullish":
        # ── Order Blocks ──────────────────────────────────────────────────────
        start = max(0, n - ob_lookback)
        for j in range(start, n - 1):
            if close[j] >= open_[j]:   # only bearish candles qualify
                continue
            j1 = j + 1
            # Displacement: next bar is bullish AND large
            if (
                close[j1] > open_[j1]
                and (high[j1] - low[j1]) >= displacement_atr * atr
            ):
                z_lo = min(open_[j], close[j])
                z_hi = max(open_[j], close[j])
                zones.append({"kind": "OB", "low": z_lo, "high": z_hi})

        # ── Fair Value Gaps ───────────────────────────────────────────────────
        start = max(2, n - fvg_lookback)
        for i in range(start, n):
            fvg_lo = high[i - 2]
            fvg_hi = low[i]
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi})

    elif bias == "bearish":
        # ── Order Blocks ──────────────────────────────────────────────────────
        start = max(0, n - ob_lookback)
        for j in range(start, n - 1):
            if close[j] <= open_[j]:   # only bullish candles qualify
                continue
            j1 = j + 1
            if (
                close[j1] < open_[j1]
                and (high[j1] - low[j1]) >= displacement_atr * atr
            ):
                z_lo = min(open_[j], close[j])
                z_hi = max(open_[j], close[j])
                zones.append({"kind": "OB", "low": z_lo, "high": z_hi})

        # ── Fair Value Gaps ───────────────────────────────────────────────────
        start = max(2, n - fvg_lookback)
        for i in range(start, n):
            fvg_hi = low[i - 2]
            fvg_lo = high[i]
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi})

    log.debug("Found %d POI zones (bias=%s, atr=%.2f)", len(zones), bias, atr)
    return zones


def price_in_poi(price: float, zones: list[dict]) -> dict | None:
    """Return the first zone that contains price, or None."""
    for z in zones:
        if z["low"] <= price <= z["high"]:
            return z
    return None
