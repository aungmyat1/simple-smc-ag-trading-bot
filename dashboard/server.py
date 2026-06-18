"""
SMC Bot Dashboard — http://localhost:8000/dashboard/

Run with:
    python -m dashboard.server
    uvicorn dashboard.server:app --host 0.0.0.0 --port 8000

Auto-refreshes every 30 s. Single candle-fetch per request shared between
the pipeline analysis and the SVG chart renderer.
"""
from __future__ import annotations

import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from smc_bot import confirmation, data, executor, fib as fib_mod, liquidity, poi, structure, targets as tgt_mod  # noqa: E402

with open(ROOT / "smc_bot" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

API_KEY    = os.getenv("BYBIT_DEMO_API_KEY", os.getenv("BYBIT_API_KEY", ""))
API_SECRET = os.getenv("BYBIT_DEMO_API_SECRET", os.getenv("BYBIT_API_SECRET", ""))
SYMBOL     = CFG["exchange"]["symbol"]
HTF        = CFG["exchange"]["htf"]
LTF        = CFG["exchange"]["ltf"]
DEMO       = CFG["bybit"]["demo"]

_client  = data.make_client(testnet=False)
_session = executor.make_session(API_KEY, API_SECRET, demo=DEMO)

app = FastAPI(docs_url=None, redoc_url=None)


# ── live data collectors ───────────────────────────────────────────────────────

def _account() -> dict:
    try:
        resp = _session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        coin = next(
            (c for c in resp["result"]["list"][0]["coin"] if c["coin"] == "USDT"), {}
        )
        wallet  = float(coin.get("walletBalance") or 0)
        equity  = float(coin.get("equity") or wallet)
        pnl     = float(coin.get("unrealisedPnl") or 0)
        cum_pnl = float(coin.get("cumRealisedPnl") or 0)
        return {"wallet": wallet, "equity": equity, "unreal_pnl": pnl, "cum_pnl": cum_pnl, "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _position() -> dict:
    try:
        pos = executor.get_position(_session, SYMBOL)
        if pos is None:
            return {"open": False}
        entry = float(pos.get("avgPrice") or 0)
        size  = float(pos.get("size") or 0)
        side  = pos.get("side", "")
        upnl  = float(pos.get("unrealisedPnl") or 0)
        sl    = pos.get("stopLoss", "")
        tp    = pos.get("takeProfit", "")
        return {
            "open":  True,
            "side":  side,
            "size":  size,
            "entry": entry,
            "upnl":  upnl,
            "sl":    sl,
            "tp":    tp,
        }
    except Exception as e:
        return {"open": False, "error": str(e)}


def _analyze_pipeline(df_1h, df_5m) -> dict:
    """
    Full 15-step pipeline analysis.

    Stages 0-5:
      0 = no 1H bias
      1 = bias set (not in Fib zone or POI yet)
      2 = in Fib zone + in POI (watching for sweep)
      3 = sweep confirmed (watching for displacement)
      4 = displacement confirmed (waiting for CHoCH)
      5 = CHoCH confirmed → SIGNAL
    """
    try:
        price   = float(df_5m["close"].iloc[-1])
        swing_n = CFG["structure"]["swing_n"]
        bias    = structure.get_bias(df_1h, swing_n=swing_n)

        # ── Fib 50% filter + swing range ──────────────────────────────────────
        fib_mid    = fib_mod.get_fib_midpoint(df_1h, bias, swing_n=swing_n) if bias != "neutral" else None
        fib_ok     = fib_mod.fib_filter(price, bias, fib_mid) if fib_mid is not None else False
        fib_zone   = "discount" if bias == "bullish" else "premium"

        # ── BSL / SSL liquidity pools ─────────────────────────────────────────
        tc       = CFG.get("targets", {})
        _tol     = tc.get("equal_level_tolerance", 0.002)
        _swing_t = CFG["structure"]["swing_n"]
        bsl_levels = tgt_mod.get_bsl_levels(df_1h, swing_n=_swing_t, tolerance=_tol) if bias != "neutral" else []
        ssl_levels = tgt_mod.get_ssl_levels(df_1h, swing_n=_swing_t, tolerance=_tol) if bias != "neutral" else []

        # Derive swing high / swing low for the dealing-range indicator
        swing_high = swing_low = None
        if bias != "neutral":
            _sh = structure._swing_highs(df_1h["high"].values, swing_n)
            _sl = structure._swing_lows(df_1h["low"].values, swing_n)
            if _sh:
                swing_high = float(df_1h["high"].values[_sh[-1]])
            if _sl:
                swing_low  = float(df_1h["low"].values[_sl[-1]])

        # ── 1H POI zones ──────────────────────────────────────────────────────
        poi_zones_raw = poi.get_pois(
            df_1h, bias,
            ob_lookback=CFG["poi"]["ob_lookback"],
            fvg_lookback=CFG["poi"]["fvg_lookback"],
            displacement_atr=CFG["poi"]["displacement_atr"],
        ) if bias != "neutral" else []

        poi_zones  = [
            {"kind": z["kind"], "low": float(z["low"]), "high": float(z["high"])}
            for z in poi_zones_raw
        ]
        active_poi = poi.price_in_poi(price, poi_zones) if poi_zones else None

        # Nearest POI for distance display
        nearest_poi, nearest_dist = None, float("inf")
        if poi_zones and not active_poi:
            for z in poi_zones:
                d = abs(price - (z["low"] + z["high"]) / 2)
                if d < nearest_dist:
                    nearest_dist, nearest_poi = d, z

        # ── 5M sweep ──────────────────────────────────────────────────────────
        sweep_result = None
        if bias != "neutral":
            if bias == "bullish":
                sweep_result = liquidity.get_sweep(df_5m, bias,
                    lookback=CFG["liquidity"]["lookback"],
                    swing_n=CFG["liquidity"]["swing_n"])
            else:
                # bearish: sweep of swing highs (BSL sweep)
                sweep_result = liquidity.get_sweep(df_5m, bias,
                    lookback=CFG["liquidity"]["lookback"],
                    swing_n=CFG["liquidity"]["swing_n"])

        # ── Displacement ───────────────────────────────────────────────────────
        displacement = False
        if sweep_result:
            displacement = liquidity.check_displacement(
                df_5m, sweep_result["bar_idx"], bias,
                atr_mult=CFG["liquidity"].get("displacement_atr", CFG["poi"]["displacement_atr"]),
            )

        # ── CHoCH ─────────────────────────────────────────────────────────────
        choch_ref_level = None
        choch = False
        if sweep_result:
            sb = sweep_result["bar_idx"]
            lb = CFG["confirmation"]["lookback"]
            rs = max(0, sb - lb)
            if bias == "bullish":
                choch_ref_level = float(np.max(df_5m["high"].values[rs : sb + 1]))
            else:
                choch_ref_level = float(np.min(df_5m["low"].values[rs : sb + 1]))
            choch = bool(confirmation.get_choch(df_5m, bias, sweep_result, lookback=lb))

        # ── Stage + blocker ────────────────────────────────────────────────────
        if bias == "neutral":
            stage, blocker = 0, "No clear 1H structure (need HH+HL or LL+LH)"
        elif not fib_ok or not active_poi:
            if not fib_ok and fib_mid:
                dir_ = "≤" if bias == "bullish" else "≥"
                stage = 1
                blocker = (
                    f"Fib filter: price ${price:,.0f} not in {fib_zone} "
                    f"(need {dir_} ${fib_mid:,.0f})"
                )
            elif not poi_zones:
                stage, blocker = 1, "No 1H OB/FVG zones detected"
            else:
                dist_str = f"${nearest_dist:,.0f} away" if nearest_poi else "—"
                stage    = 1
                blocker  = f"Fib OK but price not in POI yet — nearest {dist_str}"
        elif not sweep_result:
            swing_dir = "low" if bias == "bullish" else "high"
            stage     = 2
            blocker   = f"In POI + Fib {fib_zone} — watching for 5M swing {swing_dir} sweep"
        elif not displacement:
            stage   = 3
            sw_lv   = sweep_result["swept_level"]
            sw_wk   = sweep_result["wick_extreme"]
            blocker = (
                f"Sweep of ${sw_lv:,.0f} confirmed (wick ${sw_wk:,.0f}) — "
                f"waiting for displacement candle (≥{CFG['poi']['displacement_atr']}×ATR)"
            )
        elif not choch:
            ref_str = f"${choch_ref_level:,.0f}" if choch_ref_level else "—"
            brk_dir = "above" if bias == "bullish" else "below"
            stage   = 4
            blocker = f"Displacement confirmed — close {brk_dir} {ref_str} to trigger CHoCH"
        else:
            stage, blocker = 5, None

        signal = ("LONG" if bias == "bullish" else "SHORT") if stage == 5 else "FLAT"

        return {
            "ok":              True,
            "price":           price,
            "bias":            bias,
            "fib_mid":         fib_mid,
            "fib_ok":          fib_ok,
            "poi_zones":       poi_zones,
            "active_poi":      active_poi,
            "nearest_poi":     nearest_poi,
            "poi_count":       len(poi_zones),
            "in_poi":          bool(active_poi),
            "poi_kind":        active_poi["kind"] if active_poi else None,
            "sweep":           bool(sweep_result),
            "sweep_bar":       int(sweep_result["bar_idx"]) if sweep_result else None,
            "sweep_level":     float(sweep_result["swept_level"]) if sweep_result else None,
            "sweep_wick":      float(sweep_result["wick_extreme"]) if sweep_result else None,
            "displacement":    displacement,
            "choch":           bool(choch),
            "choch_ref_level": choch_ref_level,
            "signal":          signal,
            "stage":           stage,
            "blocker":         blocker,
            "swing_high":      swing_high,
            "swing_low":       swing_low,
            "bsl_levels":      bsl_levels,
            "ssl_levels":      ssl_levels,
        }
    except Exception as exc:
        import traceback; traceback.print_exc()
        return {"ok": False, "error": str(exc), "price": 0, "bias": "—", "signal": "—", "stage": 0,
                "poi_zones": [], "sweep": False, "displacement": False, "choch": False}


def _trades(n: int = 25) -> list[dict]:
    path = ROOT / "smc_bot_trades.csv"
    if not path.exists():
        return []
    try:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        return list(reversed(rows))[:n]
    except Exception:
        return []


def _log_tail(n: int = 30) -> list[str]:
    path = ROOT / "logs" / "smc_bot.log"
    if not path.exists():
        return ["(log file not found — bot not started yet)"]
    try:
        lines = path.read_text().splitlines()
        return lines[-n:]
    except Exception:
        return ["(error reading log)"]


def _stats(trades: list[dict]) -> dict:
    if not trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_r": 0}
    wins = [t for t in trades if float(t.get("pnl_r", 0) or 0) > 0]
    total = len(trades)
    return {"total": total, "wins": len(wins), "losses": total - len(wins),
            "win_r": len(wins) / total if total else 0}


# ── SVG chart ─────────────────────────────────────────────────────────────────

def _render_chart_svg(df_5m, pipe: dict, position: dict) -> str:
    N    = 60
    df   = df_5m.tail(N).reset_index(drop=True)
    n    = len(df)
    bias = pipe.get("bias", "neutral")

    # Canvas
    W, H          = 1020, 430
    ML, MR, MT, MB = 68, 190, 44, 34
    CW = W - ML - MR    # 784
    CH = H - MT - MB    # 352

    # Price range — include all annotation levels
    p_hi = float(df["high"].max())
    p_lo = float(df["low"].min())
    extras = []
    for z in pipe.get("poi_zones", []):
        extras += [z["low"], z["high"]]
    for k in ("sweep_level", "sweep_wick", "choch_ref_level", "swing_high", "swing_low", "fib_mid"):
        if pipe.get(k):
            extras.append(pipe[k])
    for lvl in pipe.get("bsl_levels", []) + pipe.get("ssl_levels", []):
        extras.append(lvl)
    if position.get("open"):
        for k in ("sl", "tp"):
            try:
                v = float(position[k])
                if v > 0:
                    extras.append(v)
            except Exception:
                pass
    elif pipe.get("sweep_wick") and pipe.get("price"):
        buf = CFG["risk"]["sl_buffer"]
        sl  = pipe["sweep_wick"] * (1 - buf) if bias == "bullish" else pipe["sweep_wick"] * (1 + buf)
        r   = abs(pipe["price"] - sl)
        tp  = pipe["price"] + r * 2 if bias == "bullish" else pipe["price"] - r * 2
        extras += [sl, tp]

    if extras:
        p_hi = max(p_hi, max(e for e in extras if e > 0))
        p_lo = min(p_lo, min(e for e in extras if e > 0))

    pad   = (p_hi - p_lo) * 0.16
    p_max = p_hi + pad
    p_min = p_lo - pad
    p_rng = p_max - p_min

    def py(price: float) -> float:
        return MT + CH * (1.0 - (float(price) - p_min) / p_rng)

    def px(i: int) -> float:
        return ML + CW * i / max(n - 1, 1)

    bw  = max(4.5, CW / n * 0.62)   # body width
    bhw = bw / 2                      # half

    o: list[str] = []
    o.append(
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block;background:#0d1117;border-radius:6px">'
    )

    # ── background grid ────────────────────────────────────────────────────────
    for pct in (0.15, 0.35, 0.5, 0.65, 0.85):
        yg = MT + CH * pct
        pg = p_max - p_rng * pct
        o.append(f'<line x1="{ML}" y1="{yg:.1f}" x2="{ML+CW}" y2="{yg:.1f}" stroke="#1c2230" stroke-width="1"/>')
        o.append(f'<text x="{ML-6}" y="{yg+4:.1f}" text-anchor="end" fill="#3d4a5a" font-family="monospace" font-size="9">{pg:,.0f}</text>')

    # Chart border
    o.append(f'<rect x="{ML}" y="{MT}" width="{CW}" height="{CH}" fill="none" stroke="#1c2230" stroke-width="1"/>')

    # ── Dealing Range indicator (swing high / 50% mid / swing low) ────────────
    s_hi  = pipe.get("swing_high")
    s_lo  = pipe.get("swing_low")
    f_mid = pipe.get("fib_mid")

    if s_hi and s_lo and s_hi > s_lo:
        ysh = py(s_hi)
        ysl = py(s_lo)
        ymid = py(f_mid) if f_mid else (ysh + ysl) / 2

        # Discount zone (swing_low → mid): subtle green fill
        disc_top = ymid
        disc_bot = ysl
        if disc_bot > disc_top:
            o.append(
                f'<rect x="{ML}" y="{disc_top:.1f}" width="{CW}" '
                f'height="{disc_bot - disc_top:.1f}" fill="#0a1f0a" opacity="0.55"/>'
            )
            o.append(
                f'<text x="{ML + CW - 6}" y="{(disc_top + disc_bot) / 2 + 4:.1f}" '
                f'text-anchor="end" fill="#204a20" font-family="monospace" '
                f'font-size="9" font-weight="600" opacity="0.9">DISCOUNT</text>'
            )

        # Premium zone (mid → swing_high): subtle red fill
        prem_top = ysh
        prem_bot = ymid
        if prem_bot > prem_top:
            o.append(
                f'<rect x="{ML}" y="{prem_top:.1f}" width="{CW}" '
                f'height="{prem_bot - prem_top:.1f}" fill="#1f0a0a" opacity="0.55"/>'
            )
            o.append(
                f'<text x="{ML + CW - 6}" y="{(prem_top + prem_bot) / 2 + 4:.1f}" '
                f'text-anchor="end" fill="#4a2020" font-family="monospace" '
                f'font-size="9" font-weight="600" opacity="0.9">PREMIUM</text>'
            )

        # Swing High line
        o.append(
            f'<line x1="{ML}" y1="{ysh:.1f}" x2="{ML+CW}" y2="{ysh:.1f}" '
            f'stroke="#7a3030" stroke-width="1.2" stroke-dasharray="8,4"/>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{ysh+4:.1f}" fill="#c05050" '
            f'font-family="monospace" font-size="8.5">Swing H</text>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{ysh+14:.1f}" fill="#804040" '
            f'font-family="monospace" font-size="8">{s_hi:,.0f}</text>'
        )

        # Swing Low line
        o.append(
            f'<line x1="{ML}" y1="{ysl:.1f}" x2="{ML+CW}" y2="{ysl:.1f}" '
            f'stroke="#207a30" stroke-width="1.2" stroke-dasharray="8,4"/>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{ysl+4:.1f}" fill="#40c060" '
            f'font-family="monospace" font-size="8.5">Swing L</text>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{ysl+14:.1f}" fill="#408050" '
            f'font-family="monospace" font-size="8">{s_lo:,.0f}</text>'
        )

        # 50% Midpoint line
        if f_mid:
            o.append(
                f'<line x1="{ML}" y1="{ymid:.1f}" x2="{ML+CW}" y2="{ymid:.1f}" '
                f'stroke="#6060a0" stroke-width="1" stroke-dasharray="4,4"/>'
            )
            o.append(
                f'<text x="{ML+CW+7}" y="{ymid+4:.1f}" fill="#8888cc" '
                f'font-family="monospace" font-size="8.5">50% Mid</text>'
            )
            o.append(
                f'<text x="{ML+CW+7}" y="{ymid+14:.1f}" fill="#6060a0" '
                f'font-family="monospace" font-size="8">{f_mid:,.0f}</text>'
            )

    # ── POI zones (under candles) ──────────────────────────────────────────────
    # Chart filter: always show OBs; FVGs only within 2% of current price
    cur_p = pipe.get("price") or float(df["close"].iloc[-1])
    chart_zones = []
    for z in pipe.get("poi_zones", []):
        if z["kind"] == "OB":
            chart_zones.append(z)
        else:
            mid = (z["low"] + z["high"]) / 2
            if cur_p and abs(mid - cur_p) / cur_p <= 0.02:
                chart_zones.append(z)

    # Draw FVGs first (behind OBs)
    for z in chart_zones:
        if z["kind"] != "FVG":
            continue
        zy1 = py(z["high"])
        zy2 = py(z["low"])
        zh  = max(1.0, zy2 - zy1)
        mid_y = (zy1 + zy2) / 2 + 4
        o.append(
            f'<rect x="{ML}" y="{zy1:.1f}" width="{CW}" height="{zh:.1f}" '
            f'fill="#1c1205" stroke="#6a5010" stroke-width="0.8" opacity="0.6"/>'
        )
        # Dashed midline
        o.append(
            f'<line x1="{ML}" y1="{(zy1+zy2)/2:.1f}" x2="{ML+CW}" y2="{(zy1+zy2)/2:.1f}" '
            f'stroke="#7a6018" stroke-width="0.6" stroke-dasharray="4,4" opacity="0.7"/>'
        )
        o.append(
            f'<text x="{ML+6}" y="{mid_y:.1f}" fill="#c8950a" '
            f'font-family="monospace" font-size="9" opacity="0.85">FVG</text>'
        )
        o.append(
            f'<text x="{ML+CW+6}" y="{zy1+10:.1f}" fill="#9a7010" '
            f'font-family="monospace" font-size="8">{z["high"]:,.0f}</text>'
        )
        o.append(
            f'<text x="{ML+CW+6}" y="{zy2+1:.1f}" fill="#9a7010" '
            f'font-family="monospace" font-size="8">{z["low"]:,.0f}</text>'
        )

    # Draw OBs on top with strong styling
    for z in chart_zones:
        if z["kind"] != "OB":
            continue
        zy1 = py(z["high"])
        zy2 = py(z["low"])
        zh  = max(2.0, zy2 - zy1)
        mid_y = (zy1 + zy2) / 2
        is_active = (
            pipe.get("active_poi") is not None and
            pipe["active_poi"].get("low") == z["low"] and
            pipe["active_poi"].get("high") == z["high"]
        )

        if bias == "bullish":
            fill   = "#0a2848" if not is_active else "#0d3a66"
            stroke = "#2472c8" if not is_active else "#3a96ff"
            lbl    = "#4a9eff"
            border = "#3a8aff"
        else:
            fill   = "#2a0a12" if not is_active else "#3d0e1a"
            stroke = "#c02840" if not is_active else "#ff3a5a"
            lbl    = "#ff6070"
            border = "#ff4060"

        opacity = "1.0" if is_active else "0.82"

        # Zone body
        o.append(
            f'<rect x="{ML}" y="{zy1:.1f}" width="{CW}" height="{zh:.1f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1" opacity="{opacity}"/>'
        )
        # Bold left border
        o.append(
            f'<rect x="{ML}" y="{zy1:.1f}" width="3" height="{zh:.1f}" '
            f'fill="{border}" opacity="{opacity}"/>'
        )
        # High line (top edge)
        o.append(
            f'<line x1="{ML}" y1="{zy1:.1f}" x2="{ML+CW}" y2="{zy1:.1f}" '
            f'stroke="{stroke}" stroke-width="1.2" opacity="{opacity}"/>'
        )
        # Low line (bottom edge)
        o.append(
            f'<line x1="{ML}" y1="{zy2:.1f}" x2="{ML+CW}" y2="{zy2:.1f}" '
            f'stroke="{stroke}" stroke-width="1.2" opacity="{opacity}"/>'
        )
        # "OB" label inside zone (left)
        active_mark = " ◀" if is_active else ""
        label_y = max(zy1 + 12, min(zy2 - 3, mid_y + 4))
        o.append(
            f'<text x="{ML+7}" y="{label_y:.1f}" fill="{lbl}" '
            f'font-family="monospace" font-size="10" font-weight="bold" opacity="0.95">'
            f'OB{active_mark}</text>'
        )
        # Right-side: "OB" label + High and Low prices
        o.append(
            f'<text x="{ML+CW+7}" y="{mid_y:.1f}" fill="{lbl}" '
            f'font-family="monospace" font-size="9" font-weight="bold">OB</text>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{zy1+10:.1f}" fill="{stroke}" '
            f'font-family="monospace" font-size="8">{z["high"]:,.0f}</text>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{zy2-2:.1f}" fill="{stroke}" '
            f'font-family="monospace" font-size="8">{z["low"]:,.0f}</text>'
        )

    # ── BSL / SSL liquidity pools ──────────────────────────────────────────────
    # BSL = equal-highs clusters (Buy-Side Liquidity) — TP target for longs
    # SSL = equal-lows  clusters (Sell-Side Liquidity) — TP target for shorts
    _liq_price = pipe.get("price") or (float(df["close"].iloc[-1]) if len(df) else 0)
    bsl_all = sorted(pipe.get("bsl_levels", []))
    ssl_all = sorted(pipe.get("ssl_levels", []))

    # Limit to 4 nearest to current price each side to avoid clutter
    bsl_near = sorted(bsl_all, key=lambda v: abs(v - _liq_price))[:4]
    ssl_near = sorted(ssl_all, key=lambda v: abs(v - _liq_price))[:4]

    # Identify the active TP target (nearest above price for longs / below for shorts)
    _liq_bias = pipe.get("bias", "neutral")
    tp_pool = None
    if _liq_bias == "bullish":
        cands = [v for v in bsl_near if v > _liq_price]
        tp_pool = min(cands) if cands else None
    elif _liq_bias == "bearish":
        cands = [v for v in ssl_near if v < _liq_price]
        tp_pool = max(cands) if cands else None

    for lvl in bsl_near:
        yl = py(lvl)
        is_tp = (tp_pool is not None and abs(lvl - tp_pool) < 1)
        col  = "#ffa040" if not is_tp else "#ffcc44"
        dash = "5,4" if not is_tp else "0"
        sw   = "1" if not is_tp else "1.6"
        lbl  = "BSL" if not is_tp else "BSL ● TP"
        o.append(
            f'<line x1="{ML}" y1="{yl:.1f}" x2="{ML+CW}" y2="{yl:.1f}" '
            f'stroke="{col}" stroke-width="{sw}" stroke-dasharray="{dash}" opacity="0.75"/>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{yl+4:.1f}" fill="{col}" '
            f'font-family="monospace" font-size="8.5" font-weight="{"bold" if is_tp else "normal"}">{lbl}</text>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{yl+14:.1f}" fill="#a06820" '
            f'font-family="monospace" font-size="8">{lvl:,.0f}</text>'
        )

    for lvl in ssl_near:
        yl = py(lvl)
        is_tp = (tp_pool is not None and abs(lvl - tp_pool) < 1)
        col  = "#40a0d0" if not is_tp else "#44ddff"
        dash = "5,4" if not is_tp else "0"
        sw   = "1" if not is_tp else "1.6"
        lbl  = "SSL" if not is_tp else "SSL ● TP"
        o.append(
            f'<line x1="{ML}" y1="{yl:.1f}" x2="{ML+CW}" y2="{yl:.1f}" '
            f'stroke="{col}" stroke-width="{sw}" stroke-dasharray="{dash}" opacity="0.75"/>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{yl+4:.1f}" fill="{col}" '
            f'font-family="monospace" font-size="8.5" font-weight="{"bold" if is_tp else "normal"}">{lbl}</text>'
        )
        o.append(
            f'<text x="{ML+CW+7}" y="{yl+14:.1f}" fill="#207090" '
            f'font-family="monospace" font-size="8">{lvl:,.0f}</text>'
        )

    # ── sweep & stop-hunt levels ───────────────────────────────────────────────
    sweep_level = pipe.get("sweep_level")
    sweep_wick  = pipe.get("sweep_wick")

    if sweep_level:
        ys = py(sweep_level)
        o.append(f'<line x1="{ML}" y1="{ys:.1f}" x2="{ML+CW}" y2="{ys:.1f}" stroke="#7a8a9a" stroke-width="1" stroke-dasharray="6,4"/>')
        o.append(f'<text x="{ML+CW+8}" y="{ys+4:.1f}" fill="#7a8a9a" font-family="monospace" font-size="8.5">Equal lows</text>')
        o.append(f'<text x="{ML+CW+8}" y="{ys+15:.1f}" fill="#7a8a9a" font-family="monospace" font-size="8.5">swept liq.</text>')

    # ── SL & TP lines ──────────────────────────────────────────────────────────
    sl_price, tp_price = None, None
    if position.get("open"):
        try:
            sl_price = float(position["sl"]) if position["sl"] else None
            tp_price = float(position["tp"]) if position["tp"] else None
        except Exception:
            pass
    elif sweep_wick and pipe.get("price"):
        buf = CFG["risk"]["sl_buffer"]
        r_  = CFG["risk"]["target_r"]
        cp  = pipe["price"]
        if bias == "bullish":
            sl_price = sweep_wick * (1 - buf)
            tp_price = cp + abs(cp - sl_price) * r_
        else:
            sl_price = sweep_wick * (1 + buf)
            tp_price = cp - abs(cp - sl_price) * r_

    if tp_price:
        yt = py(tp_price)
        o.append(f'<line x1="{ML}" y1="{yt:.1f}" x2="{ML+CW}" y2="{yt:.1f}" stroke="#3fb950" stroke-width="1" stroke-dasharray="5,4"/>')
        o.append(f'<text x="{ML+CW+8}" y="{yt+4:.1f}" fill="#3fb950" font-family="monospace" font-size="8.5">Take profit</text>')
        o.append(f'<text x="{ML+CW+8}" y="{yt+15:.1f}" fill="#3fb950" font-family="monospace" font-size="8.5">old highs (BSL)</text>')

    if sl_price:
        ys2 = py(sl_price)
        o.append(f'<line x1="{ML}" y1="{ys2:.1f}" x2="{ML+CW}" y2="{ys2:.1f}" stroke="#f85149" stroke-width="1" stroke-dasharray="4,3"/>')
        o.append(f'<text x="{ML+CW+8}" y="{ys2+4:.1f}" fill="#f85149" font-family="monospace" font-size="8.5">Stop loss</text>')

    # Risk zone (between SL and sweep_level)
    if sl_price and sweep_level:
        yrz1 = py(max(sl_price, sweep_level))
        yrz2 = py(min(sl_price, sweep_level))
        o.append(
            f'<rect x="{ML}" y="{yrz1:.1f}" width="{CW}" height="{max(1,yrz2-yrz1):.1f}" '
            f'fill="#f85149" opacity="0.07"/>'
        )
        o.append(f'<text x="{ML+CW+8}" y="{(yrz1+yrz2)/2+4:.1f}" fill="#c04040" font-family="monospace" font-size="8.5">Risk</text>')

    # Reward zone (between price and TP)
    if tp_price and pipe.get("price"):
        cp   = pipe["price"]
        yr1  = py(max(cp, tp_price))
        yr2  = py(min(cp, tp_price))
        o.append(
            f'<rect x="{ML}" y="{yr1:.1f}" width="{CW}" height="{max(1,yr2-yr1):.1f}" '
            f'fill="#3fb950" opacity="0.06"/>'
        )
        o.append(f'<text x="{ML+CW+8}" y="{(yr1+yr2)/2+4:.1f}" fill="#308040" font-family="monospace" font-size="8.5">Reward</text>')

    # ── CHoCH reference level ──────────────────────────────────────────────────
    choch_ref = pipe.get("choch_ref_level")
    if choch_ref:
        yc = py(choch_ref)
        confirmed = pipe.get("choch", False)
        col       = "#3fb950" if confirmed else "#a0a0a0"
        dash      = "8,3" if confirmed else "6,5"
        lbl       = "CHoCH ✓" if confirmed else "CHoCH"
        o.append(f'<line x1="{ML}" y1="{yc:.1f}" x2="{ML+CW}" y2="{yc:.1f}" stroke="{col}" stroke-width="1.2" stroke-dasharray="{dash}"/>')
        o.append(f'<text x="{ML+CW+8}" y="{yc+4:.1f}" fill="{col}" font-family="monospace" font-size="9" font-weight="bold">{lbl}</text>')

    # ── candlesticks ───────────────────────────────────────────────────────────
    sweep_bar_idx = pipe.get("sweep_bar")

    for i in range(n):
        row  = df.iloc[i]
        cx   = px(i)
        op_  = float(row["open"])
        hi_  = float(row["high"])
        lo_  = float(row["low"])
        cl_  = float(row["close"])
        bull = cl_ >= op_
        col  = "#3fb950" if bull else "#f85149"

        body_top = py(max(op_, cl_))
        body_bot = py(min(op_, cl_))
        body_h   = max(1.0, body_bot - body_top)

        # Wick
        o.append(f'<line x1="{cx:.1f}" y1="{py(hi_):.1f}" x2="{cx:.1f}" y2="{py(lo_):.1f}" stroke="{col}" stroke-width="1" opacity="0.85"/>')
        # Body
        o.append(f'<rect x="{cx-bhw:.1f}" y="{body_top:.1f}" width="{bw:.1f}" height="{body_h:.1f}" fill="{col}" rx="0.5"/>')

        # Sweep bar marker
        if sweep_bar_idx is not None and i == (sweep_bar_idx if sweep_bar_idx < n else n - 1):
            if bias == "bullish":
                my = py(lo_) + 14
                o.append(f'<text x="{cx:.1f}" y="{my:.1f}" text-anchor="middle" fill="#58a6ff" font-size="10" font-weight="bold">▼</text>')
                o.append(f'<text x="{cx:.1f}" y="{my+11:.1f}" text-anchor="middle" fill="#58a6ff" font-family="monospace" font-size="7.5">Sweep</text>')
            else:
                my = py(hi_) - 14
                o.append(f'<text x="{cx:.1f}" y="{my:.1f}" text-anchor="middle" fill="#58a6ff" font-size="10" font-weight="bold">▲</text>')
                o.append(f'<text x="{cx:.1f}" y="{my-3:.1f}" text-anchor="middle" fill="#58a6ff" font-family="monospace" font-size="7.5">Sweep</text>')

    # ── current price line ─────────────────────────────────────────────────────
    cur_price = pipe.get("price", 0)
    if cur_price:
        yp = py(cur_price)
        o.append(f'<line x1="{ML}" y1="{yp:.1f}" x2="{ML+CW}" y2="{yp:.1f}" stroke="#4a8aff" stroke-width="1" stroke-dasharray="3,3" opacity="0.7"/>')
        o.append(f'<rect x="{ML+CW-1}" y="{yp-9:.1f}" width="69" height="16" fill="#1a2f60" rx="2"/>')
        o.append(f'<text x="{ML+CW+33:.1f}" y="{yp+4:.1f}" text-anchor="middle" fill="#6ab0ff" font-family="monospace" font-size="9" font-weight="bold">{cur_price:,.0f}</text>')

    # ── entry arrow (if signal or in setup) ────────────────────────────────────
    if pipe.get("in_poi") and pipe.get("sweep") and cur_price:
        lx   = px(n - 1)
        acol = "#3fb950" if bias == "bullish" else "#f85149"
        arr  = "▲" if bias == "bullish" else "▼"
        ey   = py(cur_price) + (-14 if bias == "bullish" else 14)
        o.append(f'<text x="{lx:.1f}" y="{ey:.1f}" text-anchor="middle" fill="{acol}" font-size="13">{arr}</text>')
        o.append(f'<text x="{lx:.1f}" y="{ey+(14 if bias=="bullish" else 0):.1f}" text-anchor="middle" fill="{acol}" font-family="monospace" font-size="7.5">Entry</text>')
        o.append(f'<text x="{ML+CW+8}" y="{ey+10:.1f}" fill="{acol}" font-family="monospace" font-size="8.5">Entry</text>')

    # ── HTF bias badge (top-left) ──────────────────────────────────────────────
    bcol = {"bullish": "#3fb950", "bearish": "#f85149"}.get(bias, "#6e7681")
    blbl = {"bullish": "HTF bias: bullish", "bearish": "HTF bias: bearish"}.get(bias, "HTF bias: neutral")
    bsub = "1H HH+HL uptrend" if bias == "bullish" else "1H LL+LH downtrend" if bias == "bearish" else "no clear structure"
    o.append(f'<rect x="{ML+6}" y="{MT+5}" width="140" height="35" rx="5" fill="#161b22" stroke="{bcol}" stroke-width="1.5" opacity="0.95"/>')
    o.append(f'<text x="{ML+14}" y="{MT+19}" fill="{bcol}" font-family="monospace" font-size="11" font-weight="bold">{blbl}</text>')
    o.append(f'<text x="{ML+14}" y="{MT+31}" fill="#4a5a6a" font-family="monospace" font-size="8.5">{bsub}</text>')

    # ── stage badge (top-right of chart) ──────────────────────────────────────
    stage = pipe.get("stage", 0)
    stage_lbl = f"Stage {stage}/5 · {['NO BIAS','BIAS','FIB+POI','SWEEP','DISP','SIGNAL'][min(stage,5)]}"
    scol = ["#4a5568", "#d29922", "#e3b341", "#58a6ff", "#c97dff", "#3fb950"][min(stage, 5)]
    o.append(f'<rect x="{ML+CW-145}" y="{MT+5}" width="143" height="22" rx="4" fill="#161b22" stroke="{scol}" stroke-width="1" opacity="0.95"/>')
    o.append(f'<text x="{ML+CW-72}" y="{MT+18}" text-anchor="middle" fill="{scol}" font-family="monospace" font-size="9" font-weight="bold">{stage_lbl}</text>')

    # ── axis label ────────────────────────────────────────────────────────────
    o.append(f'<text x="{W//2}" y="{H-6}" text-anchor="middle" fill="#3d4a5a" font-family="monospace" font-size="9">{SYMBOL} · {LTF.upper()} · last {n} bars</text>')
    o.append(f'<text x="{ML+4}" y="{H-6}" fill="#3d4a5a" font-family="monospace" font-size="9">price →</text>')
    o.append(f'<text x="{ML+CW-4}" y="{H-6}" text-anchor="end" fill="#3d4a5a" font-family="monospace" font-size="9">time →</text>')

    o.append("</svg>")
    return "".join(o)


# ── Order Block / FVG zones panel ────────────────────────────────────────────

def _ob_zones_html(pipe: dict) -> str:
    zones = pipe.get("poi_zones", [])
    price = pipe.get("price", 0)
    bias  = pipe.get("bias", "neutral")
    active_poi = pipe.get("active_poi")

    if not zones:
        empty_msg = (
            "No 1H OB/FVG zones detected."
            if bias != "neutral"
            else "No zones — bias is neutral (no clear HH+HL or LL+LH structure)."
        )
        return (
            f'<div class="card full-width">'
            f'<div class="card-title">1H Order Block &amp; FVG Zones</div>'
            f'<div style="color:var(--muted);padding:12px 0;font-size:12px">{empty_msg}</div>'
            f'</div>'
        )

    obs  = [z for z in zones if z.get("kind") == "OB"]
    fvgs = [z for z in zones if z.get("kind") == "FVG"]

    def _is_active(z: dict) -> bool:
        if not active_poi:
            return False
        return z["low"] == active_poi.get("low") and z["high"] == active_poi.get("high")

    def _dist_str(z: dict) -> str:
        mid = (z["low"] + z["high"]) / 2
        if z["low"] <= price <= z["high"]:
            return '<span class="green" style="font-weight:700">● ACTIVE</span>'
        d = price - mid
        pct = abs(d) / price * 100
        direction = "above" if d < 0 else "below"
        return f'<span class="muted">${abs(d):,.0f} ({pct:.2f}%) {direction}</span>'

    def _zone_row(z: dict) -> str:
        kind = z.get("kind", "?")
        tag_cls  = "tag-ob" if kind == "OB" else "tag-fvg"
        width_usd = z["high"] - z["low"]
        width_pct = width_usd / z["low"] * 100
        active_mark = ' <span style="color:#4a9eff">◀</span>' if _is_active(z) else ""
        return (
            f'<tr>'
            f'<td><span class="tag {tag_cls}">{kind}</span>{active_mark}</td>'
            f'<td class="green">${z["high"]:,.2f}</td>'
            f'<td class="red">${z["low"]:,.2f}</td>'
            f'<td class="muted">${width_usd:,.2f} ({width_pct:.2f}%)</td>'
            f'<td>{_dist_str(z)}</td>'
            f'</tr>'
        )

    bias_col  = "#3fb950" if bias == "bullish" else "#f85149" if bias == "bearish" else "#6e7681"
    bias_lbl  = bias.upper()
    n_active  = 1 if active_poi else 0
    active_badge = (
        f'<span style="color:var(--green);font-weight:700;font-size:11px">● 1 ACTIVE</span>'
        if n_active else
        f'<span style="color:var(--muted);font-size:11px">none active</span>'
    )

    header_row = (
        '<tr><th>Kind</th><th>High</th><th>Low</th>'
        '<th>Width</th><th>Distance from price</th></tr>'
    )
    ob_rows  = "".join(_zone_row(z) for z in obs)
    fvg_rows = "".join(_zone_row(z) for z in fvgs)

    ob_section = ""
    if obs:
        ob_section = (
            f'<div style="font-size:10px;font-weight:700;letter-spacing:.10em;'
            f'text-transform:uppercase;color:#4a9eff;margin:0 0 6px">Order Blocks ({len(obs)})</div>'
            f'<div style="overflow-x:auto;margin-bottom:14px">'
            f'<table class="trades-table"><thead>{header_row}</thead>'
            f'<tbody>{ob_rows}</tbody></table></div>'
        )

    fvg_section = ""
    if fvgs:
        fvg_section = (
            f'<div style="font-size:10px;font-weight:700;letter-spacing:.10em;'
            f'text-transform:uppercase;color:var(--orange);margin:0 0 6px">Fair Value Gaps ({len(fvgs)})</div>'
            f'<div style="overflow-x:auto">'
            f'<table class="trades-table"><thead>{header_row}</thead>'
            f'<tbody>{fvg_rows}</tbody></table></div>'
        )

    note = (
        '<div style="font-size:11px;color:var(--muted);margin-top:10px;border-top:1px solid var(--border);'
        'padding-top:8px">'
        '<strong style="color:#4a9eff">OB (Order Block)</strong> — last opposite candle before a '
        f'displacement move ≥{CFG["poi"]["displacement_atr"]}×ATR. Entry zone per diagrams. '
        '&nbsp;|&nbsp; '
        '<strong style="color:var(--orange)">FVG (Fair Value Gap)</strong> — 3-candle imbalance; '
        'confluence confirmation (bot enters OB, not standalone FVG).'
        '</div>'
    )

    return (
        f'<div class="card full-width">'
        f'<div class="card-title" style="display:flex;justify-content:space-between;align-items:center">'
        f'<span>1H Order Block &amp; FVG Zones</span>'
        f'<span style="font-size:11px">'
        f'<span style="color:{bias_col};font-weight:700">{bias_lbl}</span>'
        f' &nbsp;·&nbsp; {len(zones)} zones &nbsp;·&nbsp; {active_badge}'
        f'</span></div>'
        f'{ob_section}{fvg_section}{note}'
        f'</div>'
    )


# ── proximity explanation panel ───────────────────────────────────────────────

def _proximity_html(pipe: dict, position: dict) -> str:
    if not pipe.get("ok"):
        return f'<div class="card full-width"><div class="card-title">Setup Proximity</div><span class="red">⚠ {pipe.get("error","")}</span></div>'

    stage = pipe.get("stage", 0)
    bias  = pipe.get("bias", "neutral")
    signal= pipe.get("signal", "FLAT")

    # Stage progress bar — 5 steps interleaved with 4 connectors
    steps = [
        (f"{HTF.upper()} Bias",  stage >= 1),
        ("Fib+POI",              stage >= 2),
        (f"{LTF.upper()} Sweep", stage >= 3),
        ("Displace",             stage >= 4),
        (f"{LTF.upper()} CHoCH", stage >= 5),
    ]

    bar_parts: list[str] = []
    for idx, (label, done) in enumerate(steps):
        col, icon, bg = ("#3fb950", "✅", "#1a3a1a") if done else ("#4a5568", "⬜", "#0d1117")
        bar_parts.append(
            f'<div style="flex:1;text-align:center;padding:6px 3px;background:{bg};'
            f'border-radius:5px;border:1px solid {col}30">'
            f'<div style="font-size:14px">{icon}</div>'
            f'<div style="font-size:9px;color:{col};margin-top:2px;font-weight:600">{label}</div>'
            f'</div>'
        )
        if idx < len(steps) - 1:
            ac = "#3fb950" if done else "#2a2a2a"
            bar_parts.append(
                f'<div style="display:flex;align-items:center;color:{ac};font-size:11px;padding:0 2px">→</div>'
            )
    step_bar_html = "".join(bar_parts)

    # What's next description
    price = pipe.get("price", 0)
    cp    = f"${price:,.0f}"

    if stage == 0:
        title = "Waiting for 1H Structure"
        desc  = (
            f"BTC price is {cp}. No clear higher-high / higher-low (bullish) "
            f"or lower-low / lower-high (bearish) pattern on the 1H chart yet. "
            f"The bot waits until structure becomes unambiguous."
        )
        next_action = "Watch for 1H to form a clear HH+HL (bullish) or LL+LH (bearish) sequence."

    elif stage == 1:
        fib_mid = pipe.get("fib_mid")
        near    = pipe.get("nearest_poi")
        fib_ok  = pipe.get("fib_ok", False)
        if not fib_ok and fib_mid:
            dir_  = "discount (≤" if bias == "bullish" else "premium (≥"
            title = "Waiting for Fib Discount/Premium Zone"
            desc  = (
                f"1H bias is <strong>{'▲ BULLISH' if bias=='bullish' else '▼ BEARISH'}</strong>. "
                f"Fib 50% midpoint is ${fib_mid:,.0f}. Current price {cp} is not yet in the "
                f"{dir_} ${fib_mid:,.0f}) zone. The bot only looks for longs in discount and shorts in premium."
            )
            next_action = f"Wait for price to enter the {'discount (below' if bias=='bullish' else 'premium (above'} ${fib_mid:,.0f})."
        elif near:
            dist = abs(price - (near["low"] + near["high"]) / 2)
            dir_ = "rally into" if bias == "bullish" else "sell into"
            zone_str = f'${near["low"]:,.0f} – ${near["high"]:,.0f}'
            title = f"Fib OK — Watching for Price to Enter {near['kind']} Zone"
            desc  = (
                f"1H bias is <strong>{'▲ BULLISH' if bias=='bullish' else '▼ BEARISH'}</strong>. "
                f"Fib discount/premium filter passed. "
                f"A {near['kind']} zone exists at {zone_str} — ${dist:,.0f} away. "
                f"No trade setup until price enters the POI."
            )
            next_action = f"Wait for price to {dir_} the {near['kind']} at {zone_str}."
        else:
            title = "No POI Zones Found"
            desc  = f"1H bias is {'bullish' if bias=='bullish' else 'bearish'} but no OB or FVG zones detected."
            next_action = "Monitor for a displacement candle to form a new 1H OB or FVG."

    elif stage == 2:
        apoi = pipe.get("active_poi") or {}
        swing_dir = "swing low" if bias == "bullish" else "swing high"
        sweep_dir = "below" if bias == "bullish" else "above"
        fib_mid   = pipe.get("fib_mid")
        mid_str   = f" (${fib_mid:,.0f})" if fib_mid else ""
        poi_lo = f"${apoi.get('low', 0):,.0f}"
        poi_hi = f"${apoi.get('high', 0):,.0f}"
        title  = f"In Fib{mid_str} + {apoi.get('kind','POI')} Zone — Waiting for Sweep"
        desc   = (
            f"Price {cp} is in the {'discount' if bias=='bullish' else 'premium'} zone "
            f"and inside the 1H {apoi.get('kind','POI')} [{poi_lo} – {poi_hi}]. "
            f"Now watching 5M for a stop-hunt: wick piercing {sweep_dir} a prior {swing_dir} "
            f"and closing back — the inducement sweep that shows smart money absorbed retail stops."
        )
        next_action = f"Watch 5M for a wick {sweep_dir} a recent {swing_dir} with close back above/below it."

    elif stage == 3:
        sw_lv  = pipe.get("sweep_level", 0)
        sw_wk  = pipe.get("sweep_wick", 0)
        d_atr  = CFG["poi"]["displacement_atr"]
        disp_dir = "bullish" if bias == "bullish" else "bearish"
        title  = "Sweep Confirmed — Waiting for Displacement"
        desc   = (
            f"5M sweep of ${sw_lv:,.0f} confirmed (wick to ${sw_wk:,.0f}). "
            f"Now need a strong {disp_dir} displacement candle (range ≥ {d_atr}×ATR) "
            f"to prove institutional momentum is behind the move — not just a wick noise."
        )
        next_action = f"Watch for a large {disp_dir} candle (≥{d_atr}×ATR range) after the sweep bar."

    elif stage == 4:
        choch_ref = pipe.get("choch_ref_level", 0)
        brk_dir   = "above" if bias == "bullish" else "below"
        sw_wk     = pipe.get("sweep_wick", 0)
        title = "Displacement Confirmed — Waiting for CHoCH"
        desc  = (
            f"Displacement candle confirmed. Now need a 5M candle to "
            f"<strong>close {brk_dir} ${choch_ref:,.0f}</strong> — "
            f"the swing level formed before the sweep. This Change of Character (CHoCH) "
            f"confirms the structural reversal and triggers the entry signal."
        )
        next_action = f"Watch for a 5M close {brk_dir} ${choch_ref:,.0f}. Entry with SL at sweep wick ${sw_wk:,.0f}."

    else:  # stage 5 = signal
        sw_wk  = pipe.get("sweep_wick", price)
        buf    = CFG["risk"]["sl_buffer"]
        sl_p   = sw_wk * (1 - buf) if bias == "bullish" else sw_wk * (1 + buf)
        r_dist = abs(price - sl_p)
        tc     = CFG.get("targets", {})
        tp_r   = tc.get("fallback_r", 2.0)
        tp_p   = price + r_dist * tp_r if bias == "bullish" else price - r_dist * tp_r
        tp1_p  = price + r_dist if bias == "bullish" else price - r_dist
        title  = f"{'▲ LONG' if bias=='bullish' else '▼ SHORT'} SIGNAL ACTIVE"
        desc   = (
            f"All 5 conditions met. Entry at ~{cp}, "
            f"SL at ${sl_p:,.0f} (sweep wick ${sw_wk:,.0f}), "
            f"TP1 at ${tp1_p:,.0f} (1R, 50% off → SL→BE), "
            f"TP2 at ${tp_p:,.0f} ({tp_r}R or nearest BSL/SSL pool)."
        )
        next_action = "CONFIRM token required before any order is placed (CLAUDE.md §7)."

    stage_color = ["#4a5568","#d29922","#e3b341","#58a6ff","#c97dff","#3fb950"][min(stage, 5)]
    signal_cls  = {"LONG": "sig-long", "SHORT": "sig-short"}.get(signal, "sig-flat")
    signal_txt  = {"LONG": "▲ LONG", "SHORT": "▼ SHORT", "FLAT": "— FLAT"}[signal]

    return f"""
    <div class="card full-width">
      <div class="card-title">Setup Proximity — How Close to Entry</div>
      <div style="display:flex;gap:6px;margin-bottom:14px;align-items:center">
        {step_bar_html}</div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start">
        <div>
          <div style="font-size:13px;font-weight:700;color:{stage_color};margin-bottom:6px">{title}</div>
          <div style="font-size:12px;color:#8a9ab0;line-height:1.65">{desc}</div>
        </div>
        <div style="background:#0d1117;border:1px solid #2a3040;border-radius:6px;padding:12px">
          <div style="font-size:10px;font-weight:700;letter-spacing:.1em;color:#4a5568;margin-bottom:8px;text-transform:uppercase">What Needs to Happen</div>
          <div style="font-size:12px;color:#c0c8d8;line-height:1.6">{next_action}</div>
          <div style="margin-top:12px;text-align:center">
            <span class="signal-badge {signal_cls}">{signal_txt}</span>
          </div>
        </div>
      </div>
    </div>"""


# ── CSS ────────────────────────────────────────────────────────────────────────

_CSS = """
:root {
    --bg:      #0d1117;
    --bg2:     #161b22;
    --bg3:     #1c2230;
    --border:  #30363d;
    --text:    #c9d1d9;
    --muted:   #6e7681;
    --green:   #3fb950;
    --red:     #f85149;
    --yellow:  #d29922;
    --blue:    #58a6ff;
    --orange:  #e3b341;
    --mono:    'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: var(--mono);
       font-size: 13px; line-height: 1.55; padding: 16px; }
a { color: var(--blue); text-decoration: none; }
a:hover { text-decoration: underline; }

.header { display:flex; align-items:center; justify-content:space-between;
          background:var(--bg2); border:1px solid var(--border); border-radius:8px;
          padding:12px 18px; margin-bottom:14px; }
.header-left { display:flex; align-items:center; gap:14px; }
.logo { font-size:16px; font-weight:700; color:var(--blue); letter-spacing:.05em; }
.badge { font-size:11px; padding:2px 8px; border-radius:4px; font-weight:600; letter-spacing:.06em; }
.badge-demo  { background:#1c2f50; color:var(--blue); border:1px solid #2d4a7a; }
.badge-live  { background:#3a1a1a; color:var(--red);  border:1px solid #6b2020; }
.badge-paper { background:#1a2a1a; color:var(--green); border:1px solid #204020; }
.header-right { color:var(--muted); font-size:12px; text-align:right; }
.refresh-link { color:var(--blue); font-size:11px; border:1px solid var(--border);
                border-radius:4px; padding:2px 8px; margin-left:8px; }

.grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px; }
.grid-3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-bottom:12px; }

.card { background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:14px 16px; }
.card-title { font-size:10px; font-weight:700; letter-spacing:.12em; text-transform:uppercase;
              color:var(--muted); margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid var(--border); }
.metric { display:flex; justify-content:space-between; align-items:baseline; margin:5px 0; }
.metric-label { color:var(--muted); font-size:12px; }
.metric-value { font-size:14px; font-weight:600; }

.green  { color:var(--green); }  .red   { color:var(--red); }
.yellow { color:var(--yellow); } .blue  { color:var(--blue); }
.muted  { color:var(--muted); }  .orange{ color:var(--orange); }

.gate { display:flex; align-items:center; gap:8px; padding:5px 0; border-bottom:1px solid var(--border); }
.gate:last-child { border-bottom:none; }
.gate-icon  { font-size:14px; width:20px; text-align:center; }
.gate-label { color:var(--muted); width:90px; font-size:12px; }
.gate-value { font-size:12px; flex:1; }
.gate-pass  { color:var(--green); }
.gate-fail  { color:var(--red); }
.gate-wait  { color:var(--muted); }

.signal-badge { display:inline-block; padding:4px 14px; border-radius:6px;
                font-size:15px; font-weight:700; letter-spacing:.08em; margin-top:4px; }
.sig-long  { background:#1a3a1a; color:var(--green); border:1px solid #2a5a2a; }
.sig-short { background:#3a1a1a; color:var(--red);   border:1px solid #6b2020; }
.sig-flat  { background:var(--bg3); color:var(--muted); border:1px solid var(--border); }

.trades-table { width:100%; border-collapse:collapse; font-size:12px; }
.trades-table th { text-align:left; padding:6px 8px; border-bottom:1px solid var(--border);
                   color:var(--muted); font-weight:600; font-size:10px; letter-spacing:.08em;
                   text-transform:uppercase; }
.trades-table td { padding:5px 8px; border-bottom:1px solid #1c2230; }
.trades-table tr:last-child td { border-bottom:none; }
.trades-table tr:hover td { background:var(--bg3); }
.tag { display:inline-block; padding:1px 6px; border-radius:3px; font-size:10px; font-weight:700; }
.tag-long  { background:#1a3a1a; color:var(--green); }
.tag-short { background:#3a1a1a; color:var(--red); }
.tag-ob    { background:#1c2f50; color:var(--blue); }
.tag-fvg   { background:#2a2010; color:var(--orange); }

.log-box { background:var(--bg); border:1px solid var(--border); border-radius:6px;
           padding:10px 12px; max-height:280px; overflow-y:auto; font-size:11px; line-height:1.6; }
.log-info  { color:var(--muted); }
.log-warn  { color:var(--yellow); }
.log-error { color:var(--red); }
.log-debug { color:#333; }
.log-signal{ color:var(--green); font-weight:600; }

.pos-badge { padding:3px 10px; border-radius:5px; font-weight:700; font-size:12px; letter-spacing:.06em; }
.pos-long  { background:#1a3a1a; color:var(--green); border:1px solid #2a5a2a; }
.pos-short { background:#3a1a1a; color:var(--red);   border:1px solid #6b2020; }
.pos-flat  { background:var(--bg3); color:var(--muted); border:1px solid var(--border); }

.full-width { grid-column:1 / -1; }

/* ── SMC Checklist ─────────────────────────────────────────────────────────── */
.cl-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.cl-card { background:var(--bg3); border:1px solid var(--border); border-radius:8px; padding:12px 14px; break-inside:avoid; }
.cl-card.full { grid-column:1/-1; }
.cl-h2 { font-size:11px; font-weight:700; letter-spacing:.10em; text-transform:uppercase;
         color:var(--blue); margin:0 0 9px; padding-bottom:6px; border-bottom:1px solid var(--border); }
.cl-h3 { font-size:11px; font-weight:700; color:var(--orange); margin:8px 0 5px; }
.cl-item { display:flex; gap:8px; align-items:flex-start; margin:5px 0; font-size:12px; line-height:1.4; }
.cl-box  { appearance:none; -webkit-appearance:none; width:15px; height:15px; flex:0 0 15px; margin-top:1px;
           border:1.5px solid #3d4a5a; border-radius:3px; background:var(--bg2); cursor:pointer; position:relative; }
.cl-box:checked { background:var(--green); border-color:var(--green); }
.cl-box:checked::after { content:"✓"; position:absolute; top:-1px; left:1px; font-size:11px; color:#000; font-weight:900; }
.cl-note { background:var(--bg2); border-left:3px solid var(--blue); padding:7px 10px; font-size:11px;
           color:var(--muted); border-radius:4px; margin-top:8px; }
.cl-table { width:100%; border-collapse:collapse; font-size:11.5px; margin-top:6px; }
.cl-table th { background:var(--bg2); padding:5px 7px; color:var(--muted); font-weight:600;
               font-size:10px; letter-spacing:.06em; text-transform:uppercase; border-bottom:1px solid var(--border); }
.cl-table td { padding:5px 7px; border-bottom:1px solid var(--bg); font-size:11.5px; }
.cl-table tr:last-child td { border-bottom:none; }
.grade-bar { display:flex; gap:8px; margin-bottom:10px; }
.grade-box { flex:1; text-align:center; padding:8px 4px; border-radius:6px; border:2px solid transparent;
             font-size:13px; font-weight:700; letter-spacing:.06em; transition:all .2s; }
.grade-A { background:#1a3a1a; color:var(--green); border-color:#2a5a2a; }
.grade-B { background:#2a2010; color:var(--orange); border-color:#4a3820; }
.grade-C { background:#3a1a1a; color:var(--red); border-color:#6b2020; }
.grade-inactive { background:var(--bg2); color:var(--muted); border-color:var(--border); opacity:.4; }
.cl-reset { font-size:10px; color:var(--muted); border:1px solid var(--border); border-radius:4px;
            padding:2px 8px; cursor:pointer; background:transparent; float:right; margin-top:-2px; }
.cl-reset:hover { color:var(--text); border-color:#6e7681; }
.cl-progress { height:4px; background:var(--bg2); border-radius:2px; margin-bottom:10px; overflow:hidden; }
.cl-progress-bar { height:100%; background:var(--green); border-radius:2px; transition:width .3s; }
"""


# ── Cycle flow panel ───────────────────────────────────────────────────────────

def _cycle_flow_html() -> str:
    """Static panel describing the per-5M-close execution path of bot.py."""

    def step(n: str, label: str, detail: str, color: str = "#58a6ff", branch: str = "") -> str:
        branch_html = (
            f'<div style="margin-top:5px;font-size:10.5px;color:#d29922;border-left:2px solid #d29922;'
            f'padding-left:8px">{branch}</div>'
        ) if branch else ""
        return (
            f'<div style="display:flex;gap:10px;align-items:flex-start;margin-bottom:10px">'
            f'<div style="flex:0 0 22px;height:22px;border-radius:50%;background:{color}22;'
            f'border:1.5px solid {color};font-size:10px;font-weight:700;color:{color};'
            f'display:flex;align-items:center;justify-content:center;margin-top:1px">{n}</div>'
            f'<div style="flex:1">'
            f'<div style="font-size:12px;font-weight:700;color:{color};margin-bottom:2px">{label}</div>'
            f'<div style="font-size:11.5px;color:#8a9ab0;line-height:1.55">{detail}</div>'
            f'{branch_html}</div></div>'
        )

    def conn() -> str:
        return '<div style="margin-left:10px;border-left:1.5px solid #2a3040;height:8px;margin-bottom:2px"></div>'

    col1 = (
        step("1", "Fetch balance",
             "ccxt public call to Bybit. If the API fails 5 consecutive times the circuit-breaker "
             "skips the rest of the cycle and logs a warning.",
             "#58a6ff") +
        conn() +
        step("2", "trading_allowed?",
             "Three pure-function guards in <code>risk.py</code>: <em>drawdown_breached</em> (equity vs peak), "
             "<em>daily_loss_breached</em> (day-start equity vs now), <em>consecutive_losses_breached</em> "
             "(counter vs limit). Any failure halts the cycle.",
             "#d29922",
             branch="⛔ Any guard fires → HALT. Telegram alert. Cycle ends.") +
        conn() +
        step("3", "Position already open?",
             "<code>executor.get_position()</code>. If position is open, exit here (one-at-a-time rule). "
             "If <code>open_order_id</code> is set but size=0, the trade just closed: query closed-PnL "
             "filtered by <code>entry_time</code>, update loss counter, clear BotState, alert.",
             "#c97dff",
             branch="→ Position live: return (wait). → Position just closed: attribute PnL, update counter, persist state.") +
        conn() +
        step("4", "1H Bias",
             "<code>structure.get_bias(df_1h)</code> — scans the 1H swing sequence. "
             "HH+HL → bullish. LL+LH → bearish. Neutral if no pattern.",
             "#3fb950",
             branch="⬛ neutral → cycle ends immediately.") +
        conn() +
        step("5", "Fib 50% filter",
             "<code>fib.get_fib_midpoint()</code> finds the 50% equilibrium of the most recent "
             "swing range. <code>fib.fib_filter()</code> passes longs only in discount (price ≤ mid) "
             "and shorts only in premium (price ≥ mid).",
             "#e3b341",
             branch="✗ price outside the correct half → cycle ends.") +
        conn() +
        step("6", "1H POI",
             "<code>poi.get_pois(df_1h)</code> returns Order Block and FVG zones qualified by "
             "≥1.5×ATR displacement. <code>poi.price_in_poi(price, zones)</code> checks whether the "
             "current 5M price is already inside a zone.",
             "#58a6ff",
             branch="✗ no zones, or price not yet inside one → cycle ends.")
    )

    col2 = (
        step("7", "5M Liquidity sweep",
             "<code>liquidity.get_sweep(df_5m)</code> scans the last N 5M bars for a wick that "
             "pierces a prior swing low (long) or swing high (short) and closes back above/below it — "
             "the stop-hunt that absorbs retail orders.",
             "#58a6ff",
             branch="✗ no qualifying sweep → cycle ends.") +
        conn() +
        step("8", "Displacement gate",
             "<code>liquidity.check_displacement(df_5m, sweep_bar)</code> — in the bars after the "
             "sweep, at least one candle must have a range ≥ 1.5×ATR(14) in the trade direction. "
             "Proves institutional momentum; rules out noise wicks.",
             "#c97dff",
             branch="✗ no displacement candle → cycle ends.") +
        conn() +
        step("9", "5M CHoCH",
             "<code>confirmation.get_choch(df_5m)</code> — after the sweep a 5M bar must close "
             "above the pre-sweep swing high (long) or below the pre-sweep swing low (short). "
             "This Change of Character confirms the structural reversal.",
             "#3fb950",
             branch="✗ CHoCH not confirmed → cycle ends.") +
        conn() +
        step("10", "SL at sweep wick",
             "Stop-loss computed from the sweep wick extreme with a buffer from config: "
             "<code>wick × (1 − sl_buffer)</code> for longs, <code>wick × (1 + sl_buffer)</code> "
             "for shorts. R = |entry − SL|.",
             "#d29922") +
        conn() +
        step("11", "TP via targets.py",
             "<code>targets.get_tp_level()</code> finds the nearest BSL cluster (equal highs) for longs "
             "or SSL cluster (equal lows) for shorts that provides ≥ min_r reward. Falls back to "
             "<code>fallback_r × stop_dist</code> if no qualifying pool exists.",
             "#d29922") +
        conn() +
        step("12", "Position sizing",
             "<code>risk.calc_qty(balance, sl_dist, risk_pct)</code> sizes to risk_pct of equity, "
             "snaps to Bybit lot step. Returns 0.0 if result is below minimum — trade skipped cleanly.",
             "#58a6ff",
             branch="qty = 0 → log skip, cycle ends.") +
        conn() +
        step("13", "Log or Place",
             "All gates passed. If <code>signal_only_mode=True</code>: write signal row to CSV and "
             "send Telegram alert — no order sent. If <code>LIVE_TRADING=True</code>: "
             "<code>executor.place_order()</code> sends a market entry with attached SL/TP, "
             "verifies <code>retCode=0</code> and <code>orderId</code>, persists BotState, alerts.",
             "#3fb950",
             branch="signal_only → log + alert only. | LIVE=True → retCode-checked order + BotState flush + alert.")
    )

    return (
        '<div class="card full-width">'
        '<div class="card-title">Bot Cycle Flow — What Happens on Every 5M Close</div>'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 28px">'
        f'<div>{col1}</div><div>{col2}</div>'
        '</div></div>'
    )


# ── SMC Checklist panel (12-phase trade lifecycle) ────────────────────────────

def _checklist_html() -> str:
    """
    12-phase SMC trade lifecycle checklist.
    State stored in localStorage — survives 30s auto-refresh.
    Grade computed live in JS.
    """
    def item(cid: str, text: str) -> str:
        return (f'<div class="cl-item">'
                f'<input type="checkbox" class="cl-box" id="{cid}" onchange="clSave(this)">'
                f'<label for="{cid}">{text}</label></div>')

    def nt(cid: str, text: str) -> str:
        return (f'<div class="cl-item">'
                f'<input type="checkbox" class="cl-box" id="{cid}" onchange="clSave(this)" '
                f'style="border-color:#f85149">'
                f'<label for="{cid}" style="color:#f85149">NO TRADE: {text}</label></div>')

    def note(text: str) -> str:
        return f'<div class="cl-note">{text}</div>'

    def ph(num: str, title: str) -> str:
        return (f'<div style="font-size:10px;font-weight:700;letter-spacing:.10em;'
                f'text-transform:uppercase;color:var(--blue);margin:0 0 8px;'
                f'padding-bottom:6px;border-bottom:1px solid var(--border)">'
                f'Phase {num} — {title}</div>')

    def sub(text: str) -> str:
        return f'<div style="font-size:10px;font-weight:700;color:var(--orange);margin:8px 0 4px">{text}</div>'

    # ── Phase 1 + 2 ──────────────────────────────────────────────────────────
    p1_2 = f"""
    <div class="cl-card">
      {ph("1", "Market Scan — HTF Bias")}
      {item("p1a", "Trend is clear OR confirmed CHoCH has shifted bias")}
      {item("p1b", "External swing high/low identified")}
      {item("p1c", "Not trading random internal chop")}
      {item("p1d", "Overall directional bias established")}
      {nt("p1_nt_a", "HTF structure is unclear")}
      {nt("p1_nt_b", "Market ranging without clear narrative")}

      <div style="border-top:1px solid var(--border);margin:10px 0 8px"></div>
      {ph("2", "Premium / Discount Location")}
      {note("Draw dealing range: external swing low → swing high")}
      {sub("Long")}
      {item("p2a", "Current price is in Discount (<50% of range)")}
      {sub("Short")}
      {item("p2b", "Current price is in Premium (>50% of range)")}
      {nt("p2_nt_a", "Price is in the middle of the range")}
      {nt("p2_nt_b", "Buying premium or selling discount")}
    </div>"""

    # ── Phase 3 ──────────────────────────────────────────────────────────────
    p3 = f"""
    <div class="cl-card">
      {ph("3", "POI Identification")}
      {sub("Order Block")}
      {item("p3a", "OB aligns with meaningful BOS or CHoCH")}
      {item("p3b", "Strong displacement away from OB")}
      {item("p3c", "OB remains fresh / unmitigated")}
      {item("p3d", "Liquidity confluence exists")}
      {item("p3e", "FVG confluence exists")}
      {sub("Fair Value Gap")}
      {item("p3f", "FVG caused meaningful BOS or CHoCH")}
      {item("p3g", "FVG aligns with trend or confirmed reversal")}
      {item("p3h", "FVG remains unmitigated")}
      {sub("POI Selected")}
      {item("p3_ob", "Order Block")}
      {item("p3_fvg", "Fair Value Gap")}
      {item("p3_both", "OB + FVG Combination")}
      {nt("p3_nt_a", "OB lacks displacement")}
      {nt("p3_nt_b", "FVG did not create BOS/CHoCH")}
      {nt("p3_nt_c", "Zone already mitigated")}
    </div>"""

    # ── Phase 4 ──────────────────────────────────────────────────────────────
    p4 = f"""
    <div class="cl-card">
      {ph("4", "Liquidity Narrative")}
      {sub("Liquidity To Be Swept")}
      {item("p4a", "Equal highs")}
      {item("p4b", "Equal lows")}
      {item("p4c", "Previous day high")}
      {item("p4d", "Previous day low")}
      {item("p4e", "Session high / low")}
      {item("p4f", "Swing high / low")}
      {sub("Target After Entry")}
      {item("p4g", "Internal liquidity identified")}
      {item("p4h", "External liquidity identified")}
      {item("p4i", "Target pool is significant enough")}
      {nt("p4_nt_a", "Liquidity story is unclear")}
      {nt("p4_nt_b", "No obvious target pool exists")}
    </div>"""

    # ── Phase 5 + 9 ──────────────────────────────────────────────────────────
    p5_9 = f"""
    <div class="cl-card">
      {ph("5", "Wait for POI")}
      {item("p5a", "Price has reached HTF POI")}
      {item("p5b", "No early entry before POI")}
      {item("p5c", "Market is reacting inside the zone")}
      {item("p5d", "Waiting for confirmation, not predicting")}

      <div style="border-top:1px solid var(--border);margin:10px 0 8px"></div>
      {ph("9", "Session Filter")}
      {item("p9a", "Trading during active market session")}
      {item("p9b", "Avoiding low-volume chop")}
      {item("p9c", "Not entering directly into major news")}
      {sub("Session")}
      {item("p9_lon", "London")}
      {item("p9_ny",  "New York")}
      {item("p9_asi", "Asia")}
      {item("p9_cry", "Crypto Active Hours")}
      {nt("p9_nt", "Outside active session — bot session filter is OFF (future trial)")}
    </div>"""

    # ── Phase 6 ──────────────────────────────────────────────────────────────
    p6 = f"""
    <div class="cl-card">
      {ph("6", "LTF Confirmation (5M)")}
      {sub("Sweep")}
      {item("p6a", "Liquidity sweep occurred")}
      {sub("Structure Shift")}
      {item("p6b", "Valid CHoCH formed")}
      {item("p6c", "Valid MSS formed")}
      {item("p6d", "Valid BOS formed")}
      {sub("Displacement")}
      {item("p6e", "Strong displacement candle appeared")}
      {item("p6f", "New 5M OB created")}
      {item("p6g", "New 5M FVG created")}
      {sub("Retracement")}
      {item("p6h", "Waiting for retrace into execution zone")}
      {item("p6i", "Not chasing impulse candles")}
      {nt("p6_nt_a", "No sweep")}
      {nt("p6_nt_b", "No structure shift (CHoCH / MSS / BOS)")}
      {nt("p6_nt_c", "No displacement")}
    </div>"""

    # ── Phase 7 ──────────────────────────────────────────────────────────────
    p7 = f"""
    <div class="cl-card">
      {ph("7", "Execution — Entry Model")}
      {sub("Confirmation (Preferred)")}
      {item("p7a", "Retest of 5M OB")}
      {item("p7b", "Retest of 5M FVG")}
      {item("p7c", "Entry after sweep + CHoCH/BOS")}
      {sub("Refined")}
      {item("p7d", "50% retracement of impulse")}
      {item("p7e", "OB midpoint / FVG midpoint")}
      {sub("Aggressive")}
      {item("p7f", "Direct touch of HTF POI")}
      {item("p7g", "Exceptional confluence present")}
      {sub("Selected Model")}
      {item("p7_conf", "Confirmation")}
      {item("p7_ref",  "Refined")}
      {item("p7_agg",  "Aggressive")}
    </div>"""

    # ── Phase 8 ──────────────────────────────────────────────────────────────
    p8 = f"""
    <div class="cl-card">
      {ph("8", "Risk Management — Stop Loss")}
      {sub("Long")}
      {item("p8a", "SL below sweep low")}
      {item("p8b", "SL below OB/FVG invalidation")}
      {sub("Short")}
      {item("p8c", "SL above sweep high")}
      {item("p8d", "SL above OB/FVG invalidation")}
      {sub("Risk Check")}
      {item("p8e", "Stop is placed beyond invalidation")}
      {item("p8f", "Position size adjusted correctly")}
      {item("p8g", "Risk % acceptable")}
      {nt("p8_nt_a", "Stop placement is arbitrary")}
      {nt("p8_nt_b", "Required position size exceeds risk limits")}
    </div>"""

    # ── Phase 10 ─────────────────────────────────────────────────────────────
    p10 = f"""
    <div class="cl-card">
      {ph("10", "Profit Planning")}
      {sub("TP1")}
      {item("p10a", "Nearest internal liquidity identified")}
      {sub("TP2")}
      {item("p10b", "Structural high/low as target")}
      {item("p10c", "External liquidity pool as target")}
      {sub("R:R Check")}
      {note("Confirmation ≥ 1:2 preferred / 1:3 ideal · Refined ≥ 1:3 · Aggressive ≥ 1:2 minimum")}
      {item("p10d", "RR meets minimum for selected entry model")}
      {nt("p10_nt", "RR below minimum threshold — do not enter")}
    </div>"""

    # ── Phase 11 + Grade ─────────────────────────────────────────────────────
    p11 = f"""
    <div class="cl-card full">
      {ph("11", "Final Grade — Execution Decision")}
      <div class="cl-progress"><div class="cl-progress-bar" id="clProg" style="width:0%"></div></div>
      <div class="grade-bar">
        <div class="grade-box grade-inactive" id="grA">A — FULL RISK</div>
        <div class="grade-box grade-inactive" id="grB">B — REDUCED RISK</div>
        <div class="grade-box grade-inactive" id="grC">C — NO TRADE</div>
      </div>
      <table class="cl-table">
        <tr><th>Grade</th><th>Requirements</th><th>Action</th></tr>
        <tr><td style="color:var(--green);font-weight:700">A</td>
            <td>Clear HTF bias + discount/premium + fresh POI + sweep + LTF shift + strong RR</td>
            <td style="color:var(--green)">Full risk per plan</td></tr>
        <tr><td style="color:var(--orange);font-weight:700">B</td>
            <td>One minor weakness — structure still valid</td>
            <td style="color:var(--orange)">Reduce size or be selective</td></tr>
        <tr><td style="color:var(--red);font-weight:700">C</td>
            <td>Missing critical component or any NO-TRADE box checked</td>
            <td style="color:var(--red)">No trade</td></tr>
      </table>
      <button class="cl-reset" onclick="clReset()" style="margin-top:10px">Reset all</button>
    </div>"""

    # ── Phase 12 + Exit ──────────────────────────────────────────────────────
    p12 = f"""
    <div class="cl-card">
      {ph("12", "Trade Management (After Entry)")}
      {item("p12a", "Trade executed according to plan")}
      {item("p12b", "No moving SL emotionally")}
      {item("p12c", "No adding to losers")}
      {item("p12d", "TP1 managed according to plan")}
      {item("p12e", "Partial profit rules followed")}
      {item("p12f", "Break-even rules followed")}
      {item("p12g", "No discretionary interference")}
    </div>"""

    p_exit = f"""
    <div class="cl-card">
      {ph("12", "Exit & Journal")}
      {sub("Outcome")}
      {item("ex_tp1", "TP1 Hit")}
      {item("ex_tp2", "TP2 Hit")}
      {item("ex_full", "Full TP")}
      {item("ex_be",   "Break Even")}
      {item("ex_sl",   "Stop Loss")}
      {sub("Post-Trade Review")}
      {item("ex_r1", "Followed checklist completely")}
      {item("ex_r2", "Followed risk rules")}
      {item("ex_r3", "Followed entry model")}
      {item("ex_r4", "Emotional mistakes noted")}
      {item("ex_r5", "Screenshots saved")}
    </div>"""

    # Critical checkboxes → all required for Grade A
    critical = [
        "p1a","p1b","p1c",
        "p5a","p5b",
        "p6a","p6e",
        "p8e","p8f",
        "p10d",
        "p4g","p4h","p4i",
    ]
    # No-trade checkboxes → any checked = Grade C
    no_trade = [
        "p1_nt_a","p1_nt_b",
        "p2_nt_a","p2_nt_b",
        "p3_nt_a","p3_nt_b","p3_nt_c",
        "p4_nt_a","p4_nt_b",
        "p6_nt_a","p6_nt_b","p6_nt_c",
        "p8_nt_a","p8_nt_b",
        "p9_nt",
        "p10_nt",
    ]
    all_ids = [
        "p1a","p1b","p1c","p1d","p1_nt_a","p1_nt_b",
        "p2a","p2b","p2_nt_a","p2_nt_b",
        "p3a","p3b","p3c","p3d","p3e","p3f","p3g","p3h",
        "p3_ob","p3_fvg","p3_both","p3_nt_a","p3_nt_b","p3_nt_c",
        "p4a","p4b","p4c","p4d","p4e","p4f","p4g","p4h","p4i","p4_nt_a","p4_nt_b",
        "p5a","p5b","p5c","p5d",
        "p6a","p6b","p6c","p6d","p6e","p6f","p6g","p6h","p6i",
        "p6_nt_a","p6_nt_b","p6_nt_c",
        "p7a","p7b","p7c","p7d","p7e","p7f","p7g","p7_conf","p7_ref","p7_agg",
        "p8a","p8b","p8c","p8d","p8e","p8f","p8g","p8_nt_a","p8_nt_b",
        "p9a","p9b","p9c","p9_lon","p9_ny","p9_asi","p9_cry","p9_nt",
        "p10a","p10b","p10c","p10d","p10_nt",
        "p12a","p12b","p12c","p12d","p12e","p12f","p12g",
        "ex_tp1","ex_tp2","ex_full","ex_be","ex_sl",
        "ex_r1","ex_r2","ex_r3","ex_r4","ex_r5",
    ]

    critical_js  = str(critical).replace("'", '"')
    no_trade_js  = str(no_trade).replace("'", '"')
    all_ids_js   = str(all_ids).replace("'", '"')

    js = f"""
    <script>
    const CL_KEY2   = 'smc_cl_v2';
    const CRITICAL2 = {critical_js};
    const NO_TRADE2 = {no_trade_js};
    const ALL2      = {all_ids_js};

    function clLoad() {{
      const saved = JSON.parse(localStorage.getItem(CL_KEY2) || '{{}}');
      ALL2.forEach(id => {{
        const el = document.getElementById(id);
        if (el) el.checked = !!saved[id];
      }});
      clGrade();
    }}
    function clSave(el) {{
      const saved = JSON.parse(localStorage.getItem(CL_KEY2) || '{{}}');
      saved[el.id] = el.checked;
      localStorage.setItem(CL_KEY2, JSON.stringify(saved));
      clGrade();
    }}
    function clReset() {{
      localStorage.removeItem(CL_KEY2);
      ALL2.forEach(id => {{ const el = document.getElementById(id); if (el) el.checked = false; }});
      clGrade();
    }}
    function clGrade() {{
      const checked = new Set(ALL2.filter(id => {{ const el = document.getElementById(id); return el && el.checked; }}));
      const total = ALL2.length;
      const done  = checked.size;
      const pct   = total ? Math.round(done / total * 100) : 0;
      const prog  = document.getElementById('clProg');
      if (prog) prog.style.width = pct + '%';

      const ntFail  = NO_TRADE2.some(id => checked.has(id));
      const critOk  = CRITICAL2.every(id => checked.has(id));
      const shiftOk = checked.has('p6b') || checked.has('p6c') || checked.has('p6d');
      const pdOk    = checked.has('p2a') || checked.has('p2b');
      const poiOk   = checked.has('p3_ob') || checked.has('p3_fvg') || checked.has('p3_both');

      let grade = 'C';
      if (ntFail) {{
        grade = 'C';
      }} else if (critOk && shiftOk && pdOk && poiOk && done >= Math.round(total * 0.70)) {{
        grade = 'A';
      }} else if (done >= Math.round(total * 0.45)) {{
        grade = 'B';
      }}

      ['grA','grB','grC'].forEach(id => {{
        const el = document.getElementById(id);
        if (!el) return;
        const letter = id.replace('gr','');
        el.className = 'grade-box ' + (grade === letter ? 'grade-' + letter : 'grade-inactive');
      }});
    }}
    document.addEventListener('DOMContentLoaded', clLoad);
    </script>"""

    return f"""
    <div class="card full-width">
      <div class="card-title">SMC Trade Lifecycle Checklist — Scan → Entry → Management → Journal</div>
      <div class="cl-grid">
        {p1_2}{p3}{p4}{p5_9}{p6}{p7}{p8}{p10}{p11}{p12}{p_exit}
      </div>
      {js}
    </div>"""


# ── Forex charts (SMC 4H→1H + Asian Session) ─────────────────────────────────

FOREX_PAIRS = ["EURUSD", "GBPUSD"]
FOREX_HTF   = "240m"   # 4H  — bias + POI
FOREX_LTF   = "60m"    # 1H  — sweep + CHoCH / session box

_FOREX_CFG: dict = {
    "structure": {"swing_n": 3},
    "fib":       {"level": 0.5},
    "poi": {
        "ob_lookback": 60, "fvg_lookback": 30,
        "displacement_atr": 1.0,
        "mitigation_enabled": False, "mitigation_pct": 50, "mitigation_mode": "wick",
    },
    "liquidity": {
        "swing_n": 2, "lookback": 20, "displacement_atr": 1.0,
        "ltf_poi_lookback": 15, "fvg_retest_enabled": False, "fvg_retest_lookforward": 20,
    },
    "confirmation": {"lookback": 8},
    "targets": {"equal_level_tolerance": 0.0005, "min_r": 1.5, "fallback_r": 3.0},
    "risk":    {"sl_buffer": 0.0002, "target_r": 3.0},
    "session": {
        "asian": {
            "start_h": 0, "end_h": 8,
            "range_thr": 0.5, "trend_thr": 0.7,
            "sweep_beyond_pct": 0.002, "sl_pct_of_range": 0.25,
            "target_r": 5.0, "trend_first_close_r": 4.0, "first_close_pct": 0.75,
        }
    },
}


def _load_forex(symbol: str, tf: str):
    p = ROOT / "data" / "cache" / f"{symbol}_{tf}.parquet"
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def _px5(v) -> str:
    try:   return f"{float(v):.5f}"
    except: return str(v)


def _analyze_smc_forex(df4h, df1h, symbol: str) -> dict:
    try:
        cfg     = _FOREX_CFG
        price   = float(df1h["close"].iloc[-1])
        swing_n = cfg["structure"]["swing_n"]
        bias    = structure.get_bias(df4h, swing_n=swing_n)

        fib_mid = fib_mod.get_fib_midpoint(df4h, bias, swing_n=swing_n) if bias != "neutral" else None
        fib_ok  = fib_mod.fib_filter(price, bias, fib_mid) if fib_mid else False

        swing_high = swing_low = None
        if bias != "neutral":
            sh = structure._swing_highs(df4h["high"].values, swing_n)
            sl = structure._swing_lows(df4h["low"].values, swing_n)
            if sh: swing_high = float(df4h["high"].values[sh[-1]])
            if sl: swing_low  = float(df4h["low"].values[sl[-1]])

        tc         = cfg["targets"]
        bsl_levels = tgt_mod.get_bsl_levels(df4h, swing_n=swing_n, tolerance=tc["equal_level_tolerance"]) if bias != "neutral" else []
        ssl_levels = tgt_mod.get_ssl_levels(df4h, swing_n=swing_n, tolerance=tc["equal_level_tolerance"]) if bias != "neutral" else []

        pc       = cfg["poi"]
        poi_raw  = poi.get_pois(df4h, bias, ob_lookback=pc["ob_lookback"],
                                fvg_lookback=pc["fvg_lookback"],
                                displacement_atr=pc["displacement_atr"]) if bias != "neutral" else []
        poi_zones  = [{"kind": z["kind"], "low": float(z["low"]), "high": float(z["high"])} for z in poi_raw]
        active_poi = poi.price_in_poi(price, poi_zones) if poi_zones else None

        nearest_poi, nearest_dist = None, float("inf")
        if poi_zones and not active_poi:
            for z in poi_zones:
                d = abs(price - (z["low"] + z["high"]) / 2)
                if d < nearest_dist: nearest_dist, nearest_poi = d, z

        lc    = cfg["liquidity"]
        sweep = liquidity.get_sweep(df1h, bias, lookback=lc["lookback"],
                                    swing_n=lc["swing_n"]) if bias != "neutral" else None

        displacement = False
        if sweep:
            displacement = liquidity.check_displacement(df1h, sweep["bar_idx"], bias,
                                                        atr_mult=lc["displacement_atr"])

        choch_ref = None; choch = False
        if sweep:
            sb = sweep["bar_idx"]; lb = cfg["confirmation"]["lookback"]
            rs = max(0, sb - lb)
            if bias == "bullish":
                choch_ref = float(np.max(df1h["high"].values[rs:sb + 1]))
            else:
                choch_ref = float(np.min(df1h["low"].values[rs:sb + 1]))
            choch = bool(confirmation.get_choch(df1h, bias, sweep, lookback=lb))

        if bias == "neutral":      stage, blocker = 0, "No clear 4H structure (need HH+HL or LL+LH)"
        elif not fib_ok or not active_poi: stage, blocker = 1, "Waiting for Fib + 4H POI"
        elif not sweep:            stage, blocker = 2, "In POI — watching for 1H sweep"
        elif not displacement:     stage, blocker = 3, "Sweep confirmed — waiting for displacement"
        elif not choch:            stage, blocker = 4, "Displacement — waiting for 1H CHoCH"
        else:                      stage, blocker = 5, None

        signal = ("LONG" if bias == "bullish" else "SHORT") if stage == 5 else "FLAT"

        return {
            "ok": True, "symbol": symbol, "price": price, "bias": bias,
            "fib_mid": fib_mid, "fib_ok": fib_ok,
            "poi_zones": poi_zones, "active_poi": active_poi, "nearest_poi": nearest_poi,
            "poi_count": len(poi_zones), "in_poi": bool(active_poi),
            "poi_kind": active_poi["kind"] if active_poi else None,
            "sweep": bool(sweep),
            "sweep_bar": int(sweep["bar_idx"]) if sweep else None,
            "sweep_level": float(sweep["swept_level"]) if sweep else None,
            "sweep_wick": float(sweep["wick_extreme"]) if sweep else None,
            "displacement": displacement, "choch": bool(choch), "choch_ref_level": choch_ref,
            "signal": signal, "stage": stage, "blocker": blocker,
            "swing_high": swing_high, "swing_low": swing_low,
            "bsl_levels": bsl_levels, "ssl_levels": ssl_levels,
        }
    except Exception as exc:
        import traceback; traceback.print_exc()
        return {"ok": False, "symbol": symbol, "error": str(exc), "price": 0,
                "bias": "—", "signal": "—", "stage": 0, "poi_zones": [],
                "sweep": False, "displacement": False, "choch": False}


def _analyze_session_forex(df4h, df1h, symbol: str) -> dict:
    try:
        from smc_bot import session_range as sr
        cfg   = _FOREX_CFG
        ac    = cfg["session"]["asian"]
        price = float(df1h["close"].iloc[-1])
        box   = sr._most_recent_completed_box(df1h, start_h=ac["start_h"], end_h=ac["end_h"])
        if box is None:
            return {"ok": True, "symbol": symbol, "price": price,
                    "box": None, "label": "—", "sweep": None, "signal": None}
        label  = sr.classify_session(box, df1h, range_thr=ac["range_thr"], trend_thr=ac["trend_thr"])
        sweep  = sr.detect_sweep_in_session(df1h, box, sweep_beyond_pct=ac["sweep_beyond_pct"])
        signal = sr.build_session_signal(df4h, df1h, cfg)
        return {
            "ok": True, "symbol": symbol, "price": price,
            "box": {"high": box.high, "low": box.low, "range": box.range, "date": box.date},
            "label": label, "sweep": sweep, "signal": signal,
        }
    except Exception as exc:
        import traceback; traceback.print_exc()
        return {"ok": False, "symbol": symbol, "error": str(exc), "price": 0,
                "box": None, "label": "—", "sweep": None, "signal": None}


def _render_forex_smc_svg(df1h, pipe: dict) -> str:
    """1H candle chart with 4H OB/FVG zones, fib levels, BSL/SSL, sweep+CHoCH annotations."""
    N   = 60
    df  = df1h.tail(N).reset_index(drop=True)
    n   = len(df)
    bias = pipe.get("bias", "neutral")
    sym  = pipe.get("symbol", "")

    W, H          = 1020, 430
    ML, MR, MT, MB = 68, 200, 44, 34
    CW = W - ML - MR
    CH = H - MT - MB

    p_hi = float(df["high"].max())
    p_lo = float(df["low"].min())
    extras = []
    for z in pipe.get("poi_zones", []):
        extras += [z["low"], z["high"]]
    for k in ("sweep_level", "sweep_wick", "choch_ref_level", "swing_high", "swing_low", "fib_mid"):
        v = pipe.get(k)
        if v: extras.append(v)
    for lvl in pipe.get("bsl_levels", []) + pipe.get("ssl_levels", []):
        extras.append(lvl)
    if extras:
        p_hi = max(p_hi, max(e for e in extras if e > 0))
        p_lo = min(p_lo, min(e for e in extras if e > 0))

    pad   = (p_hi - p_lo) * 0.16
    p_max = p_hi + pad
    p_min = p_lo - pad
    p_rng = p_max - p_min if p_max != p_min else 1e-10

    def py(price: float) -> float:
        return MT + CH * (1.0 - (float(price) - p_min) / p_rng)

    def px(i: int) -> float:
        return ML + CW * i / max(n - 1, 1)

    bw  = max(4.5, CW / n * 0.62)
    bhw = bw / 2
    o: list[str] = []

    o.append(f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;display:block;background:#0d1117;border-radius:6px">')

    # Grid lines (5 levels)
    for pct in (0.15, 0.35, 0.5, 0.65, 0.85):
        yg = MT + CH * pct
        pg = p_max - p_rng * pct
        o.append(f'<line x1="{ML}" y1="{yg:.1f}" x2="{ML+CW}" y2="{yg:.1f}" stroke="#1c2230" stroke-width="1"/>')
        o.append(f'<text x="{ML-4}" y="{yg+4:.1f}" text-anchor="end" fill="#3d4a5a" font-family="monospace" font-size="9">{pg:.5f}</text>')
    o.append(f'<rect x="{ML}" y="{MT}" width="{CW}" height="{CH}" fill="none" stroke="#1c2230" stroke-width="1"/>')

    # Dealing range (swing high → mid → swing low)
    s_hi  = pipe.get("swing_high")
    s_lo  = pipe.get("swing_low")
    f_mid = pipe.get("fib_mid")
    if s_hi and s_lo and s_hi > s_lo:
        ysh  = py(s_hi); ysl = py(s_lo)
        ymid = py(f_mid) if f_mid else (ysh + ysl) / 2
        if ymid > ysh:
            o.append(f'<rect x="{ML}" y="{ymid:.1f}" width="{CW}" height="{max(1,ysl-ymid):.1f}" fill="#0a1f0a" opacity="0.5"/>')
            o.append(f'<text x="{ML+CW-4}" y="{(ymid+ysl)/2+4:.1f}" text-anchor="end" fill="#204a20" font-family="monospace" font-size="9" font-weight="600" opacity="0.85">DISCOUNT</text>')
            o.append(f'<rect x="{ML}" y="{ysh:.1f}" width="{CW}" height="{max(1,ymid-ysh):.1f}" fill="#1f0a0a" opacity="0.5"/>')
            o.append(f'<text x="{ML+CW-4}" y="{(ysh+ymid)/2+4:.1f}" text-anchor="end" fill="#4a2020" font-family="monospace" font-size="9" font-weight="600" opacity="0.85">PREMIUM</text>')
        o.append(f'<line x1="{ML}" y1="{ysh:.1f}" x2="{ML+CW}" y2="{ysh:.1f}" stroke="#7a3030" stroke-width="1.2" stroke-dasharray="8,4"/>')
        o.append(f'<text x="{ML+CW+6}" y="{ysh+4:.1f}" fill="#c05050" font-family="monospace" font-size="8.5">Swing H</text>')
        o.append(f'<text x="{ML+CW+6}" y="{ysh+14:.1f}" fill="#804040" font-family="monospace" font-size="8">{s_hi:.5f}</text>')
        o.append(f'<line x1="{ML}" y1="{ysl:.1f}" x2="{ML+CW}" y2="{ysl:.1f}" stroke="#207a30" stroke-width="1.2" stroke-dasharray="8,4"/>')
        o.append(f'<text x="{ML+CW+6}" y="{ysl+4:.1f}" fill="#40c060" font-family="monospace" font-size="8.5">Swing L</text>')
        o.append(f'<text x="{ML+CW+6}" y="{ysl+14:.1f}" fill="#408050" font-family="monospace" font-size="8">{s_lo:.5f}</text>')
        if f_mid:
            o.append(f'<line x1="{ML}" y1="{ymid:.1f}" x2="{ML+CW}" y2="{ymid:.1f}" stroke="#6060a0" stroke-width="1" stroke-dasharray="4,4"/>')
            o.append(f'<text x="{ML+CW+6}" y="{ymid+4:.1f}" fill="#8888cc" font-family="monospace" font-size="8.5">50% Mid</text>')
            o.append(f'<text x="{ML+CW+6}" y="{ymid+14:.1f}" fill="#6060a0" font-family="monospace" font-size="8">{f_mid:.5f}</text>')

    # 4H POI zones (FVGs first, OBs on top)
    cur_p = pipe.get("price") or float(df["close"].iloc[-1])
    chart_zones = []
    for z in pipe.get("poi_zones", []):
        if z["kind"] == "OB":
            chart_zones.append(z)
        else:
            mid = (z["low"] + z["high"]) / 2
            if cur_p and abs(mid - cur_p) / (cur_p or 1) <= 0.005:
                chart_zones.append(z)

    for z in [z for z in chart_zones if z["kind"] == "FVG"]:
        zy1 = py(z["high"]); zy2 = py(z["low"]); zh = max(1.0, zy2 - zy1)
        o.append(f'<rect x="{ML}" y="{zy1:.1f}" width="{CW}" height="{zh:.1f}" fill="#1c1205" stroke="#6a5010" stroke-width="0.8" opacity="0.6"/>')
        o.append(f'<text x="{ML+6}" y="{(zy1+zy2)/2+4:.1f}" fill="#c8950a" font-family="monospace" font-size="9" opacity="0.85">FVG</text>')
        o.append(f'<text x="{ML+CW+6}" y="{zy1+10:.1f}" fill="#9a7010" font-family="monospace" font-size="8">{z["high"]:.5f}</text>')
        o.append(f'<text x="{ML+CW+6}" y="{zy2+1:.1f}" fill="#9a7010" font-family="monospace" font-size="8">{z["low"]:.5f}</text>')

    for z in [z for z in chart_zones if z["kind"] == "OB"]:
        zy1 = py(z["high"]); zy2 = py(z["low"]); zh = max(2.0, zy2 - zy1)
        mid_y = (zy1 + zy2) / 2
        is_active = (pipe.get("active_poi") is not None and
                     pipe["active_poi"].get("low") == z["low"] and
                     pipe["active_poi"].get("high") == z["high"])
        fill   = ("#0d3a66" if is_active else "#0a2848") if bias == "bullish" else ("#3d0e1a" if is_active else "#2a0a12")
        stroke = ("#3a96ff" if is_active else "#2472c8") if bias == "bullish" else ("#ff3a5a" if is_active else "#c02840")
        lbl    = "#4a9eff" if bias == "bullish" else "#ff6070"
        op     = "1.0" if is_active else "0.82"
        mark   = " ◀" if is_active else ""
        o.append(f'<rect x="{ML}" y="{zy1:.1f}" width="{CW}" height="{zh:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="1" opacity="{op}"/>')
        o.append(f'<rect x="{ML}" y="{zy1:.1f}" width="3" height="{zh:.1f}" fill="{stroke}" opacity="{op}"/>')
        label_y = max(zy1 + 12, min(zy2 - 3, mid_y + 4))
        o.append(f'<text x="{ML+7}" y="{label_y:.1f}" fill="{lbl}" font-family="monospace" font-size="10" font-weight="bold" opacity="0.95">OB{mark}</text>')
        o.append(f'<text x="{ML+CW+6}" y="{mid_y:.1f}" fill="{lbl}" font-family="monospace" font-size="9" font-weight="bold">OB</text>')
        o.append(f'<text x="{ML+CW+6}" y="{zy1+10:.1f}" fill="{stroke}" font-family="monospace" font-size="8">{z["high"]:.5f}</text>')
        o.append(f'<text x="{ML+CW+6}" y="{zy2-2:.1f}" fill="{stroke}" font-family="monospace" font-size="8">{z["low"]:.5f}</text>')

    # BSL / SSL pools (nearest 4)
    bsl_near = sorted(pipe.get("bsl_levels", []), key=lambda v: abs(v - cur_p))[:4]
    ssl_near = sorted(pipe.get("ssl_levels", []), key=lambda v: abs(v - cur_p))[:4]
    _liq_bias = pipe.get("bias", "neutral")
    tp_pool = None
    if _liq_bias == "bullish":
        c = [v for v in bsl_near if v > cur_p]; tp_pool = min(c) if c else None
    elif _liq_bias == "bearish":
        c = [v for v in ssl_near if v < cur_p]; tp_pool = max(c) if c else None

    for lvl in bsl_near:
        yl = py(lvl); is_tp = tp_pool is not None and abs(lvl - tp_pool) < 0.0001
        col  = "#ffcc44" if is_tp else "#ffa040"; sw = "1.6" if is_tp else "1"
        dash = "0" if is_tp else "5,4"; lbl_t = "BSL ● TP" if is_tp else "BSL"
        o.append(f'<line x1="{ML}" y1="{yl:.1f}" x2="{ML+CW}" y2="{yl:.1f}" stroke="{col}" stroke-width="{sw}" stroke-dasharray="{dash}" opacity="0.75"/>')
        o.append(f'<text x="{ML+CW+6}" y="{yl+4:.1f}" fill="{col}" font-family="monospace" font-size="8.5" font-weight="{"bold" if is_tp else "normal"}">{lbl_t}</text>')
        o.append(f'<text x="{ML+CW+6}" y="{yl+14:.1f}" fill="#a06820" font-family="monospace" font-size="8">{lvl:.5f}</text>')

    for lvl in ssl_near:
        yl = py(lvl); is_tp = tp_pool is not None and abs(lvl - tp_pool) < 0.0001
        col  = "#44ddff" if is_tp else "#40a0d0"; sw = "1.6" if is_tp else "1"
        dash = "0" if is_tp else "5,4"; lbl_t = "SSL ● TP" if is_tp else "SSL"
        o.append(f'<line x1="{ML}" y1="{yl:.1f}" x2="{ML+CW}" y2="{yl:.1f}" stroke="{col}" stroke-width="{sw}" stroke-dasharray="{dash}" opacity="0.75"/>')
        o.append(f'<text x="{ML+CW+6}" y="{yl+4:.1f}" fill="{col}" font-family="monospace" font-size="8.5" font-weight="{"bold" if is_tp else "normal"}">{lbl_t}</text>')
        o.append(f'<text x="{ML+CW+6}" y="{yl+14:.1f}" fill="#207090" font-family="monospace" font-size="8">{lvl:.5f}</text>')

    # Sweep level
    sl_v = pipe.get("sweep_level")
    if sl_v:
        ys = py(sl_v)
        o.append(f'<line x1="{ML}" y1="{ys:.1f}" x2="{ML+CW}" y2="{ys:.1f}" stroke="#7a8a9a" stroke-width="1" stroke-dasharray="6,4"/>')
        o.append(f'<text x="{ML+CW+6}" y="{ys+4:.1f}" fill="#7a8a9a" font-family="monospace" font-size="8.5">Swept liq.</text>')

    # SL / TP projected lines
    sw_wk = pipe.get("sweep_wick")
    sl_p = tp_p = None
    if sw_wk and cur_p:
        buf = _FOREX_CFG["risk"]["sl_buffer"]
        r_  = _FOREX_CFG["risk"]["target_r"]
        if bias == "bullish":
            sl_p = sw_wk * (1 - buf); tp_p = cur_p + abs(cur_p - sl_p) * r_
        else:
            sl_p = sw_wk * (1 + buf); tp_p = cur_p - abs(cur_p - sl_p) * r_
    if tp_p:
        yt = py(tp_p)
        o.append(f'<line x1="{ML}" y1="{yt:.1f}" x2="{ML+CW}" y2="{yt:.1f}" stroke="#3fb950" stroke-width="1" stroke-dasharray="5,4"/>')
        o.append(f'<text x="{ML+CW+6}" y="{yt+4:.1f}" fill="#3fb950" font-family="monospace" font-size="8.5">Take profit</text>')
        o.append(f'<text x="{ML+CW+6}" y="{yt+15:.1f}" fill="#308040" font-family="monospace" font-size="8">{tp_p:.5f}</text>')
    if sl_p:
        ys2 = py(sl_p)
        o.append(f'<line x1="{ML}" y1="{ys2:.1f}" x2="{ML+CW}" y2="{ys2:.1f}" stroke="#f85149" stroke-width="1" stroke-dasharray="4,3"/>')
        o.append(f'<text x="{ML+CW+6}" y="{ys2+4:.1f}" fill="#f85149" font-family="monospace" font-size="8.5">Stop loss</text>')
        o.append(f'<text x="{ML+CW+6}" y="{ys2+15:.1f}" fill="#c04040" font-family="monospace" font-size="8">{sl_p:.5f}</text>')

    # CHoCH reference
    choch_ref = pipe.get("choch_ref_level")
    if choch_ref:
        yc = py(choch_ref); confirmed = pipe.get("choch", False)
        col  = "#3fb950" if confirmed else "#a0a0a0"
        dash = "8,3" if confirmed else "6,5"
        lbl  = "CHoCH ✓" if confirmed else "CHoCH"
        o.append(f'<line x1="{ML}" y1="{yc:.1f}" x2="{ML+CW}" y2="{yc:.1f}" stroke="{col}" stroke-width="1.2" stroke-dasharray="{dash}"/>')
        o.append(f'<text x="{ML+CW+6}" y="{yc+4:.1f}" fill="{col}" font-family="monospace" font-size="9" font-weight="bold">{lbl}</text>')

    # Candlesticks
    sweep_bar_idx = pipe.get("sweep_bar")
    for i in range(n):
        row  = df.iloc[i]
        cx   = px(i)
        op_, hi_, lo_, cl_ = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        bull = cl_ >= op_
        col  = "#3fb950" if bull else "#f85149"
        body_top = py(max(op_, cl_)); body_bot = py(min(op_, cl_))
        body_h   = max(1.0, body_bot - body_top)
        o.append(f'<line x1="{cx:.1f}" y1="{py(hi_):.1f}" x2="{cx:.1f}" y2="{py(lo_):.1f}" stroke="{col}" stroke-width="1" opacity="0.85"/>')
        o.append(f'<rect x="{cx-bhw:.1f}" y="{body_top:.1f}" width="{bw:.1f}" height="{body_h:.1f}" fill="{col}" rx="0.5"/>')
        if sweep_bar_idx is not None and i == (sweep_bar_idx if sweep_bar_idx < n else n - 1):
            if bias == "bullish":
                my = py(lo_) + 14
                o.append(f'<text x="{cx:.1f}" y="{my:.1f}" text-anchor="middle" fill="#58a6ff" font-size="10" font-weight="bold">▼</text>')
                o.append(f'<text x="{cx:.1f}" y="{my+11:.1f}" text-anchor="middle" fill="#58a6ff" font-family="monospace" font-size="7.5">Sweep</text>')
            else:
                my = py(hi_) - 14
                o.append(f'<text x="{cx:.1f}" y="{my:.1f}" text-anchor="middle" fill="#58a6ff" font-size="10" font-weight="bold">▲</text>')
                o.append(f'<text x="{cx:.1f}" y="{my-3:.1f}" text-anchor="middle" fill="#58a6ff" font-family="monospace" font-size="7.5">Sweep</text>')

    # Current price line
    if cur_p:
        yp = py(cur_p)
        o.append(f'<line x1="{ML}" y1="{yp:.1f}" x2="{ML+CW}" y2="{yp:.1f}" stroke="#4a8aff" stroke-width="1" stroke-dasharray="3,3" opacity="0.7"/>')
        o.append(f'<rect x="{ML+CW-1}" y="{yp-9:.1f}" width="90" height="16" fill="#1a2f60" rx="2"/>')
        o.append(f'<text x="{ML+CW+44:.1f}" y="{yp+4:.1f}" text-anchor="middle" fill="#6ab0ff" font-family="monospace" font-size="9" font-weight="bold">{cur_p:.5f}</text>')

    # Bias badge
    bcol = {"bullish": "#3fb950", "bearish": "#f85149"}.get(bias, "#6e7681")
    blbl = {"bullish": "4H Bias: BULLISH", "bearish": "4H Bias: BEARISH"}.get(bias, "4H Bias: NEUTRAL")
    bsub = "4H HH+HL" if bias == "bullish" else "4H LL+LH" if bias == "bearish" else "no clear structure"
    o.append(f'<rect x="{ML+6}" y="{MT+5}" width="155" height="35" rx="5" fill="#161b22" stroke="{bcol}" stroke-width="1.5" opacity="0.95"/>')
    o.append(f'<text x="{ML+14}" y="{MT+19}" fill="{bcol}" font-family="monospace" font-size="11" font-weight="bold">{blbl}</text>')
    o.append(f'<text x="{ML+14}" y="{MT+31}" fill="#4a5a6a" font-family="monospace" font-size="8.5">{bsub}</text>')

    # Stage badge
    stage      = pipe.get("stage", 0)
    stage_lbl  = f"Stage {stage}/5 · {['NO BIAS','BIAS','FIB+POI','SWEEP','DISP','SIGNAL'][min(stage,5)]}"
    scol       = ["#4a5568","#d29922","#e3b341","#58a6ff","#c97dff","#3fb950"][min(stage, 5)]
    o.append(f'<rect x="{ML+CW-145}" y="{MT+5}" width="143" height="22" rx="4" fill="#161b22" stroke="{scol}" stroke-width="1" opacity="0.95"/>')
    o.append(f'<text x="{ML+CW-72}" y="{MT+18}" text-anchor="middle" fill="{scol}" font-family="monospace" font-size="9" font-weight="bold">{stage_lbl}</text>')

    o.append(f'<text x="{W//2}" y="{H-6}" text-anchor="middle" fill="#3d4a5a" font-family="monospace" font-size="9">{sym} · 1H (last {n} bars, 4H OB/FVG zones)</text>')
    o.append("</svg>")
    return "".join(o)


def _render_forex_session_svg(df1h, sess: dict) -> str:
    """1H candle chart with Asian session box, sweep marker, and signal lines."""
    N   = 48    # last 2 days of 1H bars
    df  = df1h.tail(N).reset_index(drop=True)
    n   = len(df)
    sym = sess.get("symbol", "")

    W, H          = 1020, 430
    ML, MR, MT, MB = 68, 200, 44, 34
    CW = W - ML - MR
    CH = H - MT - MB

    # Build extras list
    box = sess.get("box")
    sig = sess.get("signal")

    p_hi = float(df["high"].max())
    p_lo = float(df["low"].min())
    extras = []
    if box:
        extras += [box["high"], box["low"]]
    if sig:
        for attr in ("entry", "sl", "tp"):
            v = getattr(sig, attr, None)
            if v: extras.append(v)
    if extras:
        p_hi = max(p_hi, max(extras))
        p_lo = min(p_lo, min(extras))

    pad   = (p_hi - p_lo) * 0.18
    p_max = p_hi + pad
    p_min = p_lo - pad
    p_rng = p_max - p_min if p_max != p_min else 1e-10
    price = sess.get("price", 0)

    def py(v: float) -> float:
        return MT + CH * (1.0 - (float(v) - p_min) / p_rng)

    def px(i: int) -> float:
        return ML + CW * i / max(n - 1, 1)

    bw  = max(4.5, CW / n * 0.62)
    bhw = bw / 2
    o: list[str] = []
    o.append(f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;display:block;background:#0d1117;border-radius:6px">')

    # Grid
    for pct in (0.15, 0.35, 0.5, 0.65, 0.85):
        yg = MT + CH * pct
        pg = p_max - p_rng * pct
        o.append(f'<line x1="{ML}" y1="{yg:.1f}" x2="{ML+CW}" y2="{yg:.1f}" stroke="#1c2230" stroke-width="1"/>')
        o.append(f'<text x="{ML-4}" y="{yg+4:.1f}" text-anchor="end" fill="#3d4a5a" font-family="monospace" font-size="9">{pg:.5f}</text>')
    o.append(f'<rect x="{ML}" y="{MT}" width="{CW}" height="{CH}" fill="none" stroke="#1c2230" stroke-width="1"/>')

    # Shade bars that fall within Asian session hours (UTC 00–08)
    if len(df) > 0 and "ts" in df.columns:
        ts_arr = pd.to_datetime(df["ts"].values, utc=True)
        for i, ts in enumerate(ts_arr):
            if 0 <= ts.hour < 8:
                bx = px(i) - bhw * 1.4
                bx_w = bw * 2.2
                o.append(f'<rect x="{bx:.1f}" y="{MT}" width="{bx_w:.1f}" height="{CH}" fill="#1a1a2e" opacity="0.4"/>')

    # Session box (horizontal band)
    if box:
        yh = py(box["high"]); yl = py(box["low"]); bh = max(2, yl - yh)
        o.append(f'<rect x="{ML}" y="{yh:.1f}" width="{CW}" height="{bh:.1f}" fill="#1a1a35" stroke="#4040cc" stroke-width="1" opacity="0.7"/>')
        o.append(f'<line x1="{ML}" y1="{yh:.1f}" x2="{ML+CW}" y2="{yh:.1f}" stroke="#6060ee" stroke-width="1.5" stroke-dasharray="10,4"/>')
        o.append(f'<line x1="{ML}" y1="{yl:.1f}" x2="{ML+CW}" y2="{yl:.1f}" stroke="#6060ee" stroke-width="1.5" stroke-dasharray="10,4"/>')

        label = sess.get("label", "—")
        label_col = {"trend": "#e3b341", "range": "#58a6ff", "neutral": "#6e7681"}.get(label, "#a0a0a0")
        box_mid_y  = (yh + yl) / 2
        o.append(f'<text x="{ML+8}" y="{box_mid_y+4:.1f}" fill="{label_col}" font-family="monospace" font-size="11" font-weight="bold" opacity="0.9">Asian Box · {label.upper()}</text>')
        o.append(f'<text x="{ML+8}" y="{box_mid_y+17:.1f}" fill="#4a5a6a" font-family="monospace" font-size="8.5">{box["date"]} · range={box["range"]:.5f}</text>')

        o.append(f'<text x="{ML+CW+6}" y="{yh+4:.1f}" fill="#8080ff" font-family="monospace" font-size="8.5">Box H</text>')
        o.append(f'<text x="{ML+CW+6}" y="{yh+14:.1f}" fill="#6060cc" font-family="monospace" font-size="8">{box["high"]:.5f}</text>')
        o.append(f'<text x="{ML+CW+6}" y="{yl+4:.1f}" fill="#8080ff" font-family="monospace" font-size="8.5">Box L</text>')
        o.append(f'<text x="{ML+CW+6}" y="{yl+14:.1f}" fill="#6060cc" font-family="monospace" font-size="8">{box["low"]:.5f}</text>')

    # Signal lines (entry, SL, TP)
    if sig:
        entry = getattr(sig, "entry", None)
        sl    = getattr(sig, "sl", None)
        tp    = getattr(sig, "tp", None)
        side  = getattr(sig, "side", "Buy")
        setup = getattr(sig, "setup", "—")
        if entry:
            ye = py(entry)
            ecol = "#3fb950" if side == "Buy" else "#f85149"
            arr  = "▲" if side == "Buy" else "▼"
            o.append(f'<line x1="{ML}" y1="{ye:.1f}" x2="{ML+CW}" y2="{ye:.1f}" stroke="{ecol}" stroke-width="1.8" stroke-dasharray="6,3"/>')
            o.append(f'<text x="{ML+CW+6}" y="{ye+4:.1f}" fill="{ecol}" font-family="monospace" font-size="9" font-weight="bold">{arr} Entry ({setup})</text>')
            o.append(f'<text x="{ML+CW+6}" y="{ye+15:.1f}" fill="{ecol}" font-family="monospace" font-size="8">{entry:.5f}</text>')
        if sl:
            ysl2 = py(sl)
            o.append(f'<line x1="{ML}" y1="{ysl2:.1f}" x2="{ML+CW}" y2="{ysl2:.1f}" stroke="#f85149" stroke-width="1" stroke-dasharray="4,3"/>')
            o.append(f'<text x="{ML+CW+6}" y="{ysl2+4:.1f}" fill="#f85149" font-family="monospace" font-size="8.5">Stop loss</text>')
            o.append(f'<text x="{ML+CW+6}" y="{ysl2+14:.1f}" fill="#c04040" font-family="monospace" font-size="8">{sl:.5f}</text>')
        if tp:
            ytp2 = py(tp)
            o.append(f'<line x1="{ML}" y1="{ytp2:.1f}" x2="{ML+CW}" y2="{ytp2:.1f}" stroke="#3fb950" stroke-width="1" stroke-dasharray="5,4"/>')
            o.append(f'<text x="{ML+CW+6}" y="{ytp2+4:.1f}" fill="#3fb950" font-family="monospace" font-size="8.5">Take profit</text>')
            o.append(f'<text x="{ML+CW+6}" y="{ytp2+15:.1f}" fill="#308040" font-family="monospace" font-size="8">{tp:.5f}</text>')
        if entry and sl:
            yr1 = py(max(entry, sl)); yr2 = py(min(entry, sl))
            o.append(f'<rect x="{ML}" y="{yr1:.1f}" width="{CW}" height="{max(1,yr2-yr1):.1f}" fill="#f85149" opacity="0.05"/>')
        if entry and tp:
            yr1 = py(max(entry, tp)); yr2 = py(min(entry, tp))
            o.append(f'<rect x="{ML}" y="{yr1:.1f}" width="{CW}" height="{max(1,yr2-yr1):.1f}" fill="#3fb950" opacity="0.05"/>')

    # Sweep bar marker
    sweep = sess.get("sweep")
    if sweep:
        bar_idx = sweep.get("bar_idx", 0)
        direction = sweep.get("direction", "bullish")
        adj_i = bar_idx - (len(df1h) - N)
        if 0 <= adj_i < n:
            row = df.iloc[adj_i]; cx = px(adj_i)
            if direction == "bullish":
                my = py(float(row["low"])) + 14
                o.append(f'<text x="{cx:.1f}" y="{my:.1f}" text-anchor="middle" fill="#58a6ff" font-size="10" font-weight="bold">▼</text>')
                o.append(f'<text x="{cx:.1f}" y="{my+11:.1f}" text-anchor="middle" fill="#58a6ff" font-family="monospace" font-size="7.5">Sweep</text>')
            else:
                my = py(float(row["high"])) - 14
                o.append(f'<text x="{cx:.1f}" y="{my:.1f}" text-anchor="middle" fill="#58a6ff" font-size="10" font-weight="bold">▲</text>')
                o.append(f'<text x="{cx:.1f}" y="{my-3:.1f}" text-anchor="middle" fill="#58a6ff" font-family="monospace" font-size="7.5">Sweep</text>')

    # Candlesticks
    for i in range(n):
        row  = df.iloc[i]
        cx   = px(i)
        op_, hi_, lo_, cl_ = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        bull = cl_ >= op_; col = "#3fb950" if bull else "#f85149"
        body_top = py(max(op_, cl_)); body_bot = py(min(op_, cl_))
        body_h   = max(1.0, body_bot - body_top)
        o.append(f'<line x1="{cx:.1f}" y1="{py(hi_):.1f}" x2="{cx:.1f}" y2="{py(lo_):.1f}" stroke="{col}" stroke-width="1" opacity="0.85"/>')
        o.append(f'<rect x="{cx-bhw:.1f}" y="{body_top:.1f}" width="{bw:.1f}" height="{body_h:.1f}" fill="{col}" rx="0.5"/>')

    # Current price
    if price:
        yp = py(price)
        o.append(f'<line x1="{ML}" y1="{yp:.1f}" x2="{ML+CW}" y2="{yp:.1f}" stroke="#4a8aff" stroke-width="1" stroke-dasharray="3,3" opacity="0.7"/>')
        o.append(f'<rect x="{ML+CW-1}" y="{yp-9:.1f}" width="90" height="16" fill="#1a2f60" rx="2"/>')
        o.append(f'<text x="{ML+CW+44:.1f}" y="{yp+4:.1f}" text-anchor="middle" fill="#6ab0ff" font-family="monospace" font-size="9" font-weight="bold">{price:.5f}</text>')

    # Signal badge
    sig_txt = "— NO SIGNAL"
    sig_col = "#6e7681"
    if sig:
        side  = getattr(sig, "side", "Buy")
        setup = getattr(sig, "setup", "—")
        sig_txt = f"{'▲' if side=='Buy' else '▼'} {'LONG' if side=='Buy' else 'SHORT'} · {setup.upper()}"
        sig_col = "#3fb950" if side == "Buy" else "#f85149"

    label = sess.get("label", "—")
    label_col = {"trend": "#e3b341", "range": "#58a6ff", "neutral": "#6e7681"}.get(label, "#a0a0a0")
    o.append(f'<rect x="{ML+6}" y="{MT+5}" width="170" height="35" rx="5" fill="#161b22" stroke="{label_col}" stroke-width="1.5" opacity="0.95"/>')
    o.append(f'<text x="{ML+14}" y="{MT+19}" fill="{label_col}" font-family="monospace" font-size="11" font-weight="bold">Session: {label.upper()}</text>')
    o.append(f'<text x="{ML+14}" y="{MT+31}" fill="{sig_col}" font-family="monospace" font-size="9" font-weight="bold">{sig_txt}</text>')

    o.append(f'<text x="{W//2}" y="{H-6}" text-anchor="middle" fill="#3d4a5a" font-family="monospace" font-size="9">{sym} · 1H · Asian Session Box (00–08 UTC) · last {n} bars</text>')
    o.append("</svg>")
    return "".join(o)


def _smc_gate_panel(pipe: dict, symbol: str) -> str:
    """Compact SMC gate summary card for one forex pair."""
    if not pipe.get("ok"):
        return f'<div style="color:var(--red);font-size:12px">⚠ {pipe.get("error","")}</div>'
    bias  = pipe.get("bias", "neutral")
    price = pipe.get("price", 0)
    stage = pipe.get("stage", 0)
    scol  = ["#4a5568","#d29922","#e3b341","#58a6ff","#c97dff","#3fb950"][min(stage, 5)]
    sig   = pipe.get("signal", "FLAT")
    sig_cls = {"LONG": "sig-long", "SHORT": "sig-short"}.get(sig, "sig-flat")
    sig_txt = {"LONG": "▲ LONG", "SHORT": "▼ SHORT", "FLAT": "— FLAT"}[sig]
    bias_col = {"bullish": "#3fb950", "bearish": "#f85149"}.get(bias, "#6e7681")

    def gate(icon, lbl, val, cls):
        return (f'<div class="gate">'
                f'<span class="gate-icon">{icon}</span>'
                f'<span class="gate-label">{lbl}</span>'
                f'<span class="gate-value {cls}">{val}</span></div>')

    def gpass(ok): return "gate-pass" if ok else "gate-wait"

    fib_ok  = pipe.get("fib_ok", False)
    fib_mid = pipe.get("fib_mid")
    fib_lbl = f'{"discount" if bias=="bullish" else "premium"} ({_px5(fib_mid)})' if fib_mid else "—"
    poi_lbl = f'in {pipe["poi_kind"]}' if pipe.get("in_poi") else f'{pipe.get("poi_count",0)} zones · not reached'

    return f"""
    <div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">
        <span style="color:{bias_col};font-weight:700">{bias.upper()}</span>
        &nbsp;·&nbsp; price {_px5(price)}
        &nbsp;·&nbsp; <span style="color:{scol};font-weight:600">Stage {stage}/5</span>
      </div>
      {gate("✅" if bias!="neutral" else "⬜", "4H Bias", bias.upper(), gpass(bias!="neutral"))}
      {gate("✅" if fib_ok else "⬜", "Fib Zone", fib_lbl, gpass(fib_ok))}
      {gate("✅" if pipe.get("in_poi") else "⬜", "4H POI", poi_lbl, gpass(pipe.get("in_poi",False)))}
      {gate("✅" if pipe.get("sweep") else "⬜", "1H Sweep", f'swept {_px5(pipe.get("sweep_level"))}' if pipe.get("sweep") else "none", gpass(pipe.get("sweep",False)))}
      {gate("✅" if pipe.get("displacement") else "⬜", "Displace", "confirmed" if pipe.get("displacement") else "pending" if pipe.get("sweep") else "—", gpass(pipe.get("displacement",False)))}
      {gate("✅" if pipe.get("choch") else "⬜", "1H CHoCH", "confirmed" if pipe.get("choch") else "—", gpass(pipe.get("choch",False)))}
      <div style="text-align:center;margin-top:10px">
        <span class="signal-badge {sig_cls}">{sig_txt}</span>
      </div>
    </div>"""


def _session_panel(sess: dict) -> str:
    """Compact session status card for one forex pair."""
    if not sess.get("ok"):
        return f'<div style="color:var(--red);font-size:12px">⚠ {sess.get("error","")}</div>'
    box   = sess.get("box")
    label = sess.get("label", "—")
    sig   = sess.get("signal")
    price = sess.get("price", 0)
    label_col = {"trend": "#e3b341", "range": "#58a6ff", "neutral": "#6e7681"}.get(label, "#a0a0a0")

    def row(lbl, val, col="#c9d1d9"):
        return (f'<div class="metric">'
                f'<span class="metric-label">{lbl}</span>'
                f'<span class="metric-value" style="color:{col}">{val}</span></div>')

    if not box:
        box_html = '<div style="color:var(--muted);font-size:12px">No completed Asian session box in data.</div>'
    else:
        box_range_pips = round(box["range"] / 0.0001, 1)
        box_html = (
            row("Session", label.upper(), label_col) +
            row("Box High", f'{box["high"]:.5f}', "#8080ff") +
            row("Box Low",  f'{box["low"]:.5f}',  "#8080ff") +
            row("Box Range", f'{box["range"]:.5f} ({box_range_pips} pips)', "#a0a0c0") +
            row("Date", box["date"], "#6e7681")
        )

    sweep = sess.get("sweep")
    sweep_html = ""
    if sweep:
        d = sweep.get("direction", "—")
        col = "#3fb950" if d == "bullish" else "#f85149"
        sweep_html = row("Sweep", f'{d.upper()} detected', col)

    sig_html = ""
    if sig:
        side  = getattr(sig, "side", "Buy")
        setup = getattr(sig, "setup", "—")
        sig_col = "#3fb950" if side == "Buy" else "#f85149"
        sig_html = (
            '<div style="border-top:1px solid var(--border);margin:8px 0"></div>' +
            row("Signal", f'{"▲ LONG" if side=="Buy" else "▼ SHORT"} · {setup}', sig_col) +
            row("Entry", f'{sig.entry:.5f}', sig_col) +
            row("SL",    f'{sig.sl:.5f}', "#f85149") +
            row("TP",    f'{sig.tp:.5f}', "#3fb950") +
            row("R dist", f'{abs(sig.entry - sig.sl):.5f}', "#a0a0a0")
        )
    else:
        sig_html = '<div style="color:var(--muted);font-size:12px;margin-top:6px">— No active signal</div>'

    return f'{row("Price", f"{price:.5f}", "#6ab0ff")}{box_html}{sweep_html}{sig_html}'


def _forex_charts_html() -> str:
    """
    Two tabbed chart sections:
      Tab 1 — SMC Method (4H→1H): EURUSD | GBPUSD sub-tabs
      Tab 2 — Session Trading (Asian Box): EURUSD | GBPUSD sub-tabs
    Data loaded from parquet cache — no live API call.
    """
    data_eu4h = _load_forex("EURUSD", FOREX_HTF)
    data_eu1h = _load_forex("EURUSD", FOREX_LTF)
    data_gb4h = _load_forex("GBPUSD", FOREX_HTF)
    data_gb1h = _load_forex("GBPUSD", FOREX_LTF)

    if data_eu1h is None or data_eu4h is None:
        eu_avail = False
        eu_smc  = {"ok": False, "symbol": "EURUSD", "error": "EURUSD parquet not found — run fetch_forex_data.py"}
        eu_sess = {"ok": False, "symbol": "EURUSD", "error": "EURUSD parquet not found — run fetch_forex_data.py"}
    else:
        eu_avail = True
        eu_smc  = _analyze_smc_forex(data_eu4h, data_eu1h, "EURUSD")
        eu_sess = _analyze_session_forex(data_eu4h, data_eu1h, "EURUSD")

    if data_gb1h is None or data_gb4h is None:
        gb_avail = False
        gb_smc  = {"ok": False, "symbol": "GBPUSD", "error": "GBPUSD parquet not found — run fetch_forex_data.py"}
        gb_sess = {"ok": False, "symbol": "GBPUSD", "error": "GBPUSD parquet not found — run fetch_forex_data.py"}
    else:
        gb_avail = True
        gb_smc  = _analyze_smc_forex(data_gb4h, data_gb1h, "GBPUSD")
        gb_sess = _analyze_session_forex(data_gb4h, data_gb1h, "GBPUSD")

    # Charts
    eu_smc_svg  = _render_forex_smc_svg(data_eu1h, eu_smc)  if eu_avail else "<p class='red'>No data</p>"
    gb_smc_svg  = _render_forex_smc_svg(data_gb1h, gb_smc)  if gb_avail else "<p class='red'>No data</p>"
    eu_sess_svg = _render_forex_session_svg(data_eu1h, eu_sess) if eu_avail else "<p class='red'>No data</p>"
    gb_sess_svg = _render_forex_session_svg(data_gb1h, gb_sess) if gb_avail else "<p class='red'>No data</p>"

    # Data freshness
    def _freshness(df):
        if df is None: return "—"
        try:
            ts = pd.to_datetime(df["ts"].iloc[-1], utc=True)
            return ts.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return "—"

    eu_fresh = _freshness(data_eu1h)
    gb_fresh = _freshness(data_gb1h)

    eu_smc_gates  = _smc_gate_panel(eu_smc,  "EURUSD")
    gb_smc_gates  = _smc_gate_panel(gb_smc,  "GBPUSD")
    eu_sess_panel = _session_panel(eu_sess)
    gb_sess_panel = _session_panel(gb_sess)

    return f"""
<div class="card full-width" style="margin-bottom:12px">
  <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
    <span>Forex Charts — SMC (4H→1H) &amp; Session Trading</span>
    <span style="font-size:10px;color:var(--muted)">EURUSD: {eu_fresh} &nbsp;·&nbsp; GBPUSD: {gb_fresh} &nbsp;·&nbsp; cached data</span>
  </div>

  <!-- Strategy tabs -->
  <div style="display:flex;gap:0;margin-bottom:0;border-bottom:1px solid var(--border)">
    <button class="ftab active" id="ftab-smc"     onclick="fxTab('smc')"
      style="padding:7px 20px;font-family:var(--mono);font-size:12px;font-weight:700;border:none;cursor:pointer;
             border-radius:5px 5px 0 0;background:#1c2230;color:#4a9eff;border-bottom:2px solid #4a9eff">
      SMC Method
    </button>
    <button class="ftab" id="ftab-sess"   onclick="fxTab('sess')"
      style="padding:7px 20px;font-family:var(--mono);font-size:12px;font-weight:700;border:none;cursor:pointer;
             border-radius:5px 5px 0 0;background:transparent;color:var(--muted);border-bottom:2px solid transparent">
      Session Trading
    </button>
  </div>

  <!-- ── SMC pane ───────────────────────────────────────────────────────────── -->
  <div id="fpane-smc" style="padding-top:12px">
    <div style="font-size:11px;color:var(--muted);margin-bottom:8px;line-height:1.6">
      <strong style="color:#4a9eff">SMC Sniper — Forex 4H→1H chain:</strong>
      4H HH+HL or LL+LH bias · 4H OB/FVG POI (displacement ≥1×ATR) · Fib 50% discount/premium filter ·
      1H liquidity sweep · 1H displacement · 1H CHoCH → signal.
      SL = swept wick ± 2 pips. Target = 3R or nearest BSL/SSL pool.
    </div>
    <!-- Pair sub-tabs -->
    <div style="display:flex;gap:6px;margin-bottom:10px">
      <button id="stab-eu-smc" onclick="fxSub('smc','eu')"
        style="padding:4px 14px;font-family:var(--mono);font-size:11px;font-weight:700;border-radius:4px;
               border:1px solid #4a9eff;background:#0a1f40;color:#4a9eff;cursor:pointer">EURUSD</button>
      <button id="stab-gb-smc" onclick="fxSub('smc','gb')"
        style="padding:4px 14px;font-family:var(--mono);font-size:11px;font-weight:700;border-radius:4px;
               border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer">GBPUSD</button>
    </div>
    <!-- EURUSD SMC -->
    <div id="spane-eu-smc">
      <div style="display:grid;grid-template-columns:1fr 200px;gap:12px;align-items:start">
        <div>{eu_smc_svg}</div>
        <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px">
          <div style="font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#4a9eff;margin-bottom:10px">EURUSD Signal Gates</div>
          {eu_smc_gates}
        </div>
      </div>
    </div>
    <!-- GBPUSD SMC -->
    <div id="spane-gb-smc" style="display:none">
      <div style="display:grid;grid-template-columns:1fr 200px;gap:12px;align-items:start">
        <div>{gb_smc_svg}</div>
        <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px">
          <div style="font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#4a9eff;margin-bottom:10px">GBPUSD Signal Gates</div>
          {gb_smc_gates}
        </div>
      </div>
    </div>
  </div>

  <!-- ── Session pane ───────────────────────────────────────────────────────── -->
  <div id="fpane-sess" style="display:none;padding-top:12px">
    <div style="font-size:11px;color:var(--muted);margin-bottom:8px;line-height:1.6">
      <strong style="color:#e3b341">Asian Session Box — 00:00–08:00 UTC:</strong>
      Box = session high/low. Classify by ATR ratio: range (ratio&lt;0.5) · trend (ratio&gt;0.7).
      Sweep = wick beyond box extreme by 0.2% of range, close back inside.
      Entry models: sweep (body back inside box) · range (fade box edge) · trend (box midpoint pullback).
      SL = 25% of box range. First partial = 75% at opposite box edge (sweep/range) or 4R (trend). Final = 5R.
      4H macro bias gate (must be non-neutral).
    </div>
    <!-- Pair sub-tabs -->
    <div style="display:flex;gap:6px;margin-bottom:10px">
      <button id="stab-eu-sess" onclick="fxSub('sess','eu')"
        style="padding:4px 14px;font-family:var(--mono);font-size:11px;font-weight:700;border-radius:4px;
               border:1px solid #e3b341;background:#2a1e00;color:#e3b341;cursor:pointer">EURUSD</button>
      <button id="stab-gb-sess" onclick="fxSub('sess','gb')"
        style="padding:4px 14px;font-family:var(--mono);font-size:11px;font-weight:700;border-radius:4px;
               border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer">GBPUSD</button>
    </div>
    <!-- EURUSD Session -->
    <div id="spane-eu-sess">
      <div style="display:grid;grid-template-columns:1fr 200px;gap:12px;align-items:start">
        <div>{eu_sess_svg}</div>
        <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px">
          <div style="font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#e3b341;margin-bottom:10px">EURUSD Session</div>
          {eu_sess_panel}
        </div>
      </div>
    </div>
    <!-- GBPUSD Session -->
    <div id="spane-gb-sess" style="display:none">
      <div style="display:grid;grid-template-columns:1fr 200px;gap:12px;align-items:start">
        <div>{gb_sess_svg}</div>
        <div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px">
          <div style="font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#e3b341;margin-bottom:10px">GBPUSD Session</div>
          {gb_sess_panel}
        </div>
      </div>
    </div>
  </div>
</div>

<script>
function fxTab(t) {{
  ['smc','sess'].forEach(s => {{
    document.getElementById('fpane-' + s).style.display = s === t ? '' : 'none';
    const btn = document.getElementById('ftab-' + s);
    if (s === t) {{
      btn.style.color = '#4a9eff'; btn.style.borderBottom = '2px solid #4a9eff';
      btn.style.background = '#1c2230';
    }} else {{
      btn.style.color = 'var(--muted)'; btn.style.borderBottom = '2px solid transparent';
      btn.style.background = 'transparent';
    }}
  }});
}}
function fxSub(strategy, pair) {{
  ['eu','gb'].forEach(p => {{
    const pane = document.getElementById('spane-' + p + '-' + strategy);
    const btn  = document.getElementById('stab-' + p + '-' + strategy);
    if (!pane || !btn) return;
    pane.style.display = p === pair ? '' : 'none';
    const acol = strategy === 'smc' ? '#4a9eff' : '#e3b341';
    const abg  = strategy === 'smc' ? '#0a1f40' : '#2a1e00';
    if (p === pair) {{
      btn.style.borderColor = acol; btn.style.color = acol; btn.style.background = abg;
    }} else {{
      btn.style.borderColor = 'var(--border)'; btn.style.color = 'var(--muted)';
      btn.style.background = 'transparent';
    }}
  }});
}}
</script>"""


# ── HTML builder ───────────────────────────────────────────────────────────────

def _fmt_price(v) -> str:
    try: return f"${float(v):,.2f}"
    except: return str(v)

def _fmt_pnl(v: float) -> str:
    sign = "+" if v >= 0 else ""; cls = "green" if v >= 0 else "red"
    return f'<span class="{cls}">{sign}${v:,.2f}</span>'

def _gate_row(icon, label, value, cls) -> str:
    return (f'<div class="gate"><span class="gate-icon">{icon}</span>'
            f'<span class="gate-label">{label}</span>'
            f'<span class="gate-value {cls}">{value}</span></div>')


def _build_html(
    now_str: str, account: dict, position: dict, pipe: dict,
    df_5m, trades: list[dict], log_lines: list[str], elapsed_ms: int,
) -> str:
    live_mode  = os.getenv("LIVE_TRADING", "false").lower() == "true"
    mode_label = "LIVE" if live_mode else "DEMO"
    mode_cls   = "badge-live" if live_mode else "badge-demo"

    header = f"""
    <div class="header">
      <div class="header-left">
        <span class="logo">◈ SMC BOT</span>
        <span class="badge {mode_cls}">{mode_label}</span>
        <span class="badge badge-paper">BTCUSDT {HTF}→{LTF}</span>
      </div>
      <div class="header-right">
        Last update: <strong>{now_str}</strong><br>
        Fetch: {elapsed_ms}ms &nbsp;<a class="refresh-link" href="/dashboard/">↺ Refresh</a>
      </div>
    </div>"""

    # Account card
    if account.get("ok"):
        w, eq = account["wallet"], account["equity"]
        acct_html = f"""
        <div class="card">
          <div class="card-title">Account — Bybit Demo USDT</div>
          <div class="metric"><span class="metric-label">Wallet Balance</span>
            <span class="metric-value blue">{_fmt_price(w)}</span></div>
          <div class="metric"><span class="metric-label">Equity</span>
            <span class="metric-value">{_fmt_price(eq)}</span></div>
          <div class="metric"><span class="metric-label">Unrealised PnL</span>
            <span class="metric-value">{_fmt_pnl(account["unreal_pnl"])}</span></div>
          <div class="metric"><span class="metric-label">Realised PnL</span>
            <span class="metric-value">{_fmt_pnl(account["cum_pnl"])}</span></div>
        </div>"""
    else:
        acct_html = f'<div class="card"><div class="card-title">Account</div><span class="red">⚠ {account.get("error","")}</span></div>'

    # Position card
    if position.get("open"):
        side = position["side"]
        cls  = "pos-long" if side == "Buy" else "pos-short"
        arr  = "▲ LONG" if side == "Buy" else "▼ SHORT"
        pos_html = f"""
        <div class="card">
          <div class="card-title">Open Position</div>
          <div class="metric"><span class="metric-label">Direction</span>
            <span class="pos-badge {cls}">{arr}</span></div>
          <div class="metric"><span class="metric-label">Size (BTC)</span>
            <span class="metric-value">{position['size']}</span></div>
          <div class="metric"><span class="metric-label">Entry</span>
            <span class="metric-value">{_fmt_price(position['entry'])}</span></div>
          <div class="metric"><span class="metric-label">Stop Loss</span>
            <span class="metric-value red">{_fmt_price(position['sl'])}</span></div>
          <div class="metric"><span class="metric-label">Take Profit</span>
            <span class="metric-value green">{_fmt_price(position['tp'])}</span></div>
          <div class="metric"><span class="metric-label">Unrealised PnL</span>
            <span class="metric-value">{_fmt_pnl(position['upnl'])}</span></div>
        </div>"""
    else:
        pos_html = """
        <div class="card">
          <div class="card-title">Open Position</div>
          <div style="text-align:center;padding:28px 0;color:var(--muted)">
            <div style="font-size:26px;margin-bottom:6px">—</div>
            <div>No open position</div></div>
        </div>"""

    # Pipeline gate card (compact — full story is in proximity panel)
    if pipe.get("ok"):
        bias      = pipe["bias"]
        bias_cls  = {"bullish": "gate-pass", "bearish": "gate-fail"}.get(bias, "gate-wait")
        bias_icon = {"bullish": "✅", "bearish": "🔻"}.get(bias, "⬜")
        fib_ok    = pipe.get("fib_ok", False)
        fib_mid   = pipe.get("fib_mid")
        fib_lbl   = f'{"discount" if bias=="bullish" else "premium"} (mid ${fib_mid:,.0f})' if fib_mid else "—"
        fib_cls   = "gate-pass" if fib_ok else "gate-wait"
        poi_cls   = "gate-pass" if pipe["in_poi"] else "gate-wait"
        poi_lbl   = f'in {pipe["poi_kind"]} zone' if pipe["in_poi"] else f'{pipe["poi_count"]} zones · not reached'
        sw_cls    = "gate-pass" if pipe["sweep"] else "gate-wait"
        sw_lbl    = f'swept ${pipe["sweep_level"]:,.0f}' if pipe["sweep"] else "none"
        disp_ok   = pipe.get("displacement", False)
        disp_cls  = "gate-pass" if disp_ok else "gate-wait"
        disp_lbl  = "confirmed" if disp_ok else ("pending" if pipe["sweep"] else "—")
        ch_cls    = "gate-pass" if pipe["choch"] else "gate-wait"
        ch_lbl    = "confirmed" if pipe["choch"] else ("break ${:,.0f}".format(pipe["choch_ref_level"]) if pipe.get("choch_ref_level") else "—")
        sig       = pipe["signal"]
        sig_cls   = {"LONG":"sig-long","SHORT":"sig-short"}.get(sig,"sig-flat")

        pipe_html = f"""
        <div class="card">
          <div class="card-title">Signal Gates · BTC {_fmt_price(pipe['price'])}</div>
          {_gate_row(bias_icon, f"{HTF.upper()} Bias", bias.upper(), bias_cls)}
          {_gate_row("✅" if fib_ok else "⬜", "Fib Zone", fib_lbl, fib_cls)}
          {_gate_row("✅" if pipe['in_poi'] else "⬜", f"{HTF.upper()} POI", poi_lbl, poi_cls)}
          {_gate_row("✅" if pipe['sweep'] else "⬜", f"{LTF.upper()} Sweep", sw_lbl, sw_cls)}
          {_gate_row("✅" if disp_ok else "⬜", "Displacement", disp_lbl, disp_cls)}
          {_gate_row("✅" if pipe['choch'] else "⬜", f"{LTF.upper()} CHoCH", ch_lbl, ch_cls)}
          <div style="text-align:center;margin-top:12px">
            <span class="signal-badge {sig_cls}">{"▲ " if sig=="LONG" else "▼ " if sig=="SHORT" else ""}{sig}</span>
          </div>
        </div>"""
    else:
        pipe_html = f'<div class="card"><div class="card-title">Signal Gates</div><span class="red">⚠ {pipe.get("error","")}</span></div>'

    # SVG chart
    chart_svg = _render_chart_svg(df_5m, pipe, position) if df_5m is not None else "<p class='red'>No candle data</p>"
    chart_html = f"""
    <div class="card full-width">
      <div class="card-title">Live SMC Chart — {SYMBOL} {LTF.upper()} (last 60 bars)</div>
      {chart_svg}
    </div>"""

    # OB zones panel
    ob_zones_html = _ob_zones_html(pipe)

    # Proximity panel
    proximity_html = _proximity_html(pipe, position)

    # Cycle flow
    cycle_flow_html = _cycle_flow_html()

    # Stats card
    stats = _stats(trades)
    st_html = f"""
    <div class="card">
      <div class="card-title">Trade Stats</div>
      <div class="metric"><span class="metric-label">Total trades</span>
        <span class="metric-value">{stats['total']}</span></div>
      <div class="metric"><span class="metric-label">Wins</span>
        <span class="metric-value green">{stats['wins']}</span></div>
      <div class="metric"><span class="metric-label">Losses</span>
        <span class="metric-value red">{stats['losses']}</span></div>
      <div class="metric"><span class="metric-label">Win rate</span>
        <span class="metric-value {'green' if stats['win_r']>=.4 else 'red'}">{stats['win_r']:.1%}</span></div>
    </div>"""

    links_html = """
    <div class="card">
      <div class="card-title">Quick Links</div>
      <div class="metric"><span class="metric-label">API JSON</span>
        <span class="metric-value"><a href="/api/status">/api/status</a></span></div>
      <div class="metric"><span class="metric-label">Trade log</span>
        <span class="metric-value"><a href="/trades">download CSV</a></span></div>
      <div class="metric"><span class="metric-label">Run bot</span>
        <span class="metric-value muted">python -m smc_bot.bot</span></div>
    </div>"""

    # Trades table
    if trades:
        rows = "".join(
            f'<tr><td class="muted">{t.get("timestamp","")[:19].replace("T"," ")}</td>'
            f'<td><span class="tag {"tag-long" if t.get("side")=="Buy" else "tag-short"}">{"▲ BUY" if t.get("side")=="Buy" else "▼ SELL"}</span></td>'
            f'<td>{_fmt_price(t.get("entry",""))}</td>'
            f'<td class="red">{_fmt_price(t.get("stop",""))}</td>'
            f'<td class="green">{_fmt_price(t.get("target",""))}</td>'
            f'<td>{t.get("qty","")}</td>'
            f'<td><span class="tag {"tag-ob" if t.get("poi_kind")=="OB" else "tag-fvg"}">{t.get("poi_kind","—")}</span></td>'
            f'<td>{"<span class=muted>open</span>" if not t.get("pnl_r") else _fmt_pnl(float(t["pnl_r"]))}</td></tr>'
            for t in trades
        )
        trades_html = f"""
        <div class="card full-width">
          <div class="card-title">Recent Trades (last 25)</div>
          <div style="overflow-x:auto">
          <table class="trades-table">
            <thead><tr><th>Time (UTC)</th><th>Side</th><th>Entry</th>
            <th>SL</th><th>TP</th><th>Qty</th><th>POI</th><th>PnL</th></tr></thead>
            <tbody>{rows}</tbody>
          </table></div>
        </div>"""
    else:
        trades_html = """
        <div class="card full-width">
          <div class="card-title">Recent Trades</div>
          <div style="text-align:center;padding:18px;color:var(--muted)">No trades recorded yet.</div>
        </div>"""

    # SMC Checklist
    checklist_html = _checklist_html()

    # Log
    def _lcls(ln):
        l = ln.lower()
        if "error" in l or "exception" in l: return "log-error"
        if "warn" in l: return "log-warn"
        if "signal" in l or "choch" in l or "sweep" in l or "order" in l: return "log-signal"
        if "debug" in l: return "log-debug"
        return "log-info"

    log_rows = "".join(f'<div class="{_lcls(ln)}">{ln}</div>' for ln in log_lines)
    log_html = f"""
    <div class="card full-width">
      <div class="card-title">System Log — logs/smc_bot.log (last 30 lines)</div>
      <div class="log-box" id="lb">{log_rows}</div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>SMC Bot Dashboard</title>
  <style>{_CSS}</style>
</head>
<body>
  {header}
  <div class="grid-3">{acct_html}{pos_html}{pipe_html}</div>
  <div class="grid-2" style="margin-bottom:12px">{chart_html}</div>
  <div class="grid-2" style="margin-bottom:12px">{ob_zones_html}</div>
  <div class="grid-2" style="margin-bottom:12px">{proximity_html}</div>
  <div class="grid-2" style="margin-bottom:12px">{cycle_flow_html}</div>
  <div class="grid-2" style="margin-bottom:12px">{st_html}{links_html}</div>
  <div class="grid-2" style="margin-bottom:12px">{trades_html}</div>
  <div class="grid-2" style="margin-bottom:12px">{checklist_html}</div>
  {_forex_charts_html()}
  <div class="grid-2">{log_html}</div>
  <script>const lb=document.getElementById('lb');if(lb)lb.scrollTop=lb.scrollHeight;</script>
</body>
</html>"""


# ── routes ─────────────────────────────────────────────────────────────────────

@app.get("/dashboard/", response_class=HTMLResponse)
async def dashboard():
    t0 = time.monotonic()

    # Single candle fetch shared by pipeline + chart
    try:
        df_1h = data.get_candles(_client, SYMBOL, HTF, limit=CFG["data"]["htf_limit"])
        df_5m = data.get_candles(_client, SYMBOL, LTF, limit=CFG["data"]["ltf_limit"])
    except Exception as e:
        df_1h = df_5m = None

    account  = _account()
    position = _position()
    pipe     = _analyze_pipeline(df_1h, df_5m) if df_1h is not None else {
        "ok": False, "error": "candle fetch failed", "stage": 0, "poi_zones": [],
        "sweep": False, "choch": False, "signal": "—", "bias": "—", "price": 0,
    }
    trades    = _trades()
    log_lines = _log_tail()

    elapsed  = int((time.monotonic() - t0) * 1000)
    now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    html = _build_html(now_str, account, position, pipe, df_5m, trades, log_lines, elapsed)
    return HTMLResponse(content=html)


@app.get("/api/status")
async def api_status():
    try:
        df_1h = data.get_candles(_client, SYMBOL, HTF, limit=CFG["data"]["htf_limit"])
        df_5m = data.get_candles(_client, SYMBOL, LTF, limit=CFG["data"]["ltf_limit"])
        pipe  = _analyze_pipeline(df_1h, df_5m)
    except Exception as e:
        pipe = {"ok": False, "error": str(e)}
    return {
        "account":  _account(),
        "position": _position(),
        "pipeline": {k: v for k, v in pipe.items() if k not in ("poi_zones",) or True},
        "ts":       datetime.now(timezone.utc).isoformat(),
    }


@app.get("/trades")
async def trades_csv():
    from fastapi.responses import FileResponse
    path = ROOT / "smc_bot_trades.csv"
    if path.exists():
        return FileResponse(path, media_type="text/csv", filename="smc_bot_trades.csv")
    return HTMLResponse("<pre>No trades yet.</pre>", status_code=404)


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/dashboard/")


if __name__ == "__main__":
    # Bind to localhost only. Use an SSH tunnel or nginx reverse proxy with
    # authentication if remote access is needed — never expose raw on 0.0.0.0.
    uvicorn.run("dashboard.server:app", host="127.0.0.1", port=8000, reload=False)
