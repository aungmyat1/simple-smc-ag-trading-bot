"""
Phase-0 gate: run SMC signal on historical data and check the gate.

Gate criteria (from CLAUDE.md §4):
  n >= 50 AND net PF > 1.0

Fee model: Bybit taker 0.06%/side = 0.12% round trip.
Entry: open of bar AFTER signal fires (conservative).
SL/TP: checked intrabar (high/low of subsequent bars).

Usage:
    # First fetch data if needed:
    python scripts/fetch_data.py

    # Run the backtest:
    python scripts/backtest.py
    python scripts/backtest.py --csv results.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))   # allow running directly

from bot import config
from bot.signal import get_signals

TAKER_FEE    = 0.0006   # 0.06% per side
ROUND_TRIP   = TAKER_FEE * 2


def _intrabar_exit(
    high:        np.ndarray,
    low:         np.ndarray,
    entry_price: float,
    sl:          float,
    tp:          float,
    start_bar:   int,
) -> tuple[float, str, int]:
    """
    Scan bars from start_bar until SL or TP is hit.
    Returns (exit_price, reason, exit_bar_idx).
    Assumes a long position.
    """
    for k in range(start_bar, len(high)):
        if low[k] <= sl:
            return sl, "SL", k
        if high[k] >= tp:
            return tp, "TP", k
    # end of data — exit at last close approximation (not ideal but rare)
    return (sl + tp) / 2, "EOD", len(high) - 1


def run_backtest(df: pd.DataFrame) -> dict:
    """
    Simulate trades on df with signals pre-computed.
    Returns stats dict.
    """
    df = get_signals(df)

    open_  = df["open"].values
    high   = df["high"].values
    low    = df["low"].values
    signal = df["signal"].values
    sl_arr = df["sl"].values
    tp_arr = df["tp"].values

    trades: list[dict] = []
    in_position = False
    skip_until  = 0

    for i in range(config.STARTUP_CANDLE, len(df) - 1):
        if i < skip_until:
            continue

        if in_position:
            continue   # one position at a time (handled via skip_until)

        if signal[i] != 1:
            continue

        entry_bar   = i + 1
        entry_price = open_[entry_bar]
        sl          = sl_arr[i]
        tp          = tp_arr[i]

        if np.isnan(sl) or np.isnan(tp) or entry_price <= sl:
            continue

        exit_price, reason, exit_bar = _intrabar_exit(
            high, low, entry_price, sl, tp, entry_bar
        )

        risk_r    = entry_price - sl
        gross_pnl = exit_price - entry_price              # per unit
        fee_cost  = entry_price * ROUND_TRIP              # per unit (approx)
        net_pnl   = gross_pnl - fee_cost
        gross_r   = gross_pnl / risk_r
        net_r     = net_pnl / risk_r
        fee_r     = fee_cost / risk_r

        trades.append({
            "entry_bar":   entry_bar,
            "exit_bar":    exit_bar,
            "entry":       round(entry_price, 2),
            "exit":        round(exit_price, 2),
            "sl":          round(sl, 2),
            "tp":          round(tp, 2),
            "gross_r":     round(gross_r, 4),
            "fee_r":       round(fee_r, 4),
            "net_r":       round(net_r, 4),
            "reason":      reason,
        })
        skip_until = exit_bar + 1

    if not trades:
        return {"n": 0, "gross_pf": 0.0, "net_pf": 0.0, "win_rate": 0.0, "avg_fee_r": 0.0}

    gross_wins = sum(t["gross_r"] for t in trades if t["gross_r"] > 0)
    gross_loss = abs(sum(t["gross_r"] for t in trades if t["gross_r"] <= 0))
    net_wins   = sum(t["net_r"]   for t in trades if t["net_r"] > 0)
    net_loss   = abs(sum(t["net_r"]   for t in trades if t["net_r"] <= 0))
    wins       = sum(1 for t in trades if t["net_r"] > 0)

    return {
        "n":         len(trades),
        "gross_pf":  round(gross_wins / gross_loss, 4) if gross_loss else float("inf"),
        "net_pf":    round(net_wins / net_loss, 4) if net_loss else float("inf"),
        "win_rate":  round(wins / len(trades) * 100, 2),
        "avg_fee_r": round(sum(t["fee_r"] for t in trades) / len(trades), 4),
        "trades":    trades,
    }


def print_report(stats: dict) -> None:
    n        = stats["n"]
    gross_pf = stats["gross_pf"]
    net_pf   = stats["net_pf"]
    win_rate = stats["win_rate"]
    avg_fee  = stats["avg_fee_r"]

    print("\n" + "=" * 55)
    print("  SMC Bot — Phase-0 Backtest Report")
    print("=" * 55)
    print(f"  Signal family : SMC (OB + Sweep + CHoCH)  Trial 3")
    print(f"  Timeframe     : {config.SYMBOL} {config.TIMEFRAME}m")
    print(f"  Fee model     : Bybit taker {TAKER_FEE*100:.2f}%/side")
    print("-" * 55)
    print(f"  Trades (n)    : {n}")
    print(f"  Win rate      : {win_rate:.1f}%")
    print(f"  Gross PF      : {gross_pf:.3f}")
    print(f"  Avg fee (R)   : {avg_fee:.3f}")
    print(f"  Net PF        : {net_pf:.3f}")
    print("-" * 55)

    gate_n  = n >= 50
    gate_pf = net_pf > 1.0
    verdict = "PASS" if (gate_n and gate_pf) else "FAIL"

    print(f"  Gate n≥50     : {'PASS' if gate_n  else 'FAIL'}  ({n})")
    print(f"  Gate net PF>1 : {'PASS' if gate_pf else 'FAIL'}  ({net_pf})")
    print(f"\n  VERDICT: {verdict}")
    print("=" * 55)

    if verdict == "PASS":
        print("\n  → Phase-0 gate cleared. Proceed to Phase-1 paper trade.")
        print("  → Log this trial in docs/VERDICT_LOG.md before starting.")
    else:
        print("\n  → Gate FAILED. Do NOT proceed to paper trade.")
        print("  → Log this trial in docs/VERDICT_LOG.md.")
        print("  → Change signal family (new trial) before re-running.")
    print()


def save_trades_csv(stats: dict, path: str) -> None:
    trades = stats.get("trades", [])
    if not trades:
        print(f"  (no trades to save)")
        return
    keys = list(trades[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(trades)
    print(f"  Trade log saved → {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase-0 SMC backtest")
    parser.add_argument("--data",  default=str(config.CACHE_DIR / f"{config.SYMBOL}_{config.TIMEFRAME}m.parquet"),
                        help="Path to OHLCV parquet file")
    parser.add_argument("--csv",   default=None, help="Save trade list to this CSV path")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"Data file not found: {data_path}")
        print("Run:  python scripts/fetch_data.py")
        sys.exit(1)

    print(f"Loading data from {data_path} …")
    df = pd.read_parquet(data_path)
    print(f"  {len(df)} bars | {df['ts'].iloc[0]} → {df['ts'].iloc[-1]}")

    stats = run_backtest(df)
    print_report(stats)

    if args.csv:
        save_trades_csv(stats, args.csv)


if __name__ == "__main__":
    main()
