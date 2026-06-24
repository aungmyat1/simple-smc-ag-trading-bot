"""
Tests for smc_bot/health.py.

The headline behaviour: an UNCONFIGURED database must SKIP (not FAIL), so the
file-based bot never emits the spurious CRITICAL "DB connectivity check failed"
alert it used to. A *configured* but unreachable DB must still FAIL.
"""
from datetime import datetime, timezone

from smc_bot import health


# ── database check (the spurious-alert fix) ─────────────────────────────────────

def test_database_skips_when_not_configured():
    r = health.check_database(env={})
    assert r.status == health.SKIP
    assert r.ok                      # SKIP counts as ok — no false CRITICAL
    assert "file-based" in r.detail


def test_database_fail_when_configured_and_unreachable():
    def refuse(host, port, timeout):
        raise ConnectionRefusedError(111, "Connect call failed")

    r = health.check_database(env={"DB_HOST": "127.0.0.1", "DB_PORT": "5432"}, probe=refuse)
    assert r.status == health.FAIL
    assert "127.0.0.1:5432" in r.detail


def test_database_pass_when_reachable():
    r = health.check_database(env={"DB_HOST": "db.internal"}, probe=lambda h, p, t: None)
    assert r.status == health.PASS
    assert "db.internal:5432" in r.detail   # default port applied


def test_database_url_parsed():
    captured = {}

    def probe(host, port, timeout):
        captured["host"], captured["port"] = host, port

    health.check_database(env={"DATABASE_URL": "postgres://u:p@pg.host:6543/trades"}, probe=probe)
    assert captured == {"host": "pg.host", "port": 6543}


def test_healthcheck_db_url_takes_precedence():
    captured = {}
    health.check_database(
        env={"HEALTHCHECK_DB_URL": "postgres://h:1@override:1111/d",
             "DATABASE_URL": "postgres://x@other:2222/d"},
        probe=lambda h, p, t: captured.update(host=h, port=p),
    )
    assert captured == {"host": "override", "port": 1111}


# ── disk ────────────────────────────────────────────────────────────────────────

def test_disk_returns_a_result():
    r = health.check_disk()
    assert r.name == "disk"
    assert r.status in (health.PASS, health.WARN, health.FAIL)


# ── heartbeat ─────────────────────────────────────────────────────────────────────

def test_heartbeat_warns_when_missing(tmp_path):
    r = health.check_heartbeat(tmp_path / "nope.json")
    assert r.status == health.WARN


def test_heartbeat_pass_when_fresh(tmp_path):
    p = tmp_path / "smc_bot_state.json"
    p.write_text("{}")
    r = health.check_heartbeat(p, max_age_min=30)
    assert r.status == health.PASS


def test_heartbeat_fail_when_stale(tmp_path):
    p = tmp_path / "smc_bot_state.json"
    p.write_text("{}")
    mtime = p.stat().st_mtime
    # Evaluate "now" as 60 min after the file's mtime.
    r = health.check_heartbeat(p, max_age_min=30, now=mtime + 3600)
    assert r.status == health.FAIL


# ── aggregation ───────────────────────────────────────────────────────────────────

def test_overall_status_and_exit_code():
    results = [
        health.CheckResult("a", health.PASS),
        health.CheckResult("b", health.SKIP),
        health.CheckResult("c", health.WARN),
    ]
    assert health.overall_status(results) == health.WARN
    assert health.exit_code(results) == 1

    results.append(health.CheckResult("d", health.FAIL))
    assert health.overall_status(results) == health.FAIL
    assert health.exit_code(results) == 2


def test_all_clean_is_healthy():
    results = [health.CheckResult("a", health.PASS), health.CheckResult("b", health.SKIP)]
    assert health.overall_status(results) == health.PASS
    assert health.exit_code(results) == 0


# ── report formatting ─────────────────────────────────────────────────────────────

def test_format_report_critical():
    results = [health.CheckResult("database", health.FAIL, "cannot connect to x:5432")]
    ts = datetime(2026, 6, 19, 0, 38, 23, tzinfo=timezone.utc)
    out = health.format_report(results, timestamp=ts)
    assert "CRITICAL" in out
    assert "2026-06-19 00:38:23 UTC" in out
    assert "database: FAIL" in out


def test_format_report_ok_when_db_skipped():
    results = [
        health.CheckResult("database", health.SKIP, "no database configured"),
        health.CheckResult("disk", health.PASS, "40% used"),
    ]
    out = health.format_report(results)
    assert "OK" in out
    assert "CRITICAL" not in out
