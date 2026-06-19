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
    """Live config must match the T21/T22 PASS baseline: HTF=4h, LTF=1h."""
    ex = _cfg()["exchange"]
    assert ex["htf"] == "4h", (
        "HTF must be 4h to match the T21/T22 Phase-0 PASS. "
        "The 1H+5M chain (Trial 4) has no gross edge and must never be deployed. "
        "Changing this is a new trial — register in VERDICT_LOG.md."
    )
    assert ex["ltf"] == "1h", (
        "LTF must be 1h to match the T21/T22 Phase-0 PASS. "
        "Changing this is a new trial — register in VERDICT_LOG.md."
    )


def test_fvg_retest_enabled():
    """fvg_retest_enabled must be True — required by the 4H+1H entry chain."""
    lc = _cfg()["liquidity"]
    assert lc.get("fvg_retest_enabled") is True, (
        "liquidity.fvg_retest_enabled must be True for the 4H+1H chain. "
        "Disabling it changes entry behaviour — register as a new trial."
    )
