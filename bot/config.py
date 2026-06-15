"""Central config — import everywhere, hardcode nowhere."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Exchange ──────────────────────────────────────────────────────────────────
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BYBIT_TESTNET    = os.getenv("BYBIT_TESTNET", "true").lower() == "true"
LIVE_TRADING     = os.getenv("LIVE_TRADING", "false").lower() == "true"

SYMBOL    = "BTCUSDT"
TIMEFRAME = "15"       # Bybit interval string for 15m
LEVERAGE  = 1

# ── Risk (non-bypassable) ─────────────────────────────────────────────────────
RISK_PER_TRADE = 0.005   # 0.5% of account per trade
MAX_DAILY_LOSS = 0.02    # 2% — halt for the day
MAX_DRAWDOWN   = 0.10    # 10% from peak — kill switch

# ── Strategy params (LOCKED — change = new trial, log in docs/VERDICT_LOG.md) ─
EMA_FAST            = 50
EMA_SLOW            = 200
SWING_LOOKBACK      = 20
ATR_PERIOD          = 14
ATR_STOP_MULT       = 1.5
TARGET_R            = 2.5
USE_RETEST          = True
RETEST_ATR          = 0.5
BREAKOUT_VALID_BARS = 10
STARTUP_CANDLE      = 260

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
DATA_DIR   = ROOT / "data"
CACHE_DIR  = DATA_DIR / "cache"
TRADES_CSV = DATA_DIR / "trades.csv"
LOG_DIR    = ROOT / "logs"
