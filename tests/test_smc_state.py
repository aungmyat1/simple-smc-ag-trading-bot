"""
Tests for smc_bot/bot.py — BotState persistence and day/peak tracking.

Covers the restart-resume invariant: state must survive a process restart.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_state(tmp_path: Path):
    """Import BotState with _STATE_FILE redirected to a temp dir."""
    state_file = tmp_path / "smc_bot_state.json"
    import smc_bot.bot as bot_module
    original = bot_module._STATE_FILE
    bot_module._STATE_FILE = state_file
    try:
        from smc_bot.bot import BotState
        yield BotState, state_file
    finally:
        bot_module._STATE_FILE = original


# ── Persistence ───────────────────────────────────────────────────────────────

class TestBotStatePersistence:
    def test_load_returns_defaults_when_file_missing(self, tmp_path):
        import smc_bot.bot as bot_module
        original = bot_module._STATE_FILE
        bot_module._STATE_FILE = tmp_path / "nonexistent.json"
        try:
            from smc_bot.bot import BotState
            s = BotState.load()
            assert s.peak_equity == 0.0
            assert s.consecutive_losses == 0
            assert s.was_in_position is False
        finally:
            bot_module._STATE_FILE = original

    def test_save_and_load_roundtrip(self, tmp_path):
        import smc_bot.bot as bot_module
        original = bot_module._STATE_FILE
        state_file = tmp_path / "state.json"
        bot_module._STATE_FILE = state_file
        try:
            from smc_bot.bot import BotState
            s = BotState(
                peak_equity=12_000.0,
                day_start_equity=11_500.0,
                day_start_date="2026-06-15",
                consecutive_losses=1,
                was_in_position=True,
            )
            s.save()
            assert state_file.exists()

            loaded = BotState.load()
            assert loaded.peak_equity == 12_000.0
            assert loaded.day_start_equity == 11_500.0
            assert loaded.day_start_date == "2026-06-15"
            assert loaded.consecutive_losses == 1
            assert loaded.was_in_position is True
        finally:
            bot_module._STATE_FILE = original

    def test_save_produces_valid_json(self, tmp_path):
        import smc_bot.bot as bot_module
        original = bot_module._STATE_FILE
        state_file = tmp_path / "state.json"
        bot_module._STATE_FILE = state_file
        try:
            from smc_bot.bot import BotState
            BotState(peak_equity=9_999.99, consecutive_losses=2).save()
            data = json.loads(state_file.read_text())
            assert "peak_equity" in data
            assert "consecutive_losses" in data
        finally:
            bot_module._STATE_FILE = original

    def test_load_tolerates_corrupt_file(self, tmp_path):
        import smc_bot.bot as bot_module
        original = bot_module._STATE_FILE
        state_file = tmp_path / "state.json"
        state_file.write_text("NOT VALID JSON }{")
        bot_module._STATE_FILE = state_file
        try:
            from smc_bot.bot import BotState
            s = BotState.load()
            assert s.consecutive_losses == 0
        finally:
            bot_module._STATE_FILE = original


# ── Day-start tracking ────────────────────────────────────────────────────────

class TestBotStateDayTracking:
    def test_first_call_sets_day_start(self):
        from smc_bot.bot import BotState
        s = BotState()
        s.update_day_start(10_000.0)
        assert s.day_start_equity == 10_000.0
        assert len(s.day_start_date) == 10  # "YYYY-MM-DD"

    def test_same_day_does_not_reset(self):
        from smc_bot.bot import BotState
        s = BotState(day_start_equity=10_000.0, day_start_date="2026-06-15")
        with patch("smc_bot.bot.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-06-15"
            s.update_day_start(11_000.0)
        assert s.day_start_equity == 10_000.0  # unchanged

    def test_new_day_resets(self):
        from smc_bot.bot import BotState
        s = BotState(day_start_equity=10_000.0, day_start_date="2026-06-14")
        with patch("smc_bot.bot.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-06-15"
            s.update_day_start(11_000.0)
        assert s.day_start_equity == 11_000.0


# ── Peak tracking ─────────────────────────────────────────────────────────────

class TestBotStatePeakTracking:
    def test_peak_updates_on_new_high(self):
        from smc_bot.bot import BotState
        s = BotState(peak_equity=10_000.0)
        s.update_peak(11_000.0)
        assert s.peak_equity == 11_000.0

    def test_peak_does_not_decrease(self):
        from smc_bot.bot import BotState
        s = BotState(peak_equity=10_000.0)
        s.update_peak(9_000.0)
        assert s.peak_equity == 10_000.0

    def test_peak_initialises_from_zero(self):
        from smc_bot.bot import BotState
        s = BotState()
        s.update_peak(8_500.0)
        assert s.peak_equity == 8_500.0
