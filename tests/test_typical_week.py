"""Tests for this week against the typical week (agentpulse/typical_week.py)."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agentpulse.forecast import Sample, quota_outlook
from agentpulse.typical_week import typical_week, typical_weeks

NOW = 2_000_000_000.0
HOUR = 3600
DAY = 24 * HOUR
PERIOD = 7 * DAY
# Three days into the current week.
RESET = NOW + 4 * DAY
START = RESET - PERIOD


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _cycle(weeks_ago: int, values: list[tuple[float, float]]) -> list[Sample]:
    """Readings of the week that reset ``weeks_ago`` weeks before the current one, at (days into it, utilization)."""
    reset = RESET - weeks_ago * PERIOD
    return [Sample(reset - PERIOD + days * DAY, utilization, reset) for days, utilization in values]


def _history() -> list[Sample]:
    return (
        _cycle(3, [(1, 10.0), (3, 20.0), (6, 60.0)])
        + _cycle(2, [(1, 5.0), (3, 30.0), (6, 50.0)])
        + _cycle(1, [(1, 8.0), (3, 25.0), (6, 95.0)])
        + _cycle(0, [(1, 12.0), (3, 25.0)])
    )


def _value_at(points: list[list[float]], age: float) -> float:
    return next(value for point_age, value in points if point_age == age)


class TestTypicalWeek(unittest.TestCase):
    """Tests for typical_week()."""

    def test_every_cycle_is_placed_by_how_far_into_its_window_it_came(self):
        week = typical_week('seven_day', _history(), now=NOW)

        self.assertEqual((week['start'], week['age'], week['period_seconds']), (START, 3 * DAY, PERIOD))
        self.assertEqual(week['current'], [[DAY, 12.0], [3 * DAY, 25.0]])
        self.assertEqual(week['past'][0], [[DAY, 10.0], [3 * DAY, 20.0], [6 * DAY, 60.0]])
        self.assertEqual(len(week['past']), 3)
        self.assertEqual((week['field'], week['utilization']), ('seven_day', 25.0))

    def test_each_hour_keeps_its_highest_reading(self):
        samples = _cycle(0, [(1, 12.0), (1 + 20 / 1440, 14.0), (1 + 40 / 1440, 13.0), (3, 25.0)])

        week = typical_week('seven_day', samples, now=NOW)

        self.assertEqual(week['current'][0], [DAY + 20 * 60, 14.0])
        self.assertEqual(len(week['current']), 2)

    def test_typical_is_the_median_of_the_past_cycles_at_every_hour(self):
        week = typical_week('seven_day', _history(), now=NOW)

        self.assertEqual(_value_at(week['typical'], DAY), 8.0)
        self.assertEqual(_value_at(week['typical'], 3 * DAY), 25.0)
        self.assertEqual(_value_at(week['typical'], PERIOD), 60.0)

    def test_typical_counts_only_the_cycles_observed_by_then(self):
        samples = _cycle(2, [(1, 10.0), (6, 60.0)]) + _cycle(1, [(4, 40.0), (6, 70.0)]) + _cycle(0, [(3, 25.0)])

        week = typical_week('seven_day', samples, now=NOW)

        self.assertEqual(_value_at(week['typical'], 2 * DAY), 10.0)
        self.assertEqual(_value_at(week['typical'], 5 * DAY), 25.0)
        self.assertEqual(week['typical'][0][0], DAY)

    def test_forecast_follows_the_past_cycles_from_today_on(self):
        """Growth after day three: 40, 20 and 70 points, as in the quota's outlook."""
        week = typical_week('seven_day', _history(), now=NOW)

        forecast = week['forecast']
        self.assertEqual([forecast[end][0] for end in ('likely', 'low', 'high')], [[3 * DAY, 25.0]] * 3)
        self.assertEqual([forecast[end][-1] for end in ('likely', 'low', 'high')], [[PERIOD, 65.0], [PERIOD, 45.0], [PERIOD, 95.0]])
        outlook = quota_outlook('seven_day', 25.0, _iso(RESET), _history(), now=NOW)
        self.assertEqual((outlook.forecast_pct, outlook.forecast_low_pct, outlook.forecast_high_pct), (65.0, 45.0, 95.0))

    def test_forecast_points_follow_the_hours_of_the_window(self):
        week = typical_week('seven_day', _history(), now=NOW + 1800)

        ages = [age for age, _value in week['forecast']['likely']]
        self.assertEqual(ages[:3], [3 * DAY + 1800, 3 * DAY + HOUR, 3 * DAY + 2 * HOUR])
        self.assertEqual(ages[-1], PERIOD)

    def test_forecast_stops_at_the_limit(self):
        samples = _cycle(1, [(3, 10.0), (6, 90.0)]) + _cycle(0, [(3, 40.0)])

        week = typical_week('seven_day', samples, now=NOW)

        self.assertEqual(week['forecast']['likely'][-1], [PERIOD, 100.0])

    def test_no_forecast_without_a_past_cycle_observed_by_now(self):
        samples = _cycle(1, [(5, 40.0), (6, 60.0)]) + _cycle(0, [(3, 25.0)])

        week = typical_week('seven_day', samples, now=NOW)

        self.assertEqual(week['forecast'], {'likely': [], 'low': [], 'high': []})
        self.assertTrue(week['typical'])

    def test_first_week_has_no_past(self):
        week = typical_week('seven_day', _cycle(0, [(1, 12.0), (3, 25.0)]), now=NOW)

        self.assertEqual((week['past'], week['typical']), ([], []))
        self.assertEqual(week['forecast']['likely'], [])

    def test_readings_after_now_are_ignored(self):
        samples = _history() + [Sample(NOW + HOUR, 90.0, RESET)]

        week = typical_week('seven_day', samples, now=NOW)

        self.assertEqual(week['utilization'], 25.0)

    def test_only_windows_measured_in_days_have_a_typical_week(self):
        session = [Sample(NOW - HOUR, 20.0, NOW + 4 * HOUR)]

        self.assertIsNone(typical_week('five_hour', session, now=NOW))
        self.assertIsNone(typical_week('iguana_necktie', _history(), now=NOW))

    def test_no_typical_week_without_a_running_cycle(self):
        self.assertIsNone(typical_week('seven_day', _cycle(1, [(1, 10.0), (6, 60.0)]), now=NOW))
        self.assertIsNone(typical_week('seven_day', [], now=NOW))


class TestTypicalWeeks(unittest.TestCase):
    """Tests for typical_weeks()."""

    def test_each_provider_uses_its_longest_base_window(self):
        series = {
            'claude': {
                'five_hour': [Sample(NOW - HOUR, 20.0, NOW + 4 * HOUR)],
                'seven_day': _history(),
                'seven_day_sonnet': _cycle(0, [(3, 80.0)]),
            },
            'kimi': {'five_hour': [Sample(NOW - HOUR, 20.0, NOW + 4 * HOUR)]},
        }

        weeks = typical_weeks(series, now=NOW)

        self.assertEqual([(week['id'], week['field']) for week in weeks], [('claude', 'seven_day')])
        self.assertEqual(weeks[0]['utilization'], 25.0)

    def test_no_history_has_no_weeks(self):
        self.assertEqual(typical_weeks({}, now=NOW), [])


if __name__ == '__main__':
    unittest.main()
