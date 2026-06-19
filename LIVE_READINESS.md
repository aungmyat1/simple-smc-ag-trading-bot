# LIVE TRADING READINESS AUDIT

**Project:** simple-smc-ag-trading-bot
**Audit date:** 2026-06-18
**Auditor:** Senior Quant Auditor (automated scan)
**Grade:** D — NOT ready for live trading

---

## MT5 / MetaAPI Integration

| Feature | Status | Notes |
|---|---|---|
| Login implemented | PASS | MetaApiBroker connects via MetaAPI cloud SDK with account ID + token |
| Reconnect logic | FAIL | No automatic WebSocket reconnect — stale `_conn` after drop causes silent order failures until process restart |
| Order placement | PASS | `place_order()` implemented in `brokers/metaapi.py` |
| Order modification | PASS | `modify_order()` implemented |
| SL update | PASS | `update_sl()` implemented |
| Position sync | PASS | `get_positions()` syncs open positions from MetaAPI |
| Account status | WARN | DEMO account (VT Markets MT5 Demo via MetaAPI cloud SDK); state checked for DEPLOYED/DEPLOYING on connect, but no live-account flag enforced in code |
| Sandbox available | PASS | Dry-run mode available; `signal_only_mode` in config blocks real execution |

**Critical gap:** If the MetaAPI WebSocket drops mid-session, the runner continues with a stale connection. Every subsequent order call silently fails — no exception is raised at the call site, no alert is sent. This is a **data-integrity and capital-safety risk** in live operation.

---

## VPS Infrastructure

| Feature | Status | Notes |
|---|---|---|
| Auto-restart on crash | PASS | `Restart=on-failure` in `smc-bot.service` |
| Process monitoring (watchdog) | FAIL | No `WatchdogSec` / `NotifyAccess` in systemd unit; no external health-check ping — a hung (non-crashed) process is invisible to the restarter |
| Log rotation | PASS | Log rotation configured |
| Health-check endpoint | FAIL | Dashboard uvicorn bound to `0.0.0.0:8000` with no auth — publicly reachable on the GCP IP unless VPC firewall explicitly blocks port 8000; no `/healthz` probe wired to systemd |
| Disk space | CRITICAL | VPS root at 92% (3.4 GiB free per FINAL_DEPLOYMENT_REPORT) — a log flush, data cache write, or pip install can fill the disk and crash the bot mid-trade |

---

## Telegram Alerting

| Feature | Status | Notes |
|---|---|---|
| Trade alerts | PASS | Signal-fired, trade-opened, trade-closed events sent |
| Error alerts | PASS | Exception and runtime errors forwarded to Telegram |
| Circuit-breaker alerts | PASS | Daily-loss-limit and drawdown kill-switch events alerted |
| Implementation | PASS | Alerting module fully wired into bot.py / runner.py |

Telegram alerting is the strongest layer of this system. No gaps found.

---

## Environment & Configuration

| Item | Status | Notes |
|---|---|---|
| Env-var template | PASS | `.env.example` committed; `.env` gitignored |
| Dependencies | PASS | `requirements.txt` present and complete |
| `LIVE_TRADING` guard | PASS | Checked independently in `executor.py`, `brokers/metaapi.py`, and `runner.py`; `BaseBroker._assert_live()` raises `RuntimeError` if called without `live_trading=True` |
| `signal_only_mode` | PASS | Second layer in `config.yaml` blocks execution even in paper mode |
| `METAAPI_TOKEN` | FAIL | Blank in `.env.example`; `runner.py` hard-fails at `broker.connect()` without a real value — the bot cannot start at all until this is populated |
| `METAAPI_ACCOUNT_ID` | FAIL | Same as above — blank, required, hard-fail on connect |

---

## Deployment Blockers

The following issues **must** be resolved before any live (or sustained paper) trading. Listed in severity order.

### BLOCKER 1 — Phase-0 gate not passed (CRITICAL)
`MIGRATION_REPORT` confirms that **SMC_SNIPER** and **SESSION_TRADER** on EURUSD/GBPUSD have not been run through the Phase-0 backtest gate (requirement: n ≥ 50, net PF > 1.0). `runner.py` targets these strategies. Per CLAUDE.md §4, Phase-1 paper trading cannot begin until Phase-0 PASSES. Running live capital on an unvalidated signal is a fundamental violation of the project mandate.

**Fix:** Run `scripts/backtest.py` for each strategy/symbol combination. Log results in `docs/VERDICT_LOG.md`. Do not advance to paper trade until at least one combination achieves n ≥ 50 AND net PF > 1.0 on the 2-year holdout window.

### BLOCKER 2 — MetaAPI credentials blank (CRITICAL)
`METAAPI_TOKEN` and `METAAPI_ACCOUNT_ID` are empty in `.env.example`. The runner hard-fails at `broker.connect()` without valid values. The bot **cannot start** in its current state.

**Fix:** Populate `.env` (not `.env.example`) with real MetaAPI credentials before any run.

### BLOCKER 3 — No WebSocket reconnect logic (HIGH)
`MetaApiBroker` has no automatic reconnect. If the MetaAPI WebSocket drops mid-session, the runner continues with a stale `_conn`. All subsequent `place_order()`, `modify_order()`, and `update_sl()` calls silently fail — no exception propagates to the caller, no Telegram alert fires, and no position protection is in effect.

**Fix:** Wrap the MetaAPI connection in a reconnect loop (exponential backoff, max retries). On reconnect failure, send a CRITICAL Telegram alert and halt the runner. Re-sync open positions after reconnect to check for orphaned fills.

### BLOCKER 4 — Disk space critical (HIGH)
VPS root filesystem is at 92% (3.4 GiB free). Log writes, OHLCV cache writes, or any pip operation can fill the disk. When the disk is full: log writes fail silently, SQLite state files corrupt, and the OS OOM-killer may terminate the bot process.

**Fix:** Free at minimum 10 GiB before live deployment. Options: move OHLCV parquet cache to a separate volume, purge old logs, clean pip cache (`pip cache purge`), remove unused Docker images. Add a disk-space check to the startup sequence — halt if free space < 2 GiB.

### BLOCKER 5 — No process health-check / watchdog (MEDIUM)
`systemd` `Restart=on-failure` only fires on a clean crash (non-zero exit). A hung process (blocking network call, deadlock, infinite retry loop) is **not** detected. The process appears alive to systemd but is not trading.

**Fix:** Add `WatchdogSec=60` to `smc-bot.service` and call `systemd.daemon.notify('WATCHDOG=1')` inside the main loop heartbeat. Alternatively, add a `/healthz` HTTP endpoint to the dashboard and configure an external cron-based health-check ping (e.g., Uptime Robot or a local `curl` cron).

### BLOCKER 6 — Dashboard port 8000 publicly exposed (MEDIUM)
`uvicorn` is bound to `0.0.0.0:8000`. Unless the GCP VPC firewall explicitly denies inbound TCP 8000, the dashboard is publicly reachable with no authentication.

**Fix:** Either bind uvicorn to `127.0.0.1:8000` (lo only, access via SSH tunnel), or add HTTP Basic Auth / API-key middleware. Verify with `gcloud compute firewall-rules list` that port 8000 is not open to `0.0.0.0/0`.

### BLOCKER 7 — No process monitoring supervisor (LOW-MEDIUM)
Beyond systemd restart, there is no supervisor watchdog, no `supervisord`, and no external heartbeat ping (e.g., Dead Man's Snitch / BetterUptime). A silent hang between systemd restarts could leave the bot in an unmonitored gap for an unbounded period.

**Fix:** Integrate a dead-man's-switch heartbeat: the main loop POSTs to a monitoring URL every N minutes. If the ping is missed, an alert fires independently of Telegram.

---

## Live Readiness Grade

```
Grade: D
```

| Category | Score | Reason |
|---|---|---|
| Signal validation | 0/1 | Phase-0 gate not passed for any live-targeted strategy |
| Broker integration | 0.5/1 | Order execution implemented but no reconnect logic |
| Infrastructure | 0.5/1 | Auto-restart present; watchdog and disk space are blockers |
| Security | 0.5/1 | LIVE_TRADING guard is solid; dashboard exposure is a risk |
| Alerting | 1/1 | Telegram alerting complete |
| Configuration | 0/1 | Required credentials blank; bot cannot start |

**Overall: 2.5/6 = D**

The system has a sound architecture (layered guards, Telegram alerts, clean env-var templating) but **cannot be promoted to live trading** in its current state. The two absolute stoppers are:

1. Phase-0 backtest gate has not been passed — no validated edge exists for the deployed strategies.
2. MetaAPI credentials are blank — the bot does not start.

All seven blockers above must be resolved and re-audited before advancing to Phase-1 paper trading, per CLAUDE.md §4.
