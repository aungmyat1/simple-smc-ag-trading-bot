# EXECUTION SAFETY AUDIT

**Project:** simple-smc-ag-trading-bot
**Auditor:** Senior Quant Audit (Claude Sonnet 4.6)
**Date:** 2026-06-18
**Scope:** Runtime execution safety — order placement, state management, position tracking, risk guards, async concurrency

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3 |
| HIGH     | 5 |
| MEDIUM   | 4 |
| LOW      | 2 |
| **Total issues** | **14** |

**Overall verdict: NOT SAFE FOR LIVE TRADING.**

Three critical issues are present that can cause ghost duplicate positions, a broken import that prevents the async runner from ever fetching candle data, and a permanently dead consecutive-loss halt guard. Five high-severity issues compound these with pre-fill price contamination, a no-op FVG gate, and a balance-check/order-placement race condition under asyncio concurrency. The bot in its current state must not be promoted beyond paper mode until EXEC-001 through EXEC-003 are resolved at minimum, and the full high-severity list is addressed before any Phase-1 deployment.

---

## Critical Issues

### [CRITICAL] EXEC-001 — Position desync on `get_position` API failure

- **File:** `smc_bot/bot.py` (~line 256)
- **Evidence:**
  ```python
  pos = executor.get_position(session, sym)
  in_position = pos is not None
  if state.was_in_position and not in_position:
      ...state.open_order_id = ''
      state.entry_qty = 0.0
      state.tp1_filled = False
      state.save()
  # executor.get_position: except Exception as exc: log.error(...); return None
  ```
- **Risk:** `executor.get_position()` swallows all exceptions and returns `None` on any API error. If the exchange is temporarily unreachable, `None` is returned as though no position exists. The bot sees `state.was_in_position=True` and `in_position=False`, increments `consecutive_losses`, wipes all state (`open_order_id`, `entry_price`, `entry_qty`), and on the next poll generates a new entry signal — while the real position is still open on the exchange. This creates a ghost duplicate position with unbounded exposure.
- **Fix:** Do not treat a `get_position` exception as "no position." Return a typed sentinel (e.g. raise the exception, or return a result with `is_error=True`) and skip the entire cycle on API errors, leaving `state.was_in_position` unchanged. Only treat `None` as "no position" when the call definitively succeeds with no position found.

---

### [CRITICAL] EXEC-002 — `fetch_candles` does not exist in `smc_bot.data`; runtime `ImportError`

- **File:** `runner.py` (~line 70)
- **Evidence:**
  ```python
  from smc_bot.data import fetch_candles
  ...
  df_htf = await asyncio.get_event_loop().run_in_executor(None, fetch_candles, symbol, htf_tf, htf_bars)
  df_ltf = await asyncio.get_event_loop().run_in_executor(None, fetch_candles, symbol, ltf_tf, ltf_bars)
  ```
  `smc_bot/data.py` only defines `get_candles()`. `fetch_candles` does not exist.
- **Risk:** The async runner raises `ImportError` at startup. No candle data is ever fetched. All signal generation is silently skipped on every cycle. The runner appears to be running (no crash after startup) but produces zero signals and zero trades. This failure mode is invisible unless logs are actively monitored.
- **Fix:** Replace `fetch_candles` with `get_candles` at the import site and all call sites in `runner.py`, ensuring the argument signature matches (`symbol`, `timeframe`, `limit`). Alternatively, add a `fetch_candles` alias in `smc_bot/data.py`.

---

### [CRITICAL] EXEC-003 — `RiskManager.record_trade()` is never called; consecutive-loss halt is dead

- **File:** `runner.py` (~line 83)
- **Evidence:**
  ```python
  # risk_mgr.record_trade is defined in risk/manager.py but is called nowhere in runner.py.
  # After broker.place_order(), no outcome tracking feeds back to risk_mgr.
  # consec_losses stays at 0 permanently.
  ```
- **Risk:** The consecutive-loss halt guard in `trading_allowed()` will never fire regardless of how many consecutive losing trades are taken. A drawdown sequence that should halt the bot — e.g. 5 losing trades in a row — is invisible to the risk manager. The bot continues placing new orders with no circuit breaker.
- **Fix:** After each position close is detected (via `get_position()` returning `None` where a position was previously open), calculate the realized PnL from the broker and call `risk_mgr.record_trade(name, pnl)`. This should be wired into the position-close handling block immediately after state is confirmed as closed.

---

## High Issues

### [HIGH] EXEC-004 — IOC order fill not confirmed before writing `was_in_position=True`

- **File:** `smc_bot/bot.py` (~line 666)
- **Evidence:**
  ```python
  result = executor.place_order(session, sym, side, qty, sl, tp)
  order_id = result.get('orderId', '')
  ...
  state.entry_price = price   # pre-fill price, not actual fill
  state.was_in_position = True
  state.save()
  ```
- **Risk:** `place_order()` uses `timeInForce=IOC`. If the order is rejected after returning `retCode=0` (e.g. insufficient margin, price moved away), state is written as if a real position is open. On the next cycle, `was_in_position=True` plus `in_position=False` triggers a spurious consecutive-loss increment and full state wipe, corrupting the risk counters without any actual trade having occurred.
- **Fix:** After `place_order()` returns, immediately call `get_position()` and confirm the position exists before setting `state.was_in_position=True`. If the position is not present, log the miss and leave state unchanged. Record the actual fill price from `get_position()` `avgPrice` field.

---

### [HIGH] EXEC-005 — `state.entry_price` set from pre-order candle close, not actual fill price

- **File:** `smc_bot/bot.py` (~line 711)
- **Evidence:**
  ```python
  price = float(df_5m['close'].iloc[-1])   # snapshot before order sent
  ...
  state.entry_price = price
  ...
  executor.set_trading_stop(session, sym, sl=state.entry_price)  # uses pre-fill price as BE
  ```
- **Risk:** The breakeven stop-loss move is placed at the last-closed-candle price rather than the actual fill price. Depending on slippage direction, this places the breakeven SL above or below the real entry, causing premature stop-outs (stop triggered before breakeven is reached) or a stop that does not protect capital (stop remains in loss territory).
- **Fix:** After confirming the fill via `get_position()`, extract `avgPrice` from the position dict and store that as `state.entry_price` before any downstream SL calculations.

---

### [HIGH] EXEC-006 — `fvg_retest_enabled` gate in `SMCSniper._run_chain()` is a no-op `pass`

- **File:** `strategies/smc_sniper.py` (~line 216)
- **Evidence:**
  ```python
  if lc.get('fvg_retest_enabled', True):
      # get_choch already embeds the FVG retest logic when the flag is set
      # in the global CFG — here we re-check the result from choch
      pass   # gate already enforced by confirmation module when enabled
  ```
- **Risk:** `confirmation.get_choch()` has no knowledge of FVG retest and does not enforce it. The comment is incorrect. The result is that the strategy generates entry signals without requiring price to retrace into the owned FVG, violating the documented 15-step chain and invalidating backtest parity. Live performance will deviate from backtested performance in an unknown direction.
- **Fix:** Port the `get_owned_fvg` + `price_in_zone` logic from `smc_bot/bot.py` (lines 487–508) into `_run_chain` at steps 11–12. The FVG retest check must be an explicit gate that blocks signal generation when price is not inside the owned FVG zone.

---

### [HIGH] EXEC-007 — Balance-check/order-placement race under `asyncio.gather()`

- **File:** `runner.py` (~line 213)
- **Evidence:**
  ```python
  tasks = [_run_strategy(name=name, ..., broker=broker, risk_mgr=risk_mgr, ...) for ...]
  await asyncio.gather(*tasks)
  # Inside _run_strategy:
  balance = await broker.get_balance()      # <-- yield point; other strategy runs here
  risk_mgr.update_balance(name, balance)
  ...
  result = await broker.place_order(...)    # <-- yield point; race window
  ```
- **Risk:** `SMC_SNIPER` and `SESSION_TRADER` run concurrently via `asyncio.gather()`, sharing a single `RiskManager` and `MetaApiBroker` instance. Between `broker.get_balance()` and `broker.place_order()`, the other coroutine can issue its own order, so the risk check is evaluated against stale balance. Both strategies can place simultaneous orders on the same account, defeating all per-trade and daily-loss risk guards.
- **Fix:** Wrap the check-then-place sequence in an `asyncio.Lock` (per symbol or globally) to serialize the balance-check through order-submission window. Alternatively, run strategies sequentially rather than with `gather()`.

---

### [HIGH] EXEC-008 — `asyncio.get_event_loop()` deprecated; raises `RuntimeError` in Python 3.12

- **File:** `runner.py` (~line 70)
- **Evidence:**
  ```python
  df_htf = await asyncio.get_event_loop().run_in_executor(None, fetch_candles, symbol, htf_tf, htf_bars)
  df_ltf = await asyncio.get_event_loop().run_in_executor(None, fetch_candles, symbol, ltf_tf, ltf_bars)
  ```
- **Risk:** `asyncio.get_event_loop()` is deprecated in Python 3.10+ and raises `DeprecationWarning`. In Python 3.12 it can raise `RuntimeError` when called from inside a running coroutine with no explicit loop set. If the deployment target is Python 3.12, this causes a crash on every candle fetch.
- **Fix:** Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()` inside any `async` function. `get_running_loop()` is guaranteed to return the active loop and raises `RuntimeError` only when there is genuinely no running loop, making failures explicit.

---

## Medium Issues

### [MEDIUM] EXEC-009 — Wall-clock session filter mismatches stale candle timestamp

- **File:** `smc_bot/bot.py` (~line 363)
- **Evidence:**
  ```python
  price = float(df_5m['close'].iloc[-1])   # last closed candle, up to 5 min old
  ...
  h = datetime.now(timezone.utc).hour       # real-time wall clock
  in_london = 8 <= h <= 15
  in_ny     = 13 <= h <= 21
  ```
- **Risk:** The candle reflecting price may be 0–4 minutes old, while the session filter uses real wall-clock time. A candle from 07:56 UTC is evaluated at 08:00 UTC: the session filter passes (London open) but the candle is pre-session. The inverse occurs at session boundaries. Discrepancy is small but causes non-reproducible behavior between live and backtest (which uses candle timestamps throughout).
- **Fix:** Derive the session filter from `df_5m['ts'].iloc[-1]` (the candle's own timestamp) rather than `datetime.now(UTC)` for full consistency with backtesting. Document the decision either way.

---

### [MEDIUM] EXEC-010 — `choch_bar_idx` hardcoded to `len(df_5m) - 2` instead of actual CHoCH bar

- **File:** `smc_bot/bot.py` (~line 489)
- **Evidence:**
  ```python
  choch_bar_idx = len(df_5m) - 2
  owned_fvg = poi.get_owned_fvg(df_5m, bias, sweep_bar=sweep['bar_idx'], choch_bar=choch_bar_idx, ...)
  ```
- **Risk:** `get_choch()` returns only a boolean. The bar index is always assumed to be the second-to-last bar. If a CHoCH confirmed on an earlier bar and is re-confirmed on the current poll, the FVG search window `[sweep_bar, len-2]` includes bars post-dating the actual CHoCH, introducing a subtle lookahead bias in FVG selection. This would inflate backtest parity if the live path re-confirms stale CHoCHs.
- **Fix:** Extend `get_choch()` to return the index of the bar on which CHoCH first confirmed (e.g. return a dict `{confirmed: bool, bar_idx: int}`). Use that index as `choch_bar_idx`. Explicitly document and enforce that CHoCH only counts on the bar immediately following the sweep.

---

### [MEDIUM] EXEC-011 — Partial IOC fill causes immediate false TP1 trigger

- **File:** `smc_bot/executor.py` (~line 62)
- **Evidence:**
  ```python
  pos_size = float(pos.get('size', '0'))
  if pos_size < state.entry_qty * 0.75:
      executor.set_trading_stop(session, sym, sl=state.entry_price)
      state.tp1_filled = True
  ```
- **Risk:** IOC allows partial fills. If the order partially fills, `state.entry_qty` holds the full requested quantity, but `pos_size` reflects only the filled portion. On the very next cycle, `pos_size < entry_qty * 0.75` is immediately true (e.g. 50% fill triggers the 75% threshold), causing the bot to move the stop to breakeven and mark TP1 as filled — before any profit has been taken. This misrepresents trade state and moves the stop prematurely.
- **Fix:** After `place_order()`, immediately call `get_position()` and record the actual filled quantity as `state.entry_qty`. The TP1 threshold comparison is then relative to what was actually filled, not the pre-submission requested size.

---

### [MEDIUM] EXEC-012 — Dry-run `fill_price` uses pre-signal hint price, not simulated market price

- **File:** `brokers/metaapi.py` (~line 122)
- **Evidence:**
  ```python
  return OrderResult(success=True, order_id=f'DRY-{magic}-{symbol}',
                     fill_price=kwargs.get('entry_hint'), ...)
  ```
- **Risk:** In paper/dry-run mode, `fill_price` is set to the caller's pre-submission price hint, which was derived from the last closed candle close. Any downstream SL/TP or paper-trade statistics that consume `fill_price` use a price that is at minimum one candle old, understating slippage in paper results. Phase-1 paper-trade statistics will appear cleaner than live execution will be.
- **Fix:** Acceptable for paper mode but must be documented. Optionally, fetch current bid/ask at "fill" time to simulate realistic slippage. Add a comment in code clearly stating that paper fill prices are not slippage-adjusted.

---

## Low Issues

### [LOW] EXEC-013 — Losing trade can permanently bypass consecutive-loss counter when PnL unavailable

- **File:** `smc_bot/bot.py` (~line 279)
- **Evidence:**
  ```python
  if pnl is not None:
      if pnl < 0: state.consecutive_losses += 1
  else:
      log.warning('...leaving consecutive_losses unchanged')
  state.open_order_id = ''    # reset regardless of pnl availability
  state.entry_time    = ''    # cleared — get_last_closed_pnl can never match again
  ```
- **Risk:** When `get_last_closed_pnl` returns `None` (exchange has not yet indexed the close), the comment says "leaving counter unchanged" but `entry_time` is cleared anyway, making a retry on the next cycle impossible. A losing trade silently bypasses the consecutive-loss counter. Under a sequence of rapid closes, the halt guard is understated.
- **Fix:** Introduce a `pending_pnl_check` boolean field in `BotState`. When `pnl is None`, set the flag and retain `entry_time`. On subsequent cycles where `was_in_position=False` and `pending_pnl_check=True`, retry `get_last_closed_pnl` before clearing `entry_time`. Only clear `entry_time` and unset the flag once a PnL result (positive or negative) is obtained.

---

### [LOW] EXEC-014 — London session boundary includes hour 15 (15:00–15:59 UTC), borderline vs. convention

- **File:** `smc_bot/bot.py` (~line 376)
- **Evidence:**
  ```python
  in_london = 8 <= h <= 15   # includes 15:xx UTC
  in_ny     = 13 <= h <= 21
  ```
- **Risk:** Standard London close is 16:00–16:30 UTC. Including hour 15 (15:00–15:59) as London is technically valid but the boundary comment says "08-15," which is ambiguous. Combined with candles up to 5 minutes old, a candle from 15:50 UTC evaluated at 15:55 would pass the London filter even at the tail end of the overlap window. The kill zone label may be slightly misleading in edge cases.
- **Fix:** Low priority. If intention is to include 15:xx as London (overlap with NY), document this explicitly in the config comment. If intention is to close London at 16:00, change to `8 <= h < 16`. Align the code comment with the actual boundary.

---

## Safe Patterns Confirmed

The following execution safety patterns are correctly implemented and should be preserved in all future changes:

1. **`LIVE_TRADING` guard is layered and runtime-safe.** `executor._live()` reads the env var at call time (not startup), so runtime changes are honored. `MetaApiBroker` checks `self.live_trading` before every write call. `base.py._assert_live()` raises if `live_trading=False`. Three independent layers prevent accidental live writes.

2. **`SIGTERM`/`SIGINT` handler saves state before exiting.** Prevents orphaned `BotState` on process kill; the state file reflects the position that was open at shutdown.

3. **`BotState` has graceful field-migration.** Unknown fields from future schema versions are silently dropped on load, preventing crashes when the state file schema evolves across deployments.

4. **Signal dedup via `last_signal_ts`.** Correctly prevents duplicate execution within a single LTF candle boundary; a signal fires at most once per closed candle.

5. **Forming candle always dropped in `get_candles()`.** `df.iloc[:-1]` is applied before returning data. No lookahead from a live partial candle ever reaches the signal chain.

6. **API failure streak counter alerts without acting.** After 5 consecutive `get_balance()` failures, the bot alerts but does not place orders or modify state, avoiding action on stale data.

7. **TP1 reduce-only limit relies on Bybit auto-cancel.** When position reaches zero, the exchange auto-cancels the remaining limit. No manual cancellation race condition is possible.

8. **`positionIdx=0` consistently used across all executor calls.** One-way mode assumption is consistent throughout; no risk of hedged-mode position misidentification.

9. **All broker write calls validate `retCode` via `_assert_ok()`.** API-level errors raise exceptions rather than silently succeeding. No write operation proceeds on an error response.

10. **`MetaApiBroker.get_position()` uses `(symbol, magic)` composite key.** Prevents misidentifying positions across strategies sharing the same broker account.

---

## Overall Verdict

**NOT SAFE FOR LIVE TRADING.**

The bot has three critical failures:

- **EXEC-001** can open duplicate positions during any API blip, creating unbounded directional exposure with no cleanup mechanism.
- **EXEC-002** means the async runner never fetches candle data at all — the runner is silently broken at import time.
- **EXEC-003** means the consecutive-loss halt guard is permanently disabled, removing a key circuit breaker.

Additionally, EXEC-004 and EXEC-005 mean that every order's post-submission state is written before the fill is confirmed, and at the wrong price. EXEC-006 means the FVG retest condition documented in the signal spec is not actually enforced in the `strategies/` path. EXEC-007 introduces a balance-race that allows simultaneous orders to bypass all risk checks under asyncio concurrency.

**Minimum remediation before Phase-1 paper trade:**

Priority 1 — resolve EXEC-001, EXEC-002, EXEC-003 (bot is functionally broken without these).
Priority 2 — resolve EXEC-004, EXEC-005, EXEC-006, EXEC-007 (execution integrity and risk enforcement).
Priority 3 — resolve EXEC-008 through EXEC-012 (Python compatibility, edge-case accuracy, paper-trade fidelity).
Priority 4 — EXEC-013, EXEC-014 (low-risk counter integrity and boundary documentation).

After resolving Priority 1–2, re-run `scripts/backtest.py` to verify that the FVG gate fix (EXEC-006) does not materially alter the Phase-0 gate result. If net PF drops below 1.0, a new trial must be logged in `docs/VERDICT_LOG.md` before proceeding.
