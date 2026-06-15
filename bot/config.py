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

SYMBOL   = "BTCUSDT"
LEVERAGE = 1

# ── Risk (non-bypassable) ─────────────────────────────────────────────────────
RISK_PER_TRADE = 0.005   # 0.5% of account per trade
MAX_DAILY_LOSS = 0.02    # 2% — halt for the day
MAX_DRAWDOWN   = 0.10    # 10% from peak — kill switch

# ── Strategy params — Trial 3: SMC 1H POI → 5M execution ────────────────────
# LOCKED — every parameter change = new trial, log in docs/VERDICT_LOG.md
ATR_PERIOD = 14

# Dual-timeframe config
HTF_TIMEFRAME      = "60"   # 1H bars: bias + POI location
LTF_TIMEFRAME      = "5"    # 5M bars: execution (sweep + MSS + entry zone)
HTF_BARS           = 250    # 1H warmup bars to fetch/hold
LTF_BARS           = 500    # 5M warmup bars to fetch/hold

# 1H (HTF) bias + zone detection
HTF_EMA            = 200    # EMA span for 1H trend filter
HTF_SWING_LOOKBACK = 20     # 1H bars to define swing high/low
FIB_DISCOUNT_LEVEL = 0.5    # below this fib level = discount (longs only)
HTF_OB_DISPLACEMENT = 1.5   # displacement range >= this × ATR(14) on 1H
HTF_OB_MAX_AGE     = 50     # 1H bars before an OB zone expires
HTF_EQUAL_LEVEL_TOL = 0.001 # 0.1% — tolerance for "equal" highs/lows

# 5M (LTF) execution
LTF_SWING_LOOKBACK = 10     # 5M bars to define swing high/low
LTF_SWEEP_LOOKBACK = 20     # 5M bars to scan for a liquidity sweep
LTF_SWEEP_PIERCE   = 0.0003 # low must breach ref level by ≥ 0.03%
LTF_OB_MAX_AGE     = 30     # 5M bars before entry OB/FVG expires

# Partial TP management
TP1_R    = 1.0   # close TP1_FRAC of position at 1R
TP1_FRAC = 0.50  # 50% off at TP1, then move SL to breakeven
TP2_R    = 2.0   # close TP2_FRAC of position at 2R
TP2_FRAC = 0.25  # 25% off at TP2; remaining 25% runs to HTF liquidity target
TARGET_R = 3.0   # fallback runner target if no HTF liquidity found above entry

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
DATA_DIR   = ROOT / "data"
CACHE_DIR  = DATA_DIR / "cache"
TRADES_CSV = DATA_DIR / "trades.csv"
LOG_DIR    = ROOT / "logs"
