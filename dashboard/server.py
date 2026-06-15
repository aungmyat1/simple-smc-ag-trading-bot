"""
SMC Bot Dashboard — http://localhost:8000/dashboard/

Run with:
    python -m dashboard.server
    # or
    uvicorn dashboard.server:app --reload --port 8000

Auto-refreshes every 30 s. Pulls live data from Bybit Demo on each request.
"""
from __future__ import annotations

import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ── bootstrap ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from smc_bot import confirmation, data, executor, liquidity, poi, structure  # noqa: E402

# ── config ─────────────────────────────────────────────────────────────────────
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

# ── data collectors ────────────────────────────────────────────────────────────

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
        return {
            "wallet":   wallet,
            "equity":   equity,
            "unreal_pnl": pnl,
            "cum_pnl":  cum_pnl,
            "ok":       True,
        }
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
        liq   = pos.get("liqPrice", "—")
        sl    = pos.get("stopLoss", "—")
        tp    = pos.get("takeProfit", "—")
        r_pnl = upnl / (entry * size * 0.01) if entry and size else 0
        return {
            "open":  True,
            "side":  side,
            "size":  size,
            "entry": entry,
            "upnl":  upnl,
            "liq":   liq,
            "sl":    sl,
            "tp":    tp,
            "r_pnl": r_pnl,
        }
    except Exception as e:
        return {"open": False, "error": str(e)}


def _pipeline() -> dict:
    try:
        df_1h = data.get_candles(_client, SYMBOL, HTF, limit=CFG["data"]["htf_limit"])
        df_5m = data.get_candles(_client, SYMBOL, LTF, limit=CFG["data"]["ltf_limit"])

        price = float(df_5m["close"].iloc[-1])
        bias  = structure.get_bias(df_1h, swing_n=CFG["structure"]["swing_n"])

        pois = poi.get_pois(
            df_1h, bias,
            ob_lookback=CFG["poi"]["ob_lookback"],
            fvg_lookback=CFG["poi"]["fvg_lookback"],
            displacement_atr=CFG["poi"]["displacement_atr"],
        ) if bias != "neutral" else []

        active_poi = poi.price_in_poi(price, pois) if pois else None

        sweep = liquidity.get_sweep(
            df_5m, bias,
            lookback=CFG["liquidity"]["lookback"],
            swing_n=CFG["liquidity"]["swing_n"],
        ) if bias != "neutral" else None

        choch = bool(confirmation.get_choch(
            df_5m, bias, sweep, lookback=CFG["confirmation"]["lookback"]
        ) if sweep else False)

        # Determine what's the signal gate blocker
        if bias == "neutral":
            blocker = "no clear 1H bias"
        elif not pois:
            blocker = "no 1H POI zones found"
        elif not active_poi:
            blocker = f"price ${price:,.0f} not in any POI"
        elif not sweep:
            blocker = "no 5M liquidity sweep"
        elif not choch:
            blocker = "waiting for 5M CHoCH"
        else:
            blocker = None

        signal = "FLAT"
        if bias != "neutral" and active_poi and sweep and choch:
            signal = "LONG" if bias == "bullish" else "SHORT"

        return {
            "ok":          True,
            "price":       float(price),
            "bias":        bias,
            "poi_count":   int(len(pois)),
            "in_poi":      bool(active_poi is not None),
            "poi_kind":    active_poi["kind"] if active_poi else None,
            "sweep":       bool(sweep is not None),
            "sweep_level": float(sweep["swept_level"]) if sweep else None,
            "choch":       bool(choch),
            "signal":      signal,
            "blocker":     blocker,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "price": 0, "bias": "—", "signal": "—"}


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
        return lines[-n:] if len(lines) >= n else lines
    except Exception:
        return ["(error reading log)"]


def _stats(trades: list[dict]) -> dict:
    if not trades:
        return {"total": 0}
    wins  = [t for t in trades if float(t.get("pnl_r", 0) or 0) > 0]
    total = len(trades)
    win_r = len(wins) / total if total else 0
    return {
        "total":  total,
        "wins":   len(wins),
        "losses": total - len(wins),
        "win_r":  win_r,
    }


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
body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.55;
    padding: 16px;
}
a { color: var(--blue); text-decoration: none; }
a:hover { text-decoration: underline; }

/* header */
.header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 18px;
    margin-bottom: 14px;
}
.header-left { display: flex; align-items: center; gap: 14px; }
.logo { font-size: 16px; font-weight: 700; color: var(--blue); letter-spacing: 0.05em; }
.badge {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 600;
    letter-spacing: 0.06em;
}
.badge-demo  { background: #1c2f50; color: var(--blue); border: 1px solid #2d4a7a; }
.badge-live  { background: #3a1a1a; color: var(--red);  border: 1px solid #6b2020; }
.badge-paper { background: #1a2a1a; color: var(--green);border: 1px solid #204020; }
.header-right { color: var(--muted); font-size: 12px; text-align: right; }
.refresh-link {
    color: var(--blue); font-size: 11px; cursor: pointer;
    border: 1px solid var(--border); border-radius: 4px;
    padding: 2px 8px; margin-left: 8px;
}

/* grid layout */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 12px; }

/* card */
.card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
}
.card-title {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
}
.metric { display: flex; justify-content: space-between; align-items: baseline; margin: 5px 0; }
.metric-label { color: var(--muted); font-size: 12px; }
.metric-value { font-size: 14px; font-weight: 600; }
.big-value { font-size: 22px; font-weight: 700; }
.sub-value { font-size: 12px; color: var(--muted); }

/* colors */
.green  { color: var(--green); }
.red    { color: var(--red); }
.yellow { color: var(--yellow); }
.blue   { color: var(--blue); }
.muted  { color: var(--muted); }
.orange { color: var(--orange); }

/* pipeline gate */
.gate { display: flex; align-items: center; gap: 8px; padding: 5px 0; border-bottom: 1px solid var(--border); }
.gate:last-child { border-bottom: none; }
.gate-icon { font-size: 14px; width: 20px; text-align: center; }
.gate-label { color: var(--muted); width: 90px; font-size: 12px; }
.gate-value { font-size: 12px; flex: 1; }
.gate-pass { color: var(--green); }
.gate-fail { color: var(--red); }
.gate-wait { color: var(--muted); }

/* signal badge */
.signal-badge {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 6px;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.08em;
    margin-top: 10px;
}
.sig-long  { background: #1a3a1a; color: var(--green); border: 1px solid #2a5a2a; }
.sig-short { background: #3a1a1a; color: var(--red);   border: 1px solid #6b2020; }
.sig-flat  { background: var(--bg3); color: var(--muted); border: 1px solid var(--border); }

/* blocker */
.blocker {
    margin-top: 10px;
    padding: 6px 10px;
    background: var(--bg3);
    border-left: 3px solid var(--yellow);
    border-radius: 4px;
    font-size: 11px;
    color: var(--yellow);
}

/* trades table */
.trades-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.trades-table th {
    text-align: left;
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-weight: 600;
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.trades-table td { padding: 5px 8px; border-bottom: 1px solid #1c2230; }
.trades-table tr:last-child td { border-bottom: none; }
.trades-table tr:hover td { background: var(--bg3); }
.tag {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 700;
}
.tag-long  { background: #1a3a1a; color: var(--green); }
.tag-short { background: #3a1a1a; color: var(--red); }
.tag-ob  { background: #1c2f50; color: var(--blue); }
.tag-fvg { background: #2a2010; color: var(--orange); }

/* log box */
.log-box {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    max-height: 300px;
    overflow-y: auto;
    font-size: 11px;
    line-height: 1.6;
}
.log-line { color: var(--text); }
.log-info  { color: var(--muted); }
.log-warn  { color: var(--yellow); }
.log-error { color: var(--red); }
.log-debug { color: #444; }
.log-signal { color: var(--green); font-weight: 600; }

/* position card */
.pos-badge {
    padding: 3px 10px;
    border-radius: 5px;
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 0.06em;
}
.pos-long  { background: #1a3a1a; color: var(--green); border: 1px solid #2a5a2a; }
.pos-short { background: #3a1a1a; color: var(--red);   border: 1px solid #6b2020; }
.pos-flat  { background: var(--bg3); color: var(--muted); border: 1px solid var(--border); }

.full-width { grid-column: 1 / -1; }
"""

# ── HTML builder ───────────────────────────────────────────────────────────────

def _fmt_price(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return str(v)


def _fmt_pnl(v: float) -> str:
    sign = "+" if v >= 0 else ""
    cls  = "green" if v >= 0 else "red"
    return f'<span class="{cls}">{sign}${v:,.2f}</span>'


def _fmt_r(v: float) -> str:
    sign = "+" if v >= 0 else ""
    cls  = "green" if v >= 0 else "red"
    return f'<span class="{cls}">{sign}{v:.2f}R</span>'


def _gate_row(icon: str, label: str, value: str, cls: str) -> str:
    return (
        f'<div class="gate">'
        f'<span class="gate-icon">{icon}</span>'
        f'<span class="gate-label">{label}</span>'
        f'<span class="gate-value {cls}">{value}</span>'
        f'</div>'
    )


def _build_html(
    now_str: str,
    account: dict,
    position: dict,
    pipe: dict,
    trades: list[dict],
    log_lines: list[str],
    elapsed_ms: int,
) -> str:
    live_mode = os.getenv("LIVE_TRADING", "false").lower() == "true"
    mode_label = "LIVE" if live_mode else "DEMO"
    mode_cls   = "badge-live" if live_mode else "badge-demo"

    # ── header ────────────────────────────────────────────────────────────────
    header = f"""
    <div class="header">
      <div class="header-left">
        <span class="logo">◈ SMC BOT</span>
        <span class="badge {mode_cls}">{mode_label}</span>
        <span class="badge badge-paper">BTCUSDT&nbsp;{HTF}→{LTF}</span>
      </div>
      <div class="header-right">
        Last update: <strong>{now_str}</strong><br>
        Data latency: {elapsed_ms}ms &nbsp;
        <a class="refresh-link" href="/dashboard/">↺ Refresh</a>
      </div>
    </div>"""

    # ── account card ──────────────────────────────────────────────────────────
    if account.get("ok"):
        w   = account["wallet"]
        eq  = account["equity"]
        up  = account["unreal_pnl"]
        cum = account["cum_pnl"]
        acct_html = f"""
        <div class="card">
          <div class="card-title">Account — Bybit Demo USDT</div>
          <div class="metric">
            <span class="metric-label">Wallet Balance</span>
            <span class="metric-value blue">{_fmt_price(w)}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Equity</span>
            <span class="metric-value">{_fmt_price(eq)}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Unrealised PnL</span>
            <span class="metric-value">{_fmt_pnl(up)}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Realised PnL (total)</span>
            <span class="metric-value">{_fmt_pnl(cum)}</span>
          </div>
        </div>"""
    else:
        acct_html = f'<div class="card"><div class="card-title">Account</div><span class="red">⚠ {account.get("error","API error")}</span></div>'

    # ── position card ─────────────────────────────────────────────────────────
    if position.get("open"):
        side   = position["side"]
        cls    = "pos-long" if side == "Buy" else "pos-short"
        arrow  = "▲ LONG" if side == "Buy" else "▼ SHORT"
        upnl   = position["upnl"]
        pos_html = f"""
        <div class="card">
          <div class="card-title">Open Position</div>
          <div class="metric">
            <span class="metric-label">Direction</span>
            <span class="pos-badge {cls}">{arrow}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Size (BTC)</span>
            <span class="metric-value">{position['size']}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Entry</span>
            <span class="metric-value">{_fmt_price(position['entry'])}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Stop Loss</span>
            <span class="metric-value red">{_fmt_price(position['sl'])}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Take Profit</span>
            <span class="metric-value green">{_fmt_price(position['tp'])}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Unrealised PnL</span>
            <span class="metric-value">{_fmt_pnl(upnl)}</span>
          </div>
        </div>"""
    else:
        pos_html = f"""
        <div class="card">
          <div class="card-title">Open Position</div>
          <div style="text-align:center; padding: 24px 0; color: var(--muted);">
            <div style="font-size:28px; margin-bottom:6px;">—</div>
            <div>No open position</div>
            {'<div class="red" style="font-size:11px;margin-top:6px;">⚠ ' + position.get("error","") + '</div>' if position.get("error") else ''}
          </div>
        </div>"""

    # ── signal pipeline card ──────────────────────────────────────────────────
    if pipe.get("ok"):
        price    = pipe["price"]
        bias     = pipe["bias"]
        bias_lbl = {"bullish": "▲ BULLISH", "bearish": "▼ BEARISH", "neutral": "— NEUTRAL"}.get(bias, bias.upper())
        bias_cls = {"bullish": "gate-pass", "bearish": "gate-fail", "neutral": "gate-wait"}.get(bias, "gate-wait")
        bias_icon= {"bullish": "✅", "bearish": "🔻", "neutral": "⬜"}.get(bias, "⬜")

        poi_pass = pipe["in_poi"]
        poi_lbl  = f'IN {pipe["poi_kind"]} zone' if poi_pass else f'{pipe["poi_count"]} zones · price outside'
        poi_cls  = "gate-pass" if poi_pass else "gate-fail"

        sw_pass  = pipe["sweep"]
        sw_lbl   = f'swept {_fmt_price(pipe["sweep_level"])}' if sw_pass else "none detected"
        sw_cls   = "gate-pass" if sw_pass else ("gate-fail" if bias != "neutral" and poi_pass else "gate-wait")

        ch_pass  = pipe["choch"]
        ch_lbl   = "confirmed" if ch_pass else ("waiting..." if sw_pass else "—")
        ch_cls   = "gate-pass" if ch_pass else ("gate-fail" if sw_pass else "gate-wait")

        sig      = pipe["signal"]
        sig_cls  = {"LONG": "sig-long", "SHORT": "sig-short"}.get(sig, "sig-flat")

        blocker_html = ""
        if pipe.get("blocker"):
            blocker_html = f'<div class="blocker">⏸ Waiting: {pipe["blocker"]}</div>'

        pipeline_html = f"""
        <div class="card">
          <div class="card-title">Signal Pipeline · BTC {_fmt_price(price)}</div>
          {_gate_row(bias_icon, "1H Bias", bias_lbl, bias_cls)}
          {_gate_row("🟦" if poi_pass else "⬜", "1H POI", poi_lbl, poi_cls)}
          {_gate_row("✅" if sw_pass else "⬜", "5M Sweep", sw_lbl, sw_cls)}
          {_gate_row("✅" if ch_pass else "⬜", "5M CHoCH", ch_lbl, ch_cls)}
          <div style="text-align:center; margin-top: 12px;">
            <span class="signal-badge {sig_cls}">{"▲ " if sig=="LONG" else "▼ " if sig=="SHORT" else ""}{sig}</span>
          </div>
          {blocker_html}
        </div>"""
    else:
        pipeline_html = f'<div class="card"><div class="card-title">Signal Pipeline</div><span class="red">⚠ {pipe.get("error","API error")}</span></div>'

    # ── stats card ────────────────────────────────────────────────────────────
    stats = _stats(trades)
    st_html = f"""
    <div class="card">
      <div class="card-title">Trade Stats (all-time)</div>
      <div class="metric">
        <span class="metric-label">Total trades</span>
        <span class="metric-value">{stats.get("total", 0)}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Wins</span>
        <span class="metric-value green">{stats.get("wins", 0)}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Losses</span>
        <span class="metric-value red">{stats.get("losses", 0)}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Win rate</span>
        <span class="metric-value {'green' if stats.get('win_r', 0) >= 0.4 else 'red'}">{stats.get("win_r", 0):.1%}</span>
      </div>
    </div>"""

    # ── trades table ──────────────────────────────────────────────────────────
    if trades:
        rows = []
        for t in trades:
            side    = t.get("side", "")
            dir_cls = "tag-long" if side == "Buy" else "tag-short"
            dir_lbl = "▲ BUY" if side == "Buy" else "▼ SELL"
            poi_k   = t.get("poi_kind", "")
            poi_tag = f'<span class="tag {"tag-ob" if poi_k=="OB" else "tag-fvg"}">{poi_k or "—"}</span>'
            pnl_r   = t.get("pnl_r", "")
            pnl_html= _fmt_r(float(pnl_r)) if pnl_r else '<span class="muted">open</span>'
            ts      = t.get("timestamp", "")[:19].replace("T", " ")
            rows.append(f"""
            <tr>
              <td class="muted">{ts}</td>
              <td><span class="tag {dir_cls}">{dir_lbl}</span></td>
              <td>{_fmt_price(t.get("entry",""))}</td>
              <td class="red">{_fmt_price(t.get("stop",""))}</td>
              <td class="green">{_fmt_price(t.get("target",""))}</td>
              <td>{t.get("qty","")}</td>
              <td>{poi_tag}</td>
              <td>{pnl_html}</td>
            </tr>""")
        trades_html = f"""
        <div class="card full-width">
          <div class="card-title">Recent Trades (last 25)</div>
          <div style="overflow-x:auto">
          <table class="trades-table">
            <thead><tr>
              <th>Time (UTC)</th><th>Side</th><th>Entry</th>
              <th>SL</th><th>TP</th><th>Qty</th><th>POI</th><th>PnL</th>
            </tr></thead>
            <tbody>{"".join(rows)}</tbody>
          </table>
          </div>
        </div>"""
    else:
        trades_html = """
        <div class="card full-width">
          <div class="card-title">Recent Trades</div>
          <div style="text-align:center; padding: 18px 0; color: var(--muted);">
            No trades recorded yet. Bot has not entered any positions.
          </div>
        </div>"""

    # ── log section ───────────────────────────────────────────────────────────
    def _classify(line: str) -> str:
        l = line.lower()
        if "error" in l or "exception" in l or "failed" in l:
            return "log-error"
        if "warn" in l:
            return "log-warn"
        if "signal" in l or "trade" in l or "order" in l or "choch" in l or "sweep" in l:
            return "log-signal"
        if "debug" in l:
            return "log-debug"
        return "log-info"

    log_rows = "".join(
        f'<div class="{_classify(ln)}">{ln}</div>' for ln in log_lines
    )
    log_html = f"""
    <div class="card full-width">
      <div class="card-title">System Log — logs/smc_bot.log (last 30 lines)</div>
      <div class="log-box" id="logbox">{log_rows}</div>
    </div>"""

    # ── assemble ──────────────────────────────────────────────────────────────
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

  <div class="grid-3">
    {acct_html}
    {pos_html}
    {pipeline_html}
  </div>

  <div class="grid-2" style="margin-bottom:12px">
    {st_html}
    <div class="card">
      <div class="card-title">Quick Links</div>
      <div class="metric"><span class="metric-label">Trade log</span>
        <span class="metric-value"><a href="/trades">smc_bot_trades.csv</a></span></div>
      <div class="metric"><span class="metric-label">Config</span>
        <span class="metric-value muted">smc_bot/config.yaml</span></div>
      <div class="metric"><span class="metric-label">Bot runner</span>
        <span class="metric-value muted">python -m smc_bot.bot</span></div>
      <div class="metric"><span class="metric-label">API</span>
        <span class="metric-value"><a href="/api/status">/api/status (JSON)</a></span></div>
    </div>
  </div>

  <div class="grid-2" style="margin-bottom:12px">
    {trades_html}
  </div>

  <div class="grid-2">
    {log_html}
  </div>

  <script>
    // Scroll log to bottom
    const lb = document.getElementById('logbox');
    if (lb) lb.scrollTop = lb.scrollHeight;
  </script>
</body>
</html>"""


# ── routes ─────────────────────────────────────────────────────────────────────

@app.get("/dashboard/", response_class=HTMLResponse)
async def dashboard():
    t0 = time.monotonic()

    account  = _account()
    position = _position()
    pipe     = _pipeline()
    trades   = _trades()
    log_lines= _log_tail()

    elapsed  = int((time.monotonic() - t0) * 1000)
    now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    html = _build_html(now_str, account, position, pipe, trades, log_lines, elapsed)
    return HTMLResponse(content=html)


@app.get("/api/status")
async def api_status():
    return {
        "account":  _account(),
        "position": _position(),
        "pipeline": _pipeline(),
        "trades":   _trades(5),
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


# ── main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("dashboard.server:app", host="0.0.0.0", port=8000, reload=False)
