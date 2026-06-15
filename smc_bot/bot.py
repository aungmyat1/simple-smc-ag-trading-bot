"""
SMC Bot — main loop.

Run with:
    python -m smc_bot.bot

Every 5M candle close:
  1. Fetch balance; enforce capital-protection guards (drawdown / daily-loss / consecutive-losses)
  2. Check if already in position — if just closed, detect win/loss and update state
  3. Fetch 1H candles → determine bias (bullish / bearish / neutral)
  4. Detect 1H POI zones (Order Block or FVG)
  5. Check if current 5M price is inside a POI
  6. Detect 5M liquidity sweep (stop-hunt of prior swing)
  7. Detect 5M CHoCH (structural confirmation after sweep)
  8. Size and place market order with SL=sweep wick ± buffer, TP=2R
  9. Append trade to smc_bot_trades.csv

LIVE_TRADING=false by default — set manually in .env to enable real orders.
Never modify LIVE_TRADING here; the owner controls it.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from smc_bot import confirmation, data, executor, liquidity, poi, risk, structure

load_dotenv()

_STATE_FILE = Path("smc_bot_state.json")


# ── Persistent state ───────────────────────────────────────────────────────────

@dataclass
class BotState:
    peak_equity:        float = 0.0
    day_start_equity:   float = 0.0
    day_start_date:     str   = ""
    consecutive_losses: int   = 0
    was_in_position:    bool  = False  # tracks position transitions for loss detection

    def save(self) -> None:
        _STATE_FILE.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls) -> "BotState":
        try:
            return cls(**json.loads(_STATE_FILE.read_text()))
        except Exception:
            return cls()

    def update_day_start(self, equity: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day_start_date:
            self.day_start_date    = today
            self.day_start_equity  = equity

    def update_peak(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity


# ── Logging ────────────────────────────────────────────────────────────────────

def _setup_logging(log_file: str, level: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
        force=True,
    )


# ── Config ─────────────────────────────────────────────────────────────────────

def _load_config(path: str = "smc_bot/config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ── Trade / signal logs ────────────────────────────────────────────────────────

_TRADE_COLS = [
    "timestamp", "symbol", "side", "entry", "stop", "target",
    "qty", "order_id", "poi_kind", "bias",
]

_SIGNAL_COLS = [
    "timestamp", "symbol", "bias", "poi_kind", "poi_low", "poi_high",
    "sweep_level", "choch", "entry", "stop", "target", "qty", "mode",
]


def _append_csv(row: dict, cols: list[str], log_file: str) -> None:
    p            = Path(log_file)
    write_header = not p.exists()
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in cols})


def _log_trade(row: dict, log_file: str = "smc_bot_trades.csv") -> None:
    _append_csv(row, _TRADE_COLS, log_file)


def _log_signal(row: dict, log_file: str = "smc_bot_signals.csv") -> None:
    _append_csv(row, _SIGNAL_COLS, log_file)


# ── Timing ─────────────────────────────────────────────────────────────────────

def _sleep_to_next_candle(interval_min: int = 5) -> None:
    log  = logging.getLogger(__name__)
    now  = time.time()
    secs = interval_min * 60
    nxt  = (now // secs + 1) * secs + 2   # +2 s buffer past candle close
    sleep = max(0.0, nxt - time.time())
    log.info("Next %dM candle in %.0fs", interval_min, sleep)
    time.sleep(sleep)


# ── Core cycle ─────────────────────────────────────────────────────────────────

def run_cycle(cfg: dict, client, session, state: BotState) -> None:
    log = logging.getLogger(__name__)
    sym = cfg["exchange"]["symbol"]
    rc  = cfg["risk"]

    # 1. Fetch live balance; update daily-start and all-time peak
    balance = executor.get_balance(session)
    if balance <= 0:
        log.error("Balance is 0 or unavailable; skipping cycle")
        return

    state.update_day_start(balance)
    state.update_peak(balance)

    # 2. Capital-protection guards — check before ANY position action
    ok, reason = risk.trading_allowed(
        equity               = balance,
        peak_equity          = state.peak_equity,
        day_start_equity     = state.day_start_equity,
        consecutive_losses   = state.consecutive_losses,
        max_daily_loss       = rc["max_daily_loss"],
        max_drawdown         = rc["max_drawdown"],
        max_consecutive_losses = rc["max_consecutive_losses"],
    )
    if not ok:
        log.warning("GUARD HALT — %s", reason)
        state.save()
        return

    # 3. Position check — detect close and update consecutive_losses counter
    pos = executor.get_position(session, sym)
    in_position = pos is not None

    if state.was_in_position and not in_position:
        # Position just closed (Bybit hit SL or TP); query realized PnL
        pnl = executor.get_last_closed_pnl(session, sym)
        if pnl is not None:
            if pnl < 0:
                state.consecutive_losses += 1
                log.info("Trade closed at LOSS (pnl=%.4f); consecutive_losses=%d",
                         pnl, state.consecutive_losses)
            else:
                state.consecutive_losses = 0
                log.info("Trade closed at WIN (pnl=%.4f); consecutive_losses reset", pnl)
        state.save()

    state.was_in_position = in_position
    state.save()

    if in_position:
        log.info(
            "Position open: side=%s size=%s avgPrice=%s",
            pos.get("side"), pos.get("size"), pos.get("avgPrice"),
        )
        return

    # 4. Fetch candles
    df_1h = data.get_candles(
        client, sym, cfg["exchange"]["htf"], limit=cfg["data"]["htf_limit"]
    )
    df_5m = data.get_candles(
        client, sym, cfg["exchange"]["ltf"], limit=cfg["data"]["ltf_limit"]
    )
    if df_1h.empty or df_5m.empty:
        log.warning("Empty candle data; skipping cycle")
        return

    # 5. Bias
    bias = structure.get_bias(df_1h, swing_n=cfg["structure"]["swing_n"])
    log.info("Bias: %s", bias)
    if bias == "neutral":
        return

    # 6. POI zones
    pois = poi.get_pois(
        df_1h,
        bias,
        ob_lookback      = cfg["poi"]["ob_lookback"],
        fvg_lookback     = cfg["poi"]["fvg_lookback"],
        displacement_atr = cfg["poi"]["displacement_atr"],
    )
    if not pois:
        log.info("No POI zones")
        return

    # 7. Price in POI?
    price  = float(df_5m["close"].iloc[-1])
    active = poi.price_in_poi(price, pois)
    if active is None:
        log.info("Price %.2f not in any %s POI", price, bias)
        return
    log.info("Price in %s POI [%.2f – %.2f]", active["kind"], active["low"], active["high"])

    # 8. Liquidity sweep on 5M
    sweep = liquidity.get_sweep(
        df_5m,
        bias,
        lookback = cfg["liquidity"]["lookback"],
        swing_n  = cfg["liquidity"]["swing_n"],
    )
    if sweep is None:
        log.info("No liquidity sweep detected")
        return
    log.info(
        "Sweep: bar=%d level=%.2f wick=%.2f",
        sweep["bar_idx"], sweep["swept_level"], sweep["wick_extreme"],
    )

    # 9. CHoCH confirmation on 5M
    choch = confirmation.get_choch(
        df_5m, bias, sweep, lookback=cfg["confirmation"]["lookback"]
    )
    if not choch:
        log.info("No CHoCH confirmation")
        return
    log.info("CHoCH confirmed (%s)", bias)

    # 10. Compute order parameters
    buf = rc["sl_buffer"]
    r   = rc["target_r"]

    if bias == "bullish":
        sl   = sweep["wick_extreme"] * (1.0 - buf)
        side = "Buy"
    else:
        sl   = sweep["wick_extreme"] * (1.0 + buf)
        side = "Sell"

    stop_dist = abs(price - sl)
    tp        = price + r * stop_dist if side == "Buy" else price - r * stop_dist
    qty       = risk.calc_qty(balance, price, sl, rc["risk_pct"])

    if qty <= 0:
        log.warning("qty=0; skipping (stop distance too small?)")
        return

    signal_only = cfg.get("signal_only_mode", True)
    mode        = "SIGNAL_ONLY" if signal_only else "EXECUTE"

    log.info(
        "[%s] %s | entry=%.2f SL=%.2f TP=%.2f (%.1fR) qty=%s",
        mode, side, price, sl, tp, r, qty,
    )

    # 11. Always log the signal (even in signal-only mode)
    _log_signal({
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "symbol":      sym,
        "bias":        bias,
        "poi_kind":    active["kind"],
        "poi_low":     round(active["low"], 2),
        "poi_high":    round(active["high"], 2),
        "sweep_level": round(sweep["swept_level"], 2),
        "choch":       True,
        "entry":       round(price, 2),
        "stop":        round(sl, 2),
        "target":      round(tp, 2),
        "qty":         qty,
        "mode":        mode,
    })

    # 12. Execute only when signal_only_mode is off
    if signal_only:
        log.info("SIGNAL_ONLY — order NOT placed. Flip signal_only_mode: false to enable.")
        return

    result = executor.place_order(session, sym, side, qty, sl, tp)

    _log_trade({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol":    sym,
        "side":      side,
        "entry":     round(price, 2),
        "stop":      round(sl, 2),
        "target":    round(tp, 2),
        "qty":       qty,
        "order_id":  result.get("orderId", ""),
        "poi_kind":  active["kind"],
        "bias":      bias,
    })
    log.info("Trade logged")
    state.was_in_position = True
    state.save()


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = _load_config()
    _setup_logging(cfg["logging"]["file"], cfg["logging"]["level"])
    log = logging.getLogger(__name__)

    api_key    = os.getenv("BYBIT_DEMO_API_KEY", os.getenv("BYBIT_API_KEY", ""))
    api_secret = os.getenv("BYBIT_DEMO_API_SECRET", os.getenv("BYBIT_API_SECRET", ""))
    demo       = cfg["bybit"]["demo"]
    live       = os.getenv("LIVE_TRADING", "false").lower() == "true"

    client      = data.make_client(testnet=False)
    session     = executor.make_session(api_key, api_secret, demo=demo)
    state       = BotState.load()
    signal_only = cfg.get("signal_only_mode", True)

    log.info(
        "SMC Bot started — %s %s/%s demo=%s live=%s mode=%s | "
        "state: peak=%.2f day_start=%.2f consec_losses=%d",
        cfg["exchange"]["symbol"],
        cfg["exchange"]["htf"],
        cfg["exchange"]["ltf"],
        demo,
        live,
        "SIGNAL_ONLY" if signal_only else "EXECUTE",
        state.peak_equity,
        state.day_start_equity,
        state.consecutive_losses,
    )

    while True:
        try:
            run_cycle(cfg, client, session, state)
        except KeyboardInterrupt:
            log.info("Stopped by user")
            break
        except Exception as exc:
            log.exception("Cycle error: %s", exc)

        _sleep_to_next_candle(interval_min=5)


if __name__ == "__main__":
    main()
