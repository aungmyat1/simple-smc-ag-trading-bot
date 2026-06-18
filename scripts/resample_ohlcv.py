"""
Resample an M1 master parquet into M5 / M15 / H1 / H4 parquet files.

Why this exists (forex readiness plan, docs/FOREX_VALIDATION.md):
  Institutional multi-timeframe systems download ONE master series (M1) and
  derive every higher timeframe from it, rather than pulling each timeframe
  separately from the broker. Separate pulls drift: an H4 bar from the feed and
  an H4 bar you build from its own M1 can disagree on open/high/low/close
  because of differing session boundaries and gap handling. Deriving H1/H4 from
  the same M1 master removes that inconsistency, so the backtest's HTF bias and
  LTF execution always reference the same underlying ticks.

  Pipeline:
      M1 master  →  H4 (macro bias)  →  H1 (box / execution)  →  M15  →  M5

Source-agnostic: the M1 master can come from Dukascopy (free, broker-independent
research feed) or from MetaAPI / VT Markets (the live execution broker). Fetch
M1 with whichever source, then run this once to produce the timeframe set the
backtest reads.

Output schema matches scripts/fetch_data.py and scripts/fetch_forex_data.py
exactly so scripts/backtest.py can read it unchanged:
    columns = [ts(UTC, bar OPEN time), open, high, low, close, volume]
    file    = data/cache/{SYMBOL}_{interval}m.parquet   (e.g. EURUSD_240m.parquet)

Convention: bars are labelled at their OPEN time (left edge), matching Bybit and
MetaAPI candle timestamps and the no-lookahead alignment in backtest.py
(_align_htf takes the last bar whose open ≤ the LTF bar's open).

Usage:
    python scripts/resample_ohlcv.py --in data/cache/EURUSD_1m.parquet
    python scripts/resample_ohlcv.py --in data/cache/EURUSD_1m.parquet \\
        --symbol EURUSD --intervals 5 15 60 240

Exit codes: 0 ok · 1 bad input / empty result · 2 missing file
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT      = Path(__file__).parent.parent
CACHE_DIR = ROOT / "data" / "cache"

# Default timeframe set for the SMC chain: H4 macro + H1 box + M15/M5 execution.
_DEFAULT_INTERVALS = [5, 15, 60, 240]

_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
_REQUIRED = ["ts", "open", "high", "low", "close", "volume"]


def _load_master(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"ERROR: input not found: {path}")
        sys.exit(2)
    df = pd.read_parquet(path)
    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        print(f"ERROR: input missing columns {missing}; have {list(df.columns)}")
        sys.exit(1)
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    if df.empty:
        print("ERROR: input has no rows")
        sys.exit(1)
    return df[_REQUIRED]


def resample_ohlcv(df_m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate a finer OHLCV frame to `minutes`-minute bars (left-labelled).

    Empty periods (forex weekends / gaps) are dropped — no synthetic flat bars.
    Pure function: no I/O, fully unit-testable offline.
    """
    rule = f"{minutes}min"
    out = (
        df_m1.set_index("ts")
        .resample(rule, label="left", closed="left")
        .agg(_AGG)
        .dropna(subset=["open"])          # drop weekend/holiday gaps, not flat-fill
        .reset_index()
    )
    return out[_REQUIRED]


def main() -> None:
    p = argparse.ArgumentParser(description="Resample an M1 master parquet to M5/M15/H1/H4")
    p.add_argument("--in", dest="infile", required=True,
                   help="path to the M1 master parquet (e.g. data/cache/EURUSD_1m.parquet)")
    p.add_argument("--symbol", default=None,
                   help="symbol for output filenames; default: parsed from input stem before '_'")
    p.add_argument("--intervals", type=int, nargs="+", default=_DEFAULT_INTERVALS,
                   help="target intervals in minutes (default: 5 15 60 240)")
    args = p.parse_args()

    in_path = Path(args.infile)
    symbol  = args.symbol or in_path.stem.split("_")[0]
    df_m1   = _load_master(in_path)

    span = f"{df_m1['ts'].iloc[0]} → {df_m1['ts'].iloc[-1]}"
    print(f"Master: {len(df_m1)} bars  {span}  (symbol={symbol})")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for m in sorted(set(args.intervals)):
        out = resample_ohlcv(df_m1, m)
        if out.empty:
            print(f"  {m:>4}m: 0 bars — skipped")
            continue
        dest = CACHE_DIR / f"{symbol}_{m}m.parquet"
        out.to_parquet(dest, index=False)
        print(f"  {m:>4}m: {len(out):>7} bars  {out['ts'].iloc[0]} → {out['ts'].iloc[-1]}  → {dest.name}")

    print("Done. Run the Phase-0 gate next (see docs/FOREX_VALIDATION.md).")


if __name__ == "__main__":
    main()
