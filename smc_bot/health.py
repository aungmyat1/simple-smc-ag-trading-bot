"""
Health / connectivity checks for the SMC bot.

Why this exists
---------------
The bot is intentionally **file-based** (CLAUDE.md §0: "Data → Signal → Risk →
Execute → Log → Alert. Nothing else."). Trades are written to CSV/JSONL and
market data is cached as parquet — there is **no SQL database** anywhere in the
architecture.

A monitor on the VPS was firing a recurring CRITICAL alert:

    DB connectivity check failed: ConnectionRefusedError:
    [Errno 111] Connect call failed ('127.0.0.1', 5432)

…because it unconditionally probed a Postgres on 127.0.0.1:5432 that this bot
neither has nor needs. The fix is here: the database check is **opt-in**. It
runs only when a database is explicitly configured via environment variables,
and reads the host/port from that config (never hard-coded 5432). When no DB is
configured — the normal case for this bot — the check reports SKIP, not CRITICAL.

This module performs the checks that actually matter for this bot:

  * database   — SKIP unless a DB is configured; TCP-probe it if it is
  * disk       — free space on the working volume (the VPS hit 92% once)
  * heartbeat  — the bot's state file has been touched recently (loop alive)
  * bybit      — (optional) public Bybit market-data reachability

Each check returns a CheckResult. ``run_checks`` aggregates them and
``overall_status`` / ``exit_code`` collapse them to a single verdict so a cron
or systemd timer can act on the exit code, and ``format_report`` renders a
Telegram-friendly message in the same "AG trade report" style.
"""
from __future__ import annotations

import os
import shutil
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# ── status levels ───────────────────────────────────────────────────────────────
PASS = "PASS"   # healthy
WARN = "WARN"   # degraded, not yet critical
FAIL = "FAIL"   # critical — something the bot depends on is down
SKIP = "SKIP"   # not applicable / not configured (neutral)

_SEVERITY = {PASS: 0, SKIP: 0, WARN: 1, FAIL: 2}

_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CheckResult:
    name:   str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (PASS, SKIP)


# ── database (the spurious-alert fix) ────────────────────────────────────────────

def _db_target(env: dict[str, str]) -> tuple[str, int] | None:
    """
    Resolve a (host, port) to probe from the environment, or None if no database
    is configured. Recognised forms (first match wins):

        HEALTHCHECK_DB_URL / DATABASE_URL = postgres://user:pass@host:5432/db
        DB_HOST [+ DB_PORT]               = host  (+ port, default 5432)

    Returning None is the normal case for this file-based bot and means the
    database check is skipped rather than failed.
    """
    url = env.get("HEALTHCHECK_DB_URL") or env.get("DATABASE_URL")
    if url:
        parsed = urlparse(url)
        host = parsed.hostname
        if host:
            return host, int(parsed.port or 5432)

    host = env.get("DB_HOST")
    if host:
        return host, int(env.get("DB_PORT") or 5432)

    return None


def check_database(
    env: dict[str, str] | None = None,
    *,
    timeout: float = 3.0,
    probe=None,
) -> CheckResult:
    """
    TCP connectivity to a configured database — and ONLY if one is configured.

    The bot stores everything in flat files, so by default no DB is configured
    and this returns SKIP. It returns FAIL (the thing that used to spam CRITICAL)
    only when a DB *is* configured and is genuinely unreachable — which is then a
    real problem worth alerting on.
    """
    env = os.environ if env is None else env
    target = _db_target(env)
    if target is None:
        return CheckResult(
            "database", SKIP,
            "no database configured — bot is file-based (CSV/JSONL/parquet)",
        )

    host, port = target
    do_probe = probe or _tcp_probe
    try:
        do_probe(host, port, timeout)
    except OSError as exc:
        return CheckResult(
            "database", FAIL,
            f"cannot connect to {host}:{port} — {type(exc).__name__}: {exc}",
        )
    return CheckResult("database", PASS, f"reachable at {host}:{port}")


def _tcp_probe(host: str, port: int, timeout: float) -> None:
    """Open and immediately close a TCP socket; raises OSError on failure."""
    with socket.create_connection((host, port), timeout=timeout):
        pass


# ── disk ─────────────────────────────────────────────────────────────────────────

def check_disk(path: str | Path = _ROOT, *, warn_pct: float = 85.0,
               fail_pct: float = 95.0) -> CheckResult:
    """Free space on the working volume. The VPS once sat at 92% (see deployment
    report) — a disk-full kills the bot and every log/state write."""
    try:
        usage = shutil.disk_usage(str(path))
    except OSError as exc:
        return CheckResult("disk", WARN, f"could not stat {path}: {exc}")

    used_pct = usage.used / usage.total * 100 if usage.total else 0.0
    free_gb  = usage.free / 1e9
    detail   = f"{used_pct:.0f}% used, {free_gb:.1f} GiB free"
    if used_pct >= fail_pct:
        return CheckResult("disk", FAIL, detail + f" (>= {fail_pct:.0f}%)")
    if used_pct >= warn_pct:
        return CheckResult("disk", WARN, detail + f" (>= {warn_pct:.0f}%)")
    return CheckResult("disk", PASS, detail)


# ── heartbeat ─────────────────────────────────────────────────────────────────────

def check_heartbeat(
    state_path: str | Path = _ROOT / "smc_bot_state.json",
    *,
    max_age_min: float = 30.0,
    now: float | None = None,
) -> CheckResult:
    """
    The bot rewrites its state file every loop. A stale (or missing) state file
    means the loop has stopped. Returns WARN if the file was never created
    (fresh install / not yet started) and FAIL if it exists but is stale.
    """
    p = Path(state_path)
    if not p.exists():
        return CheckResult(
            "heartbeat", WARN,
            f"{p.name} not found — bot not started yet?",
        )
    now = time.time() if now is None else now
    age_min = (now - p.stat().st_mtime) / 60.0
    detail = f"{p.name} updated {age_min:.0f} min ago"
    if age_min > max_age_min:
        return CheckResult("heartbeat", FAIL, detail + f" (> {max_age_min:.0f} min)")
    return CheckResult("heartbeat", PASS, detail)


# ── bybit (optional; networked) ───────────────────────────────────────────────────

def check_bybit(*, symbol: str = "BTC/USDT:USDT") -> CheckResult:
    """
    Public Bybit market-data reachability — the bot's one true upstream
    dependency. Networked, so it is opt-in (run_checks(..., bybit=True)).
    A network failure is FAIL because no data means no signals.
    """
    try:
        from smc_bot import data
        client = data.make_client(testnet=False)
        df = data.get_candles(client, symbol, "5m", limit=2)
    except Exception as exc:  # noqa: BLE001 - any failure is a connectivity fault
        return CheckResult("bybit", FAIL, f"market data fetch failed: {exc}")
    if df is None or df.empty:
        return CheckResult("bybit", WARN, "no candles returned (stale or empty)")
    return CheckResult("bybit", PASS, f"{len(df)} candles for {symbol}")


# ── aggregation ───────────────────────────────────────────────────────────────────

def run_checks(*, bybit: bool = False, env: dict[str, str] | None = None) -> list[CheckResult]:
    """Run the standard battery of checks and return their results."""
    results = [
        check_database(env=env),
        check_disk(),
        check_heartbeat(),
    ]
    if bybit:
        results.append(check_bybit())
    return results


def overall_status(results: list[CheckResult]) -> str:
    """Worst status across all checks (FAIL > WARN > PASS/SKIP)."""
    worst = PASS
    for r in results:
        if _SEVERITY[r.status] > _SEVERITY[worst]:
            worst = r.status
    return worst


def exit_code(results: list[CheckResult]) -> int:
    """0 = healthy/degraded-ok, 1 = warn, 2 = critical. Suitable for cron."""
    return _SEVERITY[overall_status(results)]


def format_report(results: list[CheckResult], *, timestamp: datetime | None = None) -> str:
    """Render a Telegram-friendly report in the 'AG trade report' style."""
    status = overall_status(results)
    ts = (timestamp or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S UTC")

    if status == FAIL:
        header = "🚨 **CRITICAL: Health Check Failed**"
    elif status == WARN:
        header = "⚠️ **WARNING: Health Check Degraded**"
    else:
        header = "✅ **Health Check OK**"

    icon = {PASS: "✅", WARN: "⚠️", FAIL: "🚨", SKIP: "⏭️"}
    lines = [f"{icon[r.status]} {r.name}: {r.status}" + (f" — {r.detail}" if r.detail else "")
             for r in results]

    return f"{header}\n\n" + "\n".join(lines) + f"\n\n_Timestamp: {ts}_"
