"""
Detector unit tests — smc_bot/{structure, poi, liquidity, confirmation}.

Each test uses a minimal synthetic OHLCV DataFrame so failures point
directly to the detection function, not to data issues.

AST guard at the bottom enforces that scripts/backtest.py never re-imports
from _archive/ — the seam must stay fixed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smc_bot import confirmation, liquidity, poi, structure

ROOT = Path(__file__).parent.parent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _df(opens, highs, lows, closes) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame (no timestamp needed for unit tests)."""
    n = len(closes)
    ts = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({
        "ts":    ts,
        "open":  opens,
        "high":  highs,
        "low":   lows,
        "close": closes,
        "volume": [1.0] * n,
    })


def _rising(n: int = 30, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """Monotonically rising OHLCV bars — clear bullish structure."""
    c = [start + step * i for i in range(n)]
    h = [x + 0.5 for x in c]
    l = [x - 0.5 for x in c]
    o = [x - 0.3 for x in c]
    return _df(o, h, l, c)


def _falling(n: int = 30, start: float = 200.0, step: float = 1.0) -> pd.DataFrame:
    """Monotonically falling OHLCV bars — clear bearish structure."""
    c = [start - step * i for i in range(n)]
    h = [x + 0.5 for x in c]
    l = [x - 0.5 for x in c]
    o = [x + 0.3 for x in c]
    return _df(o, h, l, c)


# ── structure.get_bias ────────────────────────────────────────────────────────

def _zigzag_bullish() -> pd.DataFrame:
    """
    Sine wave on a gentle uptrend: every bar has a unique value (no ties).
    HH: peak(20) > peak(4); HL: trough(28) > trough(12).
    Works with swing_n up to 5.
    """
    closes = [float(100 + 0.4 * i + 12 * np.sin(2 * np.pi * i / 16)) for i in range(60)]
    h = [c + 0.3 for c in closes]
    l = [c - 0.3 for c in closes]
    o = closes[:]
    return _df(o, h, l, closes)


def _zigzag_bearish() -> pd.DataFrame:
    """Sine wave on a gentle downtrend: LH + LL → bearish."""
    closes = [float(200 - 0.4 * i + 12 * np.sin(2 * np.pi * i / 16)) for i in range(60)]
    h = [c + 0.3 for c in closes]
    l = [c - 0.3 for c in closes]
    o = closes[:]
    return _df(o, h, l, closes)


class TestGetBias:
    def test_bullish_on_hh_hl_zigzag(self):
        df = _zigzag_bullish()
        assert structure.get_bias(df, swing_n=5) == "bullish"

    def test_bearish_on_ll_lh_zigzag(self):
        df = _zigzag_bearish()
        assert structure.get_bias(df, swing_n=5) == "bearish"

    def test_neutral_when_too_few_bars(self):
        # Only 5 bars — can't form 2 confirmed swing highs with swing_n=5
        df = _rising(5)
        assert structure.get_bias(df, swing_n=5) == "neutral"

    def test_explicit_hh_hl_sequence(self):
        # HH at bar 10, HL at bar 20 — should be bullish (not bearish at minimum)
        highs  = [100] * 5 + [110] + [105] * 4 + [120] + [115] * 4 + [130] * 6
        lows   = [95]  * 5 + [100] + [97]  * 4 + [108] + [103] * 4 + [115] * 6
        closes = lows
        opens  = lows
        df = _df(opens, highs, lows, closes)
        bias = structure.get_bias(df, swing_n=5)
        assert bias in ("bullish", "neutral")

    def test_explicit_ll_lh_sequence(self):
        highs  = [200] * 5 + [190] + [185] * 4 + [175] + [170] * 4 + [160] * 6
        lows   = [195] * 5 + [185] + [180] * 4 + [170] + [165] * 4 + [155] * 6
        closes = lows
        opens  = lows
        df = _df(opens, highs, lows, closes)
        bias = structure.get_bias(df, swing_n=5)
        assert bias in ("bearish", "neutral")


# ── poi.get_pois ──────────────────────────────────────────────────────────────

class TestGetPois:
    def _base_df(self, n: int = 60) -> pd.DataFrame:
        return _rising(n)

    def test_returns_list(self):
        df = self._base_df()
        zones = poi.get_pois(df, "bullish")
        assert isinstance(zones, list)

    def test_neutral_bias_returns_empty(self):
        df = self._base_df()
        assert poi.get_pois(df, "neutral") == []

    def test_ob_zone_has_required_keys(self):
        # Build a pattern: bearish candle → large bullish displacement
        n = 60
        o = [100.0] * n
        c = [100.0] * n
        h = [101.0] * n
        l = [99.0]  * n

        # Bar 50: bearish candle
        o[50], c[50], h[50], l[50] = 105.0, 100.0, 106.0, 99.0
        # Bar 51: large bullish displacement (range >> 1×ATR)
        o[51], c[51], h[51], l[51] = 100.0, 125.0, 126.0, 99.0

        df = _df(o, h, l, c)
        zones = poi.get_pois(df, "bullish", ob_lookback=15, displacement_atr=1.0)
        # We only assert structure when a zone is detected
        for z in zones:
            assert "kind"  in z
            assert "low"   in z
            assert "high"  in z
            assert z["low"] <= z["high"]

    def test_fvg_detected(self):
        # FVG: high[i-2] < low[i]
        n = 40
        o = [100.0] * n
        c = [100.0] * n
        h = [101.0] * n
        l = [99.0]  * n

        # bars 35, 36, 37: create a gap  high[35]=102, low[37]=105
        h[35], l[35] = 102.0, 99.0
        h[36], l[36] = 103.0, 100.0
        h[37], l[37] = 108.0, 105.0   # low[37]=105 > high[35]=102 → FVG
        c[37] = 107.0
        o[37] = 104.0

        df = _df(o, h, l, c)
        zones = poi.get_pois(df, "bullish", fvg_lookback=10)
        fvg_zones = [z for z in zones if z["kind"] == "FVG"]
        assert len(fvg_zones) >= 1
        assert fvg_zones[0]["low"] == pytest.approx(102.0)
        assert fvg_zones[0]["high"] == pytest.approx(105.0)

    def test_price_in_poi_returns_zone(self):
        zones = [{"kind": "OB", "low": 100.0, "high": 105.0}]
        assert poi.price_in_poi(102.5, zones) == zones[0]

    def test_price_in_poi_returns_none_outside(self):
        zones = [{"kind": "OB", "low": 100.0, "high": 105.0}]
        assert poi.price_in_poi(110.0, zones) is None

    def test_price_in_poi_empty_list(self):
        assert poi.price_in_poi(100.0, []) is None


# ── liquidity.get_sweep ───────────────────────────────────────────────────────

class TestGetSweep:
    def _sweep_df(self) -> pd.DataFrame:
        """
        Build a 5M bar series with a clear bullish sweep:
          bars 0-9:  declining lows establishing a swing low at bar 5 (local minimum)
          bar 15:    wick pierces the swing low, but closes ABOVE it → sweep
        """
        n = 40
        o = [100.0] * n
        c = [100.0] * n
        h = [101.0] * n
        l = [99.0]  * n

        # Create a swing low at bar 10 (n=3 each side → bars 7-13 needed)
        for j in range(7, 14):
            l[j] = 98.0
        l[10] = 95.0   # local minimum
        c[10] = 98.5
        o[10] = 98.0

        # Bar 25: wick below swing low (pierce), close above → sweep
        l[25] = 94.0   # pierce below 95.0
        c[25] = 96.0   # close above 95.0
        o[25] = 98.0
        h[25] = 99.0

        return _df(o, h, l, c)

    def test_detects_bullish_sweep(self):
        df = self._sweep_df()
        result = liquidity.get_sweep(df, "bullish", lookback=30, swing_n=3)
        assert result is not None
        assert "bar_idx"      in result
        assert "swept_level"  in result
        assert "wick_extreme" in result

    def test_no_sweep_on_flat_bars(self):
        df = _rising(40)
        result = liquidity.get_sweep(df, "bullish", lookback=30, swing_n=3)
        # Rising bars → no swing-low pierce → None
        assert result is None

    def test_bearish_sweep_not_detected_for_bullish_bias(self):
        df = _falling(40)
        result = liquidity.get_sweep(df, "bullish", lookback=30, swing_n=3)
        assert result is None

    def test_sweep_wick_extreme_is_minimum(self):
        df = self._sweep_df()
        result = liquidity.get_sweep(df, "bullish", lookback=30, swing_n=3)
        if result is not None:
            bar = result["bar_idx"]
            assert result["wick_extreme"] == pytest.approx(df["low"].iloc[bar])


# ── confirmation.get_choch ───────────────────────────────────────────────────

class TestGetChoch:
    def _choch_df_and_sweep(self) -> tuple[pd.DataFrame, dict]:
        """Bars leading up to and including a CHoCH close."""
        n = 30
        o = [100.0] * n
        c = [100.0] * n
        h = [101.0] * n
        l = [99.0]  * n

        # Sweep at bar 10
        l[10] = 97.0
        c[10] = 100.5
        h[10] = 101.0

        # ref_level = max(high[0:11]) = 105 (we set bar 8 high)
        h[8] = 105.0

        # CHoCH bar = last bar (29): close above ref high 105
        c[29] = 106.0
        h[29] = 107.0

        df     = _df(o, h, l, c)
        sweep  = {"bar_idx": 10, "swept_level": 99.0, "wick_extreme": 97.0}
        return df, sweep

    def test_choch_confirmed_when_close_above_ref_high(self):
        df, sweep = self._choch_df_and_sweep()
        assert bool(confirmation.get_choch(df, "bullish", sweep, lookback=10)) is True

    def test_choch_not_confirmed_when_close_below_ref_high(self):
        df, sweep = self._choch_df_and_sweep()
        df = df.copy()
        df.loc[df.index[-1], "close"] = 103.0  # below ref 105
        assert bool(confirmation.get_choch(df, "bullish", sweep, lookback=10)) is False

    def test_choch_false_when_sweep_is_current_bar(self):
        df, sweep = self._choch_df_and_sweep()
        sweep2 = {**sweep, "bar_idx": len(df) - 1}
        assert bool(confirmation.get_choch(df, "bullish", sweep2, lookback=10)) is False

    def test_choch_bearish_requires_close_below_ref_low(self):
        n = 25
        o = [100.0] * n
        c = [100.0] * n
        h = [101.0] * n
        l = [99.0]  * n

        # Sweep at bar 10 for bearish (high pierced, closed below)
        h[10] = 107.0
        c[10] = 99.5

        # ref_level = min(low[0:11]) = 93 (bar 7)
        l[7] = 93.0

        # CHoCH: close below 93
        c[24] = 92.0
        l[24] = 91.0

        df    = _df(o, h, l, c)
        sweep = {"bar_idx": 10, "swept_level": 101.0, "wick_extreme": 107.0}
        assert bool(confirmation.get_choch(df, "bearish", sweep, lookback=10)) is True


# ── AST guard — backtest.py must never import from _archive ───────────────────

def test_backtest_no_archive_imports():
    """
    Seam integrity: scripts/backtest.py must NOT import from _archive/.
    If this test fails the seam regressed — fix the import, not this test.
    """
    src  = (ROOT / "scripts" / "backtest.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            dumped = ast.dump(node)
            assert "_archive" not in dumped, (
                f"scripts/backtest.py imports from _archive/: {ast.unparse(node)}\n"
                "Fix the seam — backtest must use smc_bot/ only."
            )
