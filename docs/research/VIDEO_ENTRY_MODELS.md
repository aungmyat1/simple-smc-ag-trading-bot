# VIDEO_ENTRY_MODELS.md — SMC Entry Model Research Note

**Source:** `videoplayback__1_.mp4` (~36 min, TradingView screen-recording)
**Converted:** 2026-06-16 | Tags: `[FRAME]` verified on-screen text · `[REPO]` from codebase · `[NEEDS-TRANSCRIPT]` audio-only

---

## A. Provenance — verified from the file

- `[FRAME]` 640×360 H.264 + AAC, 2167 s (~36m), TradingView screen-recording with presenter face-cam (bottom-right).
- `[FRAME]` Produced lesson, not a single replay. Title cards + on-screen definition text. Section structure:
  1. Intro title card: **retail entry ✗ vs institutional entry ✓**
  2. **Displacement Trap Entry** (header "EM 🎯 – Displacement Trap Entry")
  3. **Refined Order Block Entry** (BASIL checklist box shown on the right)
  4. **Breaker Block Entry** (header "EM 🧱 – Breaker Block Entry", definition slide)
  5. **Bonus: Session Timing Filter + live trade example**
- `[FRAME]` Chart markup: demand/supply zones, liquidity rays, TradingView long/short position tool (entry · SL · TP) drawn on each example.
- `[NEEDS-TRANSCRIPT]` Exact instrument(s)/timeframe per section, and every spoken numeric rule.

---

## B. Three entry models — repo mapping

### Model 1 — Displacement Trap Entry `[FRAME]` + `[REPO]`

**Status: LIVE (Trial 21, Phase-0 PASS)**

- Sequence: HTF bias → price into POI → liquidity **sweep** → **CHoCH** → enter on the shift.
- Safest of the three; confirmation entry only after institutional intent is visible.
- `[REPO]` Fully encodes the existing 15-step chain: `structure → poi → liquidity → confirmation`.
- Nothing to build. Validated at n=60, net PF=1.38 on 5yr BTC 4H+1H holdout.

### Model 2 — Refined Order Block Entry (BASIL) `[FRAME]` + `[REPO]`

**Status: PENDING — requires its own gross-PF > 1.0 holdout trial**

- `[FRAME]` Refine the HTF OB down to a 5M/15M OB inside it; enter via **limit order** at the refined zone.
- `[FRAME]` Quality scored with **BASIL** checklist:
  - **B** — Break of Structure (BoS before the OB forms)
  - **A** — Alignment with trend (OB in discount/premium zone matching bias)
  - **S** — Sweep of liquidity (prior swing swept before OB forms)
  - **I** — Imbalance present (FVG left by the displacement candle)
  - **L** — Last candle before displacement (OB is the last candle before the strong move)
- Higher BASIL score = stronger OB.
- Trade-off `[REPO]`: tighter stop / higher RR, but **no confirmation** (limit fires on touch, not on CHoCH) → lower win rate vs Model 1 (aggressive entry).
- `[REPO]` Repo path: a new **limit-entry mode** in `smc_bot/entry_modes.py` → `refined_ob_entry()`. NOT currently wired into `bot.py` or `backtest.py`. Requires own trial before activation.
- `[NEEDS-TRANSCRIPT]` Exact SL placement, minimum BASIL score threshold, RR target.

### Model 3 — Breaker Block Entry `[FRAME]` + `[REPO]`

**Status: PENDING — requires its own gross-PF > 1.0 holdout trial**

- `[FRAME]` Definition: a Breaker forms when price **fails to respect** an Order Block and closes strongly through it; that failed OB flips into a POI in the **opposite direction**, because the liquidity there has been absorbed.
- Use: enter on the **retest** of the failed/flipped OB, in the new direction.
- `[REPO]` Repo path: a `poi` variant that detects a **mitigated/violated OB** and re-polarises it. Currently `poi.py` marks mitigated zones as stale (filtered out). Breaker logic would instead flip them. NOT wired. Requires own trial.
- `[NEEDS-TRANSCRIPT]` Exact "strongly closes through" threshold (% of OB body), lookback window, SL/RR rules.

---

## C. The session timing bonus — BTC trap `[FRAME]` + `[REPO]`

`[FRAME]` The video's bonus section demonstrates a **London/NY killzone filter** on what appears to be a forex-style chart.

`[REPO]` **This does NOT apply to BTC.** Reason:
- BTC trades 24/7. London/NY session boundaries are not structural liquidity events the same way they are in FX.
- Sprint 3 (Trial 17) tested London/NY filter on 1H+5M BTC → net PF dropped to 0.34 (worse than unfiltered).
- `REPO_UPGRADE_PLAN.md` U3 explicitly removed the forex session filter and replaced it with an ATR-floor chop guard.
- `config.yaml` retains `session.filter_enabled: false` — untested on 4H+1H, kept wired but off.

=> **Do NOT enable a London/NY killzone into the BTC path.** If session filter is ever re-evaluated, it requires its own trial on the 4H+1H chain, not just activation in config.

---

## D. Open items — audio transcript required

- `[NEEDS-TRANSCRIPT]` Per-model: exact SL placement rule, RR target, entry-trigger candle definition.
- `[NEEDS-TRANSCRIPT]` Which sessions the presenter trades and on which instruments.
- `[NEEDS-TRANSCRIPT]` Live-trade example symbol/result (treat as anecdote until validated on our holdout).
- `[NEEDS-TRANSCRIPT]` Any win-rate/PF claims (marketing until replicated on our own 5yr BTC data).

> Provide a `.srt` transcript file and re-run: each `[NEEDS-TRANSCRIPT]` becomes a verifiable cited rule.

---

## E. Dispatch record

Task completed per `VIDEO_SMC_ENTRY_MODELS_SPEC.md` DO block:
- [x] Research note written (this file)
- [x] `smc_bot/entry_modes.py` stub created (3 signatures, NotImplementedError, no executor/pybit)
- [x] `VERDICT_LOG.md` appended: research note + PENDING rows for Models 2 and 3
- [x] `tests/test_ast_guard.py` extended: `entry_modes.py` in PROPOSE_ONLY guard
- [ ] Models 2 and 3 are PENDING — each needs its own backtest trial before activation
