"""
Per-strategy independent risk manager.

Each strategy (SMC_SNIPER, SESSION_TRADER) has its own:
  - daily PnL counter  (resets at UTC midnight)
  - drawdown tracker   (peak equity → current equity)
  - consecutive loss counter

Loss in one strategy never disables the other.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_DEFAULT_LIMITS: dict = {
    "max_daily_loss_pct":   0.02,
    "max_drawdown_pct":     0.10,
    "max_consec_losses":    5,
    "risk_per_trade_pct":   0.005,
}


@dataclass
class _StrategyState:
    peak_equity:        float = 0.0
    current_equity:     float = 0.0
    daily_start_equity: float = 0.0
    daily_pnl:          float = 0.0
    consec_losses:      int   = 0
    halted_today:       bool  = False
    killed:             bool  = False
    last_reset_date:    str   = ""
    limits:             dict  = field(default_factory=lambda: dict(_DEFAULT_LIMITS))


class RiskManager:
    """Independent risk state per registered strategy name."""

    def __init__(self, strategy_configs: dict[str, dict] | None = None) -> None:
        self._states: dict[str, _StrategyState] = {}
        if strategy_configs:
            for name, cfg in strategy_configs.items():
                self.register(name, cfg)

    def register(self, strategy_name: str, limits: dict | None = None) -> None:
        merged = dict(_DEFAULT_LIMITS)
        if limits:
            merged.update(limits)
        self._states[strategy_name] = _StrategyState(limits=merged)

    def update_balance(self, strategy_name: str, equity: float) -> None:
        """Call each loop cycle with equity allocated to this strategy."""
        st = self._get_state(strategy_name)
        today_iso = datetime.now(timezone.utc).date().isoformat()

        if st.last_reset_date != today_iso:
            st.daily_start_equity = equity
            st.daily_pnl          = 0.0
            st.halted_today       = False
            st.consec_losses      = 0
            st.last_reset_date    = today_iso
            log.info("RiskManager %s: daily reset (equity=%.2f)", strategy_name, equity)

        st.current_equity = equity
        if equity > st.peak_equity:
            st.peak_equity = equity
        st.daily_pnl = equity - st.daily_start_equity

    def record_trade(self, strategy_name: str, pnl: float) -> None:
        st = self._get_state(strategy_name)
        if pnl < 0:
            st.consec_losses += 1
        else:
            st.consec_losses = 0

    def trading_allowed(self, strategy_name: str) -> bool:
        st  = self._get_state(strategy_name)
        lim = st.limits

        if st.killed:
            return False

        if st.peak_equity > 0:
            dd_pct = (st.peak_equity - st.current_equity) / st.peak_equity
            if dd_pct >= lim["max_drawdown_pct"]:
                st.killed = True
                log.critical("RiskManager %s: kill switch DD=%.2f%%", strategy_name, dd_pct * 100)
                return False

        if st.halted_today:
            return False

        if st.daily_start_equity > 0 and st.daily_pnl < 0:
            loss_pct = abs(st.daily_pnl) / st.daily_start_equity
            if loss_pct >= lim["max_daily_loss_pct"]:
                st.halted_today = True
                log.warning("RiskManager %s: daily halt loss=%.2f%%", strategy_name, loss_pct * 100)
                return False

        if st.consec_losses >= lim["max_consec_losses"]:
            st.halted_today = True
            log.warning("RiskManager %s: daily halt consec=%d", strategy_name, st.consec_losses)
            return False

        return True

    def calc_qty(
        self,
        strategy_name: str,
        balance:       float,
        sl_distance:   float,
        pip_value:     float,
        pip_size:      float = 0.0001,
        min_lot:       float = 0.01,
        max_lot:       float = 5.0,
    ) -> float:
        """Lot size so |entry-sl| loss equals risk_per_trade_pct of balance."""
        st       = self._get_state(strategy_name)
        risk_usd = balance * st.limits["risk_per_trade_pct"]
        sl_pips  = sl_distance / pip_size
        if sl_pips <= 0:
            return min_lot
        lots = risk_usd / (sl_pips * pip_value)
        return round(max(min_lot, min(lots, max_lot)), 2)

    def get_state(self, strategy_name: str) -> dict:
        st = self._get_state(strategy_name)
        return {
            "strategy":        strategy_name,
            "peak_equity":     st.peak_equity,
            "current_equity":  st.current_equity,
            "daily_pnl":       st.daily_pnl,
            "consec_losses":   st.consec_losses,
            "halted_today":    st.halted_today,
            "killed":          st.killed,
            "trading_allowed": self.trading_allowed(strategy_name),
        }

    def _get_state(self, strategy_name: str) -> _StrategyState:
        if strategy_name not in self._states:
            self.register(strategy_name)
        return self._states[strategy_name]
