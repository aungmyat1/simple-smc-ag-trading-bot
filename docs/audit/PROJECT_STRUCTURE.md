# PROJECT STRUCTURE AUDIT
**Audit date:** 2026-06-18  
**Auditor:** Automated 7-phase audit (12 agents + manual code verification)  
**Repo:** simple-smc-ag-trading-bot  
**Status:** Phase-0 gate regime — LIVE_TRADING=false enforced

---

## Trading Strategy

**Name:** SMC Sniper — Smart Money Concept liquidity sweep + CHoCH entry  
**Two live deployments share the same signal chain:**

| Deployment | Symbol | HTF | LTF | Broker | Runner |
|---|---|---|---|---|---|
| BTC Bot | BTCUSDT perp | 4H | 1H | Bybit Demo (pybit SDK) | `smc_bot/bot.py` |
| Forex Multi-Strategy | EURUSD, GBPUSD | 4H | 1H | VT Markets MT5 via MetaAPI | `runner.py` |

A second Forex strategy — SessionTrader (`strategies/session_trader.py`) — runs concurrently via `runner.py` with isolated risk state.

---

## Entry Logic (15-step chain)

1. **HTF Swing Bias** — `structure.get_bias()`: HH+HL=bullish, LL+LH=bearish, else neutral → exit cycle
2. **Fib 50% Filter** — `fib.fib_filter()`: longs require price ≤ swing midpoint; shorts require price ≥ midpoint
3. **HTF OB/FVG Detection** — `poi.get_pois()`: OB = last opposite-body candle before displacement ≥ 1.0×ATR; FVG = 3-bar gap
4. **BSL/SSL Pool Scan** — `targets.get_bsl_levels()`: equal-highs/lows clusters within 0.2% tolerance (used for TP, not a gate)
5. **Price Inside Active HTF Zone** — `poi.price_in_poi()`: no matching zone → exit cycle
6. **LTF Liquidity Sweep** — `liquidity.get_sweep()`: wick pierces prior swing, closes back (lookback=30, swing_n=3)
7. **Post-Sweep Displacement Gate** — `liquidity.check_displacement()`: at least one bar ≥ 1.0×ATR after sweep bar
8. **LTF CHoCH** — `confirmation.get_choch()`: close breaks ref high/low from lookback window before sweep
9. **Owned FVG Retest** (`fvg_retest_enabled=true`): `poi.get_owned_fvg()` identifies FVG from first displacement candle post-CHoCH; entry fires only when price enters that exact FVG
10. **SL Placement** — sweep wick extreme ± 0.1% buffer
11. **TP Targeting** — `targets.get_tp_level()`: nearest BSL/SSL ≥ 1.5R; fallback = 2.0R fixed
12. **Partial TP** — 50% closed at 1R via reduce-only GTC limit; SL moved to BE after TP1

**⚠ UNDOCUMENTED FALLBACK:** When `fvg_retest_enabled=false`, if no LTF OB/FVG found, `bot.py` logs "fast move; proceeding to market entry" and enters WITHOUT any LTF zone confirmation. This path is not tested in backtests.

**⚠ STUB IN FOREX RUNNER:** `strategies/smc_sniper.py` Steps 11–12 contain `pass` — FVG retest gate is NOT enforced in the Forex SMCSniper live code despite config `fvg_retest_enabled=true`.

---

## Exit Logic

| Exit Type | Mechanism | File |
|---|---|---|
| TP1 (50% partial) | Reduce-only GTC limit placed at entry | `executor.place_reduce_only_limit()` |
| SL-to-BE after TP1 | Position SL amended when size < 75% of entry_qty | `executor.set_trading_stop(sl=entry_price)` |
| Final TP (50%) | Bybit exchange-native TP attached to entry order | `executor.place_order()` |
| Risk guard halt | Existing position left open; new entries suspended | `risk.trading_allowed()` |
| Emergency close | Manual via CONFIRM-CLOSE-BTC token only | `executor.close_position()` |

**⚠ TP1 detection is polling-based** (checked each 5-min cycle via position size heuristic). Not event-driven. Risk: partial fill or exchange rounding triggers premature SL-to-BE.

---

## Risk Management

| Guard | Threshold | Code Location | Enforced |
|---|---|---|---|
| Daily loss limit | 2% of day-open equity | `smc_bot/risk.py:daily_loss_breached()` | ✅ |
| Max drawdown | 10% from all-time peak | `smc_bot/risk.py:drawdown_breached()` | ✅ |
| Consecutive losses | 2 in a row → halt | `smc_bot/risk.py:consecutive_losses_breached()` | ✅ |
| Minimum order size | 0.001 BTC floor | `smc_bot/risk.py:calc_qty()` | ✅ |
| API fail streak | 5 consecutive fails → alert | `smc_bot/bot.py` | ✅ |
| Weekly loss limit | MISSING | — | ❌ |
| Max concurrent trades | MISSING (assumed 1) | — | ❌ |
| Exposure cap | MISSING | — | ❌ |

---

## Position Sizing

**BTC Bot:** `risk_usd = 100` (fixed dollar risk). `qty = risk_usd / stop_distance`. Falls back to `risk_pct × balance` if `risk_usd` absent. Enforced in `smc_bot/risk.py:calc_qty()`.

**Forex runner:** `risk/manager.py:RiskManager` — per-strategy sizing using `risk_pct_per_trade` (config default 0.5%). Lot calculation uses pip-based stop distance. MT5/MetaAPI lot sizing **not wired** to live order placement (MetaAPI account in DRAFT).

---

## Session Filters

| Filter | Status | Location |
|---|---|---|
| London 08–15 UTC, NY 13–21 UTC | DISABLED (`filter_enabled: false`) | `smc_bot/config.yaml`, `smc_bot/bot.py` |
| SessionTrader Asian box 00–08 UTC | IMPLEMENTED in strategies/ | `strategies/session_trader.py` |
| Forex SMCSniper session gate | MISSING | `strategies/smc_sniper.py` has no session gate |

---

## News Filters

**MISSING.** No news API integration exists anywhere in the codebase. CLAUDE.md §1 notes this as the only genuinely missing SMC compliance item (confirmed by codebase audit). High-impact news events are not blocked.

---

## Broker Integration

| Feature | BTC (Bybit) | Forex (MetaAPI/MT5) |
|---|---|---|
| Auth | pybit SDK, HMAC-SHA256 | metaapi-cloud-sdk 29.x, OAuth token |
| Order placement | ✅ `executor.py` | ⚠ PARTIAL — `brokers/metaapi.py` implemented; account in DRAFT |
| SL modification | ✅ `set_trading_stop()` | ⚠ PARTIAL — method exists, not tested live |
| Position sync | ✅ `get_position()` polling | ⚠ PARTIAL — `get_positions()` exists; account blocked |
| Demo/paper mode | ✅ Bybit demo=True | ❌ MetaAPI account in DRAFT (billing blocked) |
| Error retry | ✅ API fail streak counter | ⚠ PARTIAL — single retry in metaapi.py |

---

## MT5 Integration

MetaAPI cloud SDK (`brokers/metaapi.py`) wraps the MetaAPI REST API. Implementation:
- Connection management with async context manager ✅
- Lot-based position sizing ✅ (method exists)
- `place_order()`, `close_position()`, `get_positions()` ✅ (implemented)
- Reconnect: implicit via MetaAPI SDK; no explicit reconnect loop in `brokers/metaapi.py` ⚠

**Blocker:** MetaAPI account `35e4d9de-1f2a-474e-a4d0-5a03fd4f5e09` in DRAFT status — requires billing top-up at app.metaapi.cloud/billing. All live Forex trading is blocked until resolved.

---

## Telegram Integration

| Feature | Implementation |
|---|---|
| Trade alerts (entry/exit/TP1/BE) | ✅ `smc_bot/alerts.py` — all key events send messages |
| Error alerts | ✅ API fail streak, guard halt, exceptions |
| Bot startup/shutdown | ✅ SIGTERM handler sends alert |
| Circuit breaker alerts | ✅ Guard halt sends 🔴 message |
| Forex runner alerts | ⚠ `runner.py` delegates to per-strategy alert method; not verified implemented in `strategies/smc_sniper.py` |

Credentials: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` from `.env` (gitignored).

---

## Database Usage

**NONE.** State is persisted as flat JSON files:
- `smc_bot_state.json` — BotState (consecutive losses, day-start equity, peak equity, entry metadata)
- `.pionex_monitor_state.json` — separate Pionex system counter (out of scope)

No SQLite, PostgreSQL, or time-series database. **Risk: crash during write corrupts state file → silent reset of risk counters.**

---

## Watchdogs

| Watchdog | Implementation |
|---|---|
| API fail streak counter | ✅ 5 consecutive balance failures → alert + skip cycle |
| SIGTERM handler | ✅ Graceful shutdown + Telegram alert |
| Process auto-restart | ❌ MISSING — no systemd unit, no supervisor config, no cron restart |
| Memory leak detection | ❌ MISSING |
| Log rotation | ❌ MISSING — `logging.FileHandler` with no `RotatingFileHandler` |

---

## Circuit Breakers

| Circuit Breaker | Status |
|---|---|
| Daily loss halt | ✅ Implemented — halts new entries for day |
| Max drawdown kill | ✅ Implemented — permanent halt until process restart |
| Consecutive loss halt | ✅ Implemented — 2 losses → halt |
| LIVE_TRADING=false guard | ✅ Enforced in `executor.py` via env var |
| Weekly loss halt | ❌ MISSING |
| Position size cap | ❌ MISSING (no max notional limit) |

---

## File Map

```
smc_bot/
  bot.py          — Main bot loop: guards → signal → execute (BTC Bybit)
  structure.py    — HTF swing bias detection
  poi.py          — OB/FVG zone detection + get_owned_fvg()
  liquidity.py    — LTF sweep detection
  confirmation.py — CHoCH detection
  risk.py         — Position sizing + 3 trading guards
  executor.py     — Bybit order placement (paper/live)
  data.py         — OHLCV candle fetching from Bybit
  alerts.py       — Telegram send wrapper
  config.yaml     — All constants

strategies/
  smc_sniper.py   — Forex SMC signal chain (⚠ FVG gate is STUB)
  session_trader.py — Asian session box strategy
  base.py         — Abstract strategy interface

brokers/
  base.py         — Abstract broker interface
  metaapi.py      — MetaAPI/MT5 implementation (⚠ account DRAFT)

risk/
  manager.py      — Multi-strategy risk manager

runner.py         — Forex multi-strategy orchestrator

scripts/
  backtest.py     — Phase-0 gate runner (3500+ LOC, comprehensive)
  fetch_data.py   — OHLCV historical data downloader

docs/
  VERDICT_LOG.md  — Trial-by-trial results (29 BTC trials + Forex)
  SIGNAL_SPEC.md  — Locked signal specification
```
