"""
Unit tests for the Trial 6 mitigation / freshness filter.

Rules under test (poi.filter_fresh_zones):
  - A bullish zone is mitigated when any bar after creation has low  ≤ midpoint.
  - A bearish zone is mitigated when any bar after creation has high ≥ midpoint.
  - Fresh zones (midpoint never touched) are passed through unchanged.
"""
import pandas as pd
import pytest

from smc_bot.poi import filter_fresh_zones


def _df(highs, lows):
    return pd.DataFrame({"high": highs, "low": lows, "open": highs, "close": lows})


# ── Test 1: fresh zone accepted ──────────────────────────────────────────────

def test_fresh_zone_accepted():
    """A bullish zone whose midpoint is never touched by subsequent bars stays fresh."""
    # Zone: low=100, high=110, midpoint=105, created at bar 0.
    # Bars 1-3 all have lows above 105 → zone is unmitigated.
    df   = _df(highs=[115, 114, 113], lows=[106, 108, 107])
    zone = {"kind": "OB", "low": 100.0, "high": 110.0, "creation_bar": 0}

    result = filter_fresh_zones([zone], df, bias="bullish")

    assert result == [zone]


# ── Test 2: midpoint touched → zone rejected ─────────────────────────────────

def test_midpoint_touched_zone_rejected():
    """A bullish zone is rejected when a subsequent bar's low touches the midpoint."""
    # Zone: low=100, high=110, midpoint=105.
    # Bar 1 has low=104 ≤ 105 → mitigated.
    df   = _df(highs=[115, 106], lows=[108, 104])
    zone = {"kind": "OB", "low": 100.0, "high": 110.0, "creation_bar": 0}

    result = filter_fresh_zones([zone], df, bias="bullish")

    assert result == []


def test_midpoint_exact_touch_rejected():
    """A zone is mitigated when the bar low equals the midpoint exactly."""
    # midpoint = (100 + 110) / 2 = 105; low[1] = 105 → low <= mid → mitigated
    df   = _df(highs=[115, 110], lows=[108, 105])
    zone = {"kind": "OB", "low": 100.0, "high": 110.0, "creation_bar": 0}

    result = filter_fresh_zones([zone], df, bias="bullish")

    assert result == []


# ── Test 3: multiple zones, mixed fresh and mitigated ────────────────────────

def test_multiple_zones_only_fresh_returned():
    """When a mix of fresh and mitigated zones is given, only fresh ones survive."""
    # Zone A: low=50, high=70, midpoint=60. Subsequent lows [106, 104] both > 60 → FRESH.
    # Zone B: low=100, high=110, midpoint=105. Bar at index 2 has low=104 ≤ 105 → MITIGATED.
    # Both zones have creation_bar=0; bars 1-2 are checked.
    df     = _df(highs=[120, 115, 112], lows=[60, 106, 104])
    zone_a = {"kind": "OB", "low": 50.0,  "high": 70.0,  "creation_bar": 0}
    zone_b = {"kind": "OB", "low": 100.0, "high": 110.0, "creation_bar": 0}

    result = filter_fresh_zones([zone_a, zone_b], df, bias="bullish")

    assert len(result) == 1
    assert result[0] is zone_a


# ── Bearish symmetry check ───────────────────────────────────────────────────

def test_bearish_zone_mitigated_by_high():
    """A bearish zone is rejected when a subsequent bar's high reaches the midpoint."""
    # Zone: low=110, high=120, midpoint=115. Bar 1 has high=116 ≥ 115 → mitigated.
    df   = _df(highs=[108, 116], lows=[100, 105])
    zone = {"kind": "OB", "low": 110.0, "high": 120.0, "creation_bar": 0}

    result = filter_fresh_zones([zone], df, bias="bearish")

    assert result == []


def test_bearish_fresh_zone_accepted():
    """A bearish zone whose midpoint is never reached by subsequent highs stays fresh."""
    # Zone: low=110, high=120, midpoint=115. Subsequent highs [108, 109, 107] < 115 → fresh.
    df   = _df(highs=[108, 109, 107], lows=[100, 102, 101])
    zone = {"kind": "OB", "low": 110.0, "high": 120.0, "creation_bar": 0}

    result = filter_fresh_zones([zone], df, bias="bearish")

    assert result == [zone]


# ── Edge: zone created at last bar — no subsequent bars to check ─────────────

def test_zone_at_last_bar_always_fresh():
    """A zone created at the last bar has no subsequent bars and is always fresh."""
    df   = _df(highs=[115, 110], lows=[108, 104])
    zone = {"kind": "OB", "low": 100.0, "high": 110.0, "creation_bar": 1}

    result = filter_fresh_zones([zone], df, bias="bullish")

    assert result == [zone]


# ── Zones without creation_bar are passed through (backward-compat guard) ────

def test_zone_without_creation_bar_passes_through():
    """Zones missing creation_bar are not filtered (safe fallback for old callers)."""
    df   = _df(highs=[115, 104], lows=[108, 100])
    zone = {"kind": "OB", "low": 100.0, "high": 110.0}  # no creation_bar

    result = filter_fresh_zones([zone], df, bias="bullish")

    assert result == [zone]
