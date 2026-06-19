# BACKTEST VALIDATION AUDIT

**Project:** simple-smc-ag-trading-bot
**Audit Date:** 2026-06-18
**Auditor:** Senior Quant Auditor (automated scan)
**Scope:** All trials T1 – ST-1 run against BTC/USDT 1H+5M and Forex 1H+4H datasets

---

## Data Quality

| File | Bars | Start | End | Span |
|------|------|-------|-----|------|
| BTCUSDT_240m.parquet | 10,950 | 2021-06-17 | 2026-06-16 | ~5 yr |
| BTCUSDT_60m.parquet  | 43,800 | 2021-06-17 | 2026-06-16 | ~5 yr |
| BTCUSDT_5m.parquet   | 210,240 | 2024-06-16 | 2026-06-16 | ~2 yr |
| EURUSD_240m.parquet  | 7,781  | 2021-06-20 | 2026-06-18 | ~5 yr |
| EURUSD_60m.parquet   | 31,120 | 2021-06-20 | 2026-06-18 | ~5 yr |
| GBPUSD_240m.parquet  | 7,781  | 2021-06-20 | 2026-06-18 | ~5 yr |
| GBPUSD_60m.parquet   | 31,120 | 2021-06-20 | 2026-06-18 | ~5 yr |
| XAUUSD_240m.parquet  | 3,719  | 2024-01-26 | 2026-06-18 | ~2.4 yr |
| XAUUSD_60m.parquet   | 13,720 | 2024-01-26 | 2026-06-18 | ~2.4 yr |

**Observations:**

- BTC and EUR/GBP 1H datasets span approximately 5 years (2021-2026), which is adequate in
  calendar time but produces only 60-67 qualifying trades per PASS trial — dangerously thin.
- The 5M BTC file covers only 2 years (2024-2026). Any trial relying on 5M confirmation for
  pre-2024 signal bars cannot confirm exits on actual tick data; the engine must extrapolate
  or skip. The impact on pre-2024 trade counts and exit accuracy is not fully documented.
- XAUUSD data begins 2024-01-26, limiting Gold backtests to ~2.4 years — below the 3-year
  minimum generally required for regime coverage.
- No gap-fill or corporate-action audit has been documented. Parquet files are assumed clean
  from the Bybit / MetaAPI feeds with no independent cross-check against a reference vendor.
- Bar counts are internally consistent with the stated timeframes and intervals.

**Rating: ACCEPTABLE with caveats** — 5M BTC data gap pre-2024 and short XAUUSD window are
known limitations that must be disclosed in any Phase-1 decision.

---

## Lookahead Bias Analysis

**Finding: PASS — No material lookahead bias detected.**

The scan confirmed the following causal properties throughout the signal chain:

1. **HTF alignment** (`_align_htf`): uses `searchsorted(..., side='left') - 1`, mapping each
   LTF bar to the last *completed* HTF bar before its own timestamp. Correct.

2. **Swing confirmation**: confirmed index = `htf_idx - SWING_N`, requiring SWING_N right-side
   bars to have closed before a swing is used. Consistent with the live `smc_bot/` modules that
   use `range(n, len-n)` growing-window convention. Correct.

3. **Entry timing**: always at the open of bar `i+1` (next bar after signal bar close). No same-
   bar entry on the signal candle. Correct.

4. **Zone construction** (FVG, OB): built from `df[:htf_idx+1]` slices — only historically
   available bars are used. Correct.

5. **Exit simulation** (`_scan_exit`): starts from `entry_bar` onward, not backward. Correct.

6. **ATR series** (`_ATR14_5M`): computed as a Wilder EWM with `adjust=False` over the full
   array offline. EWM at index `i` depends only on bars `0..i` by construction, so the offline
   computation does not introduce lookahead despite using the full array. The minor point that
   `_fast_displacement` reads `_ATR14_5M[i]` (current bar, not sweep bar) is noted but deemed
   immaterial — EWM ATR changes slowly and the difference is sub-threshold.

**One residual note:** the "5yr holdout" label in the project documentation is a misnomer. The
full 2021-2026 window is the *evaluation* period, not a withheld OOS slice. This is a snooping
issue (see below) but not a lookahead issue.

---

## Fee & Cost Modeling

**Finding: PASS — Fee model is exchange-correct.**

- **BTC/USDT perp**: `TAKER_FEE = 0.0006` per side; `ROUND_TRIP = 0.0012` (0.12%). Applied as
  `fee_r = (ROUND_TRIP * entry_price) / stop_dist`. No maker rebate assumed. Conservative and
  correct for Bybit taker fills.

- **Forex**: spread + commission modeled in pips (`SPREAD_PIPS + COMMISSION_RT_PIPS`), converted
  to R via `pip_size / stop_dist`. Correctly avoids the erroneous %-of-notional model that would
  inflate forex fee drag.

- All gate decisions (`net_pf > 1.0`) use net R after fee deduction. The `avgfee_r` column
  values (0.07-0.09R) are consistent with the formula at realistic BTC price / stop-distance
  combinations (~$50k entry, $2k-3k stop → fee ≈ 0.03-0.06R per $50k; higher stops produce
  lower fee_r, consistent with reported values).

- Fee is charged once per round-trip regardless of partial-TP legs. This is a slight
  conservatism (real cost would be one entry + two exit fills) but errs in the correct direction.

**Gap:** No overnight funding / swap cost is modeled for BTC perpetual positions held beyond one
funding interval (8h). For trades lasting multiple days, accumulated funding can meaningfully
erode net R. This is noted as a documentation gap, not a disqualifying error.

---

## Slippage Assumptions

**Finding: FAIL — Slippage is not modeled.**

Zero slippage is assumed on all entries and exits. For BTC/USDT perp on Bybit this is partially
mitigated by entering at next-bar open (which approximates a market order at a realistic price),
but:

- Market-impact slippage on entry is 0 in all simulations.
- Stop-loss exits assume execution at the exact stop price, with no gap-through slippage.
- Partial-TP exits assume execution at exactly the TP price level.

For a liquid instrument like BTC perp with modest position sizes, slippage is small but non-zero.
At the typical trade R values in this dataset (net R contributions of 0.5-1.5 per winning trade),
even 0.02-0.05R of slippage per trade would meaningfully reduce a marginal net PF of 1.20.

**Recommendation:** Add a conservative slippage assumption of 0.5 × ATR(1m) or a flat $5-10
per side before Phase-1 signoff.

---

## Walk-Forward / OOS Testing

**Finding: FAIL — No walk-forward or true OOS holdout is implemented.**

- The `--from / --to` date-range flags exist in `scripts/backtest.py` but there is no documented
  evidence they were used to reserve an untouched segment prior to any trial run.
- All 29+ trials, including both PASS results (T21, T22), were evaluated over the full 2021-2026
  dataset. The yearly breakdowns in the VERDICT_LOG are diagnostic summaries, not independent
  OOS evaluations.
- No walk-forward windows (e.g., rolling 3yr-train / 1yr-test) have been run.
- No Monte Carlo permutation test or bootstrap confidence interval is reported for either PASS
  trial.

**Consequence:** The two PASS results (T21 n=60, net PF=1.38; T22 n=67, net PF=1.20) were
obtained after exhausting 27+ FAIL configurations on the *same* dataset. The probability that at
least one of 29 independent random strategies produces net PF > 1.0 with n=60 by chance alone is
non-trivial. Without a held-back OOS window, no statistical basis exists to distinguish genuine
edge from false discovery.

**Minimum remediation before Phase-1:** Reserve 2024-01-01 to present as a true OOS holdout.
Re-run T22 on 2021-2023 only. If T22 still passes with n ≥ 30 and net PF > 1.0 on the in-sample
window, evaluate OOS performance as an independent confirmation step.

---

## Curve Fitting Risk

**Rating: HIGH**

Evidence:

1. **29 distinct registered trial variants** across 40+ logged entries spanning T1 through ST-1.

2. **Systematic grid search** over at least 8-10 binary/ordinal feature flags:
   - Mitigation threshold: none / 50% / 75% / 100%
   - Mitigation mode: wick vs close
   - Entry zone: OB-only vs OB+FVG
   - Displacement ATR multiplier: 1.0× vs 1.5×
   - Target R: 2R vs 8R
   - TP structure: fixed / BSL-SSL pool / partial
   - Session filter: on / off
   - 4H macro bias filter: on / off
   - BOS confirmation: on / off
   - H1 counter-bias filter: on / off
   - Fib distance minimum: 0.0 vs 0.01
   - Signal family: SMC reversal / continuation / Asian session / session box

3. **Effective degrees of freedom vs sample size**: with ~10 binary flags the combinatorial
   search space is ≥ 1,024 configurations. Only 60-67 trades in the PASS trials means the
   ratio of free parameters to observations is extreme. Standard overfitting theory requires
   n >> 10 × k; here k is effectively 10+ and n = 67.

4. **2021 fragility**: in both PASS trials the 2021 year contributes n=8-9 trades with win
   rates of 12-33%, far below the strategy's mean. The overall PASS is sensitive to which
   calendar window is used.

5. **T21 → T22 dependency**: the second PASS (T22, partial-TP variant) is a direct permutation
   of the first PASS (T21). They are not independent confirmations.

**Conclusion:** The two PASS results are best interpreted as the highest-scoring outcomes from a
multi-hypothesis search, not as pre-registered, independently confirmed strategies. The
probability of at least one false positive under this search protocol is elevated and unquantified.

---

## Data Snooping Risk

**Rating: HIGH**

Three compounding snooping mechanisms are present:

### 1. Multiple Testing on the Same Dataset
29+ trial variants were evaluated on the same or overlapping 5-year BTC/1H dataset. No
Bonferroni correction, Benjamini-Hochberg FDR adjustment, or multiple-testing penalty is applied
to the gate threshold (net PF > 1.0). Under 29 independent tests, the expected number of false
positives at a 5% significance level is 1.45 — consistent with observing 2 PASSes out of 29 runs.

### 2. No True OOS Holdout
The project documentation refers to a "5yr holdout" but this label is incorrect. The 2021-2026
window is the full evaluation period used for all trials including the PASSes. No year or segment
was designated as untouched prior to the first trial run. The --from/--to mechanism exists but
its use for genuine train/test separation is undocumented.

### 3. Sequential Parameter Selection (Implicit Overfitting)
Each trial was explicitly designed to address the failure mode of the previous one:
- Mitigation turned OFF because mitigation collapsed n below gate
- FVG entries restored because OB-only reduced n below gate
- Session filter removed because it was over-filtering
- BOS retest added to improve win rate on the weakest year

This sequential search on fixed data is implicit overfitting even if no single trial is modified
mid-run. The final PASS configuration was informed by all prior failure modes observed on the
same data.

**Remediation:** Run T22 exact specification on a genuinely unseen segment (e.g., 2024-2026 for
BTC, which was not part of the signal development loop). Accept the result whatever it shows
before proceeding to Phase-1.

---

## Trial History Summary

| Trial | n | Net PF | Verdict |
|-------|---|--------|---------|
| 1 | 1,570 | 0.683 | FAIL |
| 2 | 1,333 | 0.640 | FAIL |
| 3 | — | — | ABANDONED |
| 4 | 301 | 0.657 | FAIL |
| 5 | 26 | 2.083 | OVERFILTERED (n < 50) |
| 5X | 45 | 0.995 | FAIL |
| 6 | 85 | 0.994 | INVALIDATED (bug) |
| 7 | 1 | — | VALID but n = 1 |
| 8 | 47 | 1.566 | FAIL (n < 50 by 3) |
| 9 | 47 | 1.566 | SENSITIVITY STUDY — no PASS |
| 10 | 14 | 1.331 | FAIL |
| 11 | 26 | 1.504 | FAIL (n < 50) |
| 12 | 26 | 1.504 | FAIL (n < 50) |
| 13 | 194 | 0.519 | FAIL |
| 14 | 188 | 0.643 | FAIL |
| 15 | 81 | 0.368 | FAIL |
| 16 | 83 | 0.390 | FAIL |
| 17 | 56 | 0.342 | FAIL |
| 18 | 44 | 0.458 | FAIL (n < 50) |
| 19 | 26 | 1.504 | FAIL (n < 50) |
| 20 | 48 | 1.637 | FAIL (n < 50 by 2) |
| **21** | **60** | **1.375** | **PASS** |
| **22** | **67** | **1.199** | **PASS** |
| 23 | 48 | 1.345 | FAIL (n < 50 by 2) |
| 24 | 18 | 0.858 | FAIL |
| 25 | 921 | 0.328 | FAIL |
| 26 | 66 | 0.972 | FAIL |
| 27 | 685 | 0.370 | FAIL |
| 28 | 635 | 0.430 | FAIL |
| 29-EUR | 335 | 0.736 | FAIL |
| 29-GBP | 339 | 1.032 | FAIL (2× stress) |
| ST-1 | 72 | 0.225 | FAIL |

*Note: Trials 8, 20, 23 each missed the n ≥ 50 gate by 2-3 trades despite positive PF.
The two PASS results are the only trials meeting both gates simultaneously.*

---

## Backtest Grade

**Overall Grade: C**

| Dimension | Score | Notes |
|-----------|-------|-------|
| Lookahead Bias | PASS | Causal HTF alignment, next-bar entry, correct EWM |
| Fee Modeling | PASS | Bybit taker 0.12% RT applied to all net_pf decisions |
| Slippage | FAIL | Zero slippage assumed throughout |
| Walk-Forward / OOS | FAIL | No true holdout; all trials on full 2021-2026 window |
| Curve Fitting Risk | HIGH | 29 variants, 8-10 feature flags, n=60-67 |
| Data Snooping Risk | HIGH | Multiple testing, no FDR correction, sequential design |
| Data Quality | ACCEPTABLE | 5M gap pre-2024; XAUUSD short history |
| Sample Size | MARGINAL | Both PASSes: n=60-67 over 5 years |

### Summary Judgment

The mechanical backtest engine is well-built: no lookahead bias, correct fee application, and
causal signal construction throughout. These are genuine positives.

However, the process that produced the two PASS results exhibits all three classical hallmarks
of backtest overfitting: exhaustive search across a large combinatorial parameter space,
evaluation on the same fixed dataset for every trial, and sequential parameter adjustment
informed by prior failure modes. With 29 trials on the same data, the false-discovery rate is
elevated and unquantified. Neither PASS result has been confirmed on an independently held-out
segment.

**The two PASS results (T21, T22) should be treated as promising hypotheses, not confirmed
edges, until validated on a genuinely unseen data window.**

### Required Actions Before Phase-1

1. **Slippage model**: add conservative slippage (0.5 × ATR(1m) or flat $5-10 per side).
2. **True OOS holdout**: designate 2024-2026 as held-out. Re-run T22 exact spec on 2021-2023
   only. If in-sample passes, evaluate OOS as confirmation.
3. **Multiple-testing adjustment**: apply Bonferroni-corrected gate (net PF > 1.15 at n=60
   given 29 tests) or accept higher bar on OOS confirmation.
4. **Funding cost**: document estimated BTC perpetual funding drag for multi-day trades and
   assess materiality.
5. **5M data gap**: document which pre-2024 trades used estimated vs actual 5M exit prices.

Until items 1-3 are addressed, Phase-1 paper trading is premature. The risk is not that the
strategy has no edge — it may — but that the current evidence is insufficient to distinguish
real edge from false discovery after 29 trials on fixed data.
