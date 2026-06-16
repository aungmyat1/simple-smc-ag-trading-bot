"""
V6 — candle staleness guard in data.get_candles().

Contract:
  - last closed candle ts + interval > now - 2×interval → normal DataFrame returned.
  - last closed candle ts + interval <= now - 2×interval → EMPTY DataFrame returned.
  - The 2× threshold uses the CORRECT interval in seconds per timeframe (5m=300s, 1h=3600s).
  - An unknown timeframe skips the staleness check (returns data as-is; caller is safe).
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from smc_bot import data

# ── helpers ───────────────────────────────────────────────────────────────────

def _raw_ohlcv(ts_ms: int, n: int = 5) -> list[list]:
    """Return n synthetic OHLCV rows, the last being 'forming' (gets dropped)."""
    interval_ms = 300_000  # 5m in ms
    rows = []
    for i in range(n):
        t = ts_ms + i * interval_ms
        rows.append([t, 100.0, 110.0, 90.0, 105.0, 1000.0])
    return rows


def _client_stub(raw: list) -> MagicMock:
    c = MagicMock()
    c.fetch_ohlcv.return_value = raw
    return c


# ── tests ─────────────────────────────────────────────────────────────────────

class TestCandleStaleness:

    def test_fresh_5m_data_returns_dataframe(self):
        now_s = time.time()
        # Most-recent closed candle closed 10 s ago (well within 2×300 = 600 s)
        last_close_epoch_ms = int((now_s - 10) * 1000)
        # fetch_ohlcv is called with limit+1; last row is the forming candle
        rows = _raw_ohlcv(last_close_epoch_ms - 4 * 300_000, n=6)
        # Shift the second-to-last row to be the "closed" candle we want
        rows[-2][0] = last_close_epoch_ms - 300_000  # closed 310s ago — fresh
        rows[-1][0] = last_close_epoch_ms             # forming — dropped

        client = _client_stub(rows)
        df = data.get_candles(client, "BTCUSDT", "5m", limit=5)
        assert not df.empty

    def test_stale_5m_data_returns_empty(self):
        now_s = time.time()
        # Last closed candle's interval ended 1200s ago (> 2×300 = 600s)
        stale_close_ms = int((now_s - 1200 - 300) * 1000)  # closed 1500s ago
        rows = _raw_ohlcv(stale_close_ms - 4 * 300_000, n=6)
        rows[-2][0] = stale_close_ms
        rows[-1][0] = stale_close_ms + 300_000

        client = _client_stub(rows)
        df = data.get_candles(client, "BTCUSDT", "5m", limit=5)
        assert df.empty, "Expected empty DataFrame for stale 5m candle data"

    def test_fresh_1h_data_returns_dataframe(self):
        now_s = time.time()
        # Closed 30s ago — fresh for 1h (2×3600 = 7200s threshold)
        last_close_ms = int((now_s - 30) * 1000)
        rows = _raw_ohlcv(last_close_ms - 4 * 3_600_000, n=6)
        rows[-2][0] = last_close_ms - 3_600_000
        rows[-1][0] = last_close_ms
        client = _client_stub(rows)
        df = data.get_candles(client, "BTCUSDT", "1h", limit=5)
        assert not df.empty

    def test_stale_1h_data_returns_empty(self):
        now_s = time.time()
        # Closed 8000s ago > 2×3600 = 7200s
        stale_ms = int((now_s - 8000 - 3600) * 1000)
        rows = _raw_ohlcv(stale_ms - 4 * 3_600_000, n=6)
        rows[-2][0] = stale_ms
        rows[-1][0] = stale_ms + 3_600_000
        client = _client_stub(rows)
        df = data.get_candles(client, "BTCUSDT", "1h", limit=5)
        assert df.empty, "Expected empty DataFrame for stale 1h candle data"

    def test_threshold_is_2x_interval_not_fixed(self):
        """5m and 1h use different thresholds — not a shared constant."""
        # A delay of 700s would be stale for 5m (>2×300=600) but fresh for 1h (<2×3600)
        now_s = time.time()
        delay_s = 700

        # 5m: stale
        stale_5m_ms = int((now_s - delay_s - 300) * 1000)
        rows5 = _raw_ohlcv(stale_5m_ms - 4 * 300_000, n=6)
        rows5[-2][0] = stale_5m_ms
        rows5[-1][0] = stale_5m_ms + 300_000
        df5 = data.get_candles(_client_stub(rows5), "BTCUSDT", "5m", limit=5)
        assert df5.empty, "700s delay must be stale for 5m threshold (2×300=600)"

        # 1h: fresh
        fresh_1h_ms = int((now_s - delay_s - 3600) * 1000)
        rows1 = _raw_ohlcv(fresh_1h_ms - 4 * 3_600_000, n=6)
        rows1[-2][0] = fresh_1h_ms
        rows1[-1][0] = fresh_1h_ms + 3_600_000
        df1 = data.get_candles(_client_stub(rows1), "BTCUSDT", "1h", limit=5)
        assert not df1.empty, "700s delay must be fresh for 1h threshold (2×3600=7200)"
