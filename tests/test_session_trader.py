"""
Unit tests for SessionTrader strategy.

Uses synthetic OHLCV DataFrames — no network calls, no file I/O.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.session_trader import SessionTrader, _DEFAULT_CFG


def _make_df(
    n: int = 100,
    base_price: float = 1.0800,
    start_hour: int = 6,
    noise: float = 0.0005,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate a synthetic 1H OHLCV DataFrame with UTC timestamps."""
    rng   = np.random.default_rng(seed)
    times = pd.date_range("2026-06-18", periods=n, freq="1h", tz="UTC")
    times = times.map(lambda t: t.replace(hour=(start_hour + times.get_loc(t)) % 24))
    closes = base_price + np.cumsum(rng.normal(0, noise, n))
    opens  = closes - rng.normal(0, noise / 2, n)
    highs  = np.maximum(opens, closes) + rng.uniform(0, noise, n)
    lows   = np.minimum(opens, closes) - rng.uniform(0, noise, n)
    return pd.DataFrame({
        "ts":     times,
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": rng.uniform(100, 500, n),
    })


def _make_htf(n: int = 60, **kw) -> pd.DataFrame:
    return _make_df(n=n, **kw)


# ── instantiation ─────────────────────────────────────────────────────────────

def test_default_instantiation():
    st = SessionTrader()
    assert st.strategy_name == "SESSION_TRADER"
    assert st.magic_number("EURUSD") == 12001
    assert st.magic_number("GBPUSD") == 12002


def test_cfg_override():
    st = SessionTrader({"sweep_beyond_pips": 5.0, "min_range_pips": 20.0})
    assert st.cfg["sweep_beyond_pips"] == 5.0
    assert st.cfg["min_range_pips"] == 20.0
    # Untouched defaults preserved
    assert st.cfg["pip_size"] == _DEFAULT_CFG["pip_size"]


# ── session detection ─────────────────────────────────────────────────────────

def test_active_session_london():
    from datetime import datetime, timezone
    st  = SessionTrader()
    now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    assert st._active_session(now) == "London"


def test_active_session_ny():
    from datetime import datetime, timezone
    st  = SessionTrader()
    now = datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc)
    assert st._active_session(now) == "New York"


def test_active_session_overlap():
    from datetime import datetime, timezone
    st  = SessionTrader()
    now = datetime(2026, 6, 18, 14, 0, tzinfo=timezone.utc)
    assert st._active_session(now) == "Overlap"


def test_active_session_none():
    from datetime import datetime, timezone
    st  = SessionTrader()
    now = datetime(2026, 6, 18, 4, 0, tzinfo=timezone.utc)
    assert st._active_session(now) is None


# ── _find_session_sweep (running range + sweep combined) ─────────────────────

def test_find_session_sweep_returns_none_when_no_session_bars():
    from datetime import datetime, timezone
    st  = SessionTrader()
    n   = 7
    ts  = [pd.Timestamp(f"2026-06-18 0{h}:00:00", tz="UTC") for h in range(1, 8)]
    df  = pd.DataFrame({
        "ts": ts, "open": [1.080]*n, "high": [1.081]*n,
        "low": [1.079]*n, "close": [1.080]*n, "volume": [100.0]*n,
    })
    now = datetime(2026, 6, 18, 7, 30, tzinfo=timezone.utc)
    assert st._find_session_sweep(df, "London", now) is None


def test_find_session_sweep_bullish():
    """
    IB bars 08-09 build range (high=1.0820, low=1.0790).
    Trading-phase bar 10: wicks below low-1pip, closes above low  → bullish sweep.
    Bar 11: closes above IB high (1.0820) → CHoCH confirmed.
    """
    from datetime import datetime, timezone
    cfg = {"sweep_beyond_pips": 1.0, "min_range_pips": 0.1,
           "london": {"start_h": 8, "end_h": 16, "range_start_h": 8, "range_end_h": 10},
           "new_york": {"start_h": 13, "end_h": 21, "range_start_h": 13, "range_end_h": 15}}
    st = SessionTrader(cfg)
    n  = 14   # 00:00 – 13:00
    ts = pd.date_range("2026-06-18 00:00", periods=n, freq="1h", tz="UTC")
    highs  = [1.0810] * n
    lows   = [1.0800] * n
    closes = [1.0805] * n
    # IB bars 8-9: high=1.0820, low=1.0790
    for j in [8, 9]:
        highs[j]  = 1.0820
        lows[j]   = 1.0790
        closes[j] = 1.0805
    # Trading bar 10: wick to 1.0779 (below 1.0789 = 1.0790-1pip), close above low
    highs[10]  = 1.0815; lows[10]  = 1.0779; closes[10] = 1.0800
    # Trading bar 11: stays inside range
    highs[11]  = 1.0818; lows[11]  = 1.0795; closes[11] = 1.0810
    # Trading bar 12: CHoCH — closes ABOVE IB high 1.0820
    highs[12]  = 1.0835; lows[12]  = 1.0805; closes[12] = 1.0825
    # Bar 13: current bar (now=13:00 → London session, trading phase)
    highs[13]  = 1.0828; lows[13]  = 1.0812; closes[13] = 1.0820
    df = pd.DataFrame({"ts": ts, "open": closes, "high": highs, "low": lows,
                       "close": closes, "volume": [100.0]*n})
    now = datetime(2026, 6, 18, 13, 0, tzinfo=timezone.utc)
    sweep = st._find_session_sweep(df, "London", now)
    assert sweep is not None, "Expected bullish sweep+CHoCH"
    assert sweep["direction"] == "bullish"
    assert sweep["wick_extreme"] == pytest.approx(1.0779)
    assert sweep["rng_low"]    == pytest.approx(1.0790)
    assert sweep["choch_bar"]  is not None


def test_find_session_sweep_none_when_no_wick():
    from datetime import datetime, timezone
    st = SessionTrader({"sweep_beyond_pips": 2.0, "min_range_pips": 0.1})
    n  = 12
    ts = pd.date_range("2026-06-18 00:00", periods=n, freq="1h", tz="UTC")
    highs  = [1.0810 if 8 <= t.hour < 12 else 1.0805 for t in ts]
    lows   = [1.0795 if 8 <= t.hour < 12 else 1.0800 for t in ts]
    closes = [1.0803] * n
    df = pd.DataFrame({"ts": ts, "open": closes, "high": highs, "low": lows,
                       "close": closes, "volume": [100.0]*n})
    now = datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)
    # Last bar (11:00) must sweep range built by 08-10, but it stays inside range
    assert st._find_session_sweep(df, "London", now) is None


# ── TP calculator ─────────────────────────────────────────────────────────────

def test_session_tp_london_buy():
    st  = SessionTrader()
    rng = {"high": 1.0820, "low": 1.0780, "range_pips": 40.0, "open_price": 1.0800}
    tp  = st._session_tp(1.0800, rng, "London", "Buy", sl=1.0770)
    # r = 0.0030; projection = high + range*(1.5-1) = 1.0820+0.0020 = 1.0840
    # fallback 3R = 1.0800+0.009 = 1.0890; max(1.0840, 1.0890) = 1.0890
    assert tp > 1.0820, "TP must be above session high"


def test_session_tp_ny_sell():
    st  = SessionTrader()
    rng = {"high": 1.0820, "low": 1.0780, "range_pips": 40.0, "open_price": 1.0800}
    tp  = st._session_tp(1.0800, rng, "New York", "Sell", sl=1.0830)
    assert tp < 1.0780, "TP must be below session low for shorts"


# ── TradeSignal fields ────────────────────────────────────────────────────────

def test_signal_fields_when_returned(monkeypatch):
    """Patch _run_chain to skip complex data deps and verify TradeSignal fields."""
    from strategies.base import TradeSignal

    st = SessionTrader()
    expected = TradeSignal(
        symbol="EURUSD", side="Buy", entry=1.0800, sl=1.0770, tp=1.0890,
        tp1=1.0820, tp1_pct=0.75,
        strategy="SESSION_TRADER", setup="london_sweep",
        magic=12001, comment="SESSION_TRADER_EURUSD",
        r_dist=0.0030,
    )

    monkeypatch.setattr(st, "_run_chain", lambda sym, htf, ltf: expected)
    df = _make_df()
    result = st.generate_signal("EURUSD", df, df)
    assert result is not None
    assert result.strategy    == "SESSION_TRADER"
    assert result.magic       == 12001
    assert result.tp1_pct     == 0.75
    assert result.side        == "Buy"


def test_generate_signal_returns_none_on_error():
    """generate_signal must never raise — returns None on any internal exception."""
    st  = SessionTrader()
    df  = pd.DataFrame()   # empty — will cause errors inside _run_chain
    out = st.generate_signal("EURUSD", df, df)
    assert out is None
