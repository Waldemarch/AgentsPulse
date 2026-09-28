"""
Usage Statistics Tests
======================

Unit tests for consumption buckets and the weekday-by-hour heatmap.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agentpulse.forecast import Sample
from agentpulse.usage_stats import bucket_starts, consumption_buckets, consumption_field, heatmap_cells, usage_increments

TZ = timezone(timedelta(hours=2))
HOUR = 3600.0
DAY = 24 * HOUR
# Wednesday 2026-01-14 12:30 local time.
NOW = datetime(2026, 1, 14, 12, 30, tzinfo=TZ).timestamp()
WEEK_RESET = NOW + 3 * DAY


def _at(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 1, day, hour, minute, tzinfo=TZ).timestamp()


class TestConsumptionField(unittest.TestCase):
    def test_longest_base_window_wins(self):
        self.assertEqual(consumption_field(['five_hour', 'seven_day', 'seven_day_sonnet']), 'seven_day')

    def test_variants_and_unknown_fields_are_skipped(self):
        self.assertEqual(consumption_field(['seven_day_opus', 'iguana_necktie', 'five_hour']), 'five_hour')

    def test_nothing_usable(self):
        self.assertIsNone(consumption_field([]))
        self.assertIsNone(consumption_field(['seven_day_opus', 'iguana_necktie']))


class TestUsageIncrements(unittest.TestCase):
    def test_first_reading_only_sets_the_baseline(self):
        self.assertEqual(usage_increments([Sample(1.0, 40.0, WEEK_RESET)]), [])

    def test_growth_within_one_window(self):
        samples = [Sample(1.0, 10.0, WEEK_RESET), Sample(2.0, 14.5, WEEK_RESET + 2), Sample(3.0, 14.5, WEEK_RESET)]

        self.assertEqual(usage_increments(samples), [(2.0, 4.5), (3.0, 0.0)])

    def test_a_drop_within_a_window_is_not_negative_usage(self):
        samples = [Sample(1.0, 10.0, WEEK_RESET), Sample(2.0, 8.0, WEEK_RESET)]

        self.assertEqual(usage_increments(samples), [(2.0, 0.0)])

    def test_first_reading_of_a_new_window_counts_in_full(self):
        old_reset = NOW - HOUR
        samples = [Sample(NOW - 2 * HOUR, 90.0, old_reset), Sample(NOW, 6.0, WEEK_RESET)]

        self.assertEqual(usage_increments(samples), [(NOW, 6.0)])

    def test_a_rescheduled_reset_is_the_same_window(self):
        """The reset moved while the old window still had time left: only the growth counts."""
        samples = [Sample(NOW - HOUR, 30.0, NOW + 2 * HOUR), Sample(NOW, 32.0, NOW + 3 * HOUR)]

        self.assertEqual(usage_increments(samples), [(NOW, 2.0)])

    def test_reading_without_a_window_consumes_nothing(self):
        samples = [Sample(1.0, 20.0, WEEK_RESET), Sample(2.0, 0.0, None), Sample(3.0, 0.0, None)]

        self.assertEqual(usage_increments(samples), [(2.0, 0.0), (3.0, 0.0)])

    def test_new_window_after_an_idle_stretch(self):
        samples = [Sample(1.0, 0.0, None), Sample(2.0, 3.0, WEEK_RESET)]

        self.assertEqual(usage_increments(samples), [(2.0, 3.0)])


class TestBucketStarts(unittest.TestCase):
    def test_hours_end_with_the_current_hour(self):
        starts = bucket_starts('hour', 3, now=NOW, tz=TZ)

        self.assertEqual(starts, [_at(14, 10), _at(14, 11), _at(14, 12)])

    def test_days_start_at_local_midnight(self):
        starts = bucket_starts('day', 2, now=NOW, tz=TZ)

        self.assertEqual(starts, [_at(13, 0), _at(14, 0)])

    def test_system_time_zone_by_default(self):
        starts = bucket_starts('day', 1, now=NOW)
        local = datetime.fromtimestamp(NOW).replace(hour=0, minute=0, second=0, microsecond=0)

        self.assertEqual(starts, [local.timestamp()])


class TestConsumptionBuckets(unittest.TestCase):
    def test_usage_lands_in_the_day_of_the_later_reading(self):
        samples = [
            Sample(_at(12, 9), 1.0, WEEK_RESET),
            Sample(_at(12, 17), 6.0, WEEK_RESET),
            Sample(_at(13, 10), 9.0, WEEK_RESET),
            Sample(_at(14, 11), 15.0, WEEK_RESET),
        ]

        totals = consumption_buckets(samples, unit='day', count=3, now=NOW, tz=TZ)

        self.assertEqual(totals, [5.0, 3.0, 6.0])

    def test_usage_before_the_first_bucket_and_after_now_is_ignored(self):
        samples = [Sample(_at(10, 9), 1.0, WEEK_RESET), Sample(_at(11, 9), 20.0, WEEK_RESET), Sample(NOW + HOUR, 30.0, WEEK_RESET)]

        totals = consumption_buckets(samples, unit='day', count=2, now=NOW, tz=TZ)

        self.assertEqual(totals, [0.0, 0.0])

    def test_hourly_buckets(self):
        samples = [Sample(_at(14, 10, 5), 2.0, WEEK_RESET), Sample(_at(14, 10, 50), 3.0, WEEK_RESET), Sample(_at(14, 12, 10), 7.5, WEEK_RESET)]

        totals = consumption_buckets(samples, unit='hour', count=3, now=NOW, tz=TZ)

        self.assertEqual(totals, [1.0, 0.0, 4.5])

    def test_no_samples(self):
        self.assertEqual(consumption_buckets([], unit='hour', count=24, now=NOW, tz=TZ), [0.0] * 24)


class TestHeatmapCells(unittest.TestCase):
    def test_empty_history_is_all_zero(self):
        cells = heatmap_cells([], now=NOW, tz=TZ)

        self.assertEqual(len(cells), 7)
        self.assertTrue(all(len(row) == 24 and not any(row) for row in cells))

    def test_usage_lands_in_its_weekday_and_hour(self):
        samples = [Sample(_at(12, 9, 0), 0.0, WEEK_RESET), Sample(_at(12, 10, 15), 4.0, WEEK_RESET)]

        cells = heatmap_cells(samples, now=NOW, tz=TZ)

        # 2026-01-12 is a Monday: row 0, hour 10.
        self.assertEqual(cells[0][10], 4.0)
        self.assertEqual(sum(sum(row) for row in cells), 4.0)

    def test_each_slot_is_averaged_over_the_weeks_it_occurred(self):
        samples = [
            Sample(_at(5, 9), 0.0, WEEK_RESET),
            Sample(_at(5, 10, 30), 6.0, WEEK_RESET),
            Sample(_at(12, 10, 30), 8.0, WEEK_RESET),
        ]

        cells = heatmap_cells(samples, now=NOW, tz=TZ)

        # Monday 10:00 occurred twice since the first reading; 6 + 2 points in total.
        self.assertEqual(cells[0][10], 4.0)

    def test_older_usage_than_the_period_is_left_out(self):
        samples = [Sample(NOW - 40 * DAY, 0.0, WEEK_RESET), Sample(NOW - 35 * DAY, 9.0, WEEK_RESET), Sample(NOW - HOUR, 9.0, WEEK_RESET)]

        cells = heatmap_cells(samples, now=NOW, days=28, tz=TZ)

        self.assertEqual(sum(sum(row) for row in cells), 0.0)


if __name__ == '__main__':
    unittest.main()
