"""Tests for the away summary notification (agentpulse/away.py)."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agentpulse.away import away_summary
from agentpulse.formatting import format_clock, format_duration
from agentpulse.i18n import T

# Local time, the way the summary names reset times.
STARTED = datetime(2026, 1, 14, 12, 0).timestamp()
MINUTE = 60
HOUR = 3600
DAY = 24 * HOUR
ENDED = STARTED + 95 * MINUTE


def _quota(pct: float | None, reset: float | None) -> dict:
    resets_at = datetime.fromtimestamp(reset, tz=timezone.utc).isoformat() if reset is not None else None
    return {'utilization': pct, 'resets_at': resets_at}


def _used(label: str, before: int, after: int) -> str:
    return T['away_used'].format(label=label, before=before, after=after)


def _line(provider: str, *changes: str) -> str:
    return T['away_line'].format(provider=provider, changes=', '.join(changes))


class TestAwaySummary(unittest.TestCase):
    """Tests for away_summary()."""

    def setUp(self):
        self.session_reset = STARTED + 4 * HOUR
        self.week_reset = STARTED + 3 * DAY

    def _summary(self, before: dict, after: dict) -> tuple[str, str] | None:
        return away_summary(before, after, started=STARTED, ended=ENDED)

    def test_usage_added_while_away_is_listed(self):
        before = {'claude': {'five_hour': _quota(20.0, self.session_reset), 'seven_day': _quota(40.0, self.week_reset)}}
        after = {'claude': {'five_hour': _quota(85.0, self.session_reset), 'seven_day': _quota(43.0, self.week_reset)}}

        message, title = self._summary(before, after)

        self.assertEqual(message, _line('Claude', _used('5h', 20, 85), _used('7d', 40, 43)))
        self.assertEqual(title, T['away_title'].format(duration=format_duration(95 * MINUTE)))

    def test_less_than_a_point_counts_as_unchanged(self):
        before = {'claude': {'five_hour': _quota(20.0, self.session_reset)}}
        after = {'claude': {'five_hour': _quota(20.9, self.session_reset)}}

        self.assertIsNone(self._summary(before, after))

    def test_nothing_changed_gives_no_summary(self):
        usage = {'claude': {'five_hour': _quota(42.0, self.session_reset), 'seven_day': _quota(61.0, self.week_reset)}}

        self.assertIsNone(self._summary(usage, usage))

    def test_window_that_reset_while_away_names_the_time(self):
        reset = STARTED + 30 * MINUTE
        before = {'claude': {'five_hour': _quota(60.0, reset)}}
        after = {'claude': {'five_hour': _quota(12.0, reset + 5 * HOUR)}}

        message, _title = self._summary(before, after)

        self.assertEqual(message, _line('Claude', T['away_renewed'].format(label='5h', clock=format_clock(reset, now=ENDED), pct='12')))

    def test_limit_that_reset_while_away_is_available_again(self):
        reset = STARTED + 30 * MINUTE
        before = {'claude': {'five_hour': _quota(100.0, reset)}}
        after = {'claude': {'five_hour': _quota(0.0, None)}}

        message, _title = self._summary(before, after)

        self.assertEqual(message, _line('Claude', T['away_unblocked'].format(label='5h', clock=format_clock(reset, now=ENDED), pct='0')))

    def test_reset_before_the_absence_is_not_reported(self):
        before = {'claude': {'five_hour': _quota(60.0, STARTED - 10 * MINUTE)}}
        after = {'claude': {'five_hour': _quota(5.0, STARTED + 5 * HOUR)}}

        self.assertIsNone(self._summary(before, after))

    def test_reset_still_ahead_is_not_reported(self):
        before = {'claude': {'five_hour': _quota(60.0, ENDED + MINUTE)}}
        after = {'claude': {'five_hour': _quota(60.0, ENDED + MINUTE)}}

        self.assertIsNone(self._summary(before, after))

    def test_sessions_come_before_weekly_quotas(self):
        before = {'claude': {
            'seven_day_sonnet': _quota(10.0, self.week_reset), 'seven_day': _quota(40.0, self.week_reset), 'five_hour': _quota(0.0, self.session_reset),
        }}
        after = {'claude': {
            'seven_day_sonnet': _quota(12.0, self.week_reset), 'seven_day': _quota(43.0, self.week_reset), 'five_hour': _quota(30.0, self.session_reset),
        }}

        message, _title = self._summary(before, after)

        self.assertEqual(message, _line('Claude', _used('5h', 0, 30), _used('7d', 40, 43), _used('7d Sonnet', 10, 12)))

    def test_session_started_while_away_counts_from_zero(self):
        before = {'claude': {'five_hour': None}}
        after = {'claude': {'five_hour': _quota(40.0, self.session_reset)}}

        message, _title = self._summary(before, after)

        self.assertEqual(message, _line('Claude', _used('5h', 0, 40)))

    def test_disabled_quota_is_skipped(self):
        before = {'claude': {'five_hour': _quota(10.0, self.session_reset), 'seven_day_opus': None}}
        after = {'claude': {'five_hour': _quota(20.0, self.session_reset), 'seven_day_opus': None}}

        message, _title = self._summary(before, after)

        self.assertEqual(message, _line('Claude', _used('5h', 10, 20)))

    def test_extra_usage_is_not_a_quota_window(self):
        before = {'claude': {'extra_usage': {'is_enabled': True, 'utilization': 10.0}}}
        after = {'claude': {'extra_usage': {'is_enabled': True, 'utilization': 60.0}}}

        self.assertIsNone(self._summary(before, after))

    def test_providers_follow_the_order_of_the_new_readings(self):
        before = {'kimi': {'five_hour': _quota(1.0, self.session_reset)}, 'claude': {'five_hour': _quota(10.0, self.session_reset)}}
        after = {'claude': {'five_hour': _quota(20.0, self.session_reset)}, 'kimi': {'five_hour': _quota(5.0, self.session_reset)}}

        message, _title = self._summary(before, after)

        self.assertEqual(message, '\n'.join([_line('Claude', _used('5h', 10, 20)), _line('Kimi', _used('5h', 1, 5))]))

    def test_provider_with_an_error_is_left_out(self):
        before = {'claude': {'five_hour': _quota(10.0, self.session_reset)}, 'codex': {'five_hour': _quota(10.0, self.session_reset)}}
        after = {'claude': {'error': 'server down'}, 'codex': {'five_hour': _quota(30.0, self.session_reset)}}

        message, _title = self._summary(before, after)

        self.assertEqual(message, _line('Codex', _used('5h', 10, 30)))

    def test_provider_without_a_reading_from_before_is_left_out(self):
        before = {'claude': {}}
        after = {'claude': {'five_hour': _quota(30.0, self.session_reset)}, 'kimi': {'five_hour': _quota(30.0, self.session_reset)}}

        self.assertIsNone(self._summary(before, after))

    def test_long_summaries_drop_whole_lines_to_fit_a_notification(self):
        fields = [f'seven_day_{name}' for name in ('alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta', 'iota')]
        before = {provider: {field: _quota(10.0, self.week_reset) for field in fields} for provider in ('claude', 'codex', 'kimi')}
        after = {provider: {field: _quota(20.0, self.week_reset) for field in fields} for provider in ('claude', 'codex', 'kimi')}

        message, _title = self._summary(before, after)

        self.assertLessEqual(len(message), 255)
        self.assertTrue(message.startswith('Claude'))
        self.assertNotIn('Kimi', message)

    def test_one_line_that_is_too_long_is_cut(self):
        fields = [f'seven_day_model{index}' for index in range(20)]
        before = {'claude': {field: _quota(10.0, self.week_reset) for field in fields}}
        after = {'claude': {field: _quota(20.0, self.week_reset) for field in fields}}

        message, title = self._summary(before, after)

        self.assertEqual(len(message), 255)
        self.assertLessEqual(len(title), 63)


if __name__ == '__main__':
    unittest.main()
