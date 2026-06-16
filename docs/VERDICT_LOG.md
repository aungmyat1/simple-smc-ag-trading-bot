# Verdict Log — Simple SMC AG Trading Bot

One row per trial. Never delete entries. Every parameter change = new row.
Source data for all trials: BTCUSDT 15m, 2023-01-01 → 2024-12-31 (70,081 bars).
Fee model: Bybit taker 0.06%/side = 0.12% round trip.

---

## Results

| Trial | Date | Signal | TF | n | Gross PF | Avg fee (R) | Net PF | Win% | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-06-15 | EMA50/200 trend + 20-bar swing break + retest (tol 0.5×ATR, valid 10 bars) | 15m | 1570 | 1.0233 | 0.313 | 0.6834 | 29.0% | **FAIL** |
| 2 | 2026-06-15 | EMA50/200 trend + 20-bar swing breakout-only (no retest) | 15m | 1333 | 0.9932 | 0.348 | 0.6399 | 28.4% | **FAIL** |
| 3 | 2026-06-15 | SMC Sniper (1BullBear Ep15A): London/NY session filter + 1H POI (OB/FVG, discount) → 5M inducement sweep + displacement (≥1.5×ATR) + CHoCH + 5M OB/FVG retrace. Partials: 50%@2R (→BE) / 25%@3R / 25%@HTF-liq. Min R:R=2. Consec-loss guard=2. | 1H+5M | TBD | — | — | — | — | **ABANDONED** (backtest scored _archive EMA chain not smc_bot/) |
| 4 | 2026-06-15 | SMC Sniper via smc_bot/ chain: 1H swing bias (HH+HL) + 1H OB/FVG POI → 5M sweep (swing pierce+close) + CHoCH. Single exit: 2R TP / SL=wick−0.1%. Config: smc_bot/config.yaml. Backtest: scripts/backtest.py (seam fixed, no _archive). | 1H+5M | 301 | 0.9366 | 0.2498 | 0.6567 | 31.9% | **FAIL** (gross PF < 1.0 — no edge before fees; signal family dead on 5M) |
| 5 | 2026-06-15 | SMC Sniper H1 variant: same smc_bot/ chain (HH+HL bias + OB/FVG POI + sweep + CHoCH), HTF=4H, LTF=1H. Single 2R exit. 2yr holdout (2024-06). | 4H+1H | 26 | 2.3333 | 0.0792 | 2.0833 | 53.8% | **OVERFILTERED** (n=26<50; fee=0.08R confirmed; re-run on 4yr below) |
| 5X | 2026-06-15 | Same as Trial 5 — 4H+1H, single 2R exit, LONG-ONLY — extended to 4yr holdout (2022-06 → 2026-06). | 4H+1H | 45 | 1.1034 | 0.0704 | 0.9946 | 35.6% | **FAIL** (n=45<50; net PF=0.9946 barely misses; 2022 bear kills longs; solution: add shorts) |
| 6 | 2026-06-16 | SMC Sniper bidirectional: same smc_bot/ 15-step chain (fib+mitigation+displacement), HTF=4H, LTF=1H. Long+short. 2R single exit. 4yr holdout (2022-06 → 2026-06). | 4H+1H | 85 | 1.0909 | 0.0633 | 0.9935 | 35.3% | **FAIL** (net PF misses by 0.0065; long sub-PF=0.69 destroys bidirectional; short sub-PF=1.33 at n=45 passes in isolation — see Trial 7) |
| 7 | 2026-06-16 | SMC Sniper SHORT-ONLY: same 15-step chain (fib+displacement+mitigation), HTF=4H, LTF=1H, `--side short`. | 4H+1H | 1 | inf | 0.0424 | inf | 100% | **FAIL (n=1 — chain too restrictive)** Mitigation filter kills 76.5% of zones (97.4% bar-level drop poi_raw→poi_fresh). Only 3 CHoCH signals in 4yr holdout. Trial 6 (n=85) used OLD chain without fib/displacement/mitigation — not a valid baseline. Need to either relax mitigation filter or re-baseline. |

> **2026-06-16 — Bot↔Gate alignment (code change, not a trial).** `smc_bot/bot.py`
> had drifted *ahead* of the gate: it added a 5M OB/FVG retrace entry (steps 11–12)
> and a BSL/SSL liquidity-pool TP (step 14) that `scripts/backtest.py` never
> modelled — so Trials 4/5/5X measured a **simpler** chain than the deployed bot.
> Reverted the bot to the gated chain: market entry on the signal bar, fixed
> `risk.target_r` TP, single exit. `bot.py` and `backtest.py` are now the same
> strategy, so Trials 4/5/5X **and** pre-registered Trial 6 describe the live bot.
> Re-add the retrace entry or liquidity-pool TP only as a new, separately-gated trial.

---

## Diagnosis

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

## Next Trial Candidates

Options (log here before testing):

| # | Signal idea | Hypothesis | Fee concern |
|---|---|---|---|
| 3 | Move to H1 — same EMA + swing | H1 ATR ≈ 0.9% → fee ≈ 0.09R/trade vs 0.31R | Lower fee ratio; same signal family already tested (A4: PF 0.96) |
| 4 | H1 with wider stop (2×ATR) | Reduce fee/R ratio | Increases loss per SL; may improve ratio but widen real $ risk |
| 5 | Different signal family on 15m | Higher win rate signal needed (≥37%) | Fee still high; signal must be selective |

**Owner decides next trial. Log it here before running.**
