"""
Download forex OHLCV from MetaAPI (VT Markets MT5) and save to parquet.

Counterpart to scripts/fetch_data.py (Bybit/crypto). Forex feeds are NOT
reachable from the ephemeral session container — run this on the VPS where
METAAPI_TOKEN / METAAPI_ACCOUNT_ID are configured and metaapi.cloud is allowed.

Output schema matches fetch_data.py exactly so scripts/backtest.py can read it:
    columns = [ts(UTC), open, high, low, close, volume]
    file    = data/cache/{SYMBOL}_{interval}m.parquet   (e.g. EURUSD_60m.parquet)

Usage (on the VPS, venv active, .env populated):
    python scripts/fetch_forex_data.py --symbol EURUSD --interval 60  --days 1825
    python scripts/fetch_forex_data.py --symbol EURUSD --interval 240 --days 1825
    python scripts/fetch_forex_data.py --symbol GBPUSD --interval 60  --days 1825
    python scripts/fetch_forex_data.py --symbol GBPUSD --interval 240 --days 1825

Then run the Step-5 Phase-0 gate (see docs/FOREX_VALIDATION.md).

Exit codes: 0 ok · 1 connect/data failure · 2 package/credentials missing
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT      = Path(__file__).parent.parent
CACHE_DIR = ROOT / "data" / "cache"

# Bybit-style integer minute interval → MetaAPI timeframe string
_TF_MAP = {"1": "1m", "5": "5m", "15": "15m", "30": "30m",
           "60": "1h", "240": "4h", "1440": "1d"}

# MetaAPI returns at most 1000 candles per call.
_PAGE = 1000


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass  # env vars may already be exported


async def _fetch(symbol: str, interval: str, days: int) -> pd.DataFrame:
    try:
        from metaapi_cloud_sdk import MetaApi
    except ImportError:
        print("ERROR: metaapi-cloud-sdk not installed.  FIX: pip install metaapi-cloud-sdk")
        sys.exit(2)

    token      = os.getenv("METAAPI_TOKEN", "").strip()
    account_id = os.getenv("METAAPI_ACCOUNT_ID", "").strip()
    if not token or not account_id:
        print("ERROR: METAAPI_TOKEN / METAAPI_ACCOUNT_ID not set in .env")
        sys.exit(2)

    timeframe = _TF_MAP.get(interval)
    if timeframe is None:
        print(f"ERROR: unsupported --interval {interval}; choose from {sorted(_TF_MAP)}")
        sys.exit(2)

    api = MetaApi(token)
    try:
        account = await api.metatrader_account_api.get_account(account_id)
        if account.state == "DRAFT":
            print("ERROR: MetaAPI account is DRAFT (never deployed). Deploy it first.")
            sys.exit(1)
        await account.wait_connected(timeout_in_seconds=120)

        start_ts = datetime.now(timezone.utc) - timedelta(days=days)
        rows: list[dict] = []
        cursor: datetime | None = None  # None = newest; page backward via earliest time
        prev_earliest: pd.Timestamp | None = None

        print(f"Fetching {days}d of {symbol} {timeframe} from MetaAPI …")
        for _page in range(2000):  # guard: 2000×1000 candles ≫ 5yr of 1h/4h
            # get_historical_candles(symbol, timeframe, start_time, limit):
            # returns up to `limit` candles with time <= start_time (backward).
            candles = await account.get_historical_candles(
                symbol, timeframe, cursor, _PAGE,
            )
            if not candles:
                break
            rows.extend(candles)
            # Do not assume batch order — page from the oldest candle in the batch.
            batch_times = pd.to_datetime([c["time"] for c in candles], utc=True)
            earliest = batch_times.min()
            if earliest <= start_ts:
                break
            if prev_earliest is not None and earliest >= prev_earliest:
                break  # no backward progress — broker has no older data
            prev_earliest = earliest
            cursor = earliest.to_pydatetime() - timedelta(seconds=1)
            await asyncio.sleep(0.2)
    finally:
        api.close()

    if not rows:
        raise RuntimeError("No candles returned from MetaAPI")

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["time"], utc=True)
    # MetaAPI volume field is 'tickVolume' (real volume often absent on FX)
    vol = df["tickVolume"] if "tickVolume" in df.columns else df.get("volume", 0)
    out = pd.DataFrame({
        "ts":     df["ts"],
        "open":   df["open"].astype(float),
        "high":   df["high"].astype(float),
        "low":    df["low"].astype(float),
        "close":  df["close"].astype(float),
        "volume": pd.to_numeric(vol, errors="coerce").fillna(0.0).astype(float),
    })
    out = (out[out["ts"] >= start_ts]
           .drop_duplicates("ts")
           .sort_values("ts")
           .reset_index(drop=True))
    print(f"  {len(out)} bars: {out['ts'].iloc[0]} → {out['ts'].iloc[-1]}")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch forex OHLCV from MetaAPI (VT Markets MT5)")
    p.add_argument("--symbol",   default="EURUSD")
    p.add_argument("--interval", default="60", help="minutes: 1 5 15 30 60 240 1440")
    p.add_argument("--days",     type=int, default=1825, help="lookback days (default 5yr)")
    args = p.parse_args()

    _load_env()
    df = asyncio.run(_fetch(args.symbol, args.interval, args.days))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{args.symbol}_{args.interval}m.parquet"
    df.to_parquet(out, index=False)
    print(f"Saved → {out}")


if __name__ == "__main__":
    main()
