"""Unit tests for bot/risk.py."""
import pytest

from bot.risk import calc_position_size, daily_loss_breached, drawdown_breached, trading_allowed


def test_position_size_basic():
    # $10k account, entry $50k, SL $49k → risk $500, stop $1k/BTC → qty 0.5 BTC
    qty = calc_position_size(10_000, 50_000, 49_000)
    assert abs(qty - 0.05) < 0.001   # 10k * 0.005 / 1000 = 0.05


def test_position_size_zero_on_invalid_sl():
    assert calc_position_size(10_000, 50_000, 50_000) == 0.0
    assert calc_position_size(10_000, 50_000, 51_000) == 0.0
    assert calc_position_size(10_000, 0, 49_000)      == 0.0


def test_daily_loss_not_breached():
    assert not daily_loss_breached(9_900, 10_000)   # -1% < 2% limit


def test_daily_loss_breached():
    assert daily_loss_breached(9_700, 10_000)   # -3% > 2% limit


def test_drawdown_not_breached():
    assert not drawdown_breached(9_500, 10_000)   # -5% < 10% limit


def test_drawdown_breached():
    assert drawdown_breached(8_900, 10_000)   # -11% > 10% limit


def test_trading_allowed_ok():
    ok, reason = trading_allowed(10_000, 10_000, 10_000)
    assert ok
    assert reason == "ok"


def test_trading_allowed_blocked_on_drawdown():
    ok, reason = trading_allowed(8_900, 10_000, 8_900)
    assert not ok
    assert "DRAWDOWN" in reason.upper()


def test_consecutive_losses_not_breached():
    from bot.risk import consecutive_losses_breached
    assert not consecutive_losses_breached(0)
    assert not consecutive_losses_breached(1)


def test_consecutive_losses_breached():
    from bot.risk import consecutive_losses_breached
    assert consecutive_losses_breached(2)
    assert consecutive_losses_breached(3)


def test_trading_allowed_blocked_on_consecutive_losses():
    ok, reason = trading_allowed(10_000, 10_000, 10_000, consecutive_losses=2)
    assert not ok
    assert "CONSECUTIVE" in reason.upper()
