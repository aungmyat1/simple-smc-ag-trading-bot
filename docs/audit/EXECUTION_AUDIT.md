# EXECUTION SAFETY AUDIT
**Audit date:** 2026-06-18  
**Files audited:** smc_bot/bot.py, smc_bot/executor.py, smc_bot/data.py, runner.py, strategies/smc_sniper.py, brokers/metaapi.py, scripts/backtest.py

---

## Summary

| Severity | Count |
|---|---|
| CRITICAL | 2 |
| HIGH | 4 |
| MEDIUM | 5 |
| LOW | 3 |

**Overall execution safety:** ⚠ NOT SAFE FOR LIVE — 2 critical issues block deployment.

---

## CRITICAL Issues

### [CRITICAL-1] FVG Retest Gate is a STUB in Forex Live Code
- **File:** `strategies/smc_sniper.py`, Steps 11–12
- **Evidence:** The method body for the FVG retest confirmation contains `pass` where it should call `poi.get_owned_fvg()` and check zone membership. The config key `fvg_retest_enabled: true` is read but has no effect.
- **Risk:** The Forex live bot enters trades without the FVG retest gate that was present in all backtest runs. The backtested edge (T22 net PF=1.20) was derived WITH this gate active. Live Forex trades will fire on CHoCH alone — a signal family that produced net PF=0.54 on EURUSD and 1.40 on GBPUSD at n=38 (insufficient sample).
- **Fix:** Implement the owned-FVG check in `smc_sniper.py` steps 11–12, mirroring `smc_bot/bot.py` lines 530–565. Port `poi.get_owned_fvg()` call with the same parameters.

### [CRITICAL-2] MetaAPI Account in DRAFT — Forex Broker Cannot Execute
- **File:** `brokers/metaapi.py`, `runner.py`
- **Evidence:** MetaAPI account `35e4d9de-1f2a-474e-a4d0-5a03fd4f5e09` is in DRAFT status per project documentation and memory. The `place_order()` method in `brokers/metaapi.py` will raise a connection error on any live call.
- **Risk:** Runner launches, signals fire, but ALL order placements fail silently or raise unhandled exceptions. No actual trades are placed; bot appears to run while doing nothing.
- **Fix:** Resolve MetaAPI billing at app.metaapi.cloud/billing before any Forex live testing.

---

## HIGH Issues

### [HIGH-1] TP1 Fill Detection via Position Size Heuristic (Not Event-Driven)
- **File:** `smc_bot/bot.py` ~line 307
- **Evidence:** `if (state.tp1_placed and not state.tp1_filled and state.entry_qty > 0 and pos_size < state.entry_qty * 0.75)` — TP1 fill is inferred from position size shrinking, not from an order-fill API call.
- **Risk:** (a) A partial fill at TP1 (exchange fills 40% instead of 50%) passes the 75% threshold and triggers SL-to-BE prematurely, locking in a smaller gain. (b) Exchange rounding of qty field could trigger false positive. (c) In a fast market where price gaps through TP1, the size check may never trigger within one poll cycle.
- **Fix:** Use `executor.get_closed_pnl()` with the TP1 order ID to confirm fill, or poll open orders by order ID.

### [HIGH-2] Flat-File State Persistence — No Crash Recovery
- **File:** `smc_bot/bot.py` — `BotState`, JSON write
- **Evidence:** State is written to `smc_bot_state.json` using `json.dump()` with no atomic write pattern. A process crash mid-write corrupts the file. On restart, `BotState` initializes from defaults — resetting `consecutive_losses`, `peak_equity`, and `day_start_equity` to 0.
- **Risk:** After a crash mid-trade, the bot restarts with zeroed risk counters. It will enter new trades even if the daily or drawdown limit was already breached before the crash.
- **Fix:** Use `os.replace()` (atomic rename) on a temp file: `json.dump → temp_file → os.replace(temp, state_file)`. Add a `last_updated_ts` field; on startup, validate age of state file.

### [HIGH-3] No Duplicate-Order Guard in Forex Runner
- **File:** `runner.py`, `strategies/smc_sniper.py`
- **Evidence:** `smc_bot/bot.py` has `last_signal_ts` dedup guard (line ~582). `strategies/smc_sniper.py` has no equivalent. `runner.py` runs strategies on a poll interval with no global position check before calling `strategy.execute()`.
- **Risk:** If two consecutive polls both detect a CHoCH (possible if the signal doesn't expire within one poll), two market orders fire on the same setup. On MetaAPI/MT5, both orders execute independently — two open positions on the same signal.
- **Fix:** Add `last_signal_ts` to `strategies/base.py` and enforce it in `smc_sniper.py` before calling broker `place_order()`. Also add a pre-entry position check: if already in trade, skip.

### [HIGH-4] Undocumented Market Entry Fallback When No LTF Zone Found
- **File:** `smc_bot/bot.py` ~line 510
- **Evidence:** Code path: `if fvg_retest: ... elif not ltf_zones: log.info("fast move; proceeding to market entry")` — enters a market order with NO LTF zone confirmation when the FVG retest path returns None and no OB/FVG exists on LTF.
- **Risk:** This fallback fires on the fastest post-CHoCH candles, precisely when price is most extended. No backtest covers this fallback path — it fires only in live conditions not represented in historical data. Entry with no zone confirmation is contrary to the entire signal premise.
- **Fix:** Remove the fallback. If no LTF zone exists, skip the trade: `log.info("no LTF zone; skipping"); return`.

---

## MEDIUM Issues

### [MEDIUM-1] Lookahead Bias in Swing Detection Library (F-1 Partial Fix)
- **File:** `scripts/backtest.py` — custom `_swing_highs_np()` / `_swing_lows_np()` (causal); but the earlier F-1 test at `/opt/forex-validate/` used the `smc` library whose `swing_highs_lows()` uses `shift(-swing_length)` (future bars).
- **Risk:** The F-1 PASS result (EURUSD H1, n=59, net PF=1.289 at `/opt/forex-validate/`) has residual look-ahead contamination from the `smc` library. The current `scripts/backtest.py` uses causal rolling arrays and is clean. The F-1 result should NOT be cited as clean evidence.
- **Fix:** F-1 must be rerun on the current causal backtest infrastructure before being used as evidence. It is excluded from this audit's profitability evidence.

### [MEDIUM-2] Current-Bar Close Used as Entry Price in Asian Session Backtest
- **File:** `scripts/backtest.py:run_backtest_asian()`
- **Evidence:** `entry_price = float(open_1h[fill_bar])` where `fill_bar = i + 1` ✅ — but within the backtest the Asian box signal fires at `box.close_h` UTC exactly, and the `_in_asian_session()` check is correct. Minor: the box is built by scanning up to the last 200 bars, recalculated each iteration. This is O(n) per bar in a tight loop.
- **Risk:** No execution issue; performance only.

### [MEDIUM-3] Timezone Awareness Inconsistency in Data Alignment
- **File:** `scripts/backtest.py:_align_htf()` and `_precompute_4h()`
- **Evidence:** `htf4_ts = df_4h["ts"].values` — numpy datetime64 arrays. `np.searchsorted` on these works correctly only if both arrays have the same timezone encoding. Parquet files from yfinance may have timezone-aware timestamps while Bybit fetches may not.
- **Risk:** Misaligned timestamps cause each LTF bar to map to the wrong HTF bar — every trade uses wrong bias/POI data. Observable symptom: unexpectedly high trade count or low trade count in runs mixing data sources.
- **Fix:** Normalize all parquet files to UTC-aware timestamps on load in `_precompute()`.

### [MEDIUM-4] No Position Sync on Bot Restart (BTC)
- **File:** `smc_bot/bot.py:main()`
- **Evidence:** On startup, `state.entry_price = 0.0` and `state.position_open = False`. The bot checks `executor.get_position()` on each cycle, so it WILL detect an existing position correctly. However, `state.tp1_placed` and `state.entry_qty` are reset to defaults — if the bot was restarted mid-trade after TP1 was placed but before TP1 filled, it re-places TP1.
- **Risk:** Double TP1 order — one from before restart, one new. Both execute, closing 100% of position at TP1 price. SL-to-BE then fires on an already-closed position (harmless but noisy).
- **Fix:** On startup, if `get_position()` returns non-None, restore `tp1_placed=True` and skip re-placing TP1.

### [MEDIUM-5] Runner.py Has No Global Concurrent-Position Cap
- **File:** `runner.py`
- **Evidence:** SMCSniper and SessionTrader have independent risk states. Both can open positions simultaneously on the same pair (EURUSD). The combined notional could exceed acceptable risk.
- **Risk:** Two simultaneous 0.5%-risk trades on EURUSD = 1% combined exposure. If both trigger stops at the same time (correlated signals on same pair), daily loss could double unexpectedly.
- **Fix:** Add a global `max_open_positions` guard in `RiskManager` checked before either strategy places an order.

---

## LOW Issues

### [LOW-1] BTCUSDT_5m.parquet Only 2yr (Not 5yr Like 1H/4H)
- **File:** `data/cache/BTCUSDT_5m.parquet`
- **Evidence:** 5M cache spans 2024-06-16 → 2026-06-16 (2yr, 210,240 bars). 1H cache spans 2021-06-17 → 2026-06-16 (5yr). Sprint trials (T4–T18) that used the 1H+5M chain were therefore backtested on 2yr of LTF data, not 5yr. However, ALL those trials FAILED, so the data period is not a concern for the current PASS (T22 uses 4H+1H which has full 5yr data).
- **Risk:** Low (failed trials only).

### [LOW-2] No Log Rotation
- **File:** `smc_bot/config.yaml` `logging: file: logs/smc_bot.log`; `logging.FileHandler` in bot.py
- **Evidence:** Plain `FileHandler` — no `RotatingFileHandler` or logrotate config.
- **Risk:** On VPS, log file grows indefinitely. On 1 GB disk, ~60 days of active trading fills the disk, causing silent log loss and potential write errors.
- **Fix:** Replace `FileHandler` with `RotatingFileHandler(maxBytes=10MB, backupCount=5)`.

### [LOW-3] No .env Validation on Startup
- **File:** `smc_bot/bot.py:main()`
- **Evidence:** Missing env vars (e.g., `BYBIT_DEMO_API_KEY` unset) cause `None` to be passed to `executor.make_session()`, which raises a pybit exception mid-cycle rather than failing fast on startup.
- **Fix:** Add startup env validation: assert all required keys are non-empty before creating the session.

---

## Safe Patterns Confirmed

- ✅ **Entry is always next-bar open**: `entry_bar = i + 1` in all backtest paths; `entry_price = open[entry_bar]`
- ✅ **Swing confirmation is causal**: `max_conf = htf_idx - SWING_N` with `bisect_right` ensures no future bars contaminate swing detection
- ✅ **Signal dedup (BTC)**: `last_signal_ts` in `BotState` prevents acting twice on the same LTF candle
- ✅ **LIVE_TRADING guard**: env-var gated in `executor._live()`, checked before every order call
- ✅ **Paper mode synthetic orders**: In paper mode, `place_order()` returns a synthetic result with no exchange call
- ✅ **Skip-until pattern**: Backtest `skip_until = exit_bar + 1` prevents overlapping trades
- ✅ **ATR displacement check**: `_fast_displacement()` uses ATR from bars preceding the signal bar only
- ✅ **FVG audit causal chain**: All 3 steps (displacement → FVG → retest) scan bars strictly after the CHoCH bar
