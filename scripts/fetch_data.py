"""
Download BTCUSDT 15m OHLCV from Bybit public API and save to parquet.
No API key required — uses the public market data endpoint.

Usage:
    python scripts/fetch_data.py
    python scripts/fetch_data.py --days 730
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
INTERVAL         = "15"   # 15-minute bars
SYMBOL           = "BTCUSDT"
LIMIT            = 1000   # max per request


def _fetch_chunk(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Fetch up to LIMIT bars in [start_ms, end_ms)."""
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
    """Fetch `days` of OHLCV data, handling pagination."""
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
        # each row: [open_time_ms, open, high, low, close, volume, turnover]
        oldest_ts = int(chunk[-1][0])
        cursor_end = oldest_ts - 1
        time.sleep(0.1)   # be polite

    if not all_rows:
        raise RuntimeError("No data returned from Bybit API")

    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df["ts"]       = pd.to_datetime(df["ts"].astype(int), unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df.drop(columns=["turnover"]).sort_values("ts").reset_index(drop=True)
    df = df.drop_duplicates("ts")
    print(f"  {len(df)} bars: {df['ts'].iloc[0]} → {df['ts'].iloc[-1]}")
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=730, help="Days of history to fetch")
    parser.add_argument("--symbol", default=SYMBOL)
    parser.add_argument("--interval", default=INTERVAL)
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df = fetch_ohlcv(args.symbol, args.interval, args.days)
    out = CACHE_DIR / f"{args.symbol}_{args.interval}m.parquet"
    df.to_parquet(out, index=False)
    print(f"Saved → {out}")


if __name__ == "__main__":
    main()
