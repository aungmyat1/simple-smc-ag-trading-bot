"""
Health check CLI — the single source of truth for "is the bot healthy?".

Run this from cron / a systemd timer on the VPS INSTEAD of an ad-hoc Postgres
probe. The bot is file-based and has no database, so the old monitor that
hard-coded a connect to 127.0.0.1:5432 only ever produced false CRITICAL
alerts (ConnectionRefusedError [Errno 111]). Here the database check is opt-in:
it is SKIPped unless HEALTHCHECK_DB_URL / DATABASE_URL / DB_HOST is set.

Usage:
    python scripts/healthcheck.py                 # text report, exit 0/1/2
    python scripts/healthcheck.py --json          # machine-readable
    python scripts/healthcheck.py --check-bybit   # also probe Bybit market data
    python scripts/healthcheck.py --alert         # Telegram on WARN/FAIL only
    python scripts/healthcheck.py --alert-always  # Telegram regardless of status

Exit codes: 0 = healthy (or degraded-but-ok), 1 = warning, 2 = critical.

Cron example (every 15 min, alert only on trouble):
    */15 * * * * cd ~/simple-smc-ag-trading-bot && \
        .venv/bin/python scripts/healthcheck.py --check-bybit --alert
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from smc_bot import health  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="SMC bot health check")
    ap.add_argument("--check-bybit", action="store_true",
                    help="Also probe public Bybit market-data reachability (networked)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    ap.add_argument("--alert", action="store_true",
                    help="Send a Telegram alert when status is WARN or FAIL")
    ap.add_argument("--alert-always", action="store_true",
                    help="Send a Telegram alert regardless of status")
    args = ap.parse_args()

    # Load .env so DB_* / TELEGRAM_* / BYBIT_* are available, matching the bot.
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except Exception:  # noqa: BLE001 - dotenv is optional
            pass

    results = health.run_checks(bybit=args.check_bybit)
    status  = health.overall_status(results)
    code    = health.exit_code(results)

    if args.json:
        print(json.dumps({
            "status":  status,
            "exit_code": code,
            "checks":  [{"name": r.name, "status": r.status, "detail": r.detail} for r in results],
        }, indent=2))
    else:
        print(health.format_report(results))

    if args.alert_always or (args.alert and status in (health.WARN, health.FAIL)):
        from smc_bot import alerts
        alerts.send(health.format_report(results))

    return code


if __name__ == "__main__":
    sys.exit(main())
