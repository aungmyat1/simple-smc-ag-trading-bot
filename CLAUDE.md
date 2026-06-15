# Simple SMC AG Trading Bot — Claude Instructions
# v1.0 | read every session

---

## §0 — PROJECT OBJECTIVE

Ship the smallest bot that can plausibly make money.
**One symbol. One timeframe. One strategy. No research bureaucracy.**

This project is NOT a research platform. It is a production trading bot.
Architecture goal: Data → Signal → Risk → Execute → Log → Alert. Nothing else.

---

## §1 — HARD RULES

1. **Never enable live trading** until Phase-0 gate passes AND paper trade runs 30 days clean.
   `LIVE_TRADING = False` until the owner flips it manually. Not the agent. Ever.

2. **Never tune parameters mid-trial.** Every parameter change = a new trial. Log it.
   The graveyard (ag-auto-trade) was built by tuning losers. Don't repeat it.

3. **One position at a time.** No portfolio, no concurrency, no pairs.

4. **Net-of-fees only.** A backtest result without Bybit taker fees (0.06%/side) applied
   is not a result. Fee floor is real: 15m BTC loses ~0.31R/trade to fees.

5. **Never commit secrets.** API keys live in `.env` (gitignored). Never in code.

6. **Prefer reading over building.** Check `docs/` and `data/` before writing new code.
   The ag-auto-trade graveyard has 17 tested variants — read verdicts before proposing
   a "new" idea that may already be archived there.

---

## §2 — SYSTEM MAP

| Layer | File | Purpose |
|---|---|---|
| Signal | `smc_bot/structure.py` | 1H bias: HH+HL → bullish / LL+LH → bearish |
| | `smc_bot/poi.py` | 1H Order Block + FVG zone detection |
| | `smc_bot/liquidity.py` | 5M sweep: swing pierced, closed back above |
| | `smc_bot/confirmation.py` | 5M CHoCH: close breaks ref high after sweep |
| Risk | `smc_bot/risk.py` | Position sizing + daily/drawdown/consec-loss guards |
| Execution | `smc_bot/executor.py` | Bybit order placement (signal_only_mode by default) |
| Data | `smc_bot/data.py` | OHLCV candle fetching from Bybit |
| Runner | `smc_bot/bot.py` | Main loop: balance → guards → signal → (place) |
| Config | `smc_bot/config.yaml` | All constants; read by `bot.py` and `scripts/backtest.py` |

---

## §3 — CURRENT STRATEGY (Trial 4: SMC Sniper via smc_bot/)

**BTC/USDT:USDT perpetual · Bybit · 1H+5M · 1× leverage**

> STATUS: Phase-0 PENDING (2026-06-15). SMC Sniper: 1H swing bias + OB/FVG POI →
> 5M liquidity sweep + CHoCH. Single-TP exit at 2R. Scored by scripts/backtest.py
> using smc_bot/ signal chain (not _archive).

**Fee constraint (non-negotiable):**
- Bybit taker: 0.06% per side = 0.12% round trip
- On 15m BTC with ATR ≈ 0.3% of price: fee ≈ 0.31R per trade
- A 15m strategy must have gross win rate ≥ 37% to survive fees at 1.5×ATR stop / 2.5R target
- Prior result: EMA + swing break delivered 29% win rate → dead

**What is proven to NOT work (do not re-propose):**
- EMA cross on BTC H1 (A4 — negative gross edge)
- EMA + OB/FVG context on BTC H1 (A4S — worse than A4)
- Pure SMC entry on BTC H1 (A5 — FRAGILE)
- M15 ChoCH/volume confirmation on H1 zones (A1_LTF_WHEN — n=52, gross PF=0.83)
- EMA50/200 + swing breakout + retest on 15m (BOT v1 trial 1 — net PF=0.68)
- EMA50/200 + swing breakout-only on 15m (BOT v1 trial 2 — net PF=0.64)

---

## §4 — PHASE PLAN

| Phase | Condition | Action |
|---|---|---|
| **0 — Gate** | Any new signal | Run `scripts/backtest.py` on 2yr holdout. n ≥ 50 AND net PF > 1.0. |
| **1 — Paper** | Phase 0 PASS | `LIVE_TRADING=False`, 30 days, 100+ trades, no execution bugs |
| **2 — Micro** | Phase 1 clean | $100–300 live, 0.25% risk, verify slippage/latency/sizing |
| **3 — Small** | Phase 2 stable | $500–1000 live, 0.5% risk, 3 months consistent |
| **4 — Scale** | Phase 3 proven | Owner decision only |

---

## §5 — RISK PARAMETERS (non-bypassable)

```python
RISK_PER_TRADE = 0.005     # 0.5% of account per trade (Phase 3; 0.25% in Phase 2)
MAX_DAILY_LOSS = 0.02      # 2% — halt trading for the day
MAX_DRAWDOWN   = 0.10      # 10% from peak — kill switch
LEVERAGE       = 1.0       # no leverage in v1
```

---

## §6 — EXCHANGE / AUTH

- Exchange: **Bybit** (perpetual futures, USDT margined)
- Auth: `pybit` SDK with API key/secret from `.env`
- Never use raw `curl` with signed endpoints — always use the SDK
- Paper mode: Bybit testnet OR set `LIVE_TRADING=False` in config

---

## §7 — WRITE ACTIONS REQUIRE CONFIRM TOKEN

Any order placement or cancellation requires an exact-match CONFIRM token:

| Token | Action |
|---|---|
| `CONFIRM-LONG-BTC` | Place long market entry at current signal |
| `CONFIRM-SHORT-BTC` | Place short market entry at current signal |
| `CONFIRM-CLOSE-BTC` | Close open position at market |
| `CONFIRM-LIVE-ON` | Enable live trading (owner only, irreversible until `CONFIRM-LIVE-OFF`) |

Agent must NEVER self-execute a write action. Always propose, wait for token.

---

## §8 — TELEGRAM ALERTS

Bot token: `TELEGRAM_BOT_TOKEN` (from `.env`)
Chat ID: `TELEGRAM_CHAT_ID` (from `.env`)

Alert events:
- Signal fired (not yet executed — awaiting CONFIRM in manual mode)
- Trade opened / closed
- Daily loss limit hit → trading halted
- Drawdown limit hit → kill switch triggered
- Bot error / exception

---

## §9 — FILE LAYOUT

```
simple-smc-ag-trading-bot/
  smc_bot/
    config.yaml       # all constants — read by bot.py and scripts/backtest.py
    structure.py      # get_bias(): 1H swing structure → bullish/bearish/neutral
    poi.py            # get_pois(), price_in_poi(): 1H OB + FVG zones
    liquidity.py      # get_sweep(): 5M stop-hunt of prior swing
    confirmation.py   # get_choch(): 5M CHoCH after sweep
    risk.py           # calc_qty(), trading_allowed() — pure guards
    executor.py       # Bybit order placement (signal_only_mode blocks real orders)
    data.py           # OHLCV candle fetching from Bybit
    bot.py            # main loop: balance → guards → signal → (place)
  _archive/
    bot_v1/           # Trial 1+2 EMA code — read-only, never re-import in new code
  data/
    cache/            # OHLCV parquet files (BTCUSDT_60m.parquet, BTCUSDT_5m.parquet)
  scripts/
    backtest.py       # Phase-0 gate — imports smc_bot/, NOT _archive
    fetch_data.py     # download OHLCV from Bybit public API
  tests/
    test_signal.py    # detector unit tests (structure/poi/liquidity/confirmation)
    test_smc_risk.py  # risk guard tests
    test_smc_state.py # BotState persistence tests
    test_signal_parity.py  # archive vs live path parity (divergence documented)
  docs/
    VERDICT_LOG.md    # one row per trial — never delete entries
    SIGNAL_SPEC.md    # current signal spec (locked before backtest)
  logs/               # runtime logs (gitignored)
  .env                # secrets (gitignored)
  .env.example        # template (committed, no values)
  CLAUDE.md           # this file
```

---

## §A — VERDICT LOG FORMAT (docs/VERDICT_LOG.md)

One row per trial. Never delete. Every parameter change = new row.

```
| Trial | Date | Signal | TF | n | Gross PF | Net PF | Win% | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | 2026-06-15 | EMA50/200 + swing retest | 15m | 1570 | 1.023 | 0.683 | 29.0% | FAIL |
| 2 | 2026-06-15 | EMA50/200 + breakout-only | 15m | 1333 | 0.993 | 0.640 | 28.4% | FAIL |
```
