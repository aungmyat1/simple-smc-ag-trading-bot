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
                zones.append({"kind": "OB", "low": z_lo, "high": z_hi, "creation_bar": j})

        # ── Fair Value Gaps ───────────────────────────────────────────────────
        start = max(2, n - fvg_lookback)
        for i in range(start, n):
            fvg_lo = high[i - 2]
            fvg_hi = low[i]
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi, "creation_bar": i})

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
                zones.append({"kind": "OB", "low": z_lo, "high": z_hi, "creation_bar": j})

        # ── Fair Value Gaps ───────────────────────────────────────────────────
        start = max(2, n - fvg_lookback)
        for i in range(start, n):
            fvg_hi = low[i - 2]
            fvg_lo = high[i]
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi, "creation_bar": i})

    log.debug("Found %d POI zones (bias=%s, atr=%.2f)", len(zones), bias, atr)
    return zones


def price_in_poi(price: float, zones: list[dict]) -> dict | None:
    """Return the first zone that contains price, or None."""
    for z in zones:
        if z["low"] <= price <= z["high"]:
            return z
    return None


def ob_for_price(price: float, zones: list[dict]) -> dict | None:
    """
    Return the first OB zone containing price, or None.

    Rule from diagrams: entry is at the Order Block, not at a standalone FVG.
    FVG zones are skipped — FVG presence is checked separately as confluence
    via has_fvg(). Only call this when you want the strict OB-at-entry rule.
    """
    for z in zones:
        if z.get("kind") == "OB" and z["low"] <= price <= z["high"]:
            return z
    return None


def has_fvg(zones: list[dict]) -> bool:
    """Return True if any FVG zone is present in the list (confluence check)."""
    return any(z.get("kind") == "FVG" for z in zones)



def fvg_for_price(price: float, zones: list[dict]) -> dict | None:
    """Return first FVG zone containing price, or None (FVG-retest entry gate)."""
    for z in zones:
        if z.get("kind") == "FVG" and z["low"] <= price <= z["high"]:
            return z
    return None

def filter_fresh_zones(
    zones: list[dict],
    df: pd.DataFrame,
    bias: str,
    consume_pct: float = 0.5,
    mode: str = "wick",
) -> list[dict]:
    """
    Remove mitigated zones.

    consume_pct: depth from entry edge before zone is considered consumed.
      0.5  = midpoint  |  0.75 = 75%  |  1.0 = fully consumed

    mode: which price level to compare against the threshold.
      "wick"  = low[k]/high[k] (Trial 9 baseline — aggressive at 4H)
      "close" = close[k] for both directions (Trial 10 — less aggressive)

    Threshold:
      bullish: zone.high − consume_pct × range  (mitigated when price ≤ threshold)
      bearish: zone.low  + consume_pct × range  (mitigated when price ≥ threshold)

    Zones without 'creation_bar' pass through unchanged (backward compat).
    """
    if not zones:
        return zones

    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    n     = len(df)
    fresh: list[dict] = []
    rejected = 0
    use_close = (mode == "close")

    for z in zones:
        if "creation_bar" not in z:
            fresh.append(z)
            continue
        zone_range = z["high"] - z["low"]
        if bias == "bullish":
            threshold = z["high"] - consume_pct * zone_range
        else:
            threshold = z["low"]  + consume_pct * zone_range
        cb  = z["creation_bar"]
        mitigated = False
        for k in range(cb + 1, n):
            if use_close:
                if bias == "bullish" and close[k] <= threshold:
                    mitigated = True
                    break
                if bias == "bearish" and close[k] >= threshold:
                    mitigated = True
                    break
            else:
                if bias == "bullish" and low[k] <= threshold:
                    mitigated = True
                    break
                if bias == "bearish" and high[k] >= threshold:
                    mitigated = True
                    break
        if mitigated:
            rejected += 1
        else:
            fresh.append(z)

    if rejected:
        log.debug(
            "Mitigation filter (%s, pct=%.0f%%, mode=%s): %d zones → %d fresh, %d rejected",
            bias, consume_pct * 100, mode, len(zones), len(fresh), rejected,
        )
    return fresh


def get_ltf_pois(
    df: pd.DataFrame,
    bias: str,
    start_bar: int,
    displacement_atr: float = 1.5,
    lookback: int = 15,
) -> list[dict]:
    """
    Steps 11-12 — detect the 5M OB/FVG created by the displacement move.

    Scans bars from start_bar (sweep bar) onward — the displacement and
    pullback zone are all to the RIGHT of the sweep.

    For a bullish setup:
      OB = last bearish candle before a bullish displacement ≥ N×ATR in this window.
      FVG = gap where high[i-2] < low[i] (upward gap filled on retrace).

    These zones define the ideal limit-entry area after the CHoCH fires.
    """
    zones: list[dict] = []
    n      = len(df)
    atr    = _atr14(df)
    scan_s = max(start_bar, 0)
    scan_e = min(n, start_bar + lookback)

    high   = df["high"].values
    low    = df["low"].values
    open_  = df["open"].values
    close  = df["close"].values

    if bias == "bullish":
        for j in range(scan_s, scan_e - 1):
            if close[j] >= open_[j]:
                continue
            j1 = j + 1
            if (
                close[j1] > open_[j1]
                and (high[j1] - low[j1]) >= displacement_atr * atr
            ):
                zones.append({"kind": "OB", "low": float(min(open_[j], close[j])),
                               "high": float(max(open_[j], close[j]))})
        for i in range(max(scan_s + 2, 2), scan_e):
            fvg_lo, fvg_hi = float(high[i - 2]), float(low[i])
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi})

    elif bias == "bearish":
        for j in range(scan_s, scan_e - 1):
            if close[j] <= open_[j]:
                continue
            j1 = j + 1
            if (
                close[j1] < open_[j1]
                and (high[j1] - low[j1]) >= displacement_atr * atr
            ):
                zones.append({"kind": "OB", "low": float(min(open_[j], close[j])),
                               "high": float(max(open_[j], close[j]))})
        for i in range(max(scan_s + 2, 2), scan_e):
            fvg_hi, fvg_lo = float(low[i - 2]), float(high[i])
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi})

    log.debug("LTF POI zones (bias=%s, start=%d): %d found", bias, start_bar, len(zones))
    return zones


def get_owned_fvg(
    df: "pd.DataFrame",
    bias: str,
    sweep_bar: int,
    choch_bar: int,
    displacement_atr: float = 1.5,
) -> "dict | None":
    """
    Return the FVG owned by the first displacement candle in [sweep_bar+1, choch_bar].

    Displacement = first candle with range ≥ displacement_atr × ATR14 and a
    directional body (bearish for shorts, bullish for longs).

    Owned FVG = 3-bar gap centred on that displacement candle:
      bearish: fvg_hi = low[disp-1]   fvg_lo = high[disp+1]
      bullish: fvg_lo = high[disp-1]  fvg_hi = low[disp+1]

    Returns {"kind": "FVG", "low": fvg_lo, "high": fvg_hi, "creation_bar": fvg_bar}
    or None if no qualifying displacement or gap is found.

    For bot.py use: pass choch_bar = len(df) - 2 (the bar just confirmed as CHoCH,
    where df has n bars and bar n-1 is the current bar where CHoCH fires).
    """
    n      = len(df)
    atr    = _atr14(df)
    high   = df["high"].values
    low    = df["low"].values
    open_  = df["open"].values
    close  = df["close"].values

    scan_start = max(sweep_bar + 1, 1)
    scan_end   = min(choch_bar, n - 2)   # fvg_bar = j+1 must be < n

    for j in range(scan_start, scan_end + 1):
        if (high[j] - low[j]) < displacement_atr * atr:
            continue
        if bias == "bearish" and close[j] >= open_[j]:
            continue
        if bias == "bullish" and close[j] <= open_[j]:
            continue
        fvg_bar = j + 1
        if bias == "bearish":
            fvg_hi = float(low[j - 1])
            fvg_lo = float(high[fvg_bar])
            if fvg_hi > fvg_lo:
                return {"kind": "FVG", "low": fvg_lo, "high": fvg_hi, "creation_bar": fvg_bar}
        else:
            fvg_lo = float(high[j - 1])
            fvg_hi = float(low[fvg_bar])
            if fvg_hi > fvg_lo:
                return {"kind": "FVG", "low": fvg_lo, "high": fvg_hi, "creation_bar": fvg_bar}
    return None

