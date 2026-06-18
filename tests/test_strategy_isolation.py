"""
Test that SMC_SNIPER and SESSION_TRADER risk states are fully isolated.
A loss (or kill) in one strategy must not affect the other.
"""
import pytest
from risk.manager import RiskManager


def make_rm() -> RiskManager:
    return RiskManager({
        "SMC_SNIPER":     {"max_daily_loss_pct": 0.02, "max_drawdown_pct": 0.10,
                           "max_consec_losses": 5, "risk_per_trade_pct": 0.005},
        "SESSION_TRADER": {"max_daily_loss_pct": 0.02, "max_drawdown_pct": 0.10,
                           "max_consec_losses": 5, "risk_per_trade_pct": 0.005},
    })


# ── daily loss isolation ──────────────────────────────────────────────────────

def test_daily_loss_halts_only_affected_strategy():
    rm = make_rm()
    rm.update_balance("SMC_SNIPER", 1000.0)
    rm.update_balance("SESSION_TRADER", 1000.0)

    # Simulate SMC_SNIPER equity dropping 3% (exceeds 2% limit)
    rm.update_balance("SMC_SNIPER", 970.0)

    assert not rm.trading_allowed("SMC_SNIPER"), "SMC_SNIPER should be halted"
    assert rm.trading_allowed("SESSION_TRADER"),  "SESSION_TRADER must be unaffected"


def test_drawdown_kill_switch_isolated():
    rm = make_rm()
    rm.update_balance("SMC_SNIPER", 1000.0)
    rm.update_balance("SESSION_TRADER", 500.0)

    # SMC_SNIPER loses 12% → kill switch
    rm.update_balance("SMC_SNIPER", 880.0)

    assert not rm.trading_allowed("SMC_SNIPER"), "SMC_SNIPER should be killed"
    assert rm.trading_allowed("SESSION_TRADER"),  "SESSION_TRADER must be unaffected"


def test_consec_loss_halts_only_affected_strategy():
    rm = make_rm()
    rm.update_balance("SMC_SNIPER", 1000.0)
    rm.update_balance("SESSION_TRADER", 1000.0)

    for _ in range(5):
        rm.record_trade("SMC_SNIPER", pnl=-10.0)

    assert not rm.trading_allowed("SMC_SNIPER"),  "SMC_SNIPER should be halted (5 consec losses)"
    assert rm.trading_allowed("SESSION_TRADER"),   "SESSION_TRADER must be unaffected"


# ── win resets consecutive counter ───────────────────────────────────────────

def test_win_resets_consec_counter():
    rm = make_rm()
    rm.update_balance("SMC_SNIPER", 1000.0)
    for _ in range(4):
        rm.record_trade("SMC_SNIPER", pnl=-10.0)
    rm.record_trade("SMC_SNIPER", pnl=+20.0)

    assert rm.trading_allowed("SMC_SNIPER"), "Win should reset consec counter"
    state = rm.get_state("SMC_SNIPER")
    assert state["consec_losses"] == 0


# ── lot sizing ────────────────────────────────────────────────────────────────

def test_calc_qty_basic():
    rm = make_rm()
    rm.update_balance("SMC_SNIPER", 10_000.0)
    # 0.5% of 10000 = $50 risk
    # sl_distance=0.0010 (10 pips), pip_value=10 → 10pips × $10 = $100/lot
    # lots = 50 / 100 = 0.50
    qty = rm.calc_qty("SMC_SNIPER", balance=10_000.0, sl_distance=0.0010,
                      pip_value=10.0, pip_size=0.0001)
    assert qty == pytest.approx(0.5, abs=0.01)


def test_calc_qty_clamped_to_min():
    rm = make_rm()
    rm.update_balance("SMC_SNIPER", 100.0)
    qty = rm.calc_qty("SMC_SNIPER", balance=100.0, sl_distance=0.0100,
                      pip_value=10.0, pip_size=0.0001)
    assert qty >= 0.01


# ── magic numbers ─────────────────────────────────────────────────────────────

def test_magic_numbers_unique():
    from strategies.smc_sniper import SMCSniper
    from strategies.session_trader import SessionTrader

    smc = SMCSniper()
    ses = SessionTrader()
    symbols = ["EURUSD", "GBPUSD"]

    all_magic = [smc.magic_number(s) for s in symbols] + [ses.magic_number(s) for s in symbols]
    assert len(all_magic) == len(set(all_magic)), "All magic numbers must be unique"
    assert smc.magic_number("EURUSD") == 11001
    assert smc.magic_number("GBPUSD") == 11002
    assert ses.magic_number("EURUSD") == 12001
    assert ses.magic_number("GBPUSD") == 12002
