"""Tests for bot/signal.py — dual-TF SMC pipeline."""
import numpy as np
import pandas as pd
import pytest

from bot.signal import get_htf_context, get_ltf_signal, get_signal_latest


def _make_df(n: int, freq: str = "1h", seed: int = 42, bullish: bool = True) -> pd.DataFrame:
    """Synthetic OHLCV. bullish=True produces an upward-drifting series."""
    rng   = np.random.default_rng(seed)
    drift = 50 if bullish else -50
    close = 50_000 + np.cumsum(rng.normal(drift, 200, n))
    open_ = close - rng.uniform(-100, 100, n)
    high  = np.maximum(close, open_) + rng.uniform(20, 200, n)
    low   = np.minimum(close, open_) - rng.uniform(20, 200, n)
    return pd.DataFrame({
        "ts":     pd.date_range("2023-01-01", periods=n, freq=freq, tz="UTC"),
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": rng.uniform(1, 10, n),
    })


def _make_1h(n: int = 300, bullish: bool = True) -> pd.DataFrame:
    return _make_df(n, freq="1h", bullish=bullish)


def _make_5m(n: int = 600) -> pd.DataFrame:
    return _make_df(n, freq="5min", seed=99)


# ── HTF context ───────────────────────────────────────────────────────────────

def test_htf_context_keys():
    ctx = get_htf_context(_make_1h())
    assert "bias"            in ctx
    assert "poi_zones"       in ctx
    assert "fib50"           in ctx
    assert "liquidity_highs" in ctx
    assert "liquidity_lows"  in ctx


def test_htf_context_bias_values():
    ctx = get_htf_context(_make_1h(300, bullish=True))
    assert ctx["bias"] in ("bullish", "bearish", "neutral")


def test_htf_context_neutral_on_short_df():
    ctx = get_htf_context(_make_1h(10))
    assert ctx["bias"] == "neutral"
    assert ctx["poi_zones"] == []


def test_htf_fib50_is_midpoint():
    df  = _make_1h(300)
    ctx = get_htf_context(df)
    # fib50 should be between min and max of the data
    assert df["low"].min() <= ctx["fib50"] <= df["high"].max()


# ── LTF signal ────────────────────────────────────────────────────────────────

def test_ltf_flat_on_neutral_htf():
    ctx = {"bias": "neutral", "poi_zones": [], "fib50": 0.0,
           "liquidity_highs": [], "liquidity_lows": []}
    sig = get_ltf_signal(_make_5m(), ctx)
    assert sig["action"] == "FLAT"


def test_ltf_flat_on_short_df():
    ctx = {"bias": "bullish", "poi_zones": [(40000, 55000, "OB")],
           "fib50": 52000.0, "liquidity_highs": [60000.0], "liquidity_lows": []}
    sig = get_ltf_signal(_make_5m(10), ctx)
    assert sig["action"] == "FLAT"


def test_ltf_signal_returns_valid_keys():
    ctx = get_htf_context(_make_1h(300))
    sig = get_ltf_signal(_make_5m(600), ctx)
    assert "action" in sig
    assert sig["action"] in ("LONG", "FLAT")
    if sig["action"] == "LONG":
        assert sig["sl"] is not None
        assert sig["tp1"] > sig["sl"]
        assert sig["tp2"] > sig["tp1"]
        assert sig["tp_runner"] >= sig["tp2"]


# ── Combined wrapper ──────────────────────────────────────────────────────────

def test_get_signal_latest_returns_valid_keys():
    sig = get_signal_latest(_make_1h(300), _make_5m(600))
    assert "action" in sig
    assert sig["action"] in ("LONG", "FLAT")


def test_get_signal_latest_flat_on_tiny_data():
    sig = get_signal_latest(_make_1h(5), _make_5m(5))
    assert sig["action"] == "FLAT"
