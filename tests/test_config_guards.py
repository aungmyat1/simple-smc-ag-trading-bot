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


def test_chain_is_1h_5m():
    """Trial 25 chain is HTF=1h, LTF=5m (1H bias+POI → 5M sweep+CHoCH+FVG-retest)."""
    ex = _cfg()["exchange"]
    assert ex["htf"] == "1h", (
        "Trial 25 requires HTF=1h. "
        "To revert to 4H+1H (Trial 22 PASS), update both config.yaml and this test."
    )
    assert ex["ltf"] == "5m", (
        "Trial 25 requires LTF=5m. "
        "To revert to 4H+1H (Trial 22 PASS), update both config.yaml and this test."
    )


def test_fvg_retest_enabled_for_trial_25():
    """Trial 25 requires fvg_retest_enabled=true in liquidity config."""
    lc = _cfg()["liquidity"]
    assert lc.get("fvg_retest_enabled") is True, (
        "liquidity.fvg_retest_enabled must be True for Trial 25 (1H+5M FVG-retest chain). "
        "Disabling it is a new trial — register in VERDICT_LOG.md."
    )
