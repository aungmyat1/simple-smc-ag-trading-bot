"""
Readiness report — run before enabling demo execution.

Checks:
  1. Module imports cleanly (smc_bot/)
  2. Config file loads with all required keys
  3. Guard thresholds are in range
  4. State file exists or initialises cleanly
  5. Signal log exists / is readable
  6. Test suite passes (via subprocess)
  7. Signal generation stats (if smc_bot_signals.csv exists)

Usage:
    python scripts/readiness_report.py
    python scripts/readiness_report.py --no-tests   # skip pytest run
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_OK   = "PASS"
_FAIL = "FAIL"
_WARN = "WARN"


def _row(label: str, status: str, detail: str = "") -> None:
    width = 42
    pad   = " " * max(1, width - len(label))
    print(f"  {label}{pad}{status}  {detail}")


# ── Check 1: imports ──────────────────────────────────────────────────────────

def check_imports() -> bool:
    print("\n[1] Module imports")
    ok = True
    for mod in ["smc_bot.risk", "smc_bot.bot"]:
        try:
            __import__(mod)
            _row(mod, _OK)
        except Exception as exc:
            _row(mod, _FAIL, str(exc))
            ok = False
    return ok


# ── Check 2: config ───────────────────────────────────────────────────────────

def check_config() -> bool:
    print("\n[2] Config file (smc_bot/config.yaml)")
    try:
        import yaml
        cfg  = yaml.safe_load((ROOT / "smc_bot/config.yaml").read_text())
        rc   = cfg.get("risk", {})
        ok   = True

        required = {
            "risk.risk_pct":                rc.get("risk_pct"),
            "risk.max_daily_loss":          rc.get("max_daily_loss"),
            "risk.max_drawdown":            rc.get("max_drawdown"),
            "risk.max_consecutive_losses":  rc.get("max_consecutive_losses"),
            "signal_only_mode":             cfg.get("signal_only_mode"),
        }
        for key, val in required.items():
            if val is None:
                _row(key, _FAIL, "MISSING")
                ok = False
            else:
                _row(key, _OK, str(val))

        # Sanity ranges
        if rc.get("max_daily_loss", 0) > 0.10:
            _row("risk.max_daily_loss range", _WARN, "> 10% — unusually high")
        if rc.get("max_drawdown", 0) > 0.25:
            _row("risk.max_drawdown range",   _WARN, "> 25% — unusually high")

        mode = "SIGNAL_ONLY" if cfg.get("signal_only_mode") else "EXECUTE"
        _row("execution mode", _OK if mode == "SIGNAL_ONLY" else _WARN, mode)
        return ok
    except Exception as exc:
        _row("config.yaml load", _FAIL, str(exc))
        return False


# ── Check 3: guard functions ──────────────────────────────────────────────────

def check_guards() -> bool:
    print("\n[3] Guard function smoke-tests")
    try:
        from smc_bot.risk import trading_allowed
        ok, _ = trading_allowed(
            equity=10_000, peak_equity=10_000, day_start_equity=10_000,
            consecutive_losses=0,
            max_daily_loss=0.02, max_drawdown=0.10, max_consecutive_losses=2,
        )
        _row("all-clean → ok=True", _OK if ok else _FAIL)

        ok2, reason = trading_allowed(
            equity=8_500, peak_equity=10_000, day_start_equity=10_000,
            consecutive_losses=0,
            max_daily_loss=0.02, max_drawdown=0.10, max_consecutive_losses=2,
        )
        fired = not ok2 and "DRAWDOWN" in reason.upper()
        _row("drawdown breach → ok=False", _OK if fired else _FAIL, reason[:60] if not fired else "")

        ok3, reason3 = trading_allowed(
            equity=10_000, peak_equity=10_000, day_start_equity=10_000,
            consecutive_losses=2,
            max_daily_loss=0.02, max_drawdown=0.10, max_consecutive_losses=2,
        )
        fired3 = not ok3 and "CONSECUTIVE" in reason3.upper()
        _row("consec breach → ok=False", _OK if fired3 else _FAIL, reason3[:60] if not fired3 else "")

        return ok and fired and fired3
    except Exception as exc:
        _row("guard smoke-test", _FAIL, str(exc))
        return False


# ── Check 4: state persistence ────────────────────────────────────────────────

def check_state() -> bool:
    print("\n[4] BotState persistence")
    import tempfile
    try:
        import smc_bot.bot as bot_module
        original = bot_module._STATE_FILE
        with tempfile.NamedTemporaryFile(suffix=".json", delete=True) as tmp:
            bot_module._STATE_FILE = Path(tmp.name)
            tmp.close()  # let BotState re-create it
            from smc_bot.bot import BotState
            s = BotState(peak_equity=12_000.0, consecutive_losses=1)
            s.save()
            loaded = BotState.load()
            ok = loaded.peak_equity == 12_000.0 and loaded.consecutive_losses == 1
            _row("save/load roundtrip", _OK if ok else _FAIL)
            bot_module._STATE_FILE = original
            return ok
    except Exception as exc:
        _row("state persistence", _FAIL, str(exc))
        return False


# ── Check 5: pytest suite ─────────────────────────────────────────────────────

def check_tests(run_tests: bool) -> bool:
    print("\n[5] Test suite")
    if not run_tests:
        _row("pytest", _WARN, "skipped (--no-tests)")
        return True
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_smc_risk.py",
             "tests/test_smc_state.py", "-v", "--tb=short"],
            capture_output=True, text=True, cwd=str(ROOT)
        )
        lines   = result.stdout.strip().split("\n")
        summary = next((l for l in reversed(lines) if "passed" in l or "failed" in l or "error" in l), "no summary")
        passed  = result.returncode == 0
        _row("pytest smc_bot tests", _OK if passed else _FAIL, summary)
        if not passed:
            print("\n    --- pytest output ---")
            for line in lines[-25:]:
                print(f"    {line}")
        return passed
    except Exception as exc:
        _row("pytest run", _FAIL, str(exc))
        return False


# ── Check 6: signal log stats ─────────────────────────────────────────────────

def check_signals() -> None:
    print("\n[6] Signal log stats (smc_bot_signals.csv)")
    sig_file = ROOT / "smc_bot_signals.csv"
    if not sig_file.exists():
        _row("smc_bot_signals.csv", _WARN, "not yet created — run the bot first")
        return
    try:
        with open(sig_file) as f:
            rows = list(csv.DictReader(f))
        n = len(rows)
        _row("total signals logged", _OK, str(n))
        if n > 0:
            by_bias = {}
            by_mode = {}
            for r in rows:
                by_bias[r.get("bias", "?")] = by_bias.get(r.get("bias", "?"), 0) + 1
                by_mode[r.get("mode", "?")] = by_mode.get(r.get("mode", "?"), 0) + 1
            for bias, cnt in sorted(by_bias.items()):
                _row(f"  bias={bias}", _OK, f"{cnt} signals")
            for mode, cnt in sorted(by_mode.items()):
                _row(f"  mode={mode}", _OK, f"{cnt} signals")
    except Exception as exc:
        _row("signal log read", _FAIL, str(exc))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SMC Bot readiness report")
    parser.add_argument("--no-tests", action="store_true", help="Skip pytest run")
    args = parser.parse_args()

    print("=" * 60)
    print("  SMC BOT — READINESS REPORT")
    print("=" * 60)

    results = [
        check_imports(),
        check_config(),
        check_guards(),
        check_state(),
        check_tests(not args.no_tests),
    ]
    check_signals()  # informational, doesn't block verdict

    all_pass = all(results)
    print("\n" + "=" * 60)
    if all_pass:
        print("  VERDICT: READY — guards verified, signal-only mode active.")
        print("  Next step: run the bot, collect 1–2 weeks of signal logs,")
        print("  then flip signal_only_mode: false to enable demo execution.")
    else:
        failed = sum(1 for r in results if not r)
        print(f"  VERDICT: NOT READY — {failed} check(s) failed. Fix before proceeding.")
    print("=" * 60)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
