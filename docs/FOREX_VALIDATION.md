# Forex Readiness — Step 5 (Phase-0 Validation)

The strategic fork is decided: **validate-first** (run the Phase-0 backtest
*before* building any execution layer) and **forex replaces BTC** (single-focus
pivot, per CLAUDE.md §0). Steps 2–4 (MetaAPI broker adapter, lot-based sizing,
live session repoint) do **not** start until EURUSD/GBPUSD clears Phase-0 here.

> Gate (CLAUDE.md §4): **n ≥ 50 AND net PF > 1.0**, per setup mode, net of cost.
> A FAIL retires the forex strategy layer the same way the Asian-session BTC
> family was retired (Trials 25–26) — log it, do not tune (§1).

---

## What is being validated

The **session-box signal** (`smc_bot/session_range.py`, already built): 4H macro
bias + 1H box → **sweep / range / trend** setup, partial-TP exit (75% at
box-edge/4R, runner to 5R, SL→BE). This is the only strategy logic that is
plausibly forex-native: it failed on 24/7 BTC (Trials 25–26) precisely because
it depends on a session open. Forex has real session opens.

The backtest harness already exists: `scripts/backtest.py --signal asian_session`
with per-mode scoring (`--asian-setup {all,sweep,range,trend}`), per-year
breakdown, and auto-append to `docs/VERDICT_LOG.md`.

The box window comes from `config.yaml → session.asian.start_h/end_h`. The
default `00–08 UTC` **is** the Asian range box in forex terms; the classic play
is to trade its London-open break/sweep. Validate that window first; only change
it (Step 4) if the verdict warrants it. Changing the window = a new trial (§1).

---

## The one thing that had to be built: the forex cost model

The Bybit fee model (`fee = 0.12% × notional`) is **wrong for forex** — on
EURUSD ≈ 1.10 that is ~13 pips round-trip, roughly 10× reality, and would fail
every forex run on cost alone. `scripts/backtest.py` now has a `--cost-model`:

| Model | Cost (price terms) | Use |
|---|---|---|
| `pct` (default) | `0.12% × entry_price` | Bybit/BTC — all prior trials, unchanged |
| `forex` | `(spread_pips + commission_rt_pips) × pip_size` | EURUSD/GBPUSD |

**VT Markets Raw ECN defaults** (override per instrument as needed):

- `--spread-pips 0.8` — average raw EURUSD spread (one crossing)
- `--commission-rt-pips 0.6` — ≈ $3/side/lot ($6 round-trip / $100k ≈ 0.6 pip)
- `--pip-size 0.0001` — majors (use `0.01` for JPY pairs)

So `fee_r ≈ 1.4 pip / stop_dist`. On a typical ~20-pip session stop that is
~0.07 R/trade — vs ~0.66 R under the %-model. Covered by `tests/test_forex_cost.py`
(7 tests, including end-to-end wiring through `run_backtest_asian`).

GBPUSD spreads run wider — start `--spread-pips 1.2` and tighten only with a
VT Markets spread sample (each change is a new trial).

---

## Data pipeline (one M1 master → every timeframe)

`data/cache/` is gitignored and **forex feeds are not reachable from the web
session container** (MetaAPI, Dukascopy, Yahoo, Stooq all return 403; only pypi
and github raw are open). All fetching/resampling runs on the **VPS** (RUNBOOK
§0) or locally, where the feeds are allowed and `.env` holds the credentials.

Pull **one M1 master series per symbol** and derive H4/H1/M15/M5 from it, rather
than fetching each timeframe separately. Separate pulls drift — an H4 bar from
the feed and an H4 bar built from its own M1 can disagree on OHLC because of
differing session boundaries and gap handling. Deriving everything from the same
M1 keeps the macro-bias timeframe (H4) and the execution timeframe (H1) anchored
to the same ticks. `scripts/resample_ohlcv.py` does this (left-labelled bars,
weekend gaps dropped not flat-filled, output schema identical to the fetchers).

```
M1 master  →  H4 (4H macro bias)  →  H1 (box + execution)  →  M15  →  M5
```

**Two sources, two jobs:**

| Layer | Source | Why |
|---|---|---|
| Research / validation | **Dukascopy M1** (free, no account) | Broker-independent ground truth — long history, used to find/validate the edge and as a cross-check against the broker feed |
| Execution truth | **MetaAPI / VT Markets M1** (`fetch_forex_data.py --interval 1`) | Matches the live broker's exact bars, spreads and session boundaries — the feed that actually fills orders |

A strategy that clears the gate on Dukascopy *and* holds up on the VT Markets
feed is far less likely to be a data artifact. Disagreement between the two is
itself a finding (log it; do not tune to whichever looks better).

**Fetch 5-year holdouts** (matches the BTC Trial-21 window) and resample:

```bash
# Source M1 (run on the VPS / locally — pick one source per pass)
python scripts/fetch_forex_data.py --symbol EURUSD --interval 1 --days 1825   # VT Markets
#   …or Dukascopy M1 via your downloader → data/cache/EURUSD_1m.parquet (same schema)

# Derive every timeframe the backtest reads from that one master
python scripts/resample_ohlcv.py --in data/cache/EURUSD_1m.parquet --intervals 5 15 60 240
python scripts/resample_ohlcv.py --in data/cache/GBPUSD_1m.parquet --intervals 5 15 60 240
```

3–5 years is the minimum (the gate needs n≥50 per mode); 5–10 years is preferred
so the verdict spans trending, ranging, high- and low-volatility regimes.

---

## Run the gate (turnkey — recommended)

One command runs both symbols × all setup modes × a **spread-sensitivity sweep**
and prints a single go/no-go. A setup only earns a robust PASS if it clears the
gate (n≥50, net PF>1.0) at *every* spread level — guarding against a verdict that
hinges on one guessed spread:

```bash
python scripts/forex_phase0.py                       # EURUSD+GBPUSD, spreads 0.8/1.2/2.0
python scripts/forex_phase0.py --spreads 0.8 1.5 2.5 # custom stress range
```

Output: per-symbol table of `n / netPF` per spread (✓ = clears gate), then GO /
NO-GO. Exit 0 if any (symbol, mode) is a robust PASS. This does not auto-append
to VERDICT_LOG — log the passing/failing trial rows manually (Trial 27/28 below).

## Run the gate (granular — per symbol, per setup mode)

Use this when you want the full per-trial report (per-year breakdown, auto
VERDICT_LOG row) for a specific symbol/mode:

```bash
# EURUSD — all modes, then split out each mode for its own n≥50 / PF>1 check
for SET in all sweep range trend; do
  python scripts/backtest.py --signal asian_session \
    --htf data/cache/EURUSD_240m.parquet \
    --ltf data/cache/EURUSD_60m.parquet \
    --cost-model forex --spread-pips 0.8 --commission-rt-pips 0.6 --pip-size 0.0001 \
    --asian-setup $SET --side both \
    --trial 27 --run-label "Trial 27 EURUSD $SET" \
    --csv data/eurusd_$SET.csv
done

# GBPUSD (wider spread)
for SET in all sweep range trend; do
  python scripts/backtest.py --signal asian_session \
    --htf data/cache/GBPUSD_240m.parquet \
    --ltf data/cache/GBPUSD_60m.parquet \
    --cost-model forex --spread-pips 1.2 --commission-rt-pips 0.6 --pip-size 0.0001 \
    --asian-setup $SET --side both \
    --trial 28 --run-label "Trial 28 GBPUSD $SET" \
    --csv data/gbpusd_$SET.csv
done
```

Each run prints the gate verdict + per-year table and appends a row to
`docs/VERDICT_LOG.md`. (`--asian-setup` filters which modes count toward n; run
`all` for the headline and each mode individually since the gate is per mode.)

---

## Two bars: the Phase-0 gate vs. the go-live bar

The Phase-0 gate (n≥50, net PF>1.0) is only the **keep-or-retire** line — clear
it and the strategy is worth carrying forward; fail it and the forex layer is
retired (§1). It is *not* the bar for risking money. Before Phase-2 micro-live
(CLAUDE.md §4) the strategy must also clear the higher **go-live bar**, measured
net of the forex cost model on the holdout:

| Metric | Go-live minimum | Where it's read |
|---|---|---|
| Net PF | > 1.2 | report `Net PF` |
| Win rate | > 35% | report `Win rate` |
| Max drawdown | < 15% of equity (≈ 30R at 0.5% risk) | report `Max DD` (R) |
| Trades (n) | > 500 preferred (≥50 hard floor) | report `Trades` |
| Walk-forward net PF | > 1.1 in every window | per-window runs below |
| Out-of-sample year | profitable, untouched by any decision | `--from`/`--to` split |

A strategy that survives all of these on **both** EURUSD and GBPUSD is far more
likely to stay profitable live than one that only clears the bare gate on a
single in-sample run. If the 5-year holdout cannot reach n>500, that is a
statistical-power limitation to record — **not** a reason to loosen filters or
tune (every parameter change is a new trial, §1).

## Walk-forward + out-of-sample (locked params, no refit)

The backtest takes `--from`/`--to` (UTC dates, inclusive) so the *same* locked
config can be scored across rolling windows and a held-out year. This is
validation, not optimization: parameters never change between windows — a change
would be a new trial, not a walk-forward step (§1).

```bash
# Hold out the most recent year as untouched OOS; decide only on the earlier span
python scripts/backtest.py --signal asian_session \
  --htf data/cache/EURUSD_240m.parquet --ltf data/cache/EURUSD_60m.parquet \
  --cost-model forex --spread-pips 0.8 --commission-rt-pips 0.6 --pip-size 0.0001 \
  --from 2021-06-18 --to 2025-06-17 --asian-setup all --trial 27 \
  --run-label "Trial 27 EURUSD in-sample"

# Then score the untouched OOS year — same params, no tuning
python scripts/backtest.py --signal asian_session \
  --htf data/cache/EURUSD_240m.parquet --ltf data/cache/EURUSD_60m.parquet \
  --cost-model forex --spread-pips 0.8 --commission-rt-pips 0.6 --pip-size 0.0001 \
  --from 2025-06-18 --to 2026-06-18 --asian-setup all --trial 27 \
  --run-label "Trial 27 EURUSD OOS-2025"
```

Roll yearly `--from`/`--to` windows for the walk-forward check; the go-live bar
requires net PF > 1.1 in **every** window, not just on the pooled run. The
per-year table the report already prints is the quick read; the windowed runs are
the auditable proof. News-event robustness is a sanity check on the same data —
confirm the edge does not depend on a handful of high-impact prints (NFP/CPI/FOMC)
by spot-checking the trade log around those dates before any capital is risked.

## Decision after the verdict

- **Any mode PASSES (n≥50, net PF>1, robust across years):** proceed to Step 2
  (`smc_bot/brokers/` MetaAPI adapter + `test_metaapi_paper_gate.py`), Step 3
  (lot-based `calc_qty` from the MetaAPI symbol spec — pip value, contract size,
  lot step), Step 4 (repoint `config.yaml` symbol + session window).
- **All modes FAIL:** retire the forex strategy layer in `VERDICT_LOG.md`. Do
  not build the execution layer. The pivot stops here (§1).
