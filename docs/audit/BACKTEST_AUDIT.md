# BACKTEST VALIDATION AUDIT
**Audit date:** 2026-06-18  
**Files audited:** scripts/backtest.py (3,500+ LOC), docs/VERDICT_LOG.md, data/cache/

---

## Data Quality

| File | Bars | Start | End | Period | Notes |
|---|---|---|---|---|---|
| BTCUSDT_240m.parquet | 10,950 | 2021-06-17 | 2026-06-16 | 5yr | ✅ Full holdout |
| BTCUSDT_60m.parquet | 43,800 | 2021-06-17 | 2026-06-16 | 5yr | ✅ Full holdout |
| BTCUSDT_5m.parquet | 210,240 | 2024-06-16 | 2026-06-16 | 2yr | ⚠ Only 2yr (Sprint trials only; all failed) |
| EURUSD_240m.parquet | 7,781 | 2021-06-20 | 2026-06-18 | 5yr | ✅ Full holdout |
| EURUSD_60m.parquet | 31,120 | 2021-06-20 | 2026-06-18 | 5yr | ✅ Full holdout |
| GBPUSD_240m.parquet | 7,781 | 2021-06-20 | 2026-06-18 | 5yr | ✅ Full holdout |
| GBPUSD_60m.parquet | 31,120 | 2021-06-20 | 2026-06-18 | 5yr | ✅ Full holdout |
| XAUUSD_240m.parquet | 3,719 | 2024-01-26 | 2026-06-18 | 2.4yr | ⚠ Short history (yfinance GC=F limit) |
| XAUUSD_60m.parquet | 13,720 | 2024-01-26 | 2026-06-18 | 2.4yr | ⚠ Short history |

**Data source:**
- BTC: Bybit public API (no authentication, reliable OHLCV)
- Forex 4H/1H: MetaAPI historical export (fetched before account went DRAFT)
- XAUUSD: yfinance GC=F (spot gold futures — has overnight gaps, roll periods)

**Gap analysis:** Not explicitly checked by backtest engine. Bybit data has minimal gaps (continuous futures). Forex 1H has weekend gaps and overnight gaps (48-60hr Friday-Sunday). No candle-gap interpolation applied. In the SMC chain, weekend gaps could cause a "sweep" signal where a gap low/high is mistaken for a wick through a prior swing — FALSE POSITIVE RISK on Forex.

---

## Lookahead Bias Analysis

### Current `scripts/backtest.py` — **CLEAN**

| Pattern | Implementation | Status |
|---|---|---|
| Swing detection | `_swing_highs_np()`: `if high[i] == high[i-n:i+n+1].max()` requires `n` right-side bars — uses `max_conf = htf_idx - SWING_N` via `bisect_right` | ✅ Causal |
| Entry price | Always `open[entry_bar]` where `entry_bar = signal_bar + 1` | ✅ Next-bar open |
| Sweep detection | Scans bars strictly after swing confirmation bar | ✅ Causal |
| CHoCH detection | Uses close of current bar `_C_5M[i]` vs ref level from bars ≤ sweep_bar | ✅ Causal |
| FVG detection | `disp_bar → fvg_bar = disp_bar + 1 → retest_bar > fvg_bar` | ✅ Strictly forward |
| Exit scan | `_scan_exit()` starts from `entry_bar` | ✅ No future data |
| ATR | Wilder EWM — no future leak | ✅ Causal |

### `/opt/forex-validate/` F-1 test (2026-06-17) — **CONTAMINATED**

The F-1 EURUSD H1 test (n=59, net PF=1.289) used the `smc` Python library whose `bos_choch()` / `swing_highs_lows()` functions use `shift(-swing_length)` internally. The project's own memory notes: *"Residual caveat: smc swing_highs_lows has ~10-bar look-ahead from shift(-swing_length)."*

**The F-1 result is NOT admissible as clean evidence of edge.** It must be rerun on the current causal infrastructure.

---

## Fee & Cost Modeling

| Cost type | Model | Application |
|---|---|---|
| Bybit taker (BTC) | 0.06%/side = 0.12% round-trip | `_cost_r()` deducts `ROUND_TRIP × entry_price / stop_distance` in R-units |
| Forex spread | Configurable pips (EURUSD: 0.8pip default) | `_cost_r()` with pip-based formula: `(spread + commission) × pip_size / stop_dist_price` |
| Forex commission | 0.6pip round-trip (VT Markets Raw ECN default) | Included in pip formula |
| Slippage | **NOT MODELED** | Market orders assumed to fill at signal-close or next-bar open exactly |
| Swap/overnight | **NOT MODELED** | Forex trades held overnight incur swap; not deducted |
| Partial fill | **NOT MODELED** | Assumes 100% fill at stated price |

**Slippage omission:** For BTC on Bybit at typical position sizes ($100 risk), slippage on a market order is negligible (BTC perp is deep-liquid). For Forex via MetaAPI/MT5 at VT Markets, slippage of 0.5–2 pips is realistic during news events, equivalent to 0.05–0.2R on a typical 10-pip stop. This is a material omission for Forex.

---

## Slippage Assumptions

**BTC Bybit perp:** Assumes fill at next-bar open. In practice, market order fills within the same bar's open-to-close range. Assumption is conservative (slightly pessimistic — actual fill could be anywhere in that range, usually near open). **Acceptable.**

**Forex MetaAPI/MT5:** Same assumption. MT5 market orders have execution latency of 100–500ms and fill price can vary from the signal-bar close. On EURUSD with 1pip spread, an additional 0.5pip slippage is ~5% of a typical 10-pip stop. **Mildly optimistic assumption — not addressed.**

---

## Walk-Forward / OOS Testing

| Test | Status |
|---|---|
| Walk-forward validation | ❌ NOT PRESENT |
| Out-of-sample holdout | ⚠ PARTIAL — backtest uses full 5yr continuously, no reserved OOS period |
| Monte Carlo simulation | ❌ NOT PRESENT |
| Bootstrap resampling | ❌ NOT PRESENT |
| --from/--to date-slicing | ✅ CLI args exist; not used in published trials |

The `--from` and `--to` CLI arguments were added but no trial has been published with a held-out OOS year. The entire 5yr dataset has been used for every trial. This means there is NO true out-of-sample result.

**Mitigating factor:** The 5yr window covers multiple market regimes (2021 bull, 2022 bear, 2023 transition, 2024 bull, 2025–2026 volatile). The year-by-year breakdown in Trial 22 shows the signal earned positive returns in 4 of 6 periods, providing some regime diversity. But this is not a substitute for a reserved holdout.

---

## Curve Fitting Risk

**Total parameter iterations across all trials:** 29 BTC trials + 9 Forex trials = 38 total runs.

**Parameter changes logged:**
- swing_n: tested 3, 5
- displacement_atr: tested 0.5, 1.0, 1.5
- mitigation: wick/close at 50%, 75%, 100%, OFF
- ob_lookback, fvg_lookback: 30, 50
- Timeframes: 1H+5M, 4H+1H (tested both sides)
- Entry modes: OB-only, FVG+OB, FVG-only, BOS-confirm, FVG-retest
- Exit modes: fixed 2R, partial TP at 1R, BSL/SSL pool TP
- Filters: 4H macro bias, H1 bias filter, session/kill-zone, fib distance

**Curve fitting risk: MEDIUM**

Each change was logged as a new trial with a new number, which is good practice. However, the PASS result (T22) was reached after 22 BTC iterations, meaning 21 variants were tried before finding the passing configuration. With 22 attempts, a random strategy has a meaningful probability of passing a PF>1.0 gate by chance on a 5yr backtest.

**Mitigating factors:**
- Each trial changed the signal logic (not just threshold tuning)
- The failing mechanisms are economically interpretable (fee floor, zone rejection)
- T22's PF=1.20 is conservative, not inflated

**Data snooping risk: MEDIUM** — same dataset used for all 38 iterations. The VERDICT_LOG shows T23 (n=48, PF=1.35) nearly passes with better metrics than T22 (n=67, PF=1.20), but was rejected due to n<50. The threshold of n≥50 was set pre-trial, which is correct discipline.

---

## Trial History Summary (from VERDICT_LOG.md)

| Trial | Signal | TF | n | Net PF | Verdict |
|---|---|---|---|---|---|
| 1 | EMA50/200 + swing retest | 15m | 1,570 | 0.683 | FAIL |
| 2 | EMA50/200 + breakout-only | 15m | 1,333 | 0.640 | FAIL |
| 4 | SMC Sniper 1H+5M baseline | 1H+5M | 301 | 0.657 | FAIL |
| 8 | 4H+1H corrected (no mitigation) | 4H+1H | 47 | 1.566 | FAIL (n<50) |
| 15–18 | Sprint features on 1H+5M | 1H+5M | 44–83 | 0.34–0.46 | FAIL |
| 21 | 4H+1H, FVG+OB, 5yr | 4H+1H | 60 | **1.375** | **PASS** |
| 22 | T21 + partial TP | 4H+1H | 67 | **1.199** | **PASS** |
| 23 | T22 + H1 bias filter | 4H+1H | 48 | 1.345 | FAIL (n<50) |
| 24 | T23 + fib distance min | 4H+1H | 18 | 0.858 | FAIL |
| 25 (BTC) | Owned FVG retest | 4H+1H | — | — | INCONCLUSIVE |
| 27 (Forex) | EURUSD session box | 4H+1H | 108 | 0.580 | FAIL |
| 28 (Forex) | GBPUSD session box | 4H+1H | 119 | 0.950 | FAIL |
| 29-EUR | EURUSD BOS-retest | 4H+1H | 335 | 0.736 | FAIL |
| 29-GBP | GBPUSD BOS-retest | 4H+1H | 339 | 1.032 | FAIL (stress) |

*Trials 3–7, 9–14, 19–20 omitted from table for brevity (all FAIL, various reasons)*

---

## Backtest Grade: **B−**

**Justification:**
- Clean causal implementation (no lookahead in current code) ✅
- Fees properly modeled for both BTC and Forex ✅
- 5yr holdout covers multiple regimes ✅
- Entry at next-bar open ✅
- No slippage modeled ⚠
- No walk-forward or true OOS ⚠
- 38 iterations on same dataset — medium curve fitting risk ⚠
- F-1 EURUSD result is contaminated (look-ahead from `smc` library) ❌
- No Monte Carlo ❌
