"""
Strategy 1 — SMC Sniper (Forex 4H → 1H chain).

Wraps the existing smc_bot/ signal chain without modification.
Target: EURUSD / GBPUSD via VT Markets MT5 (MetaAPI).

Workflow (15 steps, same as smc_bot/bot.py):
  1-2.  4H swing bias (bullish / bearish / neutral)
  3.    Fib 50% filter — long only in discount, short only in premium
  4.    4H OB/FVG POI zones (mitigation OFF — validated T21/T22)
  5.    4H BSL/SSL liquidity pools identified
  6.    Wait for price to tap a 4H POI zone
  7-8.  1H liquidity sweep (stop-hunt of prior swing)
  9.    Post-sweep 1H displacement candle (≥ displacement_atr × ATR)
  10.   1H CHoCH (structural break confirming reversal)
  11-12. 1H OB/FVG entry zone; wait for FVG retest
  13.   SL at sweep wick ± 2-pip buffer
  14.   TP at nearest BSL/SSL pool (≥1.5R) or fallback 3R
  15.   Partial plan: 50% at 1R → SL to BE → remainder at TP

Tags every signal: strategy = "SMC_SNIPER"
Magic numbers: EURUSD → 11001 | GBPUSD → 11002

Per-trial params are frozen in strategies/config.yaml (smc_sniper section).
Changing any param = new trial entry in docs/VERDICT_LOG.md.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .base import BaseStrategy, TradeSignal
from smc_bot import (
    confirmation,
    fib as fib_mod,
    liquidity,
    poi,
    structure,
    targets as tgt_mod,
)
from smc_bot._util import atr as _atr_series

log = logging.getLogger(__name__)

# ── Magic number registry ─────────────────────────────────────────────────────

_MAGIC: dict[str, int] = {
    "EURUSD": 11001,
    "GBPUSD": 11002,
}

# ── Default forex-tuned config (overridden by strategies/config.yaml) ─────────
#
# These values are the validated baseline from the dashboard _FOREX_CFG:
#   T21/T22 BTC 4H+1H PASS → ported to forex with pip-scaled adjustments.
# Do NOT change without registering a new trial.

_DEFAULT_CFG: dict = {
    "structure": {"swing_n": 3},
    "fib":       {"level": 0.5},
    "poi": {
        "ob_lookback": 60,
        "fvg_lookback": 30,
        "displacement_atr": 1.0,
        "mitigation_enabled": False,
        "mitigation_pct": 50,
        "mitigation_mode": "wick",
    },
    "liquidity": {
        "swing_n": 2,
        "lookback": 20,
        "displacement_atr": 1.0,
        "ltf_poi_lookback": 15,
        "fvg_retest_enabled": True,
        "fvg_retest_lookforward": 20,
    },
    "confirmation": {"lookback": 8},
    "targets": {
        "equal_level_tolerance": 0.0005,   # 5 pips
        "min_r": 1.5,
        "fallback_r": 3.0,
    },
    "risk": {
        "sl_buffer": 0.0002,   # 2 pips
        "target_r": 3.0,
    },
    "partials": {
        "tp1_r": 1.0,
        "tp1_pct": 0.50,
    },
}


class SMCSniper(BaseStrategy):
    """
    SMC Sniper strategy for EURUSD / GBPUSD on VT Markets MT5.

    Input:
        df_htf — 4H OHLCV (at least 200 bars)
        df_ltf — 1H OHLCV (at least 100 bars)

    Output:
        TradeSignal or None
    """

    strategy_name = "SMC_SNIPER"

    def __init__(self, cfg: dict | None = None) -> None:
        merged = dict(_DEFAULT_CFG)
        if cfg:
            for k, v in cfg.items():
                if isinstance(v, dict) and k in merged:
                    merged[k] = {**merged[k], **v}
                else:
                    merged[k] = v
        super().__init__(merged)

    def magic_number(self, symbol: str) -> int:
        return _MAGIC.get(symbol, 11000)

    def generate_signal(
        self,
        symbol:  str,
        df_htf:  pd.DataFrame,
        df_ltf:  pd.DataFrame,
    ) -> TradeSignal | None:
        """
        Run the full 15-step SMC chain.  Returns a TradeSignal when all gates
        pass, otherwise None.  Raises no exceptions — any failure is logged
        and returns None so the runner stays alive.
        """
        try:
            return self._run_chain(symbol, df_htf, df_ltf)
        except Exception as exc:
            log.warning("SMCSniper.generate_signal(%s) error: %s", symbol, exc,
                        exc_info=True)
            return None

    # ── internal chain ─────────────────────────────────────────────────────────

    def _run_chain(
        self,
        symbol:  str,
        df_htf:  pd.DataFrame,
        df_ltf:  pd.DataFrame,
    ) -> TradeSignal | None:
        cfg     = self.cfg
        price   = float(df_ltf["close"].iloc[-1])
        swing_n = cfg["structure"]["swing_n"]

        # Step 1-2: 4H swing bias
        bias = structure.get_bias(df_htf, swing_n=swing_n)
        if bias == "neutral":
            log.debug("SMCSniper %s: 4H bias neutral", symbol)
            return None

        side    = "Buy" if bias == "bullish" else "Sell"
        bullish = bias == "bullish"

        # Step 3: Fib 50% filter
        fib_mid = fib_mod.get_fib_midpoint(df_htf, bias, swing_n=swing_n)
        if fib_mid is None or not fib_mod.fib_filter(price, bias, fib_mid):
            log.debug("SMCSniper %s: Fib filter fail (price=%.5f mid=%.5f %s)",
                      symbol, price, fib_mid or 0, bias)
            return None

        # Step 4: 4H OB/FVG POI
        pc       = cfg["poi"]
        poi_raw  = poi.get_pois(
            df_htf, bias,
            ob_lookback=pc["ob_lookback"],
            fvg_lookback=pc["fvg_lookback"],
            displacement_atr=pc["displacement_atr"],
        )
        poi_zones  = [{"kind": z["kind"], "low": float(z["low"]), "high": float(z["high"])}
                      for z in poi_raw]
        active_poi = poi.price_in_poi(price, poi_zones)
        if not active_poi:
            log.debug("SMCSniper %s: price %.5f not in any 4H POI", symbol, price)
            return None

        # Step 5: BSL/SSL pools (for TP targeting later)
        tc = cfg["targets"]
        bsl = tgt_mod.get_bsl_levels(df_htf, swing_n=swing_n, tolerance=tc["equal_level_tolerance"])
        ssl = tgt_mod.get_ssl_levels(df_htf, swing_n=swing_n, tolerance=tc["equal_level_tolerance"])

        # Steps 7-8: 1H liquidity sweep
        lc    = cfg["liquidity"]
        sweep = liquidity.get_sweep(
            df_ltf, bias,
            lookback=lc["lookback"],
            swing_n=lc["swing_n"],
        )
        if not sweep:
            log.debug("SMCSniper %s: no 1H sweep", symbol)
            return None

        # Step 9: post-sweep displacement
        if not liquidity.check_displacement(
            df_ltf, sweep["bar_idx"], bias,
            atr_mult=lc["displacement_atr"],
        ):
            log.debug("SMCSniper %s: displacement not confirmed", symbol)
            return None

        # Step 10: 1H CHoCH
        lb = cfg["confirmation"]["lookback"]
        choch = confirmation.get_choch(df_ltf, bias, sweep, lookback=lb)
        if not choch:
            log.debug("SMCSniper %s: CHoCH not confirmed", symbol)
            return None

        # Steps 11-12: FVG retest gate — price must retrace into the displacement FVG
        # that was created between the sweep bar and the CHoCH bar.
        # This mirrors the logic in smc_bot/bot.py (get_owned_fvg path).
        if lc.get("fvg_retest_enabled", True):
            choch_bar = len(df_ltf) - 1
            owned_fvg = poi.get_owned_fvg(
                df_ltf, bias,
                sweep_bar        = int(sweep["bar_idx"]),
                choch_bar        = choch_bar,
                displacement_atr = lc["displacement_atr"],
            )
            if owned_fvg is None:
                log.debug("SMCSniper %s: no owned FVG in sweep→CHoCH window — skip", symbol)
                return None
            if not (owned_fvg["low"] <= price <= owned_fvg["high"]):
                log.debug(
                    "SMCSniper %s: FVG retest pending — price=%.5f not in [%.5f, %.5f]",
                    symbol, price, owned_fvg["low"], owned_fvg["high"],
                )
                return None

        # Step 13: SL at sweep wick ± pip buffer
        wick   = float(sweep["wick_extreme"])
        buf    = cfg["risk"]["sl_buffer"]
        sl     = wick * (1 - buf) if bullish else wick * (1 + buf)
        r_dist = abs(price - sl)

        if r_dist <= 0:
            log.warning("SMCSniper %s: r_dist=0, skip", symbol)
            return None

        # Step 14: TP — nearest qualifying BSL/SSL or fallback R
        tp = self._pick_tp(price, sl, side, bsl, ssl, tc)

        # Step 15: partial plan (50% at 1R → SL to BE)
        pc_cfg   = cfg["partials"]
        tp1_r    = pc_cfg.get("tp1_r", 1.0)
        tp1_pct  = pc_cfg.get("tp1_pct", 0.50)
        tp1      = price + r_dist * tp1_r if bullish else price - r_dist * tp1_r

        mag = self.magic_number(symbol)
        log.info(
            "SMCSniper SIGNAL %s %s | price=%.5f sl=%.5f tp1=%.5f tp=%.5f r=%.5f",
            side, symbol, price, sl, tp1, tp, r_dist,
        )

        return TradeSignal(
            symbol   = symbol,
            side     = side,
            entry    = price,
            sl       = sl,
            tp       = tp,
            tp1      = tp1,
            tp1_pct  = tp1_pct,
            strategy = self.strategy_name,
            setup    = f"{active_poi['kind'].lower()}_retest",
            magic    = mag,
            comment  = self._comment(symbol),
            r_dist   = r_dist,
            metadata = {
                "bias":       bias,
                "poi_kind":   active_poi["kind"],
                "sweep_bar":  int(sweep["bar_idx"]),
                "sweep_wick": wick,
            },
        )

    def _pick_tp(
        self,
        price: float,
        sl:    float,
        side:  str,
        bsl:   list[float],
        ssl:   list[float],
        tc:    dict,
    ) -> float:
        """Nearest BSL (for longs) or SSL (for shorts) that provides ≥ min_r reward."""
        r_dist  = abs(price - sl)
        min_r   = tc.get("min_r", 1.5)
        fallback = price + r_dist * tc.get("fallback_r", 3.0) \
                   if side == "Buy" else price - r_dist * tc.get("fallback_r", 3.0)

        pool = bsl if side == "Buy" else ssl
        candidates = sorted(
            [v for v in pool if (v > price if side == "Buy" else v < price)
             and abs(v - price) / r_dist >= min_r]
        )
        if candidates:
            return candidates[0] if side == "Buy" else candidates[-1]
        return fallback
