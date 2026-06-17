"""
tests/test_session_range.py

Unit tests for smc_bot/session_range.py.

Synthetic DataFrames are constructed explicitly so swing detection and
ATR computation are deterministic — no randomness, no API calls.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from smc_bot.session_range import (
    SessionBox,
    _most_recent_completed_box,
    classify_session,
    detect_sweep_in_session,
    build_session_signal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ohlcv(closes: list[float], freq: str = "1h", base: str = "2025-06-01 00:00") -> pd.DataFrame:
    """Minimal OHLCV DataFrame from a list of close prices (spread ±5)."""
    c = np.array(closes, dtype=float)
    return pd.DataFrame(
        {
            "ts":     pd.date_range(base, periods=len(c), freq=freq, tz="UTC"),
            "open":   c - 10,
            "high":   c + 5,
            "low":    c - 5,
            "close":  c,
            "volume": np.ones(len(c)) * 1000.0,
        }
    )


def _uniform_df(n: int, bar_range: float, base_price: float = 50000.0) -> pd.DataFrame:
    """
    n 1H bars all with the same high-low spread.

    ATR steady-state = bar_range (each TR = max(bar_range, bar_range/2, bar_range/2) = bar_range
    because prev_close == close when all closes are identical).
    """
    return pd.DataFrame(
        {
            "ts":     pd.date_range("2025-06-01", periods=n, freq="1h", tz="UTC"),
            "open":   [base_price] * n,
            "high":   [base_price + bar_range / 2] * n,
            "low":    [base_price - bar_range / 2] * n,
            "close":  [base_price] * n,
            "volume": [1000.0] * n,
        }
    )


def _make_4h_bullish() -> pd.DataFrame:
    """
    40-bar 4H OHLCV with HH+HL pattern (structure.get_bias → 'bullish').

    Swing high 1 at bar 8  (close=200, high=205)
    Swing low  1 at bar 15 (close=80,  low=75)
    Swing high 2 at bar 22 (close=230, high=235)  HH: 235 > 205
    Swing low  2 at bar 29 (close=110, low=105)   HL: 105 > 75
    """
    closes = [
        100, 110, 120, 130, 140, 150, 160, 170,   # 0-7
        200,                                         # 8 — peak 1
        170, 160, 150, 140, 130, 100,               # 9-14
        80,                                          # 15 — trough 1
        100, 110, 120, 130, 140, 150,               # 16-21
        230,                                         # 22 — peak 2 (HH)
        200, 190, 180, 170, 160, 140,               # 23-28
        110,                                         # 29 — trough 2 (HL)
        130, 140, 150, 160, 170, 180, 190, 200, 210, 220,  # 30-39
    ]
    return _ohlcv(closes, freq="4h", base="2025-01-01 00:00")


def _make_4h_bearish() -> pd.DataFrame:
    """
    40-bar 4H OHLCV with LL+LH pattern (structure.get_bias → 'bearish').

    Constructed by inverting _make_4h_bullish() around 155 (i.e. 310-c).
      Swing high 1 at bar 15 (high=235) → Swing high 2 at bar 29 (high=205)  LH ✓
      Swing low  1 at bar 8  (low=105)  → Swing low  2 at bar 22 (low=75)    LL ✓
    """
    bull_closes = [
        100, 110, 120, 130, 140, 150, 160, 170,
        200,
        170, 160, 150, 140, 130, 100,
        80,
        100, 110, 120, 130, 140, 150,
        230,
        200, 190, 180, 170, 160, 140,
        110,
        130, 140, 150, 160, 170, 180, 190, 200, 210, 220,
    ]
    bear_closes = [310.0 - c for c in bull_closes]
    return _ohlcv(bear_closes, freq="4h", base="2025-01-01 00:00")


def _make_4h_neutral() -> pd.DataFrame:
    """Short 4H df — not enough bars for swing detection → 'neutral'."""
    return _ohlcv([100, 110, 105, 115, 108], freq="4h")


def _asian_1h_df(
    today: pd.Timestamp,
    box_high: float = 51000.0,
    box_low: float = 50000.0,
    atr_bar_range: float = 50.0,
    prior_bar_range: float | None = None,
    n_prior: int = 8,
    sweep_direction: str | None = None,
    now_h: int = 10,
    include_yesterday: bool = False,
) -> pd.DataFrame:
    """
    Build a 1H OHLCV df with:

      • optional prior bars (added *before* the Asian session window)
        centered near box midpoint with large TR — for ATR control without
        touching the last-6-bar sweep window.
      • optional yesterday's Asian session bars (hours 0-7)
      • today's Asian session bars (hours 0-7)
      • post-session bars (hours 8 … now_h-1), default tiny range (atr_bar_range=50)
        so they stay inside the box and never accidentally trigger sweep detection.
      • exactly ONE explicit sweep bar at hour 9 if sweep_direction is set.

    The prior bars have timestamps in the 8 hours before today's midnight so they
    never land in the Asian session hour range [0, 7) and don't affect box bounds.
    """
    rows: list[dict] = []
    mid_price = box_low + (box_high - box_low) / 2.0
    step = (box_high - box_low) / 8.0

    # ── Prior bars (pre-session, large TR for ATR inflation) ─────────────
    if prior_bar_range is not None and n_prior > 0:
        for i in range(n_prior):
            rows.append(
                dict(
                    ts=today - pd.Timedelta(hours=n_prior - i),
                    open=mid_price,
                    high=mid_price + prior_bar_range / 2,
                    low=mid_price - prior_bar_range / 2,
                    close=mid_price,
                    volume=500.0,
                )
            )

    # ── Yesterday's Asian session (optional) ─────────────────────────────
    if include_yesterday:
        ydate = today - pd.Timedelta(days=1)
        for h in range(8):
            mid = box_low + step * h + step / 2
            rows.append(
                dict(
                    ts=ydate + pd.Timedelta(hours=h),
                    open=mid - 10,
                    high=box_low + step * (h + 1),
                    low=box_low + step * h,
                    close=mid,
                    volume=500.0,
                )
            )

    # ── Today's Asian session (hours 0-7) ────────────────────────────────
    for h in range(8):
        mid = box_low + step * h + step / 2
        rows.append(
            dict(
                ts=today + pd.Timedelta(hours=h),
                open=mid - 10,
                high=box_low + step * (h + 1),
                low=box_low + step * h,
                close=mid,
                volume=500.0,
            )
        )

    # ── Post-session bars ─────────────────────────────────────────────────
    threshold = 0.02 * (box_high - box_low)
    for h in range(8, now_h):
        is_sweep_bar = (sweep_direction is not None and h == 9)
        if is_sweep_bar:
            if sweep_direction == "bullish":
                rows.append(
                    dict(
                        ts=today + pd.Timedelta(hours=h),
                        open=mid_price,
                        high=mid_price + 50,
                        low=box_low - threshold - 50,    # wick beyond threshold
                        close=box_low + 100,              # body back inside box
                        volume=800.0,
                    )
                )
            else:  # bearish
                rows.append(
                    dict(
                        ts=today + pd.Timedelta(hours=h),
                        open=mid_price,
                        high=box_high + threshold + 50,  # wick beyond threshold
                        low=mid_price - 50,
                        close=box_high - 100,             # body back inside box
                        volume=800.0,
                    )
                )
        else:
            # Narrow bar well inside the box — never triggers sweep
            rows.append(
                dict(
                    ts=today + pd.Timedelta(hours=h),
                    open=mid_price - atr_bar_range / 2,
                    high=mid_price + atr_bar_range / 2,
                    low=mid_price - atr_bar_range / 2,
                    close=mid_price,
                    volume=500.0,
                )
            )

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


_DEFAULT_TODAY = pd.Timestamp("2025-06-17", tz="UTC")

_MINIMAL_CFG = {
    "session": {
        "asian": {
            "start_h":             0,
            "end_h":               8,
            "range_thr":           0.5,
            "trend_thr":           0.7,
            "sweep_beyond_pct":    0.02,
            "sl_pct_of_range":     0.25,
            "target_r":            5.0,
            "trend_first_close_r": 4.0,
            "first_close_pct":     0.75,
        }
    },
    "structure": {"swing_n": 5},
}


# ---------------------------------------------------------------------------
# 1. Box builder
# ---------------------------------------------------------------------------

class TestMostRecentCompletedBox:
    def test_box_completed_today(self):
        """now_utc after 08:00 → uses today's Asian session bars."""
        today = _DEFAULT_TODAY
        df = _asian_1h_df(today, box_high=51000, box_low=50000, now_h=10)
        now = datetime(2025, 6, 17, 10, 0, 0, tzinfo=timezone.utc)

        box = _most_recent_completed_box(df, now_utc=now)

        assert box is not None
        assert box.date == "2025-06-17"
        assert box.high == pytest.approx(51000.0)
        assert box.low  == pytest.approx(50000.0)
        assert box.range == pytest.approx(1000.0)

    def test_box_half_formed_guard(self):
        """
        now_utc at 06:00 UTC (inside today's Asian window) → today's box is
        still forming.  Must return yesterday's completed box instead.
        """
        today = _DEFAULT_TODAY
        df = _asian_1h_df(today, box_high=51000, box_low=50000,
                          now_h=6, include_yesterday=True)
        now = datetime(2025, 6, 17, 6, 0, 0, tzinfo=timezone.utc)

        box = _most_recent_completed_box(df, now_utc=now)

        assert box is not None
        assert box.date == "2025-06-16"

    def test_box_half_formed_no_yesterday_returns_none(self):
        """Half-formed today, no yesterday bars → None."""
        today = _DEFAULT_TODAY
        df = _asian_1h_df(today, box_high=51000, box_low=50000,
                          now_h=5, include_yesterday=False)
        now = datetime(2025, 6, 17, 5, 0, 0, tzinfo=timezone.utc)

        box = _most_recent_completed_box(df, now_utc=now)

        assert box is None

    def test_box_empty_df_returns_none(self):
        df = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        now = datetime(2025, 6, 17, 10, 0, 0, tzinfo=timezone.utc)
        assert _most_recent_completed_box(df, now_utc=now) is None

    def test_box_min_bars_guard(self):
        """Fewer than min_bars (6) Asian bars on the latest date → None."""
        today = _DEFAULT_TODAY
        df = _asian_1h_df(today, now_h=10)
        # Drop Asian bars 4-7 so only 4 remain
        today_asian = (df["ts"].dt.date == today.date()) & (df["ts"].dt.hour < 8)
        keep = ~today_asian | (df["ts"].dt.hour < 4)
        df = df[keep].reset_index(drop=True)

        now = datetime(2025, 6, 17, 10, 0, 0, tzinfo=timezone.utc)
        box = _most_recent_completed_box(df, now_utc=now, min_bars=6)
        assert box is None


# ---------------------------------------------------------------------------
# 2. Session classifier — uses _uniform_df for precise ATR control
# ---------------------------------------------------------------------------

class TestClassifySession:
    """
    _uniform_df gives ATR = bar_range exactly (all closes identical,
    TR = max(bar_range, bar_range/2, bar_range/2) = bar_range).
    """

    def test_classify_range(self):
        """box.range=200, ATR=800 → ratio=0.25 < range_thr=0.5 → 'range'."""
        box = SessionBox(high=51000.0, low=50800.0, range=200.0, date="2025-06-17")
        df  = _uniform_df(20, bar_range=800.0)
        assert classify_session(box, df, range_thr=0.5, trend_thr=0.7) == "range"

    def test_classify_trend(self):
        """box.range=2000, ATR=100 → ratio=20 > trend_thr=0.7 → 'trend'."""
        box = SessionBox(high=52000.0, low=50000.0, range=2000.0, date="2025-06-17")
        df  = _uniform_df(20, bar_range=100.0)
        assert classify_session(box, df, range_thr=0.5, trend_thr=0.7) == "trend"

    def test_classify_neutral(self):
        """box.range=1000, ATR=1600 → ratio=0.625 (between 0.5 and 0.7) → 'neutral'."""
        box = SessionBox(high=51000.0, low=50000.0, range=1000.0, date="2025-06-17")
        df  = _uniform_df(20, bar_range=1600.0)
        assert classify_session(box, df, range_thr=0.5, trend_thr=0.7) == "neutral"

    def test_zero_atr_returns_neutral(self):
        """ATR=0 (flat prices) → neutral (guard against division by zero)."""
        box = SessionBox(high=51000.0, low=50000.0, range=1000.0, date="2025-06-17")
        df  = _uniform_df(20, bar_range=0.0)
        assert classify_session(box, df) == "neutral"


# ---------------------------------------------------------------------------
# 3. Sweep detector
# ---------------------------------------------------------------------------

class TestDetectSweepInSession:
    def _box(self) -> SessionBox:
        return SessionBox(high=51000.0, low=50000.0, range=1000.0, date="2025-06-17")

    def test_bullish_sweep_detected(self):
        """Wick below box.low - threshold, close back above → 'bullish'."""
        box = self._box()
        threshold = 0.02 * 1000.0   # 20 pts
        df = pd.DataFrame(
            [
                dict(ts="2025-06-17 08:00", open=50500, high=50700, low=50100, close=50600, volume=100),
                dict(ts="2025-06-17 09:00", open=50400,
                     high=50600,
                     low=box.low - threshold - 10,   # 49970: clearly beyond threshold
                     close=box.low + 100,             # 50100: back inside
                     volume=200),
            ]
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True)

        result = detect_sweep_in_session(df, box, sweep_beyond_pct=0.02, lookback=6)

        assert result is not None
        assert result["direction"] == "bullish"
        assert result["wick_extreme"] < box.low - threshold
        assert result["body_back"] > box.low

    def test_bearish_sweep_detected(self):
        """Wick above box.high + threshold, close back below → 'bearish'."""
        box = self._box()
        threshold = 0.02 * 1000.0
        df = pd.DataFrame(
            [
                dict(ts="2025-06-17 08:00", open=50500, high=50800, low=50200, close=50600, volume=100),
                dict(ts="2025-06-17 09:00", open=50800,
                     high=box.high + threshold + 10,   # 51030: clearly beyond
                     low=50700,
                     close=box.high - 100,              # 50900: back inside
                     volume=200),
            ]
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True)

        result = detect_sweep_in_session(df, box, sweep_beyond_pct=0.02, lookback=6)

        assert result is not None
        assert result["direction"] == "bearish"
        assert result["wick_extreme"] > box.high + threshold
        assert result["body_back"] < box.high

    def test_no_sweep_when_close_stays_outside(self):
        """Wick pierces the box but close does NOT re-enter → not a sweep."""
        box = self._box()
        threshold = 0.02 * 1000.0
        df = pd.DataFrame(
            [
                dict(ts="2025-06-17 09:00", open=50000,
                     high=50200,
                     low=box.low - threshold - 10,    # wick beyond
                     close=box.low - 5,               # close OUTSIDE box
                     volume=200),
            ]
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True)

        result = detect_sweep_in_session(df, box, sweep_beyond_pct=0.02, lookback=6)
        assert result is None

    def test_no_sweep_when_wick_not_deep_enough(self):
        """Wick reaches right up to (but not past) threshold → not a sweep."""
        box = self._box()
        threshold = 0.02 * 1000.0
        df = pd.DataFrame(
            [
                dict(ts="2025-06-17 09:00", open=50100,
                     high=50400,
                     low=box.low - threshold + 1,    # just short of threshold
                     close=box.low + 50,
                     volume=200),
            ]
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True)

        result = detect_sweep_in_session(df, box, sweep_beyond_pct=0.02, lookback=6)
        assert result is None


# ---------------------------------------------------------------------------
# 4. build_session_signal — full integration
# ---------------------------------------------------------------------------

class TestBuildSessionSignal:
    def _now(self, h: int = 10) -> datetime:
        return datetime(2025, 6, 17, h, 0, 0, tzinfo=timezone.utc)

    def test_signal_sweep_long(self):
        """
        Bullish 4H bias + bullish sweep (lows grabbed) → SWEEP Buy.
        Entry = body of sweep bar back inside box.
        SL below entry (long).
        first_close_at = box.high (opposite box edge for sweep).
        """
        df_4h = _make_4h_bullish()
        df_1h = _asian_1h_df(
            _DEFAULT_TODAY,
            box_high=51000, box_low=50000,
            atr_bar_range=50.0,       # post bars narrow — stay inside box, no false sweeps
            sweep_direction="bullish",
            now_h=12,
        )
        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=self._now(12))

        assert sig is not None
        assert sig.side == "Buy"
        assert sig.setup == "sweep"
        assert sig.sl < sig.entry          # SL below for long
        assert sig.tp > sig.entry          # TP above for long
        assert sig.mgmt["be_after_first"] is True
        assert sig.mgmt["trail_after"] is False
        # Sweep/range first close = box.high (opposite edge)
        assert sig.mgmt["first_close_at"] == pytest.approx(51000.0)
        assert sig.mgmt["first_close_pct"] == pytest.approx(0.75)

    def test_signal_sweep_short(self):
        """
        Bearish 4H bias + bearish sweep (highs grabbed) → SWEEP Sell.
        """
        df_4h = _make_4h_bearish()
        df_1h = _asian_1h_df(
            _DEFAULT_TODAY,
            box_high=51000, box_low=50000,
            atr_bar_range=50.0,
            sweep_direction="bearish",
            now_h=12,
        )
        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=self._now(12))

        assert sig is not None
        assert sig.side == "Sell"
        assert sig.setup == "sweep"
        assert sig.sl > sig.entry          # SL above for short
        assert sig.tp < sig.entry          # TP below for short
        # Sweep first close = box.low (opposite edge)
        assert sig.mgmt["first_close_at"] == pytest.approx(50000.0)

    def test_signal_range_long(self):
        """
        Bullish 4H bias + range session (ATR >> box.range) + no sweep → RANGE Buy at box.low.

        prior_bar_range=5000 gives large ATR in the rolling window while the bars
        themselves precede the Asian session (not in the last-6-bar sweep window).
        """
        df_4h = _make_4h_bullish()
        df_1h = _asian_1h_df(
            _DEFAULT_TODAY,
            box_high=51000, box_low=50000,
            prior_bar_range=5000.0, n_prior=8,   # inflates ATR → ratio < 0.5 → 'range'
            atr_bar_range=50.0,                  # post bars stay inside box
            sweep_direction=None,
            now_h=8,                             # 0 post-session bars → no sweep window
        )
        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=self._now(8))

        assert sig is not None
        assert sig.side == "Buy"
        assert sig.setup == "range"
        assert sig.entry == pytest.approx(50000.0)    # box.low for long
        assert sig.sl < sig.entry
        assert sig.tp > sig.entry
        assert sig.mgmt["first_close_at"] == pytest.approx(51000.0)  # box.high

    def test_signal_range_short(self):
        """
        Bearish 4H bias + range session + no sweep → RANGE Sell at box.high.
        """
        df_4h = _make_4h_bearish()
        df_1h = _asian_1h_df(
            _DEFAULT_TODAY,
            box_high=51000, box_low=50000,
            prior_bar_range=5000.0, n_prior=8,
            atr_bar_range=50.0,
            sweep_direction=None,
            now_h=8,
        )
        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=self._now(8))

        assert sig is not None
        assert sig.side == "Sell"
        assert sig.setup == "range"
        assert sig.entry == pytest.approx(51000.0)    # box.high for short
        assert sig.sl > sig.entry
        assert sig.tp < sig.entry
        assert sig.mgmt["first_close_at"] == pytest.approx(50000.0)  # box.low

    def test_signal_trend_long(self):
        """
        Bullish 4H bias + trend session (small ATR relative to wide box) + no sweep
        → TREND Buy at box midpoint.  first_close = tp_plan.tp1 (4R).
        """
        df_4h = _make_4h_bullish()
        df_1h = _asian_1h_df(
            _DEFAULT_TODAY,
            box_high=51000, box_low=50000,
            atr_bar_range=50.0,    # narrow post bars; Asian bars give ATR << box.range → 'trend'
            sweep_direction=None,
            now_h=12,
        )
        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=self._now(12))

        assert sig is not None
        assert sig.side == "Buy"
        assert sig.setup == "trend"
        assert sig.entry == pytest.approx(50500.0)    # (51000+50000)/2
        assert sig.sl < sig.entry
        assert sig.tp > sig.entry
        assert sig.mgmt["trail_after"] is True
        assert sig.mgmt["be_after_first"] is False
        # first_close = entry + 4 * stop_dist
        stop_dist = sig.entry - sig.sl
        assert sig.mgmt["first_close_at"] == pytest.approx(sig.entry + 4.0 * stop_dist)

    def test_signal_trend_short(self):
        """
        Bearish 4H bias + trend session + no sweep → TREND Sell at box midpoint.
        """
        df_4h = _make_4h_bearish()
        df_1h = _asian_1h_df(
            _DEFAULT_TODAY,
            box_high=51000, box_low=50000,
            atr_bar_range=50.0,
            sweep_direction=None,
            now_h=12,
        )
        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=self._now(12))

        assert sig is not None
        assert sig.side == "Sell"
        assert sig.setup == "trend"
        assert sig.entry == pytest.approx(50500.0)
        assert sig.sl > sig.entry
        assert sig.tp < sig.entry

    def test_neutral_bias_returns_none(self):
        """Neutral 4H bias (not enough swing history) → None."""
        df_4h = _make_4h_neutral()
        df_1h = _asian_1h_df(_DEFAULT_TODAY, now_h=10)

        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=self._now(10))
        assert sig is None

    def test_neutral_session_no_sweep_returns_none(self):
        """
        Bullish bias, neutral session (ATR mid-range), no sweep → no valid setup → None.

        prior_bar_range=3500, n_prior=8 gives ATR ≈ 1598, ratio ≈ 0.626
        which is between range_thr=0.5 and trend_thr=0.7 → 'neutral'.
        """
        df_4h = _make_4h_bullish()
        df_1h = _asian_1h_df(
            _DEFAULT_TODAY,
            box_high=51000, box_low=50000,
            prior_bar_range=3500.0, n_prior=8,
            atr_bar_range=50.0,
            sweep_direction=None,
            now_h=8,
        )
        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=self._now(8))
        assert sig is None

    def test_half_formed_box_returns_none(self):
        """now_utc during Asian session + no yesterday bars → None."""
        df_4h = _make_4h_bullish()
        df_1h = _asian_1h_df(
            _DEFAULT_TODAY,
            box_high=51000, box_low=50000,
            now_h=6,
            include_yesterday=False,
        )
        now = datetime(2025, 6, 17, 6, 0, 0, tzinfo=timezone.utc)
        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=now)
        assert sig is None

    def test_sweep_opposing_bias_discarded_falls_to_range(self):
        """
        Bearish bias + bullish sweep fires (lows swept) → direction mismatch
        → sweep is discarded.  With large prior_bar_range the session label is
        'range', so the signal falls back to RANGE Sell.

        Verifies: an opposing sweep never contaminates the non-sweep signal.
        """
        df_4h = _make_4h_bearish()
        df_1h = _asian_1h_df(
            _DEFAULT_TODAY,
            box_high=51000, box_low=50000,
            prior_bar_range=14000.0, n_prior=8,   # ATR ≫ 2*box.range → 'range'
            atr_bar_range=50.0,
            sweep_direction="bullish",             # bullish sweep, bearish bias → mismatch
            now_h=12,
        )
        sig = build_session_signal(df_4h, df_1h, _MINIMAL_CFG, now_utc=self._now(12))

        assert sig is not None
        assert sig.side == "Sell"            # bearish bias → Sell
        assert sig.setup == "range"          # sweep discarded, 'range' label used
        assert sig.entry == pytest.approx(51000.0)  # box.high for short
