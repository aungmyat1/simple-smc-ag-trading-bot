"""
Signal parity test — Phase B integrity check.

Runs BOTH the backtest signal path (_archive/bot_v1/signal.py)
and the live-bot signal path (smc_bot/{structure,poi,liquidity,confirmation})
over the same ≥500 cached 5M + 1H bars and asserts identical fire/no-fire.

When a bar fires in either path the test also compares the SL level.

If this test FAILS → the two implementations diverge and Phase-C Trial-3
results must be labelled PROVISIONAL until the owner picks the source of truth.

Usage:
    python -m pytest tests/test_signal_parity.py -v
    python tests/test_signal_parity.py          # standalone, prints full report
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Import both paths ─────────────────────────────────────────────────────────

from _archive.bot_v1.signal import get_htf_context, get_ltf_signal
from _archive.bot_v1 import config as arc_cfg

from smc_bot import confirmation, liquidity, poi, structure

# ── Config constants (live path — mirror smc_bot/config.yaml) ────────────────
_LIV_SWING_N     = 5      # structure.get_bias swing_n
_LIV_OB_LB       = 50     # poi ob_lookback
_LIV_FVG_LB      = 30     # poi fvg_lookback
_LIV_DISP_ATR    = 1.5    # poi displacement_atr
_LIV_LIQ_SWING_N = 3      # liquidity swing_n
_LIV_LIQ_LB      = 30     # liquidity lookback
_LIV_CHOCH_LB    = 10     # confirmation lookback
_LIV_SL_BUF      = 0.001  # risk.sl_buffer

# ── HTF alignment (identical to scripts/backtest.py) ─────────────────────────

def _align_htf(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> np.ndarray:
    """For each 5M bar return the iloc of the last complete 1H bar."""
    htf_ts = df_1h["ts"].values
    ltf_ts = df_5m["ts"].values
    result = np.full(len(ltf_ts), -1, dtype=int)
    min_htf = arc_cfg.HTF_BARS // 2
    for i, ts in enumerate(ltf_ts):
        idx = np.searchsorted(htf_ts, ts, side="left") - 1
        result[i] = idx if idx >= min_htf else -1
    return result


# ── Live path wrapper ─────────────────────────────────────────────────────────

def _live_signal(df_1h_w: pd.DataFrame, df_5m_w: pd.DataFrame) -> dict:
    """
    Run the live-bot signal pipeline and return a parity-comparable dict:
        {"action": "LONG"|"FLAT", "sl": float|None, "stages": dict}

    stages = per-stage pass/fail for diagnosis.
    """
    stages: dict[str, bool] = {}

    bias = structure.get_bias(df_1h_w, swing_n=_LIV_SWING_N)
    stages["bias_bullish"] = (bias == "bullish")
    if bias != "bullish":
        return {"action": "FLAT", "sl": None, "stages": stages}

    pois = poi.get_pois(
        df_1h_w, bias,
        ob_lookback=_LIV_OB_LB,
        fvg_lookback=_LIV_FVG_LB,
        displacement_atr=_LIV_DISP_ATR,
    )
    price  = float(df_5m_w["close"].iloc[-1])
    active = poi.price_in_poi(price, pois)
    stages["in_htf_poi"] = (active is not None)
    if active is None:
        return {"action": "FLAT", "sl": None, "stages": stages}

    sweep = liquidity.get_sweep(
        df_5m_w, bias,
        lookback=_LIV_LIQ_LB,
        swing_n=_LIV_LIQ_SWING_N,
    )
    stages["sweep"] = (sweep is not None)
    if sweep is None:
        return {"action": "FLAT", "sl": None, "stages": stages}

    choch = confirmation.get_choch(
        df_5m_w, bias, sweep,
        lookback=_LIV_CHOCH_LB,
    )
    stages["choch"] = choch
    if not choch:
        return {"action": "FLAT", "sl": None, "stages": stages}

    sl = sweep["wick_extreme"] * (1.0 - _LIV_SL_BUF)
    return {"action": "LONG", "sl": sl, "stages": stages}


# ── Parity runner ─────────────────────────────────────────────────────────────

def run_parity(
    df_1h: pd.DataFrame,
    df_5m: pd.DataFrame,
    n_bars: int = 2000,
    sl_tol: float = 0.0001,     # 0.01% relative tolerance on SL
) -> dict:
    """
    Iterate over `n_bars` 5M bars (after warmup) and compare both paths.

    Returns:
        {
          "n_checked":    int,
          "arc_fires":    int,
          "liv_fires":    int,
          "both_fire":    int,
          "divergences":  list[dict],   # bars where fire/SL differs
        }
    """
    htf_map = _align_htf(df_1h, df_5m)
    warmup  = max(arc_cfg.LTF_BARS // 2, arc_cfg.HTF_BARS // 2)

    total     = len(df_5m)
    start_bar = warmup
    end_bar   = min(total - 1, start_bar + n_bars)

    n_checked   = 0
    arc_fires   = 0
    liv_fires   = 0
    both_fire   = 0
    divergences: list[dict] = []

    for i in range(start_bar, end_bar):
        htf_idx = htf_map[i]
        if htf_idx < 0:
            continue

        df_1h_w = df_1h.iloc[: htf_idx + 1]
        df_5m_w = df_5m.iloc[: i + 1]

        # Archive path
        htf_ctx = get_htf_context(df_1h_w)
        arc     = get_ltf_signal(df_5m_w, htf_ctx)
        arc_fire = (arc["action"] == "LONG")

        # Live path
        liv     = _live_signal(df_1h_w, df_5m_w)
        liv_fire = (liv["action"] == "LONG")

        n_checked += 1
        if arc_fire:
            arc_fires += 1
        if liv_fire:
            liv_fires += 1
        if arc_fire and liv_fire:
            both_fire += 1

        # Divergence: fire decision differs OR both fire with different SL
        fire_diff = arc_fire != liv_fire
        sl_diff   = False
        sl_rel    = None
        if arc_fire and liv_fire and arc["sl"] is not None and liv["sl"] is not None:
            sl_rel  = abs(arc["sl"] - liv["sl"]) / arc["sl"]
            sl_diff = sl_rel > sl_tol

        if fire_diff or sl_diff:
            ts_5m = df_5m["ts"].iloc[i] if "ts" in df_5m.columns else i
            ts_1h = df_1h["ts"].iloc[htf_idx] if "ts" in df_1h.columns else htf_idx
            divergences.append({
                "bar_5m":       i,
                "ts_5m":        str(ts_5m),
                "ts_1h":        str(ts_1h),
                "arc_fire":     arc_fire,
                "liv_fire":     liv_fire,
                "arc_sl":       arc["sl"],
                "liv_sl":       liv["sl"],
                "sl_rel_diff":  round(sl_rel, 6) if sl_rel is not None else None,
                "liv_stages":   liv["stages"],
                # Explain which archive stage(s) gated the signal
                "arc_bias":     htf_ctx.get("bias"),
                "arc_poi":      bool(htf_ctx.get("poi_zones")),
            })

    return {
        "n_checked":   n_checked,
        "arc_fires":   arc_fires,
        "liv_fires":   liv_fires,
        "both_fire":   both_fire,
        "divergences": divergences,
    }


# ── Data fixture ──────────────────────────────────────────────────────────────

def _load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    htf_path = ROOT / "data" / "cache" / f"{arc_cfg.SYMBOL}_60m.parquet"
    ltf_path = ROOT / "data" / "cache" / f"{arc_cfg.SYMBOL}_5m.parquet"
    missing  = [str(p) for p in (htf_path, ltf_path) if not p.exists()]
    if missing:
        pytest.skip(f"BLOCKED — cached data missing off-box: {missing}")
    df_1h = pd.read_parquet(htf_path)
    df_5m = pd.read_parquet(ltf_path)
    return df_1h, df_5m


# ── pytest test ───────────────────────────────────────────────────────────────

@pytest.mark.skip(
    reason=(
        "Trial 3 ABANDONED — archive (_archive/bot_v1, EMA200 bias) and live "
        "(smc_bot/, swing-structure bias) are intentionally different paths. "
        "Trial 4 uses smc_bot/ exclusively; this parity check is no longer load-bearing. "
        "Kept for historical reference."
    )
)
def test_signal_parity_fire_decision():
    """
    BOTH paths must agree on fire / no-fire for every bar in the sample.
    Divergences are listed; the test FAILS if any exist.
    """
    df_1h, df_5m = _load_data()
    result = run_parity(df_1h, df_5m, n_bars=2000)

    _print_report(result)

    divs = result["divergences"]
    assert len(divs) == 0, (
        f"\n\nSIGNAL PARITY FAILED — {len(divs)} divergent bars found.\n"
        f"Archive fires: {result['arc_fires']}  "
        f"Live fires: {result['liv_fires']}  "
        f"Both fire: {result['both_fire']}\n"
        f"Bars checked: {result['n_checked']}\n\n"
        f"First 10 divergences:\n" +
        "\n".join(
            f"  bar {d['bar_5m']} ({d['ts_5m']})  "
            f"arc={'LONG' if d['arc_fire'] else 'FLAT'}  "
            f"liv={'LONG' if d['liv_fire'] else 'FLAT'}  "
            f"arc_sl={d['arc_sl']}  liv_sl={d['liv_sl']}  "
            f"sl_rel={d['sl_rel_diff']}  "
            f"stages={d['liv_stages']}  "
            f"arc_bias={d['arc_bias']}  arc_poi={d['arc_poi']}"
            for d in divs[:10]
        )
    )


# ── Standalone report helper ──────────────────────────────────────────────────

def _print_report(result: dict) -> None:
    divs = result["divergences"]
    print(f"\n{'='*62}")
    print("  SIGNAL PARITY REPORT")
    print(f"{'='*62}")
    print(f"  Bars checked    : {result['n_checked']}")
    print(f"  Archive fires   : {result['arc_fires']}")
    print(f"  Live fires      : {result['liv_fires']}")
    print(f"  Both fire       : {result['both_fire']}")
    print(f"  Divergent bars  : {len(divs)}")
    print(f"  Verdict         : {'PARITY PASS' if not divs else 'PARITY FAIL — see divergences below'}")
    print(f"{'-'*62}")

    if divs:
        # Bucket divergences by cause
        arc_only  = [d for d in divs if d["arc_fire"] and not d["liv_fire"]]
        liv_only  = [d for d in divs if d["liv_fire"] and not d["arc_fire"]]
        sl_diffs  = [d for d in divs if d["arc_fire"] and d["liv_fire"]]

        print(f"  Archive fires, live doesn't : {len(arc_only)}")
        print(f"  Live fires, archive doesn't : {len(liv_only)}")
        print(f"  Both fire, SL differs       : {len(sl_diffs)}")
        print()

        print("  DIVERGENT BARS (first 20 of each bucket):")
        print()
        if arc_only:
            print("  [ARC-ONLY] Archive fires but live is FLAT:")
            for d in arc_only[:20]:
                # Identify which live stage first failed
                stage_fail = next(
                    (k for k, v in d["liv_stages"].items() if not v), "unknown"
                )
                print(
                    f"    bar {d['bar_5m']} | {d['ts_5m']} | "
                    f"arc_sl={d['arc_sl']:.2f} | "
                    f"first live stage fail: {stage_fail} | "
                    f"arc_bias={d['arc_bias']} arc_poi={d['arc_poi']}"
                )
        if liv_only:
            print()
            print("  [LIV-ONLY] Live fires but archive is FLAT:")
            for d in liv_only[:20]:
                print(
                    f"    bar {d['bar_5m']} | {d['ts_5m']} | "
                    f"liv_sl={d['liv_sl']:.2f} | "
                    f"stages={d['liv_stages']}"
                )
        if sl_diffs:
            print()
            print("  [SL-DIFF] Both fire, SL differs:")
            for d in sl_diffs[:20]:
                print(
                    f"    bar {d['bar_5m']} | {d['ts_5m']} | "
                    f"arc_sl={d['arc_sl']:.2f} liv_sl={d['liv_sl']:.2f} "
                    f"rel={d['sl_rel_diff']:.4%}"
                )

    print(f"{'='*62}\n")


if __name__ == "__main__":
    df_1h, df_5m = _load_data()
    print(f"1H: {len(df_1h)} bars | 5M: {len(df_5m)} bars")
    result = run_parity(df_1h, df_5m, n_bars=2000)
    _print_report(result)
    if result["divergences"]:
        sys.exit(1)
