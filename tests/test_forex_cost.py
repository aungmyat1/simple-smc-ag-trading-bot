"""
Step 5 — forex transaction-cost model tests.

Covers scripts/backtest.py::_cost_r and its wiring into the session-box
backtest (run_backtest_asian). No network and no market data required — the
end-to-end smoke runs on a synthetic random-walk frame.

The point of these tests: prove the forex cost regime is (a) mathematically
correct and (b) materially cheaper than the Bybit %-of-notional model, which
is the whole reason a forex run needs its own cost model.
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

import backtest as bt  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cost_globals():
    """Each test sets its own regime; restore defaults afterward."""
    saved = (bt.COST_MODEL, bt.SPREAD_PIPS, bt.COMMISSION_RT_PIPS, bt.PIP_SIZE)
    yield
    bt.COST_MODEL, bt.SPREAD_PIPS, bt.COMMISSION_RT_PIPS, bt.PIP_SIZE = saved


# ── _cost_r math ──────────────────────────────────────────────────────────────

def test_pct_model_matches_bybit_notional():
    """Default 'pct' regime = ROUND_TRIP × entry / stop_dist (unchanged BTC math)."""
    bt.COST_MODEL = "pct"
    entry, stop = 30_000.0, 150.0      # BTC: 0.5% stop
    expected = bt.ROUND_TRIP * entry / stop
    assert bt._cost_r(entry, stop) == pytest.approx(expected)


def test_forex_model_is_pip_based_not_notional():
    """forex cost = (spread + commission) pips × pip_size / stop_dist."""
    bt.COST_MODEL = "forex"
    bt.SPREAD_PIPS, bt.COMMISSION_RT_PIPS, bt.PIP_SIZE = 0.8, 0.6, 0.0001
    entry, stop = 1.1000, 0.0020       # EURUSD: 20-pip stop
    expected = (0.8 + 0.6) * 0.0001 / 0.0020   # 1.4 pip / 20 pip = 0.07 R
    assert bt._cost_r(entry, stop) == pytest.approx(expected)
    assert bt._cost_r(entry, stop) == pytest.approx(0.07)


def test_forex_cost_independent_of_price_level():
    """Pip cost does not scale with notional — same R at EURUSD 1.05 or 1.20."""
    bt.COST_MODEL = "forex"
    bt.SPREAD_PIPS, bt.COMMISSION_RT_PIPS, bt.PIP_SIZE = 0.8, 0.6, 0.0001
    stop = 0.0020
    assert bt._cost_r(1.05, stop) == pytest.approx(bt._cost_r(1.20, stop))


def test_pct_model_would_overcharge_forex_roughly_10x():
    """Why the forex model exists: the %-model is ~10x too expensive on EURUSD."""
    entry, stop = 1.1000, 0.0020
    bt.COST_MODEL = "pct"
    pct_cost = bt._cost_r(entry, stop)
    bt.COST_MODEL = "forex"
    bt.SPREAD_PIPS, bt.COMMISSION_RT_PIPS, bt.PIP_SIZE = 0.8, 0.6, 0.0001
    forex_cost = bt._cost_r(entry, stop)
    assert pct_cost > 8 * forex_cost     # 0.66R vs 0.07R ≈ 9.4x


def test_jpy_pip_size():
    """JPY pairs use pip_size 0.01."""
    bt.COST_MODEL = "forex"
    bt.SPREAD_PIPS, bt.COMMISSION_RT_PIPS, bt.PIP_SIZE = 1.0, 0.6, 0.01
    entry, stop = 150.00, 0.30          # USDJPY: 30-pip stop
    expected = (1.0 + 0.6) * 0.01 / 0.30
    assert bt._cost_r(entry, stop) == pytest.approx(expected)


def test_zero_stop_is_safe():
    bt.COST_MODEL = "forex"
    assert bt._cost_r(1.10, 0.0) == 0.0
    assert bt._cost_r(1.10, -1.0) == 0.0


# ── End-to-end wiring: run_backtest_asian honours the forex cost model ─────────

def _synth_forex_frames(n_days: int = 400, seed: int = 7):
    """Synthetic EURUSD-like 1H + 4H random walk with intraday structure.

    Not a market model — just enough OHLC shape to drive the session-box chain
    so the cost-application path is exercised. Verdicts from this are meaningless;
    only the per-trade fee identity is asserted.
    """
    rng = np.random.default_rng(seed)
    periods = n_days * 24
    start = pd.Timestamp("2022-01-01", tz="UTC")
    ts = pd.date_range(start, periods=periods, freq="1h")
    # random walk around 1.10 with ~0.0001 hourly steps
    close = 1.10 + np.cumsum(rng.normal(0, 0.0004, periods))
    open_ = np.concatenate([[1.10], close[:-1]])
    spread = np.abs(rng.normal(0, 0.0006, periods))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    df_1h = pd.DataFrame({
        "ts": ts, "open": open_, "high": high, "low": low,
        "close": close, "volume": rng.uniform(100, 1000, periods),
    })
    # 4H resample for macro bias
    df_4h = (
        df_1h.set_index("ts")
        .resample("4h")
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    return df_4h, df_1h


def test_run_backtest_asian_runs_with_forex_cost_and_applies_pip_fee():
    df_4h, df_1h = _synth_forex_frames()
    bt._precompute(df_4h, df_1h)        # stores 4H/1H arrays in the _1H/_5M globals

    bt.COST_MODEL = "forex"
    bt.SPREAD_PIPS, bt.COMMISSION_RT_PIPS, bt.PIP_SIZE = 0.8, 0.6, 0.0001

    stats = bt.run_backtest_asian(df_4h=df_4h, df_1h=df_1h, side="both")

    # well-formed result regardless of trade count
    for key in ("n", "gross_pf", "net_pf", "win_rate", "avg_fee_r", "trades"):
        assert key in stats

    # for every trade actually produced, the recorded fee must equal the
    # forex pip cost (proves _cost_r is wired in, not the %-model)
    for t in stats["trades"]:
        stop_dist = abs(t["entry"] - t["sl"])
        expected_fee = bt._cost_r(t["entry"], stop_dist)
        # entry/sl logged at 5 dp → stop_dist recompute carries small rounding error
        assert t["fee_r"] == pytest.approx(expected_fee, rel=3e-2)
        # all three columns stored at 4 dp → identity holds within rounding
        assert t["net_r"] == pytest.approx(t["gross_r"] - t["fee_r"], abs=1e-3)
