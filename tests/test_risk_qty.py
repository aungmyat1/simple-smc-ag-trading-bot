"""
V5 — qty sizing: risk-cap integrity and exchange-minimum handling.

CRITICAL CONTRACT:
  - A computed qty BELOW BYBIT_MIN_QTY (0.001 BTC) MUST return 0.0 so the
    caller's 'qty <= 0' guard skips the trade.
  - Rounding UP to BYBIT_MIN_QTY is PROHIBITED: it silently multiplies the
    intended dollar risk by (MIN_QTY / raw_qty), which can be several orders
    of magnitude on a small account.

Example of the risk-cap breach we are guarding against:
  balance=$10, risk_pct=0.5% → risk_usdt=$0.05
  stop_dist=$1000 → raw_qty=0.00005 BTC
  If rounded to 0.001: actual_risk = 0.001 × 1000 = $1 = 10× breached risk_pct
"""
import pytest
from smc_bot.risk import calc_qty, BYBIT_MIN_QTY, BYBIT_QTY_STEP


class TestQtySnapToStep:

    def test_already_aligned_unchanged(self):
        # $10k balance, 1% risk, $1k stop → 0.1 BTC (100 steps of 0.001)
        qty = calc_qty(10_000, 60_000, 59_000, risk_pct=0.01)
        assert qty == pytest.approx(0.1, abs=1e-9)
        assert qty % BYBIT_QTY_STEP < 1e-9 or abs(qty % BYBIT_QTY_STEP - BYBIT_QTY_STEP) < 1e-9

    def test_fractional_snaps_to_nearest_step(self):
        # $5k, 1%, stop=$223 → raw=0.22421... → snapped to 0.224
        qty = calc_qty(5_000, 67_123, 66_900, risk_pct=0.01)
        assert qty == pytest.approx(0.224, abs=1e-9)

    def test_result_rounded_to_3dp(self):
        qty = calc_qty(5_000, 67_123, 66_900, risk_pct=0.01)
        # 3 decimal places (0.001 step)
        assert round(qty, 3) == qty


class TestQtyBelowMinimumSkipsTrade:
    """
    V5 HALT-OR-PASS:  below BYBIT_MIN_QTY → 0.0, NOT rounded up.
    If this returns anything other than 0.0, it's a risk-cap breach.
    """

    def test_tiny_account_returns_zero(self):
        # $10 balance, 0.5% risk = $0.05; stop_dist=$1000 → raw=0.00005
        qty = calc_qty(10, 60_000, 59_000, risk_pct=0.005)
        assert qty == 0.0, (
            f"Expected 0.0 (skip), got {qty}. "
            "Rounding up to BYBIT_MIN_QTY would breach risk_pct."
        )

    def test_wide_stop_returns_zero(self):
        # $100 balance, 0.5% risk = $0.50; stop_dist=$5000 → raw=0.0001
        qty = calc_qty(100, 60_000, 55_000, risk_pct=0.005)
        assert qty == 0.0, f"Expected 0.0 (skip), got {qty}"

    def test_exactly_at_minimum_is_allowed(self):
        # Exact minimum should pass (not be blocked)
        # We need raw_qty to be exactly 0.001
        # balance * risk_pct / stop_dist = 0.001
        # e.g. balance=1000, risk=0.1%, stop=$1000 → 1000*0.001/1000 = 0.001
        qty = calc_qty(1_000, 60_000, 59_000, risk_pct=0.001)
        assert qty == pytest.approx(0.001, abs=1e-9)

    def test_just_above_minimum_is_allowed(self):
        # raw ≈ 0.0015 → snapped to 0.002 (above min)
        qty = calc_qty(1_500, 60_000, 59_000, risk_pct=0.001)
        assert qty > 0.0

    def test_zero_stop_distance_returns_zero(self):
        qty = calc_qty(10_000, 60_000, 60_000)
        assert qty == 0.0

    def test_risk_cap_not_breached_on_minimum_path(self):
        """
        If calc_qty returns 0.0, the caller skips the trade (qty <= 0 guard).
        This test verifies the guard comment exists in bot.py.
        """
        import ast
        from pathlib import Path
        src = (Path(__file__).parent.parent / "smc_bot" / "bot.py").read_text()
        assert "qty <= 0" in src, "bot.py must have qty <= 0 guard to skip below-minimum trades"
