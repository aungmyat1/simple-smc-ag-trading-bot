"""
Phase-0 gate — dual-TF SMC backtest (Trial 3: 1H POI → 5M execution).

Gate: n ≥ 50 AND net PF > 1.0
Fee model: Bybit taker 0.06%/side = 0.12% round trip.

Exit model (3-tier partials):
  TP1 (50%) at 1R   → SL moves to breakeven
  TP2 (25%) at 2R
  Runner (25%) at HTF liquidity or 3R fallback
  SL (all remaining) if hit before any TP

Usage:
    # fetch data first:
    python scripts/fetch_data.py --interval 5  --days 730
    python scripts/fetch_data.py --interval 60 --days 730

    # run gate:
    python scripts/backtest.py
    python scripts/backtest.py --csv trial3_trades.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from bot import config
from bot.signal import get_htf_context, get_ltf_signal

TAKER_FEE  = 0.0006   # 0.06% per side
ROUND_TRIP = TAKER_FEE * 2


def _align_htf(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> pd.Series:
    """
    For each 5M bar timestamp, find the index of the last COMPLETE 1H bar.
    Returns a pd.Series: 5M index → 1H iloc index (int or -1 if not enough 1H data).
    """
    htf_ts = df_1h["ts"].values
    ltf_ts = df_5m["ts"].values
    result = np.full(len(ltf_ts), -1, dtype=int)

    for i, ts in enumerate(ltf_ts):
        # last 1H bar whose close time is strictly before this 5M bar
        idx = np.searchsorted(htf_ts, ts, side="left") - 1
        result[i] = idx if idx >= config.HTF_BARS // 2 else -1

    return pd.Series(result, index=df_5m.index)


def _exit_scan(
    high: np.ndarray,
    low: np.ndarray,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    tp_runner: float,
    start: int,
) -> list[dict]:
    """
    Simulate partial exits from bar `start` onward.
    Returns list of exit events: [{price, qty_frac, reason, bar}].
    """
    sl_cur      = sl
    tp1_hit     = False
    tp2_hit     = False
    sl_at_be    = False
    events: list[dict] = []

    for k in range(start, len(high)):
        # Check SL first (conservative)
        if low[k] <= sl_cur:
            remaining = 1.0 - sum(e["qty_frac"] for e in events)
            events.append({"price": sl_cur, "qty_frac": remaining,
                           "reason": "SL-BE" if sl_at_be else "SL", "bar": k})
            return events

        if not tp1_hit and high[k] >= tp1:
            events.append({"price": tp1, "qty_frac": config.TP1_FRAC, "reason": "TP1", "bar": k})
            tp1_hit  = True
            sl_cur   = entry   # breakeven
            sl_at_be = True

        if tp1_hit and not tp2_hit and high[k] >= tp2:
            events.append({"price": tp2, "qty_frac": config.TP2_FRAC, "reason": "TP2", "bar": k})
            tp2_hit = True

        if tp1_hit and tp2_hit and high[k] >= tp_runner:
            remaining = 1.0 - sum(e["qty_frac"] for e in events)
            events.append({"price": tp_runner, "qty_frac": remaining,
                           "reason": "RUNNER", "bar": k})
            return events

    # EOD — close at last close (fallback)
    remaining = 1.0 - sum(e["qty_frac"] for e in events)
    if remaining > 0:
        events.append({"price": (sl_cur + tp_runner) / 2, "qty_frac": remaining,
                       "reason": "EOD", "bar": len(high) - 1})
    return events


def run_backtest(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> dict:
    htf_map   = _align_htf(df_1h, df_5m)
    high_5m   = df_5m["high"].values
    low_5m    = df_5m["low"].values
    open_5m   = df_5m["open"].values

    trades: list[dict] = []
    skip_until = 0

    warmup = max(config.LTF_BARS // 2, config.HTF_BARS // 2)

    for i in range(warmup, len(df_5m) - 1):
        if i < skip_until:
            continue

        htf_idx = htf_map.iloc[i]
        if htf_idx < 0:
            continue

        df_1h_window = df_1h.iloc[: htf_idx + 1]
        df_5m_window = df_5m.iloc[: i + 1]

        htf_ctx = get_htf_context(df_1h_window)
        sig     = get_ltf_signal(df_5m_window, htf_ctx)

        if sig["action"] != "LONG":
            continue

        entry_bar   = i + 1
        entry_price = open_5m[entry_bar]
        sl          = sig["sl"]
        tp1         = sig["tp1"]
        tp2         = sig["tp2"]
        tp_runner   = sig["tp_runner"]

        if np.isnan(sl) or entry_price <= sl:
            continue

        # Recompute TPs from actual entry (signal was based on close of bar i)
        risk_pts = entry_price - sl
        tp1      = entry_price + risk_pts * config.TP1_R
        tp2      = entry_price + risk_pts * config.TP2_R
        if tp_runner <= entry_price:
            tp_runner = entry_price + risk_pts * config.TARGET_R

        events = _exit_scan(high_5m, low_5m, entry_price, sl, tp1, tp2, tp_runner, entry_bar)
        if not events:
            continue

        # Aggregate across all exit events
        gross_pnl_r = 0.0
        fee_r       = 0.0
        for ev in events:
            pnl_r    = (ev["price"] - entry_price) / risk_pts * ev["qty_frac"]
            fee_frac = entry_price * ROUND_TRIP / risk_pts * ev["qty_frac"]
            gross_pnl_r += pnl_r
            fee_r       += fee_frac

        net_pnl_r = gross_pnl_r - fee_r
        last_bar  = events[-1]["bar"]
        reason    = "+".join(e["reason"] for e in events)

        trades.append({
            "entry_bar": entry_bar,
            "exit_bar":  last_bar,
            "entry":     round(entry_price, 2),
            "sl":        round(sl, 2),
            "tp1":       round(tp1, 2),
            "tp2":       round(tp2, 2),
            "tp_runner": round(tp_runner, 2),
            "gross_r":   round(gross_pnl_r, 4),
            "fee_r":     round(fee_r, 4),
            "net_r":     round(net_pnl_r, 4),
            "reason":    reason,
        })
        skip_until = last_bar + 1

    if not trades:
        return {"n": 0, "gross_pf": 0.0, "net_pf": 0.0, "win_rate": 0.0,
                "avg_fee_r": 0.0, "trades": []}

    gross_wins = sum(t["gross_r"] for t in trades if t["gross_r"] > 0)
    gross_loss = abs(sum(t["gross_r"] for t in trades if t["gross_r"] <= 0))
    net_wins   = sum(t["net_r"]   for t in trades if t["net_r"] > 0)
    net_loss   = abs(sum(t["net_r"]   for t in trades if t["net_r"] <= 0))
    wins       = sum(1 for t in trades if t["net_r"] > 0)

    return {
        "n":         len(trades),
        "gross_pf":  round(gross_wins / gross_loss, 4) if gross_loss else float("inf"),
        "net_pf":    round(net_wins   / net_loss,   4) if net_loss   else float("inf"),
        "win_rate":  round(wins / len(trades) * 100, 2),
        "avg_fee_r": round(sum(t["fee_r"] for t in trades) / len(trades), 4),
        "trades":    trades,
    }


def print_report(stats: dict) -> None:
    n, gross_pf, net_pf = stats["n"], stats["gross_pf"], stats["net_pf"]
    print("\n" + "=" * 58)
    print("  SMC Bot — Phase-0 Backtest  (Trial 3: 1H→5M)")
    print("=" * 58)
    print(f"  Signal  : 1H POI → 5M sweep + MSS + OB/FVG retrace")
    print(f"  Symbol  : {config.SYMBOL}  HTF={config.HTF_TIMEFRAME}m  LTF={config.LTF_TIMEFRAME}m")
    print(f"  Exits   : TP1={config.TP1_R}R({int(config.TP1_FRAC*100)}%)  "
          f"TP2={config.TP2_R}R({int(config.TP2_FRAC*100)}%)  Runner (25%)")
    print(f"  Fee     : Bybit taker {TAKER_FEE*100:.2f}%/side")
    print("-" * 58)
    print(f"  Trades  : {n}")
    print(f"  Win rate: {stats['win_rate']:.1f}%")
    print(f"  Gross PF: {gross_pf:.3f}")
    print(f"  Avg fee : {stats['avg_fee_r']:.3f}R")
    print(f"  Net PF  : {net_pf:.3f}")
    print("-" * 58)

    gate_n  = n >= 50
    gate_pf = net_pf > 1.0
    verdict = "PASS" if (gate_n and gate_pf) else "FAIL"

    print(f"  Gate n≥50    : {'PASS' if gate_n  else 'FAIL'}  ({n})")
    print(f"  Gate net PF>1: {'PASS' if gate_pf else 'FAIL'}  ({net_pf})")
    print(f"\n  VERDICT: {verdict}")
    print("=" * 58)

    if verdict == "PASS":
        print("\n  → Phase-0 cleared. Log trial in VERDICT_LOG.md.")
        print("  → Proceed to Phase-1 paper trade (30 days, 100+ trades).")
    else:
        print("\n  → Gate FAILED. Log trial and change signal family.")
    print()


def save_trades_csv(stats: dict, path: str) -> None:
    trades = stats.get("trades", [])
    if not trades:
        return
    keys = list(trades[0].keys())
    with open(path, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=keys).writeheader()
        csv.DictWriter(f, fieldnames=keys).writerows(trades)
    print(f"  Trade log saved → {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--htf",  default=str(config.CACHE_DIR / f"{config.SYMBOL}_60m.parquet"))
    parser.add_argument("--ltf",  default=str(config.CACHE_DIR / f"{config.SYMBOL}_5m.parquet"))
    parser.add_argument("--csv",  default=None)
    args = parser.parse_args()

    for label, path in [("HTF (1H)", args.htf), ("LTF (5M)", args.ltf)]:
        if not Path(path).exists():
            print(f"Missing {label} data: {path}")
            print("Run:")
            print("  python scripts/fetch_data.py --interval 60 --days 730")
            print("  python scripts/fetch_data.py --interval 5  --days 730")
            sys.exit(1)

    print(f"Loading 1H data from {args.htf} …")
    df_1h = pd.read_parquet(args.htf)
    print(f"  {len(df_1h)} bars | {df_1h['ts'].iloc[0]} → {df_1h['ts'].iloc[-1]}")

    print(f"Loading 5M data from {args.ltf} …")
    df_5m = pd.read_parquet(args.ltf)
    print(f"  {len(df_5m)} bars | {df_5m['ts'].iloc[0]} → {df_5m['ts'].iloc[-1]}")

    stats = run_backtest(df_1h, df_5m)
    print_report(stats)

    if args.csv:
        save_trades_csv(stats, args.csv)


if __name__ == "__main__":
    main()
