"""
Phase-0 gate — Trial 4: smc_bot/ SMC chain on Bybit 1H+5M.

Signal chain (matches smc_bot/bot.py exactly):
  structure.get_bias → poi.get_pois → price_in_poi →
  liquidity.get_sweep → confirmation.get_choch

Exit model (matches smc_bot/bot.py):
  Single TP at target_r × risk (2R default); SL = sweep wick × (1 − sl_buffer)

Fee model: Bybit taker 0.06%/side = 0.12% round trip (net-of-fees only).

Gate: n ≥ 50 AND net PF > 1.0

Config: read from smc_bot/config.yaml — same values as the live bot.
No imports from _archive/ in this file.

Performance notes (see docs/PERFORMANCE_AUDIT.md):
  All three O(N²) bottlenecks (liquidity._swing_lows, structure._swing_highs/lows,
  poi._atr14) are replaced with precomputed arrays + bisect lookups in this file.
  Signal results are mathematically identical; see docs/PERFORMANCE_PLAN.md §Signal
  Identity Guarantee.

Usage:
    python3 scripts/backtest.py
    python3 scripts/backtest.py --htf data/cache/BTCUSDT_60m.parquet \\
                                --ltf data/cache/BTCUSDT_5m.parquet \\
                                --csv data/trial4_trades.csv
    # profiling only (do NOT use for real trials):
    python3 scripts/backtest.py --max-bars 5000
"""
from __future__ import annotations

import argparse
import bisect
import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from smc_bot import poi  # price_in_poi only — fast, kept as-is

# ── Config (smc_bot/config.yaml — single source of truth) ────────────────────

def _load_cfg() -> dict:
    with open(ROOT / "smc_bot" / "config.yaml") as f:
        return yaml.safe_load(f)

_CFG      = _load_cfg()
SYMBOL    = _CFG["exchange"]["symbol"]          # BTCUSDT
SL_BUF    = _CFG["risk"]["sl_buffer"]          # 0.001
TARGET_R  = _CFG["risk"]["target_r"]           # 2.0
SWING_N   = _CFG["structure"]["swing_n"]       # 5
OB_LB     = _CFG["poi"]["ob_lookback"]         # 50
FVG_LB    = _CFG["poi"]["fvg_lookback"]        # 30
DISP_ATR  = _CFG["poi"]["displacement_atr"]    # 1.5
LIQ_SN    = _CFG["liquidity"]["swing_n"]       # 3
LIQ_LB    = _CFG["liquidity"]["lookback"]      # 30
CHOCH_LB  = _CFG["confirmation"]["lookback"]   # 10

TAKER_FEE  = 0.0006   # Bybit taker 0.06%/side (not in config.yaml — exchange constant)
ROUND_TRIP = TAKER_FEE * 2

# Warmup: enough bars for stable HTF bias + POI detection
HTF_WARMUP = max(OB_LB, FVG_LB, 2 * SWING_N + 1)   # 50 bars
LTF_WARMUP = LIQ_LB + 2 * LIQ_SN + 1               # 37 bars


# ── Precomputed arrays (populated once by _precompute) ────────────────────────

_H_1H: np.ndarray
_L_1H: np.ndarray
_O_1H: np.ndarray
_C_1H: np.ndarray
_SH_1H: list[int]       # 1H swing high indices (sorted asc)
_SL_1H: list[int]       # 1H swing low  indices (sorted asc)
_ATR14_1H: np.ndarray   # ATR14 (Wilder EWM) for every 1H bar

_H_5M: np.ndarray
_L_5M: np.ndarray
_C_5M: np.ndarray
_O_5M: np.ndarray
_SL_5M: np.ndarray      # 5M swing low indices (sorted, int64) — bisect target


def _swing_lows_np(low: np.ndarray, n: int) -> list[int]:
    """Same logic as smc_bot/structure._swing_lows and smc_bot/liquidity._swing_lows."""
    result = []
    for i in range(n, len(low) - n):
        if low[i] == low[i - n : i + n + 1].min():
            result.append(i)
    return result


def _swing_highs_np(high: np.ndarray, n: int) -> list[int]:
    result = []
    for i in range(n, len(high) - n):
        if high[i] == high[i - n : i + n + 1].max():
            result.append(i)
    return result


def _atr14_series(df: pd.DataFrame) -> np.ndarray:
    """ATR14 (Wilder EWM) for every bar — same formula as smc_bot/poi._atr14."""
    prev = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"]  - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=14, adjust=False).mean().values


def _precompute(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> None:
    global _H_1H, _L_1H, _O_1H, _C_1H, _SH_1H, _SL_1H, _ATR14_1H
    global _H_5M, _L_5M, _C_5M, _O_5M, _SL_5M

    print("  Precomputing 1H swings + ATR14 …", flush=True)
    _H_1H = df_1h["high"].values
    _L_1H = df_1h["low"].values
    _O_1H = df_1h["open"].values
    _C_1H = df_1h["close"].values
    _SH_1H = _swing_highs_np(_H_1H, SWING_N)
    _SL_1H = _swing_lows_np(_L_1H, SWING_N)
    _ATR14_1H = _atr14_series(df_1h)

    print("  Precomputing 5M swing lows …", flush=True)
    _H_5M = df_5m["high"].values
    _L_5M = df_5m["low"].values
    _C_5M = df_5m["close"].values
    _O_5M = df_5m["open"].values
    _SL_5M = np.array(_swing_lows_np(_L_5M, LIQ_SN), dtype=np.int64)


# ── Fast signal functions (O(1)–O(lookback) each) ────────────────────────────

def _fast_bias(htf_idx: int) -> str:
    """
    O(log N) replacement for structure.get_bias(df_1h.iloc[:htf_idx+1], swing_n=SWING_N).

    Confirmed swing at index j requires SWING_N bars after it, so j ≤ htf_idx − SWING_N.
    This matches the growing-window exclusion: range(n, len(high)-n) where n=SWING_N.
    """
    max_conf = htf_idx - SWING_N
    sh_end   = bisect.bisect_right(_SH_1H, max_conf)
    sl_end   = bisect.bisect_right(_SL_1H, max_conf)
    if sh_end < 2 or sl_end < 2:
        return "neutral"
    hh = _H_1H[_SH_1H[sh_end - 1]] > _H_1H[_SH_1H[sh_end - 2]]
    hl = _L_1H[_SL_1H[sl_end - 1]] > _L_1H[_SL_1H[sl_end - 2]]
    lh = _H_1H[_SH_1H[sh_end - 1]] < _H_1H[_SH_1H[sh_end - 2]]
    ll = _L_1H[_SL_1H[sl_end - 1]] < _L_1H[_SL_1H[sl_end - 2]]
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "neutral"


def _fast_pois(htf_idx: int, bias: str) -> list[dict]:
    """
    O(OB_LB + FVG_LB) replacement for poi.get_pois(df_1h.iloc[:htf_idx+1], bias, ...).

    Uses _ATR14_1H[htf_idx] instead of recomputing EWM over the full growing slice.
    Logic is identical to smc_bot/poi.py:get_pois — verified line-by-line.
    """
    if bias == "neutral":
        return []
    n   = htf_idx + 1
    h   = _H_1H[:n]
    l   = _L_1H[:n]
    o   = _O_1H[:n]
    c   = _C_1H[:n]
    atr = float(_ATR14_1H[htf_idx])
    zones: list[dict] = []

    if bias == "bullish":
        start = max(0, n - OB_LB)
        for j in range(start, n - 1):
            if c[j] >= o[j]:
                continue
            j1 = j + 1
            if c[j1] > o[j1] and (h[j1] - l[j1]) >= DISP_ATR * atr:
                zones.append({"kind": "OB", "low": float(min(o[j], c[j])),
                               "high": float(max(o[j], c[j]))})
        start = max(2, n - FVG_LB)
        for i in range(start, n):
            fvg_lo, fvg_hi = float(h[i - 2]), float(l[i])
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi})

    elif bias == "bearish":
        start = max(0, n - OB_LB)
        for j in range(start, n - 1):
            if c[j] <= o[j]:
                continue
            j1 = j + 1
            if c[j1] < o[j1] and (h[j1] - l[j1]) >= DISP_ATR * atr:
                zones.append({"kind": "OB", "low": float(min(o[j], c[j])),
                               "high": float(max(o[j], c[j]))})
        start = max(2, n - FVG_LB)
        for i in range(start, n):
            fvg_hi, fvg_lo = float(l[i - 2]), float(h[i])
            if fvg_hi > fvg_lo:
                zones.append({"kind": "FVG", "low": fvg_lo, "high": fvg_hi})

    return zones


def _fast_sweep(i: int) -> dict | None:
    """
    O(n_candidates × lookback) replacement for
    liquidity.get_sweep(df_5m.iloc[:i+1], bias, LIQ_LB, LIQ_SN).

    Matches the original exactly:
      n = i + 1  (same as len(df) in the original)
      scan_start = max(LIQ_SN * 2 + 1, n - LIQ_LB)
      candidates = confirmed swing lows in [scan_start, i - LIQ_SN]
      sweep search: range(sl_idx + 1, n), most-recent swing first
    """
    n          = i + 1
    scan_start = max(LIQ_SN * 2 + 1, n - LIQ_LB)
    max_conf   = i - LIQ_SN   # confirmed swing low must have LIQ_SN right-side bars
    if max_conf < scan_start:
        return None
    left  = bisect.bisect_left(_SL_5M, scan_start)
    right = bisect.bisect_right(_SL_5M, max_conf)
    for sl_idx in _SL_5M[left:right][::-1]:   # most-recent first
        level = _L_5M[sl_idx]
        for k in range(int(sl_idx) + 1, n):
            if _L_5M[k] < level and _C_5M[k] > level:
                return {
                    "bar_idx":      int(k),
                    "swept_level":  float(level),
                    "wick_extreme": float(_L_5M[k]),
                }
    return None


def _fast_choch(sweep: dict, i: int) -> bool:
    """
    O(CHOCH_LB) replacement for
    confirmation.get_choch(df_5m.iloc[:i+1], "bullish", sweep, CHOCH_LB).

    long-only: only the bullish branch is needed (bias == "bullish" is pre-checked).
    Matches the original: sweep_bar >= n-1 → n-1 = i; last close = close[-1] = _C_5M[i].
    """
    sweep_bar = sweep["bar_idx"]
    if sweep_bar >= i:
        return False
    ref_start = max(0, sweep_bar - CHOCH_LB)
    ref_level = float(_H_5M[ref_start : sweep_bar + 1].max())
    return bool(_C_5M[i] > ref_level)


# ── HTF alignment ─────────────────────────────────────────────────────────────

def _align_htf(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> np.ndarray:
    """For each 5M bar return the iloc of the last complete 1H bar (−1 if warmup not met)."""
    htf_ts = df_1h["ts"].values
    ltf_ts = df_5m["ts"].values
    result = np.full(len(ltf_ts), -1, dtype=int)
    for i, ts in enumerate(ltf_ts):
        idx = int(np.searchsorted(htf_ts, ts, side="left")) - 1
        result[i] = idx if idx >= HTF_WARMUP else -1
    return result


# ── Exit simulation ───────────────────────────────────────────────────────────

def _scan_exit(
    high: np.ndarray,
    low:  np.ndarray,
    entry_bar: int,
    sl: float,
    tp: float,
) -> tuple[float, str, int]:
    """First SL or TP hit from entry_bar onward. Returns (price, reason, bar)."""
    for k in range(entry_bar, len(high)):
        if low[k] <= sl:
            return sl, "SL", k
        if high[k] >= tp:
            return tp, "TP", k
    return sl, "EOD-SL", len(high) - 1  # conservative: treat open EOD as SL


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> dict:
    htf_map    = _align_htf(df_1h, df_5m)
    open_5m    = _O_5M   # precomputed — same as df_5m["open"].values
    high_5m    = _H_5M
    low_5m     = _L_5M

    # Cache bias + POI by htf_idx: 1H context only changes when the 1H bar advances.
    _htf_cache: dict[int, tuple] = {}
    trades: list[dict] = []
    skip_until = 0

    for i in range(LTF_WARMUP, len(df_5m) - 1):
        if i < skip_until:
            continue

        htf_idx = int(htf_map[i])
        if htf_idx < 0:
            continue

        if htf_idx not in _htf_cache:
            bias_val = _fast_bias(htf_idx)
            pois_val = _fast_pois(htf_idx, bias_val)
            _htf_cache[htf_idx] = (bias_val, pois_val)

        bias, pois = _htf_cache[htf_idx]

        if bias != "bullish":   # long-only per CLAUDE.md §1
            continue

        price = float(_C_5M[i])

        if poi.price_in_poi(price, pois) is None:
            continue

        sweep = _fast_sweep(i)
        if sweep is None:
            continue

        if not _fast_choch(sweep, i):
            continue

        # Signal fires — enter at next bar open (matching bot.py live behaviour)
        entry_bar   = i + 1
        entry_price = float(open_5m[entry_bar])
        sl          = sweep["wick_extreme"] * (1.0 - SL_BUF)

        stop_dist = entry_price - sl
        if stop_dist <= 0:
            continue

        tp = entry_price + TARGET_R * stop_dist

        exit_price, exit_reason, exit_bar = _scan_exit(
            high_5m, low_5m, entry_bar, sl, tp
        )

        gross_r = (exit_price - entry_price) / stop_dist
        fee_r   = ROUND_TRIP * entry_price / stop_dist
        net_r   = gross_r - fee_r

        trades.append({
            "entry_bar": entry_bar,
            "exit_bar":  exit_bar,
            "ts":        str(df_5m["ts"].iloc[i]) if "ts" in df_5m.columns else i,
            "entry":     round(entry_price, 2),
            "sl":        round(sl, 2),
            "tp":        round(tp, 2),
            "gross_r":   round(gross_r, 4),
            "fee_r":     round(fee_r, 4),
            "net_r":     round(net_r, 4),
            "reason":    exit_reason,
        })
        skip_until = exit_bar + 1

    if not trades:
        return {
            "n": 0, "gross_pf": 0.0, "net_pf": 0.0,
            "win_rate": 0.0, "avg_fee_r": 0.0, "max_dd_r": 0.0, "trades": [],
        }

    gross_wins = sum(t["gross_r"] for t in trades if t["gross_r"] > 0)
    gross_loss = abs(sum(t["gross_r"] for t in trades if t["gross_r"] <= 0))
    net_wins   = sum(t["net_r"]   for t in trades if t["net_r"]   > 0)
    net_loss   = abs(sum(t["net_r"]   for t in trades if t["net_r"]   <= 0))
    wins       = sum(1 for t in trades if t["net_r"] > 0)

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["net_r"]
        if equity > peak:
            peak = equity
        max_dd = max(max_dd, peak - equity)

    return {
        "n":         len(trades),
        "gross_pf":  round(gross_wins / gross_loss, 4) if gross_loss else float("inf"),
        "net_pf":    round(net_wins   / net_loss,   4) if net_loss   else float("inf"),
        "win_rate":  round(wins / len(trades) * 100, 2),
        "avg_fee_r": round(sum(t["fee_r"] for t in trades) / len(trades), 4),
        "max_dd_r":  round(max_dd, 4),
        "trades":    trades,
    }


# ── Phase-C report ────────────────────────────────────────────────────────────

def print_report(stats: dict) -> None:
    n, gross_pf, net_pf = stats["n"], stats["gross_pf"], stats["net_pf"]
    print("\n" + "=" * 60)
    print("  SMC Bot — Phase-0 Gate  (Trial 4: smc_bot/ chain)")
    print("=" * 60)
    print(f"  Signal  : 1H swing bias + OB/FVG POI → 5M sweep + CHoCH")
    print(f"  Symbol  : {SYMBOL}  HTF=1H  LTF=5M  (long-only)")
    print(f"  Exit    : single TP={TARGET_R}R  SL=sweep-wick−{SL_BUF*100:.1f}%")
    print(f"  Fee     : Bybit taker {TAKER_FEE*100:.2f}%/side = {ROUND_TRIP*100:.2f}% round-trip")
    print("-" * 60)
    print(f"  Trades  : {n}")
    print(f"  Win rate: {stats['win_rate']:.1f}%")
    print(f"  Gross PF: {gross_pf:.4f}")
    print(f"  Avg fee : {stats['avg_fee_r']:.4f} R")
    print(f"  Net PF  : {net_pf:.4f}")
    print(f"  Max DD  : {stats['max_dd_r']:.4f} R")
    print("-" * 60)

    gate_n  = n >= 50
    gate_pf = net_pf > 1.0
    verdict = "PASS" if (gate_n and gate_pf) else "FAIL"

    print(f"  Gate n≥50    : {'PASS' if gate_n  else 'FAIL'}  (n={n})")
    print(f"  Gate net PF>1: {'PASS' if gate_pf else 'FAIL'}  (net PF={net_pf})")
    print(f"\n  VERDICT: {verdict}")
    print("=" * 60)

    if verdict == "PASS":
        print("\n  → Phase-0 cleared. Log trial 4 in VERDICT_LOG.md.")
        print("  → Proceed to Phase-1 paper trade (30 days, 100+ trades).")
    else:
        print("\n  → Gate FAILED. Log trial 4. Change signal family — do not tune.")
    print()


# ── Phase-D funnel ────────────────────────────────────────────────────────────

def count_funnel(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> dict:
    """
    Count candidates surviving each AND-gated stage — diagnosis only.
    Stages match bot.py's signal chain exactly.
    """
    htf_map  = _align_htf(df_1h, df_5m)

    counts = {
        "total_bars": 0,
        "bias":       0,
        "poi":        0,
        "sweep":      0,
        "choch":      0,
    }

    _htf_cache: dict[int, tuple] = {}

    for i in range(LTF_WARMUP, len(df_5m) - 1):
        htf_idx = int(htf_map[i])
        if htf_idx < 0:
            continue

        counts["total_bars"] += 1

        if htf_idx not in _htf_cache:
            bias_val = _fast_bias(htf_idx)
            pois_val = _fast_pois(htf_idx, bias_val)
            _htf_cache[htf_idx] = (bias_val, pois_val)

        bias, pois = _htf_cache[htf_idx]

        if bias != "bullish":
            continue
        counts["bias"] += 1

        price  = float(_C_5M[i])
        in_poi = any(z["low"] <= price <= z["high"] for z in pois)
        if not in_poi:
            continue
        counts["poi"] += 1

        sweep = _fast_sweep(i)
        if sweep is None:
            continue
        counts["sweep"] += 1

        if not _fast_choch(sweep, i):
            continue
        counts["choch"] += 1

    return counts


def print_funnel(counts: dict) -> None:
    total  = counts["total_bars"]
    stages = [
        ("bias",   "1H swing bias bullish"),
        ("poi",    "Price inside 1H OB/FVG"),
        ("sweep",  "5M liquidity sweep"),
        ("choch",  "5M CHoCH confirmed"),
    ]
    print("\n" + "=" * 60)
    print("  PHASE-D FUNNEL  (bot.py signal chain, diagnosis only)")
    print("=" * 60)
    print(f"  {'Stage':<30}  {'n':>6}  {'%total':>7}  {'drop':>7}")
    print(f"  {'-'*30}  {'-'*6}  {'-'*7}  {'-'*7}")
    print(f"  {'Total candidate bars':<30}  {total:>6}")
    prev = total
    for key, label in stages:
        n       = counts[key]
        pct     = n / total * 100 if total else 0.0
        dropped = prev - n
        print(f"  {label:<30}  {n:>6}  {pct:>6.1f}%  {dropped:>6} ↓")
        prev = n
    print(f"  {'─'*30}")
    print(f"  {'→ SIGNALS (= choch)':<30}  {counts['choch']:>6}")
    print("=" * 60)
    if total and counts["choch"] < 50:
        prev2 = total
        worst_key, worst_drop = None, 0
        for key, _ in stages:
            d = prev2 - counts[key]
            if d > worst_drop:
                worst_drop = d
                worst_key  = key
            prev2 = counts[key]
        label = next(lbl for k, lbl in stages if k == worst_key)
        print(f"\n  WARNING n<50 — starving stage: {label} (−{worst_drop} bars)")
    print()


# ── CSV export ────────────────────────────────────────────────────────────────

def save_trades_csv(stats: dict, path: str) -> None:
    trades = stats.get("trades", [])
    if not trades:
        return
    keys = list(trades[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(trades)
    print(f"  Trade log → {path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--htf", default=str(ROOT / "data" / "cache" / f"{SYMBOL}_60m.parquet"))
    parser.add_argument("--ltf", default=str(ROOT / "data" / "cache" / f"{SYMBOL}_5m.parquet"))
    parser.add_argument("--csv", default=None)
    parser.add_argument("--max-bars", type=int, default=None,
                        help="Limit 5M bars processed (profiling only — do NOT use for real trials)")
    args = parser.parse_args()

    for label, path in [("HTF (1H)", args.htf), ("LTF (5M)", args.ltf)]:
        if not Path(path).exists():
            print(f"Missing {label} data: {path}")
            print("Run: python scripts/fetch_data.py --interval 60 --days 730")
            print("     python scripts/fetch_data.py --interval 5  --days 730")
            sys.exit(1)

    print(f"Loading 1H  from {args.htf} …", flush=True)
    df_1h = pd.read_parquet(args.htf)
    print(f"  {len(df_1h)} bars | {df_1h['ts'].iloc[0]} → {df_1h['ts'].iloc[-1]}", flush=True)

    print(f"Loading 5M  from {args.ltf} …", flush=True)
    df_5m = pd.read_parquet(args.ltf)
    if args.max_bars:
        df_5m = df_5m.iloc[: args.max_bars].copy()
        print(f"  PROFILING SLICE: first {args.max_bars} bars", flush=True)
    print(f"  {len(df_5m)} bars | {df_5m['ts'].iloc[0]} → {df_5m['ts'].iloc[-1]}", flush=True)

    print("Precomputing signal arrays …", flush=True)
    _precompute(df_1h, df_5m)

    print("Running Phase-C backtest …", flush=True)
    stats = run_backtest(df_1h, df_5m)
    print_report(stats)
    sys.stdout.flush()

    print("Running Phase-D funnel …", flush=True)
    funnel = count_funnel(df_1h, df_5m)
    print_funnel(funnel)
    sys.stdout.flush()

    if args.csv:
        save_trades_csv(stats, args.csv)


if __name__ == "__main__":
    main()
