"""
Multi-strategy async runner.

Runs SMC_SNIPER and SESSION_TRADER independently on EURUSD / GBPUSD.
Each strategy has isolated risk tracking, trade logs, and signal flow.

Usage:
    python runner.py                       # both strategies (from strategies/config.yaml)
    python runner.py --strategy smc        # SMC Sniper only
    python runner.py --strategy session    # Session Trader only
    python runner.py --dry-run             # override LIVE_TRADING=false

Safety rules (enforced here, not bypassable):
    - LIVE_TRADING=false by default — never flipped by this code
    - No order is placed without a TradeSignal from a strategy
    - Risk guards checked before every signal is routed to the broker
    - Each strategy's risk state is fully isolated
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

_HERE = Path(__file__).parent
_CFG_PATH = _HERE / "strategies" / "config.yaml"


def _load_cfg() -> dict:
    with open(_CFG_PATH) as f:
        return yaml.safe_load(f)


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ── per-strategy runner ───────────────────────────────────────────────────────

async def _run_strategy(
    name:     str,
    strategy,
    symbols:  list[str],
    broker,
    risk_mgr,
    trade_logs: dict,
    cfg:      dict,
    htf_bars: int,
    ltf_bars: int,
    htf_tf:   str,
    ltf_tf:   str,
) -> None:
    """One cycle of signal generation → risk check → order placement for a strategy."""
    import pandas as pd
    from smc_bot.data import fetch_candles

    for symbol in symbols:
        try:
            # Fetch data
            df_htf = await asyncio.get_event_loop().run_in_executor(
                None, fetch_candles, symbol, htf_tf, htf_bars
            )
            df_ltf = await asyncio.get_event_loop().run_in_executor(
                None, fetch_candles, symbol, ltf_tf, ltf_bars
            )

            if df_htf is None or df_ltf is None or len(df_htf) < 10 or len(df_ltf) < 10:
                log.warning("%s %s: insufficient data", name, symbol)
                continue

            # Risk gate
            balance = await broker.get_balance()
            risk_mgr.update_balance(name, balance)
            if not risk_mgr.trading_allowed(name):
                log.info("%s %s: risk guard — trading not allowed", name, symbol)
                continue

            # Already in a position?
            magic = strategy.magic_number(symbol)
            pos   = await broker.get_position(symbol, magic)
            if pos is not None:
                log.debug("%s %s: position open (magic=%d) — skip signal", name, symbol, magic)
                continue

            # Generate signal
            signal = strategy.generate_signal(symbol, df_htf, df_ltf)
            if signal is None:
                log.debug("%s %s: no signal", name, symbol)
                continue

            # Lot size
            pip_key = f"pip_value_{symbol.lower()}"
            pip_val = cfg.get("risk", {}).get(pip_key, 10.0)
            qty = risk_mgr.calc_qty(
                name, balance, signal.r_dist,
                pip_value=pip_val,
                pip_size=cfg.get("pip_size", 0.0001),
            )

            log.info(
                "%s %s: SIGNAL %s entry=%.5f sl=%.5f tp=%.5f qty=%.2f",
                name, symbol, signal.side, signal.entry, signal.sl, signal.tp, qty,
            )

            # Place order (dry-run if LIVE_TRADING=false)
            result = await broker.place_order(
                symbol=symbol, side=signal.side, qty=qty,
                sl=signal.sl, tp=signal.tp,
                magic=signal.magic, comment=signal.comment,
                entry_hint=signal.entry,
            )

            if result.success:
                log.info("%s %s: order placed order_id=%s", name, symbol, result.order_id)
            else:
                log.error("%s %s: order failed — %s", name, symbol, result.error)

        except Exception as exc:
            log.error("%s %s: unhandled error in cycle — %s", name, symbol, exc, exc_info=True)


# ── main loop ─────────────────────────────────────────────────────────────────

async def main(mode: str = "both", dry_run: bool = False) -> None:
    from brokers.metaapi import MetaApiBroker
    from risk.manager import RiskManager
    from reporting.trade_log import TradeLog
    from strategies.smc_sniper import SMCSniper
    from strategies.session_trader import SessionTrader

    cfg = _load_cfg()

    live_trading = False   # NEVER auto-enable — owner flips LIVE_TRADING=true in .env
    if not dry_run:
        live_trading = os.getenv("LIVE_TRADING", "false").lower() == "true"

    log.info("Runner starting | mode=%s live_trading=%s", mode, live_trading)

    svc      = cfg.get("service", {})
    symbols  = cfg.get("symbols", ["EURUSD", "GBPUSD"])
    interval = cfg.get("runner", {}).get("loop_interval_seconds", 60)

    run_smc     = mode in ("both", "smc")     and svc.get("smc_sniper",     True)
    run_session = mode in ("both", "session") and svc.get("session_trader", True)

    # Instantiate strategies
    strategies_map: list[tuple[str, object, dict, str, str, int, int]] = []

    if run_smc:
        smc_cfg = cfg.get("smc_sniper", {})
        strategies_map.append((
            "SMC_SNIPER", SMCSniper(smc_cfg), smc_cfg,
            smc_cfg.get("htf", "240m"), smc_cfg.get("ltf", "60m"),
            smc_cfg.get("htf_bars", 200), smc_cfg.get("ltf_bars", 100),
        ))

    if run_session:
        ses_cfg = cfg.get("session_trader", {})
        strategies_map.append((
            "SESSION_TRADER", SessionTrader(ses_cfg), ses_cfg,
            ses_cfg.get("htf", "240m"), ses_cfg.get("ltf", "60m"),
            ses_cfg.get("htf_bars", 60), ses_cfg.get("ltf_bars", 72),
        ))

    if not strategies_map:
        log.error("No strategies enabled — check strategies/config.yaml")
        return

    # Risk manager
    risk_cfgs = {}
    for name, _, s_cfg, *_ in strategies_map:
        risk_cfgs[name] = s_cfg.get("limits", {})
    risk_mgr = RiskManager(risk_cfgs)

    # Trade logs
    trade_logs = {
        name: TradeLog(name, base_dir=str(_HERE))
        for name, *_ in strategies_map
    }

    # Connect broker
    broker = MetaApiBroker(live_trading=live_trading)
    try:
        await broker.connect()
    except Exception as exc:
        log.critical("Broker connection failed: %s", exc)
        return

    log.info("Broker connected. Entering loop (interval=%ds).", interval)

    try:
        while True:
            tasks = [
                _run_strategy(
                    name=name, strategy=strat, symbols=symbols,
                    broker=broker, risk_mgr=risk_mgr,
                    trade_logs=trade_logs, cfg=s_cfg,
                    htf_bars=htf_bars, ltf_bars=ltf_bars,
                    htf_tf=htf_tf, ltf_tf=ltf_tf,
                )
                for name, strat, s_cfg, htf_tf, ltf_tf, htf_bars, ltf_bars in strategies_map
            ]
            await asyncio.gather(*tasks)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("Runner cancelled — shutting down")
    finally:
        await broker.disconnect()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Multi-strategy forex runner")
    ap.add_argument("--strategy", choices=["smc", "session", "both"], default="both")
    ap.add_argument("--dry-run", action="store_true", help="Force dry-run (ignore LIVE_TRADING env)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    _setup_logging(args.log_level)

    # Load .env if present
    env_file = _HERE / ".env"
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)

    asyncio.run(main(mode=args.strategy, dry_run=args.dry_run))
