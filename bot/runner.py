"""
Main loop: fetch → signal → risk → execute → log → alert.
Runs on a 15-minute tick aligned to exchange candle closes.

Usage:
    python -m bot.runner          # paper mode (LIVE_TRADING=False)
    LIVE_TRADING=true python -m bot.runner   # live (NEVER set by agent)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from bot import alerts, config, executor, logger, risk
from bot.signal import get_signal_latest

log = logging.getLogger(__name__)

_BYBIT_KLINES = "https://api.bybit.com/v5/market/kline"
_WARMUP_BARS  = config.STARTUP_CANDLE + 50   # fetch extra for indicator warmup


def _fetch_recent_ohlcv(n: int = _WARMUP_BARS) -> pd.DataFrame:
    """Fetch the last n closed 15m bars from Bybit public API."""
    params = {
        "category": "linear",
        "symbol":   config.SYMBOL,
        "interval": config.TIMEFRAME,
        "limit":    min(n, 1000),
    }
    resp = requests.get(_BYBIT_KLINES, params=params, timeout=15)
    resp.raise_for_status()
    rows = resp.json()["result"]["list"]   # newest first
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df["ts"]   = pd.to_datetime(df["ts"].astype(int), unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df.drop(columns=["turnover"]).sort_values("ts").reset_index(drop=True)
    # drop the currently forming candle (last row may be incomplete)
    return df.iloc[:-1].tail(n)


class BotState:
    """In-memory state for the current run."""
    def __init__(self) -> None:
        self.in_position:     bool            = False
        self.entry_price:     float           = 0.0
        self.sl:              float           = 0.0
        self.tp:              float           = 0.0
        self.qty:             float           = 0.0
        self.peak_equity:     float           = 0.0
        self.day_start_eq:    float           = 0.0
        self.day_start_date:  str             = ""
        self.paper_equity:    float           = 1000.0   # simulated account

    def update_day_start(self, equity: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day_start_date:
            self.day_start_date = today
            self.day_start_eq   = equity

    def update_peak(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity


def _get_equity(state: BotState) -> float:
    if config.LIVE_TRADING:
        return executor.get_balance()
    return state.paper_equity


def run_cycle(state: BotState) -> None:
    """One 15-minute cycle."""
    equity = _get_equity(state)
    state.update_peak(equity)
    state.update_day_start(equity)

    ok, reason = risk.trading_allowed(equity, state.peak_equity, state.day_start_eq)
    if not ok:
        msg = f"[BOT] HALT — {reason}"
        log.warning(msg)
        alerts.send(msg)
        return

    df = _fetch_recent_ohlcv()
    latest_close = df["close"].iloc[-1]

    # ── Manage open position ──────────────────────────────────────────────────
    if state.in_position:
        hit_sl = latest_close <= state.sl
        hit_tp = latest_close >= state.tp
        if hit_sl or hit_tp:
            reason_str = "SL" if hit_sl else "TP"
            exit_price = state.sl if hit_sl else state.tp
            pnl_usdt   = (exit_price - state.entry_price) * state.qty

            if config.LIVE_TRADING:
                executor.close_position()
            else:
                state.paper_equity += pnl_usdt

            logger.log_trade(
                entry       = state.entry_price,
                exit_price  = exit_price,
                sl          = state.sl,
                tp          = state.tp,
                qty         = state.qty,
                exit_reason = reason_str,
            )
            pnl_r = (exit_price - state.entry_price) / (state.entry_price - state.sl)
            alerts.send(
                f"[BOT] {reason_str} hit | entry={state.entry_price:.2f}"
                f" exit={exit_price:.2f} pnl={pnl_r:+.2f}R"
            )
            state.in_position = False
        return   # already in a position; skip signal check

    # ── Check for new signal ──────────────────────────────────────────────────
    sig = get_signal_latest(df)
    if sig["action"] != "LONG":
        log.debug("FLAT — no signal")
        return

    sl = sig["sl"]
    tp = sig["tp"]
    qty = risk.calc_position_size(equity, latest_close, sl)
    if qty <= 0:
        log.warning("Signal fired but qty=0 (SL too close or zero balance)")
        return

    if config.LIVE_TRADING:
        executor.place_long(qty, sl, tp)
    else:
        # paper: deduct nothing at entry; PnL realised at exit
        pass

    state.in_position  = True
    state.entry_price  = latest_close
    state.sl           = sl
    state.tp           = tp
    state.qty          = qty

    alerts.send(
        f"[BOT] LONG signal\n"
        f"entry≈{latest_close:.2f}  SL={sl:.2f}  TP={tp:.2f}\n"
        f"qty={qty}  mode={'LIVE' if config.LIVE_TRADING else 'PAPER'}"
    )
    log.info("LONG opened: entry=%.2f sl=%.2f tp=%.2f qty=%.4f", latest_close, sl, tp, qty)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_DIR / "runner.log"),
            logging.StreamHandler(),
        ],
    )
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    mode = "LIVE" if config.LIVE_TRADING else "PAPER"
    log.info("Bot starting — symbol=%s  tf=%sm  mode=%s", config.SYMBOL, config.TIMEFRAME, mode)
    alerts.send(f"[BOT] Started — {config.SYMBOL} {config.TIMEFRAME}m {mode}")

    state = BotState()
    # initialise peak and day_start
    initial_eq = _get_equity(state)
    state.peak_equity  = initial_eq
    state.day_start_eq = initial_eq

    interval_s = int(config.TIMEFRAME) * 60   # 15 min in seconds

    while True:
        try:
            run_cycle(state)
        except Exception as exc:
            msg = f"[BOT] ERROR: {exc}"
            log.exception(msg)
            alerts.send(msg)

        # sleep until the next 15-minute boundary
        now      = time.time()
        next_tick = (now // interval_s + 1) * interval_s + 5   # +5s buffer for candle close
        time.sleep(max(0, next_tick - time.time()))


if __name__ == "__main__":
    main()
