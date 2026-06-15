"""
SMC signal detector — Trial 3.

Pipeline per bar:
  1. Trend filter  : close > EMA200 → bullish regime only
  2. Order Block   : find most recent bullish OB in last OB_MAX_AGE_BARS bars
  3. Price in OB   : current bar overlaps the OB zone
  4. Sweep         : recent bar swept the swing low (pierced + closed above)
  5. CHoCH         : after the sweep, a swing high was broken (trend flip confirmed)
  → LONG with SL below sweep wick, TP at TARGET_R × risk

Public API
----------
add_indicators(df)         → df with ema200, atr, swing_high, swing_low columns
get_signals(df)            → df with 'signal', 'sl', 'tp' columns  (for backtest)
get_signal_latest(df)      → dict(action, sl, tp)                  (for runner)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bot import config


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["ema200"] = df["close"].ewm(span=config.HTF_EMA, adjust=False).mean()

    # True Range → ATR
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - prev_close).abs(),
         (df["low"]  - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.ewm(span=config.ATR_PERIOD, adjust=False).mean()

    lb = config.SWING_LOOKBACK
    # shift(1) so bar i only sees data before itself (no lookahead)
    df["swing_high"] = df["high"].rolling(lb).max().shift(1)
    df["swing_low"]  = df["low"].rolling(lb).min().shift(1)

    return df


# ── Order Block detection (vectorised pre-pass) ───────────────────────────────

def _precompute_obs(df: pd.DataFrame) -> list[tuple[float, float] | None]:
    """
    Return ob_zones list where ob_zones[i] = (ob_low, ob_high) if bar i is
    the bearish candle of a valid bullish OB, else None.

    Bullish OB at bar j:
      - bar j is bearish (close < open)
      - bar j+1 is displacement: range >= OB_DISPLACEMENT_MULT * atr[j]
        AND close[j+1] > swing_high[j+1]
    """
    close      = df["close"].values
    open_      = df["open"].values
    high       = df["high"].values
    low        = df["low"].values
    atr        = df["atr"].values
    swing_high = df["swing_high"].values
    n          = len(df)

    ob_zones: list[tuple[float, float] | None] = [None] * n

    for j in range(n - 1):
        if close[j] >= open_[j]:          # must be bearish
            continue
        j1 = j + 1
        bar_range = high[j1] - low[j1]
        if np.isnan(atr[j]) or bar_range < config.OB_DISPLACEMENT_MULT * atr[j]:
            continue
        if np.isnan(swing_high[j1]) or close[j1] <= swing_high[j1]:
            continue
        ob_low  = min(open_[j], close[j])
        ob_high = max(open_[j], close[j])
        ob_zones[j] = (ob_low, ob_high)

    return ob_zones


# ── Per-bar signal logic ───────────────────────────────────────────────────────

def _signal_at(
    df: pd.DataFrame,
    ob_zones: list,
    i: int,
) -> tuple[bool, float | None, float | None]:
    """
    Return (is_long, sl, tp) for bar i.
    Uses look-back only — no future data.
    """
    close = df["close"].values
    high  = df["high"].values
    low   = df["low"].values
    ema   = df["ema200"].values

    # 1. Trend filter
    if close[i] <= ema[i]:
        return False, None, None

    # 2. Find most recent active bullish OB whose zone overlaps current bar
    ob_zone = None
    age_limit = max(config.STARTUP_CANDLE, i - config.OB_MAX_AGE_BARS)
    for j in range(i - 1, age_limit, -1):
        if ob_zones[j] is None:
            continue
        ob_low, ob_high = ob_zones[j]
        if low[i] <= ob_high and high[i] >= ob_low:
            ob_zone = (ob_low, ob_high)
            break

    if ob_zone is None:
        return False, None, None

    # 3. Liquidity sweep in last SWEEP_LOOKBACK bars
    sweep_low  = None
    sweep_idx  = None
    look_start = max(0, i - config.SWEEP_LOOKBACK)

    # Reference: the swing low of the 20 bars before the lookback window
    ref_start = max(0, look_start - config.SWING_LOOKBACK)
    ref_low   = np.min(low[ref_start:look_start + 1]) if look_start > ref_start else None

    if ref_low is None:
        return False, None, None

    for k in range(look_start, i + 1):
        if low[k] < ref_low * (1 - config.SWEEP_MIN_PIERCE) and close[k] >= ref_low:
            sweep_low = low[k]
            sweep_idx = k
            # keep the most recent sweep

    if sweep_idx is None:
        return False, None, None

    # 4. CHoCH: after the sweep, close broke a recent swing high
    if sweep_idx >= i:
        return False, None, None
    post_sweep_high = np.max(high[sweep_idx:i]) if sweep_idx < i else 0.0
    if close[i] <= post_sweep_high:
        return False, None, None

    # All conditions met → LONG
    sl    = sweep_low * (1.0 - 0.001)   # 0.1% below the sweep wick
    risk  = close[i] - sl
    if risk <= 0:
        return False, None, None
    tp    = close[i] + risk * config.TARGET_R

    return True, sl, tp


# ── Public: vectorised signal column (used by backtest) ──────────────────────

def get_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds columns signal (1=LONG, 0=flat), sl, tp to df.
    Signals reference bar close; backtest enters at next bar open.
    """
    df = add_indicators(df).copy()
    ob_zones = _precompute_obs(df)

    signals = np.zeros(len(df), dtype=int)
    sl_arr  = np.full(len(df), np.nan)
    tp_arr  = np.full(len(df), np.nan)

    for i in range(config.STARTUP_CANDLE, len(df)):
        is_long, sl, tp = _signal_at(df, ob_zones, i)
        if is_long:
            signals[i] = 1
            sl_arr[i]  = sl
            tp_arr[i]  = tp

    df["signal"] = signals
    df["sl"]     = sl_arr
    df["tp"]     = tp_arr
    return df


# ── Public: latest-bar signal (used by runner) ────────────────────────────────

def get_signal_latest(df: pd.DataFrame) -> dict:
    """
    Compute signal for the last complete bar.
    df must already have all required columns (pass raw OHLCV).
    Returns {'action': 'LONG'|'FLAT', 'sl': float|None, 'tp': float|None}
    """
    df = add_indicators(df)
    if len(df) < config.STARTUP_CANDLE:
        return {"action": "FLAT", "sl": None, "tp": None}

    ob_zones = _precompute_obs(df)
    i        = len(df) - 1
    is_long, sl, tp = _signal_at(df, ob_zones, i)
    return {"action": "LONG" if is_long else "FLAT", "sl": sl, "tp": tp}
