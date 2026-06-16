"""
entry_modes.py — PROPOSE-ONLY stub for SMC entry model candidates.

Three entry models from VIDEO_SMC_ENTRY_MODELS_SPEC.md / docs/research/VIDEO_ENTRY_MODELS.md:
  Model 1: Displacement Trap (confirmation) — LIVE in bot.py / backtest.py (Trial 21 PASS)
  Model 2: Refined OB Limit (BASIL scoring) — PENDING trial, not wired
  Model 3: Breaker Block                    — PENDING trial, not wired

RULES:
  - This file must NOT import executor, pybit, ccxt, or any exchange SDK.
  - Do NOT call these from bot.py or backtest.py until each passes its own
    gross-PF > 1.0 holdout trial and is approved in VERDICT_LOG.md.
  - [NEEDS-TRANSCRIPT] items (SL, RR, BASIL threshold) must not be hard-coded
    until the video transcript is provided and rules are explicitly cited.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Model 1 — Displacement Trap Entry (confirmation)
# ---------------------------------------------------------------------------

def confirmation_entry(
    bias: str,
    price: float,
    pois: list,
    sweep_result: dict | None,
    choch_result: dict | None,
) -> dict | None:
    """
    Confirmation (displacement trap) entry: HTF bias → POI → sweep → CHoCH.

    This is the LIVE path implemented in bot.py / backtest.py (Trial 21).
    This stub exists for documentation parity only — do not call it; use the
    live chain in bot.py instead.

    Returns:
        Entry dict with keys {side, entry_price, sl, tp, reason} or None if no signal.

    [REPO] Fully implemented: structure → poi → liquidity → confirmation.
    [FRAME] = "Displacement Trap Entry" (video Model 1).
    """
    raise NotImplementedError(
        "confirmation_entry is documented here for parity only. "
        "Use the live chain in smc_bot/bot.py (Step 3 → Step 11)."
    )


# ---------------------------------------------------------------------------
# Model 2 — Refined Order Block Entry (BASIL scoring)
# ---------------------------------------------------------------------------

def refined_ob_entry(
    bias: str,
    price: float,
    htf_ob: dict,
    ltf_bars: list,
    basil_min_score: int = 3,
) -> dict | None:
    """
    Refined OB limit entry: drill the HTF OB to a 5M/15M OB inside it,
    score with BASIL checklist, place limit at the refined zone.

    BASIL checklist [FRAME]:
      B — Break of Structure before OB
      A — Alignment with HTF trend (discount/premium)
      S — Sweep of prior liquidity
      I — Imbalance (FVG) left by displacement candle
      L — Last candle before displacement

    Args:
        bias:           'bullish' | 'bearish'
        price:          current price
        htf_ob:         HTF (4H) Order Block dict from poi.get_pois()
        ltf_bars:       LTF (1H or 5M) OHLCV list within the HTF OB time window
        basil_min_score: minimum BASIL hits required (default 3/5; [NEEDS-TRANSCRIPT])

    Returns:
        Entry dict with keys {side, limit_price, sl, tp, basil_score} or None.

    [NEEDS-TRANSCRIPT] Exact SL placement, RR target, minimum BASIL score,
    and entry-trigger candle rule are audio-only in the source video.

    Status: PENDING — requires own gross-PF > 1.0 holdout trial before activation.
    """
    raise NotImplementedError(
        "refined_ob_entry (Model 2 / BASIL) is not yet implemented. "
        "Run its own backtest trial and log the result in VERDICT_LOG.md "
        "before wiring this into bot.py."
    )


# ---------------------------------------------------------------------------
# Model 3 — Breaker Block Entry
# ---------------------------------------------------------------------------

def breaker_entry(
    bias: str,
    price: float,
    violated_obs: list,
) -> dict | None:
    """
    Breaker block entry: a failed/violated OB flips polarity and becomes a
    POI in the opposite direction; enter on the retest of the flipped zone.

    Definition [FRAME]: price closes strongly through an OB (the OB fails),
    absorbing the liquidity. That failed OB becomes a 'breaker' — a supply zone
    in bullish context becomes a demand breaker (and vice-versa) on retest.

    Args:
        bias:           'bullish' | 'bearish' — NEW direction after the break
        price:          current price
        violated_obs:   list of OB dicts that price has closed through (from poi.py)

    Returns:
        Entry dict with keys {side, entry_price, sl, tp, breaker_zone} or None.

    [NEEDS-TRANSCRIPT] Exact "strongly closes through" threshold (% of OB body),
    lookback window, and SL/RR rules are audio-only in the source video.

    [REPO] Requires poi.py change: mitigated/violated zones currently filtered out;
    breaker logic would re-polarise them instead of discarding them.

    Status: PENDING — requires own gross-PF > 1.0 holdout trial before activation.
    """
    raise NotImplementedError(
        "breaker_entry (Model 3) is not yet implemented. "
        "Requires poi.py change to re-polarise violated OBs, plus its own "
        "backtest trial logged in VERDICT_LOG.md before activation."
    )
