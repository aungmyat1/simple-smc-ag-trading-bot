"""
Step-5 Phase-0 gate runner for the forex pivot — turnkey + spread-robust.

Runs the session-box signal (smc_bot/session_range.py via backtest.run_backtest_asian)
across {symbols} × {setup modes} × {spread assumptions} under the forex cost model,
and prints one consolidated PASS/FAIL table plus an overall go/no-go.

Why the spread sweep: a single guessed spread (e.g. 0.8 pip) makes the verdict
fragile. A setup only earns "PASS" here if it clears the gate (n≥50, net PF>1.0)
at EVERY spread level — robustness the BTC graveyard lacked (CLAUDE.md §1).

Data is NOT fetched here (forex feeds are network-gated in the web container).
Fetch first on the VPS (scripts/fetch_forex_data.py), then run this. Missing
parquets are reported and skipped, not fatal.

Usage (VPS):
    python scripts/forex_phase0.py
    python scripts/forex_phase0.py --symbols EURUSD GBPUSD --spreads 0.8 1.2 2.0
    python scripts/forex_phase0.py --cache-dir data/cache --commission-rt-pips 0.6

Exit code: 0 if at least one (symbol, mode) is a robust PASS, else 1.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backtest as bt  # noqa: E402

MODES   = ["all", "sweep", "range", "trend"]
GATE_N  = 50
GATE_PF = 1.0


def _run_one(df_4h, df_1h, mode: str, spread: float,
             commission_rt_pips: float, pip_size: float) -> dict:
    """Run one (mode, spread) cell. Arrays are precomputed by the caller."""
    bt.COST_MODEL         = "forex"
    bt.SPREAD_PIPS        = spread
    bt.COMMISSION_RT_PIPS = commission_rt_pips
    bt.PIP_SIZE           = pip_size
    stats = bt.run_backtest_asian(df_4h=df_4h, df_1h=df_1h, side="both",
                                  setup_filter=mode)
    n   = stats["n"]
    npf = stats["net_pf"]
    return {
        "n":        n,
        "net_pf":   npf,
        "win_rate": stats["win_rate"],
        "avg_fee_r": stats["avg_fee_r"],
        "pass":     bool(n >= GATE_N and npf > GATE_PF and npf != float("inf")),
    }


def _load_pair(cache: Path, symbol: str):
    htf = cache / f"{symbol}_240m.parquet"
    ltf = cache / f"{symbol}_60m.parquet"
    if not htf.exists() or not ltf.exists():
        return None, None, f"missing {htf.name} and/or {ltf.name}"
    df_4h = pd.read_parquet(htf)
    df_1h = pd.read_parquet(ltf)
    return df_4h, df_1h, None


def run(symbols: list[str], spreads: list[float], cache: Path,
        commission_rt_pips: float, pip_size: float) -> bool:
    any_robust_pass = False
    print("=" * 72)
    print("  FOREX STEP-5 PHASE-0 GATE  (session-box: sweep/range/trend)")
    print(f"  Gate: n≥{GATE_N} AND net PF>{GATE_PF}  |  cost=forex  "
          f"commission={commission_rt_pips}pip rt  pip={pip_size}")
    print(f"  Spread sweep (pips): {spreads}  — PASS requires clearing every level")
    print("=" * 72)

    for symbol in symbols:
        df_4h, df_1h, err = _load_pair(cache, symbol)
        print(f"\n  ── {symbol} " + "─" * (66 - len(symbol)))
        if err:
            print(f"     SKIP — {err}")
            print(f"     Fetch on VPS: python scripts/fetch_forex_data.py "
                  f"--symbol {symbol} --interval 240 --days 1825  (and --interval 60)")
            continue

        bars = f"{len(df_4h)} 4H / {len(df_1h)} 1H bars  " \
               f"({df_1h['ts'].iloc[0].date()} → {df_1h['ts'].iloc[-1].date()})"
        print(f"     {bars}")
        bt._precompute(df_4h, df_1h)

        hdr = f"     {'mode':<7}" + "".join(f"{f'{s}pip':>14}" for s in spreads) + f"   {'verdict':>9}"
        print(hdr)
        print("     " + "-" * (len(hdr) - 5))

        for mode in MODES:
            cells = [_run_one(df_4h, df_1h, mode, s, commission_rt_pips, pip_size)
                     for s in spreads]
            robust = all(c["pass"] for c in cells)
            any_robust_pass = any_robust_pass or robust

            def _fmt(c: dict) -> str:
                pf = "inf" if c["net_pf"] == float("inf") else f"{c['net_pf']:.2f}"
                mark = "✓" if c["pass"] else " "
                return f"{c['n']}/{pf}{mark}"

            cellstr = "".join(f"{_fmt(c):>14}" for c in cells)
            verdict = ("ROBUST ✓" if robust
                       else "partial" if any(c["pass"] for c in cells)
                       else "fail")
            print(f"     {mode:<7}{cellstr}   {verdict:>9}")
        print("     (cells show  n / netPF  per spread level; ✓ = clears gate)")

    print("\n" + "=" * 72)
    if any_robust_pass:
        print("  GO — at least one (symbol, mode) clears the gate across all spreads.")
        print("  → Log the passing trial(s) in docs/VERDICT_LOG.md, then proceed to")
        print("    Step 2 (smc_bot/brokers/ MetaAPI adapter) per docs/FOREX_VALIDATION.md.")
    else:
        print("  NO-GO — no (symbol, mode) clears the gate robustly.")
        print("  → Per CLAUDE.md §1: log the FAIL, do not tune. The forex strategy")
        print("    layer retires unless a different signal family is proposed.")
    print("=" * 72)
    return any_robust_pass


def main() -> None:
    p = argparse.ArgumentParser(description="Forex Step-5 Phase-0 gate runner")
    p.add_argument("--symbols", nargs="+", default=["EURUSD", "GBPUSD"])
    p.add_argument("--spreads", nargs="+", type=float, default=[0.8, 1.2, 2.0],
                   help="spread assumptions in pips (PASS must clear all)")
    p.add_argument("--commission-rt-pips", type=float, default=0.6)
    p.add_argument("--pip-size", type=float, default=0.0001,
                   help="0.0001 majors; 0.01 JPY pairs (keep symbol set homogeneous)")
    p.add_argument("--cache-dir", default=str(ROOT / "data" / "cache"))
    args = p.parse_args()

    ok = run(args.symbols, args.spreads, Path(args.cache_dir),
             args.commission_rt_pips, args.pip_size)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
