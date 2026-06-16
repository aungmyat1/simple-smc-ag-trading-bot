"""
V2 — AST guards for import hygiene.

alerts.py must import only stdlib + requests.
It must NOT import executor, pybit, or any trading SDK — alerts must
be a leaf module so a Telegram outage cannot drag in exchange state.

backtest.py must NOT import from _archive/ (regression from Trial 3).

entry_modes.py is PROPOSE-ONLY: must NOT import executor, pybit, ccxt,
or any exchange SDK — it contains NotImplementedError stubs only.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_FORBIDDEN_IN_ALERTS = {"executor", "pybit", "ccxt", "bot", "risk", "structure"}


def _collect_imports(src: str) -> list[str]:
    tree = ast.parse(src)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def test_alerts_imports_only_allowed_modules():
    src = (ROOT / "smc_bot" / "alerts.py").read_text()
    imported = _collect_imports(src)
    violations = [n for n in imported if any(f in n for f in _FORBIDDEN_IN_ALERTS)]
    assert violations == [], (
        f"alerts.py must not import trading modules: {violations}"
    )


def test_alerts_does_not_import_pybit():
    src = (ROOT / "smc_bot" / "alerts.py").read_text()
    assert "pybit" not in src


def test_alerts_does_not_import_executor():
    src = (ROOT / "smc_bot" / "alerts.py").read_text()
    assert "executor" not in src


def test_backtest_no_archive_imports():
    src = (ROOT / "scripts" / "backtest.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert "_archive" not in ast.dump(node), (
                "backtest.py must not import from _archive/"
            )


# ---------------------------------------------------------------------------
# PROPOSE-ONLY guard — entry_modes.py
# ---------------------------------------------------------------------------

_FORBIDDEN_IN_ENTRY_MODES = {"executor", "pybit", "ccxt", "pybybit", "bybit"}


def test_entry_modes_does_not_import_exchange_sdk():
    """entry_modes.py is proposal-only: must never import executor or exchange SDKs."""
    src = (ROOT / "smc_bot" / "entry_modes.py").read_text()
    imported = _collect_imports(src)
    violations = [n for n in imported if any(f in n for f in _FORBIDDEN_IN_ENTRY_MODES)]
    assert violations == [], (
        f"entry_modes.py must not import exchange SDKs (propose-only): {violations}"
    )


def test_entry_modes_raises_not_implemented():
    """All three entry mode functions must raise NotImplementedError — stubs only."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "entry_modes", ROOT / "smc_bot" / "entry_modes.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    import pytest
    with pytest.raises(NotImplementedError):
        mod.confirmation_entry("bullish", 50000.0, [], None, None)
    with pytest.raises(NotImplementedError):
        mod.refined_ob_entry("bullish", 50000.0, {}, [])
    with pytest.raises(NotImplementedError):
        mod.breaker_entry("bullish", 50000.0, [])
