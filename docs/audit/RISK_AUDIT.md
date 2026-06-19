# RISK MANAGEMENT AUDIT
**Audit date:** 2026-06-18  
**Files audited:** smc_bot/risk.py, risk/manager.py, smc_bot/config.yaml, strategies/config.yaml, smc_bot/bot.py

---

## Risk Controls Table

| Control | Status | Value | Code Enforced | Notes |
|---|---|---|---|---|
| Max risk per trade | ✅ EXISTS | $100 fixed USD | ✅ `calc_qty()` | Fixed USD risk, not pct of equity |
| Daily loss limit | ✅ EXISTS | 2% of day-open equity | ✅ `daily_loss_breached()` | Resets at UTC midnight via `update_day_start()` |
| Max drawdown (peak) | ✅ EXISTS | 10% from peak | ✅ `drawdown_breached()` | Permanent halt until process restart |
| Consecutive loss limit | ✅ EXISTS | 2 in a row | ✅ `consecutive_losses_breached()` | Resets to 0 on any win |
| Min order quantity | ✅ EXISTS | 0.001 BTC | ✅ `calc_qty()` — returns 0 if below | Trade skipped, not rounded up |
| LIVE_TRADING guard | ✅ EXISTS | env var | ✅ `executor._live()` on every call | Default false; owner sets manually |
| Signal-only mode | ✅ EXISTS | `signal_only_mode` | ✅ `bot.py` line 659 | Logs intent without placing orders |
| Weekly loss limit | ❌ MISSING | — | ❌ | Not in risk.py, config, or bot.py |
| Max concurrent trades | ❌ MISSING | — | ❌ | Assumed 1 by BotState; no hard cap |
| Notional exposure cap | ❌ MISSING | — | ❌ | No max position size in USD/BTC |
| Leverage cap | PARTIAL | 1.0× in config | ✅ Bybit contract default; not API-enforced | |
| Equity protection floor | PARTIAL | 10% DD kill switch | ✅ | But no minimum-balance pre-trade check |

---

## BTC Bot Risk Flow (smc_bot/)

```
Each cycle:
  1. get_balance() → current equity
  2. risk.trading_allowed(equity, peak, day_start, consecutive_losses, ...)
     ├── drawdown_breached() → HALT (permanent)
     ├── daily_loss_breached() → HALT (until midnight UTC)
     └── consecutive_losses_breached() → HALT (until manual restart or win)
  3. If HALT → alert sent; return (no new entry)
  4. get_position() → check if already in trade
  5. If in trade: check TP1 fill heuristic; amend SL-to-BE if needed
  6. If flat: run full 15-step signal chain
  7. If signal: calc_qty() → place_order() or log intent (signal_only)
```

**State persistence:** JSON flat file `smc_bot_state.json`. Crash during write resets all counters. *(See EXECUTION_AUDIT.md CRITICAL-2 for fix.)*

---

## Forex Multi-Strategy Risk (risk/manager.py)

`RiskManager` has per-strategy independent state:

| Control | Config Key | Default |
|---|---|---|
| Daily loss per strategy | `max_daily_loss_pct` | 2% |
| Max drawdown per strategy | `max_drawdown_pct` | 10% |
| Max consecutive losses | `max_consecutive_losses` | 3 |
| Max concurrent positions (GLOBAL) | Not implemented | ❌ MISSING |

**⚠ Risk isolation gap:** EURUSD SMCSniper and EURUSD SessionTrader both trade the same pair with independent risk budgets. Combined daily loss could reach 4% (2× 2%) before any halt fires. This is not documented or guarded.

---

## Risk Gaps

1. **No weekly loss limit** — a streak of 5 bad days within the daily limit still accumulates a catastrophic weekly loss.
2. **No max concurrent positions** — multi-strategy runner can double-up on the same instrument.
3. **No notional exposure cap** — `risk_usd=100` is small enough to be safe at current account size ($100-300), but there is no hard ceiling if `risk_usd` is misconfigured to a large value.
4. **Equity floor not checked pre-trade** — if account drops below the minimum to place a valid order, `calc_qty()` returns 0 (correct), but there is no alert or halt on sustained near-zero balance.
5. **Daily loss counter uses live equity** — in a gapping market, equity can drop 3% in one candle between polls. The daily loss check fires one cycle late.
6. **Consecutive loss counter survives only in BotState** — if the JSON file is corrupt or absent on restart, counter resets to 0, defeating the consecutive-loss guard entirely.

---

## Risk Grade: **C**

**Rationale:**  
The three core BTC guards (daily loss, max DD, consecutive losses) are correctly implemented and code-enforced. The LIVE_TRADING=false default is solid. However:  
- Weekly loss limit is absent (significant gap for extended drawdown periods).  
- Multi-strategy concurrent exposure is not consolidated.  
- State persistence is fragile (flat file, no atomic write).  
- The Forex runner's risk isolation per strategy can double actual exposure on correlated pairs.  

Acceptable for small BTC paper trading. **Not acceptable for Forex live or multi-strategy deployment** without fixing the concurrent position cap and state persistence.
