"""
V1 — AST guards for import hygiene.

alerts.py must import only stdlib + requests.
It must NOT import executor, pybit, or any trading SDK — alerts must
be a leaf module so a Telegram outage cannot drag in exchange state.

backtest.py must NOT import from _archive/ (regression from Trial 3).
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
