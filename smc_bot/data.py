"""
Market data — fetch OHLCV from Bybit via ccxt (public endpoints, no auth needed).
"""
import logging

import ccxt
import pandas as pd

log = logging.getLogger(__name__)


def make_client(testnet: bool = True) -> ccxt.bybit:
    """Create a ccxt Bybit client pointed at testnet or mainnet."""
    ex = ccxt.bybit({
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


def get_candles(
    client: ccxt.bybit,
    symbol: str,
    timeframe: str,
    limit: int = 100,
) -> pd.DataFrame:
    """
    Return the last `limit` CLOSED candles as a DataFrame.
    Columns: ts (UTC datetime), open, high, low, close, volume
    The last forming (incomplete) candle is dropped.
    """
    raw = client.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.iloc[:-1].reset_index(drop=True)   # drop forming candle
    log.debug("Fetched %d closed %s candles for %s", len(df), timeframe, symbol)
    return df
