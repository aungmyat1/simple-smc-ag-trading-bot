"""
session_range.py — Asian-session range/sweep/trend signal (PROPOSE-ONLY).

Input frames by TIMEFRAME CONTENT (not by bot.py variable names):
  df_4h_bias — 4H candles, used only for macro bias (structure.get_bias)
  df_1h_box  — 1H candles, used for box detection, ATR, and sweep check

Import rules: ONLY structure, tp_engine, _util — no executor, pybit, ccxt.
This keeps tests/test_ast_guard.py green.

Status: PENDING Phase-0 gate. Each setup mode (sweep / range / trend) requires
its own gross-PF > 1.0 holdout trial before activation in bot.py (P3).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import structure, tp_engine
from ._util import atr as _atr_series

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class SessionBox:
    high: float
    low: float
    range: float    # high - low
    date: str       # ISO date "YYYY-MM-DD" of the completed session


@dataclass
class SessionSignal:
    side: str       # "Buy" | "Sell"
    entry: float
    sl: float
    tp: float       # final runner target (BSL/SSL pool or 5R fallback)
    setup: str      # "sweep" | "range" | "trend"
    mgmt: dict      # management metadata consumed by the execution layer (P4)
    tp_plan: object # TpPlan from tp_engine for R-math context


def _mgmt(setup: str, first_close_at: float, first_close_pct: float) -> dict:
    """
    Build the management metadata dict.

    sweep/range: close first_close_pct at the opposite box edge, then SL → BE.
    trend:       close first_close_pct at 4R, then trail the remainder.
    """
    return {
        "first_close_at":  first_close_at,
        "first_close_pct": first_close_pct,
        "trail_after":     setup == "trend",
        "be_after_first":  setup != "trend",
    }


# ---------------------------------------------------------------------------
# 1. Box builder
# ---------------------------------------------------------------------------

def _most_recent_completed_box(
    df_1h_box: pd.DataFrame,
    start_h: int = 0,
    end_h: int = 8,
    min_bars: int = 6,
    now_utc: datetime | None = None,
) -> SessionBox | None:
    """
    Return the high/low/range of the most recently COMPLETED Asian session box.

    The box window is [start_h, end_h) UTC hours.  A box is considered complete
    only when all its 1H bars have closed.  If now_utc.hour < end_h today, today's
    box is still forming — use the last completed date (typically yesterday).

    min_bars: require at least this many session bars for the box to be trusted
              (guards against sparse API responses near the window boundary).

    now_utc is injectable for deterministic unit tests.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # Latest date whose session is complete
    if now_utc.hour < end_h:
        cutoff_date = (now_utc - timedelta(days=1)).date()
    else:
        cutoff_date = now_utc.date()

    df = df_1h_box.copy()
    ts = pd.to_datetime(df["ts"], utc=True)
    df["_date"] = ts.dt.date
    df["_hour"] = ts.dt.hour

    mask = (df["_hour"] >= start_h) & (df["_hour"] < end_h) & (df["_date"] <= cutoff_date)
    window = df[mask]
    if window.empty:
        log.debug("No completed Asian session bars in df_1h_box")
        return None

    latest_date = window["_date"].max()
    box_bars = window[window["_date"] == latest_date]

    if len(box_bars) < min_bars:
        log.debug(
            "Asian session %s has only %d/%d bars — skipping (need %d)",
            latest_date, len(box_bars), end_h - start_h, min_bars,
        )
        return None

    high = float(box_bars["high"].max())
    low  = float(box_bars["low"].min())
    rng  = high - low

    if rng <= 0:
        log.debug("Asian session box %s has zero range", latest_date)
        return None

    return SessionBox(high=high, low=low, range=rng, date=str(latest_date))


# ---------------------------------------------------------------------------
# 2. Session classifier
# ---------------------------------------------------------------------------

def classify_session(
    box: SessionBox,
    df_1h_box: pd.DataFrame,
    range_thr: float = 0.5,
    trend_thr: float = 0.7,
) -> str:
    """
    Classify the Asian session as 'trend', 'range', or 'neutral'.

    ratio = box.range / ATR(14) on 1H:
      ratio > trend_thr → 'trend'   (large box: directional, wide session)
      ratio < range_thr → 'range'   (small box: tight, oscillating session)
      otherwise         → 'neutral'
    """
    atr_val = float(_atr_series(df_1h_box).iloc[-1])
    if atr_val <= 0 or box.range <= 0:
        return "neutral"

    ratio = box.range / atr_val
    if ratio > trend_thr:
        return "trend"
    if ratio < range_thr:
        return "range"
    return "neutral"


# ---------------------------------------------------------------------------
# 3. Sweep detector
# ---------------------------------------------------------------------------

def detect_sweep_in_session(
    df_1h_box: pd.DataFrame,
    box: SessionBox,
    sweep_beyond_pct: float = 0.02,
    lookback: int = 6,
) -> dict | None:
    """
    Detect a wick that pierced beyond a box extreme by sweep_beyond_pct × range
    and closed back inside the box.  Scans the last `lookback` 1H bars.

    Returns:
        {
          "direction":    "bullish" | "bearish",
          "bar_idx":      int,    # positional index in df_1h_box
          "wick_extreme": float,  # wick tip that pierced the box extreme
          "body_back":    float,  # close price back inside the box
        }
    or None if no qualifying sweep is found.

    bullish sweep: wick below (box.low − threshold), close back above box.low
                   → institutions grabbed sell-side liquidity → expect up
    bearish sweep: wick above (box.high + threshold), close back below box.high
                   → institutions grabbed buy-side liquidity → expect down
    """
    threshold = sweep_beyond_pct * box.range
    n = len(df_1h_box)
    start = max(0, n - lookback)

    for i in range(n - 1, start - 1, -1):   # most recent first
        low_i   = float(df_1h_box["low"].iloc[i])
        high_i  = float(df_1h_box["high"].iloc[i])
        close_i = float(df_1h_box["close"].iloc[i])

        if low_i < box.low - threshold and close_i > box.low:
            log.debug(
                "Bullish sweep at bar %d: wick=%.2f < %.2f, close=%.2f > %.2f",
                i, low_i, box.low - threshold, close_i, box.low,
            )
            return {
                "direction":    "bullish",
                "bar_idx":      i,
                "wick_extreme": low_i,
                "body_back":    close_i,
            }

        if high_i > box.high + threshold and close_i < box.high:
            log.debug(
                "Bearish sweep at bar %d: wick=%.2f > %.2f, close=%.2f < %.2f",
                i, high_i, box.high + threshold, close_i, box.high,
            )
            return {
                "direction":    "bearish",
                "bar_idx":      i,
                "wick_extreme": high_i,
                "body_back":    close_i,
            }

    return None


# ---------------------------------------------------------------------------
# 4. Signal builder
# ---------------------------------------------------------------------------

def build_session_signal(
    df_4h_bias: pd.DataFrame,
    df_1h_box: pd.DataFrame,
    cfg: dict,
    now_utc: datetime | None = None,
) -> SessionSignal | None:
    """
    Build an Asian-session signal from the two input frames.

    Gate 1 — macro bias: structure.get_bias(df_4h_bias) must be non-neutral.
    Gate 2 — box:        a completed session box must exist.
    Route (priority order):
      sweep present AND direction matches bias → SWEEP setup
      range session (no matching sweep)        → RANGE setup
      trend session                            → TREND setup
      neutral session, no sweep               → None

    Returns SessionSignal or None.

    Args:
        df_4h_bias: 4H OHLCV DataFrame for macro bias detection.
        df_1h_box:  1H OHLCV DataFrame for box/ATR/sweep detection.
        cfg:        full bot config dict (reads cfg["session"]["asian"]).
        now_utc:    injectable current time for deterministic testing.
    """
    ac = cfg.get("session", {}).get("asian", {})
    start_h          = ac.get("start_h", 0)
    end_h            = ac.get("end_h", 8)
    range_thr        = ac.get("range_thr", 0.5)
    trend_thr        = ac.get("trend_thr", 0.7)
    sweep_beyond_pct = ac.get("sweep_beyond_pct", 0.02)
    sl_pct_of_range  = ac.get("sl_pct_of_range", 0.25)
    target_r         = ac.get("target_r", 5.0)
    first_close_r    = ac.get("trend_first_close_r", 4.0)
    first_close_pct  = ac.get("first_close_pct", 0.75)
    swing_n          = cfg.get("structure", {}).get("swing_n", 5)

    # ── Gate 1: macro bias ────────────────────────────────────────────────
    bias = structure.get_bias(df_4h_bias, swing_n=swing_n)
    if bias == "neutral":
        log.debug("session_range: 4H bias neutral — no signal")
        return None

    side    = "Buy"  if bias == "bullish" else "Sell"
    bullish = bias == "bullish"

    # ── Gate 2: completed box ─────────────────────────────────────────────
    box = _most_recent_completed_box(df_1h_box, start_h, end_h, now_utc=now_utc)
    if box is None:
        log.debug("session_range: no completed Asian session box")
        return None

    # ── Classify and detect sweep ─────────────────────────────────────────
    label = classify_session(box, df_1h_box, range_thr, trend_thr)
    sweep = detect_sweep_in_session(df_1h_box, box, sweep_beyond_pct)

    # Sweep is only valid when its direction agrees with macro bias
    sweep_matches = (
        sweep is not None and sweep["direction"] == bias
    )

    # ── Route to setup ────────────────────────────────────────────────────
    if sweep_matches:
        setup = "sweep"
        entry = sweep["body_back"]                          # body back inside box
    elif label == "range":
        setup = "range"
        entry = box.low if bullish else box.high            # trade from box edge
    elif label == "trend":
        setup = "trend"
        entry = (box.high + box.low) / 2.0                 # pullback to midpoint
    else:
        log.debug(
            "session_range: label=%s, sweep_matches=%s — no valid setup",
            label, sweep_matches,
        )
        return None

    # ── SL: entry ± sl_pct_of_range × range ──────────────────────────────
    sl_dist = sl_pct_of_range * box.range
    sl      = entry - sl_dist if bullish else entry + sl_dist

    stop_dist = abs(entry - sl)
    if stop_dist <= 0:
        log.warning("session_range: stop_dist=0 — skipping")
        return None

    # ── TP via tp_engine (BSL/SSL pool or fallback R) ────────────────────
    tp_plan = tp_engine.build_plan(
        df_1h_box, side, entry, sl,
        tp1_r=first_close_r,        # first partial close R
        tp2_r=target_r,             # full target R
        fallback_runner_r=target_r,
        swing_n=swing_n,
    )
    tp = tp_plan.runner              # final target: BSL/SSL or fallback 5R

    # ── Management targets ────────────────────────────────────────────────
    if setup == "trend":
        first_close_at = tp_plan.tp1   # 4R for trend
    else:
        # sweep / range: first close at the opposite box edge
        first_close_at = box.high if bullish else box.low

    mgmt_dict = _mgmt(setup, first_close_at, first_close_pct)

    log.info(
        "session_range signal: %s %s | setup=%s box=[%.0f-%.0f] "
        "entry=%.2f sl=%.2f tp=%.2f first_close=%.2f",
        side, label, setup, box.low, box.high,
        entry, sl, tp, first_close_at,
    )

    return SessionSignal(
        side=side,
        entry=entry,
        sl=sl,
        tp=tp,
        setup=setup,
        mgmt=mgmt_dict,
        tp_plan=tp_plan,
    )
