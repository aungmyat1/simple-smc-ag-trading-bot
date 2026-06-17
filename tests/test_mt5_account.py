"""
MT5 Account Connectivity Diagnostic — MetaAPI cloud backend.
Read-only. No orders. No mutations.

Run:
    python tests/test_mt5_account.py

Exit codes:
    0  — all checks passed
    1  — account not deployed / not connected / symbols missing
    2  — MetaAPI package not installed or credentials missing
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# ── load .env ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # dotenv optional; env vars may already be set

# ── package check ──────────────────────────────────────────────────────────────
try:
    from metaapi_cloud_sdk import MetaApi
except ImportError:
    print("CONNECTED: NO")
    print("ERROR: metaapi-cloud-sdk not installed.")
    print("FIX:   pip install metaapi-cloud-sdk")
    sys.exit(2)

REQUIRED_SYMBOLS       = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "XAUUSD", "BTCUSD"]
EXPECTED_SERVER_SUBSTR = "VTMarkets"
DIVIDER                = "-" * 52


def _fail(reason: str) -> None:
    print(f"\nFAIL: {reason}")
    sys.exit(1)


async def run_diagnostics() -> None:
    print(DIVIDER)
    print("  MT5 ACCOUNT CONNECTIVITY DIAGNOSTIC")
    print("  via MetaAPI cloud (no local terminal)")
    print(DIVIDER)

    token      = os.getenv("METAAPI_TOKEN", "").strip()
    account_id = os.getenv("METAAPI_ACCOUNT_ID", "").strip()

    if not token:
        print("CONNECTED: NO")
        _fail("METAAPI_TOKEN not set in .env")
    if not account_id:
        print("CONNECTED: NO")
        _fail("METAAPI_ACCOUNT_ID not set in .env")

    api = MetaApi(token)
    try:
        # ── 1. Retrieve account ────────────────────────────────────────────────
        try:
            account = await api.metatrader_account_api.get_account(account_id)
        except Exception as exc:
            print("CONNECTED: NO")
            _fail(f"get_account({account_id}) failed: {exc}")

        print(f"ACCOUNT_ID: {account.id}")
        print(f"STATE:      {account.state}")

        if account.state == "DRAFT":
            print("CONNECTED: NO")
            _fail(
                "MetaAPI account is DRAFT (never deployed).\n"
                "       Top up at app.metaapi.cloud/billing, then retry.\n"
                "       The account server is already set to VTMarkets-Demo."
            )

        if account.state not in ("DEPLOYED", "DEPLOYING", "CONNECTED"):
            print("CONNECTED: NO")
            _fail(
                f"Unexpected account state: {account.state}. "
                "Expected DEPLOYED or CONNECTED."
            )

        # ── 2. Wait for broker connection ──────────────────────────────────────
        print("Waiting for broker connection ...")
        try:
            await account.wait_connected(timeout_in_seconds=90)
        except Exception as exc:
            print("CONNECTED: NO")
            _fail(f"Account never reached CONNECTED: {exc}")

        print("CONNECTED: YES")

        # ── 3. Open RPC connection ─────────────────────────────────────────────
        connection = account.get_rpc_connection()
        await connection.connect()
        try:
            await connection.wait_synchronized(timeout_in_seconds=60)
        except Exception:
            pass  # partial sync is fine for read-only diagnostics

        # ── 4. Account information ─────────────────────────────────────────────
        info = await connection.get_account_information()
        if info is None:
            _fail("get_account_information() returned None — account logged in but no data")

        balance_str  = f"{info.get('balance', 0):.2f} {info.get('currency', '?')}"
        equity_str   = f"{info.get('equity', 0):.2f} {info.get('currency', '?')}"
        leverage_str = f"1:{info.get('leverage', '?')}"

        print(f"BROKER:   {info.get('broker', account.server)}")
        print(f"SERVER:   {account.server}")
        print(f"LOGIN:    {info.get('login', '?')}")
        print(f"NAME:     {info.get('name', '?')}")
        print(f"BALANCE:  {balance_str}")
        print(f"EQUITY:   {equity_str}")
        print(f"LEVERAGE: {leverage_str}")
        print(f"CURRENCY: {info.get('currency', '?')}")
        print(DIVIDER)

        # ── 5. Symbol availability + live spread ──────────────────────────────
        missing: list[str] = []
        for sym in REQUIRED_SYMBOLS:
            try:
                spec = await connection.get_symbol_specification(sym)
                tick = await connection.get_symbol_price(sym)
                if spec is None:
                    raise RuntimeError("spec=None")
                spread = ""
                if tick:
                    bid = tick.get("bid", 0)
                    ask = tick.get("ask", 0)
                    pip = 0.01 if "JPY" in sym else 0.0001
                    if sym in ("XAUUSD", "XAGUSD"):
                        pip = 0.01
                    spread_pips = (ask - bid) / pip
                    spread = f"  spread={spread_pips:.2f}pip"
                print(f"{sym:<8}: AVAILABLE{spread}")
            except Exception as exc:
                print(f"{sym:<8}: MISSING  ({exc})")
                missing.append(sym)

        print(DIVIDER)

        # ── 6. Broker name check ───────────────────────────────────────────────
        if EXPECTED_SERVER_SUBSTR.lower() in account.server.lower():
            print(f"BROKER CHECK: PASS  ({EXPECTED_SERVER_SUBSTR!r} in {account.server!r})")
        else:
            print(
                f"BROKER CHECK: WARN  ({EXPECTED_SERVER_SUBSTR!r} NOT in "
                f"{account.server!r})"
            )

        print(DIVIDER)

        await connection.close()

    finally:
        api.close()

    if missing:
        _fail(
            f"Required symbols unavailable: {missing}. "
            "Check symbol names for this broker server."
        )

    print("RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(run_diagnostics())
