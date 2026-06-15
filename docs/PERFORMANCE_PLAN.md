# Performance Optimization Plan — scripts/backtest.py
# Date: 2026-06-15 | Status: APPROVED FOR IMPLEMENTATION

---

## Target

Runtime: **< 5 minutes** on the full 2-year holdout (210 240 5M bars, 17 520 1H bars).
Current: **> 3 hours** (killed at 44 min, ~7% complete).

---

## Constraints

- Signal logic: **ZERO CHANGE**. The same trades must fire at the same bars.
  - Verification: run `--max-bars 5000` before and after; assert identical trade list.
- Scope: `scripts/backtest.py` only. Do NOT modify `smc_bot/` modules.
- The `smc_bot/` modules remain the live-bot implementation; the backtest gets fast inline versions.

---

## Root Cause Recap (from PERFORMANCE_AUDIT.md)

All three bottlenecks share the same structural defect: **repeatedly scanning a growing
slice when only a small tail window is ever used.**

| Bottleneck | Wasted work | Fix |
|-----------|------------|-----|
| `liquidity._swing_lows(df_5m.iloc[:i+1])` | Scans 0..i but uses only last 30 bars | Precompute all swing lows once → bisect lookup |
| `structure._swing_highs/lows(df_1h.iloc[:k+1])` | Scans 0..k at each unique k | Precompute all swing highs/lows once → bisect lookup |
| `poi._atr14(df_1h.iloc[:k+1])` | EWM over full growing window | Precompute ATR14 series once → index into array |

---

## Implementation Plan

All changes are in `scripts/backtest.py`. The four `smc_bot/` modules are read-only.

### Step 1 — Add imports

```python
import bisect
```

### Step 2 — Precompute helpers (module-level, called once per `main()` invocation)

```python
def _swing_lows_np(low: np.ndarray, n: int) -> list[int]:
    """Same logic as structure._swing_lows / liquidity._swing_lows — numpy arrays only."""
    result = []
    for i in range(n, len(low) - n):
        if low[i] == low[i - n : i + n + 1].min():
            result.append(i)
    return result

def _swing_highs_np(high: np.ndarray, n: int) -> list[int]:
    result = []
    for i in range(n, len(high) - n):
        if high[i] == high[i - n : i + n + 1].max():
            result.append(i)
    return result

def _atr14_series(df: pd.DataFrame) -> np.ndarray:
    """ATR14 (Wilder EWM) for every bar — shape (N,)."""
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"]  - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=14, adjust=False).mean().values
```

### Step 3 — Precomputed global arrays

Populated by `_precompute(df_1h, df_5m)` once, used everywhere:

```python
# 1H arrays
_H_1H: np.ndarray   # high
_L_1H: np.ndarray   # low
_O_1H: np.ndarray   # open
_C_1H: np.ndarray   # close
_SH_1H: list[int]   # swing high indices (sorted asc)
_SL_1H: list[int]   # swing low  indices (sorted asc)
_ATR14_1H: np.ndarray  # ATR14 for every 1H bar

# 5M arrays
_H_5M: np.ndarray
_L_5M: np.ndarray
_C_5M: np.ndarray
_O_5M: np.ndarray
_SL_5M: np.ndarray  # swing low indices (sorted, dtype int) — bisect target
```

### Step 4 — Fast signal functions (O(1) to O(lookback) each)

#### 4a. `_fast_bias(htf_idx) → str`

Replaces `structure.get_bias(df_1h.iloc[:htf_idx+1], swing_n=SWING_N)`.

```python
def _fast_bias(htf_idx: int) -> str:
    max_conf = htf_idx - SWING_N           # confirmed swings need SWING_N right bars
    sh_end   = bisect.bisect_right(_SH_1H, max_conf)
    sl_end   = bisect.bisect_right(_SL_1H, max_conf)
    if sh_end < 2 or sl_end < 2:
        return "neutral"
    hh = _H_1H[_SH_1H[sh_end - 1]] > _H_1H[_SH_1H[sh_end - 2]]
    hl = _L_1H[_SL_1H[sl_end - 1]] > _L_1H[_SL_1H[sl_end - 2]]
    lh = _H_1H[_SH_1H[sh_end - 1]] < _H_1H[_SH_1H[sh_end - 2]]
    ll = _L_1H[_SL_1H[sl_end - 1]] < _L_1H[_SL_1H[sl_end - 2]]
    if hh and hl: return "bullish"
    if lh and ll: return "bearish"
    return "neutral"
```

Complexity: **O(log N_SH)** per call (bisect). N_SH ≈ 1 000 → ~10 comparisons.

#### 4b. `_fast_pois(htf_idx, bias) → list[dict]`

Replaces `poi.get_pois(df_1h.iloc[:htf_idx+1], bias, ...)`.

Same OB + FVG logic but reads from precomputed arrays and uses `_ATR14_1H[htf_idx]`
instead of recomputing the full EWM.

Complexity: **O(OB_LB + FVG_LB)** = **O(80)** per call. Constant.

#### 4c. `_fast_sweep(i) → dict | None`

Replaces `liquidity.get_sweep(df_5m.iloc[:i+1], bias, LIQ_LB, LIQ_SN)`.

```python
def _fast_sweep(i: int) -> dict | None:
    n          = i + 1                                 # matches original n = len(df)
    scan_start = max(LIQ_SN * 2 + 1, n - LIQ_LB)     # matches original
    max_conf   = i - LIQ_SN                            # confirmed swing: need LIQ_SN right bars
    if max_conf < scan_start:
        return None
    left  = bisect.bisect_left(_SL_5M, scan_start)
    right = bisect.bisect_right(_SL_5M, max_conf)
    for sl_idx in _SL_5M[left:right][::-1]:           # most-recent first (matches original)
        level = _L_5M[sl_idx]
        for k in range(sl_idx + 1, n):                # matches original range(sl_idx+1, n)
            if _L_5M[k] < level and _C_5M[k] > level:
                return {
                    "bar_idx":      int(k),
                    "swept_level":  float(level),
                    "wick_extreme": float(_L_5M[k]),
                }
    return None
```

Complexity: **O(n_candidates × lookback)** per call, where n_candidates ≤ ~5 swing lows
in any 30-bar window. Worst case: **O(5 × 30) = O(150)**. Constant.

#### 4d. `_fast_choch(sweep, i) → bool`

Replaces `confirmation.get_choch(df_5m.iloc[:i+1], "bullish", sweep, CHOCH_LB)`.

```python
def _fast_choch(sweep: dict, i: int) -> bool:
    sweep_bar = sweep["bar_idx"]
    if sweep_bar >= i:                     # matches: sweep_bar >= n - 1
        return False
    ref_start = max(0, sweep_bar - CHOCH_LB)
    ref_level = float(_H_5M[ref_start : sweep_bar + 1].max())
    return bool(_C_5M[i] > ref_level)
```

Complexity: **O(CHOCH_LB) = O(10)**. Already fast; included for consistency.

### Step 5 — Replace slow calls in `run_backtest()` and `count_funnel()`

| Old call | New call |
|----------|---------|
| `structure.get_bias(df_1h_w, swing_n=SWING_N)` | `_fast_bias(htf_idx)` |
| `poi.get_pois(df_1h_w, bias_val, OB_LB, FVG_LB, DISP_ATR)` | `_fast_pois(htf_idx, bias_val)` |
| `df_5m_w = df_5m.iloc[:i+1]` | (remove — no longer needed) |
| `liquidity.get_sweep(df_5m_w, bias, LIQ_LB, LIQ_SN)` | `_fast_sweep(i)` |
| `confirmation.get_choch(df_5m_w, bias, sweep, CHOCH_LB)` | `_fast_choch(sweep, i)` |
| `float(df_5m_w["close"].iloc[-1])` | `float(_C_5M[i])` |

The `_htf_cache` dict remains — it avoids recomputing `_fast_pois` for the same 1H bar,
further reducing duplicate work.

### Step 6 — Call `_precompute()` from `main()`

```python
print("Precomputing 1H swings + ATR14 …", flush=True)
print("Precomputing 5M swing lows …", flush=True)
_precompute(df_1h, df_5m)
```

Expected precompute time: **< 30 seconds** (single pass each over 17.5k and 210k bars).

---

## Signal Identity Guarantee

The fast functions are mathematically equivalent to the slow ones because:

1. **Swing confirmation right-side rule is preserved.** The slow code uses a growing window
   `df.iloc[:i+1]` and calls `_swing_lows(low[:i+1], n)` which iterates
   `range(n, len(low)-n)` = `range(n, i+1-n)` — this excludes bars at indices `> i-n`.
   The fast code uses `bisect_right(_SL_5M, i - LIQ_SN)` to get the same cutoff.

2. **Historical bar values are fixed.** `_swing_lows(arr, n)` at bar j depends only on
   `arr[j-n:j+n+1]` — values that don't change once printed. Precomputing from the full
   array and then filtering `j ≤ i - n` gives the same result.

3. **Scan window boundaries match.** `scan_start = max(LIQ_SN*2+1, n-LIQ_LB)` where
   `n = i+1` is reproduced exactly in `_fast_sweep`.

4. **ATR14 EWM is identical.** Precomputing `tr.ewm(span=14).mean()` for all bars at once
   gives the same scalar at index `htf_idx` as computing it on the slice `[:htf_idx+1]`
   (the EWM depends only on prior values).

**Parity test:** run `--max-bars 5000` before and after optimization; compare the trade list
CSV. Expected: identical entries (same entry/exit bars, same prices, same R values).

---

## Estimated Post-Optimization Runtime

| Component | Before | After |
|-----------|--------|-------|
| Precompute 1H swings | — | ~1 s (17.5k bars × 10 ops) |
| Precompute ATR14 1H | — | ~0.1 s (pandas EWM once) |
| Precompute 5M swing lows | — | ~5 s (210k bars × 7 ops) |
| `run_backtest()` main loop | ~80+ min | ~5 s |
| `count_funnel()` main loop | ~80+ min | ~5 s |
| **Total** | **>3 hours** | **< 30 seconds** |

Conservative estimate with Python loop overhead: **< 3 minutes** for the full run.
Stretch goal (with numpy vectorization of `_swing_lows_np`): **< 30 seconds**.

---

## What Is NOT Changing

- Strategy parameters (swing_n, ob_lookback, lookback, target_r, sl_buffer)
- Signal logic (HH+HL bias, OB/FVG zones, liquidity sweep, CHoCH)
- Exit model (single 2R TP, SL = wick × (1 - sl_buffer))
- Fee model (0.0006 taker × 2)
- Phase-0 gate thresholds (n ≥ 50, net PF > 1.0)
- `smc_bot/` modules (read-only)
- Trial numbering (this optimization re-runs Trial 4; no new trial row needed if signals identical)
