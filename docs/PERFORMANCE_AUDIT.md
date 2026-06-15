# Performance Audit — scripts/backtest.py (Trial 4)
# Date: 2026-06-15

---

## Profiling Method

```
python3 -m cProfile -o profile.out scripts/backtest.py --max-bars 5000
```

Sample: first 5 000 5M bars (2024-06-15 → 2024-07-02, ~17 days).

---

## TOP_RUNTIME_FUNCTIONS

| Rank | Function | File | Calls | Self-time (s) | Cum-time (s) |
|------|----------|------|-------|--------------|-------------|
| 1 | `numpy.ufunc.reduce` | numpy ufunc | 2 341 662 | 21.50 | 21.50 |
| 2 | `_swing_lows` | smc_bot/liquidity.py:28 | 581 | 12.11 | 45.21 |
| 3 | `numpy._wrapreduction` | numpy fromnumeric.py:66 | 2 339 324 | 9.46 | 32.18 |
| 4 | `numpy.fromnumeric.min` | numpy fromnumeric.py:3149 | 2 178 131 | 6.27 | 35.60 |
| 5 | `_swing_highs` | smc_bot/structure.py:16 | 715 | 0.90 | 4.50 |
| 6 | `numpy.fromnumeric.max` | numpy fromnumeric.py:3011 | 161 193 | 0.74 | 3.59 |
| 7 | `_swing_lows` | smc_bot/structure.py:28 | 715 | 0.66 | 4.16 |
| 8 | `_atr14` | smc_bot/poi.py:22 | 334 | 0.05 | 4.61 |
| 9 | `get_pois` | smc_bot/poi.py:36 | 334 | 0.04 | 5.09 |
| 10 | `get_sweep` | smc_bot/liquidity.py:36 | 581 | 0.05 | 45.66 |

**Total profiled runtime (5 000-bar sample): 67.5 seconds**

---

## O(N²) Pattern Analysis

### Pattern 1 — PRIMARY: `liquidity._swing_lows` on growing 5M slice

**File:** smc_bot/liquidity.py:28–33  
**Mechanism:**
```python
def get_sweep(df, bias, lookback=30, swing_n=3):
    n   = len(df)                              # grows: 37 → 5000 → 210240
    low = df["low"].values
    sl_idxs = _swing_lows(low, swing_n)        # ← scans ALL n bars

def _swing_lows(low, n):
    for i in range(n, len(low) - n):           # O(len(low)) = O(full_slice)
        if low[i] == np.min(low[i-n:i+n+1]):  # np.min = 7-element window
            result.append(i)
```

**Called as:** `liquidity.get_sweep(df_5m.iloc[:i+1], ...)` for each 5M bar `i` that passes bias+POI.

**Cost measured:**
- 5 000-bar sample: 581 calls × avg scan length ~2 500 bars = **1.45M inner loop iterations**
  - Self-time: 12.1 s | Cum-time: 45.2 s
- Full run (210 240 bars): ~16k calls × avg scan ~105k bars = **~1.68B iterations**
  - Extrapolated time: (16k/581) × (105k/2.5k) × 45.2s ≈ **4 800 s ≈ 80 min** for sweep alone

**Why it's O(N²):** `_swing_lows` scans O(i) bars per call, and there are O(N_passes) calls where N_passes grows with N. Total: O(N_passes × N_5M) ≈ O(N²).

**Wasted work:** the function computes ALL swing lows in the growing window, but `candidates = [j for j in sl_idxs if scan_start <= j < n-1]` uses only the LAST 30 bars (lookback=30). The entire scan of bars 0..i-31 is thrown away every call.

---

### Pattern 2 — SECONDARY: `structure._swing_highs/_swing_lows` on growing 1H slice

**File:** smc_bot/structure.py:16–34  
**Mechanism:** Same pattern — scan the full growing 1H window at each unique `htf_idx`.

**Called as:** `structure.get_bias(df_1h.iloc[:htf_idx+1], ...)` for each unique 1H bar index.

**Cost measured:**
- 5 000-bar sample: 715 calls (418 unique 1H bars reached) × avg 1H scan ~200 bars
  - Cum-time: 4.5s + 4.2s = 8.7 s
- Full run (17 520 unique 1H bars): 17 520 calls × avg scan ~8 760 bars
  - Extrapolated: (17520/715)² × 8.7s ≈ **4 800 s ≈ 80 min** for bias alone

**Why it's O(N²):** Σk from 50 to 17520 × O(k) = O(N_1H² / 2).

Note: the existing `_htf_cache` does eliminate duplicate calls per 1H bar, but does NOT fix the O(k) cost of each unique call.

---

### Pattern 3 — TERTIARY: `poi._atr14` EWM on growing 1H slice

**File:** smc_bot/poi.py:22–33  
**Mechanism:** `tr.ewm(span=14, adjust=False).mean()` computes EWM over the FULL 1H slice — O(htf_idx) per call.

**Cost measured:**
- 334 calls, cum-time: 4.6 s (5 000-bar sample, small 1H window)
- Full run: 17 520 × O(htf_idx) → same O(N_1H²) class

---

## Complexity Summary

| Component | Current | Cause | Calls (full) | Extrapolated (min) |
|-----------|---------|-------|-------------|-------------------|
| `liquidity._swing_lows` | O(N_5M × N_pass) | Full-slice scan per bar | ~16k | ~80 |
| `structure.get_bias` | O(N_1H²) | Growing-window per unique 1H bar | ~17.5k | ~80 |
| `poi._atr14` | O(N_1H²) | EWM over full growing 1H slice | ~5.9k | ~20 |
| **Total (run_backtest + count_funnel)** | | | | **~180+ min** |

**Confirmed**: 5000-bar → 67.5s. Full 210k bars extrapolated to **≫5 hours**. Killed at 44 min — only ~7% of bars completed.

---

## What Does NOT Need Optimization

- `count_funnel()` — same hot paths; fixed by same changes
- `poi.get_pois()` OB/FVG scan — O(ob_lookback=50) per unique htf_idx; already bounded
- `confirmation.get_choch()` — O(lookback=10) per call; already fast
- `poi.price_in_poi()` — O(n_zones) per bar; n_zones ≤ 10 typically; already fast
- `_scan_exit()` — O(bars_to_exit) per trade; ≤ 500 calls total; already fast
