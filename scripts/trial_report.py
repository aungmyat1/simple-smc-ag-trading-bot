"""
Trial 4 completion report.

Usage (after backtest completes):
    python scripts/trial_report.py \
        --csv   data/trial4_trades.csv \
        --htf   data/cache/BTCUSDT_60m.parquet \
        --ltf   data/cache/BTCUSDT_5m.parquet \
        --out   data/trial4_report/

Produces:
  1. Summary metrics (stdout)
  2. Funnel analysis (from backtest stdout — pass manually via --funnel)
  3. Trade audit: top-20 wins + top-20 losses (stdout + CSV)
  4. Charts: 10 winners + 10 losers (PNG files in --out/)
  5. Final classification
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")          # headless, no display needed
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from smc_bot import confirmation, liquidity, poi, structure

# ── Config ────────────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    with open(ROOT / "smc_bot" / "config.yaml") as f:
        return yaml.safe_load(f)

_CFG     = _load_cfg()
SWING_N  = _CFG["structure"]["swing_n"]
OB_LB    = _CFG["poi"]["ob_lookback"]
FVG_LB   = _CFG["poi"]["fvg_lookback"]
DISP_ATR = _CFG["poi"]["displacement_atr"]
LIQ_SN   = _CFG["liquidity"]["swing_n"]
LIQ_LB   = _CFG["liquidity"]["lookback"]
CHOCH_LB = _CFG["confirmation"]["lookback"]
SL_BUF   = _CFG["risk"]["sl_buffer"]

TAKER_FEE  = 0.0006
ROUND_TRIP = TAKER_FEE * 2

# ── 1. Summary metrics ────────────────────────────────────────────────────────

def print_summary(df: pd.DataFrame) -> str:
    n        = len(df)
    wins     = df[df["net_r"] > 0]
    losses   = df[df["net_r"] <= 0]
    win_rate = len(wins) / n * 100 if n else 0

    gross_wins = df["gross_r"].clip(lower=0).sum()
    gross_loss = df["gross_r"].clip(upper=0).abs().sum()
    net_wins   = df["net_r"].clip(lower=0).sum()
    net_loss   = df["net_r"].clip(upper=0).abs().sum()

    gross_pf   = round(gross_wins / gross_loss, 4) if gross_loss else float("inf")
    net_pf     = round(net_wins   / net_loss,   4) if net_loss   else float("inf")
    expectancy = round(df["net_r"].mean(), 4)
    avg_r      = round(df["gross_r"].mean(), 4)
    avg_fee_r  = round(df["fee_r"].mean(), 4)

    # Max drawdown in R
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for r in df["net_r"]:
        equity += r
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    verdict = _classify(n, gross_pf, net_pf, win_rate, avg_fee_r)

    lines = [
        "",
        "=" * 60,
        "  TRIAL 4 — COMPLETION REPORT",
        "=" * 60,
        f"  Trades        : {n}",
        f"  Win rate      : {win_rate:.1f}%  (fee-break-even ≈37% at 2R single TP)",
        f"  Gross PF      : {gross_pf:.4f}",
        f"  Net PF        : {net_pf:.4f}",
        f"  Expectancy    : {expectancy:+.4f} R/trade",
        f"  Avg gross R   : {avg_r:+.4f} R/trade",
        f"  Avg fee R     : {avg_fee_r:.4f} R/trade",
        f"  Max DD        : {max_dd:.4f} R",
        "-" * 60,
        f"  Gate n≥50     : {'PASS' if n >= 50 else 'FAIL'}  (n={n})",
        f"  Gate net PF>1 : {'PASS' if net_pf > 1.0 else 'FAIL'}  ({net_pf})",
        f"  Gate gross>1  : {'PASS' if gross_pf > 1.0 else 'FAIL'}  ({gross_pf})",
        "-" * 60,
        f"  CLASSIFICATION: {verdict}",
        "=" * 60,
        "",
    ]
    report = "\n".join(lines)
    print(report)
    return verdict


def _classify(n: int, gross_pf: float, net_pf: float, win_rate: float, avg_fee_r: float) -> str:
    if n < 50:
        return "OVERFILTERED (n<50 — funnel starved; no verdict possible)"
    if gross_pf < 1.0:
        return "FAIL (gross edge negative — fees irrelevant; signal family dead)"
    if net_pf > 1.0:
        return "PASS (gross + net edge positive — proceed to robustness battery)"
    # gross > 1.0 but net < 1.0
    if gross_pf >= 1.10 and net_pf >= 0.85:
        return "EDGE_PRESENT_BUT_FEES_KILL (gross edge real; H1 variant (Trial 5) likely survives)"
    if gross_pf >= 1.01:
        return "EDGE_PRESENT_BUT_FEES_KILL (marginal gross edge; H1 wider ATR recommended)"
    return "FAIL (gross edge too thin to survive fees at any timeframe)"


# ── 2. Trade audit ────────────────────────────────────────────────────────────

def print_audit(df: pd.DataFrame, n: int = 20) -> None:
    wins   = df[df["net_r"] > 0].nlargest(n, "net_r")
    losses = df[df["net_r"] <= 0].nsmallest(n, "net_r")

    cols = ["ts", "entry", "sl", "tp", "exit_price_approx", "gross_r", "fee_r", "net_r", "reason"]

    def _build_display(sub: pd.DataFrame) -> pd.DataFrame:
        out = sub.copy()
        # Approximate exit price from gross_r, entry, sl
        out["exit_price_approx"] = (
            out["entry"] + out["gross_r"] * (out["entry"] - out["sl"])
        ).round(2)
        return out[["ts", "entry", "sl", "tp", "exit_price_approx",
                     "gross_r", "fee_r", "net_r", "reason"]]

    print(f"\n{'─'*60}")
    print(f"  TOP {n} WINNERS")
    print(f"{'─'*60}")
    print(_build_display(wins).to_string(index=False))

    print(f"\n{'─'*60}")
    print(f"  TOP {n} LOSERS")
    print(f"{'─'*60}")
    print(_build_display(losses).to_string(index=False))


# ── 3. Chart generation ────────────────────────────────────────────────────────

def _get_signal_context(
    df_1h: pd.DataFrame,
    df_5m: pd.DataFrame,
    entry_bar: int,
    exit_bar:  int,
) -> dict:
    """
    Re-run the signal chain at the bar before entry to recover POI/sweep/CHoCH context.
    Returns a dict with keys: bias, pois, sweep, choch_ref.
    """
    signal_bar = entry_bar - 1   # bar where signal fired
    if signal_bar < 0:
        return {}

    htf_ts = df_1h["ts"].values
    ltf_ts = df_5m["ts"].values
    ts     = ltf_ts[signal_bar]
    htf_idx = int(np.searchsorted(htf_ts, ts, side="left")) - 1
    if htf_idx < 0:
        return {}

    df_1h_w = df_1h.iloc[:htf_idx + 1]
    df_5m_w = df_5m.iloc[:signal_bar + 1]

    bias     = structure.get_bias(df_1h_w, swing_n=SWING_N)
    pois     = poi.get_pois(df_1h_w, bias, OB_LB, FVG_LB, DISP_ATR)
    sweep    = liquidity.get_sweep(df_5m_w, bias, LIQ_LB, LIQ_SN)
    choch_ok = confirmation.get_choch(df_5m_w, bias, sweep, CHOCH_LB) if sweep else False

    return {
        "bias":       bias,
        "pois":       pois,
        "sweep":      sweep,
        "choch":      choch_ok,
    }


def _plot_trade(
    df_5m:       pd.DataFrame,
    trade:       dict,
    ctx:         dict,
    out_path:    Path,
    trade_label: str,
) -> None:
    """Plot a candlestick chart for one trade with annotations."""
    entry_bar = int(trade["entry_bar"])
    exit_bar  = int(trade["exit_bar"])

    # Window: 60 bars before signal bar, 20 bars after exit
    start = max(0, entry_bar - 65)
    end   = min(len(df_5m), exit_bar + 25)
    df_w  = df_5m.iloc[start:end].reset_index(drop=True)

    entry_rel = entry_bar - start
    exit_rel  = exit_bar  - start
    signal_rel = entry_rel - 1

    opens  = df_w["open"].values
    highs  = df_w["high"].values
    lows   = df_w["low"].values
    closes = df_w["close"].values
    n      = len(df_w)

    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("#0d0d0d")
    ax.set_facecolor("#0d0d0d")

    # Candlesticks
    for j in range(n):
        color = "#26a69a" if closes[j] >= opens[j] else "#ef5350"
        ax.plot([j, j], [lows[j], highs[j]], color=color, linewidth=0.8)
        body_lo = min(opens[j], closes[j])
        body_hi = max(opens[j], closes[j])
        ax.add_patch(
            mpatches.FancyBboxPatch(
                (j - 0.3, body_lo), 0.6, body_hi - body_lo,
                boxstyle="square,pad=0", facecolor=color, edgecolor=color,
            )
        )

    # POI zones
    for z in ctx.get("pois", []):
        ax.axhspan(z["low"], z["high"], alpha=0.15,
                   color="#2196F3" if z["kind"] == "OB" else "#9C27B0",
                   label=f'{z["kind"]} POI')

    # SL / TP horizontal lines
    sl = trade["sl"]
    tp = trade["tp"]
    entry_price = trade["entry"]
    ax.axhline(sl, color="#ef5350", linewidth=1.2, linestyle="--", alpha=0.9, label=f"SL {sl:.0f}")
    ax.axhline(tp, color="#26a69a", linewidth=1.2, linestyle="--", alpha=0.9, label=f"TP {tp:.0f}")
    ax.axhline(entry_price, color="#FFC107", linewidth=1.0, linestyle=":", alpha=0.7, label=f"Entry {entry_price:.0f}")

    # Sweep marker
    sweep = ctx.get("sweep")
    if sweep:
        sweep_rel = sweep["bar_idx"] - (entry_bar - 1 - (len(df_5m.iloc[:entry_bar]) - 1)) + signal_rel
        # Simpler: mark the sweep wick extreme with an arrow
        ax.annotate(
            "SWEEP",
            xy=(signal_rel, sweep["wick_extreme"]),
            xytext=(signal_rel - 5, sweep["wick_extreme"] - (highs.max() - lows.min()) * 0.08),
            color="#FF9800", fontsize=7, fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#FF9800", lw=1.0),
        )

    # Entry / Exit vertical markers
    ax.axvline(entry_rel, color="#FFC107", linewidth=1.5, alpha=0.6, linestyle="-")
    ax.axvline(exit_rel,  color="#9E9E9E", linewidth=1.0, alpha=0.5, linestyle=":")

    net_r  = trade["net_r"]
    reason = trade["reason"]
    color  = "#26a69a" if net_r > 0 else "#ef5350"

    ax.set_title(
        f"{trade_label}  |  net={net_r:+.2f}R  gross={trade['gross_r']:+.2f}R  "
        f"fee={trade['fee_r']:.2f}R  exit={reason}",
        color=color, fontsize=9, pad=4,
    )
    ax.tick_params(colors="#aaaaaa", labelsize=7)
    for spine in ax.spines.values():
        spine.set_edgecolor("#333333")

    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(),
              fontsize=7, facecolor="#1a1a1a", edgecolor="#444444",
              labelcolor="#cccccc", loc="upper left")

    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches="tight", facecolor="#0d0d0d")
    plt.close(fig)


def generate_charts(
    df: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_5m: pd.DataFrame,
    out_dir: Path,
    n_each: int = 10,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    winners = df[df["net_r"] > 0].nlargest(n_each, "net_r")
    losers  = df[df["net_r"] <= 0].nsmallest(n_each, "net_r")

    total = len(winners) + len(losers)
    done  = 0

    for group, label_prefix in [(winners, "win"), (losers, "loss")]:
        for rank, (_, trade) in enumerate(group.iterrows(), 1):
            label = f"Trial4_{label_prefix}_{rank:02d}_{trade['ts'][:10]}"
            print(f"  chart {done+1}/{total}: {label}", flush=True)
            ctx = _get_signal_context(df_1h, df_5m, int(trade["entry_bar"]), int(trade["exit_bar"]))
            out_path = out_dir / f"{label}.png"
            try:
                _plot_trade(df_5m, trade.to_dict(), ctx, out_path, label)
            except Exception as exc:
                print(f"    WARN: chart failed — {exc}")
            done += 1

    print(f"\n  Charts saved to {out_dir}/")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",  default=str(ROOT / "data" / "trial4_trades.csv"))
    parser.add_argument("--htf",  default=str(ROOT / "data" / "cache" / "BTCUSDT_60m.parquet"))
    parser.add_argument("--ltf",  default=str(ROOT / "data" / "cache" / "BTCUSDT_5m.parquet"))
    parser.add_argument("--out",  default=str(ROOT / "data" / "trial4_report"))
    parser.add_argument("--no-charts", action="store_true")
    args = parser.parse_args()

    if not Path(args.csv).exists():
        print(f"ERROR: trades CSV not found: {args.csv}")
        print("Wait for backtest to complete first.")
        sys.exit(1)

    print(f"Loading trades from {args.csv} …")
    df = pd.read_csv(args.csv)
    print(f"  {len(df)} trades loaded")

    # 1. Summary
    _classify_result = print_summary(df)

    # 2. Trade audit (20 wins + 20 losses)
    print_audit(df, n=20)

    # 3. Charts
    if not args.no_charts:
        print(f"\nGenerating charts (10 winners + 10 losers) …")
        df_1h = pd.read_parquet(args.htf)
        df_5m = pd.read_parquet(args.ltf)
        generate_charts(df, df_1h, df_5m, Path(args.out), n_each=10)

    # 4. Save audit CSV
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    winners_path = out_dir / "top20_winners.csv"
    losers_path  = out_dir / "top20_losers.csv"
    df[df["net_r"] > 0].nlargest(20, "net_r").to_csv(winners_path, index=False)
    df[df["net_r"] <= 0].nsmallest(20, "net_r").to_csv(losers_path, index=False)
    print(f"\n  Audit CSVs → {out_dir}/")
    print(f"  top20_winners.csv, top20_losers.csv")


if __name__ == "__main__":
    main()
