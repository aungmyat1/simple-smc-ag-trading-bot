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
