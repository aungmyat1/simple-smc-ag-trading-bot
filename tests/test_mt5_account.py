"""
MT5 Account Connectivity Diagnostic
------------------------------------
Read-only. No orders. No mutations.

Run:
    python tests/test_mt5_account.py

Exit codes:
    0  — all checks passed
    1  — MT5 not running / not logged in / symbols missing
    2  — MetaTrader5 package not installed
"""
from __future__ import annotations

import sys

# ── Package availability ───────────────────────────────────────────────────────
try:
    import MetaTrader5 as mt5
except ImportError:
    print("CONNECTED: NO")
    print("ERROR: MetaTrader5 Python package is not installed.")
    print("FIX:   pip install MetaTrader5")
    sys.exit(2)

REQUIRED_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "XAUUSD", "BTCUSD"]
EXPECTED_SERVER_SUBSTRING = "VTMarkets"

DIVIDER = "-" * 48


def _fail(reason: str) -> None:
    print(f"\nFAIL: {reason}")
    mt5.shutdown()
    sys.exit(1)


def run_diagnostics() -> None:
    print(DIVIDER)
    print("  MT5 ACCOUNT CONNECTIVITY DIAGNOSTIC")
    print(DIVIDER)

    # ── 1. Initialise terminal connection ──────────────────────────────────────
    if not mt5.initialize():
        err = mt5.last_error()
        print("CONNECTED: NO")
        _fail(
            f"mt5.initialize() returned False — MT5 terminal is not running "
            f"or not accessible.\n       MT5 error: {err}"
        )

    print("CONNECTED: YES")

    # ── 2. Account info ────────────────────────────────────────────────────────
    info = mt5.account_info()
    if info is None:
        _fail(
            "mt5.account_info() returned None — terminal is running but no "
            "account is logged in. Open MT5 and log in to a demo account."
        )

    balance_str   = f"{info.balance:.2f} {info.currency}"
    equity_str    = f"{info.equity:.2f} {info.currency}"
    leverage_str  = f"1:{info.leverage}"

    print(f"BROKER:   {info.company}")
    print(f"SERVER:   {info.server}")
    print(f"LOGIN:    {info.login}")
    print(f"NAME:     {info.name}")
    print(f"BALANCE:  {balance_str}")
    print(f"EQUITY:   {equity_str}")
    print(f"LEVERAGE: {leverage_str}")
    print(f"CURRENCY: {info.currency}")
    print(DIVIDER)

    # ── 3. Symbol availability ─────────────────────────────────────────────────
    missing: list[str] = []
    for sym in REQUIRED_SYMBOLS:
        sym_info = mt5.symbol_info(sym)
        if sym_info is None:
            # Try enabling the symbol in Market Watch first
            mt5.symbol_select(sym, True)
            sym_info = mt5.symbol_info(sym)

        status = "AVAILABLE" if sym_info is not None else "MISSING"
        print(f"{sym:<8}: {status}")
        if sym_info is None:
            missing.append(sym)

    print(DIVIDER)

    # ── 4. Server name contains expected broker string ─────────────────────────
    if EXPECTED_SERVER_SUBSTRING.lower() in info.server.lower():
        print(f"BROKER CHECK: PASS  ({EXPECTED_SERVER_SUBSTRING!r} found in {info.server!r})")
    else:
        print(
            f"BROKER CHECK: WARN  ({EXPECTED_SERVER_SUBSTRING!r} NOT found in "
            f"{info.server!r}) — connected to a different broker or server."
        )

    print(DIVIDER)

    mt5.shutdown()

    # ── 5. Final verdict ───────────────────────────────────────────────────────
    if missing:
        _fail(
            f"The following required symbols are unavailable on this server: "
            f"{missing}. Add them to Market Watch in MT5 and retry."
        )

    print("RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    run_diagnostics()
