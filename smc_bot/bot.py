"""
SMC Bot — main loop.

Run with:
    python -m smc_bot.bot

Every 5M candle close:
  1. Check if already in position (skip cycle if yes)
  2. Fetch 1H candles → determine bias (bullish / bearish / neutral)
  3. Detect 1H POI zones (Order Block or FVG)
  4. Check if current 5M price is inside a POI
  5. Detect 5M liquidity sweep (stop-hunt of prior swing)
  6. Detect 5M CHoCH (structural confirmation after sweep)
  7. Size and place market order with SL=sweep wick ± buffer, TP=2R
  8. Append trade to smc_bot_trades.csv

LIVE_TRADING=false by default — set manually in .env to enable real orders.
Never modify LIVE_TRADING here; the owner controls it.
"""
from __future__ import annotations

import csv
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from smc_bot import confirmation, data, executor, liquidity, poi, risk, structure

load_dotenv()


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


# ── Trade log ──────────────────────────────────────────────────────────────────

_TRADE_COLS = [
    "timestamp", "symbol", "side", "entry", "stop", "target",
    "qty", "order_id", "poi_kind", "bias",
]


def _log_trade(row: dict, log_file: str = "smc_bot_trades.csv") -> None:
    p           = Path(log_file)
    write_header = not p.exists()
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_TRADE_COLS)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in _TRADE_COLS})


# ── Timing ─────────────────────────────────────────────────────────────────────

def _sleep_to_next_candle(interval_min: int = 5) -> None:
    log   = logging.getLogger(__name__)
    now   = time.time()
    secs  = interval_min * 60
    nxt   = (now // secs + 1) * secs + 2   # +2 s buffer past candle close
    sleep = max(0.0, nxt - time.time())
    log.info("Next %dM candle in %.0fs", interval_min, sleep)
    time.sleep(sleep)


# ── Core cycle ─────────────────────────────────────────────────────────────────

def run_cycle(cfg: dict, client, session) -> None:
    log = logging.getLogger(__name__)
    sym = cfg["exchange"]["symbol"]

    # 1. Skip if already in a position (Bybit manages SL/TP automatically)
    pos = executor.get_position(session, sym)
    if pos is not None:
        log.info(
            "Position open: side=%s size=%s avgPrice=%s",
            pos.get("side"), pos.get("size"), pos.get("avgPrice"),
        )
        return

    # 2. Fetch candles
    df_1h = data.get_candles(
        client, sym, cfg["exchange"]["htf"], limit=cfg["data"]["htf_limit"]
    )
    df_5m = data.get_candles(
        client, sym, cfg["exchange"]["ltf"], limit=cfg["data"]["ltf_limit"]
    )
    if df_1h.empty or df_5m.empty:
        log.warning("Empty candle data; skipping cycle")
        return

    # 3. Bias
    bias = structure.get_bias(df_1h, swing_n=cfg["structure"]["swing_n"])
    log.info("Bias: %s", bias)
    if bias == "neutral":
        return

    # 4. POI zones
    pois = poi.get_pois(
        df_1h,
        bias,
        ob_lookback=cfg["poi"]["ob_lookback"],
        fvg_lookback=cfg["poi"]["fvg_lookback"],
        displacement_atr=cfg["poi"]["displacement_atr"],
    )
    if not pois:
        log.info("No POI zones")
        return

    # 5. Price in POI?
    price = float(df_5m["close"].iloc[-1])
    active = poi.price_in_poi(price, pois)
    if active is None:
        log.info("Price %.2f not in any %s POI", price, bias)
        return
    log.info("Price in %s POI [%.2f – %.2f]", active["kind"], active["low"], active["high"])

    # 6. Liquidity sweep on 5M
    sweep = liquidity.get_sweep(
        df_5m,
        bias,
        lookback=cfg["liquidity"]["lookback"],
        swing_n=cfg["liquidity"]["swing_n"],
    )
    if sweep is None:
        log.info("No liquidity sweep detected")
        return
    log.info(
        "Sweep: bar=%d level=%.2f wick=%.2f",
        sweep["bar_idx"], sweep["swept_level"], sweep["wick_extreme"],
    )

    # 7. CHoCH confirmation on 5M
    choch = confirmation.get_choch(
        df_5m, bias, sweep, lookback=cfg["confirmation"]["lookback"]
    )
    if not choch:
        log.info("No CHoCH confirmation")
        return
    log.info("CHoCH confirmed (%s)", bias)

    # 8. Size and execute
    balance = executor.get_balance(session)
    if balance <= 0:
        log.error("Balance is 0; cannot trade")
        return

    buf = cfg["risk"]["sl_buffer"]
    r   = cfg["risk"]["target_r"]

    if bias == "bullish":
        sl   = sweep["wick_extreme"] * (1.0 - buf)
        side = "Buy"
    else:
        sl   = sweep["wick_extreme"] * (1.0 + buf)
        side = "Sell"

    stop_dist = abs(price - sl)
    tp        = price + r * stop_dist if side == "Buy" else price - r * stop_dist
    qty       = risk.calc_qty(balance, price, sl, cfg["risk"]["risk_pct"])

    if qty <= 0:
        log.warning("qty=0; skipping (stop distance too small?)")
        return

    log.info(
        "SIGNAL %s | entry=%.2f SL=%.2f TP=%.2f (%.1fR) qty=%s",
        side, price, sl, tp, r, qty,
    )

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


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = _load_config()
    _setup_logging(cfg["logging"]["file"], cfg["logging"]["level"])
    log = logging.getLogger(__name__)

    api_key    = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    testnet    = cfg["bybit"]["testnet"]
    live       = os.getenv("LIVE_TRADING", "false").lower() == "true"

    client  = data.make_client(testnet=testnet)
    session = executor.make_session(api_key, api_secret, testnet=testnet)

    log.info(
        "SMC Bot started — %s %s/%s testnet=%s live=%s",
        cfg["exchange"]["symbol"],
        cfg["exchange"]["htf"],
        cfg["exchange"]["ltf"],
        testnet,
        live,
    )

    while True:
        try:
            run_cycle(cfg, client, session)
        except KeyboardInterrupt:
            log.info("Stopped by user")
            break
        except Exception as exc:
            log.exception("Cycle error: %s", exc)

        _sleep_to_next_candle(interval_min=5)


if __name__ == "__main__":
    main()
