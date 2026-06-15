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

# ── Strategy params — Trial 3: SMC (OB + Sweep + CHoCH) ──────────────────────
# LOCKED — every parameter change = new trial, log in docs/VERDICT_LOG.md
HTF_EMA             = 200    # trend filter: price must be above this EMA
ATR_PERIOD          = 14
SWING_LOOKBACK      = 20     # bars to define a swing high/low
TARGET_R            = 3.0    # take-profit in R multiples
STARTUP_CANDLE      = 250    # warm-up bars before signals fire

# Order Block (OB) params
OB_DISPLACEMENT_MULT = 1.5   # displacement candle range >= this × ATR
OB_MAX_AGE_BARS      = 100   # retire OB after N bars (keep it tractable)
OB_MAX_TOUCHES       = 2     # retire OB after N price touches

# Liquidity sweep params
SWEEP_MIN_PIERCE = 0.0005    # low must breach ref level by at least 0.05%
SWEEP_LOOKBACK   = 40        # bars to look back for a sweep before entry

# CHoCH params
CHoCH_LOOKBACK = 30          # bars after sweep to find a structure break

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
DATA_DIR   = ROOT / "data"
CACHE_DIR  = DATA_DIR / "cache"
TRADES_CSV = DATA_DIR / "trades.csv"
LOG_DIR    = ROOT / "logs"
