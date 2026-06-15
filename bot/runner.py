"""
Main loop — 5M tick, dual-TF fetch, partial TP management.

Cycle (every 5M candle close):
  1. Fetch 1H (HTF_BARS) + 5M (LTF_BARS) from Bybit public API
  2. Check risk guards
  3. If in position: manage TP1 → breakeven → TP2 → runner / SL
  4. If flat: check signal, enter if LONG

Usage:
    python -m bot.runner          # paper mode (LIVE_TRADING=False default)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import requests

from bot import alerts, config, executor, logger, risk
from bot.signal import get_signal_latest

log = logging.getLogger(__name__)

_BYBIT_KLINES = "https://api.bybit.com/v5/market/kline"


def _fetch_ohlcv(interval: str, n: int) -> pd.DataFrame:
    """Fetch last n closed bars for SYMBOL at the given interval."""
    params = {
        "category": "linear",
        "symbol":   config.SYMBOL,
        "interval": interval,
        "limit":    min(n, 1000),
    }
    resp = requests.get(_BYBIT_KLINES, params=params, timeout=15)
    resp.raise_for_status()
    rows = resp.json()["result"]["list"]   # newest first
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df.drop(columns=["turnover"]).sort_values("ts").reset_index(drop=True)
    return df.iloc[:-1].tail(n)   # drop forming candle, keep last n


@dataclass
class BotState:
    in_position:   bool  = False
    entry_price:   float = 0.0
    sl:            float = 0.0
    tp1:           float = 0.0
    tp2:           float = 0.0
    tp_runner:     float = 0.0
    original_qty:  float = 0.0
    remaining_qty: float = 0.0
    tp1_hit:       bool  = False
    tp2_hit:       bool  = False
    sl_at_be:      bool  = False
    peak_equity:   float = 0.0
    day_start_eq:  float = 0.0
    day_start_date: str  = ""
    paper_equity:  float = 1000.0

    def update_day_start(self, equity: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day_start_date:
            self.day_start_date = today
            self.day_start_eq   = equity

    def update_peak(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity


def _get_equity(state: BotState) -> float:
    return executor.get_balance() if config.LIVE_TRADING else state.paper_equity


def _realise_partial(state: BotState, exit_price: float, qty: float, reason: str) -> None:
    """Close part of the position, update paper equity."""
    if config.LIVE_TRADING:
        executor.close_partial(qty)
    else:
        pnl = (exit_price - state.entry_price) * qty
        state.paper_equity += pnl

    pnl_r = (exit_price - state.entry_price) / (state.entry_price - state.sl) if state.sl < state.entry_price else 0
    alerts.send(
        f"[BOT] {reason} | exit={exit_price:.2f} qty={qty:.4f} pnl≈{pnl_r:+.2f}R"
    )
    log.info("%s exit=%.2f qty=%.4f pnl_r=%.3f", reason, exit_price, qty, pnl_r)


def _manage_position(state: BotState, latest_high: float, latest_low: float) -> None:
    """Check TP1/TP2/runner/SL and execute partial closes or full close."""

    # TP1 — close 50%, move SL to breakeven
    if not state.tp1_hit and latest_high >= state.tp1:
        qty = round(state.original_qty * config.TP1_FRAC, 4)
        _realise_partial(state, state.tp1, qty, "TP1")
        state.remaining_qty -= qty
        state.tp1_hit   = True
        state.sl        = state.entry_price   # breakeven
        state.sl_at_be  = True
        return

    # TP2 — close 25%
    if state.tp1_hit and not state.tp2_hit and latest_high >= state.tp2:
        qty = round(state.original_qty * config.TP2_FRAC, 4)
        _realise_partial(state, state.tp2, qty, "TP2")
        state.remaining_qty -= qty
        state.tp2_hit = True
        return

    # Runner TP — close remainder
    if state.tp1_hit and state.tp2_hit and latest_high >= state.tp_runner:
        if config.LIVE_TRADING:
            executor.close_position()
        else:
            pnl = (state.tp_runner - state.entry_price) * state.remaining_qty
            state.paper_equity += pnl
        logger.log_trade(
            entry=state.entry_price, exit_price=state.tp_runner,
            sl=state.sl, tp=state.tp_runner, qty=state.remaining_qty,
            exit_reason="TP_RUNNER",
        )
        alerts.send(f"[BOT] TP_RUNNER hit | exit={state.tp_runner:.2f}")
        state.in_position = False
        return

    # SL hit
    if latest_low <= state.sl:
        if config.LIVE_TRADING:
            executor.close_position()
        else:
            pnl = (state.sl - state.entry_price) * state.remaining_qty
            state.paper_equity += pnl
        reason = "SL-BE" if state.sl_at_be else "SL"
        logger.log_trade(
            entry=state.entry_price, exit_price=state.sl,
            sl=state.sl, tp=state.tp_runner, qty=state.remaining_qty,
            exit_reason=reason,
        )
        pnl_r = (state.sl - state.entry_price) / (state.entry_price - (state.sl if state.sl_at_be else state.sl))
        alerts.send(f"[BOT] {reason} | exit={state.sl:.2f}")
        state.in_position = False


def run_cycle(state: BotState) -> None:
    equity = _get_equity(state)
    state.update_peak(equity)
    state.update_day_start(equity)

    ok, reason = risk.trading_allowed(equity, state.peak_equity, state.day_start_eq)
    if not ok:
        msg = f"[BOT] HALT — {reason}"
        log.warning(msg)
        alerts.send(msg)
        if state.in_position:
            executor.close_position()
            state.in_position = False
        return

    df_1h = _fetch_ohlcv(config.HTF_TIMEFRAME, config.HTF_BARS)
    df_5m = _fetch_ohlcv(config.LTF_TIMEFRAME, config.LTF_BARS)

    latest_high  = df_5m["high"].iloc[-1]
    latest_low   = df_5m["low"].iloc[-1]
    latest_close = df_5m["close"].iloc[-1]

    if state.in_position:
        _manage_position(state, latest_high, latest_low)
        return

    sig = get_signal_latest(df_1h, df_5m)
    if sig["action"] != "LONG":
        log.debug("FLAT")
        return

    sl  = sig["sl"]
    qty = risk.calc_position_size(equity, latest_close, sl)
    if qty <= 0:
        log.warning("Signal fired but qty=0 (SL too close or zero balance)")
        return

    if config.LIVE_TRADING:
        executor.place_long(qty, sl, sig["tp1"])

    state.in_position   = True
    state.entry_price   = latest_close
    state.sl            = sl
    state.tp1           = sig["tp1"]
    state.tp2           = sig["tp2"]
    state.tp_runner     = sig["tp_runner"]
    state.original_qty  = qty
    state.remaining_qty = qty
    state.tp1_hit       = False
    state.tp2_hit       = False
    state.sl_at_be      = False

    alerts.send(
        f"[BOT] LONG signal\n"
        f"entry≈{latest_close:.2f}  SL={sl:.2f}\n"
        f"TP1={sig['tp1']:.2f}  TP2={sig['tp2']:.2f}  Runner={sig['tp_runner']:.2f}\n"
        f"qty={qty}  mode={'LIVE' if config.LIVE_TRADING else 'PAPER'}"
    )
    log.info(
        "LONG entry=%.2f sl=%.2f tp1=%.2f tp2=%.2f runner=%.2f qty=%.4f",
        latest_close, sl, sig["tp1"], sig["tp2"], sig["tp_runner"], qty,
    )


def main() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_DIR / "runner.log"),
            logging.StreamHandler(),
        ],
    )

    mode = "LIVE" if config.LIVE_TRADING else "PAPER"
    log.info("Bot starting — %s  HTF=%sm LTF=%sm  mode=%s",
             config.SYMBOL, config.HTF_TIMEFRAME, config.LTF_TIMEFRAME, mode)
    alerts.send(f"[BOT] Started — {config.SYMBOL} 1H→5M SMC  {mode}")

    state = BotState()
    initial_eq        = _get_equity(state)
    state.peak_equity  = initial_eq
    state.day_start_eq = initial_eq

    interval_s = int(config.LTF_TIMEFRAME) * 60   # 5 min in seconds

    while True:
        try:
            run_cycle(state)
        except Exception as exc:
            msg = f"[BOT] ERROR: {exc}"
            log.exception(msg)
            alerts.send(msg)

        now      = time.time()
        next_tick = (now // interval_s + 1) * interval_s + 5
        time.sleep(max(0, next_tick - time.time()))


if __name__ == "__main__":
    main()
