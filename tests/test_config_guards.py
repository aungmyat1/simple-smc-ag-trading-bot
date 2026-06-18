"""
Shipped-config invariants for smc_bot/config.yaml.

These guard the live bot against silently diverging from the validated backtest
configuration. The mitigation guard exists because shipping it ON is exactly the
root cause of "all POIs rejected by mitigation filter" (Trials 9-10 showed any
mitigation level at 4H rejects ~76% of zones; every PASS ran with it OFF).

If a future trial deliberately enables mitigation, update this test AND register
the trial in docs/VERDICT_LOG.md (CLAUDE.md §1) — the failure is the reminder.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _cfg() -> dict:
    return yaml.safe_load((ROOT / "smc_bot" / "config.yaml").read_text())


def test_mitigation_disabled_to_match_validated_edge():
    poi = _cfg()["poi"]
    assert poi.get("mitigation_enabled") is False, (
        "poi.mitigation_enabled must be False to reproduce the Trial 21/22 PASS. "
        "Enabling it collapses the signal count and rejects nearly every POI."
    )


def test_paper_mode_defaults_are_safe():
    cfg = _cfg()
    # Signal-only OR demo must hold for paper trading; never ship a live-exec default.
    assert cfg.get("signal_only_mode") in (True, False)
    assert cfg["bybit"]["demo"] is True, "Ship demo=true; owner flips for live only."


def test_chain_is_4h_1h():
    """Trial 21/22 validated chain is HTF=4h, LTF=1h — the labels/logic depend on it."""
    ex = _cfg()["exchange"]
    assert ex["htf"] == "4h"
    assert ex["ltf"] == "1h"
