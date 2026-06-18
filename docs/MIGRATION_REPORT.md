# Multi-Strategy Architecture — Migration Report

**Date:** 2026-06-18  
**Branch:** `claude/forex-phase0-runner`  
**Status:** COMPLETE — 0 regressions on existing 137 tests; 21 new tests added.

---

## What Changed

### New Files Created

| File | Purpose |
|------|---------|
| `strategies/__init__.py` | Package entry point — exports BaseStrategy, TradeSignal, SMCSniper, SessionTrader |
| `strategies/base.py` | Abstract base: TradeSignal dataclass + BaseStrategy ABC |
| `strategies/smc_sniper.py` | Strategy 1 — SMC Sniper (4H→1H chain, wraps existing smc_bot/ without modification) |
| `strategies/session_trader.py` | Strategy 2 — Session Trader (London/NY session range, independent chain) |
| `strategies/config.yaml` | All strategy params, risk limits, broker settings, service mode flags |
| `brokers/__init__.py` | Package entry point |
| `brokers/base.py` | Abstract broker interface (BaseBroker, OrderResult) |
| `brokers/metaapi.py` | MetaAPI cloud SDK adapter for VT Markets MT5 Demo (async) |
| `risk/__init__.py` | Package entry point |
| `risk/manager.py` | Per-strategy independent risk manager (daily PnL, drawdown, consec loss) |
| `reporting/__init__.py` | Package entry point |
| `reporting/trade_log.py` | Append-only trade log (CSV + JSONL per strategy) |
| `reporting/report_generator.py` | Performance summary generator (JSON + HTML) |
| `runner.py` | Async multi-strategy loop — entry point for production |
| `reports/smc/.gitkeep` | Output directory for SMC Sniper trade logs/reports |
| `reports/session/.gitkeep` | Output directory for Session Trader trade logs/reports |
| `tests/test_strategy_isolation.py` | Risk isolation tests (7 assertions) |
| `tests/test_session_trader.py` | SessionTrader unit tests (14 assertions) |

### Modified Files

| File | Change |
|------|--------|
| `dashboard/server.py` | Added forex chart sections (SMC + Session tabs with EURUSD/GBPUSD sub-tabs) |

### Files NOT Modified (hard rule)

- `smc_bot/structure.py`, `smc_bot/poi.py`, `smc_bot/liquidity.py`, `smc_bot/confirmation.py`
- `smc_bot/risk.py`, `smc_bot/executor.py`, `smc_bot/data.py`, `smc_bot/bot.py`
- `smc_bot/config.yaml` (BTC/USDT paper trade config — untouched)
- `scripts/backtest.py`, `scripts/fetch_data.py`
- All `_archive/` files

---

## Architecture Summary

```
runner.py
  ├─ strategies/smc_sniper.py    (SMC_SNIPER — magic EURUSD=11001, GBPUSD=11002)
  │    └─ smc_bot/ modules       (unchanged — read-only import)
  ├─ strategies/session_trader.py (SESSION_TRADER — magic EURUSD=12001, GBPUSD=12002)
  ├─ brokers/metaapi.py          (VT Markets MT5 via MetaAPI cloud SDK)
  ├─ risk/manager.py             (per-strategy isolated risk state)
  └─ reporting/trade_log.py      (reports/smc/ | reports/session/)
```

---

## Safety Rules (enforced in code, not bypassable)

1. `LIVE_TRADING=false` by default — `runner.py` reads from `.env`, never sets it.
2. Every order requires a `TradeSignal` from a strategy — no manual order paths.
3. Risk guards (`trading_allowed()`) checked before every signal is routed to broker.
4. MetaApiBroker dry-runs all orders when `live_trading=False` — logs intent, returns fake order ID.
5. Magic numbers are unique per (strategy, symbol) — MT5 positions always tagged.

---

## MT5 / MetaAPI Dependencies

- `metaapi_cloud_sdk` must be installed: `pip install metaapi_cloud_sdk`
- `.env` must have `METAAPI_TOKEN` and `METAAPI_ACCOUNT_ID`
- Existing `tests/test_mt5_account.py` validates connectivity (requires live credentials)

---

## Config Changes Required

Add to `.env`:
```
METAAPI_TOKEN=<your MetaAPI token>
METAAPI_ACCOUNT_ID=<your VT Markets account GUID>
# LIVE_TRADING=false  (leave false until Phase-0 + 30-day paper pass)
```

---

## Test Results

| Suite | Before | After |
|-------|--------|-------|
| Existing tests | 137 passed, 1 skipped | 137 passed, 1 skipped ✓ |
| New tests | — | 21 passed |
| **Total** | **137** | **158 passed, 1 skipped** |

---

## Phase-0 Status

| Strategy | Status | Gate Criteria |
|----------|--------|---------------|
| SMC_SNIPER (EURUSD/GBPUSD) | NOT YET RUN | n≥50 AND net PF>1.0 |
| SESSION_TRADER (EURUSD/GBPUSD) | NOT YET RUN | n≥50 AND net PF>1.0 |

Run `scripts/backtest.py` for each strategy/symbol pair before proceeding to Phase-1.
