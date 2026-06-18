# Verdict Log — Simple SMC AG Trading Bot

One row per trial. Never delete entries. Every parameter change = new row.
Source data for all trials: BTCUSDT 15m, 2023-01-01 → 2024-12-31 (70,081 bars).
Source data for 4H+1H trials: BTCUSDT 4H+1H, 2022-06 → 2026-06 (4-year holdout).
Fee model: Bybit taker 0.06%/side = 0.12% round trip.

---

## Results

| Trial | Date | Signal | TF | n | Gross PF | Avg fee (R) | Net PF | Win% | Verdict |
| TRIAL-S | 2026-06-16 | Session Range Breakout alpha (London/NY breakout of session range). Params: sl_pct=0.25, reward_r=5.0, trend_partial_r=4.0, range_atr_mult=1.5, min_range_pct=3.2%. Dataset: BTCUSDT 5M 2yr (210,240 bars). | 5M | — | — | — | — | — | **FEE_DEAD — HALTED at Phase 0.** Combined fraction of sessions with range ≥ 3.2%: 0.233 (< 0.30 threshold). London: 123/731=16.8%, NY: 218/730=29.9%. Median ranges: London 1.93%, NY 2.26%. Fee math when passing: avg fee_r=0.11R (excellent) — but too few qualifying sessions to matter. Root cause: 3.2% minimum range too strict for current BTC volatility. No code written, no backtest run. Owner decides: (a) lower min_range_pct — new trial required, (b) NY-only with 2.5% threshold (≈56% pass rate), (c) abandon session alpha. |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-06-15 | EMA50/200 trend + 20-bar swing break + retest (tol 0.5×ATR, valid 10 bars) | 15m | 1570 | 1.0233 | 0.313 | 0.6834 | 29.0% | **FAIL** |
| 2 | 2026-06-15 | EMA50/200 trend + 20-bar swing breakout-only (no retest) | 15m | 1333 | 0.9932 | 0.348 | 0.6399 | 28.4% | **FAIL** |
| 3 | 2026-06-15 | SMC Sniper (1BullBear Ep15A): London/NY session filter + 1H POI (OB/FVG, discount) → 5M inducement sweep + displacement (≥1.5×ATR) + CHoCH + 5M OB/FVG retrace. Partials: 50%@2R (→BE) / 25%@3R / 25%@HTF-liq. Min R:R=2. Consec-loss guard=2. | 1H+5M | TBD | — | — | — | — | **ABANDONED** (backtest scored _archive EMA chain not smc_bot/) |
| 4 | 2026-06-15 | SMC Sniper via smc_bot/ chain: 1H swing bias (HH+HL) + 1H OB/FVG POI → 5M sweep (swing pierce+close) + CHoCH. Single exit: 2R TP / SL=wick−0.1%. Config: smc_bot/config.yaml. Backtest: scripts/backtest.py (seam fixed, no _archive). | 1H+5M | 301 | 0.9366 | 0.2498 | 0.6567 | 31.9% | **FAIL** (gross PF < 1.0 — no edge before fees; signal family dead on 5M) |
| 5 | 2026-06-15 | SMC Sniper H1 variant: same smc_bot/ chain (HH+HL bias + OB/FVG POI + sweep + CHoCH), HTF=4H, LTF=1H. Single 2R exit. 2yr holdout (2024-06). | 4H+1H | 26 | 2.3333 | 0.0792 | 2.0833 | 53.8% | **OVERFILTERED** (n=26<50; fee=0.08R confirmed; re-run on 4yr below) |
| 5X | 2026-06-15 | Same as Trial 5 — 4H+1H, single 2R exit, LONG-ONLY — extended to 4yr holdout (2022-06 → 2026-06). | 4H+1H | 45 | 1.1034 | 0.0704 | 0.9946 | 35.6% | **FAIL** (n=45<50; net PF=0.9946 barely misses; 2022 bear kills longs; solution: add shorts) |
| 6 | 2026-06-16 | SMC Sniper bidirectional: same smc_bot/ 15-step chain (fib+mitigation+displacement), HTF=4H, LTF=1H. Long+short. 2R single exit. 4yr holdout (2022-06 → 2026-06). | 4H+1H | 85 | 1.0909 | 0.0633 | 0.9935 | 35.3% | **INVALIDATED** — `side` variable shadowing bug corrupted bidirectional direction filtering; n=85 is unreliable. Re-run → Trial 8. |
| 7 | 2026-06-16 | SMC Sniper SHORT-ONLY: same 15-step chain (fib+displacement+50% mitigation), HTF=4H, LTF=1H, `--side short`. | 4H+1H | 1 | inf | 0.0424 | inf | 100% | **VALID but n=1** — bug did not affect single-direction runs; confirms 50% mitigation destroys short signal count. Sensitivity study → Trial 9. |
| 8 | 2026-06-16 | SMC Sniper bidirectional (bug-fixed): same 4H+1H chain, mitigation OFF, `--side both`. Corrected baseline after fixing `side` variable shadowing in `run_backtest()`. | 4H+1H | 47 | 1.7600 | 0.0724 | 1.5662 | 46.8% | **FAIL (n=47 < 50 gate)** — strong gross edge (PF=1.76), short-only sub-PF=2.25 (n=27), long sub-PF=1.22 (n=22). 2023 drags win rate (23%, n=13); 2024–2026 combined 56% win (n=30). Year breakdown: 2022(n=4,50%), 2023(n=13,23%), 2024(n=7,57%), 2025(n=17,53%), 2026(n=6,67%). CHoCH funnel: 188 signals. 3 trades short of gate — worth pursuing direction refinement. |
| 9 | 2026-06-16 | Mitigation sensitivity study: same 4H+1H chain, `--side both`, four thresholds: none / 50% / 75% / 100%. | 4H+1H | see below | — | — | see below | see below | **FINDING: mitigation is near-binary bottleneck** — 50% kills 76.5% of zones (poi_raw=4739→poi_fresh=122), collapsing CHoCH from 188 to 3; 75% produces identical n=2 (same zones survive); 100% loosens to n=4 but PF collapses to 0.61 (bad zone re-entry). Mitigation OFF (n=47) is the only viable operating point. See §Sensitivity Study table below. |
| 10 | 2026-06-16 | Trial 10 — close-based mitigation: same 4H+1H chain, full 2×3 matrix (wick/close × 50/75/100%) + baseline. Side=both primary; short/long sub-runs also recorded. | 4H+1H | see below | — | — | see below | see below | **FAIL (no combination reaches n≥50)** — close-based is materially less aggressive than wick (49.6% vs 76.5% zone rejection at 50%), but max bidirectional n=14 (close 100%). Short-only close 100% shows n=7, Win=85.7%, PF=10.69 — extraordinary quality but statistically meaningless. Long-only under any mitigation: 0% win rate across n=2-7. Mitigation filter concept is incompatible with this chain at 4H: cannot simultaneously preserve quality AND quantity. See §Trial 10 Matrix below. |
| 11 | 2026-06-16 | **OB-only entry rule** (diagram rule): same 4H+1H chain (mitigation OFF), entry zone restricted to Order Block only. FVG-only entries excluded. Both `price_in_poi` calls in backtest + bot.py changed to `ob_for_price`. | 4H+1H | 26 | 1.7143 | 0.0924 | 1.5042 | 46.1% | **FAIL (n=26 < 50)** — OB-only entry cuts n from 47→26 (45% reduction). FVG entries were ~50% of prior signals at equal quality (net PF 1.57→1.50 barely changed). Short-only: n=15, net PF=2.03. CHoCH funnel: 188→94 (50% cut at POI stage). 2022+2024–2026 profitable (PF>2), 2023 drag (Win=12%). Quality preserved — count further reduced. |
| 12 | 2026-06-16 | **Displacement gate relaxed** (1.5→1.0×ATR): same 4H+1H chain (mitigation OFF, OB-only entry). Post-sweep displacement confirmation threshold reduced from 1.5×ATR to 1.0×ATR. | 4H+1H | 26 | 1.7143 | 0.0924 | 1.5042 | 46.1% | **FAIL (n=26 < 50)** — n unchanged vs Trial 11. Funnel: displacement passes increased 855→1432 (+67%), CHoCH 94→101 (+7), trades remained 26. Bottleneck is CHoCH→trade conversion (25.7%): 75% of CHoCH signals are killed by one-at-a-time gate or absence of LTF OB retrace. Displacement relaxation does not solve the count problem. Short-only: n=15, net PF=2.03 (identical). Year: 2022(n=4,75%), 2023(n=8,12%), 2024(n=4,75%), 2025(n=8,50%), 2026(n=2,50%). |
| 14 | 2026-06-16 | **8R minimum TP** (1:8 RR): same 1H+5M chain (mitigation OFF, OB-only, Fib 50%). `targets.fallback_r: 8.0`, `targets.min_r: 8.0`, `risk.target_r: 8.0`. SL unchanged (sweep wick ±0.1%). Side=both. 2yr 5M holdout (2024-06 → 2026-06). | 1H+5M | 188 | 0.8471 | 0.2679 | 0.6430 | 9.6% | **FAIL** — win rate 9.6% is BELOW the gross break-even at 8R (11.1%). Only 18/188 trades reach 8R TP; 170 hit SL. Short-only sub: n=93, Win=10.8%, gross PF=0.964 (closest to viable but fees kill it). Long-only: n=95, Win=8.4%, gross PF=0.735. Root cause: 1H+5M entries target micro-reversals (sweep+CHoCH) with tight stops (~0.3% of price); an 8R win requires a ~2.4% sustained directional move which doesn't materialise consistently at this entry quality. Higher RR requires fewer, higher-conviction setups — this chain generates 188 trades/2yr which is too many for 8R. Year: 2024(n=50,10%WR), 2025(n=104,11%WR), 2026(n=34,6%WR). |
| 15 | 2026-06-16 | **Sprint 1: 4H macro bias** — 1H+5M chain (mitigation OFF, 2R TP reverted) with 4H macro bias pre-filter: 4H swing structure must agree with 1H direction before proceeding. `--macro-htf BTCUSDT_240m.parquet`. Side=both. 2yr 5M holdout. | 1H+5M | 81 | 0.5312 | 0.2536 | 0.3675 | 21.0% | **FAIL** — 4H macro filter cuts 67% of 1H bias bars (126k→42k), reducing trades from ~188 to 81. Win rate drops 21%. 4H disagreement does NOT consistently identify losing trades on this chain — filtering against 4H actually hurts. Root cause: 1H+5M chain has no gross edge regardless of macro filter. CONFIRMATION: sprint-based fixes cannot rescue the 1H+5M chain. |
| 16 | 2026-06-16 | **Sprint 1+2: +Partial TP/BE** — same 4H macro bias filter + two-leg partial exit: 50% close at 1R, remaining trails to break-even (SL→entry) then targets 2R. `--macro-htf --partial-tp`. Side=both. 2yr. | 1H+5M | 83 | 0.6771 | 0.2528 | 0.3900 | 42.2% | **FAIL** — partial TP raises win rate to 42% (half-wins = TP1+BE = +0.5R counted as wins) but gross PF=0.68. Average win ≈ 0.5–0.75R, average loss = 1R → still negative. Maximum achievable gross_r reduced from 2R to 1.5R (0.5×1R + 0.5×2R). Partial TP cannot fix a signal with no positive gross edge. |
| 17 | 2026-06-16 | **Sprint 1+2+3: +Kill Zone** — Sprint 1+2 + session filter: entries only during London (08-15 UTC) and NY (13-21 UTC). `--macro-htf --partial-tp --session-filter`. Side=both. 2yr. | 1H+5M | 56 | 0.5882 | 0.2411 | 0.3424 | 39.3% | **FAIL** — session filter cuts 42% of POI bars (5331→3081), reducing CHoCH to 316 and trades to 56. Win rate 39%, gross PF=0.59. Session filter selects a worse-quality subset (London/NY does not improve edge on this chain). |
| 18 | 2026-06-16 | **Sprint 1-4: all** — Sprint 1+2+3 + BOS confirmation: require a 5M BOS close after CHoCH before entry. `--macro-htf --partial-tp --session-filter --bos-confirm`. Side=both. 2yr. | 1H+5M | 44 | 0.6923 | 0.1939 | 0.4576 | 40.9% | **FAIL (n=44<50)** — BOS filter keeps 73% of CHoCH (232/316), reduces trades to 44 (below n gate). Net PF=0.46, still negative. BOS confirmation improves avg fee_r to 0.19R (entry is further from sweep) but cannot create edge where none exists. FINAL VERDICT: Sprint 1-4 applied to 1H+5M chain produces no passing result. The 1H+5M chain is dead at every filter combination. |
| 19 | 2026-06-16 | **4H+1H chain, 4yr, OB-only** — restore 4H+1H configuration. HTF=4H parquet (2022-2026, 4yr), LTF=1H parquet (re-fetched as 4yr). Mitigation OFF, OB-only entry (Trial 11 rule). Side=both. | 4H+1H | 26 | 1.7143 | 0.0924 | 1.5042 | 46.1% | **FAIL (n=26)** — exact reproduction of Trial 12. OB-only entry rule (Trial 11) is bottleneck: cuts POI from 4739→2785 bars, CHoCH 205→101, trades 48→26. Strong PF confirmed (1.50). FVG entries must be restored to recover n. |
| 20 | 2026-06-16 | **4H+1H chain, 4yr, FVG+OB entries restored** — same as Trial 19 but with `--fvg-entries` flag: allows FVG-only zone entries (`price_in_poi` instead of `ob_for_price`). Restores Trial 8 baseline. 4yr holdout. | 4H+1H | 48 | 1.8400 | 0.0821 | 1.6367 | 47.9% | **FAIL (n=48, 2 short)** — near-identical to Trial 8 (n=47, PF=1.57). FVG restoration improves PF slightly (1.64 vs 1.57) without hurting quality. Year breakdown: 2022(n=4,50%), 2023(n=13,23%), 2024(n=7,57%), 2025(n=18,56%), 2026(n=6,67%). 2 trades short of gate — need 5yr data. |
| 21 | 2026-06-16 | **4H+1H chain, 5yr, FVG+OB entries** — same as Trial 20 but extended to 5yr holdout (2021-06-17 → 2026-06-16). HTF=4H (10950 bars), LTF=1H (43800 bars). Mitigation OFF, FVG+OB entries, 2R TP, side=both. | 4H+1H | **60** | 1.5294 | 0.0739 | **1.3754** | 43.3% | **✅ PASS** — **n=60≥50 ✓  net PF=1.38>1.0 ✓**. Avg fee_r=0.074R (vs 0.25R on 1H+5M — 3.4× cheaper). Expectancy=+0.226R/trade. Max DD=9.39R. Year: 2021(n=8,12%WR), 2022(n=8,50%), 2023(n=13,23%), 2024(n=7,57%), 2025(n=18,56%), 2026(n=6,67%). 2023 is a losing year (post-FTX bear-to-bull transition, n=13, net PF=0.54) but overall 5yr PF positive. **Proceed to Phase-1 paper trade (30 days, 100+ 1H bars monitored, no execution bugs).** Config: htf=4h ltf=1h, FVG+OB entries, displacement 1.0×ATR, mitigation OFF. |
| 13 | 2026-06-16 | **1H+5M chain + BSL/SSL pool TP** (default bot.py config): 1H swing bias + OB-only entry + Fib 50% → 5M sweep + 1.0×ATR displacement gate + CHoCH. **TP = BSL/SSL liquidity pool (min_r=1.5)** with 2R fallback. Mitigation OFF. Side=both. 2yr 5M holdout (2024-06 → 2026-06). Config: smc_bot/config.yaml. Fixed-2R baseline same config: n=200, gross PF=0.74, net PF=0.50. | 1H+5M | 194 | 0.7531 | 0.2689 | 0.5186 | 25.8% | **FAIL** — no gross edge (gross PF=0.75 < 1.0). BSL/SSL pool found 96% of the time (187/194); pool-based TP lowers win rate to 25.8% vs 27% on fixed-2R (pool targets are further than 2R → harder to reach). Both exit modes fail hard. **Key finding: confirms 1H+5M SMC chain has no gross edge on 2yr BTC data**, consistent with Trial 4 (n=301, gross PF=0.94). All prior 1H+5M trials (4, 13) FAIL. Funnel 2yr data: total=210k bars, bias=126k, fib=58k, poi=15k, sweep=10.5k, disp=7.4k, CHoCH=1285, pool=1243. Note: Trial 12 used 4H+1H (LTF=1H, 35k bars = 4yr); this trial is the 1H+5M config (LTF=5M, 210k bars = 2yr) — different chain. 4H+1H remains the only viable configuration (net PF=1.57, n=47). |

---

## Sensitivity Study — Trial 9 (2026-06-16)

4H+1H chain, 4yr holdout (2022-06 → 2026-06). `side=both` unless noted. Bug-fixed backtest.

| Mitigation | Side | n | Win% | Gross PF | Net PF | Expectancy (R) | Gate |
|---|---|---|---|---|---|---|---|
| none (OFF) | both  | 47 | 46.8% | 1.7600 | 1.5662 | +0.3227 | FAIL n<50 |
| none (OFF) | short | 27 | 55.6% | 2.5000 | 2.2524 | +0.5914 | FAIL n<50 |
| none (OFF) | long  | 22 | 40.9% | 1.3800 | 1.2189 | +0.1397 | FAIL n<50 |
| 50% midpoint | both  | 2  | 50%   | 2.00   | 1.9158 | +0.4679 | FAIL n<<50 |
| 50% midpoint | short | 1  | 100%  | ∞      | ∞      | +1.96   | FAIL n=1   |
| 50% midpoint | long  | 1  | 0%    | 0.00   | 0.00   | −1.02   | FAIL n=1   |
| 75% deep     | both  | 2  | 50%   | 2.00   | 1.9158 | +0.4679 | FAIL — identical to 50% |
| 75% deep     | short | 1  | 100%  | ∞      | ∞      | +1.96   | FAIL n=1   |
| 100% full    | both  | 4  | 25%   | 0.6667 | 0.6142 | −0.3074 | FAIL — zone quality collapses |
| 100% full    | short | 2  | 50%   | 2.00   | 1.8156 | +0.44   | FAIL n=2   |
| 100% full    | long  | 2  | 0%    | 0.00   | 0.00   | −1.05   | FAIL n=2   |

**Key conclusions:**
- 50% and 75% thresholds produce **identical** trade counts → the bottleneck is not between those levels; the surviving zones all lie above both thresholds.
- 100% (allow fully-consumed zones) adds a handful of trades but with negative expectancy — these are genuinely bad entries.
- Mitigation OFF is the only level that generates enough signal to even evaluate. The filter was intended to improve quality but at 4H scale it eliminates almost everything.
- **Next experiment:** compare wick-based vs close-based mitigation (current impl is wick-based). At 4H, wick penetration is common without conviction — close-based may be materially less aggressive.

---

## Trial 10 Matrix — Close-based vs Wick-based Mitigation (2026-06-16)

4H+1H chain, 4yr holdout (2022-06 → 2026-06). Baseline = Trial 8: n=47, net PF=1.5662, CHoCH=188.

### Bidirectional (side=both)

| Mitigation | n | Win% | Gross PF | Net PF | Expectancy | CHoCH | ZoneRej% | Gate |
|---|---|---|---|---|---|---|---|---|
| none (OFF) | **47** | 46.8% | 1.7600 | 1.5662 | +0.3227 | 188 | 0.0% | FAIL n<50 |
| wick 50% | 2 | 50.0% | 2.0000 | 1.9158 | +0.4679 | 3 | 76.5% | FAIL |
| wick 75% | 2 | 50.0% | 2.0000 | 1.9158 | +0.4679 | 3 | 74.4% | FAIL |
| wick 100% | 4 | 25.0% | 0.6667 | 0.6142 | −0.3074 | 12 | 72.0% | FAIL |
| close 50% | 5 | 40.0% | 1.3333 | 1.2169 | +0.1395 | 7 | 49.6% | FAIL |
| close 75% | 8 | 37.5% | 1.2000 | 1.0849 | +0.0567 | 14 | 45.4% | FAIL |
| **close 100%** | **14** | **42.9%** | **1.5000** | **1.3306** | **+0.2045** | **40** | **40.9%** | **FAIL** |

### Short-only

| Mitigation | n | Win% | Net PF | Expectancy |
|---|---|---|---|---|
| none (OFF) | 27 | 55.6% | 2.2524 | +0.5914 |
| close 50% | 3 | 66.7% | 3.6298 | +0.9451 |
| close 75% | 4 | 75.0% | 5.3798 | +1.1806 |
| close 100% | 7 | 85.7% | 10.6876 | +1.4922 |

### Long-only

All mitigation settings (wick or close, 50–100%): n=1–7, Win%=0%, Net PF=0.00, Expect=−1.04 to −1.08.

### Key findings

1. **Close-based is 2.5–4× less aggressive than wick** at same threshold (49.6% vs 76.5% zone rejection at 50%), directionally confirming the hypothesis.
2. **Gate still not reached**: best bidirectional result is close 100% at n=14 — far short of n=50.
3. **CHoCH→trades loss**: close 100% gives 40 CHoCH signals but only 14 trades (one-at-a-time gate kills 65% of signals). Without the one-trade limit, n would be ~40 — still below gate.
4. **Short quality under close-based is exceptional but n is meaningless**: close 75% gives 4 trades at 75% win, PF=5.38. Close 100% gives 7 at 85.7% win, PF=10.69. Too few to evaluate statistically.
5. **Long-only 0% win rate under any mitigation**: systematic — filter selects poor long entries or the direction asymmetry is real. Long bias appears structurally weaker than short bias throughout this dataset.
6. **Mitigation filter is incompatible with this chain at 4H**: The filter cannot simultaneously preserve quantity AND quality — any threshold that raises quality destroys count. The only viable operating point remains mitigation OFF.

### Verdict

**Trial 10 objective not met**: close-based does not preserve n≥50 at any threshold while maintaining PF. The mitigation filter experiment (Trials 6–10) is concluded. Finding: mitigation should be OFF for this chain.

---

## Diagnosis — 15m Trials (Trials 1–4)

**Root cause: fee floor kills marginal gross edge.**

- 15m BTC ATR ≈ 0.3% of price. Stop = 1.5×ATR → stop_frac ≈ 0.45%.
- Round-trip fee = 0.12% → fee/stop = 0.12/0.45 = **0.27R per trade minimum**.
- Actual avg: 0.31–0.35R (stop sizes vary with ATR; smaller ATR → higher fee ratio).
- Gross edge (Trial 1): +0.015R/trade. After 0.313R fee: **−0.298R/trade net**.
- For 15m BTC to survive at 1.5×ATR stop / 2.5R target: need win rate ≥ 37.1%. Delivered: 29%.

**Retest adds small value** (gross PF 1.023 vs 0.993 breakout-only) — not enough.

---

## What This Proves

The signal family (EMA trend + swing structure) cannot overcome the 15m fee floor.
This is consistent with the ag-auto-trade archive (A4 EMA momentum: gross PF 0.96 on H1).

The ARCHITECTURE is confirmed working: indicators, simulation, intrabar SL/TP, fee model.
Code: `ag-auto-trade/scripts/run_botv1_backtest.py` (portable to this project).

---

## Diagnosis — 4H+1H Trials (Trials 5–9)

**Current state (Trial 8 corrected baseline):**
- Chain WITHOUT mitigation: n=47, net PF=1.57. Barely fails gate (n<50 by 3 trades).
- Short bias has real edge: n=27, net PF=2.25, Expect=+0.59 — if gate passed, this is a deployable signal.
- Long bias is marginal: n=22, net PF=1.22, Expect=+0.14 — may or may not hold on fresh data.
- 2023 is the drag year: 23% win rate vs 53–67% in 2024–2026. Bears-to-recovery transition hurts.

**Mitigation filter verdict (Trial 9):**
- The 50% wick-based midpoint filter was designed to remove low-quality zones.
- At 4H, wick penetration to midpoint is routine without constituting a genuine mitigation.
- Result: 76.5% zone rejection, 97.4% bar-level drop, CHoCH collapses from 188 to 3.
- Mitigation OFF is the only level that generates enough signal to evaluate the chain.
- The filter is a bottleneck, not a quality improvement at this timeframe.

**What to try next (priority order — updated after Trial 10):**

Mitigation experiment (Trials 6–10) is complete. Finding: mitigation OFF is the only viable operating point. All further experiments run with mitigation OFF as the baseline.

The chain without mitigation produces n=47 (3 short of gate) at net PF=1.57. The problem is signal count, not quality.

**Updated after Trial 12 (displacement gate 1.5→1.0×ATR):**

Displacement relaxation increased funnel passes from 855→1432 (+67%) but CHoCH only moved 94→101 (+7) and trades stayed at n=26. CHoCH→trade conversion is 25.7% (one-at-a-time gate and absence of LTF OB retrace kill 74% of CHoCH signals). Displacement relaxation does not solve the count problem. The true bottleneck is post-CHoCH: price not retracing to a 5M OB.

**Full bottleneck picture (Trial 12 funnel):**
```
Total bars:       34,837
Bias non-neutral: 20,541  (59%)   — Fib gate kills 53%
Fib gate:          9,808  (28%)   — POI kills 72%
Price in OB:       2,785   (8%)   — sweep kills 24%
Sweep:             2,107   (6%)   — displacement kills 32%
Displacement:      1,432   (4%)   — CHoCH kills 93%
CHoCH:               101  (0.3%)  — one-at-a-time + no LTF OB kills 74%
Trades:               26
```

The CHoCH→trade gap is now the primary bottleneck. Two causes:
1. One-at-a-time gate: when a position is open, subsequent CHoCH signals are skipped.
2. Price does not retrace to a 5M OB after CHoCH fires (or no OB exists in 15-bar lookback).

To reach n≥50 at current CHoCH=101, need ~194 CHoCH signals (at 25.7% conversion) OR improve the post-CHoCH conversion rate.

Options after Trials 11–12:

| Priority | Option | Hypothesis | Risk |
|---|---|---|---|
| 1 | **Restore FVG as co-equal entry at 1H** (revert `ob_for_price`→`price_in_poi` in backtest; keep OB-only for execution in bot.py) | FVG entries had equal quality (PF matched OBs at Trial 8). With displacement now at 1.0×ATR, restoring FVG may push n above 47. | Risk: admits lower-conviction FVG-only entries. Hypothesis: quality survives. |
| 2 | **Restore FVG + revert displacement to 1.5×ATR** (full Trial 8 replication) | Baseline n=47, net PF=1.57 — well-understood. Use as clean reference before adding other changes. | Just re-runs known results unless displacement is also being tested. |
| 3 | **Remove Fib 50% gate** | Fib kills 53% of bias-confirmed bars (9808 from 20541). This is the second-largest filter. Test n and PF impact without it. | May admit entries far outside structural range — quality risk is high. |
| 4 | **5M OB retrace window expansion** | After CHoCH, lookback for 5M OB is 15 bars. Extending to 30–45 bars may capture more retraces. | Larger lookback → staler OBs; risk of overlap with new signals. |

**Owner decides next trial. Log the exact config here before running. Never tune a failing trial — always register a new one.**

---

## Research Note — videoplayback__1_.mp4 (2026-06-16)

**Source:** `videoplayback__1_.mp4` — ~36 min SMC lesson (TradingView screen-recording).
**Converted via:** `VIDEO_SMC_ENTRY_MODELS_SPEC.md` → `docs/research/VIDEO_ENTRY_MODELS.md`

**Finding:** The video teaches three entry models:
- **Model 1 (Displacement Trap)** = current confirmation entry (`structure → poi → liquidity → confirmation`). Already live and validated — Trial 21 Phase-0 PASS.
- **Model 2 (Refined OB limit / BASIL)** = NOT yet validated. Limit entry at a refined 5M OB inside the HTF OB, scored by BASIL checklist (Break of structure · Alignment · Sweep · Imbalance · Last candle). Requires own trial.
- **Model 3 (Breaker Block)** = NOT yet validated. Failed/violated OB flips polarity; enter on retest in new direction. Requires own trial + `poi.py` change to re-polarise violated zones.

**Session-timing bonus:** Video shows London/NY filter — NOT applicable to BTC (24/7 asset). Sprint 3 / Trial 17 confirmed session filter hurts BTC edge (net PF 0.34). `session.filter_enabled` remains `false`.

**[NEEDS-TRANSCRIPT]** All spoken SL/RR/score-threshold rules are unverifiable without audio transcript. Do not hard-code them.

**Stub created:** `smc_bot/entry_modes.py` (proposal-only, NotImplementedError, no executor/pybit imports).

---

## PENDING Trials — Trial 22/23/24 (management + filters on Trial 21 baseline)

| Trial | Date | Signal | TF | n | Gross PF | Avg fee (R) | Net PF | Win% | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 22 | 2026-06-17 | **Partial TP/BE** — Trial 21 baseline + `--partial-tp`: 50% close at 1R, move SL to breakeven, remainder runs to 2R full TP. Zero code changes. 5yr holdout. Command: `--htf BTCUSDT_240m.parquet --ltf BTCUSDT_60m.parquet --mitigation-pct none --fvg-entries --side both --partial-tp` | 4H+1H | **67** | 1.4038 | 0.0749 | **1.1986** | 61.2% | **✅ PASS** — n=67≥50 ✓ net PF=1.20>1.0 ✓. Partial TP raises trade count (67 vs 60 in T21) by resolving more signals before hit. Win rate jumps 43→61% because TP1 half-closes count as wins. Net PF drops 1.38→1.20 because partial wins average 0.5–0.8R vs 2R full wins. Expectancy +0.082R/trade. Max DD=11.25R. Year: 2021(9t,33%WR), 2022(8t,50%), 2023(14t,50%), 2024(7t,71%), 2025(22t,73%), 2026(7t,86%). **2023 drag year continues** (PF=0.65). |
| 23 | 2026-06-17 | **1H counter-bias filter** — Trial 22 + `--h1-bias-filter`: skip entry if 1H LTF structural bias (HH+HL / LL+LH using SWING_N) explicitly opposes 4H HTF bias. Neutral 1H passes. 5yr holdout. | 4H+1H | **48** | 1.5556 | 0.0707 | **1.3450** | 62.5% | **FAIL (n=48, 2 short)** — Gate misses by 2 trades. However quality metrics are the best seen: net PF 1.35 (highest), expectancy +0.138R (vs +0.082R T22), max DD 5.74R (vs 11.25R T22 — halved). Filter cuts 28% of trades (67→48) and removes most loss-cluster runs (2021 drops 9→4t, 2022 6→6t, 2023 14→12t). Year: 2021(4t,50%), 2022(6t,67%), 2023(12t,50%), 2024(5t,80%), 2025(15t,60%), 2026(6t,83%). **FINDING: 1H bias filter improves quality but undershoots n gate by 2. Needs 6yr data or combined with a trade-count-expanding change to cross gate.** |
| 24 | 2026-06-17 | **Fib distance tightening** — Trial 23 + `--fib-dist-min 0.01`: require price ≥1% away from fib equilibrium midpoint. 5yr holdout. | 4H+1H | **18** | 0.9444 | 0.0468 | **0.8575** | 50.0% | **FAIL (n=18, no edge)** — `--fib-dist-min 0.01` is far too aggressive. Cuts fib-passing bars from 12523→8270 (−34%), CHoCH signals from 265→87 (−67%), trades from 48→18 (−63%). Net PF 0.86 < 1.0. Fib distance tightening destroys signal count completely. **DO NOT retry at 0.01** — the fib gate already correctly sorts discount/premium; tightening within the half selects too few setups. |

---

## PENDING Trials — Video Entry Models

| Trial | Date | Signal | TF | n | Gross PF | Avg fee (R) | Net PF | Win% | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| V2 | PENDING | **Refined OB limit (BASIL)**: 4H+1H chain. HTF OB identified → drill to 1H refined OB inside it → BASIL score ≥ 3/5 → **limit entry** at refined OB top/bottom. SL below refined OB. [NEEDS-TRANSCRIPT] exact SL/RR/threshold. 5yr holdout (same as Trial 21). Gate: n≥50 AND gross PF>1.0. | 4H+1H | — | — | — | — | — | **PENDING** — requires `smc_bot/entry_modes.py::refined_ob_entry()` implementation + separate backtest trial. Do NOT merge PnL with Trial 21 (Model 1). |
| V3 | PENDING | **Breaker Block**: 4H+1H chain. Identify violated OBs (price closed strongly through zone) → flip polarity → enter on retest of breaker in new direction. [NEEDS-TRANSCRIPT] "strongly closes through" threshold, lookback, SL/RR. Requires `poi.py` change to re-polarise violated zones. 5yr holdout. Gate: n≥50 AND gross PF>1.0. | 4H+1H | — | — | — | — | — | **PENDING** — requires `smc_bot/entry_modes.py::breaker_entry()` implementation + `poi.py` breaker detection + separate backtest trial. Do NOT merge PnL with Trial 21 or V2. |
| 25 | 2026-06-17 | **Asian session signal** — 4H macro bias + 1H Asian box (00-08 UTC) → sweep / range / trend. Exit: 75% at box-edge/4R (SL→BE), runner at 5R. SL=±25% of box range. Config: smc_bot/config.yaml session.asian. 5yr holdout (--htf BTCUSDT_240m, --ltf BTCUSDT_60m). | 4H+1H | 921 | 0.5091 | 0.3696 | 0.3279 | 13.0% | **FAIL** |
| 26 | 2026-06-17 | **Asian session signal (SWEEP-ONLY)** — 4H macro bias + 1H Asian box (00-08 UTC) → sweep setup only (TREND/RANGE disabled). Exit: 75% at box-edge (SL→BE), runner at 5R. SL=±25% of box range. 5yr holdout. | 4H+1H | 66 | 1.5208 | 0.3642 | 0.9716 | 34.9% | **FAIL** — net PF 0.97 (gate miss 2.84%). Cause: structural fee drag (0.36R avg from 25%-of-box-range stop) + mean-reversion failure in strong trend (2024: 0/10, all losses). Year breakdown: 2021 net PF 9.13 (n=5), 2022 0.88, 2023 1.69, **2024 0.00**, 2025 0.84, 2026 1.38. 3/6 years FAIL. **Asian session signal family retired.** Return to 4H+1H SMC chain (T21/T22). |

---

## PENDING Trials — Forex Pivot (Step 5, validate-first)

Strategic fork (2026-06-18): **validate-first** + **forex replaces BTC**. The
session-box signal (`session_range.py`) is repointed from BTC (24/7, where it
died — Trials 25/26) to forex pairs that have a real session open. Cost model is
**forex** (spread + commission in pips, not Bybit %): see `docs/FOREX_VALIDATION.md`.
Run on the VPS — forex feeds are network-gated in the web container.
Runner: `python scripts/forex_phase0.py` (spread-sensitivity sweep; robust PASS =
clears n≥50 & net PF>1.0 at every spread level).

| Trial | Date | Signal | TF | n | Gross PF | Avg fee (R) | Net PF | Win% | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| FX-INFRA-1 | 2026-06-18 | **MT5 adapter infrastructure (Step 1)** — `metaapi-cloud-sdk>=29,<30` added to requirements.txt (validated on 29.1.1); .env.example gets METAAPI_TOKEN + METAAPI_ACCOUNT_ID stubs; `tests/test_mt5_account.py` symbol scope narrowed to [EURUSD, GBPUSD]. No trading logic; no broker adapter. Fee model for VT Markets = spread + commission (not Bybit 0.06% taker) — applied in Step 2+. | N/A | — | — | — | — | — | **INFRA** — Connectivity gate: `python tests/test_mt5_account.py` must exit 0 before Step 2. |
| 27 | 2026-06-18 | **EURUSD session box** — 4H macro bias + 1H box (00-08 UTC, Asian range) → sweep/range/trend. Cost=forex (spreads 0.8/1.2/2.0 pip + 0.6 pip commission rt). 5yr holdout (2021-06-20 → 2026-06-18, 7781×4H / 31120×1H bars via VT Markets MT5/MetaAPI). Runner: `scripts/forex_phase0.py`. | 4H+1H | sweep: 108 / all: 685 | sweep: 0.69 / all: 0.51 | — | sweep: 0.58 (0.8p) / 0.46 (2.0p) / all: 0.45→0.37 | sweep: 24% | **FAIL** — No mode clears gate at any spread. Best: sweep n=108 net PF=0.58 at 0.8pip (gate=1.0). Range: n=0 (zero signals). Trend: n=580, net PF=0.44→0.37. Session-box signal has no edge on EURUSD. **Forex strategy layer retired per CLAUDE.md §1. Do not tune.** |
| 28 | 2026-06-18 | **GBPUSD session box** — same chain/exit as Trial 27, wider spread (1.2 pip GBPUSD start). Cost=forex (spreads 0.8/1.2/2.0 pip + 0.6 pip commission rt). 5yr holdout (same dates, 7781×4H / 31120×1H bars). | 4H+1H | sweep: 119 / all: 635 | sweep: 1.05 / all: 0.56 | — | sweep: 0.95 (0.8p) / 0.80 (2.0p) / all: 0.51→0.43 | sweep: 26% | **FAIL** — No mode clears gate. Best: GBPUSD sweep n=119 net PF=0.95 at 0.8pip (closest — misses 1.0 by 5%). Range: n=0. Trend: n=523, net PF=0.43→0.37. Gate miss is not material; GBPUSD sweep best-case is still sub-1.0 gross at wider spreads. **Forex strategy layer retired per CLAUDE.md §1.** |
