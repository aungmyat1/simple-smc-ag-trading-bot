"""
Download OHLCV from Bybit public API and save to parquet.
No API key required.

Usage:
    python scripts/fetch_data.py                          # 15m, 730 days
    python scripts/fetch_data.py --interval 5 --days 730  # 5M data
    python scripts/fetch_data.py --interval 60 --days 730 # 1H data
"""
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT      = Path(__file__).parent.parent
CACHE_DIR = ROOT / "data" / "cache"

BYBIT_KLINES_URL = "https://api.bybit.com/v5/market/kline"
SYMBOL           = "BTCUSDT"
LIMIT            = 1000


def _fetch_chunk(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    params = {
        "category": "linear",
        "symbol":   symbol,
        "interval": interval,
        "start":    start_ms,
        "end":      end_ms,
        "limit":    LIMIT,
    }
    resp = requests.get(BYBIT_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    if body.get("retCode") != 0:
        raise RuntimeError(f"Bybit API error: {body}")
    return body["result"]["list"]   # newest first


def fetch_ohlcv(symbol: str, interval: str, days: int) -> pd.DataFrame:
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    all_rows: list = []
    cursor_end = end_ms

    print(f"Fetching {days}d of {symbol} {interval}m data …")
    while cursor_end > start_ms:
        chunk = _fetch_chunk(symbol, interval, start_ms, cursor_end)
        if not chunk:
            break
        all_rows.extend(chunk)
        oldest_ts = int(chunk[-1][0])
        cursor_end = oldest_ts - 1
        time.sleep(0.1)

    if not all_rows:
        raise RuntimeError("No data returned from Bybit API")

    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df.drop(columns=["turnover"]).sort_values("ts").reset_index(drop=True)
    df = df.drop_duplicates("ts")
    print(f"  {len(df)} bars: {df['ts'].iloc[0]} → {df['ts'].iloc[-1]}")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch BTCUSDT OHLCV from Bybit")
    parser.add_argument("--days",     type=int, default=730)
    parser.add_argument("--symbol",   default=SYMBOL)
    parser.add_argument("--interval", default="15",
                        help="Bybit interval: 1 3 5 15 30 60 120 240 360 720 D")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df  = fetch_ohlcv(args.symbol, args.interval, args.days)
    out = CACHE_DIR / f"{args.symbol}_{args.interval}m.parquet"
    df.to_parquet(out, index=False)
    print(f"Saved → {out}")


if __name__ == "__main__":
    main()
