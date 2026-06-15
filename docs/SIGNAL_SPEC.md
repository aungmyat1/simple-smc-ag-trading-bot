# Signal Specification — Trial 3

**Strategy ID:** `A1_SMC_1H5M_CONFIRMATION`
**Status:** PENDING Phase-0 gate
**Locked:** 2026-06-15 — do not change parameters; any change = new trial

---

## Core Thesis

Trade only when five sequential conditions align:

```
1H POI (location)
    +
5M Liquidity Sweep (trap)
    +
5M MSS / CHoCH (confirmation)
    +
5M OB/FVG Retest (execution zone)
    =
Trade Entry
```

Hypothesis: the 1H context filter increases trade quality enough to overcome
the 5M fee floor, versus scanning 15m blindly (Trials 1 & 2 — net PF 0.64–0.68).

---

## WHERE Layer — 1H (HTF)

**Bias (must be bullish to take longs):**
- `close[1H] > EMA200` on the 1H chart
- EMA200 slope positive over the last 5 bars

**POI Zones — bullish entry zones:**
1. **Order Block (OB):** last bearish 1H candle immediately before a
   displacement bar (range ≥ 1.5 × ATR14) that closes above the prior
   20-bar swing high. Zone = OB body (open–close), active for ≤ 50 bars.
2. **Fair Value Gap (FVG):** bullish — `low[i] > high[i-2]`. Zone = the gap.
   Active for ≤ 50 bars.

**Discount filter:**
- Fibonacci midpoint of last 100-bar swing range = equilibrium (50% level)
- Long entries only when current 5M price ≤ fib50 (below midpoint = discount)

**Liquidity targets (TP runner):**
- Equal highs on 1H (within 0.1% tolerance, ≥ 2 instances) = buy-side pool

---

## WHEN Layer — 5M (LTF), sequential stages

### Stage 1 — Liquidity Sweep

In the last 20 5M bars:
- A bar's low pierces the prior 10-bar swing low by ≥ 0.03%
- That same bar closes **above** the pierced level (trap + recovery)

### Stage 2 — Displacement (implied by OB formation)

The bar immediately after the sweep must have:
- Range ≥ 1.5 × ATR14 on 5M
- Closes above the recent 10-bar 5M swing high

This bar's preceding bearish candle defines the **5M OB zone**.

### Stage 3 — MSS / CHoCH

After the sweep bar:
- `close > max(high[sweep_bar : current])` — a prior swing high is broken
- This is the Change of Character (CHoCH) confirming trend flip

### Stage 4 — Retest into Execution Zone

Current 5M bar must overlap a **fresh 5M bullish OB or FVG**
(formed by the displacement, active ≤ 30 bars).

All four stages required simultaneously. Default = NO_TRADE.

---

## Execution

| | Value |
|---|---|
| **Entry** | Close of signal bar (live: open of next bar) |
| **SL** | 0.1% below sweep wick low (structural) |
| **TP1 (50%)** | entry + 1R — move SL to breakeven |
| **TP2 (25%)** | entry + 2R |
| **Runner (25%)** | nearest 1H equal highs above entry, or entry + 3R |
| **Risk/trade** | 0.5% of account (config.RISK_PER_TRADE) |

---

## Research Questions (to answer via backtest)

| ID | Question |
|---|---|
| RQ-1 | Does Sweep + MSS + Retest outperform the single-TF OB+CHoCH (Trial 3 single-TF)? |
| RQ-2 | Which execution zone is best? OB-only vs FVG-only vs OB+FVG vs 50% Fib |
| RQ-3 | Which HTF filter is best? OB-only vs FVG-only vs OB+FVG vs discount-only |
| RQ-4 | Does 5M execution survive fees better than 15m? (fee/stop ratio on 5M structural SL) |

---

## What This Is NOT

- Not a replacement for the existing Phase-0 gate process
- Not proof of edge — backtest result determines verdict
- NOT enabled for live trading until Phase-1 (30 days paper) also passes

**Next step:** Run `python scripts/backtest.py` after fetching 5M and 1H data.
Log result in `VERDICT_LOG.md` row 3 — whether PASS or FAIL.
