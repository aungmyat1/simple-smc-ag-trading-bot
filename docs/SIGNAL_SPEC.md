# Signal Specification — Trial 3

**Strategy ID:** `A1_SMC_1H5M_CONFIRMATION` (Sniper Entry Method)
**Source:** 1BullBear Episode 15A — Smart Money Concepts Sniper Entry
**Status:** PENDING Phase-0 gate
**Locked:** 2026-06-15 — do not change parameters; any change = new trial

---

## Core Philosophy

> "Do not predict — react. Wait for smart money to reveal its hand through a
> liquidity sweep, a structure shift, and a precise retest. Then enter with a
> tight stop, structured targets, and disciplined risk management."

---

## Timeframe Hierarchy

| Role | TF | Purpose |
|------|----|---------|
| Bias | 1H (HTF) | EMA200 trend direction + POI zones + discount/premium |
| Execution | 5M (LTF) | Liquidity sweep / inducement + CHoCH + displacement |
| Entry zone | 5M OB / FVG | Exact limit entry after retrace |

Bot runs longs only (bearish side added if Trial 3 passes Phase-0 and Phase-1).

---

## Entry Sequence (ALL conditions AND-gated — one missing = NO TRADE)

### Pre-filter
- [ ] **Session filter**: London (07–12 UTC) or New York (13–21 UTC) only.
      Asian session excluded — low volume, choppy structure.

### Stage 1 — HTF Bias (1H)
- [ ] `close[1H] > EMA200` AND EMA200 slope positive over last 5 bars → bullish
- [ ] 1H bullish POI zone exists: **Order Block** or **FVG**
      - OB: last bearish 1H candle before a 1.5×ATR displacement that closes above prior swing high
      - FVG: `high[i-2] < low[i]` (gap between bar i-2 high and bar i low)
      - Zone active for ≤ 50 1H bars
- [ ] Current 5M price ≤ 1H Fibonacci 50% (in **discount** zone)

### Stage 2 — Inducement / Liquidity Sweep (5M)
- [ ] In last 20 5M bars: a bar's low pierced the prior 10-bar swing low by ≥ 0.03%
      AND that bar **closed above** the pierced level
- This is the inducement — **do not enter until the sweep is visible**
- SL will sit 0.1% below the sweep wick extreme

### Stage 3 — Displacement (5M, post-sweep)
- [ ] In the 6 bars after the sweep: at least one bar has range ≥ 1.5 × ATR14
- Confirms institutional participation — absence = no valid setup

### Stage 4 — CHoCH / MSS (5M)
- [ ] After the sweep bar, `close > max(high[sweep_bar:current])` — prior swing high broken
- This is the Change of Character confirming the structural trend flip

### Stage 5 — Execution Zone (5M OB or FVG)
- [ ] Current 5M bar overlaps a fresh 5M bullish OB or FVG formed by the displacement
      (active ≤ 30 5M bars)
- Enter on retrace into this zone — not on the CHoCH close itself

### Stage 6 — Minimum R:R Gate
- [ ] Runner target ≥ 2R from entry above the stop loss
- If no HTF liquidity pool satisfies this, trade is rejected

---

## Execution

| | Value | Source |
|---|---|---|
| **Entry** | Close of signal bar (live: open of next 5M bar) | Stage 5 retest |
| **SL** | 0.1% below sweep wick low | Inducement extreme |
| **TP1 (50%)** | entry + 2R → move SL to breakeven | Partial lock-in |
| **TP2 (25%)** | entry + 3R | Internal liquidity |
| **Runner (25%)** | Nearest 1H equal highs above entry, or entry + 4R | External liquidity |
| **Risk/trade** | 0.5% of account (config.RISK_PER_TRADE) | Non-bypassable |
| **Minimum R:R** | 1:2 to runner | Reject signal if < 2R |

---

## Risk Guards

| Guard | Threshold | Action |
|---|---|---|
| Daily loss | 2% of day-start equity | Halt for the day |
| Max drawdown | 10% from peak | Kill switch |
| Consecutive losses | 2 in a row | Halt until winning trade resets |

Consecutive loss guard resets to 0 on any winning exit (TP1/TP2/runner).
Breakeven exits (SL-BE) do not count as a loss.

---

## Common Mistakes This Spec Guards Against

1. **Entering before the sweep** → Stage 2 required
2. **No displacement** → Stage 3 required (confirms institutions, not retail noise)
3. **Trading mid-range** → Stage 1 discount filter prevents buying at 50%+ Fib
4. **Asian session chop** → Stage 0 session filter
5. **Stop too tight at inducement level** → SL is 0.1% BELOW the wick, not AT it
6. **Bad R:R** → Stage 6 minimum 1:2 gate

---

## Research Questions (to answer via backtest)

| ID | Question |
|---|---|
| RQ-1 | Does Sweep + Displacement + MSS + Retest outperform prior SMC attempts (A5: FRAGILE)? |
| RQ-2 | Does the session filter help or hurt trade count (n≥50 gate risk)? |
| RQ-3 | OB-only vs FVG-only vs combined — which execution zone has higher net PF? |
| RQ-4 | 2R/3R/4R TP ratios vs original 1R/2R/3R — impact on net PF with 3-tier partials |

---

## What This Is NOT

- Not a prediction system — reaction only
- Not proven — Phase-0 gate determines verdict
- NOT live until Phase-0 AND Phase-1 (30 days paper) both pass

**Next step:** Fetch data, run `python scripts/backtest.py`, log result in VERDICT_LOG.md row 3.
