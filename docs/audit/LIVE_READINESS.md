# LIVE TRADING READINESS AUDIT
**Audit date:** 2026-06-18  
**Files audited:** smc_bot/bot.py, runner.py, brokers/metaapi.py, risk/manager.py, strategies/smc_sniper.py

---

## Deployment Paths

Two distinct deployment paths exist in this repository:

| Path | Symbol | Broker | Runner | Status |
|---|---|---|---|---|
| **A: BTC Bot** | BTCUSDT perp | Bybit (pybit SDK) | `smc_bot/bot.py` | BLOCKED (see below) |
| **B: Forex Multi-Strategy** | EURUSD / GBPUSD | VT Markets MT5 via MetaAPI | `runner.py` | BLOCKED (2 critical) |

---

## Blocker Matrix

| Item | BTC Bot | Forex Runner | Severity |
|---|---|---|---|
| Exchange connectivity | ✅ Bybit demo confirmed working | ❌ MetaAPI account DRAFT | CRITICAL |
| Signal implementation | ✅ Full chain implemented | ❌ FVG gate is STUB in smc_sniper.py | CRITICAL |
| Phase-0 backtest PASS | ✅ T21, T22 PASS | ❌ All Forex trials FAIL | GATE |
| Paper trade (30 days) | ❌ NOT YET RUN | ❌ NOT YET RUN | GATE |
| State persistence | ⚠ Flat JSON, no atomic write | ⚠ Same issue | HIGH |
| Process supervisor | ❌ No systemd/supervisor | ❌ No systemd/supervisor | HIGH |
| Log rotation | ❌ No RotatingFileHandler | ❌ No RotatingFileHandler | MEDIUM |
| Duplicate-order guard | ✅ BTC last_signal_ts dedup | ❌ Missing in smc_sniper.py | HIGH |
| OOS backtest | ❌ Not done | ❌ Not done | HIGH |
| News filter | ❌ Missing | ❌ Missing | MEDIUM |
| Weekly loss limit | ❌ Missing | ❌ Missing | MEDIUM |
| Env validation on startup | ❌ Missing | ❌ Missing | LOW |

---

## BTC Bot Readiness (Path A)

### READY FOR PAPER TRADE: YES (with conditions)

The BTC bot (`smc_bot/bot.py`) is architecturally complete for paper trading:
- Bybit demo account works; `pybit` SDK authenticated
- Full 15-step signal chain implemented
- Risk guards (daily loss, max DD, consecutive losses) code-enforced
- LIVE_TRADING=false default enforced by env var
- Telegram alerts implemented for all key events
- `signal_only_mode=true` logs intent without placing orders

**Conditions that must be met before starting paper trade:**
1. Fix MEDIUM-4: On restart with an open position, restore `tp1_placed=True` to avoid double TP1 order
2. Fix LOW-2: Replace `FileHandler` with `RotatingFileHandler` (disk risk on VPS)
3. Add systemd unit or supervisor config (process must auto-restart on crash)
4. Remove or document the undocumented market entry fallback (HIGH-4 in EXECUTION_AUDIT.md)

### READY FOR SMALL LIVE: NO

Requires completion of 30-day paper trade with 100+ bars monitored, no execution bugs, and no backtest OOS gap.

### READY FOR FULL LIVE: NO

Requires all of paper trade + OOS validation + weekly loss limit + notional cap.

---

## Forex Runner Readiness (Path B)

### READY FOR PAPER TRADE: NO

Two critical blockers:

**CRITICAL-1: MetaAPI Account in DRAFT**  
Account `35e4d9de-1f2a-474e-a4d0-5a03fd4f5e09` requires billing activation at app.metaapi.cloud/billing. All API calls to `place_order()`, `close_position()`, `get_positions()` will fail until resolved. Estimated fix: add billing method, upgrade to paid tier ($~25/month).

**CRITICAL-2: FVG Retest Gate is a STUB**  
`strategies/smc_sniper.py` steps 11–12 contain `pass`. The backtested edge (T21 PF=1.38) was computed WITH the FVG gate. Live Forex would fire on CHoCH alone — an untested signal combination that has not passed Phase-0 gate.

**Additional Forex blockers:**
- No Forex trial has passed Phase-0 gate (9 trials, all FAIL)
- Session Trader (IB sweep) failed its own trial (EUR n=34 PF=0.24, GBP n=38 PF=0.21)
- No duplicate-order guard in `strategies/smc_sniper.py`
- EURUSD/GBPUSD 5M data gap — proper H1+M5 chain cannot be backtested without MetaAPI

### READY FOR SMALL LIVE: NO  
### READY FOR FULL LIVE: NO

---

## Process Infrastructure

### No auto-restart mechanism

Neither the BTC bot nor the Forex runner has a process supervisor:
- No `smc_bot.service` systemd unit file
- No `supervisord.conf`
- No pm2 config
- No cron restart guard

On VPS, if the Python process crashes (OOM, exception, network error), trading stops silently. No alert fires for a dead process — the Telegram API fail streak counter only alerts on live trading cycles, not on process death.

**Minimum required:** A systemd unit with `Restart=always` and `RestartSec=30`. Both bots need this before any live deployment.

### Telegram alerts

| Alert | BTC Bot | Forex Runner |
|---|---|---|
| Trade opened/closed | ✅ | ⚠ Depends on `strategy.alert()` — not verified for smc_sniper.py |
| Guard halt (daily loss, DD) | ✅ | ⚠ risk/manager.py may not send Telegram — no alerts.py equivalent |
| Bot startup | ✅ | ❌ Not implemented in runner.py |
| Bot crash | ❌ (process death is silent) | ❌ |
| Error streak | ✅ (5 API fails) | ❌ |

### Log management

Both runners use `logging.FileHandler` with no rotation. At ~1KB per log line and 288 poll cycles/day (4H+1H bot polls every ~5min): ~288KB/day. Log will reach 1GB in ~3.5 years. On a typical VPS with 20GB disk, this is acceptable if log rotation is added before the 2-year mark. For the paper trade phase, disk is not an immediate risk. Still recommended to fix before live.

---

## Dependency Audit

| Package | Version (from requirements) | Risk |
|---|---|---|
| pybit | ≥5.x | ✅ Stable Bybit SDK |
| metaapi-cloud-sdk | 29.x | ⚠ Monthly API changes; pin exact version |
| pandas | ≥2.x | ✅ |
| numpy | ≥1.24 | ✅ |
| pyarrow | (parquet read) | ✅ |
| python-dotenv | ✅ | Used correctly |
| ta | (TA-Lib wrapper) | ⚠ Not in current smc_bot/ — used in _archive only |

No critical missing dependencies for BTC bot operation. MetaAPI SDK version pinning is advised.

---

## ENV Variables Audit

| Variable | Required For | Validated on Startup | Risk if Missing |
|---|---|---|---|
| `BYBIT_API_KEY` / `BYBIT_API_SECRET` | BTC live | ❌ | pybit raises mid-cycle |
| `BYBIT_DEMO_API_KEY` / `BYBIT_DEMO_API_SECRET` | BTC paper | ❌ | pybit raises mid-cycle |
| `LIVE_TRADING` | All | ✅ read in executor.py | Defaults to False (safe) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alerts | ❌ | Silent failure — alerts don't send |
| `METAAPI_TOKEN` | Forex | ❌ | Connection error on runner start |

`.env.example` documents all required keys. Startup validation is missing — should assert all required keys are non-empty before creating any SDK sessions.

---

## Final Readiness Summary

| Deployment | Paper Trade | Small Live | Full Live |
|---|---|---|---|
| BTC Bot (Path A) | **BLOCKED** (minor fixes, no supervisor) | NO | NO |
| Forex SMCSniper (Path B) | **BLOCKED** (2 critical) | NO | NO |
| Forex SessionTrader (Path B) | **BLOCKED** (MetaAPI DRAFT + own trial FAIL) | NO | NO |

The BTC bot is 2–5 days of engineering work away from starting a paper trade. The Forex runner is 2+ weeks away from being testable (MetaAPI billing + FVG implementation + Forex Phase-0 retry with different signal).
