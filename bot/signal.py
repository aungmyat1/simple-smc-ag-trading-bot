"""
SMC signal — Trial 3: 1H POI → 5M execution.

Two-stage pipeline
──────────────────
Stage 1 (HTF — 1H):
  get_htf_context(df_1h) → bias, POI zones, Fibonacci 50%, liquidity pools

Stage 2 (LTF — 5M):
  get_ltf_signal(df_5m, htf_context) → LONG/FLAT, sl, tp1, tp2, tp_runner

Entry logic (LONG, all conditions AND-ed):
  1. 1H bias = bullish (close > EMA200, EMA slope positive)
  2. Current 5M price inside a 1H bullish POI (OB or FVG)
  3. Current 5M price ≤ Fibonacci 50% level (discount zone)
  4. 5M liquidity sweep of recent swing low (pierced + closed above)
  5. 5M MSS/CHoCH: after sweep, a 5M swing high was broken
  6. Price retracing into a fresh 5M bullish OB or FVG (execution zone)

Public API
──────────
  get_htf_context(df_1h)              → dict
  get_ltf_signal(df_5m, htf_context)  → dict
  get_signal_latest(df_1h, df_5m)     → dict  (runner entry point)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bot import config


# ── Shared helpers ────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> pd.Series:
    prev_c = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - prev_c).abs(),
         (df["low"]  - prev_c).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _swing_high(high: np.ndarray, lookback: int) -> np.ndarray:
    """Rolling max of previous `lookback` bars (no lookahead)."""
    result = np.full(len(high), np.nan)
    for i in range(lookback, len(high)):
        result[i] = np.max(high[i - lookback:i])
    return result


def _swing_low(low: np.ndarray, lookback: int) -> np.ndarray:
    """Rolling min of previous `lookback` bars (no lookahead)."""
    result = np.full(len(low), np.nan)
    for i in range(lookback, len(low)):
        result[i] = np.min(low[i - lookback:i])
    return result


# ── Stage 1: HTF context (1H) ─────────────────────────────────────────────────

def _htf_bias(df: pd.DataFrame) -> str:
    """
    'bullish' if close > EMA200 and EMA200 has been rising for 5 bars.
    'bearish' if close < EMA200 and falling.
    'neutral' otherwise.
    """
    ema = _ema(df["close"], config.HTF_EMA)
    last_close = df["close"].iloc[-1]
    last_ema   = ema.iloc[-1]
    slope_pos  = ema.iloc[-1] > ema.iloc[-6]   # rising over last 5 bars
    slope_neg  = ema.iloc[-1] < ema.iloc[-6]

    if last_close > last_ema and slope_pos:
        return "bullish"
    if last_close < last_ema and slope_neg:
        return "bearish"
    return "neutral"


def _htf_poi_zones(df: pd.DataFrame) -> list[tuple[float, float, str]]:
    """
    Return list of active bullish POI zones: (low, high, kind).
    kind = "OB" or "FVG".

    Bullish OB at bar j:
      - bar j is bearish
      - bar j+1 range >= HTF_OB_DISPLACEMENT × ATR
      - bar j+1 close > rolling swing high

    Bullish FVG between bar i-2 and bar i:
      - bar[i].low > bar[i-2].high  → gap above bar[i-2]
    """
    close      = df["close"].values
    open_      = df["open"].values
    high       = df["high"].values
    low        = df["low"].values
    atr_vals   = _atr(df).values
    swing_h    = _swing_high(high, config.HTF_SWING_LOOKBACK)
    n          = len(df)
    age_limit  = max(0, n - config.HTF_OB_MAX_AGE)
    zones: list[tuple[float, float, str]] = []

    # Order blocks
    for j in range(age_limit, n - 1):
        if close[j] >= open_[j]:    # must be bearish candle
            continue
        j1 = j + 1
        bar_range = high[j1] - low[j1]
        if np.isnan(atr_vals[j]) or bar_range < config.HTF_OB_DISPLACEMENT * atr_vals[j]:
            continue
        if np.isnan(swing_h[j1]) or close[j1] <= swing_h[j1]:
            continue
        ob_low  = min(open_[j], close[j])
        ob_high = max(open_[j], close[j])
        zones.append((ob_low, ob_high, "OB"))

    # Fair Value Gaps (bullish: low[i] > high[i-2])
    for i in range(age_limit + 2, n):
        fvg_low  = high[i - 2]
        fvg_high = low[i]
        if fvg_high > fvg_low:
            zones.append((fvg_low, fvg_high, "FVG"))

    return zones


def _htf_fib50(df: pd.DataFrame) -> float:
    """Midpoint of the range over last HTF_SWING_LOOKBACK×5 bars."""
    lb = min(config.HTF_SWING_LOOKBACK * 5, len(df))
    window = df.iloc[-lb:]
    swing_h = window["high"].max()
    swing_l = window["low"].min()
    return (swing_h + swing_l) / 2.0


def _htf_liquidity(df: pd.DataFrame) -> tuple[list[float], list[float]]:
    """
    Find equal highs / equal lows on 1H within HTF_EQUAL_LEVEL_TOL.
    Returns (liquidity_highs, liquidity_lows).
    """
    lb   = min(config.HTF_SWING_LOOKBACK * 3, len(df))
    highs = df["high"].iloc[-lb:].values
    lows  = df["low"].iloc[-lb:].values
    tol   = config.HTF_EQUAL_LEVEL_TOL

    def _cluster(levels: np.ndarray) -> list[float]:
        seen: list[float] = []
        result: list[float] = []
        for v in sorted(levels):
            matched = next((s for s in seen if abs(v - s) / s <= tol), None)
            if matched is None:
                seen.append(v)
            else:
                result.append((v + matched) / 2.0)
        return result

    return _cluster(highs), _cluster(lows)


def get_htf_context(df_1h: pd.DataFrame) -> dict:
    """
    Scan 1H dataframe and return bias, POI zones, fib50, and liquidity pools.
    Requires at least HTF_BARS rows for reliable output.
    """
    if len(df_1h) < max(config.HTF_EMA, config.HTF_SWING_LOOKBACK * 5):
        return {
            "bias": "neutral",
            "poi_zones": [],
            "fib50": 0.0,
            "liquidity_highs": [],
            "liquidity_lows": [],
        }

    liq_highs, liq_lows = _htf_liquidity(df_1h)
    return {
        "bias":             _htf_bias(df_1h),
        "poi_zones":        _htf_poi_zones(df_1h),
        "fib50":            _htf_fib50(df_1h),
        "liquidity_highs":  liq_highs,
        "liquidity_lows":   liq_lows,
    }


# ── Stage 2: LTF signal (5M) ──────────────────────────────────────────────────

def _ltf_ob_zones(df: pd.DataFrame) -> list[tuple[float, float]]:
    """
    Bullish 5M OBs formed in last LTF_OB_MAX_AGE bars.
    Same rule as HTF but using LTF params.
    """
    close      = df["close"].values
    open_      = df["open"].values
    high       = df["high"].values
    low        = df["low"].values
    atr_vals   = _atr(df).values
    swing_h    = _swing_high(high, config.LTF_SWING_LOOKBACK)
    n          = len(df)
    age_limit  = max(0, n - config.LTF_OB_MAX_AGE)
    zones: list[tuple[float, float]] = []

    for j in range(age_limit, n - 1):
        if close[j] >= open_[j]:
            continue
        j1 = j + 1
        bar_range = high[j1] - low[j1]
        if np.isnan(atr_vals[j]) or bar_range < config.HTF_OB_DISPLACEMENT * atr_vals[j]:
            continue
        if np.isnan(swing_h[j1]) or close[j1] <= swing_h[j1]:
            continue
        zones.append((min(open_[j], close[j]), max(open_[j], close[j])))

    return zones


def _ltf_fvg_zones(df: pd.DataFrame) -> list[tuple[float, float]]:
    """Bullish FVGs in last LTF_OB_MAX_AGE bars."""
    high = df["high"].values
    low  = df["low"].values
    n    = len(df)
    age_limit = max(2, n - config.LTF_OB_MAX_AGE)
    zones: list[tuple[float, float]] = []
    for i in range(age_limit, n):
        fvg_low  = high[i - 2]
        fvg_high = low[i]
        if fvg_high > fvg_low:
            zones.append((fvg_low, fvg_high))
    return zones


def _find_sweep(low: np.ndarray, close: np.ndarray, n: int) -> tuple[int, float] | tuple[None, None]:
    """
    Scan backward from bar n-1 over LTF_SWEEP_LOOKBACK bars for a liquidity sweep.
    Sweep: low[k] < ref_low * (1 - pierce) AND close[k] >= ref_low.
    ref_low = rolling min of LTF_SWING_LOOKBACK bars before the sweep window.

    Returns (sweep_bar_idx, sweep_wick_low) or (None, None).
    """
    lb         = config.LTF_SWEEP_LOOKBACK
    sl         = config.LTF_SWING_LOOKBACK
    start      = max(0, n - lb)
    ref_start  = max(0, start - sl)
    pierce     = config.LTF_SWEEP_PIERCE

    if ref_start >= start:
        return None, None

    ref_low = np.min(low[ref_start:start])
    threshold = ref_low * (1.0 - pierce)

    sweep_idx  = None
    sweep_low  = None
    for k in range(start, n):
        if low[k] < threshold and close[k] >= ref_low:
            sweep_idx = k
            sweep_low = low[k]

    return sweep_idx, sweep_low


def _has_choch(high: np.ndarray, close: np.ndarray, sweep_idx: int, n: int) -> bool:
    """True if close[n-1] broke the max high seen between sweep_idx and n-1."""
    if sweep_idx is None or sweep_idx >= n - 1:
        return False
    post_sweep_max = np.max(high[sweep_idx:n - 1])
    return close[n - 1] > post_sweep_max


def get_ltf_signal(df_5m: pd.DataFrame, htf_context: dict) -> dict:
    """
    Check LTF entry conditions given HTF context.
    Returns dict with 'action', 'sl', 'tp1', 'tp2', 'tp_runner'.
    """
    _flat = {"action": "FLAT", "sl": None, "tp1": None, "tp2": None, "tp_runner": None}

    if len(df_5m) < config.LTF_BARS // 2:
        return _flat

    # 1. HTF bias
    if htf_context.get("bias") != "bullish":
        return _flat

    high  = df_5m["high"].values
    low   = df_5m["low"].values
    close = df_5m["close"].values
    n     = len(df_5m)
    price = close[n - 1]

    # 2. Price inside a 1H bullish POI zone
    in_poi = any(
        low[n - 1] <= z_high and high[n - 1] >= z_low
        for z_low, z_high, _ in htf_context.get("poi_zones", [])
    )
    if not in_poi:
        return _flat

    # 3. Price in discount (below 1H 50% Fib)
    fib50 = htf_context.get("fib50", 0.0)
    if fib50 > 0 and price > fib50:
        return _flat

    # 4. Liquidity sweep of recent 5M swing low
    sweep_idx, sweep_low = _find_sweep(low, close, n)
    if sweep_idx is None:
        return _flat

    # 5. 5M CHoCH / MSS after the sweep
    if not _has_choch(high, close, sweep_idx, n):
        return _flat

    # 6. Price retracing into a fresh 5M OB or FVG (execution zone)
    ob_zones  = _ltf_ob_zones(df_5m)
    fvg_zones = _ltf_fvg_zones(df_5m)
    entry_zones = ob_zones + fvg_zones
    in_entry_zone = any(
        low[n - 1] <= z_high and high[n - 1] >= z_low
        for z_low, z_high in entry_zones
    )
    if not in_entry_zone:
        return _flat

    # All conditions met — build trade plan
    sl   = sweep_low * (1.0 - 0.001)   # 0.1% below wick
    risk = price - sl
    if risk <= 0:
        return _flat

    tp1 = price + risk * config.TP1_R
    tp2 = price + risk * config.TP2_R

    # Runner TP = nearest 1H liquidity high above entry
    liq_highs = [h for h in htf_context.get("liquidity_highs", []) if h > price]
    tp_runner  = min(liq_highs) if liq_highs else price + risk * config.TARGET_R

    return {
        "action":    "LONG",
        "sl":        sl,
        "tp1":       tp1,
        "tp2":       tp2,
        "tp_runner": tp_runner,
    }


# ── Public wrapper for runner ─────────────────────────────────────────────────

def get_signal_latest(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> dict:
    """Entry point for the runner. Calls both stages."""
    htf_context = get_htf_context(df_1h)
    return get_ltf_signal(df_5m, htf_context)
