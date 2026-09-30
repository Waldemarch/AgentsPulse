"""Tests for the session timeline and the time at the limit (agentpulse/sessions.py)."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agentpulse.forecast import Sample
from agentpulse.sessions import SessionWindow, _planner, session_field, session_timeline, session_windows

TZ = timezone(timedelta(hours=1))
HOUR = 3600
DAY = 24 * HOUR
PERIOD = 5 * HOUR
WORKWEEK = (0, 1, 2, 3, 4)
# Wednesday 14 January 2026, 15:00 local time.
NOW = datetime(2026, 1, 14, 15, 0, tzinfo=TZ).timestamp()


def _at(day: int, hour: float) -> float:
    """Unix time of ``hour`` o'clock on January ``day``, 2026, local time."""
    return datetime(2026, 1, day, tzinfo=TZ).timestamp() + hour * HOUR


def _window(start: float, readings: list[tuple[float, float]]) -> list[Sample]:
    """Readings of a five-hour window that began at ``start``, at (hours into it, utilization)."""
    return [Sample(start + hours * HOUR, utilization, start + PERIOD) for hours, utilization in readings]


class TestSessionField(unittest.TestCase):
    """Tests for session_field()."""

    def test_the_shortest_base_window_in_hours_is_the_session(self):
        self.assertEqual(session_field(['seven_day', 'five_hour', 'seven_day_sonnet']), 'five_hour')

    def test_no_session_without_a_window_in_hours(self):
        self.assertIsNone(session_field(['seven_day', 'seven_day_opus']))
        self.assertIsNone(session_field(['five_hour_opus', 'iguana_necktie']))


class TestSessionWindows(unittest.TestCase):
    """Tests for session_windows()."""

    def test_windows_carry_their_span_peak_and_first_reading_at_the_limit(self):
        start = _at(14, 8)
        samples = _window(start, [(1, 40.0), (3, 100.0), (4, 100.0)])

        windows = session_windows(samples, PERIOD, since=NOW - 30 * DAY, now=NOW)

        self.assertEqual(windows, [SessionWindow(start, start + PERIOD, 100.0, start + 3 * HOUR)])

    def test_a_window_that_never_ran_out_has_no_block(self):
        samples = _window(_at(13, 9), [(1, 20.0), (4, 60.0)])

        self.assertIsNone(session_windows(samples, PERIOD, since=NOW - 30 * DAY, now=NOW)[0].blocked_at)

    def test_windows_without_usage_are_no_sessions(self):
        samples = _window(_at(13, 9), [(1, 0.0), (4, 0.0)])

        self.assertEqual(session_windows(samples, PERIOD, since=NOW - 30 * DAY, now=NOW), [])

    def test_windows_that_ended_before_the_period_are_left_out(self):
        samples = _window(_at(1, 9), [(1, 20.0)]) + _window(_at(13, 9), [(1, 30.0)])

        windows = session_windows(samples, PERIOD, since=_at(10, 0), now=NOW)

        self.assertEqual([window.start for window in windows], [_at(13, 9)])

    def test_readings_after_now_are_ignored(self):
        start = _at(14, 13)
        samples = _window(start, [(1, 60.0), (3, 100.0)])

        windows = session_windows(samples, PERIOD, since=NOW - 30 * DAY, now=NOW)

        self.assertEqual((windows[0].peak, windows[0].blocked_at), (60.0, None))


class TestSessionTimeline(unittest.TestCase):
    """Tests for session_timeline()."""

    def _series(self) -> dict:
        five_hour = (
            _window(_at(1, 9), [(1, 50.0), (2, 100.0)])
            + _window(_at(12, 9), [(1, 60.0), (4, 100.0)])
            + _window(_at(13, 9), [(1, 30.0), (4, 70.0)])
            + _window(_at(14, 11), [(1, 80.0), (3, 100.0)])
        )
        weekly = [Sample(_at(14, 14), 40.0, _at(17, 12))]
        return {
            'claude': {'five_hour': five_hour, 'seven_day': weekly},
            'kimi': {'seven_day': weekly},
        }

    def test_timeline_holds_the_windows_of_the_last_seven_days(self):
        timeline = session_timeline(self._series(), now=NOW, workdays=WORKWEEK, tz=TZ)

        self.assertEqual((timeline['days'], timeline['from']), (7, _at(8, 0)))
        claude = timeline['providers'][0]
        self.assertEqual((claude['id'], claude['field'], claude['period_seconds']), ('claude', 'five_hour', PERIOD))
        self.assertEqual([window['start'] for window in claude['windows']], [_at(12, 9), _at(13, 9), _at(14, 11)])
        self.assertEqual(claude['windows'][-1]['blocked_at'], _at(14, 14))

    def test_time_at_the_limit_counts_until_the_reset_or_now(self):
        claude = session_timeline(self._series(), now=NOW, workdays=WORKWEEK, tz=TZ)['providers'][0]

        # Three hours on the 1st, one on the 12th and one so far today.
        self.assertEqual(claude['blocked'], {'count': 3, 'seconds': 5 * HOUR})
        self.assertEqual(claude['blocked_week'], {'count': 2, 'seconds': 2 * HOUR})

    def test_providers_without_a_session_window_are_left_out(self):
        timeline = session_timeline(self._series(), now=NOW, workdays=WORKWEEK, tz=TZ)

        self.assertEqual([provider['id'] for provider in timeline['providers']], ['claude'])

    def test_planner_reads_the_first_sessions_that_have_reset(self):
        plan = session_timeline(self._series(), now=NOW, workdays=WORKWEEK, tz=TZ)['providers'][0]['planner']

        # First sessions at 9:00 on the 1st, 12th and 13th; today's is still running.  Two ran out, 3 and 1 hours before their reset.
        self.assertEqual(plan, {'start': 9 * HOUR, 'reset': 14 * HOUR, 'days': 3, 'blocked': 2, 'lead': 2 * HOUR, 'suggested': 7 * HOUR})


class TestPlanner(unittest.TestCase):
    """Tests for the first-session planner."""

    def _windows(self, starts: list[float], leads: list[float | None] | None = None) -> list[SessionWindow]:
        """Windows that began at ``starts`` and reached the limit ``leads`` seconds before their reset."""
        windows = []
        for index, start in enumerate(starts):
            lead = leads[index] if leads else None
            windows.append(SessionWindow(start, start + PERIOD, 100.0 if lead else 50.0, start + PERIOD - lead if lead else None))
        return windows

    def _plan(self, windows: list[SessionWindow], workdays=WORKWEEK):
        return _planner(windows, period=PERIOD, workdays=workdays, now=NOW, tz=TZ)

    def test_usual_start_is_the_median_first_session_of_the_workdays(self):
        starts = [_at(5, 8.1), _at(5, 14), _at(6, 7.9), _at(7, 8.5), _at(8, 8.25), _at(10, 6)]

        plan = self._plan(self._windows(starts))

        # Monday to Thursday: 8:06, 7:54, 8:30 and 8:15; the median is 8:10:30, rounded to 8:10. Saturday the 10th is no workday.
        self.assertEqual(plan, {'start': 8 * HOUR + 10 * 60, 'reset': 13 * HOUR + 10 * 60, 'days': 4, 'blocked': 0, 'lead': None, 'suggested': None})

    def test_first_sessions_that_run_out_suggest_an_earlier_start(self):
        """Out of quota 1:20, 1:00 and 1:40 before the reset: starting 1:20 earlier moves the reset there."""
        starts = [_at(5, 8), _at(6, 8), _at(7, 8)]

        plan = self._plan(self._windows(starts, [80 * 60, 60 * 60, 100 * 60]))

        self.assertEqual((plan['blocked'], plan['lead'], plan['suggested']), (3, 80 * 60, 6 * HOUR + 40 * 60))

    def test_a_short_lead_is_not_worth_a_suggestion(self):
        plan = self._plan(self._windows([_at(5, 8), _at(6, 8), _at(7, 8)], [10 * 60, None, None]))

        self.assertEqual((plan['blocked'], plan['lead'], plan['suggested']), (1, 10 * 60, None))

    def test_no_suggestion_before_four(self):
        plan = self._plan(self._windows([_at(5, 5), _at(6, 5), _at(7, 5)], [90 * 60] * 3))

        self.assertEqual((plan['lead'], plan['suggested']), (90 * 60, None))

    def test_fewer_than_three_workdays_have_no_plan(self):
        self.assertIsNone(self._plan(self._windows([_at(5, 9), _at(6, 9), _at(10, 9)])))

    def test_windows_before_four_are_the_night_before(self):
        plan = self._plan(self._windows([_at(5, 2), _at(5, 9), _at(6, 9), _at(7, 9)]))

        self.assertEqual(plan['start'], 9 * HOUR)

    def test_without_workdays_every_day_counts(self):
        windows = self._windows([_at(10, 9), _at(11, 9), _at(12, 9)])

        self.assertIsNone(self._plan(windows))
        self.assertEqual(self._plan(windows, workdays=())['start'], 9 * HOUR)

    def test_a_session_that_has_not_reset_is_left_out(self):
        windows = self._windows([_at(12, 9), _at(13, 9), _at(14, 12)])

        self.assertIsNone(self._plan(windows))


if __name__ == '__main__':
    unittest.main()
