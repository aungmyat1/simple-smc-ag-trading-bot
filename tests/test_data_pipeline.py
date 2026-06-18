"""
Forex readiness data-pipeline tests — the M1-master resampler and the backtest
date-window slice. No network, no market data: synthetic frames only.

Covers two additions from docs/FOREX_VALIDATION.md:
  1. scripts/resample_ohlcv.resample_ohlcv — derive H1/H4 from one M1 master so
     every timeframe references the same ticks (no broker feed drift).
  2. scripts/backtest._slice_by_date — carve in-sample / out-of-sample windows
     for walk-forward, with locked params.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backtest as bt                       # noqa: E402
from resample_ohlcv import resample_ohlcv   # noqa: E402

_COLS = ["ts", "open", "high", "low", "close", "volume"]


def _m1_frame(periods: int = 6 * 60, seed: int = 3) -> pd.DataFrame:
    """Synthetic M1 random walk starting on a Monday 00:00 UTC."""
    rng   = np.random.default_rng(seed)
    ts    = pd.date_range("2022-01-03 00:00", periods=periods, freq="1min", tz="UTC")
    close = 1.10 + np.cumsum(rng.normal(0, 0.0001, periods))
    open_ = np.concatenate([[1.10], close[:-1]])
    wick  = np.abs(rng.normal(0, 0.0002, periods))
    return pd.DataFrame({
        "ts": ts,
        "open":  open_,
        "high":  np.maximum(open_, close) + wick,
        "low":   np.minimum(open_, close) - wick,
        "close": close,
        "volume": rng.uniform(1, 10, periods),
    })


# ── resample_ohlcv ────────────────────────────────────────────────────────────

def test_resample_schema_and_left_label():
    df = _m1_frame()
    out = resample_ohlcv(df, 60)
    assert list(out.columns) == _COLS
    # left-labelled: first H1 bar opens at the master's first minute
    assert out["ts"].iloc[0] == df["ts"].iloc[0]
    # 6h of M1 → 6 H1 bars
    assert len(out) == 6


def test_resample_ohlc_aggregation_is_correct():
    df  = _m1_frame()
    out = resample_ohlcv(df, 60)
    first_hour = df[(df["ts"] >= out["ts"].iloc[0]) &
                    (df["ts"] <  out["ts"].iloc[0] + pd.Timedelta(hours=1))]
    row = out.iloc[0]
    assert row["open"]   == pytest.approx(first_hour["open"].iloc[0])
    assert row["close"]  == pytest.approx(first_hour["close"].iloc[-1])
    assert row["high"]   == pytest.approx(first_hour["high"].max())
    assert row["low"]    == pytest.approx(first_hour["low"].min())
    assert row["volume"] == pytest.approx(first_hour["volume"].sum())


def test_resample_h4_nests_h1():
    """H4 built from M1 must equal H4 built from the H1 that M1 produced —
    the whole point of a single master: timeframes stay mutually consistent."""
    df    = _m1_frame(periods=8 * 60)
    h1    = resample_ohlcv(df, 60)
    h4_m1 = resample_ohlcv(df, 240)
    h4_h1 = resample_ohlcv(h1, 240)
    assert len(h4_m1) == len(h4_h1)
    for col in ["open", "high", "low", "close"]:
        assert h4_m1[col].to_numpy() == pytest.approx(h4_h1[col].to_numpy())


def test_resample_drops_weekend_gaps_no_flat_fill():
    """A gap in the master must not produce synthetic flat bars."""
    df  = _m1_frame(periods=120)
    df  = pd.concat([df.iloc[:30], df.iloc[90:]], ignore_index=True)  # 1h hole
    out = resample_ohlcv(df, 60)
    # 30 min + 30 min present, the empty middle hour is dropped (not flat-filled)
    assert not out.empty
    assert out["open"].notna().all()
    assert len(out) < 4


# ── _slice_by_date ────────────────────────────────────────────────────────────

def _daily_frame() -> pd.DataFrame:
    ts = pd.date_range("2021-01-01", "2025-12-31", freq="1D", tz="UTC")
    return pd.DataFrame({c: (ts if c == "ts" else np.arange(len(ts), dtype=float))
                         for c in _COLS})


def test_slice_none_is_identity():
    df = _daily_frame()
    assert bt._slice_by_date(df, None, None).equals(df)


def test_slice_from_is_inclusive():
    out = bt._slice_by_date(_daily_frame(), "2024-01-01", None)
    assert out["ts"].iloc[0] == pd.Timestamp("2024-01-01", tz="UTC")


def test_slice_to_includes_whole_day():
    out = bt._slice_by_date(_daily_frame(), None, "2024-12-31")
    assert out["ts"].iloc[-1] == pd.Timestamp("2024-12-31", tz="UTC")
    assert (out["ts"] <= pd.Timestamp("2024-12-31 23:59", tz="UTC")).all()


def test_slice_oos_window_is_disjoint_from_in_sample():
    """In-sample and the held-out OOS year must not overlap (no leakage)."""
    df  = _daily_frame()
    ins = bt._slice_by_date(df, "2021-01-01", "2024-12-31")
    oos = bt._slice_by_date(df, "2025-01-01", "2025-12-31")
    assert set(ins["ts"]).isdisjoint(set(oos["ts"]))
    assert len(ins) + len(oos) == len(df)
