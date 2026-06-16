"""
Market data — fetch OHLCV from Bybit via ccxt (public endpoints, no auth needed).
"""
import logging
import time

import ccxt
import pandas as pd

log = logging.getLogger(__name__)

# Candle staleness tolerance: if the most recent closed candle is older than
# this many multiples of the interval, the data is considered stale and the
# caller should skip the cycle rather than act on outdated signals.
_STALE_MULTIPLIER = 2.0

_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


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
    Returns an empty DataFrame if the data is stale (exchange lag) so the
    caller can skip the cycle cleanly.
    """
    raw = client.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.iloc[:-1].reset_index(drop=True)   # drop forming candle

    # Staleness guard: last closed candle timestamp + interval should be <= now
    interval_sec = _INTERVAL_SECONDS.get(timeframe, 0)
    if interval_sec > 0 and not df.empty:
        last_close_epoch = df["ts"].iloc[-1].timestamp() + interval_sec
        age = time.time() - last_close_epoch
        if age > interval_sec * _STALE_MULTIPLIER:
            log.warning(
                "Stale %s candle data for %s: last close was %.0fs ago (> %.0fs threshold)",
                timeframe, symbol, age, interval_sec * _STALE_MULTIPLIER,
            )
            return pd.DataFrame()

    log.debug("Fetched %d closed %s candles for %s", len(df), timeframe, symbol)
    return df
