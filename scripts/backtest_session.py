"""
Phase-0 gate: Session Trader strategy on EURUSD / GBPUSD.

Walk-forward simulation:
  - HTF = 4H (240m)  — macro bias
  - LTF = 1H (60m)   — session range, sweep, CHoCH, displacement, FVG

At each 1H bar we simulate "now = this bar's close time" and run
SessionTrader.generate_signal() with all data up to and including that bar.
When a signal fires, we simulate the trade on subsequent bars.

Exit model:
  - TP1 at 1R (tp1_pct close, SL → BE)
  - Full TP at session-projected level
  - SL hit at any point ends the trade

Cost model (VT Markets Raw ECN):
  EURUSD: spread 0.8 + commission 0.6 = 1.4 pips round-trip
  GBPUSD: spread 1.0 + commission 0.6 = 1.6 pips round-trip
  → cost expressed in R = (total_pips × pip_size) / stop_distance

Gate: n ≥ 50 AND net PF > 1.0

Usage:
    python3 scripts/backtest_session.py
    python3 scripts/backtest_session.py --symbol EURUSD --csv out.csv
    python3 scripts/backtest_session.py --symbol all     # runs both
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from strategies.session_trader import SessionTrader  # noqa: E402

# ── Cost model ────────────────────────────────────────────────────────────────

_COST_PRESETS: dict[str, dict] = {
    "EURUSD": {"spread_pips": 0.8,  "commission_rt_pips": 0.6, "pip_size": 0.0001},
    "GBPUSD": {"spread_pips": 1.0,  "commission_rt_pips": 0.6, "pip_size": 0.0001},
}


def _fee_r(symbol: str, stop_dist: float) -> float:
    """Round-trip cost in R for forex (fixed pip spread + commission)."""
    if stop_dist <= 0:
        return 0.0
    c = _COST_PRESETS.get(symbol, _COST_PRESETS["EURUSD"])
    cost_price = (c["spread_pips"] + c["commission_rt_pips"]) * c["pip_size"]
    return cost_price / stop_dist


# ── Config ────────────────────────────────────────────────────────────────────

def _load_strategy_cfg() -> dict:
    with open(ROOT / "strategies" / "config.yaml") as f:
        return yaml.safe_load(f).get("session_trader", {})


# ── Data loader ───────────────────────────────────────────────────────────────

def _load_parquet(symbol: str, tf: str) -> pd.DataFrame:
    path = ROOT / "data" / "cache" / f"{symbol}_{tf}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing: {path}\nRun: python scripts/fetch_data.py --symbol {symbol}")
    df = pd.read_parquet(path)
    df = df.sort_values("ts").reset_index(drop=True)
    if not pd.api.types.is_datetime64_any_dtype(df["ts"]):
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    elif df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize("UTC")
    return df


# ── Trade simulator ───────────────────────────────────────────────────────────

def _simulate_trade(
    signal,
    df_ltf:     pd.DataFrame,
    signal_bar: int,
    symbol:     str,
) -> dict | None:
    """
    Walk forward from signal_bar+1 until SL or TP is hit.
    Returns a trade dict or None if no resolution within 48 bars.
    """
    entry  = signal.entry
    sl     = signal.sl
    tp1    = signal.tp1
    tp     = signal.tp
    side   = signal.side
    r_dist = signal.r_dist
    bullish = side == "Buy"

    if r_dist <= 0:
        return None

    fee_r   = _fee_r(symbol, r_dist)
    tp1_pct = signal.tp1_pct
    be_mode = False       # SL moved to BE after TP1 hit
    tp1_hit = False
    n       = len(df_ltf)

    for i in range(signal_bar + 1, min(signal_bar + 49, n)):
        lo = float(df_ltf["low"].iloc[i])
        hi = float(df_ltf["high"].iloc[i])

        # SL check
        sl_hit = lo <= sl if bullish else hi >= sl
        if sl_hit:
            gross_r = -1.0 if not be_mode else 0.0
            return _trade_row(signal, df_ltf, signal_bar, i, gross_r, fee_r, "sl", be_mode)

        # TP1 check (only if not yet hit)
        if not tp1_hit:
            tp1_reached = hi >= tp1 if bullish else lo <= tp1
            if tp1_reached:
                tp1_hit = True
                be_mode = True
                # SL → BE (entry)
                sl = entry

        # Final TP check
        tp_hit = hi >= tp if bullish else lo <= tp
        if tp_hit:
            # Composite R: tp1_pct at 1R, (1-tp1_pct) at full TP
            gross_r = tp1_pct * 1.0 + (1 - tp1_pct) * (abs(tp - entry) / r_dist)
            return _trade_row(signal, df_ltf, signal_bar, i, gross_r, fee_r, "tp", False)

    # Timeout — mark as open/expired, count as neutral (0 R, not a loss)
    return None


def _trade_row(signal, df_ltf, entry_bar, exit_bar, gross_r, fee_r, exit_reason, be_exit) -> dict:
    net_r   = gross_r - fee_r
    win     = net_r > 0
    meta    = signal.metadata or {}
    return {
        "symbol":       signal.symbol,
        "side":         signal.side,
        "setup":        signal.setup,
        "session":      meta.get("session", ""),
        "bias":         meta.get("bias", ""),
        "open_time":    str(df_ltf["ts"].iloc[entry_bar])[:16],
        "close_time":   str(df_ltf["ts"].iloc[exit_bar])[:16],
        "entry":        round(signal.entry, 5),
        "sl":           round(signal.sl, 5),
        "tp1":          round(signal.tp1, 5),
        "tp":           round(signal.tp, 5),
        "r_dist":       round(signal.r_dist, 5),
        "gross_r":      round(gross_r, 4),
        "fee_r":        round(fee_r, 4),
        "net_r":        round(net_r, 4),
        "win":          win,
        "exit_reason":  exit_reason,
        "be_exit":      be_exit,
    }


# ── Walk-forward engine ───────────────────────────────────────────────────────

def run_backtest(symbol: str, cfg: dict, verbose: bool = False) -> dict:
    """Run the full walk-forward and return stats dict."""
    df4h = _load_parquet(symbol, "240m")
    df1h = _load_parquet(symbol, "60m")

    # Align: 4H data must span the 1H range
    htf_bars = cfg.get("htf_bars", 60)
    ltf_bars = cfg.get("ltf_bars", 72)

    trades: list[dict] = []
    in_trade = False
    trade_end_bar = -1

    warmup = max(htf_bars, ltf_bars)

    for i in range(warmup, len(df1h)):
        # Skip bars while in a trade
        if in_trade and i <= trade_end_bar:
            continue
        in_trade = False

        bar_ts = df1h["ts"].iloc[i]
        if hasattr(bar_ts, "to_pydatetime"):
            bar_dt = bar_ts.to_pydatetime()
            if bar_dt.tzinfo is None:
                bar_dt = bar_dt.replace(tzinfo=timezone.utc)
        else:
            bar_dt = bar_ts

        # Align 4H slice: all 4H bars up to this 1H bar's time
        df4h_slice = df4h[df4h["ts"] <= bar_ts].tail(htf_bars).reset_index(drop=True)
        df1h_slice = df1h.iloc[max(0, i - ltf_bars + 1): i + 1].reset_index(drop=True)

        if len(df4h_slice) < 20 or len(df1h_slice) < 20:
            continue

        # Create a strategy instance with this bar's time as "now"
        st = SessionTrader(cfg, now_fn=lambda dt=bar_dt: dt)

        signal = st.generate_signal(symbol, df4h_slice, df1h_slice)
        if signal is None:
            continue

        # Simulate trade outcome on subsequent 1H bars
        # Extend df1h_slice to include future bars for simulation (up to 48 ahead)
        sim_end  = min(i + 49, len(df1h))
        df_sim   = df1h.iloc[max(0, i - ltf_bars + 1): sim_end].reset_index(drop=True)
        sig_bar  = len(df1h_slice) - 1   # signal bar index within df_sim

        trade = _simulate_trade(signal, df_sim, sig_bar, symbol)
        if trade is None:
            continue   # expired without resolution — skip

        trades.append(trade)
        # Block future signals while in this trade
        in_trade      = True
        trade_end_bar = i + (df_sim.index.get_loc(sig_bar + 1 + trades[-1].get("_exit_offset", 0))
                             if False else 8)  # simple cooldown: 8 bars minimum

        if verbose:
            r = trade["net_r"]
            print(f"  {trade['open_time']} {trade['side']:4s} [{trade['session'][:3]}] "
                  f"gross={trade['gross_r']:+.2f}R net={r:+.2f}R {trade['exit_reason']}")

    return _compute_stats(trades, symbol)


# ── Stats ─────────────────────────────────────────────────────────────────────

def _compute_stats(trades: list[dict], symbol: str) -> dict:
    n = len(trades)
    if n == 0:
        return {"symbol": symbol, "n": 0, "win_rate": 0, "gross_pf": 0,
                "net_pf": 0, "avg_net_r": 0, "max_dd_r": 0,
                "trades": trades, "verdict": "FAIL (n=0)"}

    gross_r = [t["gross_r"] for t in trades]
    net_r   = [t["net_r"]   for t in trades]
    wins    = [r for r in gross_r if r > 0]
    losses  = [abs(r) for r in gross_r if r < 0]
    gross_pf = sum(wins) / sum(losses) if losses else float("inf")

    net_wins   = [r for r in net_r if r > 0]
    net_losses = [abs(r) for r in net_r if r < 0]
    net_pf     = sum(net_wins) / sum(net_losses) if net_losses else float("inf")

    peak = cum = max_dd = 0.0
    for r in net_r:
        cum  += r
        peak  = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    win_rate = len(wins) / n
    gate     = n >= 50 and net_pf > 1.0
    verdict  = f"PASS" if gate else f"FAIL (n={n}, net_pf={net_pf:.3f})"

    return {
        "symbol":    symbol,
        "n":         n,
        "win_rate":  round(win_rate * 100, 1),
        "gross_pf":  round(gross_pf, 3),
        "net_pf":    round(net_pf, 3),
        "avg_net_r": round(sum(net_r) / n, 3),
        "max_dd_r":  round(max_dd, 3),
        "trades":    trades,
        "verdict":   verdict,
        "gate_pass": gate,
    }


# ── CSV writer ────────────────────────────────────────────────────────────────

_CSV_COLS = [
    "symbol", "side", "setup", "session", "bias",
    "open_time", "close_time", "entry", "sl", "tp1", "tp",
    "r_dist", "gross_r", "fee_r", "net_r", "win", "exit_reason", "be_exit",
]


def _write_csv(trades: list[dict], path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(trades)
    print(f"  → trades written to {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Session Trader Phase-0 backtest")
    ap.add_argument("--symbol", default="all", help="EURUSD | GBPUSD | all")
    ap.add_argument("--csv", default="", help="Write trade log to CSV path")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    cfg = _load_strategy_cfg()
    symbols = ["EURUSD", "GBPUSD"] if args.symbol == "all" else [args.symbol.upper()]

    all_trades: list[dict] = []
    results: list[dict]    = []

    for sym in symbols:
        print(f"\nRunning {sym} …")
        stats = run_backtest(sym, cfg, verbose=args.verbose)
        results.append(stats)
        all_trades.extend(stats["trades"])

        print(f"  n={stats['n']}  win={stats['win_rate']}%  "
              f"gross_pf={stats['gross_pf']:.3f}  net_pf={stats['net_pf']:.3f}  "
              f"avg_r={stats['avg_net_r']:+.3f}  max_dd={stats['max_dd_r']:.2f}R  "
              f"→ {stats['verdict']}")

    if len(symbols) > 1 and all_trades:
        print("\n── Combined ──────────────────────────────────────────────")
        combined = _compute_stats(all_trades, "COMBINED")
        print(f"  n={combined['n']}  win={combined['win_rate']}%  "
              f"gross_pf={combined['gross_pf']:.3f}  net_pf={combined['net_pf']:.3f}  "
              f"avg_r={combined['avg_net_r']:+.3f}  max_dd={combined['max_dd_r']:.2f}R  "
              f"→ {combined['verdict']}")

    if args.csv and all_trades:
        _write_csv(all_trades, args.csv)

    # Phase-0 verdict summary
    print("\n── Phase-0 Gate ──────────────────────────────────────────")
    print("  Gate: n ≥ 50 AND net PF > 1.0")
    for r in results:
        sym_result = "✓ PASS" if r.get("gate_pass") else "✗ FAIL"
        print(f"  {r['symbol']:8s}  {sym_result}  n={r['n']}  net_pf={r['net_pf']:.3f}")

    # Reminder: log to VERDICT_LOG.md before proceeding
    print("\n  → If PASS: add a row to docs/VERDICT_LOG.md (Trial N).")
    print("    If FAIL: change a parameter = new trial row. Never retune in-place.")


if __name__ == "__main__":
    main()
