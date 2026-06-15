"""Basic sanity checks for bot/signal.py."""
import numpy as np
import pandas as pd
import pytest

from bot.signal import add_indicators, get_signal_latest


def _make_df(n: int = 300) -> pd.DataFrame:
    """Synthetic ascending price series with some volatility."""
    rng = np.random.default_rng(42)
    close = 50_000 + np.cumsum(rng.normal(0, 100, n))
    open_ = close - rng.uniform(-50, 50, n)
    high  = np.maximum(close, open_) + rng.uniform(10, 100, n)
    low   = np.minimum(close, open_) - rng.uniform(10, 100, n)
    return pd.DataFrame({
        "ts":     pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"),
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": rng.uniform(1, 10, n),
    })


def test_add_indicators_columns():
    df = add_indicators(_make_df())
    assert "ema200" in df.columns
    assert "atr"    in df.columns
    assert "swing_high" in df.columns
    assert "swing_low"  in df.columns


def test_add_indicators_no_lookahead():
    """Shifting by 1 means no bar sees its own high/low as swing reference."""
    df = add_indicators(_make_df(300))
    # swing_high is rolling(20).max().shift(1) — the first 20 values should be NaN
    assert df["swing_high"].iloc[:20].isna().all()


def test_get_signal_latest_returns_flat_on_short_df():
    df = _make_df(50)   # less than STARTUP_CANDLE
    sig = get_signal_latest(df)
    assert sig["action"] == "FLAT"
    assert sig["sl"] is None
    assert sig["tp"] is None


def test_get_signal_latest_returns_valid_keys():
    df = _make_df(300)
    sig = get_signal_latest(df)
    assert "action" in sig
    assert sig["action"] in ("LONG", "FLAT")
    if sig["action"] == "LONG":
        assert sig["sl"] is not None
        assert sig["tp"] is not None
        assert sig["tp"] > sig["sl"]
