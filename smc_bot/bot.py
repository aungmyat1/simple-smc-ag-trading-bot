"""
SMC Bot — main loop.

Run with:
    python -m smc_bot.bot

Every 5M candle close — gated SMC chain (matches scripts/backtest.py EXACTLY):
  1-2. 1H swing bias (bullish / bearish / neutral)
  3.   Fib 50% filter — long only in discount, short only in premium
  4.   1H OB/FVG POI zones marked
  5.   Wait for price to tap a 1H POI zone
  6-7. 5M liquidity sweep (stop-hunt of prior swing low/high)
  8.   Post-sweep displacement candle confirmed (>= N x ATR)
  9.   5M CHoCH (structural break confirming reversal)
  10.  Market entry; SL = sweep wick +/- buffer; TP = target_r (fixed R)

NOTE (2026-06-16): the old 5M-retrace entry (steps 11-12) and the BSL/SSL
liquidity-pool TP (step 14) were REMOVED so the live bot trades exactly what
scripts/backtest.py gates. Trials 4/5/5X were run on this simpler chain; the
richer variant was never gated. Re-add either piece only as a NEW, separately
gated trial — never deploy un-gated logic.

LIVE_TRADING=false by default — set manually in .env to enable real orders.
Never modify LIVE_TRADING here; the owner controls it.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml
from dotenv import load_dotenv

from smc_bot import (
    alerts, confirmation, data, executor, fib as fib_mod,
    liquidity, poi, risk, structure,
)

load_dotenv()

# All paths anchored to the repo root so the bot works regardless of CWD.
_REPO_ROOT  = Path(__file__).resolve().parent.parent
_STATE_FILE = _REPO_ROOT / "smc_bot_state.json"

# Module-level state reference used by the SIGTERM handler.
_shutdown_state: "BotState | None" = None

# API failure streak counter — reset on every successful balance fetch.
_api_fail_streak: int = 0
_API_FAIL_THRESHOLD: int = 5


# ── SIGTERM / SIGINT handler ────────────────────────────────────────────────────

def _handle_signal(sig: int, frame) -> None:
    _log = logging.getLogger(__name__)
    _log.info("Signal %d received — saving state and exiting cleanly", sig)
    if _shutdown_state is not None:
        _shutdown_state.save()
    alerts.send(f"SMC Bot stopped (signal {sig})")
    sys.exit(0)


# ── Persistent state ───────────────────────────────────────────────────────────

@dataclass
class BotState:
    peak_equity:        float = 0.0
    day_start_equity:   float = 0.0
    day_start_date:     str   = ""
    consecutive_losses: int   = 0
    was_in_position:    bool  = False  # tracks position transitions for loss detection
    open_order_id:      str   = ""     # orderId of the most recently placed order
    entry_time:         str   = ""     # ISO UTC timestamp of the most recent entry

    def save(self) -> None:
        _STATE_FILE.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls) -> "BotState":
        try:
            data_dict = json.loads(_STATE_FILE.read_text())
            # Gracefully handle state files from older versions that lack new fields
            valid = {f for f in cls.__dataclass_fields__}
            filtered = {k: v for k, v in data_dict.items() if k in valid}
            return cls(**filtered)
        except Exception:
            return cls()

    def update_day_start(self, equity: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day_start_date:
            self.day_start_date   = today
            self.day_start_equity = equity

    def update_peak(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity


# ── Logging ────────────────────────────────────────────────────────────────────

def _setup_logging(log_file: str, level: str) -> None:
    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = _REPO_ROOT / log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)

    rotating = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,   # 10 MB per file
        backupCount=5,
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[rotating, logging.StreamHandler()],
        force=True,
    )


# ── Config ─────────────────────────────────────────────────────────────────────

def _load_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else _REPO_ROOT / "smc_bot" / "config.yaml"
    with open(cfg_path) as f:
        return yaml.safe_load(f)


# ── Trade / signal logs ────────────────────────────────────────────────────────

_TRADE_COLS = [
    "timestamp", "symbol", "side", "entry", "stop", "target",
    "qty", "order_id", "poi_kind", "bias",
]

_JOURNAL_COLS = [
    "Date", "Trade ID", "Pair", "Day", "Session", "Kill Zone",
    "Direction", "HTF Bias", "HTF POI Type", "Premium/Discount",
    "Sweep Direction", "Liquidity Sweep", "5M MSS/BOS", "MSS Strength",
    "Entry Type", "Entry TF", "Entry Price", "Stop Loss", "TP1", "TP2",
    "Risk (pips)", "Reward TP1 (pips)", "Reward TP2 (pips)",
    "Planned RR TP1", "Planned RR TP2",
    "MAE", "MFE", "Result", "Actual R Multiple",
    "News Nearby", "Confluence Score (1-5)", "Rule Compliance",
    "Screenshot Before", "Screenshot After", "Notes",
]


def _session_and_killzone(dt: datetime) -> tuple[str, str]:
    """Map a UTC datetime to a trading session and kill zone label."""
    total = dt.hour * 60 + dt.minute
    if 480 <= total < 570:    # 08:00–09:30
        return "London", "London Open"
    if 570 <= total < 720:    # 09:30–12:00
        return "London", "London AM"
    if 720 <= total < 780:    # 12:00–13:00
        return "Overlap", "London/NY Overlap"
    if 780 <= total < 870:    # 13:00–14:30
        return "New York", "NY Open"
    if 870 <= total < 1260:   # 14:30–21:00
        return "New York", "NY PM"
    return "Asia", "Asia"


def _append_csv(row: dict, cols: list[str], log_file: str) -> None:
    p = _REPO_ROOT / log_file if not Path(log_file).is_absolute() else Path(log_file)
    write_header = not p.exists()
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in cols})


def _log_trade(row: dict, log_file: str = "smc_bot_trades.csv") -> None:
    _append_csv(row, _TRADE_COLS, log_file)


def _log_signal(row: dict, log_file: str = "smc_bot_signals.csv") -> None:
    _append_csv(row, _JOURNAL_COLS, log_file)


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
    global _api_fail_streak

    log = logging.getLogger(__name__)
    sym = cfg["exchange"]["symbol"]
    rc  = cfg["risk"]

    # 1. Fetch live balance; update daily-start and all-time peak
    balance = executor.get_balance(session)
    if balance <= 0:
        _api_fail_streak += 1
        log.error(
            "Balance is 0 or unavailable; skipping cycle (streak=%d)", _api_fail_streak
        )
        if _api_fail_streak >= _API_FAIL_THRESHOLD:
            alerts.send(
                f"⚠ SMC Bot [{sym}]: exchange API unreachable for "
                f"{_api_fail_streak} consecutive cycles — check VPS/network"
            )
        state.save()
        return

    _api_fail_streak = 0
    state.update_day_start(balance)
    state.update_peak(balance)

    # 2. Capital-protection guards — check before ANY position action
    ok, reason = risk.trading_allowed(
        equity                 = balance,
        peak_equity            = state.peak_equity,
        day_start_equity       = state.day_start_equity,
        consecutive_losses     = state.consecutive_losses,
        max_daily_loss         = rc["max_daily_loss"],
        max_drawdown           = rc["max_drawdown"],
        max_consecutive_losses = rc["max_consecutive_losses"],
    )
    if not ok:
        log.warning("GUARD HALT — %s", reason)
        alerts.send(f"🔴 SMC Bot [{sym}] GUARD HALT — {reason}")
        state.save()
        return

    # 3. Position check — detect close and update consecutive_losses counter
    pos = executor.get_position(session, sym)
    in_position = pos is not None

    if state.was_in_position and not in_position:
        # Position just closed (Bybit hit SL or TP); query realized PnL.
        # Pass entry_time so we only match the record for THIS trade and avoid
        # stale PnL from a prior trade resetting the counter incorrectly.
        pnl = executor.get_last_closed_pnl(session, sym, entry_time=state.entry_time)
        if pnl is not None:
            if pnl < 0:
                state.consecutive_losses += 1
                log.info(
                    "Trade closed at LOSS (pnl=%.4f); consecutive_losses=%d",
                    pnl, state.consecutive_losses,
                )
                alerts.send(
                    f"📉 SMC Bot [{sym}] trade closed: LOSS pnl={pnl:.4f} "
                    f"({state.consecutive_losses}/{rc['max_consecutive_losses']} consec)"
                )
            else:
                state.consecutive_losses = 0
                log.info("Trade closed at WIN (pnl=%.4f); consecutive_losses reset", pnl)
                alerts.send(f"📈 SMC Bot [{sym}] trade closed: WIN pnl={pnl:.4f}")
        else:
            # Exchange hasn't indexed the close yet; leave counter unchanged
            # and log so the operator can investigate if this persists.
            log.warning(
                "Position closed but no PnL record newer than entry_time=%s — "
                "leaving consecutive_losses=%d unchanged",
                state.entry_time, state.consecutive_losses,
            )
        state.open_order_id = ""
        state.entry_time    = ""
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
        log.warning("Empty or stale candle data; skipping cycle")
        return

    # ── WORKFLOW STEP 1-2: 1H bias + swing range ─────────────────────────────
    swing_n = cfg["structure"]["swing_n"]
    bias = structure.get_bias(df_1h, swing_n=swing_n)
    log.info("Bias: %s", bias)
    if bias == "neutral":
        return

    # ── STEP 3: Fib 50% discount/premium filter ───────────────────────────────
    # Longs: price must be in discount zone (below 50% of swing range).
    # Shorts: price must be in premium zone (above 50% of swing range).
    price = float(df_5m["close"].iloc[-1])
    fib_mid = fib_mod.get_fib_midpoint(df_1h, bias, swing_n=swing_n)
    # Match the gate (backtest.py): with no confirmed swing range, SKIP — do not
    # pass through. fib_filter() returns True for a None midpoint, so guard here.
    if fib_mid is None or not fib_mod.fib_filter(price, bias, fib_mid):
        log.info(
            "Fib filter: price=%.2f not in %s zone (mid=%s)",
            price, "discount" if bias == "bullish" else "premium", fib_mid,
        )
        return

    # ── STEP 4: Mark 1H OB/FVG demand (bullish) / supply (bearish) zones ─────
    pois = poi.get_pois(
        df_1h,
        bias,
        ob_lookback      = cfg["poi"]["ob_lookback"],
        fvg_lookback     = cfg["poi"]["fvg_lookback"],
        displacement_atr = cfg["poi"]["displacement_atr"],
    )
    # Trial 6: exclude zones already mitigated (price through ≥50% of zone)
    raw_count = len(pois)
    pois = poi.filter_fresh_zones(pois, df_1h, bias)
    if len(pois) < raw_count:
        log.info(
            "Mitigation filter: %d/%d 1H zones rejected (bias=%s)",
            raw_count - len(pois), raw_count, bias,
        )
    if not pois:
        log.info("No fresh 1H POI zones after mitigation filter")
        return

    # ── STEP 5: Wait for price to tap a 1H POI ───────────────────────────────
    active = poi.price_in_poi(price, pois)
    if active is None:
        log.info("Price %.2f not yet in any %s POI", price, bias)
        return
    log.info("Price in 1H %s POI [%.2f – %.2f]", active["kind"], active["low"], active["high"])

    # ── STEP 6-7: 5M liquidity sweep ─────────────────────────────────────────
    lc = cfg["liquidity"]
    sweep = liquidity.get_sweep(
        df_5m,
        bias,
        lookback = lc["lookback"],
        swing_n  = lc["swing_n"],
    )
    if sweep is None:
        log.info("No 5M liquidity sweep detected")
        return
    log.info(
        "Sweep: bar=%d level=%.2f wick=%.2f",
        sweep["bar_idx"], sweep["swept_level"], sweep["wick_extreme"],
    )

    # ── STEP 8: Post-sweep displacement candle (>= N x ATR, trade direction) ──
    disp_atr = lc.get("displacement_atr", cfg["poi"]["displacement_atr"])
    if not liquidity.check_displacement(df_5m, sweep["bar_idx"], bias, atr_mult=disp_atr):
        log.info("No displacement candle after sweep (bias=%s)", bias)
        return
    mss_strength = liquidity.displacement_strength(df_5m, sweep["bar_idx"], bias)
    log.info("Displacement confirmed after sweep (%s)", mss_strength)

    # ── STEP 9: Break of minor structure (CHoCH) ─────────────────────────────
    choch = confirmation.get_choch(
        df_5m, bias, sweep, lookback=cfg["confirmation"]["lookback"]
    )
    if not choch:
        log.info("No CHoCH confirmation")
        return
    log.info("CHoCH confirmed (%s)", bias)

    # ── STEP 10: Market entry — SL at sweep wick ± buffer, TP at fixed R ──────
    # The old 5M-retrace entry and BSL/SSL liquidity-pool TP were removed so this
    # path is byte-for-byte the strategy scripts/backtest.py gates. Enter at
    # market on the signal bar; the backtest models the fill at next-bar open.
    buf = rc["sl_buffer"]
    if bias == "bullish":
        sl   = sweep["wick_extreme"] * (1.0 - buf)
        side = "Buy"
    else:
        sl   = sweep["wick_extreme"] * (1.0 + buf)
        side = "Sell"

    stop_dist = abs(price - sl)
    if stop_dist <= 0:
        log.warning("stop_dist=0; skipping")
        return

    target_r = rc["target_r"]
    tp = price + target_r * stop_dist if side == "Buy" else price - target_r * stop_dist

    qty = risk.calc_qty(balance, price, sl, rc["risk_pct"])
    if qty <= 0:
        log.warning("qty=0; skipping (stop distance too small?)")
        return

    signal_only = cfg.get("signal_only_mode", True)
    mode        = "SIGNAL_ONLY" if signal_only else "EXECUTE"

    log.info(
        "[%s] %s | entry=%.2f SL=%.2f TP=%.2f (%.1fR) qty=%s",
        mode, side, price, sl, tp, target_r, qty,
    )

    # ── Signal log / trade journal (always written) ───────────────────────────
    now       = datetime.now(timezone.utc)
    session, kill_zone = _session_and_killzone(now)
    trade_id  = f"{now.strftime('%Y%m%d')}_{sym}_{now.strftime('%H%M%S')}"
    poi_label = f"1H {bias.capitalize()} {'Order Block' if active['kind'] == 'OB' else 'FVG'}"
    entry_type     = "Market"   # gated chain: market entry on the signal bar (no 5M retrace)
    risk_pts       = round(abs(price - sl), 2)
    reward_tp2     = round(abs(tp - price), 2)
    planned_rr_tp2 = round(abs(tp - price) / stop_dist, 2) if stop_dist else ""

    _log_signal({
        "Date":                   now.strftime("%Y-%m-%d"),
        "Trade ID":               trade_id,
        "Pair":                   sym,
        "Day":                    now.strftime("%A"),
        "Session":                session,
        "Kill Zone":              kill_zone,
        "Direction":              "Long" if side == "Buy" else "Short",
        "HTF Bias":               bias.capitalize(),
        "HTF POI Type":           poi_label,
        "Premium/Discount":       "Discount" if bias == "bullish" else "Premium",
        "Sweep Direction":        "SSL" if bias == "bullish" else "BSL",
        "Liquidity Sweep":        "Yes",
        "5M MSS/BOS":             "Yes",
        "MSS Strength":           mss_strength,
        "Entry Type":             entry_type,
        "Entry TF":               "5M",
        "Entry Price":            round(price, 2),
        "Stop Loss":              round(sl, 2),
        "TP1":                    "",
        "TP2":                    round(tp, 2),
        "Risk (pips)":            risk_pts,
        "Reward TP1 (pips)":      "",
        "Reward TP2 (pips)":      reward_tp2,
        "Planned RR TP1":         "",
        "Planned RR TP2":         planned_rr_tp2,
        "MAE":                    "",
        "MFE":                    "",
        "Result":                 "",
        "Actual R Multiple":      "",
        "News Nearby":            "",
        "Confluence Score (1-5)": "",
        "Rule Compliance":        "",
        "Screenshot Before":      "",
        "Screenshot After":       "",
        "Notes":                  f"mode={mode} poi=[{active['low']:.2f}-{active['high']:.2f}] sweep_level={sweep['swept_level']:.2f}",
    })

    if signal_only:
        alerts.send(
            f"📊 SMC Bot [{sym}] SIGNAL {side} | entry={price:.0f} "
            f"SL={sl:.0f} TP={tp:.0f} ({target_r:.1f}R) qty={qty} (SIGNAL_ONLY)"
        )
        return

    result     = executor.place_order(session, sym, side, qty, sl, tp)
    order_id   = result.get("orderId", "")
    entry_time = datetime.now(timezone.utc).isoformat()

    _log_trade({
        "timestamp": entry_time,
        "symbol":    sym,
        "side":      side,
        "entry":     round(price, 2),
        "stop":      round(sl, 2),
        "target":    round(tp, 2),
        "qty":       qty,
        "order_id":  order_id,
        "poi_kind":  active["kind"],
        "bias":      bias,
    })

    state.open_order_id   = order_id
    state.entry_time      = entry_time
    state.was_in_position = True
    state.save()

    log.info("Trade logged — orderId=%s", order_id)
    alerts.send(
        f"✅ SMC Bot [{sym}] ORDER {side} | entry={price:.0f} "
        f"SL={sl:.0f} TP={tp:.0f} qty={qty} orderId={order_id}"
    )


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    global _shutdown_state

    cfg = _load_config()
    _setup_logging(cfg["logging"]["file"], cfg["logging"]["level"])
    log = logging.getLogger(__name__)

    # Install signal handlers before anything else so a fast SIGTERM during
    # startup doesn't leave the process in a partially-initialised state.
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    api_key    = os.getenv("BYBIT_DEMO_API_KEY", os.getenv("BYBIT_API_KEY", ""))
    api_secret = os.getenv("BYBIT_DEMO_API_SECRET", os.getenv("BYBIT_API_SECRET", ""))
    demo       = cfg["bybit"]["demo"]
    live       = os.getenv("LIVE_TRADING", "false").lower() == "true"

    client      = data.make_client(testnet=False)
    session     = executor.make_session(api_key, api_secret, demo=demo)
    state       = BotState.load()
    _shutdown_state = state           # make accessible to SIGTERM handler

    signal_only = cfg.get("signal_only_mode", True)

    log.info(
        "SMC Bot started — state_file=%s config=%s",
        _STATE_FILE,
        _REPO_ROOT / "smc_bot" / "config.yaml",
    )
    log.info(
        "%s %s/%s demo=%s live=%s mode=%s | "
        "state: peak=%.2f day_start=%.2f consec_losses=%d open_order=%s",
        cfg["exchange"]["symbol"],
        cfg["exchange"]["htf"],
        cfg["exchange"]["ltf"],
        demo,
        live,
        "SIGNAL_ONLY" if signal_only else "EXECUTE",
        state.peak_equity,
        state.day_start_equity,
        state.consecutive_losses,
        state.open_order_id or "none",
    )
    alerts.send(
        f"🟢 SMC Bot started — {cfg['exchange']['symbol']} "
        f"{'SIGNAL_ONLY' if signal_only else 'EXECUTE'} mode"
    )

    while True:
        try:
            run_cycle(cfg, client, session, state)
        except Exception as exc:
            log.exception("Cycle error: %s", exc)
            alerts.send(f"⚠ SMC Bot [{cfg['exchange']['symbol']}] cycle error: {exc}")

        _sleep_to_next_candle(interval_min=5)


if __name__ == "__main__":
    main()
