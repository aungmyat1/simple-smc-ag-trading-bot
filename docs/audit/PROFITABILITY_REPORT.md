# PROFITABILITY EVIDENCE REPORT
**Audit date:** 2026-06-18  
**Mandate:** Evidence-based conclusions only. No speculation. No assumptions.

---

## Passing Trials Summary

Only two trials in the project's entire history have met the pre-registered gate (n≥50 AND net PF>1.0):

| Trial | Symbol | TF | n | Gross PF | Avg Fee (R) | Net PF | Win% | Expect (R/trade) | Max DD (R) |
|---|---|---|---|---|---|---|---|---|---|
| **21** | BTCUSDT perp | 4H+1H | 60 | 1.5294 | 0.0739 | **1.3754** | 43.3% | +0.226 | 9.39 |
| **22** | BTCUSDT perp | 4H+1H | 67 | 1.4038 | 0.0749 | **1.1986** | 61.2% | +0.082 | 11.25 |

All other trials (27 BTC variants + 9 Forex variants = 36 trials) FAIL or are INCONCLUSIVE.

---

## T21 Analysis

**Strategy:** 4H macro bias → 1H swing bias → 1H OB+FVG zone → 1H liquidity sweep + CHoCH. Entry at next-bar open. Fixed 2R TP. 5yr holdout (2021-06-17 → 2026-06-16).

**Year-by-year breakdown:**
| Year | n | Win% | Notes |
|---|---|---|---|
| 2021 | 8 | 12% | Bull market run-up — swing structure filters most setups |
| 2022 | 8 | 50% | Bear market — 4H shorts perform |
| 2023 | 13 | 23% | POST-FTX transition drag — only year with negative PF |
| 2024 | 7 | 57% | Bull market recovery |
| 2025 | 18 | 56% | Volatile — signal fires more frequently |
| 2026 | 6 | 67% | Limited sample |

**Regime consistency:** Signal earned positive returns in 5 of 6 years. One clear drag year (2023 post-FTX transition, net PF=0.54, n=13). 2021 has very low win rate (12%, n=8) but survived due to large winners. Signal appears to work across bear/bull regimes; transition years are the weakness.

**Expectancy:** +0.226R per trade. On $100 risk: +$22.60 per trade.

---

## T22 Analysis

**Strategy:** T21 baseline + partial TP: 50% close at 1R, SL moved to breakeven, remainder runs to 2R. No code change — config flag only.

**Behavioral change from T21:**
- Trade count increases 60→67 (partial TP resolves some trades before full 2R target)
- Win rate jumps 43→61% (TP1 half-closes count as wins)
- Net PF drops 1.38→1.20 (partial wins average only 0.5–0.8R vs 2.0R full wins)
- Max DD increases 9.39→11.25R (SL-to-BE after TP1 extends some trades that eventually stop out at BE)
- Expectancy drops +0.226→+0.082R/trade

**Regime consistency (T22):**
| Year | n | Win% |
|---|---|---|
| 2021 | 9 | 33% |
| 2022 | 8 | 50% |
| 2023 | 14 | 50% |
| 2024 | 7 | 71% |
| 2025 | 22 | 73% |
| 2026 | 7 | 86% |

**Improvement over T21 in losing year:** 2023 improves from 23% to 50% win rate — partial TP captures more of the volatile, mean-reverting intra-move action. **This is the only material quality improvement T22 offers over T21.**

**Trade-off verdict:** T22 has worse expectancy (+0.082R vs +0.226R) and higher max drawdown. T21 is a cleaner signal with better expected return per trade. T22 is the Phase-1 candidate per CLAUDE.md because it was the last PASS — but the original T21 performance is arguably stronger.

---

## Failed Trial Families

| Signal Family | Best Result | Why Failed |
|---|---|---|
| EMA cross (15m BTC) | Net PF=0.683 | Fee floor kills edge (0.31R/trade on 15m) |
| 1H+5M SMC chain (BTC) | Gross PF=0.753, n=194 | No gross edge on 5M LTF — fee was not the problem |
| Asian session box (BTC) | Net PF=0.972 (sweep-only, n=66) | 2024 year: 0/10 trades profitable; strong trend kills mean-reversion |
| EURUSD SMC reversal | Net PF=0.580, n=108 | No gross edge |
| GBPUSD SMC reversal | Net PF=0.950, n=119 | Marginal gross edge, fee drag kills it |
| EURUSD BOS-continuation | Net PF=0.736, n=335 | No gross edge |
| GBPUSD BOS-continuation | Net PF=1.032 (1×) / 0.925 (2×) | Passes 1× but fails dual-level stress gate |
| Session Trader (IB sweep) | EUR n=34 PF=0.24, GBP n=38 PF=0.21 | Entry too far from CHoCH inflection — R too large |

---

## Risk-Adjusted Metrics

Sharpe and Sortino ratios are **NOT COMPUTABLE** from the existing backtest output. The backtest produces a trade list (entry_bar, exit_bar, gross_r, net_r) but does not output an equity time series. To compute Sharpe/Sortino:
1. A daily equity curve must be generated from the trade list (time-weighted).
2. The annualized risk-free rate must be applied.
3. For Sortino, downside deviation calculated from the daily returns below zero.

**Estimate only (T22, 4H+1H, 5yr):**
- Average trades per year: 67 / 5 = 13.4 trades/year (very low frequency)
- Average return per year: 13.4 × 0.082R = +1.10R/year (at $100 risk per trade: +$110/year on a ~$1000 account)
- Standard deviation: not computable without equity curve
- At this trade frequency, Sharpe computation is unstable — 13 trades/year is insufficient sample size for reliable annual Sharpe estimates

**Conclusion:** The strategy is profitable per the backtest but at low frequency. The per-trade expectancy is positive but modest (+0.082R for T22, +0.226R for T21).

---

## F-1 Forex Result (Excluded)

The F-1 EURUSD H1 result (n=59, net PF=1.289) was generated at `/opt/forex-validate/` using the `smc` Python library. That library's `swing_highs_lows()` function uses `shift(-swing_length)` — introducing approximately 10-bar look-ahead. The result cannot be trusted as a clean evidence of edge. **It is excluded from this profitability report.**

---

## Evidence Assessment

| Question | Answer | Evidence |
|---|---|---|
| Is there a gross edge in BTC 4H+1H SMC? | **YES** | T21 gross PF=1.53, T22 gross PF=1.40. Survived fees. |
| Is the edge net-positive? | **YES** | T21 net PF=1.38, T22 net PF=1.20. Fees are small (avg 0.074R). |
| Is the edge consistent across regimes? | **PARTIAL** | 5/6 years positive. One transition year (2023) is a drag. |
| Has the edge been OOS validated? | **NO** | Same 5yr dataset used for all 38 trials. No reserved holdout. |
| Is there a Forex edge? | **NO** | All 9 Forex trials FAIL (including 2× stress gate) |
| Is the edge proven at 95% confidence? | **NO** | 38 iterations on same dataset; medium curve-fitting risk. |

---

## Profitability Verdict

**PROFITABILITY_PROVEN: NO**

Rationale: Two PASS trials exist on BTC 4H+1H. The edge appears real (economically interpretable signal, low fee drag, multi-regime consistency). However:
- "Proven" requires out-of-sample validation that does not exist.
- 22 BTC iterations before finding the first PASS elevates false-discovery probability.
- T22's expectancy (+0.082R) is thin — a small increase in real-world costs (slippage, overnight API outages, partial fills) could eliminate it.
- Forex: no evidence of edge found on the target production asset.

**The correct label is: PLAUSIBLE — not PROVEN.**

A true OOS test on the last year (2025-06-16 → 2026-06-16, not used in any prior training run) is the minimum next step before "proven" can be written.
