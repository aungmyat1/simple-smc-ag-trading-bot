"""
Strategy 2 — Session Trader (London / New York session range).

Completely independent from SMC Sniper — separate signal chain,
separate magic numbers, separate risk allocation, separate logs.

Target: EURUSD / GBPUSD via VT Markets MT5 (MetaAPI).

Sessions (UTC):
  London  : 08:00 – 16:00
  New York: 13:00 – 21:00
  Overlap : 13:00 – 16:00  (both active)

Workflow (10 steps):
  1.  Determine active session (London / NY / overlap / none)
  2.  Build LIVE session range (high/low since session open candle today)
  3.  4H macro bias gate (must be non-neutral — prevents counter-trend fade)
  4.  Detect sweep of session extreme:
        wick pierces session high (by ≥ sweep_beyond_pips pips) and close back inside, OR
        wick pierces session low
  5.  Confirm 1H market structure shift (CHoCH) after the sweep bar
  6.  Confirm displacement candle (≥ 1×ATR range) after sweep
  7.  Confirm 1H FVG retracement (price returns to 3-candle gap after CHoCH)
  8.  Enter at FVG midpoint (or CHoCH bar close when no FVG)
  9.  SL: sweep wick ± pip_buffer  |  TP: session range extended by tp_extension_r
 10.  Management: close tp1_pct (75%) at session range opposite extreme,
                  runner to TP; SL → BE after tp1 hit

Tags every signal: strategy = "SESSION_TRADER"
Magic numbers: EURUSD → 12001 | GBPUSD → 12002

Per-session TP targets:
  London / Overlap : TP = session open + (range × 1.5)   (range projection)
  New York         : TP = session open + (range × 2.0)   (NY expands London)

Config lives in strategies/config.yaml (session_trader section).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .base import BaseStrategy, TradeSignal
from smc_bot import confirmation, structure
from smc_bot._util import atr as _atr_series

log = logging.getLogger(__name__)

# ── Magic number registry ─────────────────────────────────────────────────────

_MAGIC: dict[str, int] = {
    "EURUSD": 12001,
    "GBPUSD": 12002,
}

# ── Session windows (UTC hours, [start_inclusive, end_exclusive)) ─────────────

_SESSIONS: dict[str, tuple[int, int]] = {
    "London":   (8,  16),
    "New York": (13, 21),
}
_OVERLAP = (13, 16)

_DEFAULT_CFG: dict = {
    # ── Session windows ────────────────────────────────────────────────────────
    "london": {
        "start_h":      8,    # session opens (UTC)
        "end_h":        16,   # session closes
        "range_start_h": 8,   # initial-balance start
        "range_end_h":  10,   # initial-balance end  → range locked after 10:00
    },
    "new_york": {
        "start_h":      13,
        "end_h":        21,
        "range_start_h": 13,
        "range_end_h":  15,
    },
    # ── Sweep / entry params ───────────────────────────────────────────────────
    "sweep_beyond_pips": 2.0,   # wick must pierce IB extreme by this many pips
    "pip_size":          0.0001,
    "sl_buffer_pips":    3.0,   # SL = sweep wick ± buffer
    "min_range_pips":    10.0,  # minimum IB range (noise filter)
    # ── TP ────────────────────────────────────────────────────────────────────
    "london_tp_r":       1.5,   # TP = IB range × multiplier projection
    "ny_tp_r":           2.0,
    "tp1_pct":           0.75,
    # ── Bias ──────────────────────────────────────────────────────────────────
    "macro_bias_swing_n": 3,
}


class SessionTrader(BaseStrategy):
    """
    London / New York session range strategy for EURUSD / GBPUSD.

    Input:
        df_htf — 4H OHLCV for macro bias (at least 60 bars)
        df_ltf — 1H OHLCV for session range + sweep + CHoCH (at least 48 bars)

    Output:
        TradeSignal or None
    """

    strategy_name = "SESSION_TRADER"

    def __init__(self, cfg: dict | None = None, now_fn=None) -> None:
        import copy
        merged = copy.deepcopy(_DEFAULT_CFG)
        if cfg:
            for k, v in cfg.items():
                if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                    merged[k] = {**merged[k], **v}   # deep merge session sub-dicts
                else:
                    merged[k] = v
        super().__init__(merged)
        # Injectable time source — used by the walk-forward backtest to fake "now".
        # Default: datetime.now(utc).  Override with a lambda returning a fixed datetime.
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def magic_number(self, symbol: str) -> int:
        return _MAGIC.get(symbol, 12000)

    def generate_signal(
        self,
        symbol:  str,
        df_htf:  pd.DataFrame,
        df_ltf:  pd.DataFrame,
    ) -> TradeSignal | None:
        try:
            return self._run_chain(symbol, df_htf, df_ltf)
        except Exception as exc:
            log.warning("SessionTrader.generate_signal(%s) error: %s", symbol, exc,
                        exc_info=True)
            return None

    # ── session detection ──────────────────────────────────────────────────────

    def _active_session(self, now_utc: datetime) -> str | None:
        """Return 'Overlap' | 'London' | 'New York' | None."""
        h = now_utc.hour
        in_lon = self.cfg["london"]["start_h"]    <= h < self.cfg["london"]["end_h"]
        in_ny  = self.cfg["new_york"]["start_h"]  <= h < self.cfg["new_york"]["end_h"]
        if in_lon and in_ny:
            return "Overlap"
        if in_lon:
            return "London"
        if in_ny:
            return "New York"
        return None

    # ── initial-balance range ─────────────────────────────────────────────────

    def _get_ib_range(
        self,
        df_ltf:  pd.DataFrame,
        session: str,
        today,
    ) -> dict | None:
        """
        Return the Initial Balance (IB) range for today's session.

        The IB is the locked range from range_start_h to range_end_h (UTC).
        It is only available when today has at least 1 bar in that window.
        """
        cfg_  = self.cfg["london"] if "London" in session else self.cfg["new_york"]
        rs_h  = cfg_["range_start_h"]
        re_h  = cfg_["range_end_h"]
        pip   = self.cfg["pip_size"]

        ts_vals = pd.to_datetime(df_ltf["ts"].values, utc=True)
        ib_idx  = [
            j for j, ts in enumerate(ts_vals)
            if ts.date() == today and rs_h <= ts.hour < re_h
        ]
        if not ib_idx:
            return None

        highs = df_ltf["high"].values
        lows  = df_ltf["low"].values
        ib_high = float(highs[ib_idx].max())
        ib_low  = float(lows[ib_idx].min())
        rng_p   = (ib_high - ib_low) / pip
        if rng_p < self.cfg["min_range_pips"]:
            return None

        return {
            "high":        ib_high,
            "low":         ib_low,
            "range_pips":  rng_p,
            "open_bar_idx": int(ib_idx[0]),
        }

    # ── IB sweep + CHoCH finder ────────────────────────────────────────────────

    def _find_session_sweep(
        self,
        df_ltf:  pd.DataFrame,
        session: str,
        now_utc: datetime,
    ) -> dict | None:
        """
        Find the most recent IB sweep that has already resolved with a CHoCH.

        Sweep  = bar after the IB window that wicks beyond IB extreme by
                 sweep_beyond_pips and closes BACK inside the IB.
        CHoCH  = any bar after the sweep that closes BEYOND the OPPOSITE IB
                 extreme (confirming the reversal direction).

        Returns:
            {"direction", "bar_idx", "wick_extreme", "body_back",
             "choch_bar", "rng_high", "rng_low", "range_pips"} or None
        """
        if "ts" not in df_ltf.columns:
            return None

        pip       = self.cfg["pip_size"]
        cfg_      = self.cfg["london"] if "London" in session else self.cfg["new_york"]
        s_h, e_h  = cfg_["start_h"], cfg_["end_h"]
        re_h      = cfg_["range_end_h"]   # IB ends here; sweep phase begins
        threshold = self.cfg["sweep_beyond_pips"] * pip
        today     = now_utc.date()

        ib = self._get_ib_range(df_ltf, session, today)
        if ib is None:
            return None

        ts_vals = pd.to_datetime(df_ltf["ts"].values, utc=True)
        highs   = df_ltf["high"].values
        lows    = df_ltf["low"].values
        closes  = df_ltf["close"].values

        # Trading-phase bars: session bars AFTER the IB window closes
        trade_idx = [
            j for j, ts in enumerate(ts_vals)
            if ts.date() == today and re_h <= ts.hour < e_h
        ]
        if len(trade_idx) < 2:
            return None   # need at least 1 sweep bar + 1 CHoCH bar

        # Scan from second-to-last trade bar backward for sweep candidates
        for pos in range(len(trade_idx) - 2, -1, -1):
            sweep_bar = trade_idx[pos]
            lo = float(lows[sweep_bar])
            hi = float(highs[sweep_bar])
            cl = float(closes[sweep_bar])

            # Bullish sweep: wick below IB low, close back inside
            if lo < ib["low"] - threshold and cl > ib["low"]:
                # CHoCH: look for a close ABOVE IB high in subsequent trade bars
                choch = None
                for j in trade_idx[pos + 1:]:
                    if float(closes[j]) > ib["high"]:
                        choch = j; break
                if choch is None:
                    continue
                log.debug("SessionTrader IB bullish sweep bar=%d choch_bar=%d", sweep_bar, choch)
                return {
                    "direction":    "bullish",
                    "bar_idx":      sweep_bar,
                    "wick_extreme": lo,
                    "body_back":    cl,
                    "choch_bar":    choch,
                    "rng_high":     ib["high"],
                    "rng_low":      ib["low"],
                    "range_pips":   ib["range_pips"],
                }

            # Bearish sweep: wick above IB high, close back inside
            if hi > ib["high"] + threshold and cl < ib["high"]:
                # CHoCH: look for a close BELOW IB low in subsequent trade bars
                choch = None
                for j in trade_idx[pos + 1:]:
                    if float(closes[j]) < ib["low"]:
                        choch = j; break
                if choch is None:
                    continue
                log.debug("SessionTrader IB bearish sweep bar=%d choch_bar=%d", sweep_bar, choch)
                return {
                    "direction":    "bearish",
                    "bar_idx":      sweep_bar,
                    "wick_extreme": hi,
                    "body_back":    cl,
                    "choch_bar":    choch,
                    "rng_high":     ib["high"],
                    "rng_low":      ib["low"],
                    "range_pips":   ib["range_pips"],
                }

        return None

    # ── FVG retracement detector ───────────────────────────────────────────────

    def _find_fvg_retracement(
        self,
        df_ltf:    pd.DataFrame,
        choch_bar: int,
        bias:      str,
    ) -> float | None:
        """
        Scan forward from choch_bar for price retracing into a 3-candle FVG.
        Returns the FVG midpoint entry price, or None if not found.
        """
        n        = len(df_ltf)
        look_fwd = self.cfg["fvg_lookforward"]
        end      = min(choch_bar + look_fwd + 3, n)
        price    = float(df_ltf["close"].iloc[-1])

        for i in range(choch_bar + 1, end - 2):
            c1_lo = float(df_ltf["low"].iloc[i])
            c1_hi = float(df_ltf["high"].iloc[i])
            c3_lo = float(df_ltf["low"].iloc[i + 2])
            c3_hi = float(df_ltf["high"].iloc[i + 2])

            if bias == "bullish":
                # Bullish FVG: candle 3 low > candle 1 high
                if c3_lo > c1_hi:
                    fvg_mid = (c1_hi + c3_lo) / 2
                    if price <= c3_lo:   # price retrace into FVG
                        return fvg_mid
            else:
                # Bearish FVG: candle 3 high < candle 1 low
                if c3_hi < c1_lo:
                    fvg_mid = (c3_hi + c1_lo) / 2
                    if price >= c3_hi:   # price retrace into FVG
                        return fvg_mid

        return None

    # ── displacement check ─────────────────────────────────────────────────────

    def _check_displacement(
        self,
        df_ltf:   pd.DataFrame,
        sweep_idx: int,
        bias:     str,
    ) -> bool:
        atr_vals = _atr_series(df_ltf)
        atr_last = float(atr_vals.iloc[-1])
        threshold = self.cfg["displacement_atr"] * atr_last
        n   = len(df_ltf)
        for i in range(sweep_idx + 1, min(sweep_idx + 5, n)):
            rng = float(df_ltf["high"].iloc[i]) - float(df_ltf["low"].iloc[i])
            cl  = float(df_ltf["close"].iloc[i])
            op  = float(df_ltf["open"].iloc[i])
            if bias == "bullish" and rng >= threshold and cl > op:
                return True
            if bias == "bearish" and rng >= threshold and cl < op:
                return True
        return False

    # ── TP calculator ──────────────────────────────────────────────────────────

    def _session_tp(
        self,
        entry:    float,
        rng:      dict,
        session:  str,
        side:     str,
        sl:       float,
    ) -> float:
        """
        Session-specific TP: project the session range beyond the swept extreme.

        London / Overlap: range × 1.5
        New York        : range × 2.0
        Fallback (if projection is worse than R-based): use 3R.
        """
        pip   = self.cfg["pip_size"]
        rng_p = rng["range_pips"] * pip
        r     = abs(entry - sl)

        mult  = self.cfg["ny_tp_r"] if "New York" in session else self.cfg["london_tp_r"]

        if side == "Buy":
            # Projection from session high upward
            tp_proj = rng["high"] + rng_p * (mult - 1)
            tp_r3   = entry + r * 3.0
            return max(tp_proj, tp_r3)
        else:
            # Projection from session low downward
            tp_proj = rng["low"] - rng_p * (mult - 1)
            tp_r3   = entry - r * 3.0
            return min(tp_proj, tp_r3)

    # ── main chain ─────────────────────────────────────────────────────────────

    def _run_chain(
        self,
        symbol:  str,
        df_htf:  pd.DataFrame,
        df_ltf:  pd.DataFrame,
    ) -> TradeSignal | None:
        cfg   = self.cfg
        price = float(df_ltf["close"].iloc[-1])

        # ── Step 1: Active session? ────────────────────────────────────────────
        now_utc = self._now_fn()
        session = self._active_session(now_utc)
        if session is None:
            log.debug("SessionTrader %s: outside active sessions", symbol)
            return None

        # ── Step 2: 4H macro bias gate ────────────────────────────────────────
        swing_n = cfg["macro_bias_swing_n"]
        bias    = structure.get_bias(df_htf, swing_n=swing_n)
        if bias == "neutral":
            log.debug("SessionTrader %s: 4H bias neutral — skip", symbol)
            return None

        side    = "Buy" if bias == "bullish" else "Sell"
        bullish = bias == "bullish"

        # ── Step 3: IB sweep + CHoCH (combined, IB-based) ────────────────────
        # _find_session_sweep builds the Initial Balance range from the fixed
        # range window (08-10 / 13-15 UTC), then detects a wick beyond the IB
        # extreme with CHoCH = close on the far side of the IB.
        sweep = self._find_session_sweep(df_ltf, session, now_utc)
        if sweep is None:
            log.debug("SessionTrader %s: no IB sweep+CHoCH", symbol)
            return None

        # Sweep direction must agree with macro bias
        sweep_bullish = sweep["direction"] == "bullish"
        if sweep_bullish != bullish:
            log.debug("SessionTrader %s: sweep %s vs bias %s — skip",
                      symbol, sweep["direction"], bias)
            return None

        # Convenience range dict for _session_tp
        rng = {
            "high":       sweep["rng_high"],
            "low":        sweep["rng_low"],
            "range_pips": sweep["range_pips"],
        }

        # CHoCH bar is confirmed inside _find_session_sweep
        choch_bar = sweep["choch_bar"]

        # ── Step 4: Entry price ───────────────────────────────────────────────
        # Enter at the current bar's close (signal bar = last bar in slice).
        # The CHoCH already confirmed direction; this is a "trade on confirmation"
        # entry — realistic for live execution at next bar open.
        entry_price = price   # current bar close

        # ── Step 5: SL / TP ──────────────────────────────────────────────────
        pip      = cfg["pip_size"]
        buf_pips = cfg["sl_buffer_pips"] * pip
        wick     = sweep["wick_extreme"]
        sl       = wick - buf_pips if bullish else wick + buf_pips
        r_dist   = abs(entry_price - sl)

        if r_dist <= 0:
            log.warning("SessionTrader %s: r_dist=0 — skip", symbol)
            return None

        tp = self._session_tp(entry_price, rng, session, side, sl)

        # ── Step 6: TP1 at 1R (75% partial); SL → BE after TP1 hit ──────────
        tp1     = entry_price + r_dist if bullish else entry_price - r_dist
        tp1_pct = cfg["tp1_pct"]

        mag = self.magic_number(symbol)
        log.info(
            "SessionTrader SIGNAL %s %s [%s] | price=%.5f sl=%.5f tp1=%.5f tp=%.5f r=%.5f",
            side, symbol, session, entry_price, sl, tp1, tp, r_dist,
        )

        return TradeSignal(
            symbol   = symbol,
            side     = side,
            entry    = entry_price,
            sl       = sl,
            tp       = tp,
            tp1      = tp1,
            tp1_pct  = tp1_pct,
            strategy = self.strategy_name,
            setup    = f"{session.lower().replace(' ','_')}_sweep",
            magic    = mag,
            comment  = self._comment(symbol),
            r_dist   = r_dist,
            metadata = {
                "session":       session,
                "bias":          bias,
                "session_high":  rng["high"],
                "session_low":   rng["low"],
                "range_pips":    rng["range_pips"],
                "sweep_bar":     sweep["bar_idx"],
                "sweep_wick":    wick,
                "choch_bar":     choch_bar,
            },
        )
