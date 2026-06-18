"""
Performance report generator.

Reads trades.jsonl from reports/smc/ or reports/session/ and produces:
  - summary.json  (n, win_rate, gross_pf, net_pf, avg_r, max_dd)
  - summary.html  (lightweight static page)

Run standalone:
    python -m reporting.report_generator --strategy SMC_SNIPER
Or import:
    ReportGenerator("SMC_SNIPER").generate()
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_STRATEGY_DIRS: dict[str, str] = {
    "SMC_SNIPER":      "reports/smc",
    "SESSION_TRADER":  "reports/session",
}

# Bybit / MT5 taker fee round-trip (0.06% × 2 sides = 0.12%)
_FEE_PER_TRADE_R = 0.0


class ReportGenerator:
    """Generate summary stats from a strategy's JSONL trade log."""

    def __init__(self, strategy_name: str, base_dir: str = ".") -> None:
        rel = _STRATEGY_DIRS.get(strategy_name, f"reports/{strategy_name.lower()}")
        self._dir   = Path(base_dir) / rel
        self._name  = strategy_name

    def generate(self) -> dict:
        records = self._load_records()
        stats   = self._compute_stats(records)
        self._write_json(stats)
        self._write_html(stats, records)
        log.info("ReportGenerator[%s]: n=%d net_pf=%.3f", self._name, stats["n"], stats["net_pf"])
        return stats

    # ── loader ────────────────────────────────────────────────────────────────

    def _load_records(self) -> list[dict]:
        p = self._dir / "trades.jsonl"
        if not p.exists():
            return []
        records = []
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    # ── stats ─────────────────────────────────────────────────────────────────

    def _compute_stats(self, records: list[dict]) -> dict:
        n = len(records)
        if n == 0:
            return {
                "strategy": self._name, "n": 0,
                "win_rate": 0.0, "gross_pf": 0.0, "net_pf": 0.0,
                "avg_r": 0.0, "max_dd_r": 0.0, "total_pnl": 0.0,
            }

        pnls    = [r["pnl"] for r in records]
        r_vals  = [r["pnl_r"] for r in records]
        wins    = [p for p in pnls if p > 0]
        losses  = [abs(p) for p in pnls if p < 0]

        gross_pf = (sum(wins) / sum(losses)) if losses else float("inf")
        net_r    = [r - _FEE_PER_TRADE_R for r in r_vals]
        net_wins  = [r for r in net_r if r > 0]
        net_losses = [abs(r) for r in net_r if r < 0]
        net_pf   = (sum(net_wins) / sum(net_losses)) if net_losses else float("inf")

        # Running max drawdown in R
        peak   = 0.0
        trough = 0.0
        cum    = 0.0
        max_dd = 0.0
        for r in net_r:
            cum += r
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

        return {
            "strategy":  self._name,
            "n":         n,
            "win_rate":  len(wins) / n,
            "gross_pf":  round(gross_pf, 3),
            "net_pf":    round(net_pf, 3),
            "avg_r":     round(sum(net_r) / n, 3),
            "max_dd_r":  round(max_dd, 3),
            "total_pnl": round(sum(pnls), 2),
        }

    # ── writers ───────────────────────────────────────────────────────────────

    def _write_json(self, stats: dict) -> None:
        p = self._dir / "summary.json"
        with open(p, "w") as f:
            json.dump(stats, f, indent=2)

    def _write_html(self, stats: dict, records: list[dict]) -> None:
        gate_pass = stats["n"] >= 50 and stats["net_pf"] > 1.0
        color     = "#22c55e" if gate_pass else "#ef4444"
        verdict   = "PASS" if gate_pass else "FAIL (n<50 or net PF≤1.0)"

        rows = "".join(
            f"<tr><td>{r['open_time'][:10]}</td>"
            f"<td>{r['symbol']}</td><td>{r['side']}</td>"
            f"<td>{'✓' if r['pnl']>0 else '✗'}</td>"
            f"<td style='color:{'green' if r['pnl']>0 else 'red'}'>{r['pnl']:+.2f}</td>"
            f"<td>{r['pnl_r']:+.2f}R</td>"
            f"<td>{r['setup']}</td></tr>"
            for r in records[-50:]  # last 50 trades
        )

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{stats['strategy']} Report</title>
<style>
  body{{font-family:monospace;background:#111;color:#eee;padding:20px}}
  table{{border-collapse:collapse;width:100%}}
  th,td{{border:1px solid #333;padding:4px 8px;text-align:left}}
  th{{background:#222}}
</style></head><body>
<h2>{stats['strategy']} — Performance Report</h2>
<table>
  <tr><td>Trades (n)</td><td>{stats['n']}</td></tr>
  <tr><td>Win Rate</td><td>{stats['win_rate']*100:.1f}%</td></tr>
  <tr><td>Gross PF</td><td>{stats['gross_pf']:.3f}</td></tr>
  <tr><td>Net PF</td><td>{stats['net_pf']:.3f}</td></tr>
  <tr><td>Avg R</td><td>{stats['avg_r']:+.3f}</td></tr>
  <tr><td>Max DD (R)</td><td>{stats['max_dd_r']:.2f}R</td></tr>
  <tr><td>Total PnL</td><td>${stats['total_pnl']:+.2f}</td></tr>
  <tr><td>Phase-0 Gate</td><td style="color:{color};font-weight:bold">{verdict}</td></tr>
</table>
<h3>Last 50 trades</h3>
<table>
  <tr><th>Date</th><th>Symbol</th><th>Side</th><th>W/L</th>
      <th>PnL</th><th>R</th><th>Setup</th></tr>
  {rows}
</table>
</body></html>"""

        p = self._dir / "summary.html"
        with open(p, "w") as f:
            f.write(html)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="SMC_SNIPER")
    ap.add_argument("--base-dir", default=".")
    args = ap.parse_args()
    stats = ReportGenerator(args.strategy, args.base_dir).generate()
    print(json.dumps(stats, indent=2))
