"""Tests for the daily budget of weekly quotas (agentpulse/budget.py)."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agentpulse.budget import DailyBudget, daily_budget, usage_budgets
from agentpulse.forecast import Sample

TZ = timezone(timedelta(hours=1))
HOUR = 3600
DAY = 24 * HOUR
WORKWEEK = (0, 1, 2, 3, 4)
# Wednesday 14 January 2026, 15:00 local time.
NOW = datetime(2026, 1, 14, 15, 0, tzinfo=TZ).timestamp()
MIDNIGHT = datetime(2026, 1, 14, tzinfo=TZ).timestamp()
# The weekly window resets on Monday 19 January at 12:00 and began a week earlier.
RESET = datetime(2026, 1, 19, 12, 0, tzinfo=TZ).timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _budget(utilization: float, samples: list[Sample], *, now: float = NOW, reset: float = RESET, workdays=WORKWEEK, field: str = 'seven_day'):
    return daily_budget(field, utilization, _iso(reset), samples, now=now, workdays=workdays, tz=TZ)


class TestDailyBudget(unittest.TestCase):
    """Tests for daily_budget()."""

    def test_what_is_left_is_shared_by_today_and_the_remaining_workdays(self):
        samples = [Sample(MIDNIGHT - HOUR, 40.0, RESET), Sample(NOW - HOUR, 50.0, RESET)]

        budget = _budget(52.0, samples)

        # Wednesday, Thursday, Friday and Monday, whose day starts before the reset.
        self.assertEqual(budget, DailyBudget(used=12.0, allowance=15.0, days=4))

    def test_every_day_counts_when_every_day_is_a_workday(self):
        budget = _budget(52.0, [Sample(MIDNIGHT - HOUR, 40.0, RESET)], workdays=range(7))

        self.assertEqual(budget.days, 6)
        self.assertAlmostEqual(budget.allowance, 10.0)

    def test_today_counts_even_when_it_is_not_a_workday(self):
        saturday = datetime(2026, 1, 17, 11, 0, tzinfo=TZ).timestamp()
        start = datetime(2026, 1, 17, tzinfo=TZ).timestamp()

        budget = _budget(70.0, [Sample(start - HOUR, 70.0, RESET)], now=saturday)

        self.assertEqual((budget.days, budget.allowance, budget.used), (2, 15.0, 0.0))

    def test_a_reset_later_today_leaves_everything_to_today(self):
        reset = NOW + 3 * HOUR

        budget = _budget(80.0, [Sample(MIDNIGHT - HOUR, 70.0, reset)], reset=reset)

        self.assertEqual(budget, DailyBudget(used=10.0, allowance=30.0, days=1))

    def test_a_window_that_began_today_started_empty(self):
        reset = NOW - 2 * HOUR + 7 * DAY

        budget = _budget(9.0, [Sample(NOW - HOUR, 5.0, reset)], reset=reset)

        self.assertEqual(budget.used, 9.0)

    def test_first_reading_of_today_is_the_start_without_an_earlier_one(self):
        budget = _budget(50.0, [Sample(MIDNIGHT + 8 * HOUR, 44.0, RESET)])

        self.assertEqual(budget.used, 6.0)
        self.assertAlmostEqual(budget.allowance, 56.0 / 4)

    def test_without_any_reading_today_starts_now(self):
        budget = _budget(50.0, [])

        self.assertEqual(budget.used, 0.0)
        self.assertAlmostEqual(budget.allowance, 50.0 / 4)

    def test_readings_of_the_previous_window_are_ignored(self):
        samples = [Sample(MIDNIGHT - 2 * DAY, 90.0, RESET - 7 * DAY), Sample(MIDNIGHT - HOUR, 20.0, RESET)]

        self.assertEqual(_budget(30.0, samples).used, 10.0)

    def test_a_used_up_quota_leaves_nothing(self):
        budget = _budget(100.0, [Sample(MIDNIGHT - HOUR, 100.0, RESET)])

        self.assertEqual((budget.used, budget.allowance), (0.0, 0.0))

    def test_no_budget_without_workdays(self):
        self.assertIsNone(_budget(50.0, [], workdays=()))

    def test_no_budget_for_windows_shorter_than_two_days(self):
        self.assertIsNone(_budget(50.0, [], field='five_hour', reset=NOW + 2 * HOUR))
        self.assertIsNone(_budget(50.0, [], field='one_day', reset=NOW + 2 * HOUR))

    def test_no_budget_without_a_future_reset(self):
        self.assertIsNone(daily_budget('seven_day', 50.0, '', [], now=NOW, workdays=WORKWEEK, tz=TZ))
        self.assertIsNone(_budget(50.0, [], reset=NOW - HOUR))


class TestUsageBudgets(unittest.TestCase):
    """Tests for usage_budgets()."""

    def test_only_the_longest_base_window_gets_a_budget(self):
        usage = {
            'five_hour': {'utilization': 30.0, 'resets_at': _iso(NOW + 2 * HOUR)},
            'seven_day': {'utilization': 52.0, 'resets_at': _iso(RESET)},
            'seven_day_sonnet': {'utilization': 80.0, 'resets_at': _iso(RESET)},
            'extra_usage': {'is_enabled': True, 'utilization': 10.0},
        }
        series = {'seven_day': [Sample(MIDNIGHT - HOUR, 40.0, RESET)]}

        budgets = usage_budgets(usage, series, now=NOW, workdays=WORKWEEK, tz=TZ)

        self.assertEqual(budgets, {'seven_day': DailyBudget(used=12.0, allowance=15.0, days=4)})

    def test_provider_without_a_multi_day_window_has_none(self):
        usage = {'five_hour': {'utilization': 30.0, 'resets_at': _iso(NOW + 2 * HOUR)}}

        self.assertEqual(usage_budgets(usage, {}, now=NOW, workdays=WORKWEEK, tz=TZ), {})

    def test_null_and_error_entries_are_skipped(self):
        usage = {'seven_day': None, 'error': 'server down'}

        self.assertEqual(usage_budgets(usage, {}, now=NOW, workdays=WORKWEEK, tz=TZ), {})


if __name__ == '__main__':
    unittest.main()
