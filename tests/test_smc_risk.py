"""
Tests for smc_bot/risk.py — capital-protection guards.

These verify the hard stops that prevent ruinous losses.
A broken guard is more dangerous than a missing signal filter.
"""
import pytest

from smc_bot.risk import (
    calc_qty,
    consecutive_losses_breached,
    daily_loss_breached,
    drawdown_breached,
    trading_allowed,
)


# ── calc_qty ──────────────────────────────────────────────────────────────────

class TestCalcQty:
    def test_basic(self):
        # $10k balance, 1% risk = $100 risk; $1k stop distance → 0.1 BTC
        qty = calc_qty(balance=10_000, entry=60_000, sl=59_000, risk_pct=0.01)
        assert abs(qty - 0.1) < 0.001

    def test_zero_on_zero_stop_distance(self):
        assert calc_qty(10_000, 60_000, 60_000) == 0.0

    def test_works_for_short(self):
        # Short: entry < sl (wick_extreme above entry)
        qty = calc_qty(balance=10_000, entry=60_000, sl=61_000, risk_pct=0.01)
        assert abs(qty - 0.1) < 0.001

    def test_rounds_to_4dp(self):
        qty = calc_qty(5_000, 67_123, 66_900, risk_pct=0.01)
        assert len(str(qty).split(".")[-1]) <= 4


# ── daily_loss_breached ───────────────────────────────────────────────────────

class TestDailyLossBreached:
    def test_not_breached_within_limit(self):
        # -1% loss, 2% limit
        assert not daily_loss_breached(9_900, 10_000, max_daily_loss=0.02)

    def test_breached_over_limit(self):
        # -3% loss, 2% limit
        assert daily_loss_breached(9_700, 10_000, max_daily_loss=0.02)

    def test_exactly_at_limit_not_breached(self):
        # exactly -2% — NOT breached (strict <)
        assert not daily_loss_breached(9_800, 10_000, max_daily_loss=0.02)

    def test_zero_day_start_safe(self):
        assert not daily_loss_breached(0, 0, max_daily_loss=0.02)

    def test_gain_never_breaches(self):
        assert not daily_loss_breached(11_000, 10_000, max_daily_loss=0.02)


# ── drawdown_breached ─────────────────────────────────────────────────────────

class TestDrawdownBreached:
    def test_not_breached_within_limit(self):
        assert not drawdown_breached(9_500, 10_000, max_drawdown=0.10)

    def test_breached_over_limit(self):
        # -11% from peak, 10% limit
        assert drawdown_breached(8_900, 10_000, max_drawdown=0.10)

    def test_exactly_at_limit_not_breached(self):
        assert not drawdown_breached(9_000, 10_000, max_drawdown=0.10)

    def test_zero_peak_safe(self):
        assert not drawdown_breached(0, 0, max_drawdown=0.10)

    def test_new_high_never_breaches(self):
        assert not drawdown_breached(11_000, 10_000, max_drawdown=0.10)


# ── consecutive_losses_breached ───────────────────────────────────────────────

class TestConsecutiveLossesBreached:
    def test_zero_not_breached(self):
        assert not consecutive_losses_breached(0, max_consecutive_losses=2)

    def test_one_not_breached(self):
        assert not consecutive_losses_breached(1, max_consecutive_losses=2)

    def test_at_limit_breached(self):
        assert consecutive_losses_breached(2, max_consecutive_losses=2)

    def test_over_limit_breached(self):
        assert consecutive_losses_breached(5, max_consecutive_losses=2)


# ── trading_allowed (combined guard) ──────────────────────────────────────────

class TestTradingAllowed:
    _KWARGS = dict(
        max_daily_loss=0.02,
        max_drawdown=0.10,
        max_consecutive_losses=2,
    )

    def _call(self, **overrides):
        defaults = dict(
            equity=10_000,
            peak_equity=10_000,
            day_start_equity=10_000,
            consecutive_losses=0,
        )
        defaults.update(overrides)
        return trading_allowed(**defaults, **self._KWARGS)

    def test_all_clean(self):
        ok, reason = self._call()
        assert ok
        assert reason == "ok"

    def test_blocks_on_drawdown(self):
        ok, reason = self._call(equity=8_900, peak_equity=10_000)
        assert not ok
        assert "DRAWDOWN" in reason.upper()

    def test_blocks_on_daily_loss(self):
        ok, reason = self._call(equity=9_700, day_start_equity=10_000)
        assert not ok
        assert "DAILY" in reason.upper()

    def test_blocks_on_consecutive_losses(self):
        ok, reason = self._call(consecutive_losses=2)
        assert not ok
        assert "CONSECUTIVE" in reason.upper()

    def test_drawdown_takes_priority_over_daily_loss(self):
        # Both breached — drawdown guard fires first
        ok, reason = self._call(equity=8_000, peak_equity=10_000, day_start_equity=10_000)
        assert not ok
        assert "DRAWDOWN" in reason.upper()

    def test_no_false_halt_on_slight_decline(self):
        ok, _ = self._call(equity=9_980, day_start_equity=10_000)
        assert ok
