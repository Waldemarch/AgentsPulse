"""Tests for the Claude Code status line text (agentpulse/statusline.py)."""
from __future__ import annotations

import unittest
from datetime import datetime

from agentpulse.forecast import Outlook
from agentpulse.formatting import format_clock
from agentpulse.i18n import T
from agentpulse.statusline import format_statusline

# Local time, the way the status line names reset times.
NOW = datetime(2026, 1, 14, 9, 0).timestamp()
HOUR = 3600
DAY = 24 * HOUR
FIELDS = ['five_hour', 'seven_day']
YELLOW = '\x1b[33m'
RED = '\x1b[31m'
RESET = '\x1b[0m'


def _outlook(status: str, reset_at: float, *, limit_at: float | None = None) -> Outlook:
    return Outlook(status, None, limit_at, reset_at, 50.0, 'pace')


def _usage(**utilization: float | None) -> dict:
    return {field: {'utilization': value, 'resets_at': '2026-01-14T12:00:00+00:00'} for field, value in utilization.items()}


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime('%H:%M')


class TestFormatStatusline(unittest.TestCase):
    """Tests for format_statusline()."""

    def setUp(self):
        self.session_reset = NOW + 2 * HOUR
        self.week_reset = NOW + 3 * DAY

    def _on_track(self) -> dict:
        return {'five_hour': _outlook('ok', self.session_reset), 'seven_day': _outlook('ok', self.week_reset)}

    def test_every_provider_with_its_fields(self):
        sections = [('claude', _usage(five_hour=42.0, seven_day=61.0)), ('codex', _usage(five_hour=10.0, seven_day=3.0))]
        outlooks = {'claude': self._on_track(), 'codex': self._on_track()}

        text = format_statusline(sections, outlooks, fields=FIELDS, now=NOW)

        clock = _clock(self.session_reset)
        self.assertEqual(text, f'Claude 5h 42% ↺{clock} · 7d 61% | Codex 5h 10% ↺{clock} · 7d 3%')

    def test_quotas_on_track_stay_uncoloured(self):
        text = format_statusline([('claude', _usage(five_hour=42.0, seven_day=61.0))], {'claude': self._on_track()}, fields=FIELDS, now=NOW)

        self.assertNotIn('\x1b', text)

    def test_tight_quota_turns_yellow_with_its_status(self):
        outlooks = {'claude': {'five_hour': _outlook('tight', self.session_reset)}}

        text = format_statusline([('claude', _usage(five_hour=91.0))], outlooks, fields=FIELDS, now=NOW)

        self.assertEqual(text, f"Claude {YELLOW}5h 91% ↺{_clock(self.session_reset)} {T['status_tight']}{RESET}")

    def test_projected_limit_names_its_time_in_red(self):
        limit_at = NOW + HOUR
        outlooks = {'claude': {'five_hour': _outlook('limit', self.session_reset, limit_at=limit_at)}}

        text = format_statusline([('claude', _usage(five_hour=72.0))], outlooks, fields=FIELDS, now=NOW)

        status = T['status_limit_at'].format(clock=format_clock(limit_at, now=NOW))
        self.assertEqual(text, f'Claude {RED}5h 72% ↺{_clock(self.session_reset)} {status}{RESET}')

    def test_weekly_limit_without_a_time_names_the_limit(self):
        outlooks = {'claude': {'seven_day': _outlook('limit', self.week_reset)}}

        text = format_statusline([('claude', _usage(seven_day=61.0))], outlooks, fields=FIELDS, now=NOW, color=False)

        self.assertEqual(text, f"Claude 7d 61% {T['status_limit']}")

    def test_blocked_weekly_quota_names_the_day_it_resets(self):
        outlooks = {'claude': {'five_hour': _outlook('ok', self.session_reset), 'seven_day': _outlook('blocked', self.week_reset)}}

        text = format_statusline([('claude', _usage(five_hour=0.0, seven_day=100.0))], outlooks, fields=FIELDS, now=NOW, color=False)

        weekly = f"7d 100% ↺{format_clock(self.week_reset, now=NOW)} {T['status_blocked']}"
        self.assertEqual(text, f'Claude 5h 0% ↺{_clock(self.session_reset)} · {weekly}')

    def test_blocked_session_is_red(self):
        outlooks = {'claude': {'five_hour': _outlook('blocked', self.session_reset)}}

        text = format_statusline([('claude', _usage(five_hour=100.0))], outlooks, fields=FIELDS, now=NOW)

        self.assertEqual(text, f"Claude {RED}5h 100% ↺{_clock(self.session_reset)} {T['status_blocked']}{RESET}")

    def test_color_off_has_no_escape_codes(self):
        outlooks = {'claude': {'five_hour': _outlook('tight', self.session_reset), 'seven_day': _outlook('blocked', self.week_reset)}}

        text = format_statusline([('claude', _usage(five_hour=91.0, seven_day=100.0))], outlooks, fields=FIELDS, now=NOW, color=False)

        self.assertNotIn('\x1b', text)
        self.assertIn(T['status_tight'], text)
        self.assertIn(T['status_blocked'], text)

    def test_session_reset_after_midnight_is_only_a_clock(self):
        late = datetime(2026, 1, 14, 23, 0).timestamp()
        reset = late + 2 * HOUR
        outlooks = {'claude': {'five_hour': _outlook('ok', reset)}}

        text = format_statusline([('claude', _usage(five_hour=5.0))], outlooks, fields=FIELDS, now=late)

        self.assertEqual(text, f'Claude 5h 5% ↺{_clock(reset)}')

    def test_quota_without_an_outlook_shows_only_its_percentage(self):
        text = format_statusline([('claude', _usage(five_hour=42.0, seven_day=61.0))], {}, fields=FIELDS, now=NOW)

        self.assertEqual(text, 'Claude 5h 42% · 7d 61%')

    def test_fields_keep_their_order_and_skip_missing_or_null_quotas(self):
        usage = {**_usage(five_hour=42.0, seven_day=61.0), 'seven_day_opus': None}

        text = format_statusline([('claude', usage)], {}, fields=['seven_day', 'seven_day_opus', 'seven_day_sonnet', 'five_hour'], now=NOW)

        self.assertEqual(text, 'Claude 7d 61% · 5h 42%')

    def test_quota_without_a_value_is_skipped(self):
        text = format_statusline([('claude', _usage(five_hour=None, seven_day=61.0))], {}, fields=FIELDS, now=NOW)

        self.assertEqual(text, 'Claude 7d 61%')

    def test_model_quota_is_labelled_with_its_model(self):
        text = format_statusline([('claude', _usage(seven_day_sonnet=12.4))], {}, fields=['seven_day_sonnet'], now=NOW)

        self.assertEqual(text, 'Claude 7d Sonnet 12%')

    def test_extra_usage_in_the_fields_shows_like_in_the_tooltip(self):
        usage = {**_usage(five_hour=42.0), 'extra_usage': {'is_enabled': True, 'utilization': 80.0}}

        text = format_statusline([('claude', usage)], {}, fields=['five_hour', 'extra_usage'], now=NOW)

        self.assertEqual(text, 'Claude 5h 42% · Extra Usage 80%')

    def test_provider_with_an_error_is_left_out(self):
        sections = [('claude', {'error': 'server down'}), ('codex', _usage(five_hour=10.0))]

        self.assertEqual(format_statusline(sections, {}, fields=FIELDS, now=NOW), 'Codex 5h 10%')

    def test_provider_waiting_for_its_first_reading_is_left_out(self):
        sections = [('claude', _usage(five_hour=42.0)), ('kimi', {})]

        self.assertEqual(format_statusline(sections, {}, fields=FIELDS, now=NOW), 'Claude 5h 42%')

    def test_expired_claude_session_is_named(self):
        sections = [('claude', {'error': 'unauthorized', 'auth_error': True})]

        self.assertEqual(format_statusline(sections, {}, fields=FIELDS, now=NOW), T['auth_expired_label'])

    def test_errors_without_any_data_show_the_error_label(self):
        sections = [('claude', {'error': 'server down'}), ('codex', {'error': 'unauthorized', 'auth_error': True})]

        self.assertEqual(format_statusline(sections, {}, fields=FIELDS, now=NOW), T['error_label'])

    def test_no_reading_yet_shows_loading(self):
        self.assertEqual(format_statusline([('claude', {})], {}, fields=FIELDS, now=NOW), T['loading'])

    def test_no_providers_give_an_empty_line(self):
        self.assertEqual(format_statusline([], {}, fields=FIELDS, now=NOW), '')

    def test_no_shown_fields_give_an_empty_line(self):
        self.assertEqual(format_statusline([('claude', _usage(five_hour=42.0))], {}, fields=[], now=NOW), '')

    def test_unknown_provider_is_titled_from_its_name(self):
        self.assertEqual(format_statusline([('gemini', _usage(five_hour=1.0))], {}, fields=FIELDS, now=NOW), 'Gemini 5h 1%')

    def test_text_is_one_line(self):
        sections = [('claude', _usage(five_hour=91.0, seven_day=100.0)), ('codex', _usage(five_hour=10.0)), ('kimi', {'error': 'down'})]
        outlooks = {'claude': {'five_hour': _outlook('tight', self.session_reset), 'seven_day': _outlook('blocked', self.week_reset)}}

        self.assertNotIn('\n', format_statusline(sections, outlooks, fields=FIELDS, now=NOW))


if __name__ == '__main__':
    unittest.main()
