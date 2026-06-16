"""
Phase-0 gate: smc_bot/ 15-step SMC chain on Bybit 1H+5M.

Signal chain (matches smc_bot/bot.py exactly — all 15 workflow steps):
  structure.get_bias  →  fib.fib_filter (50% discount/premium)
  → poi.get_pois → price_in_poi
  → liquidity.get_sweep → check_displacement
  → confirmation.get_choch

Exit model (matches smc_bot/bot.py):
  TP at targets.get_tp_level (BSL/SSL pool ≥ min_r) or fallback_r × risk.
  SL = sweep wick × (1 ± sl_buffer).

Fee model: Bybit taker 0.06%/side = 0.12% round trip (net-of-fees only).

Gate: n ≥ 50 AND net PF > 1.0

Config: read from smc_bot/config.yaml — single source of truth.
No imports from _archive/ in this file.

Performance notes:
  All O(N²) bottlenecks replaced with precomputed arrays + bisect lookups.
  Signal results are mathematically identical to smc_bot/ chain.

Usage:
    python3 scripts/backtest.py
    python3 scripts/backtest.py --htf data/cache/BTCUSDT_60m.parquet \\
                                --ltf data/cache/BTCUSDT_5m.parquet \\
                                --csv data/trial_trades.csv
    # profiling only (do NOT use for real trials):
    python3 scripts/backtest.py --max-bars 5000
"""
from __future__ import annotations

import argparse
import bisect
import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from smc_bot import poi, targets as tgt_mod  # noqa: E402

# ── Config (smc_bot/config.yaml — single source of truth) ────────────────────

def _load_cfg() -> dict:
    with open(ROOT / "smc_bot" / "config.yaml") as f:
        return yaml.safe_load(f)

_CFG      = _load_cfg()
SYMBOL    = _CFG["exchange"]["symbol"]                          # BTCUSDT
SL_BUF    = _CFG["risk"]["sl_buffer"]                          # 0.001
TARGET_R  = _CFG["risk"]["target_r"]                           # 2.0 (fallback)
SWING_N   = _CFG["structure"]["swing_n"]                       # 5
OB_LB     = _CFG["poi"]["ob_lookback"]                         # 50
FVG_LB    = _CFG["poi"]["fvg_lookback"]                        # 30
DISP_ATR  = _CFG["poi"]["displacement_atr"]                    # 1.5 (1H OB)
LIQ_SN    = _CFG["liquidity"]["swing_n"]                       # 3
LIQ_LB    = _CFG["liquidity"]["lookback"]                      # 30
DISP_ATR_LTF = _CFG["liquidity"].get("displacement_atr", 1.5) # 1.5 (5M displacement gate)
CHOCH_LB  = _CFG["confirmation"]["lookback"]                   # 10
FIB_LEVEL = _CFG.get("fib", {}).get("level", 0.5)             # 0.5 = 50% midpoint
TGT_FALLBACK  = _CFG.get("targets", {}).get("fallback_r", 2.0)      # 2.0
TGT_TOLERANCE = _CFG.get("targets", {}).get("equal_level_tolerance", 0.002)  # 0.2%
TGT_MIN_RR    = _CFG.get("targets", {}).get("min_r", 1.5)             # BSL/SSL min R

# Trial 13: BSL/SSL TP — use liquidity pool as TP when pool qualifies at TGT_MIN_RR.
# Set by --bsl-ssl-tp at runtime. When False, uses TGT_FALLBACK × risk (all prior trials).
BSLSSL_TP: bool = False

# Sprint flags (set by CLI args at runtime — all False by default)
MACRO_BIAS:     bool = False   # Sprint 1: 4H macro bias must agree with 1H
PARTIAL_TP:     bool = False   # Sprint 2: 50% close at TP1 R, trail remainder to BE
SESSION_FILTER: bool = False   # Sprint 3: entries only in London/NY kill zones
BOS_CONFIRM:    bool = False   # Sprint 4: require 5M BOS close after CHoCH

TP1_R   = _CFG.get("partials", {}).get("tp1_r",   1.0)   # Sprint 2: first exit R
TP1_PCT = _CFG.get("partials", {}).get("tp1_pct", 0.50)  # Sprint 2: fraction closed at TP1

# Trial 20: re-allow FVG entries (Trial 8 behavior). Set by --fvg-entries at runtime.
# When False (default), only OB zones are valid entry triggers (Trial 11 rule).
FVG_ENTRIES: bool = False

TAKER_FEE  = 0.0006   # Bybit taker 0.06%/side (not in config.yaml — exchange constant)
ROUND_TRIP = TAKER_FEE * 2

# Mitigation threshold — fraction of zone height price must penetrate from the entry side
# before the zone is considered consumed/invalid.  Set by --mitigation-pct at runtime.
#   None = filter disabled (all zones pass through)
#   0.5  = midpoint: 50% of zone consumed kills it
#   0.75 = 75% deep: zone survives a partial retrace, only dies on deep penetration
#   1.0  = fully consumed: zone only dies when price clears its far edge entirely
MITIGATION_PCT: float | None = 0.5

# Mitigation mode — controls which price level is compared against the threshold.
#   "wick"  = wick-based (Trial 9 baseline): low[k] for bullish, high[k] for bearish
#   "close" = close-based (Trial 10): close[k] for both directions
#             At 4H, wick touches midpoint on nearly every retrace; close-based is
#             materially less aggressive — price must CLOSE through the threshold.
MITIGATION_MODE: str = "wick"

# Warmup: enough bars for stable HTF bias + POI detection
HTF_WARMUP = max(OB_LB, FVG_LB, 2 * SWING_N + 1)   # 50 bars
LTF_WARMUP = LIQ_LB + 2 * LIQ_SN + 1               # 37 bars


# ── Precomputed arrays (populated once by _precompute) ────────────────────────

_H_1H: np.ndarray
_L_1H: np.ndarray
_O_1H: np.ndarray
_C_1H: np.ndarray
_SH_1H: list[int]        # 1H swing high indices (sorted asc)
_SL_1H: list[int]        # 1H swing low  indices (sorted asc)
_ATR14_1H: np.ndarray    # ATR14 (Wilder EWM) for every 1H bar

_H_5M: np.ndarray
_L_5M: np.ndarray
_C_5M: np.ndarray
_O_5M: np.ndarray
_SL_5M: np.ndarray       # 5M swing low  indices (sorted, int64) — bisect target
_SH_5M: np.ndarray       # 5M swing high indices (sorted, int64) — bisect target
_ATR14_5M: np.ndarray    # ATR14 (Wilder EWM) for every 5M bar — displacement gate

# Sprint 1: 4H macro bias arrays — populated by _precompute_4h() when --macro-htf given
_H_4H: np.ndarray | None = None
_L_4H: np.ndarray | None = None
_O_4H: np.ndarray | None = None
_C_4H: np.ndarray | None = None
_SH_4H: list[int] = []
_SL_4H: list[int] = []
_HTF4_MAP: np.ndarray | None = None   # 5M index → 4H iloc

# Sprint 3: UTC hour for each 5M bar (precomputed for kill-zone filter)
_HOUR_5M: np.ndarray | None = None


def _swing_lows_np(low: np.ndarray, n: int) -> list[int]:
    """Same logic as smc_bot/structure._swing_lows and smc_bot/liquidity._swing_lows."""
    result = []
    for i in range(n, len(low) - n):
        if low[i] == low[i - n : i + n + 1].min():
            result.append(i)
    return result


def _swing_highs_np(high: np.ndarray, n: int) -> list[int]:
    result = []
    for i in range(n, len(high) - n):
        if high[i] == high[i - n : i + n + 1].max():
            result.append(i)
    return result


def _atr14_series(df: pd.DataFrame) -> np.ndarray:
    """ATR14 (Wilder EWM) for every bar — same formula as smc_bot/poi._atr14."""
    prev = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"]  - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=14, adjust=False).mean().values


def _precompute(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> None:
    global _H_1H, _L_1H, _O_1H, _C_1H, _SH_1H, _SL_1H, _ATR14_1H
    global _H_5M, _L_5M, _C_5M, _O_5M, _SL_5M, _SH_5M, _ATR14_5M
    global _HOUR_5M

    print("  Precomputing 1H swings + ATR14 …", flush=True)
    _H_1H = df_1h["high"].values
    _L_1H = df_1h["low"].values
    _O_1H = df_1h["open"].values
    _C_1H = df_1h["close"].values
    _SH_1H = _swing_highs_np(_H_1H, SWING_N)
    _SL_1H = _swing_lows_np(_L_1H, SWING_N)
    _ATR14_1H = _atr14_series(df_1h)

    print("  Precomputing 5M swings + ATR14 …", flush=True)
    _H_5M = df_5m["high"].values
    _L_5M = df_5m["low"].values
    _C_5M = df_5m["close"].values
    _O_5M = df_5m["open"].values
    _SL_5M = np.array(_swing_lows_np(_L_5M,  LIQ_SN), dtype=np.int64)
    _SH_5M = np.array(_swing_highs_np(_H_5M, LIQ_SN), dtype=np.int64)
    _ATR14_5M = _atr14_series(df_5m)

    # Sprint 3: precompute UTC hour per 5M bar for kill-zone session filter
    if "ts" in df_5m.columns:
        _HOUR_5M = pd.to_datetime(df_5m["ts"]).dt.hour.values.astype(np.int8)
    else:
        _HOUR_5M = None


def _precompute_4h(df_4h: pd.DataFrame, df_5m: pd.DataFrame) -> None:
    """Sprint 1: precompute 4H swing arrays and 5M→4H alignment map."""
    global _H_4H, _L_4H, _O_4H, _C_4H, _SH_4H, _SL_4H, _HTF4_MAP
    print("  Precomputing 4H swings (macro bias) …", flush=True)
    _H_4H = df_4h["high"].values
    _L_4H = df_4h["low"].values
    _O_4H = df_4h["open"].values
    _C_4H = df_4h["close"].values
    _SH_4H = _swing_highs_np(_H_4H, SWING_N)
    _SL_4H = _swing_lows_np(_L_4H, SWING_N)

    htf4_ts = df_4h["ts"].values
    ltf_ts  = df_5m["ts"].values
    result  = np.full(len(ltf_ts), -1, dtype=int)
    for i, ts in enumerate(ltf_ts):
        idx = int(np.searchsorted(htf4_ts, ts, side="left")) - 1
        result[i] = idx if idx >= HTF_WARMUP else -1
    _HTF4_MAP = result


# ── Fast signal functions (O(1)–O(lookback) each) ────────────────────────────

def _fast_bias(htf_idx: int) -> str:
    """
    O(log N) replacement for structure.get_bias(df_1h.iloc[:htf_idx+1], swing_n=SWING_N).

    Confirmed swing at index j requires SWING_N bars after it, so j ≤ htf_idx − SWING_N.
    This matches the growing-window exclusion: range(n, len(high)-n) where n=SWING_N.
    """
    max_conf = htf_idx - SWING_N
    sh_end   = bisect.bisect_right(_SH_1H, max_conf)
    sl_end   = bisect.bisect_right(_SL_1H, max_conf)
    if sh_end < 2 or sl_end < 2:
        return "neutral"
    hh = _H_1H[_SH_1H[sh_end - 1]] > _H_1H[_SH_1H[sh_end - 2]]
    hl = _L_1H[_SL_1H[sl_end - 1]] > _L_1H[_SL_1H[sl_end - 2]]
    lh = _H_1H[_SH_1H[sh_end - 1]] < _H_1H[_SH_1H[sh_end - 2]]
    ll = _L_1H[_SL_1H[sl_end - 1]] < _L_1H[_SL_1H[sl_end - 2]]
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "neutral"


def _fast_bias_4h(htf4_idx: int) -> str:
    """Sprint 1 — macro bias from 4H swings (O(log N), same logic as _fast_bias)."""
    if _H_4H is None or not _SH_4H or not _SL_4H:
        return "neutral"
    max_conf = htf4_idx - SWING_N
    sh_end   = bisect.bisect_right(_SH_4H, max_conf)
    sl_end   = bisect.bisect_right(_SL_4H, max_conf)
    if sh_end < 2 or sl_end < 2:
        return "neutral"
    hh = _H_4H[_SH_4H[sh_end - 1]] > _H_4H[_SH_4H[sh_end - 2]]
    hl = _L_4H[_SL_4H[sl_end - 1]] > _L_4H[_SL_4H[sl_end - 2]]
    lh = _H_4H[_SH_4H[sh_end - 1]] < _H_4H[_SH_4H[sh_end - 2]]
    ll = _L_4H[_SL_4H[sl_end - 1]] < _L_4H[_SL_4H[sl_end - 2]]
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "neutral"


def _fast_filter_fresh(zones: list[dict], htf_idx: int, bias: str) -> tuple[list[dict], int]:
    """
    Mitigation filter — inline fast version of poi.filter_fresh_zones.

    MITIGATION_PCT (module global):
      None → disabled; all zones pass
      0.5  → 50% consumed from entry edge
      0.75 → 75% consumed
      1.0  → zone must be fully consumed

    MITIGATION_MODE (module global):
      "wick"  → bullish: low[k] ≤ threshold; bearish: high[k] ≥ threshold
      "close" → bullish: close[k] ≤ threshold; bearish: close[k] ≥ threshold

    Threshold: bullish = zone.high − PCT × range; bearish = zone.low + PCT × range

    Returns (fresh_zones, rejected_count).
    """
    if MITIGATION_PCT is None:
        return zones, 0
    fresh, rejected = [], 0
    use_close = (MITIGATION_MODE == "close")
    for z in zones:
        zone_range = z["high"] - z["low"]
        if bias == "bullish":
            threshold = z["high"] - MITIGATION_PCT * zone_range
        else:
            threshold = z["low"]  + MITIGATION_PCT * zone_range
        cb = z["creation_bar"]
        mitigated = False
        for k in range(cb + 1, htf_idx + 1):
            if use_close:
                if bias == "bullish" and _C_1H[k] <= threshold:
                    mitigated = True
                    break
                if bias == "bearish" and _C_1H[k] >= threshold:
                    mitigated = True
                    break
            else:
                if bias == "bullish" and _L_1H[k] <= threshold:
                    mitigated = True
                    break
                if bias == "bearish" and _H_1H[k] >= threshold:
                    mitigated = True
                    break
        if mitigated:
            rejected += 1
        else:
            fresh.append(z)
    return fresh, rejected


def _fast_pois_raw(htf_idx: int, bias: str) -> list[dict]:
    """Zone detection only — no mitigation filter. Used by count_funnel for stats."""
    if bias == "neutral":
        return []
    n   = htf_idx + 1
    h   = _H_1H[:n]
    l   = _L_1H[:n]
    o   = _O_1H[:n]
    c   = _C_1H[:n]
    atr = float(_ATR14_1H[htf_idx])
    zones: list[dict] = []

    if bias == "bullish":
        start = max(0, n - OB_LB)
        for j in range(start, n - 1):
            if c[j] >= o[j]:
                continue
            j1 = j + 1
            if c[j1] > o[j1] and (h[j1] - l[j1]) >= DISP_ATR * atr:
                zones.append({"kind": "OB", "low": float(min(o[j], c[j])),
                               "high": float(max(o[j], c[j])), "creation_bar": j})
        start = max(2, n - FVG_LB)
        for i in range(start, n):
            fvg_lo, fvg_hi = float(h[i - 2]), float(l[i])
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi, "creation_bar": i})

    elif bias == "bearish":
        start = max(0, n - OB_LB)
        for j in range(start, n - 1):
            if c[j] <= o[j]:
                continue
            j1 = j + 1
            if c[j1] < o[j1] and (h[j1] - l[j1]) >= DISP_ATR * atr:
                zones.append({"kind": "OB", "low": float(min(o[j], c[j])),
                               "high": float(max(o[j], c[j])), "creation_bar": j})
        start = max(2, n - FVG_LB)
        for i in range(start, n):
            fvg_hi, fvg_lo = float(l[i - 2]), float(h[i])
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi, "creation_bar": i})

    return zones


def _fast_pois(htf_idx: int, bias: str) -> list[dict]:
    """
    O(OB_LB + FVG_LB) replacement for poi.get_pois(df_1h.iloc[:htf_idx+1], bias, ...).

    Uses _ATR14_1H[htf_idx] instead of recomputing EWM over the full growing slice.
    Logic is identical to smc_bot/poi.py:get_pois — verified line-by-line.
    Includes Trial 6 mitigation filter via _fast_filter_fresh.
    """
    zones, _ = _fast_filter_fresh(_fast_pois_raw(htf_idx, bias), htf_idx, bias)
    return zones


def _fast_sweep(i: int) -> dict | None:
    """
    O(n_candidates × lookback) replacement for
    liquidity.get_sweep(df_5m.iloc[:i+1], bias, LIQ_LB, LIQ_SN).

    Matches the original exactly:
      n = i + 1  (same as len(df) in the original)
      scan_start = max(LIQ_SN * 2 + 1, n - LIQ_LB)
      candidates = confirmed swing lows in [scan_start, i - LIQ_SN]
      sweep search: range(sl_idx + 1, n), most-recent swing first
    """
    n          = i + 1
    scan_start = max(LIQ_SN * 2 + 1, n - LIQ_LB)
    max_conf   = i - LIQ_SN   # confirmed swing low must have LIQ_SN right-side bars
    if max_conf < scan_start:
        return None
    left  = bisect.bisect_left(_SL_5M, scan_start)
    right = bisect.bisect_right(_SL_5M, max_conf)
    for sl_idx in _SL_5M[left:right][::-1]:   # most-recent first
        level = _L_5M[sl_idx]
        for k in range(int(sl_idx) + 1, n):
            if _L_5M[k] < level and _C_5M[k] > level:
                return {
                    "bar_idx":      int(k),
                    "swept_level":  float(level),
                    "wick_extreme": float(_L_5M[k]),
                }
    return None


def _fast_choch(sweep: dict, i: int) -> bool:
    """
    O(CHOCH_LB) replacement for
    confirmation.get_choch(df_5m.iloc[:i+1], "bullish", sweep, CHOCH_LB).

    Matches the original: sweep_bar >= n-1 → n-1 = i; last close = close[-1] = _C_5M[i].
    """
    sweep_bar = sweep["bar_idx"]
    if sweep_bar >= i:
        return False
    ref_start = max(0, sweep_bar - CHOCH_LB)
    ref_level = float(_H_5M[ref_start : sweep_bar + 1].max())
    return bool(_C_5M[i] > ref_level)


def _fast_sweep_short(i: int) -> dict | None:
    """
    BSL sweep (short setup): swing high wicked above and closed back below.
    Mirror of _fast_sweep — uses _SH_5M instead of _SL_5M.
    """
    n          = i + 1
    scan_start = max(LIQ_SN * 2 + 1, n - LIQ_LB)
    max_conf   = i - LIQ_SN
    if max_conf < scan_start:
        return None
    left  = bisect.bisect_left(_SH_5M, scan_start)
    right = bisect.bisect_right(_SH_5M, max_conf)
    for sh_idx in _SH_5M[left:right][::-1]:   # most-recent first
        level = _H_5M[sh_idx]
        for k in range(int(sh_idx) + 1, n):
            if _H_5M[k] > level and _C_5M[k] < level:
                return {
                    "bar_idx":      int(k),
                    "swept_level":  float(level),
                    "wick_extreme": float(_H_5M[k]),
                }
    return None


def _fast_choch_short(sweep: dict, i: int) -> bool:
    """CHoCH for short: close breaks below ref low after BSL sweep."""
    sweep_bar = sweep["bar_idx"]
    if sweep_bar >= i:
        return False
    ref_start = max(0, sweep_bar - CHOCH_LB)
    ref_level = float(_L_5M[ref_start : sweep_bar + 1].min())
    return bool(_C_5M[i] < ref_level)


def _fast_bos(choch_bar: int, bias: str, lookforward: int = 20) -> int | None:
    """
    Sprint 4 — 5M BOS after CHoCH.

    Scans from choch_bar+1 for a close that breaks the CHoCH candle's extreme:
      bullish → close > high[choch_bar]
      bearish → close < low[choch_bar]
    Returns bar index of the BOS candle, or None if not found within lookforward bars.
    """
    limit = min(choch_bar + lookforward + 1, len(_C_5M))
    if bias == "bullish":
        ref = float(_H_5M[choch_bar])
        for j in range(choch_bar + 1, limit):
            if _C_5M[j] > ref:
                return j
    else:
        ref = float(_L_5M[choch_bar])
        for j in range(choch_bar + 1, limit):
            if _C_5M[j] < ref:
                return j
    return None


def _in_kill_zone(ltf_idx: int) -> bool:
    """Sprint 3 — True if 5M bar falls in London (08-15 UTC) or NY (13-21 UTC)."""
    if _HOUR_5M is None:
        return True
    h = int(_HOUR_5M[ltf_idx])
    return (8 <= h <= 15) or (13 <= h <= 21)


def _fast_fib_filter(htf_idx: int, price: float, bias: str) -> bool:
    """
    Step 3 — Fib 50% discount/premium gate (matches smc_bot/fib.py).

    Computes midpoint = (last_swing_high + last_swing_low) / 2 using precomputed
    confirmed-swing indices.  Returns True only when price is in the correct half:
      bullish → discount (price ≤ midpoint)
      bearish → premium  (price ≥ midpoint)
    """
    max_conf = htf_idx - SWING_N
    sh_end   = bisect.bisect_right(_SH_1H, max_conf)
    sl_end   = bisect.bisect_right(_SL_1H, max_conf)
    if sh_end < 1 or sl_end < 1:
        return False   # not enough swing history → skip (conservative)
    mid = (_H_1H[_SH_1H[sh_end - 1]] + _L_1H[_SL_1H[sl_end - 1]]) / 2.0
    if bias == "bullish":
        return bool(price <= mid)
    if bias == "bearish":
        return bool(price >= mid)
    return False


def _fast_displacement(sweep_bar: int, i: int, bias: str) -> bool:
    """
    Step 9 — Post-sweep displacement gate (matches smc_bot/liquidity.check_displacement).

    Scans bars in [sweep_bar+1, i] for a candle with:
      • range ≥ DISP_ATR_LTF × ATR14_5M[i]   (strong move)
      • body in trade direction (bullish body for long, bearish for short)
    Uses _ATR14_5M[i] (ATR at bar i) as the reference — stable and fast.
    """
    atr = float(_ATR14_5M[i])
    for k in range(sweep_bar + 1, i + 1):
        if (_H_5M[k] - _L_5M[k]) < DISP_ATR_LTF * atr:
            continue
        if bias == "bullish" and _C_5M[k] > _O_5M[k]:
            return True
        if bias == "bearish" and _C_5M[k] < _O_5M[k]:
            return True
    return False


# ── HTF alignment ─────────────────────────────────────────────────────────────

def _align_htf(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> np.ndarray:
    """For each 5M bar return the iloc of the last complete 1H bar (−1 if warmup not met)."""
    htf_ts = df_1h["ts"].values
    ltf_ts = df_5m["ts"].values
    result = np.full(len(ltf_ts), -1, dtype=int)
    for i, ts in enumerate(ltf_ts):
        idx = int(np.searchsorted(htf_ts, ts, side="left")) - 1
        result[i] = idx if idx >= HTF_WARMUP else -1
    return result


# ── Exit simulation ───────────────────────────────────────────────────────────

def _scan_exit(
    high: np.ndarray,
    low:  np.ndarray,
    entry_bar: int,
    sl: float,
    tp: float,
) -> tuple[float, str, int]:
    """First SL or TP hit from entry_bar onward (long). Returns (price, reason, bar)."""
    for k in range(entry_bar, len(high)):
        if low[k] <= sl:
            return sl, "SL", k
        if high[k] >= tp:
            return tp, "TP", k
    return sl, "EOD-SL", len(high) - 1  # conservative: treat open EOD as SL


def _scan_exit_short(
    high: np.ndarray,
    low:  np.ndarray,
    entry_bar: int,
    sl: float,
    tp: float,
) -> tuple[float, str, int]:
    """First SL or TP hit from entry_bar onward (short). Returns (price, reason, bar)."""
    for k in range(entry_bar, len(high)):
        if high[k] >= sl:
            return sl, "SL", k
        if low[k] <= tp:
            return tp, "TP", k
    return sl, "EOD-SL", len(high) - 1


def _scan_exit_partial(
    high: np.ndarray,
    low:  np.ndarray,
    entry_bar: int,
    entry_price: float,
    sl: float,
    tp1: float,
    tp_full: float,
    tp_full_r: float,
) -> tuple[float, str, int]:
    """
    Sprint 2 — two-leg partial TP exit for longs.

    Leg 1 (full size): scan for SL or TP1.
      SL hit  → gross_r = -1.0
      TP1 hit → close TP1_PCT at TP1_R, move SL to entry_price (break-even)
    Leg 2 (remaining 1-TP1_PCT): scan from TP1 bar for BE or tp_full.
      BE hit      → gross_r = TP1_PCT×TP1_R + (1-TP1_PCT)×0.0
      tp_full hit → gross_r = TP1_PCT×TP1_R + (1-TP1_PCT)×tp_full_r
      EOD         → treat as BE
    """
    for k in range(entry_bar, len(high)):
        if low[k] <= sl:
            return -1.0, "SL", k
        if high[k] >= tp1:
            for k2 in range(k, len(high)):
                if low[k2] <= entry_price:
                    gross = TP1_PCT * TP1_R + (1.0 - TP1_PCT) * 0.0
                    return gross, "TP1+BE", k2
                if high[k2] >= tp_full:
                    gross = TP1_PCT * TP1_R + (1.0 - TP1_PCT) * tp_full_r
                    return gross, "TP1+TP", k2
            gross = TP1_PCT * TP1_R + (1.0 - TP1_PCT) * 0.0
            return gross, "TP1+EOD", len(high) - 1
    return -1.0, "EOD-SL", len(high) - 1


def _scan_exit_partial_short(
    high: np.ndarray,
    low:  np.ndarray,
    entry_bar: int,
    entry_price: float,
    sl: float,
    tp1: float,
    tp_full: float,
    tp_full_r: float,
) -> tuple[float, str, int]:
    """Sprint 2 — two-leg partial TP exit for shorts (mirror of long version)."""
    for k in range(entry_bar, len(high)):
        if high[k] >= sl:
            return -1.0, "SL", k
        if low[k] <= tp1:
            for k2 in range(k, len(high)):
                if high[k2] >= entry_price:
                    gross = TP1_PCT * TP1_R + (1.0 - TP1_PCT) * 0.0
                    return gross, "TP1+BE", k2
                if low[k2] <= tp_full:
                    gross = TP1_PCT * TP1_R + (1.0 - TP1_PCT) * tp_full_r
                    return gross, "TP1+TP", k2
            gross = TP1_PCT * TP1_R + (1.0 - TP1_PCT) * 0.0
            return gross, "TP1+EOD", len(high) - 1
    return -1.0, "EOD-SL", len(high) - 1


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(df_1h: pd.DataFrame, df_5m: pd.DataFrame, side: str = "both") -> dict:
    htf_map    = _align_htf(df_1h, df_5m)
    open_5m    = _O_5M
    high_5m    = _H_5M
    low_5m     = _L_5M

    _htf_cache:       dict[int, tuple] = {}
    _htf4_bias_cache: dict[int, str]   = {}   # Sprint 1: 4H bias per 4H bar
    trades: list[dict] = []
    skip_until    = 0
    no_pool_skip  = 0

    for i in range(LTF_WARMUP, len(df_5m) - 1):
        if i < skip_until:
            continue

        htf_idx = int(htf_map[i])
        if htf_idx < 0:
            continue

        if htf_idx not in _htf_cache:
            bias_val = _fast_bias(htf_idx)
            pois_val = _fast_pois(htf_idx, bias_val)
            _htf_cache[htf_idx] = (bias_val, pois_val)

        bias, pois = _htf_cache[htf_idx]

        if bias == "neutral":
            continue
        if side == "long"  and bias != "bullish":
            continue
        if side == "short" and bias != "bearish":
            continue

        # Sprint 1: 4H macro bias must agree with 1H
        if MACRO_BIAS and _HTF4_MAP is not None:
            htf4_idx = int(_HTF4_MAP[i])
            if htf4_idx < 0:
                continue
            if htf4_idx not in _htf4_bias_cache:
                _htf4_bias_cache[htf4_idx] = _fast_bias_4h(htf4_idx)
            if _htf4_bias_cache[htf4_idx] != bias:
                continue

        price = float(_C_5M[i])

        if not _fast_fib_filter(htf_idx, price, bias):
            continue

        if FVG_ENTRIES:
            if poi.price_in_poi(price, pois) is None:
                continue
        else:
            if poi.ob_for_price(price, pois) is None:
                continue

        # Sprint 3: kill zone session filter (before sweep — cheap early exit)
        if SESSION_FILTER and not _in_kill_zone(i):
            continue

        if bias == "bullish":
            sweep = _fast_sweep(i)
            if sweep is None:
                continue
            if not _fast_displacement(sweep["bar_idx"], i, bias):
                continue
            if not _fast_choch(sweep, i):
                continue

            # Sprint 4: require 5M BOS close after CHoCH
            if BOS_CONFIRM:
                bos_bar = _fast_bos(i, bias)
                if bos_bar is None:
                    continue
                entry_bar = bos_bar + 1
            else:
                entry_bar = i + 1

            if entry_bar >= len(df_5m):
                continue

            entry_price = float(open_5m[entry_bar])
            sl          = sweep["wick_extreme"] * (1.0 - SL_BUF)
            stop_dist   = entry_price - sl
            if stop_dist <= 0:
                continue

            tp_full_r = TGT_FALLBACK
            if BSLSSL_TP:
                _pool = tgt_mod.get_tp_level(
                    df_1h.iloc[: htf_idx + 1], "bullish", entry_price, stop_dist,
                    swing_n=SWING_N, tolerance=TGT_TOLERANCE, min_r=TGT_MIN_RR,
                )
                if _pool is not None:
                    tp_full   = _pool
                    tp_full_r = (_pool - entry_price) / stop_dist
                else:
                    tp_full = entry_price + TGT_FALLBACK * stop_dist
                    no_pool_skip += 1
            else:
                tp_full = entry_price + TGT_FALLBACK * stop_dist

            if PARTIAL_TP:
                tp1 = entry_price + TP1_R * stop_dist
                gross_r, exit_reason, exit_bar = _scan_exit_partial(
                    high_5m, low_5m, entry_bar, entry_price, sl, tp1, tp_full, tp_full_r,
                )
            else:
                exit_price, exit_reason, exit_bar = _scan_exit(
                    high_5m, low_5m, entry_bar, sl, tp_full,
                )
                gross_r = (exit_price - entry_price) / stop_dist
            trade_side = "long"

        else:  # bearish
            sweep = _fast_sweep_short(i)
            if sweep is None:
                continue
            if not _fast_displacement(sweep["bar_idx"], i, bias):
                continue
            if not _fast_choch_short(sweep, i):
                continue

            # Sprint 4: require 5M BOS close after CHoCH
            if BOS_CONFIRM:
                bos_bar = _fast_bos(i, bias)
                if bos_bar is None:
                    continue
                entry_bar = bos_bar + 1
            else:
                entry_bar = i + 1

            if entry_bar >= len(df_5m):
                continue

            entry_price = float(open_5m[entry_bar])
            sl          = sweep["wick_extreme"] * (1.0 + SL_BUF)
            stop_dist   = sl - entry_price
            if stop_dist <= 0:
                continue

            tp_full_r = TGT_FALLBACK
            if BSLSSL_TP:
                _pool = tgt_mod.get_tp_level(
                    df_1h.iloc[: htf_idx + 1], "bearish", entry_price, stop_dist,
                    swing_n=SWING_N, tolerance=TGT_TOLERANCE, min_r=TGT_MIN_RR,
                )
                if _pool is not None:
                    tp_full   = _pool
                    tp_full_r = (entry_price - _pool) / stop_dist
                else:
                    tp_full = entry_price - TGT_FALLBACK * stop_dist
                    no_pool_skip += 1
            else:
                tp_full = entry_price - TGT_FALLBACK * stop_dist

            if PARTIAL_TP:
                tp1 = entry_price - TP1_R * stop_dist
                gross_r, exit_reason, exit_bar = _scan_exit_partial_short(
                    high_5m, low_5m, entry_bar, entry_price, sl, tp1, tp_full, tp_full_r,
                )
            else:
                exit_price, exit_reason, exit_bar = _scan_exit_short(
                    high_5m, low_5m, entry_bar, sl, tp_full,
                )
                gross_r = (entry_price - exit_price) / stop_dist
            trade_side = "short"

        fee_r = ROUND_TRIP * entry_price / stop_dist
        net_r = gross_r - fee_r

        trades.append({
            "entry_bar": entry_bar,
            "exit_bar":  exit_bar,
            "side":      trade_side,
            "ts":        str(df_5m["ts"].iloc[i]) if "ts" in df_5m.columns else i,
            "entry":     round(entry_price, 2),
            "sl":        round(sl, 2),
            "tp":        round(tp_full, 2),
            "gross_r":   round(gross_r, 4),
            "fee_r":     round(fee_r, 4),
            "net_r":     round(net_r, 4),
            "reason":    exit_reason,
        })
        skip_until = exit_bar + 1

    if not trades:
        return {
            "n": 0, "gross_pf": 0.0, "net_pf": 0.0,
            "win_rate": 0.0, "avg_fee_r": 0.0, "max_dd_r": 0.0,
            "no_pool_skip": no_pool_skip, "trades": [],
        }

    gross_wins = sum(t["gross_r"] for t in trades if t["gross_r"] > 0)
    gross_loss = abs(sum(t["gross_r"] for t in trades if t["gross_r"] <= 0))
    net_wins   = sum(t["net_r"]   for t in trades if t["net_r"]   > 0)
    net_loss   = abs(sum(t["net_r"]   for t in trades if t["net_r"]   <= 0))
    wins       = sum(1 for t in trades if t["net_r"] > 0)

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["net_r"]
        if equity > peak:
            peak = equity
        max_dd = max(max_dd, peak - equity)

    n = len(trades)
    expectancy = sum(t["net_r"] for t in trades) / n

    return {
        "n":          n,
        "gross_pf":   round(gross_wins / gross_loss, 4) if gross_loss else float("inf"),
        "net_pf":     round(net_wins   / net_loss,   4) if net_loss   else float("inf"),
        "win_rate":   round(wins / n * 100, 2),
        "avg_fee_r":  round(sum(t["fee_r"] for t in trades) / n, 4),
        "expectancy": round(expectancy, 4),
        "max_dd_r":     round(max_dd, 4),
        "no_pool_skip": no_pool_skip,
        "trades":       trades,
    }


# ── Phase-C report ────────────────────────────────────────────────────────────

def print_report(
    stats: dict,
    run_label: str = "Trial X",
    htf_label: str = "1H",
    ltf_label: str = "5M",
    side: str = "both",
) -> None:
    n, gross_pf, net_pf = stats["n"], stats["gross_pf"], stats["net_pf"]
    print("\n" + "=" * 60)
    print(f"  SMC Bot — Phase-0 Gate  ({run_label}: smc_bot/ 15-step chain)")
    print("=" * 60)
    mit_label = ("OFF" if MITIGATION_PCT is None
                 else f"{MITIGATION_PCT*100:.0f}% ({MITIGATION_MODE}-based)")
    sprint_flags = []
    if MACRO_BIAS:     sprint_flags.append("4H-macro-bias")
    if PARTIAL_TP:     sprint_flags.append(f"partial-TP({TP1_PCT*100:.0f}%@{TP1_R}R+BE)")
    if SESSION_FILTER: sprint_flags.append("kill-zone")
    if BOS_CONFIRM:    sprint_flags.append("BOS-confirm")
    print(f"  Signal  : {htf_label} bias+Fib+OB/FVG → {ltf_label} sweep+disp+CHoCH")
    print(f"  Symbol  : {SYMBOL}  HTF={htf_label}  LTF={ltf_label}  side={side}")
    print(f"  Mitig.  : {mit_label}")
    if sprint_flags:
        print(f"  Sprints : {' | '.join(sprint_flags)}")
    if BSLSSL_TP:
        no_pool = stats.get("no_pool_skip", 0)
        n_total = stats.get("n", 0)
        n_pool  = n_total - no_pool
        print(f"  Exit    : BSL/SSL pool TP (min_r={TGT_MIN_RR}) | fallback {TGT_FALLBACK}R  SL=sweep-wick±{SL_BUF*100:.1f}%")
        if n_total:
            print(f"  Pool TP : {n_pool} pool-based  {no_pool} fallback ({no_pool/n_total*100:.0f}% no pool found)")
    else:
        print(f"  Exit    : TP={TGT_FALLBACK}R fallback  SL=sweep-wick±{SL_BUF*100:.1f}%")
    print(f"  Fee     : Bybit taker {TAKER_FEE*100:.2f}%/side = {ROUND_TRIP*100:.2f}% round-trip")
    print("-" * 60)
    print(f"  Trades  : {n}")
    print(f"  Win rate: {stats['win_rate']:.1f}%")
    print(f"  Gross PF: {gross_pf:.4f}")
    print(f"  Avg fee : {stats['avg_fee_r']:.4f} R")
    print(f"  Net PF  : {net_pf:.4f}")
    print(f"  Expect. : {stats.get('expectancy', 0.0):+.4f} R/trade")
    print(f"  Max DD  : {stats['max_dd_r']:.4f} R")
    print("-" * 60)

    gate_n  = n >= 50
    gate_pf = net_pf > 1.0
    verdict = "PASS" if (gate_n and gate_pf) else "FAIL"

    print(f"  Gate n≥50    : {'PASS' if gate_n  else 'FAIL'}  (n={n})")
    print(f"  Gate net PF>1: {'PASS' if gate_pf else 'FAIL'}  (net PF={net_pf})")
    print(f"\n  VERDICT: {verdict}")
    print("=" * 60)

    # ── Per-year breakdown ────────────────────────────────────────────────────
    trades = stats.get("trades", [])
    if trades:
        by_year: dict[int, list] = {}
        for t in trades:
            try:
                yr = int(str(t["ts"])[:4])
            except (ValueError, TypeError):
                yr = 0
            by_year.setdefault(yr, []).append(t)

        print(f"\n  {'Year':<6}  {'n':>4}  {'Win%':>5}  {'Gross PF':>8}  {'Net PF':>7}  {'Expect':>7}")
        print(f"  {'-'*6}  {'-'*4}  {'-'*5}  {'-'*8}  {'-'*7}  {'-'*7}")
        for yr in sorted(by_year):
            yt  = by_year[yr]
            yn  = len(yt)
            yw  = sum(1 for t in yt if t["net_r"] > 0)
            ywin = yw / yn * 100 if yn else 0.0
            ygw  = sum(t["gross_r"] for t in yt if t["gross_r"] > 0)
            ygl  = abs(sum(t["gross_r"] for t in yt if t["gross_r"] <= 0))
            ynw  = sum(t["net_r"] for t in yt if t["net_r"] > 0)
            ynl  = abs(sum(t["net_r"] for t in yt if t["net_r"] <= 0))
            ygpf = ygw / ygl if ygl else float("inf")
            ynpf = ynw / ynl if ynl else float("inf")
            yexp = sum(t["net_r"] for t in yt) / yn
            print(f"  {yr:<6}  {yn:>4}  {ywin:>4.0f}%  {ygpf:>8.3f}  {ynpf:>7.3f}  {yexp:>+7.3f}")

    if verdict == "PASS":
        print(f"\n  → Phase-0 cleared. Log {run_label} in VERDICT_LOG.md.")
        print("  → Proceed to Phase-1 paper trade (30 days, 100+ trades).")
    else:
        print(f"\n  → Gate FAILED. Log {run_label}. Change signal family — do not tune.")
    print()


# ── Phase-D funnel ────────────────────────────────────────────────────────────

def count_funnel(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> dict:
    """
    Count candidates surviving each AND-gated stage — diagnosis only.
    Stages match bot.py's 15-step signal chain exactly.
    Includes Trial 6 zone-level mitigation stats.
    """
    htf_map = _align_htf(df_1h, df_5m)

    counts = {
        "total_bars":       0,
        "bias":             0,
        "macro_bias":       0,   # Sprint 1: 4H macro bias agrees with 1H
        "fib":              0,
        "poi_raw":          0,   # bars touching any zone before mitigation filter
        "poi":              0,   # bars touching a FRESH zone after mitigation filter
        "session":          0,   # Sprint 3: bar inside London/NY kill zone
        "sweep":            0,
        "displacement":     0,
        "choch":            0,
        "bos":              0,   # Sprint 4: 5M BOS close after CHoCH
        "pool":             0,   # BSL/SSL qualifying pool found (Trial 13, only when BSLSSL_TP)
        "zones_total":      0,
        "zones_mitigated":  0,
    }

    # cache: htf_idx → (bias, raw_zones, fresh_zones)
    _htf_cache: dict[int, tuple] = {}

    for i in range(LTF_WARMUP, len(df_5m) - 1):
        htf_idx = int(htf_map[i])
        if htf_idx < 0:
            continue

        counts["total_bars"] += 1

        if htf_idx not in _htf_cache:
            bias_val  = _fast_bias(htf_idx)
            raw_zones = _fast_pois_raw(htf_idx, bias_val)
            fresh, rejected = _fast_filter_fresh(raw_zones, htf_idx, bias_val)
            _htf_cache[htf_idx] = (bias_val, raw_zones, fresh)
            counts["zones_total"]     += len(raw_zones)
            counts["zones_mitigated"] += rejected

        bias, raw_pois, pois = _htf_cache[htf_idx]

        if bias == "neutral":
            continue
        counts["bias"] += 1

        # Sprint 1: 4H macro bias gate (funnel diagnostic)
        if MACRO_BIAS and _HTF4_MAP is not None:
            htf4_idx = int(_HTF4_MAP[i])
            if htf4_idx < 0:
                continue
            if _fast_bias_4h(htf4_idx) != bias:
                continue
        counts["macro_bias"] += 1

        price = float(_C_5M[i])

        if not _fast_fib_filter(htf_idx, price, bias):
            continue
        counts["fib"] += 1

        # poi_raw: bar touches any OB zone pre-mitigation (OB-only per diagram rule)
        if any(z.get("kind") == "OB" and z["low"] <= price <= z["high"] for z in raw_pois):
            counts["poi_raw"] += 1

        # poi: price in zone (OB only by default; OB+FVG when --fvg-entries)
        if FVG_ENTRIES:
            if not any(z["low"] <= price <= z["high"] for z in pois):
                continue
        else:
            if not any(z.get("kind") == "OB" and z["low"] <= price <= z["high"] for z in pois):
                continue
        counts["poi"] += 1

        # Sprint 3: kill zone session filter (funnel diagnostic)
        if SESSION_FILTER and not _in_kill_zone(i):
            continue
        counts["session"] += 1

        sweep = _fast_sweep(i) if bias == "bullish" else _fast_sweep_short(i)
        if sweep is None:
            continue
        counts["sweep"] += 1

        if not _fast_displacement(sweep["bar_idx"], i, bias):
            continue
        counts["displacement"] += 1

        choch = _fast_choch(sweep, i) if bias == "bullish" else _fast_choch_short(sweep, i)
        if not choch:
            continue
        counts["choch"] += 1

        # Sprint 4: BOS after CHoCH (funnel diagnostic)
        if BOS_CONFIRM:
            if _fast_bos(i, bias) is None:
                continue
        counts["bos"] += 1

        # Trial 13 pool stage: BSL/SSL cluster at min_r (funnel diagnostic only)
        if BSLSSL_TP:
            _ep  = float(_C_5M[i])   # proxy for entry (actual = next bar open)
            _sl  = (sweep["wick_extreme"] * (1.0 - SL_BUF) if bias == "bullish"
                    else sweep["wick_extreme"] * (1.0 + SL_BUF))
            _sd  = abs(_ep - _sl)
            if _sd > 0:
                _pool = tgt_mod.get_tp_level(
                    df_1h.iloc[: htf_idx + 1], bias, _ep, _sd,
                    swing_n=SWING_N, tolerance=TGT_TOLERANCE, min_r=TGT_MIN_RR,
                )
                if _pool is not None:
                    counts["pool"] += 1

    return counts


def print_funnel(counts: dict) -> None:
    total  = counts["total_bars"]
    stages = [("bias", "1H swing bias non-neutral")]
    if MACRO_BIAS:
        stages.append(("macro_bias", "4H macro bias agrees (Sprint 1)"))
    stages += [
        ("fib",  "Fib 50% discount/premium"),
        ("poi",  "Price inside FRESH 1H OB/FVG"),
    ]
    if SESSION_FILTER:
        stages.append(("session", "Kill zone: London/NY (Sprint 3)"))
    stages += [
        ("sweep",        "5M liquidity sweep"),
        ("displacement", "5M displacement candle"),
        ("choch",        "5M CHoCH confirmed"),
    ]
    if BOS_CONFIRM:
        stages.append(("bos", "5M BOS after CHoCH (Sprint 4)"))
    if BSLSSL_TP:
        stages.append(("pool", f"BSL/SSL pool found (≥{TGT_MIN_RR}R)"))

    print("\n" + "=" * 60)
    print("  PHASE-D FUNNEL  (bot.py 15-step chain — Trial 6 mitigation)")
    print("=" * 60)
    print(f"  {'Stage':<34}  {'n':>6}  {'%total':>7}  {'drop':>7}")
    print(f"  {'-'*34}  {'-'*6}  {'-'*7}  {'-'*7}")
    print(f"  {'Total candidate bars':<34}  {total:>6}")
    prev = total
    for key, label in stages:
        n       = counts[key]
        pct     = n / total * 100 if total else 0.0
        dropped = prev - n
        print(f"  {label:<34}  {n:>6}  {pct:>6.1f}%  {dropped:>6} ↓")
        prev = n
    print(f"  {'─'*34}")
    if BSLSSL_TP:
        print(f"  {'→ SIGNALS (= pool)':<34}  {counts.get('pool', 0):>6}")
    elif BOS_CONFIRM:
        print(f"  {'→ SIGNALS (= BOS)':<34}  {counts.get('bos', 0):>6}")
    else:
        print(f"  {'→ SIGNALS (= choch)':<34}  {counts['choch']:>6}")
    # Trial 6 mitigation summary
    zt  = counts.get("zones_total", 0)
    zm  = counts.get("zones_mitigated", 0)
    prw = counts.get("poi_raw", 0)
    poi = counts.get("poi", 0)
    if zt:
        zp = zm / zt * 100
        print(f"\n  [Trial 6] Zones detected : {zt}")
        print(f"  [Trial 6] Zones mitigated: {zm}  ({zp:.1f}% of all zones filtered out)")
        print(f"  [Trial 6] Bar-level impact: poi_raw={prw} → poi_fresh={poi}  (−{prw - poi} bars)")
    print("=" * 60)
    if total and counts["choch"] < 50:
        prev2 = total
        worst_key, worst_drop = None, 0
        for key, _ in stages:
            d = prev2 - counts[key]
            if d > worst_drop:
                worst_drop = d
                worst_key  = key
            prev2 = counts[key]
        label = next(lbl for k, lbl in stages if k == worst_key)
        print(f"\n  WARNING n<50 — starving stage: {label} (−{worst_drop} bars)")
    print()


# ── CSV export ────────────────────────────────────────────────────────────────

def save_trades_csv(stats: dict, path: str) -> None:
    trades = stats.get("trades", [])
    if not trades:
        return
    keys = list(trades[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(trades)
    print(f"  Trade log → {path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global MITIGATION_PCT, MITIGATION_MODE, BSLSSL_TP, TGT_MIN_RR
    global MACRO_BIAS, PARTIAL_TP, SESSION_FILTER, BOS_CONFIRM, FVG_ENTRIES

    parser = argparse.ArgumentParser()
    parser.add_argument("--htf", default=str(ROOT / "data" / "cache" / f"{SYMBOL}_60m.parquet"))
    parser.add_argument("--ltf", default=str(ROOT / "data" / "cache" / f"{SYMBOL}_5m.parquet"))
    parser.add_argument("--csv", default=None)
    parser.add_argument("--max-bars", type=int, default=None,
                        help="Limit LTF bars processed (profiling only — do NOT use for real trials)")
    parser.add_argument("--run-label", default=None,
                        help="e.g. 'Trial 10' — appears in the report header")
    parser.add_argument("--side", default="both", choices=["long", "short", "both"],
                        help="Trade direction filter (default: both)")
    parser.add_argument(
        "--mitigation-pct", default="50",
        choices=["none", "50", "75", "100"],
        help=(
            "Mitigation consume threshold: none=off  50=midpoint (default)  "
            "75=75%% from entry edge  100=zone fully consumed"
        ),
    )
    parser.add_argument(
        "--mitigation-mode", default="wick", choices=["wick", "close"],
        help=(
            "Mitigation price level: wick=low/high (default, Trial 9)  "
            "close=closing price (Trial 10 — less aggressive at 4H)"
        ),
    )
    parser.add_argument(
        "--bsl-ssl-tp", action="store_true",
        help=(
            "Trial 13: replace fixed-R TP with BSL/SSL liquidity pool TP. "
            "Requires a qualifying cluster at ≥ min_r from config (default 1.5R); "
            "falls back to fallback_r if no pool qualifies."
        ),
    )
    parser.add_argument(
        "--min-rr", type=float, default=None,
        help="Override config targets.min_r for BSL/SSL pool minimum RR (default: use config).",
    )
    parser.add_argument(
        "--sensitivity", action="store_true",
        help=(
            "Trial 10 sensitivity matrix: baseline (none) + wick×[50/75/100%%] "
            "+ close×[50/75/100%%] — 7 runs, comparison table"
        ),
    )
    parser.add_argument(
        "--macro-htf", default=None,
        help="Sprint 1: path to 4H parquet for macro bias gate (e.g. data/cache/BTCUSDT_240m.parquet)",
    )
    parser.add_argument(
        "--partial-tp", action="store_true",
        help="Sprint 2: two-leg exit — close 50%% at 1R then trail remainder to break-even",
    )
    parser.add_argument(
        "--session-filter", action="store_true",
        help="Sprint 3: restrict entries to London (08-15 UTC) and NY (13-21 UTC) kill zones",
    )
    parser.add_argument(
        "--bos-confirm", action="store_true",
        help="Sprint 4: require 5M BOS close (above CHoCH bar high/low) before entry",
    )
    parser.add_argument(
        "--fvg-entries", action="store_true",
        help=(
            "Trial 20: allow FVG-only entries (Trial 8 baseline). "
            "Default (off) = OB-only rule from Trial 11. "
            "Applies to 4H+1H chain where FVG entries were ~50%% of signals at equal PF."
        ),
    )
    args = parser.parse_args()

    for label, path in [("HTF (1H)", args.htf), ("LTF (5M)", args.ltf)]:
        if not Path(path).exists():
            print(f"Missing {label} data: {path}")
            print("Run: python scripts/fetch_data.py --interval 60 --days 730")
            print("     python scripts/fetch_data.py --interval 5  --days 730")
            sys.exit(1)

    print(f"Loading 1H  from {args.htf} …", flush=True)
    df_1h = pd.read_parquet(args.htf)
    print(f"  {len(df_1h)} bars | {df_1h['ts'].iloc[0]} → {df_1h['ts'].iloc[-1]}", flush=True)

    print(f"Loading 5M  from {args.ltf} …", flush=True)
    df_5m = pd.read_parquet(args.ltf)
    if args.max_bars:
        df_5m = df_5m.iloc[: args.max_bars].copy()
        print(f"  PROFILING SLICE: first {args.max_bars} bars", flush=True)
    print(f"  {len(df_5m)} bars | {df_5m['ts'].iloc[0]} → {df_5m['ts'].iloc[-1]}", flush=True)

    print("Precomputing signal arrays …", flush=True)
    _precompute(df_1h, df_5m)

    # Sprint 1: load 4H macro-bias data if requested
    if args.macro_htf:
        if not Path(args.macro_htf).exists():
            print(f"Missing 4H data: {args.macro_htf}")
            print("Run: python scripts/fetch_data.py --interval 240 --days 730")
            sys.exit(1)
        print(f"Loading 4H  from {args.macro_htf} …", flush=True)
        df_4h = pd.read_parquet(args.macro_htf)
        print(f"  {len(df_4h)} bars | {df_4h['ts'].iloc[0]} → {df_4h['ts'].iloc[-1]}", flush=True)
        _precompute_4h(df_4h, df_5m)
        MACRO_BIAS = True

    PARTIAL_TP     = args.partial_tp
    SESSION_FILTER = args.session_filter
    BOS_CONFIRM    = args.bos_confirm
    FVG_ENTRIES    = args.fvg_entries

    def _tf_label(path: str) -> str:
        stem = Path(path).stem.rsplit("_", 1)
        if len(stem) == 2:
            mins = int(stem[1].replace("m", ""))
            return f"{mins // 60}H" if mins >= 60 else f"{mins}m"
        return "?"

    htf_label = _tf_label(args.htf)
    ltf_label = _tf_label(args.ltf)
    run_label = args.run_label or "Trial X"

    if args.sensitivity:
        # ── Trial 10 sensitivity matrix: baseline + wick×3 + close×3 ────────
        #   Row format: (display label, mode, pct)
        #   mode=None means "no filter" (baseline row)
        LEVELS = [
            ("none (OFF)",  None,    None),
            ("wick   50%",  "wick",  0.50),
            ("wick   75%",  "wick",  0.75),
            ("wick  100%",  "wick",  1.00),
            ("close  50%",  "close", 0.50),
            ("close  75%",  "close", 0.75),
            ("close 100%",  "close", 1.00),
        ]
        w = 76
        print("\n" + "=" * w)
        print(f"  TRIAL 10 SENSITIVITY MATRIX — {SYMBOL}  {htf_label}+{ltf_label}  side={args.side}")
        print(f"  Baseline (Trial 8): n=47  net PF=1.5662  (no mitigation)")
        print("=" * w)
        hdr = (
            f"  {'Rule':<14}  {'n':>5}  {'Win%':>5}  {'GrossPF':>8}"
            f"  {'NetPF':>8}  {'Expect':>7}  {'CHoCH':>5}  {'ZoneRej%':>8}  Gate"
        )
        print(hdr)
        print(
            f"  {'-'*14}  {'-'*5}  {'-'*5}  {'-'*8}"
            f"  {'-'*8}  {'-'*7}  {'-'*5}  {'-'*8}  ----"
        )
        for label, mode, pct in LEVELS:
            MITIGATION_MODE = mode or "wick"
            MITIGATION_PCT  = pct
            s  = run_backtest(df_1h, df_5m, side=args.side)
            fc = count_funnel(df_1h, df_5m)
            gate = "PASS" if (s["n"] >= 50 and s["net_pf"] > 1.0) else "fail"
            exp  = s.get("expectancy", 0.0)
            npf  = s["net_pf"]   if s["net_pf"]   != float("inf") else float("nan")
            gpf  = s["gross_pf"] if s["gross_pf"] != float("inf") else float("nan")
            zt   = fc.get("zones_total", 0)
            zm   = fc.get("zones_mitigated", 0)
            zrej = (zm / zt * 100) if zt else 0.0
            choch = fc.get("choch", 0)
            print(
                f"  {label:<14}  {s['n']:>5}  {s['win_rate']:>4.1f}%"
                f"  {gpf:>8.4f}  {npf:>8.4f}  {exp:>+7.4f}"
                f"  {choch:>5}  {zrej:>7.1f}%  {gate}"
            )
        print("=" * w)
        print(f"  Gate: n≥50 AND net PF>1.0")
        print(f"  Success: n≥50  net PF>1.2  trade count >> wick  PF within 20% of baseline (1.253–1.879)")
        print()
        return

    # ── Single run ───────────────────────────────────────────────────────────
    MITIGATION_PCT  = None if args.mitigation_pct == "none" else float(args.mitigation_pct) / 100.0
    MITIGATION_MODE = args.mitigation_mode
    BSLSSL_TP       = args.bsl_ssl_tp
    if args.min_rr is not None:
        TGT_MIN_RR  = args.min_rr
    # Sprint flags are set above (after _precompute calls)

    print("Running Phase-C backtest …", flush=True)
    stats = run_backtest(df_1h, df_5m, side=args.side)
    print_report(stats, run_label=run_label, htf_label=htf_label, ltf_label=ltf_label, side=args.side)
    sys.stdout.flush()

    print("Running Phase-D funnel …", flush=True)
    funnel = count_funnel(df_1h, df_5m)
    print_funnel(funnel)
    sys.stdout.flush()

    if args.csv:
        save_trades_csv(stats, args.csv)


if __name__ == "__main__":
    main()
