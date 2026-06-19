# TRADING SYSTEM READINESS VERDICT
**Project:** simple-smc-ag-trading-bot
**Audit date:** 2026-06-18
**Auditor:** Senior Quant Auditor — evidence-only, no speculation
**Data sources:** VERDICT_LOG.md, data/trial22_trades.csv, data/trial21_trades.csv,
smc_bot/config.yaml, smc_bot/bot.py, scripts/backtest.py, smc_bot_state.json,
`systemctl status smc-bot.service`, live OOS backtest runs (2021–2023 and 2024–2026 windows)

---

## Phase-7 Checklist

| # | Check | Status | Evidence |
|---|---|---|---|
| 1 | Phase-0 gate passed (n≥50, net PF>1.0) | PARTIAL | T21 PASS (n=60, net PF=1.38), T22 PASS (n=67, net PF=1.20) — but see Finding F-1 |
| 2 | Live bot config matches the validated trial | FAIL | config.yaml: htf=1h, ltf=5m (1H+5M chain). T21/T22 used htf=4h, ltf=1h (4H+1H). Bot is running the DEAD chain. |
| 3 | No lookahead bias in backtest | PASS | Entry uses open[i+1]; HTF data aligned causally; confirmed in backtest.py line 1016-1024 |
| 4 | Fees applied correctly | PASS | Bybit taker 0.06%/side = 0.12% RT applied to all PASS results; avg fee_r=0.074–0.075R |
| 5 | No slippage model | FAIL | Zero slippage assumed throughout all backtest variants; none of 28 trials model it |
| 6 | No walk-forward / true OOS split | FAIL | All 28 BTC trials ran on same full 2021–2026 dataset; no segment withheld before first trial |
| 7 | Statistical significance of edge | FAIL | T22: t-stat=0.67, p>0.50; not significant at any standard threshold (10%, 5%, 1%) |
| 8 | Stable across time periods | FAIL | T22 2021–2023: net PF=0.56 (LOSING, n=31). T22 2024–2026: net PF=2.36 (profitable, n=36). Regime-dependent. |
| 9 | Adequate sample size | FAIL | T22: n=67 over 5 years = 13.4 trades/year. Monte Carlo P(negative terminal)=24.9% at n=67. |
| 10 | No multiple-testing / data snooping | FAIL | 28 BTC trials on same fixed dataset before 2 passed (7.1% PASS rate). No FDR correction. Expected false discoveries at α=0.05: ~1.4 of 28. |
| 11 | Execution bugs would not corrupt live trading | FAIL | EXEC-001: get_position() swallows exceptions → duplicate-position risk. EXEC-004: IOC fill not confirmed before writing state. EXEC-005: entry_price from pre-order candle close, not actual fill. |
| 12 | Risk state persisted across process restarts | FAIL | risk/manager.py kill switch is in-memory only; restart resets all consecutive-loss and daily-halt counters |
| 13 | Process watchdog covers hung processes | FAIL | systemd Restart=on-failure only; no WatchdogSec; hung process is invisible |
| 14 | Disk space adequate | PASS | Root at 65% (14G free) — resolved from audit-reported 92% |
| 15 | LIVE_TRADING guard enforced | PASS | .env: LIVE_TRADING=false; signal_only_mode=false; BaseBroker._assert_live() raises RuntimeError |
| 16 | No news/event filter | FAIL | No implementation anywhere; FOMC/NFP/CPI false-positive risk is unmitigated |

---

## Readiness Matrix

```
READY_FOR_DEMO       = NO
  Reason: Live bot config (htf=1h, ltf=5m) does not match the validated trial config
  (htf=4h, ltf=1h). The 1H+5M chain has no gross edge (T4: gross PF=0.94, T13:
  gross PF=0.75). The bot is running a dead strategy on demo right now. Three
  execution-layer bugs (EXEC-001, EXEC-004, EXEC-005) would corrupt state even in
  paper mode. These must be fixed before demo results carry any meaning.

READY_FOR_SMALL_LIVE = NO
  Reason: In addition to the config mismatch and execution bugs: the edge in T21/T22
  is not statistically significant (t=0.67, p>0.5); the early half of the sample
  (2021–2023, n=31) has net PF=0.56; 2 of 28 variants passed on the same dataset
  with no multiple-testing correction; and zero slippage is assumed in all backtests.
  No real capital should be committed.

READY_FOR_FULL_LIVE  = NO
  Reason: All reasons above plus: no true OOS validation (all 28 trials on the same
  window), no walk-forward, no Monte Carlo stress at production lot sizes, and the
  consecutive-loss kill switch resets on process restart.

CONFIDENCE_SCORE = 18/100
  Breakdown:
  - Signal quality (edge existence):    12/30  (t-stat 0.67; 2021-2023 losing; regime-dependent)
  - Backtest integrity:                  8/25  (no lookahead ✓, fees ✓; no OOS, no slippage, no MTC)
  - Execution integrity:                 4/20  (LIVE guard ✓; 3 critical execution bugs open)
  - Infrastructure:                      8/15  (systemd ✓; disk ok; no watchdog; kill switch not persisted)
  - Config correctness:                  0/10  (live bot runs wrong chain — 0 marks)
  Total:                                32/100 → rescaled to 18/100 after config-mismatch penalty

PROFITABILITY_PROVEN = NO
  Evidence:
  - T21 (n=60, net PF=1.38): 2 PASS results from 28 configs on the same dataset
  - T22 (n=67, net PF=1.20): t-statistic = 0.67 (p > 0.50); P(losing terminal equity) = 24.9%
  - Early-period sub-sample (2021–2023): net PF = 0.56 — below 1.0; the strategy lost money
    in the first half of its own validation window
  - True full-win rate (TP1+TP only): 16/67 = 23.9%; partial half-wins (TP1+BE) inflate
    the reported 61.2% win rate
  - OOS run (2024–2026 window only, n=36): net PF = 2.36 — promising but n<50 gate fails
    on this segment alone, and this period coincides with 2024–2025 BTC bull run
  - 28 configurations tried on one dataset before 2 passed is consistent with random
    search for the best-fitting noise; expected false positives under α=0.05 ≈ 1.4
  Conclusion: The PASSes are promising hypotheses. They are not proven profitability.
```

---

## Top 10 Failure Points

**1. LIVE BOT IS RUNNING THE WRONG STRATEGY [CRITICAL]**
The active smc-bot.service (running since 2026-06-17 18:44 UTC) is configured with
htf=1h, ltf=5m (the 1H+5M chain). That chain FAILED Phase-0: T4 gross PF=0.94,
T13 gross PF=0.75 — no edge before fees. The validated T21/T22 configs used
htf=4h, ltf=1h. Any paper or demo results generated by the current bot are useless
as Phase-1 evidence because the signal is not the validated one.
Source: config.yaml lines 2-3; VERDICT_LOG T4, T13.

**2. NO STATISTICAL EVIDENCE OF EDGE [CRITICAL]**
T22: t-statistic = 0.67 on n=67 trades. This is below the p=0.50 threshold —
the observed mean return is indistinguishable from zero at any conventional
significance level. With 28 configs tried on the same dataset, the expected number
of false discoveries at α=0.05 is ≈1.4. The two PASSes (T21, T22) could both be
false discoveries. No Bonferroni or FDR correction has been applied.
Source: computed directly from data/trial22_trades.csv.

**3. SEVERE REGIME DEPENDENCY — LOSING IN FIRST HALF [HIGH]**
T22 2021–2023 (31 trades, first 46% of sample): net PF = 0.5616, mean net_r = −0.2518R.
T22 2024–2026 (36 trades, latest 54% of sample): net PF = 2.3571, mean net_r = +0.3690R.
The entire 5-year positive expectancy is driven by the most recent BTC bull run.
The strategy lost money in the first 2.5 years it could have been traded.
The first-half / second-half split is a necessary (not sufficient) stability test
that this system fails.
Source: computed from data/trial22_trades.csv by timestamp order.

**4. NO WALK-FORWARD / TRUE OOS HOLDOUT [HIGH]**
All 28 BTC trial variants ran on BTCUSDT 2021-06-17 to 2026-06-16 (the full available
window). No segment was withheld before trial 1 began. "Holdout" in the VERDICT_LOG
refers to the entire backtest window — there is no unseen data. Running the T22
exact config on 2021–2023 alone produces net PF=0.56 (fails gate). This means
the 5-year PASS result depends on the profitable 2024–2026 tail which was visible
during parameter selection.
Source: live backtest runs performed during this audit.

**5. EXEC-001: get_position() EXCEPTION SWALLOWING [CRITICAL CODE BUG]**
If the Bybit API returns an error, get_position() catches the exception and returns
"no position." The bot interprets this as a clean slate, resets state, and can place
a new entry while an existing position is still open on the exchange — creating an
unintended double position with no SL on the first leg.
Source: EXECUTION_AUDIT.md EXEC-001.

**6. PARTIAL-TP WIN RATE INFLATION [HIGH — MISREPRESENTS EDGE]**
T22 reports 61.2% win rate. This counts TP1+BE outcomes (25 trades closed at 0.5R
then stopped at breakeven) as "wins." The true full-win rate (TP1+TP, price reaches
2R target) is 16/67 = 23.9%. The SL hit rate is 26/67 = 38.8%. A system that
reaches its full target only 23.9% of the time with a 2R:1R payoff ratio has
marginal positive expectancy — and that positive is not statistically significant
(t=0.67).
Source: computed from data/trial22_trades.csv reason column.

**7. FOREX RUNNER FVG RETEST GATE NOT IMPLEMENTED [HIGH — SPEC VIOLATION]**
strategies/smc_sniper.py Steps 11–12 are bare `pass` statements. The FVG retest
gate that is enforced in bot.py via poi.get_owned_fvg() is silently absent in the
Forex path despite fvg_retest_enabled=true in strategies/config.yaml. Forex signals
fire without LTF zone confirmation. This makes any Forex backtest parity claim false,
though the Forex track is now retired.
Source: EXECUTION_AUDIT.md EXEC-006.

**8. RISK KILL SWITCH NOT PERSISTED [HIGH]**
risk/manager.py maintains killed=True in memory only. A process crash or systemd
restart resets the permanent drawdown kill switch. On the same calendar day, the
restarted runner will trade again as if the kill switch never fired, potentially
breaching the 10% drawdown limit further before reaching the next day boundary.
Source: RISK_AUDIT.md.

**9. ZERO SLIPPAGE IN ALL 28 BACKTESTS [HIGH]**
No slippage model exists in scripts/backtest.py. At 4H+1H resolution with BTC
entries at next-bar market open, slippage of 0.02–0.05% per side is realistic
(market impact at open, bid-ask spread). At an average stop distance of ~1.5% and
0.074R average fee, adding 0.03% slippage per side would increase the cost burden
by ~0.04R per trade — roughly halving the T22 expectancy of +0.082R/trade.
Source: grep confirms no SLIPPAGE constant or model in backtest.py.

**10. asyncio RACE CONDITION IN FOREX RUNNER [HIGH — ASYNC BUG]**
runner.py uses asyncio.gather() to run two strategies concurrently over a shared
RiskManager and broker connection. Concurrent balance-check/order-placement
sequences defeat all per-strategy isolation: Strategy A can pass the balance check,
Strategy B places an order reducing balance, then Strategy A places its order
exceeding the combined risk limit. No mutex or sequential guard exists.
Source: EXECUTION_AUDIT.md EXEC-007.

---

## Required Fixes Before Demo Trading

These are the minimum-viable fixes to make demo results meaningful as Phase-1 evidence.

**Fix 1 (BLOCKING): Correct the live bot config.**
Change config.yaml: exchange.htf from "1h" to "4h", exchange.ltf from "5m" to "1h".
Update data limits: htf_limit from 200 to 500 (covers bias lookback on 4H), ltf_limit
from 300 to 200 (1H candles). Update comment to remove "Trial 25: 1H+5M chain."
Until this is done, the bot generates signals from a signal family with no gross edge.

**Fix 2 (BLOCKING): Fix get_position() exception handling (EXEC-001).**
Replace bare exception→"no position" with explicit exception propagation or a
distinct error state. Do not allow the bot to reset `was_in_position` on an API
error — skip the cycle instead.

**Fix 3 (BLOCKING): Confirm IOC fill before writing state (EXEC-004).**
After place_order(), call get_order() to verify the fill. Only write
was_in_position=True and set entry state if the order status is "Filled."
An unfilled or partially filled order must not advance the state machine.

**Fix 4 (HIGH): Use actual fill price for entry_price (EXEC-005).**
After confirmed fill, read the average fill price from the order response and use
that for entry_price in BotState. Do not use the candle close price at signal time.

**Fix 5 (HIGH): Persist the drawdown kill switch to disk.**
Write killed=True to a file (e.g., .risk_killed) when the 10% drawdown threshold
is crossed. On restart, load this file before accepting new signals. Clear only on
explicit owner reset.

**Fix 6 (MEDIUM): Add WatchdogSec to smc-bot.service.**
Add `WatchdogSec=120` and `NotifyAccess=main` to the service unit. Add
`systemd.daemon.notify("WATCHDOG=1")` in the main poll loop. This catches hung
processes that do not crash but stop polling.

---

## Evidence Summary

**What exists:**
- 28 BTC trial variants logged with n, gross PF, net PF, win%, exit reasons
- Bybit taker fee (0.12% RT) correctly applied in all backtest variants
- Next-bar-open entry timing (no lookahead)
- 2 Phase-0 PASSes: T21 (n=60, net PF=1.38) and T22 (n=67, net PF=1.20)
- Trade-level CSV for T21 and T22 (all exit reasons and timestamps)
- Active systemd service (running since 2026-06-17 18:44 UTC, no crashes)
- LIVE_TRADING=false enforced in .env and code
- Telegram alerting wired and tested
- 99 unit tests covering signal chain, risk guards, state persistence

**What is claimed but not supported by evidence:**
- "Phase-0 PASS" implies readiness for Phase-1 paper trading — the live bot runs
  the wrong timeframe chain, making all current paper-mode observations void
- "5yr holdout" implies OOS validation — all 28 parameter variants saw the full
  window; there is no withheld segment
- 61.2% win rate — inflated by TP1+BE half-wins; full-win rate is 23.9%
- Positive expectancy — t-stat=0.67 is not statistically distinguishable from zero
- Consistent edge — net PF=0.56 in the first 31 trades (2021–2023) shows the
  strategy is losing in bear/transition regimes

**What does not exist:**
- Walk-forward or true OOS backtest
- Slippage model
- Multiple-testing correction across 28 configs
- Statistical significance test (t-test, permutation test) in the backtest output
- Monte Carlo analysis in the backtest pipeline (computed ad hoc in this audit)
- News/event filter
- WebSocket reconnect logic for MetaAPI
- asyncio mutex in runner.py

---

## Final Grade

```
FINAL_GRADE: D
```

The system has a sound structural skeleton — modular signal chain, layered risk
guards, correct fee accounting, no lookahead bias, and working LIVE_TRADING
enforcement. However, the fundamental requirement for trading — a demonstrated,
statistically credible edge running on the correct configuration — is not met on
any of the three dimensions that matter. The live bot is running the 1H+5M signal
chain (T4/T13, gross PF < 1.0, no edge before fees), not the 4H+1H chain that
passed (T21/T22). The two Phase-0 PASSes are not statistically significant (t=0.67)
and are heavily regime-dependent (net PF=0.56 in the first 2.5 years of the sample
vs. net PF=2.36 in the 2024-2026 bull run). Across 28 parameter configurations
tested on the same fixed dataset with no multiple-testing correction, the expected
number of false discoveries exceeds 1. Three critical execution bugs (EXEC-001,
EXEC-004, EXEC-005) would corrupt state even in paper mode, meaning current demo
results cannot serve as valid Phase-1 evidence. The grade is D rather than F
because the codebase is professionally structured, LIVE_TRADING is correctly
gated, and there is a non-zero probability that the 4H+1H SMC signal has real
edge in bull regimes — but none of that probability justifies advancing phase
status until the config is corrected, the bugs are fixed, and a genuine OOS window
confirms the edge on data that was not accessible during parameter selection.
