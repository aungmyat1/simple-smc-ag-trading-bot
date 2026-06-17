"""
entry_modes.py — PROPOSE-ONLY entry model implementations and legacy stubs.

Section A: Working implementations (displacement_trap / refined_ob / breaker)
  Used by signal.py's generate_signal(). No executor imports.

Section B: Legacy stubs (confirmation_entry / refined_ob_entry / breaker_entry)
  Raise NotImplementedError. Retained for test_ast_guard compatibility.

RULES:
  - This file must NOT import executor, pybit, ccxt, or any exchange SDK.
  - Stub functions (Section B) are documentation only — do not call them.
  - Working implementations (Section A) are wired to signal.generate_signal().
    Each EXPERIMENTAL mode still needs its own gross-PF > 1.0 holdout trial.
"""
from __future__ import annotations

import pandas as pd


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


# ---------------------------------------------------------------------------
# Section A — Working implementations (signal.py seam — PROPOSE-ONLY)
# Each returns an EntryProposal(entry, stop, kind, mode) or None.
# ---------------------------------------------------------------------------


class EntryProposal:
    __slots__ = ("entry", "stop", "kind", "mode")

    def __init__(self, entry: float, stop: float, kind: str, mode: str) -> None:
        self.entry = entry
        self.stop = stop
        self.kind = kind    # "market" | "limit"
        self.mode = mode    # which model fired


def displacement_trap(
    price: float,
    sweep: dict,
    bias: str,
    buffer: float,
) -> EntryProposal | None:
    """Model 1 — market entry at CHoCH price, SL beyond sweep wick."""
    if sweep is None:
        return None
    wick = sweep["wick_extreme"]
    if bias == "bullish":
        return EntryProposal(price, wick * (1 - buffer), "market", "displacement_trap")
    return EntryProposal(price, wick * (1 + buffer), "market", "displacement_trap")


def refined_ob(
    active_poi: dict | None,
    bias: str,
    buffer: float,
) -> EntryProposal | None:
    """Model 2 — limit entry at POI midpoint, SL beyond POI boundary. EXPERIMENTAL."""
    if active_poi is None:
        return None
    lo, hi = active_poi["low"], active_poi["high"]
    mid = (lo + hi) / 2.0
    if bias == "bullish":
        return EntryProposal(mid, lo * (1 - buffer), "limit", "refined_ob")
    return EntryProposal(mid, hi * (1 + buffer), "limit", "refined_ob")


def breaker(
    df_1h: pd.DataFrame,
    bias: str,
    buffer: float,
    lookback: int = 60,
) -> EntryProposal | None:
    """
    Model 3 — limit at a flipped (violated) OB boundary. EXPERIMENTAL.
    Returns None unless a clean violation+flip is found in the lookback window.
    """
    o, h, l, c = (df_1h[x].values for x in ("open", "high", "low", "close"))
    n = len(df_1h)
    start = max(1, n - lookback)
    if bias == "bullish":
        for i in range(start, n - 1):
            if c[i] < o[i]:
                top = h[i]
                broke = any(c[j] > top for j in range(i + 1, n))
                if broke and l[-1] <= top:
                    return EntryProposal(top, l[i] * (1 - buffer), "limit", "breaker")
    else:
        for i in range(start, n - 1):
            if c[i] > o[i]:
                bot = l[i]
                broke = any(c[j] < bot for j in range(i + 1, n))
                if broke and h[-1] >= bot:
                    return EntryProposal(bot, h[i] * (1 + buffer), "limit", "breaker")
    return None


MODES = {
    "displacement_trap": "Model 1 — confirmation entry (live default)",
    "refined_ob":        "Model 2 — refined OB limit (EXPERIMENTAL)",
    "breaker":           "Model 3 — breaker block (EXPERIMENTAL)",
}


# ---------------------------------------------------------------------------
# Section B — Legacy stubs (raise NotImplementedError; kept for test compat)
# ---------------------------------------------------------------------------
