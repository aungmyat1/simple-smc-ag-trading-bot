# Simple SMC AG Trading Bot

One symbol (BTCUSDT perp), two timeframes (1H → 5M), one strategy: the textbook
SMC "sniper" sequence — **HTF bias → POI → liquidity sweep → CHoCH → OB/FVG
retest → enter → structural stop → liquidity target.** Bybit, 1× leverage,
one position at a time. Read [`CLAUDE.md`](CLAUDE.md) before changing anything.

---

## ⚠️ Status — read this before you "just run it"

> **The strategy has NOT passed its Phase-0 gate. It has never even been
> backtested on real data.** ([`docs/VERDICT_LOG.md`](docs/VERDICT_LOG.md) row 3 = `PENDING`.)
>
> This exact "zone → sweep → CHoCH → entry" pattern is the **archived FRAGILE
> failure** in this project's own record. The code is real and tested
> (17/17 unit tests pass); the *edge* is unproven. Do not trade it — paper or
> live — until **`make gate` passes** (`n ≥ 50` AND net PF > 1.0 after Bybit
> fees) and then 30 days of clean paper trading. Live trading stays **off**
> until the owner flips it manually (`CLAUDE.md` §1).

The bot is built and runnable. Whether running it *makes money* is the open
question the gate exists to answer — so run the gate first.

---

## Quickstart

```bash
make setup     # install dependencies
make test      # run unit tests (should be 17 passed)
make gate      # THE DECISION: fetch 2yr data + run Phase-0 backtest
make paper     # only if the gate passes — run the bot in paper mode
```

`make help` lists every target. `make gate` = `make fetch` + `make backtest`.

> **Network note:** `make fetch` and `make paper` need outbound access to
> `api.bybit.com`. Run them on a machine that can reach Bybit — some cloud
> sandboxes (including the one this repo may have been edited in) block it.

---

## The flow, mapped to the code

Default state is **NO_TRADE**. A long fires only when *all* of these align —
SMC decides **where** (context), the 5M sequence decides **when** (trigger):

| Step | Meaning | Code |
|---|---|---|
| HTF bias | 1H close > EMA200, slope up | `signal._htf_bias` |
| POI | bullish OB / FVG (≥1.5×ATR displacement) | `signal._htf_poi_zones` |
| Discount | price ≤ 1H 50% fib | `signal._htf_fib50` |
| Sweep | 5M low pierces swing low, closes back inside | `signal._find_sweep` |
| CHoCH | 5M breaks post-sweep swing high | `signal._has_choch` |
| Entry | retest into fresh 5M OB / FVG | `signal._ltf_ob_zones` / `_ltf_fvg_zones` |
| Stop | structural, below the sweep wick | `signal.get_ltf_signal` |
| Targets | 50%@1R → BE, 25%@2R, 25% to 1H equal-highs | `runner._manage_position` |

Position size comes from `risk.calc_position_size` (fixed 0.5% risk), never
from the signal. The signal never places an order — that's `executor.py`.

---

## Safety rails (non-bypassable)

- `LIVE_TRADING=false` by default. The agent must **never** flip it (`CLAUDE.md` §1).
- Order placement requires an exact CONFIRM token from the owner (`CLAUDE.md` §7).
- Risk per trade 0.5%, daily-loss halt 2%, drawdown kill-switch 10% (`bot/config.py`).
- Secrets live in `.env` (gitignored). Copy `.env.example` → `.env` to configure.

---

## Layout

```
bot/        config · signal · risk · executor · logger · alerts · runner
scripts/    fetch_data.py (download OHLCV) · backtest.py (Phase-0 gate)
tests/      test_signal.py · test_risk.py
docs/       SIGNAL_SPEC.md (locked spec) · VERDICT_LOG.md (one row per trial)
```

If the gate fails, **log the verdict and change the signal family** — do not
tune parameters on a loser (`CLAUDE.md` §1, rule 2). Every parameter change is
a new trial with its own row in `docs/VERDICT_LOG.md`.
