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
import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from smc_bot import confirmation, data, executor, liquidity, poi, structure  # noqa: E402

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
    Full pipeline analysis. Returns every level the chart needs:
    poi_zones, sweep_bar, sweep_level, sweep_wick, choch_ref_level.
    """
    try:
        price = float(df_5m["close"].iloc[-1])
        bias  = structure.get_bias(df_1h, swing_n=CFG["structure"]["swing_n"])

        poi_zones_raw = poi.get_pois(
            df_1h, bias,
            ob_lookback=CFG["poi"]["ob_lookback"],
            fvg_lookback=CFG["poi"]["fvg_lookback"],
            displacement_atr=CFG["poi"]["displacement_atr"],
        ) if bias != "neutral" else []

        # Serialisable zone dicts (no numpy scalars)
        poi_zones = [
            {"kind": z["kind"], "low": float(z["low"]), "high": float(z["high"])}
            for z in poi_zones_raw
        ]

        active_poi = poi.price_in_poi(price, poi_zones) if poi_zones else None

        sweep_result = liquidity.get_sweep(
            df_5m, bias,
            lookback=CFG["liquidity"]["lookback"],
            swing_n=CFG["liquidity"]["swing_n"],
        ) if bias != "neutral" else None

        # CHoCH reference level (swing high/low before the sweep bar)
        choch_ref_level = None
        choch = False
        if sweep_result:
            sb  = sweep_result["bar_idx"]
            lb  = CFG["confirmation"]["lookback"]
            rs  = max(0, sb - lb)
            if bias == "bullish":
                choch_ref_level = float(np.max(df_5m["high"].values[rs : sb + 1]))
            else:
                choch_ref_level = float(np.min(df_5m["low"].values[rs : sb + 1]))
            choch = bool(confirmation.get_choch(df_5m, bias, sweep_result, lookback=lb))

        # Nearest POI (for stage-1 distance display)
        nearest_poi = None
        nearest_dist = float("inf")
        if poi_zones and not active_poi:
            for z in poi_zones:
                mid = (z["low"] + z["high"]) / 2
                d   = abs(price - mid)
                if d < nearest_dist:
                    nearest_dist, nearest_poi = d, z

        # Stage + blocker
        if bias == "neutral":
            stage, blocker = 0, "No clear 1H structure (need HH+HL or LL+LH)"
        elif not poi_zones:
            stage, blocker = 1, "No POI zones detected on 1H"
        elif not active_poi:
            near = nearest_poi
            dist_str = f"${nearest_dist:,.0f} away" if nearest_poi else "—"
            stage, blocker = 1, f"Price not in POI yet — {dist_str}"
        elif not sweep_result:
            swing_dir = "low" if bias == "bullish" else "high"
            stage, blocker = 2, f"In POI — watching for 5M swing {swing_dir} sweep"
        elif not choch:
            ref_str = f"${choch_ref_level:,.0f}" if choch_ref_level else "—"
            brk_dir = "above" if bias == "bullish" else "below"
            stage, blocker = 3, f"Sweep confirmed — close {brk_dir} {ref_str} to trigger CHoCH"
        else:
            stage, blocker = 4, None

        signal = ("LONG" if bias == "bullish" else "SHORT") if stage == 4 else "FLAT"

        return {
            "ok":              True,
            "price":           price,
            "bias":            bias,
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
            "choch":           bool(choch),
            "choch_ref_level": choch_ref_level,
            "signal":          signal,
            "stage":           stage,
            "blocker":         blocker,
        }
    except Exception as exc:
        import traceback; traceback.print_exc()
        return {"ok": False, "error": str(exc), "price": 0, "bias": "—", "signal": "—", "stage": 0,
                "poi_zones": [], "sweep": False, "choch": False}


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
    ML, MR, MT, MB = 68, 168, 44, 34
    CW = W - ML - MR    # 784
    CH = H - MT - MB    # 352

    # Price range — include all annotation levels
    p_hi = float(df["high"].max())
    p_lo = float(df["low"].min())
    extras = []
    for z in pipe.get("poi_zones", []):
        extras += [z["low"], z["high"]]
    for k in ("sweep_level", "sweep_wick", "choch_ref_level"):
        if pipe.get(k):
            extras.append(pipe[k])
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

    # ── POI zones (under candles) ──────────────────────────────────────────────
    for z in pipe.get("poi_zones", []):
        zy1 = py(z["high"])
        zy2 = py(z["low"])
        zh  = max(1.0, zy2 - zy1)
        is_active = pipe.get("active_poi") == z or (
            pipe.get("active_poi") and
            pipe["active_poi"]["low"] == z["low"] and
            pipe["active_poi"]["high"] == z["high"]
        )
        if z["kind"] == "OB":
            fill, stroke, lbl_col = "#0d2540", "#1a4a80", "#4a9eff"
        else:
            fill, stroke, lbl_col = "#231a06", "#5a4010", "#d4a020"
        opacity = "0.9" if is_active else "0.55"
        o.append(
            f'<rect x="{ML}" y="{zy1:.1f}" width="{CW}" height="{zh:.1f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="0.5" opacity="{opacity}"/>'
        )
        # Kind label inside zone left edge
        label_y = min(zy2 - 4, (zy1 + zy2) / 2 + 4)
        active_mark = " ◀" if is_active else ""
        o.append(
            f'<text x="{ML+5}" y="{label_y:.1f}" fill="{lbl_col}" '
            f'font-family="monospace" font-size="9" opacity="0.9">{z["kind"]}{active_mark}</text>'
        )
        # Right-side label
        rl_y = (zy1 + zy2) / 2 + 4
        o.append(
            f'<text x="{ML+CW+8}" y="{rl_y:.1f}" fill="{lbl_col}" '
            f'font-family="monospace" font-size="9">{z["kind"]}</text>'
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
    stage_lbl = f"Stage {stage}/4 · {['NO BIAS','POI','SWEEP','CHoCH','SIGNAL'][min(stage,4)]}"
    scol = ["#4a5568", "#d29922", "#e3b341", "#58a6ff", "#3fb950"][min(stage, 4)]
    o.append(f'<rect x="{ML+CW-145}" y="{MT+5}" width="143" height="22" rx="4" fill="#161b22" stroke="{scol}" stroke-width="1" opacity="0.95"/>')
    o.append(f'<text x="{ML+CW-72}" y="{MT+18}" text-anchor="middle" fill="{scol}" font-family="monospace" font-size="9" font-weight="bold">{stage_lbl}</text>')

    # ── axis label ────────────────────────────────────────────────────────────
    o.append(f'<text x="{W//2}" y="{H-6}" text-anchor="middle" fill="#3d4a5a" font-family="monospace" font-size="9">BTCUSDT · 5M · last {n} bars</text>')
    o.append(f'<text x="{ML+4}" y="{H-6}" fill="#3d4a5a" font-family="monospace" font-size="9">price →</text>')
    o.append(f'<text x="{ML+CW-4}" y="{H-6}" text-anchor="end" fill="#3d4a5a" font-family="monospace" font-size="9">time →</text>')

    o.append("</svg>")
    return "".join(o)


# ── proximity explanation panel ───────────────────────────────────────────────

def _proximity_html(pipe: dict, position: dict) -> str:
    if not pipe.get("ok"):
        return f'<div class="card full-width"><div class="card-title">Setup Proximity</div><span class="red">⚠ {pipe.get("error","")}</span></div>'

    stage = pipe.get("stage", 0)
    bias  = pipe.get("bias", "neutral")
    signal= pipe.get("signal", "FLAT")

    # Stage progress bar — 4 steps interleaved with 3 connectors
    steps = [
        ("1H Bias",   stage >= 1),
        ("In POI",    stage >= 2),
        ("5M Sweep",  stage >= 3),
        ("5M CHoCH",  stage >= 4),
    ]

    bar_parts: list[str] = []
    for i, (label, done) in enumerate(steps):
        col, icon, bg = ("#3fb950", "✅", "#1a3a1a") if done else ("#4a5568", "⬜", "#0d1117")
        bar_parts.append(
            f'<div style="flex:1;text-align:center;padding:8px 4px;background:{bg};'
            f'border-radius:5px;border:1px solid {col}30">'
            f'<div style="font-size:16px">{icon}</div>'
            f'<div style="font-size:10px;color:{col};margin-top:3px;font-weight:600">{label}</div>'
            f'</div>'
        )
        if i < len(steps) - 1:
            ac = "#3fb950" if done else "#2a2a2a"
            bar_parts.append(
                f'<div style="display:flex;align-items:center;color:{ac};font-size:12px;padding:0 3px">→</div>'
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
        near = pipe.get("nearest_poi")
        if near:
            dist = abs(price - (near["low"] if bias == "bullish" else near["high"]))
            dir_ = "rally to" if bias == "bullish" else "sell off to"
            zone_str = f'${near["low"]:,.0f} – ${near["high"]:,.0f}'
            title = f"Watching for Price to Enter {near['kind']} Zone"
            desc  = (
                f"1H bias is <strong>{'▲ BULLISH' if bias=='bullish' else '▼ BEARISH'}</strong>. "
                f"A {near['kind']} zone exists at {zone_str}. "
                f"Current price {cp} is ${dist:,.0f} away. "
                f"No trade setup until price {dir_} the zone."
            )
            next_action = f"Wait for price to {dir_} the {near['kind']} at {zone_str}."
        else:
            title = "No POI Zones Found"
            desc  = f"1H bias is {'bullish' if bias=='bullish' else 'bearish'} but no OB or FVG zones detected. Displacement threshold may need tuning."
            next_action = "Monitor for a displacement candle to form a new OB or FVG."

    elif stage == 2:
        apoi = pipe.get("active_poi") or {}
        swing_dir = "swing low" if bias == "bullish" else "swing high"
        sweep_dir = "below" if bias == "bullish" else "above"
        title = f"Price in {apoi.get('kind','POI')} Zone — Watching for Sweep"
        desc  = (
            f"Price {cp} is inside the {apoi.get('kind','POI')} zone "
            f"[${apoi.get('low',0):,.0f} – ${apoi.get('high',0):,.0f}]. "
            f"Waiting for a 5M candle wick to pierce {sweep_dir} a prior {swing_dir} "
            f"and close back inside — this is the stop-hunt (inducement sweep) that "
            f"signals smart money absorbed retail stops."
        )
        next_action = f"Watch 5M for a wick piercing {sweep_dir} a recent {swing_dir} with a close back above/inside the zone."

    elif stage == 3:
        choch_ref = pipe.get("choch_ref_level", 0)
        brk_dir   = "above" if bias == "bullish" else "below"
        sw_lv     = pipe.get("sweep_level", 0)
        sw_wk     = pipe.get("sweep_wick", 0)
        title = "Sweep Confirmed — Waiting for CHoCH"
        desc  = (
            f"The sweep of ${sw_lv:,.0f} is confirmed (wick to ${sw_wk:,.0f}). "
            f"Now need a 5M candle to <strong>close {brk_dir} ${choch_ref:,.0f}</strong> "
            f"— the swing level formed before the sweep. This Change of Character (CHoCH) "
            f"confirms the structural reversal and triggers the entry."
        )
        next_action = f"Watch for a 5M close {brk_dir} ${choch_ref:,.0f}. When that fires, entry at market with SL below sweep wick ${sw_wk:,.0f}."

    else:  # stage 4 = signal
        sw_wk  = pipe.get("sweep_wick", price)
        buf    = CFG["risk"]["sl_buffer"]
        sl_p   = sw_wk * (1 - buf) if bias == "bullish" else sw_wk * (1 + buf)
        r_dist = abs(price - sl_p)
        tp_p   = price + r_dist * 2 if bias == "bullish" else price - r_dist * 2
        title  = f"{'▲ LONG' if bias=='bullish' else '▼ SHORT'} SIGNAL ACTIVE"
        desc   = (
            f"All 4 conditions met. Entry at market ~{cp}, "
            f"SL at ${sl_p:,.0f} (below/above sweep wick ${sw_wk:,.0f}), "
            f"TP at ${tp_p:,.0f} (2R = ${r_dist*2:,.0f} gain)."
        )
        next_action = "CONFIRM token required before any order is placed (CLAUDE.md §7)."

    stage_color = ["#4a5568","#d29922","#e3b341","#58a6ff","#3fb950"][min(stage,4)]
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
"""


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
        bias     = pipe["bias"]
        bias_cls = {"bullish": "gate-pass", "bearish": "gate-fail"}.get(bias, "gate-wait")
        bias_icon= {"bullish": "✅", "bearish": "🔻"}.get(bias, "⬜")
        poi_cls  = "gate-pass" if pipe["in_poi"] else "gate-fail"
        poi_lbl  = f'in {pipe["poi_kind"]} zone' if pipe["in_poi"] else f'{pipe["poi_count"]} zones · not reached'
        sw_cls   = "gate-pass" if pipe["sweep"] else "gate-wait"
        sw_lbl   = f'swept ${pipe["sweep_level"]:,.0f}' if pipe["sweep"] else "none"
        ch_cls   = "gate-pass" if pipe["choch"] else "gate-wait"
        ch_lbl   = "confirmed" if pipe["choch"] else ("break ${:,.0f}".format(pipe["choch_ref_level"]) if pipe.get("choch_ref_level") else "—")
        sig      = pipe["signal"]
        sig_cls  = {"LONG":"sig-long","SHORT":"sig-short"}.get(sig,"sig-flat")

        pipe_html = f"""
        <div class="card">
          <div class="card-title">Signal Gates · BTC {_fmt_price(pipe['price'])}</div>
          {_gate_row(bias_icon, "1H Bias", bias.upper(), bias_cls)}
          {_gate_row("✅" if pipe['in_poi'] else "⬜", "1H POI", poi_lbl, poi_cls)}
          {_gate_row("✅" if pipe['sweep'] else "⬜", "5M Sweep", sw_lbl, sw_cls)}
          {_gate_row("✅" if pipe['choch'] else "⬜", "5M CHoCH", ch_lbl, ch_cls)}
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
      <div class="card-title">Live SMC Chart — BTCUSDT 5M (last 60 bars)</div>
      {chart_svg}
    </div>"""

    # Proximity panel
    proximity_html = _proximity_html(pipe, position)

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
  <div class="grid-2" style="margin-bottom:12px">{proximity_html}</div>
  <div class="grid-2" style="margin-bottom:12px">{st_html}{links_html}</div>
  <div class="grid-2" style="margin-bottom:12px">{trades_html}</div>
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
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=8000, reload=False)
