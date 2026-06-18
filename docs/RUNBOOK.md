# RUNBOOK — Paper Trading the SMC Bot

Operational guide for running the bot for Phase-1 paper trading.
Strategy/parameters live in `CLAUDE.md` and `docs/VERDICT_LOG.md` — this is ops only.

Validated chain: **Trial 21/22 — HTF=4h, LTF=1h, mitigation OFF.**

---

## 0. Prerequisites (one-time, on the VPS)

The bot runs on a persistent host (see `smc-bot.service`), **not** in an ephemeral
session container. Outbound access to `api.bybit.com` is required.

```bash
cd ~/simple-smc-ag-trading-bot
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit .env
```

Fill `.env` with **Bybit Demo** keys (and Telegram, optional):

```
BYBIT_DEMO_API_KEY=...
BYBIT_DEMO_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
LIVE_TRADING=false
```

> ⚠️ Keys are required **even for signal-only mode**: `run_cycle` fetches the
> wallet balance first and skips the entire cycle if it is 0. No keys → no signals.

---

## 1. Two-gate safety model

An order reaches the exchange **only if both gates open**:

| `signal_only_mode` (config) | `LIVE_TRADING` (.env) | `bybit.demo` | Result |
|---|---|---|---|
| `true`  | (any)   | (any) | Log signal + Telegram only. No exchange call. |
| `false` | `false` | (any) | `place_order` runs but executor returns a **synthetic PAPER** fill. |
| `false` | `true`  | `true`  | **Real orders on Bybit Demo** (simulated funds). |
| `false` | `true`  | `false` | **Real orders, real money.** Owner-only (CLAUDE.md §7). |

Recommended Phase-1 progression: start `signal_only_mode: true`; after ~1–2 weeks
of clean signal logs, set `signal_only_mode: false` (keep `LIVE_TRADING=false`,
`demo: true`) to exercise the order path on the Demo account.

---

## 2. Pre-flight + start

```bash
python scripts/readiness_report.py      # must print VERDICT: READY
sudo systemctl restart smc-bot          # or: nohup .venv/bin/python -m smc_bot.bot &
sudo systemctl status smc-bot
journalctl -u smc-bot -f                 # live logs
```

Dashboard (localhost only — tunnel in, never expose `0.0.0.0`):

```bash
python -m dashboard.server               # http://localhost:8000/dashboard/
```

---

## 3. Daily monitoring

- **Logs**: `journalctl -u smc-bot -f` or `logs/smc_bot.log`.
- **Signals**: `smc_bot_signals.csv` (one row per acted-on LTF candle — deduped).
- **Trades**: `smc_bot_trades.csv` + dashboard "Recent Trades".
- **State**: `smc_bot_state.json` (peak/day-start equity, consec-losses, dedup ts).
- **Guards**: a `GUARD HALT` log/alert = daily-loss / drawdown / consec-loss tripped.

Phase-1 target: **30 days clean, 100+ trades, no execution bugs** (CLAUDE.md §4).

---

## 4. Mitigation parity (read before "no trades" panic)

If the bot logs **"No 4H POI zones"** constantly and never trades, check the
mitigation filter first.

- The validated edge (Trial 8/20/21/22) runs with the mitigation filter **OFF**.
  Trials 9–10 proved any ON level rejects ~76% of 4H zones and collapses the
  signal count to near-zero.
- Config: `poi.mitigation_enabled` must be `false`. Backtest must run with
  `--mitigation-pct none` (now the default) to reproduce the live bot.
- Enabling mitigation is a **new trial** (CLAUDE.md §1) — register it in
  `docs/VERDICT_LOG.md`. The test `tests/test_config_guards.py` enforces OFF.

Verify live == backtest:

```bash
python scripts/backtest.py --run-label "parity check"   # mitigation off by default
grep -E "mitigation_enabled|htf:|ltf:" smc_bot/config.yaml
```

---

## 5. Stop / kill

```bash
sudo systemctl stop smc-bot     # SIGTERM → state flushed (10s grace), Telegram notice
```

To halt all order routing instantly without stopping the process: set
`signal_only_mode: true` in `config.yaml` and restart. To disable live routing:
`LIVE_TRADING=false` in `.env` and restart.
