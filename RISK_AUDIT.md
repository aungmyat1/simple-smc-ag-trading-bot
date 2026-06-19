# RISK MANAGEMENT AUDIT

**Project:** simple-smc-ag-trading-bot  
**Audit date:** 2026-06-18  
**Auditor:** Senior Quant Auditor (automated scan)  
**Scope:** All active code paths — `smc_bot/risk.py`, `smc_bot/bot.py`, `risk/manager.py`, `runner.py`, `smc_bot/config.yaml`, `strategies/config.yaml`  
**Risk grade:** **B**

---

## Risk Controls Table

| Control | Status | Configured Value | Code Enforced | Notes |
|---|---|---|---|---|
| Risk per trade | EXISTS | 0.5% of balance (`risk_pct`) or $100 fixed (`risk_usd`) — `smc_bot/config.yaml`; 0.5% in `strategies/config.yaml limits.risk_per_trade_pct` | YES | Both code paths implement `calc_qty`; `risk_usd` takes priority over `risk_pct` in `smc_bot/risk.py` |
| Daily loss limit | EXISTS | 2% of day-start equity (`max_daily_loss: 0.02`) in both config files | YES | `daily_loss_breached()` in `smc_bot/risk.py`; `halted_today` flag in `risk/manager.py`; resets at UTC midnight in both |
| Weekly loss limit | MISSING | Not defined in any config or code path | NO | No weekly aggregation logic exists anywhere in the codebase |
| Max drawdown / kill switch | EXISTS (PARTIAL) | 10% from peak equity (`max_drawdown: 0.10`) in both config files | YES | `risk/manager.py` sets `killed=True` permanently (survives daily reset, but NOT process restart — in-memory only). `smc_bot/risk.py` re-evaluates `drawdown_breached()` each cycle from `BotState.peak_equity` — no permanent killed flag; state survives restarts via `smc_bot_state.json` but can be corrupted or deleted |
| Max consecutive losses | EXISTS (INCONSISTENT) | **2** in `smc_bot/config.yaml` (`max_consecutive_losses: 2`); **5** in `risk/manager.py` defaults and `strategies/config.yaml limits.max_consec_losses` | YES | The two parallel code paths apply materially different thresholds with no reconciliation. `smc_bot/bot.py` uses 2; `runner.py` uses 5. |
| Max concurrent trades | PARTIAL | No explicit config parameter; structurally enforced in `smc_bot/bot.py` by returning early when `in_position=True` | YES (implicit) | `runner.py` (multi-strategy) allows one trade per strategy simultaneously — SMC_SNIPER + SESSION_TRADER can each hold a position on EURUSD and GBPUSD, for up to 4 concurrent open trades. No cross-strategy aggregate exposure cap. |
| Absolute equity floor | MISSING | Not defined — no minimum dollar balance below which all trading halts | NO | Only percentage-based drawdown guards exist. A small starting balance with a large `peak_equity` reference could allow the account to drain near zero before the percentage threshold fires. |
| Position sizing | EXISTS | `smc_bot/risk.py calc_qty`: `risk_usd / stop_dist` or `(balance × risk_pct) / stop_dist`, snapped to `BYBIT_QTY_STEP`; returns `0.0` if below `BYBIT_MIN_QTY`. `risk/manager.py calc_qty`: pip-based lot sizing with `min_lot`/`max_lot` clamps | YES | Both used in their respective runners; never rounds up to minimum (correct — avoids silent over-risk) |
| Leverage cap | EXISTS | `LEVERAGE = 1.0` documented in CLAUDE.md §5; `bybit.demo: true` and no leverage multiplier in config | YES (by omission) | No explicit leverage parameter is set in the Bybit API call — defaults to 1× for USDT perpetuals in cross-margin. Should be asserted at bot startup. |
| Daily halt reset | EXISTS | Resets at UTC midnight via `BotState.update_day_start()` (`smc_bot/bot.py`) and `last_reset_date` comparison in `RiskManager.update_balance()` | YES | Consistent across both paths |
| API failure protection | EXISTS | `_api_fail_streak` counter in `smc_bot/bot.py`; Telegram alert after 5 consecutive failures | YES | Only in `smc_bot/bot.py`; `runner.py` has no equivalent streak guard |
| Telegram alert on guard halt | EXISTS | Sends alert when any guard fires in `smc_bot/bot.py` | YES | `runner.py` logs only to file; no Telegram alert on guard halt |

---

## Missing Controls

### 1. Weekly loss limit
No weekly aggregate PnL tracking exists in any code path. A strategy that loses 1.9% every day (just under the daily 2% cap) can sustain a −9.5% weekly drawdown across 5 trading days before the total drawdown kill switch fires. A weekly limit of 4–5% would catch this pattern earlier.

### 2. Absolute equity floor
All guards are percentage-based relative to `peak_equity` or `day_start_equity`. If `peak_equity` is set high (e.g. from a prior high-water mark) and the account has since declined significantly, the 10% drawdown threshold could permit continued trading at a much smaller remaining balance. A hard floor (e.g. halt if `balance < $X`) is absent.

### 3. Cross-strategy aggregate exposure cap
`runner.py` creates fully isolated `RiskManager` states per strategy. SMC_SNIPER and SESSION_TRADER can each hold positions simultaneously on both EURUSD and GBPUSD. The combined real-money risk exposure is not bounded by any aggregate guard — two simultaneous 0.5%-risk trades = 1.0% total at-risk, which doubles the effective per-cycle risk without any config-level gate.

### 4. Leverage assertion at startup
No code asserts that Bybit leverage is set to 1× before any trade. A manual or prior API session could leave the account at a higher leverage multiplier, silently multiplying all risk calculations.

---

## Risk Gaps

### Gap 1 — Kill switch is not fully persistent in `smc_bot/bot.py`
`risk/manager.py` sets `killed=True` as an in-memory flag. This flag is lost on process restart — if the bot crashes and restarts after a drawdown breach, the kill switch is not re-applied until `drawdown_breached()` fires again on the next cycle. `smc_bot/bot.py` re-evaluates the drawdown condition from `BotState.peak_equity` each cycle, which is more durable, but is still vulnerable to state file deletion or corruption. Neither path writes a dedicated `killed=True` flag to disk that survives restarts.

**Severity:** HIGH — a process restart during or immediately after a drawdown event could allow further trading before the guard re-engages.

### Gap 2 — Inconsistent `max_consec_losses` across the two code paths
`smc_bot/config.yaml` sets `max_consecutive_losses: 2`, while `strategies/config.yaml` and `risk/manager.py` defaults set `max_consec_losses: 5`. These are parallel systems with no shared state. A user monitoring the system expects one consistent behaviour; they get different halt thresholds depending on which runner is active.

**Severity:** MEDIUM — the two code paths are currently used for different assets (Bybit BTC vs MetaAPI forex), but the divergence is undocumented and creates confusion about actual risk behaviour.

### Gap 3 — `risk/manager.py` (RiskManager) is entirely disconnected from `smc_bot/bot.py`
`smc_bot/bot.py` uses only `smc_bot/risk.py` pure functions. `runner.py` uses only `risk/manager.py`. The two systems do not share state, configuration, or alert channels. Any improvement to one does not propagate to the other. This also means risk monitoring dashboards or audits that inspect only one path will miss the other.

**Severity:** MEDIUM — architectural debt. Not immediately dangerous given the different assets, but will become a serious issue if both systems are ever used on the same asset.

### Gap 4 — `runner.py` has no API failure streak guard or Telegram alert on guard halt
`smc_bot/bot.py` has `_api_fail_streak` and sends a Telegram alert when 5 consecutive API calls fail. `runner.py` has neither. A broker connection failure in the multi-strategy runner results only in a file log entry; no operator alert is triggered.

**Severity:** LOW-MEDIUM — silent failure mode in the multi-strategy runner.

### Gap 5 — `max_concurrent_trades` has no config knob
The single-position enforcement in `smc_bot/bot.py` is implicit: the cycle returns early if `in_position=True`. There is no config parameter that expresses this intent. A future code change or a bug in `executor.get_position()` (e.g. returning `None` on an API error instead of the actual position) could silently allow multiple concurrent entries without any config-level gate to catch it.

**Severity:** LOW — currently structurally enforced, but brittle.

### Gap 6 — Sustained multi-day losses below the daily cap
A daily loss limit of 2% with a 10% total drawdown limit means the bot can theoretically lose 1.9%/day for 5 consecutive days (−9.5% total) before either limit fires. There is no weekly loss limit and no mechanism to tighten the daily limit after a sequence of losing days.

**Severity:** LOW-MEDIUM — acceptable for a phase-1 paper-trade bot, but requires a weekly limit before Phase 2 (micro live).

---

## Recommendations

| Priority | Recommendation | Effort |
|---|---|---|
| HIGH | Persist the kill switch as a field in `BotState` (e.g. `killed: bool = False`). Once set `True` by a drawdown breach, the bot must not re-enter any trade on restart until an operator explicitly resets the flag. | Low — one field + one check in `run_cycle` |
| HIGH | Unify `max_consecutive_losses` to a single source of truth. Either synchronise both config files to the same value, or add a comment to both explicitly documenting the intentional divergence and why. | Low — config change + comment |
| MEDIUM | Add a weekly loss limit (e.g. 5%) by tracking `week_start_equity` in `BotState` with an ISO week-number reset, mirroring the existing daily pattern. | Low-medium — one new guard function |
| MEDIUM | Add an absolute equity floor parameter (e.g. `min_equity_usd: 200`) to both configs. Guard: if `balance < min_equity_usd`, halt all trading regardless of percentage thresholds. | Low — one config param + one guard |
| MEDIUM | Assert Bybit leverage at bot startup: call `session.set_leverage(symbol=sym, buyLeverage="1", sellLeverage="1")` once on startup, log the confirmed value, and alert if the call fails. | Low |
| MEDIUM | Add a Telegram alert in `runner.py` when any risk guard fires (mirroring `smc_bot/bot.py` behaviour). Add an API failure streak counter to the runner loop. | Low-medium |
| LOW | Add an explicit `max_concurrent_trades: 1` config parameter to `smc_bot/config.yaml` and enforce it by assertion at the top of `run_cycle`, not just by the `in_position` early return. | Low |
| LOW | Add a cross-strategy aggregate risk cap to `runner.py` (e.g. combined open risk across all strategies must not exceed 1.0% of total balance). This requires summing position risk across all active strategies before placing a new order. | Medium |

---

## Risk Grade

**B** — Core per-trade, daily, and total-drawdown controls are present and code-enforced in both active code paths. The system will halt correctly on the most damaging single-event scenarios (large drawdown, daily blowout). The grade is not higher due to:

1. The kill switch not surviving a process restart in `risk/manager.py`
2. The inconsistent consecutive-loss thresholds (2 vs 5) across the two code paths
3. The absence of a weekly loss limit and absolute equity floor
4. No cross-strategy aggregate exposure cap in the multi-strategy runner
5. Silent failure mode (no Telegram alert) for risk guard events in `runner.py`

These gaps are acceptable for Phase-0 and Phase-1 (paper trade) operation. Before advancing to Phase-2 (micro live with real capital), items marked HIGH and MEDIUM priority above must be resolved.
