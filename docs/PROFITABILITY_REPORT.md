# PROFITABILITY EVIDENCE REPORT
## Simple SMC AG Trading Bot — Quant Audit
**Date of audit:** 2026-06-18
**Auditor role:** Senior quant auditor (read-only; no numbers invented)
**Data sources:** `docs/VERDICT_LOG.md` + backtest integrity scan (provided)
**Policy:** All figures are transcribed directly from the verdict log. No extrapolation.

---

## 1. Best Passing Trial — Trial 22

**Config:** 4H+1H chain, FVG+OB entries, mitigation OFF, partial TP/BE (50% at 1R → move SL to BE, remainder to 2R), side=both, 5yr holdout 2021-06-17 → 2026-06-16.

| Metric | Value | Source |
|---|---|---|
| Trade count (n) | 67 | VERDICT_LOG T22 |
| Win rate | 61.2% | VERDICT_LOG T22 |
| Gross PF | 1.4038 | VERDICT_LOG T22 |
| Net PF | 1.1986 | VERDICT_LOG T22 |
| Avg fee (R/trade) | 0.0749 | VERDICT_LOG T22 |
| Expectancy | +0.082 R/trade | VERDICT_LOG T22 |
| Max Drawdown | 11.25 R | VERDICT_LOG T22 |

**Note on win rate:** The 61.2% figure counts partial-TP half-closes (TP1 hit + BE trail = scored as win). Full-win trades reaching 2R are a subset. This inflates reported win rate relative to a single-exit measure.

**Year-by-year breakdown (T22):**

| Year | Trades | Win% |
|---|---|---|
| 2021 | 9 | 33% |
| 2022 | 8 | 50% |
| 2023 | 14 | 50% |
| 2024 | 7 | 71% |
| 2025 | 22 | 73% |
| 2026 (partial) | 7 | 86% |

**Drag note:** 2021 (n=9, 33% WR) and 2022–2023 are weaker. The net PF=0.65 in 2023 is a sub-PF year noted in the T22 entry. Performance skews toward recent years.

---

## 2. Trial History Table

All entries from `VERDICT_LOG.md`, sorted by trial number. Trials without numeric n or PF are recorded as noted.

| Trial | Date | TF | n | Gross PF | Avg fee (R) | Net PF | Win% | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | 2026-06-15 | 15m | 1570 | 1.0233 | 0.313 | 0.6834 | 29.0% | FAIL |
| 2 | 2026-06-15 | 15m | 1333 | 0.9932 | 0.348 | 0.6399 | 28.4% | FAIL |
| 3 | 2026-06-15 | 1H+5M | TBD | — | — | — | — | ABANDONED |
| 4 | 2026-06-15 | 1H+5M | 301 | 0.9366 | 0.2498 | 0.6567 | 31.9% | FAIL |
| 5 | 2026-06-15 | 4H+1H | 26 | 2.3333 | 0.0792 | 2.0833 | 53.8% | OVERFILTERED (n<50) |
| 5X | 2026-06-15 | 4H+1H | 45 | 1.1034 | 0.0704 | 0.9946 | 35.6% | FAIL |
| 6 | 2026-06-16 | 4H+1H | 85 | 1.0909 | 0.0633 | 0.9935 | 35.3% | INVALIDATED (bug) |
| 7 | 2026-06-16 | 4H+1H | 1 | inf | 0.0424 | inf | 100% | VALID but n=1 |
| 8 | 2026-06-16 | 4H+1H | 47 | 1.7600 | 0.0724 | 1.5662 | 46.8% | FAIL (n=47, 3 short) |
| 9 | 2026-06-16 | 4H+1H | see matrix | — | — | — | — | SENSITIVITY STUDY |
| 10 | 2026-06-16 | 4H+1H | see matrix | — | — | — | — | SENSITIVITY STUDY |
| 11 | 2026-06-16 | 4H+1H | 26 | 1.7143 | 0.0924 | 1.5042 | 46.1% | FAIL (n<50) |
| 12 | 2026-06-16 | 4H+1H | 26 | 1.7143 | 0.0924 | 1.5042 | 46.1% | FAIL (n<50) |
| 13 | 2026-06-16 | 1H+5M | 194 | 0.7531 | 0.2689 | 0.5186 | 25.8% | FAIL |
| 14 | 2026-06-16 | 1H+5M | 188 | 0.8471 | 0.2679 | 0.6430 | 9.6% | FAIL |
| 15 | 2026-06-16 | 1H+5M | 81 | 0.5312 | 0.2536 | 0.3675 | 21.0% | FAIL |
| 16 | 2026-06-16 | 1H+5M | 83 | 0.6771 | 0.2528 | 0.3900 | 42.2% | FAIL |
| 17 | 2026-06-16 | 1H+5M | 56 | 0.5882 | 0.2411 | 0.3424 | 39.3% | FAIL |
| 18 | 2026-06-16 | 1H+5M | 44 | 0.6923 | 0.1939 | 0.4576 | 40.9% | FAIL (n<50) |
| 19 | 2026-06-16 | 4H+1H | 26 | 1.7143 | 0.0924 | 1.5042 | 46.1% | FAIL (n<50) |
| 20 | 2026-06-16 | 4H+1H | 48 | 1.8400 | 0.0821 | 1.6367 | 47.9% | FAIL (n=48, 2 short) |
| **21** | **2026-06-16** | **4H+1H** | **60** | **1.5294** | **0.0739** | **1.3754** | **43.3%** | **PASS** |
| **22** | **2026-06-17** | **4H+1H** | **67** | **1.4038** | **0.0749** | **1.1986** | **61.2%** | **PASS** |
| 23 | 2026-06-17 | 4H+1H | 48 | 1.5556 | 0.0707 | 1.3450 | 62.5% | FAIL (n=48, 2 short) |
| 24 | 2026-06-17 | 4H+1H | 18 | 0.9444 | 0.0468 | 0.8575 | 50.0% | FAIL |
| 25 | 2026-06-17 | 4H+1H | 921 | 0.5091 | 0.3696 | 0.3279 | 13.0% | FAIL |
| 26 | 2026-06-17 | 4H+1H | 66 | 1.5208 | 0.3642 | 0.9716 | 34.9% | FAIL |
| 27 | 2026-06-18 | 4H+1H | 685 | 0.5100 | — | 0.3700 | ~24% | FAIL |
| 28 | 2026-06-18 | 4H+1H | 635 | 0.5600 | — | 0.4300 | ~26% | FAIL |
| 29-EUR | 2026-06-18 | 4H+1H | 335 | 0.8270 | 0.0792 | 0.7359 | 29.2% | FAIL |
| 29-GBP | 2026-06-18 | 4H+1H | 339 | 1.1535 | 0.0759 | 1.0315 | 36.6% | FAIL at 2x stress |
| ST-1 | 2026-06-18 | 4H+1H | 72 | 0.2250 | — | 0.2250 | ~11–12% | FAIL |
| TRIAL-S | 2026-06-16 | 5M | — | — | — | — | — | FEE_DEAD — halted pre-backtest |

*Trials 9 and 10 are sensitivity matrices, not independent signal configurations; individual matrix rows are recorded in VERDICT_LOG Section "Sensitivity Study" and "Trial 10 Matrix".*

---

## 3. Metrics Derivable from Trial 22

Only numbers that appear directly in VERDICT_LOG are reported. Derived values use only the data present.

**Profit Factor:** 1.1986 (net, after fees)

**Expectancy:** +0.082 R/trade (stated in VERDICT_LOG T22)

**Win Rate:** 61.2%
- Caution: partial-TP structure means a half-close at 1R counts as a win. True full-2R win rate is lower and not separately reported in VERDICT_LOG.

**Avg Fee R:** 0.0749 R/trade
- This is 3.4x cheaper than the 1H+5M chain (0.25R), consistent with larger stop distances on 4H entries.

**Max Drawdown R:** 11.25 R
- Context: T21 (single TP) had max DD = 9.39 R. Adding partial TP increased max DD to 11.25 R — the partial-close reduced average gain without symmetrically reducing loss strings.

**Avg win / Avg loss (R):**
- NOT directly reported. Cannot compute from available data.

**Sharpe Ratio / Sortino Ratio:**
- NOT COMPUTABLE. No equity curve, no per-trade P&L series, no timestamps for individual trades. Year-level win% buckets are insufficient for ratio computation.

**Monte Carlo (ruin probability, DD distribution):**
- NOT RUN. No simulation conducted or reported across any trial.

**Slippage:**
- NOT MODELED in backtest. Integrity scan confirms slippage_modeled = false. All results are best-case (entry at next-bar open, no fill degradation).

**Walk-forward / Out-of-sample:**
- NOT CONDUCTED. Integrity scan confirms walk_forward_present = false. The 5yr holdout is the full evaluation period; no genuine OOS slice was reserved.

---

## 4. Forex Results Summary

All forex trials ran on `scripts/forex_phase0.py` (or `scripts/backtest_session.py` for ST-1) with spread + commission pip cost model. None passed the pre-registered dual gate (n>=50 AND net PF>1.0 at standard AND 2x cost stress).

| Trial | Pair | Signal | n | Net PF (std) | Net PF (2x stress) | Win% | Verdict |
|---|---|---|---|---|---|---|---|
| 27 | EURUSD | Session box — sweep mode | 108 | 0.58 | — | 24% | FAIL |
| 27 | EURUSD | Session box — all modes | 685 | 0.37–0.45 | — | — | FAIL |
| 28 | GBPUSD | Session box — sweep mode | 119 | 0.95 | — | 26% | FAIL (closest) |
| 28 | GBPUSD | Session box — all modes | 635 | 0.43–0.51 | — | — | FAIL |
| 29-EUR | EURUSD | BOS-retest continuation | 335 | 0.7359 | 0.6573 | 29.2% | FAIL |
| 29-GBP | GBPUSD | BOS-retest continuation | 339 | 1.0315 | 0.9254 | 36.6% | FAIL (2x fails) |
| ST-1 | EUR+GBP | IB sweep + CHoCH | 72 | 0.2250 | — | ~11% | FAIL |

**Summary:** Two forex signal families were tested (session-range breakout and BOS-retest continuation). Neither produced a robust PASS. GBPUSD BOS-retest (29-GBP) came closest: net PF 1.03 at standard cost, but failed at 2x stress (0.93). The pre-registered gate required both levels to pass simultaneously. Forex SMC track is closed per CLAUDE.md.

---

## 5. Curve Fitting and Data Snooping Assessment

This section transcribes the integrity scan findings verbatim.

**Curve fitting risk: HIGH**

- 29 distinct registered trial variants across 40 logged entries.
- Systematic grid search over 8-10 binary feature flags (mitigation threshold/mode, entry zone type, displacement ATR multiplier, target R, TP structure, session filter, 4H macro bias, BOS confirmation, 1H counter-bias filter, fib distance minimum, signal family).
- Only 2 trials passed (T21, T22). T22 is a direct permutation of T21 with partial TP added.
- The 2 PASS results were obtained after exhausting 27+ FAIL configurations on the same 5-year dataset.
- n=60-67 over 5 years with ~8-10 binary flags searched: degrees of freedom relative to sample size is high.
- 2021 sub-period shows fragility: n=8-9, WR=12-33%.

**Data snooping risk: HIGH**

Three compounding risks identified in scan:

1. **Multiple testing:** 29+ variants on the same 5yr BTC/1H holdout. No Bonferroni or FDR correction applied.
2. **No true OOS holdout:** The `--from`/`--to` slicing exists but no evidence it was used to reserve an untouched year. Both PASSes (T21 n=60, T22 n=67) ran over the full 2021–2026 dataset. The label "5yr holdout" refers to the full evaluation period, not an OOS slice.
3. **Sequential parameter selection:** Each successive trial was designed to address the prior failure mode. This is implicit overfitting even if each individual run is mechanistically clean.

---

## 6. Backtest Integrity Findings

| Check | Result |
|---|---|
| Lookahead bias | NONE FOUND. HTF alignment uses `searchsorted(..., side='left') - 1`; swing detection requires SWING_N right-side confirmed bars; entry at open of bar i+1; FVG/OB built from `df[:htf_idx+1]` slices. One mild note: `_ATR14_5M[i]` uses an EWM computed once offline, but EWM with `adjust=False` is causal — not material lookahead. |
| Fee model | CORRECT. Bybit taker 0.0006/side (0.12% rt) hard-coded. Forex model uses spread + commission pips. Net PF uses net_r throughout. |
| Slippage | NOT MODELED. Results are best-case fills. |
| Walk-forward | NOT PRESENT. |
| Monte Carlo | NOT PRESENT. |
| Holdout period | 5 years (2021–2026) — but this is the full evaluation period, not a true OOS split. |

---

## 7. Profitability Verdict

### Evidence that the strategy has an edge

- Two independent configurations passed the Phase-0 gate (net PF > 1.0, n >= 50) on the same 5yr dataset.
- Trial 21: net PF 1.3754, n=60, expectancy +0.226 R/trade.
- Trial 22: net PF 1.1986, n=67, expectancy +0.082 R/trade.
- Fee model is exchange-correct and applied. The 4H+1H chain pays 0.07-0.08 R/trade in fees vs 0.25-0.35 R/trade on 1H+5M, a genuine structural advantage.
- No lookahead bias found in the signal chain.
- The short-only sub-series (T8, T9) shows a strong directional edge: n=27, net PF=2.25, Win=55.6% — suggesting the raw signal quality is real on the short side.

### Evidence against reliability of the passing result

- **29 configurations tested on the same dataset before two passed.** With no multiple-testing correction, a false positive is statistically expected. The two passing results (T21 and T22) differ only in TP structure and are not independent experiments.
- **No true OOS holdout exists.** The 2021–2026 window was the search space throughout. A genuinely untouched year was never reserved.
- **n=67 over 5 years = ~13 trades/year.** Sample size is insufficient to distinguish a real edge from noise at this signal frequency, especially given the 2021 (WR=33%) and 2023 (net PF=0.65) drag years.
- **Slippage not modeled.** On 4H BTC with entries at next-bar open, real fill degradation at thin hours can meaningfully erode a 1.20 net PF.
- **Max DD = 11.25 R (T22).** At 0.5% risk/trade (Phase-3 config), this equals a 5.6% account drawdown from a single losing streak. At the Phase-2 0.25% rate, it equals 2.8%. This is within declared risk limits but historically conservative numbers may not hold on live data.

### Gate status

| Gate | Status |
|---|---|
| Phase-0: n>=50 AND net PF>1.0 | PASS (T21 and T22) |
| True OOS validation | NOT DONE |
| Walk-forward stability | NOT DONE |
| Monte Carlo ruin analysis | NOT DONE |
| Paper trade (Phase-1): 30 days | NOT DONE |

### Auditor conclusion

The strategy has cleared the defined Phase-0 gate on a mechanistically clean backtest with correct fee accounting and no identifiable lookahead. The PASS is not fabricated. However, the probability that the edge survives live data is materially discounted by: (1) the number of prior configurations tested on the same dataset, (2) the absence of a genuine OOS holdout, and (3) the low absolute trade count. The correct next step is the declared Phase-1 paper trade. The Phase-0 result should be treated as a **necessary but not sufficient** condition for deployment, not as confirmation of profitability. Live-capital exposure is not warranted until Phase-1 completes cleanly and an independent OOS window (preferably 2021 or early 2022 reserved before any testing) is evaluated.

---

*All numbers in this report originate from `docs/VERDICT_LOG.md` or the provided backtest integrity scan. No figures have been estimated, interpolated, or invented.*
