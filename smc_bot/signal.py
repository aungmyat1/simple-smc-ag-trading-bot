"""
signal.py — unified signal entry point (PROPOSE-ONLY).

generate_signal(df_1h, df_5m, cfg) -> Signal | None

Runs the full hard-rule chain and returns a Signal with price levels and R
multiples. Never places an order — the orchestrator (bot.py) disposes.

Chain (every stage AND-gated; first failure -> None):
    structure.get_bias → poi.get_pois/price_in_poi → liquidity.get_sweep
    → confirmation.get_choch → entry_modes.<mode> → fee_gate.evaluate
    → tp_engine.build_plan → Signal

NOTE: This module implements the simplified 3-gate chain (bias→POI→sweep→CHoCH).
The live bot.py adds fib discount/premium and displacement gates on top.
Use generate_signal() for exploratory scanning; bot.py remains the authoritative
execution path for the validated 4H+1H chain.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import confirmation, entry_modes, fee_gate, liquidity, poi, structure, tp_engine


@dataclass
class Signal:
    side: str            # "Buy" / "Sell"
    entry: float
    stop: float
    order_kind: str      # "market" / "limit"
    mode: str            # which entry model fired
    reward_r: float      # runner R from TP plan
    fee_r: float
    net_ev_r: float
    tp1: float
    tp2: float
    runner: float
    tp_source: str
    reason: str


def generate_signal(
    df_1h: pd.DataFrame,
    df_5m: pd.DataFrame,
    cfg: dict,
) -> Signal | None:
    if df_1h is None or df_5m is None or df_1h.empty or df_5m.empty:
        return None

    scfg = cfg.get("structure", {})
    pcfg = cfg.get("poi", {})
    lcfg = cfg.get("liquidity", {})
    ccfg = cfg.get("confirmation", {})
    rcfg = cfg.get("risk", {})
    ecfg = cfg.get("entry", {})
    fcfg = cfg.get("fee_gate", {})
    tcfg = cfg.get("tp_engine", cfg.get("targets", {}))

    # 1. HTF bias
    bias = structure.get_bias(df_1h, swing_n=scfg.get("swing_n", 5))
    if bias == "neutral":
        return None

    # 2. POI
    pois = poi.get_pois(
        df_1h, bias,
        ob_lookback=pcfg.get("ob_lookback", 60),
        fvg_lookback=pcfg.get("fvg_lookback", 30),
        displacement_atr=pcfg.get("displacement_atr", 1.5),
    )
    if not pois:
        return None
    price = float(df_5m["close"].iloc[-1])
    active = poi.price_in_poi(price, pois)
    if active is None:
        return None

    # 3. Liquidity sweep
    sweep = liquidity.get_sweep(
        df_5m, bias,
        lookback=lcfg.get("lookback", 30),
        swing_n=lcfg.get("swing_n", 3),
    )
    if sweep is None:
        return None

    # 4. CHoCH
    if not confirmation.get_choch(df_5m, bias, sweep, lookback=ccfg.get("lookback", 10)):
        return None

    # 5. Entry model
    buf = rcfg.get("sl_buffer", 0.001)
    mode = ecfg.get("mode", "displacement_trap")
    if mode == "refined_ob":
        prop = entry_modes.refined_ob(active, bias, buf)
    elif mode == "breaker":
        prop = entry_modes.breaker(df_1h, bias, buf)
    else:
        prop = entry_modes.displacement_trap(price, sweep, bias, buf)
    if prop is None:
        return None

    side = "Buy" if bias == "bullish" else "Sell"
    target_r = rcfg.get("target_r", 2.0)

    # 6. Fee / net-EV gate
    v = fee_gate.evaluate(
        prop.entry, prop.stop,
        reward_r=target_r,
        win_prob=fcfg.get("win_prob_est", 0.40),
        max_fee_r=fcfg.get("max_fee_r", 0.15),
        min_net_ev_r=fcfg.get("min_net_ev_r", 0.0),
    )
    if not v.passed:
        return None

    # 7. TP plan
    plan = tp_engine.build_plan(
        df_1h, side, prop.entry, prop.stop,
        tp1_r=tcfg.get("tp1_r", 1.0),
        tp2_r=tcfg.get("tp2_r", 2.0),
        fallback_runner_r=tcfg.get("fallback_runner_r", tcfg.get("fallback_r", 2.0)),
        swing_n=tcfg.get("liquidity_swing_n", scfg.get("swing_n", 5)),
    )

    return Signal(
        side=side,
        entry=prop.entry,
        stop=prop.stop,
        order_kind=prop.kind,
        mode=prop.mode,
        reward_r=plan.runner_r,
        fee_r=v.fee_r,
        net_ev_r=v.net_ev_r,
        tp1=plan.tp1,
        tp2=plan.tp2,
        runner=plan.runner,
        tp_source=plan.source,
        reason="ok",
    )
