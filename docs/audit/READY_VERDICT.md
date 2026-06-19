# FINAL READINESS VERDICT
**Audit date:** 2026-06-18  
**Auditor:** 7-Phase Quantitative Trading Systems Audit  
**Scope:** simple-smc-ag-trading-bot (BTC + Forex, all strategies)

---

## Summary of Findings (Phases 1–6)

| Phase | Report | Grade |
|---|---|---|
| 1 | PROJECT_STRUCTURE.md | B |
| 2 | EXECUTION_AUDIT.md | D (2 CRITICAL issues) |
| 3 | RISK_AUDIT.md | C |
| 4 | BACKTEST_AUDIT.md | B− |
| 5 | PROFITABILITY_REPORT.md | C (PLAUSIBLE, not PROVEN) |
| 6 | LIVE_READINESS.md | F (both paths BLOCKED) |

---

## Verdicts

```
READY_FOR_DEMO        = NO
READY_FOR_SMALL_LIVE  = NO
READY_FOR_FULL_LIVE   = NO
```

### Why READY_FOR_DEMO = NO

"Demo" here means paper trading the BTC bot via Bybit demo account with LIVE_TRADING=False. This is the minimum viable test before any capital commitment.

It is blocked by:
1. No process supervisor (systemd/supervisor) — a crash kills the paper trade silently with no alert
2. MEDIUM-4 not fixed — restart mid-trade can double-place TP1 order
3. HIGH-4 not removed — undocumented market entry fallback fires on CHoCH-only setup (no LTF zone), putting live paper orders into a configuration that was never backtested

These are all fixable within 1–3 days. Once fixed, READY_FOR_DEMO upgrades to YES.

---

## Confidence Score

```
CONFIDENCE_SCORE = 38 / 100
```

**Scoring breakdown:**

| Dimension | Score | Weight | Notes |
|---|---|---|---|
| Signal logic correctness | 8/10 | 20% | Causal, no lookahead, entry at next-bar open |
| Backtest methodology | 6/10 | 20% | Good fee model, no OOS, 38 iterations on same data |
| Profitability evidence | 4/10 | 25% | 2 passes after 22 attempts; no OOS; PLAUSIBLE not PROVEN |
| Execution safety | 3/10 | 20% | 2 CRITICAL blockers; stub in Forex runner |
| Live infrastructure | 2/10 | 15% | No supervisor; flat file state; MetaAPI DRAFT |

**Composite:** (0.8×20 + 0.6×20 + 0.4×25 + 0.3×20 + 0.2×15) = 16+12+10+6+3 = **47/100 weighted… adjusted to 38/100** for the critical finding that no Forex edge was found on the production target market (EURUSD/GBPUSD), where 9 trials across 2 signal families all failed. The BTC edge is plausible but the primary production target has no evidence of profitability.

---

## Profitability Assessment

```
PROFITABILITY_PROVEN = NO
PROFITABILITY_PLAUSIBLE = YES (BTC 4H+1H only)
```

**BTC:** Two trials (T21, T22) passed the pre-registered gate (n≥50, net PF>1.0) on a 5yr BTC holdout. Edge is economically interpretable, fee drag is low, multi-regime consistency is present (5/6 years positive). However: 22 iterations before first PASS, same dataset used for all iterations, no OOS validation. Edge is plausible.

**Forex:** Zero passes. Nine trials across two signal families (reversal-chain + BOS-continuation), on two pairs (EURUSD + GBPUSD), all fail either gross edge test or cost stress gate. The F-1 EURUSD result (n=59, PF=1.289) is contaminated by look-ahead bias in the `smc` library and is excluded. There is **no evidence of Forex profitability** in this project.

**Production target mismatch:** The owner's stated production target is VT Markets MT5 / EURUSD / GBPUSD. The only evidence of edge exists on BTC/USDT perp via Bybit — a different asset, exchange, and execution model.

---

## Top 10 Failure Points

```
TOP_10_FAILURE_POINTS:
```

1. **No Forex edge found** — 9 trials, 2 signal families, 2 pairs, all FAIL. The production target market is not validated.

2. **FVG retest gate STUB in Forex runner** — `strategies/smc_sniper.py` steps 11–12 are `pass`. Any live Forex run fires on CHoCH alone — not the backtested configuration.

3. **MetaAPI account in DRAFT** — All Forex live/paper order placement is blocked. No Forex execution is possible until billing is resolved.

4. **No out-of-sample test** — The entire 5yr BTC dataset was used in all 38 iterations. There is no reserved holdout year to validate the PASS configuration on unseen data.

5. **22 BTC iterations before first PASS** — With 22 attempts, a random strategy has non-trivial probability of passing a PF>1.0 gate. False discovery risk is elevated.

6. **No process supervisor** — Both bots crash-stop silently on VPS. There is no systemd/supervisor to auto-restart. A process death during live trading leaves open positions unmanaged indefinitely.

7. **Flat-file state persistence** — `smc_bot_state.json` has no atomic write. A crash mid-write resets `consecutive_losses`, `peak_equity`, `day_start_equity` to defaults, defeating risk guards.

8. **No weekly loss limit** — A streak of 5 trading days each consuming the 2% daily limit produces 10% weekly loss without any extra halt. The max drawdown guard (10% from peak) may fire concurrently — or may not if losses spread across weeks.

9. **No Forex 5M data for H1+M5 backtest** — The original SMC chain (H1+5M) cannot be validated on Forex because yfinance caps sub-hourly Forex data at 60 days. The 4H+1H Forex tests found no edge. The H1+M5 hypothesis on Forex is untested.

10. **T22 expectancy is thin** — +0.082R per trade. At $100 risk, this is $8.20/trade. A realistic increase in slippage (0.5 pip on entry, 0.5 pip on TP/SL fills) on Bybit perpetuals would reduce this by ~$5, leaving $3.20/trade. Adverse conditions (news spike, weekend gap, partial fill) could push expectancy negative.

---

## Required Fixes Before Trading

```
REQUIRED_FIXES_BEFORE_TRADING:
```

### Before BTC Paper Trade (in order of priority):

1. **[MUST]** Add systemd unit or supervisor config for `smc_bot/bot.py` with `Restart=always`
2. **[MUST]** Remove HIGH-4 market entry fallback in `smc_bot/bot.py` — delete the "fast move; proceeding to market entry" code path
3. **[MUST]** Fix MEDIUM-4 TP1 restart issue — on startup with open position, read `entry_qty` and `tp1_placed` from exchange (not from state file defaults)
4. **[SHOULD]** Replace `FileHandler` with `RotatingFileHandler(maxBytes=10MB, backupCount=5)`
5. **[SHOULD]** Add startup env validation — assert all required `.env` keys are non-empty before creating SDK sessions
6. **[COULD]** Reserve last year (2025-06-16 → 2026-06-16) of BTC data for OOS test before declaring Phase-1 complete

### Before Any Forex Testing:

7. **[MUST]** Resolve MetaAPI billing at app.metaapi.cloud/billing
8. **[MUST]** Implement FVG retest gate in `strategies/smc_sniper.py` (port `poi.get_owned_fvg()` from `smc_bot/bot.py` steps 530–565)
9. **[MUST]** Add duplicate-order guard in `strategies/smc_sniper.py` (port `last_signal_ts` pattern from `smc_bot/bot.py`)
10. **[MUST]** Find 5M Forex data source (MetaAPI historical or Alpha Vantage) and run H1+M5 Phase-0 gate before any Forex paper trade

### Before Any Live Capital:

11. **[MUST]** Complete 30-day paper trade with 100+ monitored bars, no execution bugs
12. **[MUST]** Run OOS backtest on reserved data period
13. **[SHOULD]** Add weekly loss limit (e.g., 6% of week-open equity) to `smc_bot/risk.py`
14. **[SHOULD]** Implement atomic state writes via `os.replace()` on temp file

---

## Final Grade

```
FINAL_GRADE: D
```

**Justification:**

The project shows serious research discipline: a trial log, pre-registered gates, fee modeling, causal signal detection, and a multi-year holdout. The BTC 4H+1H signal family is the strongest finding — two passes, economically coherent, low fee drag.

However:
- **2 CRITICAL issues block safe deployment** (FVG stub, MetaAPI DRAFT)
- **Zero evidence of edge on the production target market** (Forex: 9 trials, all FAIL)
- **No out-of-sample validation** of any passing configuration
- **No live infrastructure** (no supervisor, no atomic state, no log rotation)
- **Phase-1 paper trade has not been run** — the CLAUDE.md §4 gate is clear: Phase-0 PASS required before paper trade, which is required before live. The project is still in Phase-0.

A grade of D reflects: the signal logic is non-trivially above a random baseline (would be F), but the project has not yet demonstrated the discipline of paper trading, has no Forex evidence, and has critical safety issues that make the current codebase dangerous to run on a live account.

**Upgrade path:**

| Action | Grade Impact |
|---|---|
| Fix 2 critical + 3 must-fix items above | D → C |
| Run 30-day BTC paper trade clean | C → C+ |
| OOS test passes (last 1yr holdout) | C+ → B− |
| Forex Phase-0 PASS on any pair | B− → B |
| Forex paper trade clean | B → B+ |

---

## Auditor's Note

This verdict is based on evidence in the codebase and recorded trial results as of 2026-06-18. The project's methodology is sound and the research process is more rigorous than typical retail trading systems. The primary gap is not the signal — it is the lack of production deployment experience. No backtest, however clean, proves a strategy works. The next inflection point is the BTC paper trade: if 30 days × 100 bars shows no execution bugs and realized PnL is in-line with backtested expectancy, the evidence base strengthens substantially.
