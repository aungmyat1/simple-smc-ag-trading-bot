"""
tp_engine.py — structural TP plan builder (PROPOSE-ONLY).

Shim over targets.py: wraps get_tp_level() into a TpPlan dataclass that
signal.py consumes. The live bot uses targets.py directly; this module is
the unified-seam adapter for signal.generate_signal().
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import targets


@dataclass
class TpPlan:
    tp1: float
    tp2: float
    runner: float
    runner_r: float
    source: str


def build_plan(
    df: pd.DataFrame,
    side: str,
    entry: float,
    stop: float,
    tp1_r: float = 1.0,
    tp2_r: float = 2.0,
    fallback_runner_r: float = 3.0,
    swing_n: int = 5,
) -> TpPlan:
    """
    Build a 3-leg TP plan: TP1 (partial), TP2 (main), runner (BSL/SSL or fallback).

    side: "Buy" or "Sell"
    entry, stop: price levels
    """
    stop_dist = abs(entry - stop)
    bullish = side == "Buy"

    if bullish:
        tp1 = entry + tp1_r * stop_dist
        tp2 = entry + tp2_r * stop_dist
    else:
        tp1 = entry - tp1_r * stop_dist
        tp2 = entry - tp2_r * stop_dist

    bias = "bullish" if bullish else "bearish"
    pool = targets.get_tp_level(df, bias, entry, stop_dist, swing_n=swing_n)

    if pool is not None:
        runner = pool
        runner_r = abs(pool - entry) / stop_dist if stop_dist > 0 else fallback_runner_r
        source = "BSL/SSL"
    else:
        runner_r = fallback_runner_r
        runner = (entry + fallback_runner_r * stop_dist) if bullish else (entry - fallback_runner_r * stop_dist)
        source = "fallback"

    return TpPlan(tp1=tp1, tp2=tp2, runner=runner, runner_r=runner_r, source=source)
