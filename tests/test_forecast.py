"""
Forecast Tests
==============

Unit tests for quota outlooks: session pace and its band, multi-day history
and average projections, status thresholds, blocked providers and cycle
splitting.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agentpulse.forecast import (
    Outlook, Sample,
    blocked_until, next_local_time, quota_cycles, quota_outlook, reset_timestamp, usage_outlooks, worst_outlook,
)

NOW = 2_000_000_000.0
HOUR = 3600.0
DAY = 24 * HOUR


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _outlook(status: str, *, reset_at: float = NOW + HOUR, limit_at: float | None = None) -> Outlook:
    return Outlook(status, 50.0, limit_at, reset_at, 50.0, 'pace')


class TestQuotaOutlookGuards(unittest.TestCase):
    """Windows that cannot be projected."""

    def test_unknown_field_has_no_outlook(self):
        self.assertIsNone(quota_outlook('iguana_necktie', 40.0, _iso(NOW + HOUR), [], now=NOW))

    def test_missing_reset_has_no_outlook(self):
        self.assertIsNone(quota_outlook('five_hour', 40.0, '', [], now=NOW))

    def test_naive_reset_has_no_outlook(self):
        self.assertIsNone(quota_outlook('five_hour', 40.0, '2033-05-18T04:33:20', [], now=NOW))

    def test_passed_reset_has_no_outlook(self):
        self.assertIsNone(quota_outlook('five_hour', 40.0, _iso(NOW - 60), [], now=NOW))

    def test_window_that_has_not_started_has_no_outlook(self):
        """A reset further away than the window length means the window lies in the future."""
        self.assertIsNone(quota_outlook('five_hour', 0.0, _iso(NOW + 6 * HOUR), [], now=NOW))

    def test_elapsed_share_of_the_window(self):
        outlook = quota_outlook('five_hour', 10.0, _iso(NOW + 4 * HOUR), [], now=NOW)

        self.assertAlmostEqual(outlook.elapsed_pct, 20.0)
        self.assertEqual(outlook.reset_at, NOW + 4 * HOUR)


class TestBlockedAndDisabled(unittest.TestCase):
    def test_reached_limit_is_blocked(self):
        outlook = quota_outlook('five_hour', 100.0, _iso(NOW + HOUR), [], now=NOW)

        self.assertEqual(outlook.status, 'blocked')
        self.assertEqual(outlook.forecast_pct, 100.0)
        self.assertIsNone(outlook.limit_at)
        self.assertEqual(outlook.method, 'reached')

    def test_usage_above_the_limit_is_blocked(self):
        self.assertEqual(quota_outlook('seven_day', 104.0, _iso(NOW + DAY), [], now=NOW).status, 'blocked')

    def test_without_forecasts_only_the_reached_limit_counts(self):
        busy = quota_outlook('five_hour', 95.0, _iso(NOW + 4 * HOUR), [], now=NOW, forecast=False)
        blocked = quota_outlook('five_hour', 100.0, _iso(NOW + 4 * HOUR), [], now=NOW, forecast=False)

        self.assertEqual((busy.status, busy.method, busy.forecast_pct), ('ok', 'none', None))
        self.assertEqual((blocked.status, blocked.forecast_pct), ('blocked', None))


class TestSessionPace(unittest.TestCase):
    """Hour-based windows follow the pace of the current window."""

    RESET = NOW + 2.5 * HOUR  # half of a five-hour window has passed

    def test_average_pace_below_the_limit_is_ok(self):
        outlook = quota_outlook('five_hour', 20.0, _iso(self.RESET), [], now=NOW)

        self.assertEqual(outlook.status, 'ok')
        self.assertEqual(outlook.method, 'pace')
        self.assertAlmostEqual(outlook.forecast_pct, 40.0)
        self.assertIsNone(outlook.limit_at)

    def test_projection_within_ten_points_of_the_limit_is_tight(self):
        outlook = quota_outlook('five_hour', 45.0, _iso(self.RESET), [], now=NOW)

        self.assertEqual(outlook.status, 'tight')
        self.assertAlmostEqual(outlook.forecast_pct, 90.0)

    def test_projection_just_below_the_tight_line_is_ok(self):
        self.assertEqual(quota_outlook('five_hour', 44.0, _iso(self.RESET), [], now=NOW).status, 'ok')

    def test_projection_past_the_limit_reports_when_it_runs_out(self):
        outlook = quota_outlook('five_hour', 60.0, _iso(self.RESET), [], now=NOW)

        self.assertEqual(outlook.status, 'limit')
        self.assertEqual(outlook.forecast_pct, 100.0)
        # 60 points in 2.5 hours is 24 points per hour; 40 points remain.
        self.assertAlmostEqual(outlook.limit_at, NOW + 40 / 24 * HOUR)
        self.assertLess(outlook.limit_at, self.RESET)

    def test_first_minutes_are_not_extrapolated_from_seconds(self):
        """Two minutes in, the average pace still counts ten minutes of window."""
        reset = NOW + 5 * HOUR - 120
        outlook = quota_outlook('five_hour', 1.0, _iso(reset), [], now=NOW)

        self.assertEqual(outlook.status, 'ok')
        self.assertAlmostEqual(outlook.forecast_pct, 1.0 + 6.0 * (reset - NOW) / HOUR)

    def test_a_pause_in_the_last_half_hour_slows_the_projection(self):
        """Recent pace 0, average 24 points per hour: blended to 9.6 points per hour."""
        samples = [Sample(NOW - 30 * 60, 60.0, self.RESET), Sample(NOW - 15 * 60, 60.0, self.RESET)]
        outlook = quota_outlook('five_hour', 60.0, _iso(self.RESET), samples, now=NOW)

        self.assertAlmostEqual(outlook.forecast_pct, 84.0)
        self.assertEqual(outlook.status, 'ok')

    def test_a_recent_burst_speeds_up_the_projection(self):
        samples = [Sample(NOW - 20 * 60, 30.0, self.RESET)]
        outlook = quota_outlook('five_hour', 40.0, _iso(self.RESET), samples, now=NOW)

        # Recent 30 points per hour, average 16: blended to 24.4 points per hour.
        self.assertEqual(outlook.status, 'limit')
        self.assertAlmostEqual(outlook.limit_at, NOW + 60 / 24.4 * HOUR)

    def test_recent_readings_closer_than_ten_minutes_are_ignored(self):
        samples = [Sample(NOW - 5 * 60, 10.0, self.RESET)]
        outlook = quota_outlook('five_hour', 40.0, _iso(self.RESET), samples, now=NOW)

        self.assertAlmostEqual(outlook.forecast_pct, 80.0)

    def test_readings_of_another_window_are_ignored(self):
        samples = [Sample(NOW - 20 * 60, 0.0, self.RESET - 5 * HOUR)]
        outlook = quota_outlook('five_hour', 40.0, _iso(self.RESET), samples, now=NOW)

        self.assertAlmostEqual(outlook.forecast_pct, 80.0)

    def test_reset_jitter_keeps_readings_in_the_window(self):
        samples = [Sample(NOW - 30 * 60, 60.0, self.RESET + 3)]
        outlook = quota_outlook('five_hour', 60.0, _iso(self.RESET), samples, now=NOW)

        self.assertAlmostEqual(outlook.forecast_pct, 60.0 + 0.4 * 24.0 * 2.5)

    def test_projection_at_the_end_of_the_day(self):
        outlook = quota_outlook('five_hour', 20.0, _iso(self.RESET), [], now=NOW, day_end=NOW + HOUR)

        self.assertAlmostEqual(outlook.day_end_pct, 28.0)

    def test_no_day_end_projection_after_the_reset(self):
        outlook = quota_outlook('five_hour', 20.0, _iso(self.RESET), [], now=NOW, day_end=self.RESET + HOUR)

        self.assertIsNone(outlook.day_end_pct)

    def test_day_end_projection_stops_at_the_limit(self):
        outlook = quota_outlook('five_hour', 60.0, _iso(self.RESET), [], now=NOW, day_end=NOW + 2 * HOUR)

        self.assertEqual(outlook.day_end_pct, 100.0)


class TestSessionBand(unittest.TestCase):
    """A session's forecast spans a slow and a fast pace around the likely one."""

    RESET = NOW + 2.5 * HOUR  # half of a five-hour window has passed

    def test_without_readings_the_band_is_the_average_pace(self):
        outlook = quota_outlook('five_hour', 60.0, _iso(self.RESET), [], now=NOW)

        self.assertEqual((outlook.forecast_low_pct, outlook.forecast_high_pct), (100.0, 100.0))
        self.assertAlmostEqual(outlook.limit_earliest, outlook.limit_at)
        self.assertAlmostEqual(outlook.limit_latest, outlook.limit_at)

    def test_the_likely_limit_lies_within_the_band(self):
        """Recent 30 points per hour, average 24: the limit falls between the two paces."""
        samples = [Sample(NOW - 20 * 60, 50.0, self.RESET)]
        outlook = quota_outlook('five_hour', 60.0, _iso(self.RESET), samples, now=NOW)

        self.assertAlmostEqual(outlook.limit_earliest, NOW + 40 / 30 * HOUR)
        self.assertAlmostEqual(outlook.limit_at, NOW + 40 / 27.6 * HOUR)
        self.assertAlmostEqual(outlook.limit_latest, NOW + 40 / 24 * HOUR)

    def test_a_slow_pace_that_lasts_until_the_reset_has_no_latest_limit(self):
        """Recent 30 points per hour, average 16: only the faster paces reach the limit."""
        samples = [Sample(NOW - 20 * 60, 30.0, self.RESET)]
        outlook = quota_outlook('five_hour', 40.0, _iso(self.RESET), samples, now=NOW)

        self.assertEqual(outlook.status, 'limit')
        self.assertAlmostEqual(outlook.forecast_low_pct, 80.0)
        self.assertEqual(outlook.forecast_high_pct, 100.0)
        self.assertAlmostEqual(outlook.limit_earliest, NOW + 2 * HOUR)
        self.assertIsNone(outlook.limit_latest)

    def test_a_pause_keeps_the_slow_end_at_the_current_usage(self):
        samples = [Sample(NOW - 30 * 60, 60.0, self.RESET), Sample(NOW - 15 * 60, 60.0, self.RESET)]
        outlook = quota_outlook('five_hour', 60.0, _iso(self.RESET), samples, now=NOW)

        self.assertEqual(outlook.status, 'ok')
        self.assertEqual(outlook.forecast_low_pct, 60.0)
        # The window's average of 24 points per hour is the fast end.
        self.assertAlmostEqual(outlook.limit_earliest, NOW + 40 / 24 * HOUR)
        self.assertIsNone(outlook.limit_latest)

    def test_the_fastest_quarter_hour_of_the_window_is_the_fast_end(self):
        """20 points in the quarter-hour after the first reading is 80 points per hour."""
        samples = [
            Sample(NOW - 2 * HOUR, 10.0, self.RESET),
            Sample(NOW - 1.75 * HOUR, 30.0, self.RESET),
            Sample(NOW - 30 * 60, 50.0, self.RESET),
        ]
        outlook = quota_outlook('five_hour', 55.0, _iso(self.RESET), samples, now=NOW)

        self.assertEqual(outlook.status, 'tight')
        self.assertAlmostEqual(outlook.forecast_low_pct, 80.0)
        self.assertAlmostEqual(outlook.limit_earliest, NOW + 45 / 80 * HOUR)
        self.assertIsNone(outlook.limit_at)

    def test_readings_closer_than_a_quarter_hour_are_not_a_fast_pace(self):
        """Ten points in five minutes would be 120 points per hour."""
        samples = [Sample(NOW - 2 * HOUR, 10.0, self.RESET), Sample(NOW - 115 * 60, 20.0, self.RESET)]
        outlook = quota_outlook('five_hour', 40.0, _iso(self.RESET), samples, now=NOW)

        self.assertAlmostEqual(outlook.forecast_high_pct, outlook.forecast_pct)

    def test_readings_of_another_window_do_not_widen_the_band(self):
        samples = [Sample(NOW - HOUR, 0.0, self.RESET - 5 * HOUR), Sample(NOW - 40 * 60, 90.0, self.RESET - 5 * HOUR)]
        outlook = quota_outlook('five_hour', 20.0, _iso(self.RESET), samples, now=NOW)

        self.assertAlmostEqual(outlook.forecast_low_pct, 40.0)
        self.assertAlmostEqual(outlook.forecast_high_pct, 40.0)

    def test_readings_after_now_are_ignored(self):
        samples = [Sample(NOW + 20 * 60, 99.0, self.RESET)]
        outlook = quota_outlook('five_hour', 20.0, _iso(self.RESET), samples, now=NOW)

        self.assertAlmostEqual(outlook.forecast_high_pct, 40.0)

    def test_only_sessions_have_a_band(self):
        weekly = quota_outlook('seven_day', 20.0, _iso(NOW + 3 * DAY), [], now=NOW)
        blocked = quota_outlook('five_hour', 100.0, _iso(self.RESET), [], now=NOW)
        disabled = quota_outlook('five_hour', 60.0, _iso(self.RESET), [], now=NOW, forecast=False)

        for outlook in (weekly, blocked, disabled):
            band = (outlook.forecast_low_pct, outlook.forecast_high_pct, outlook.limit_earliest, outlook.limit_latest)
            self.assertEqual(band, (None, None, None, None))


class TestMultiDayAverage(unittest.TestCase):
    """Day-based windows without comparable history average over at least one day."""

    def test_one_working_session_is_not_extrapolated_across_the_week(self):
        reset = NOW + 7 * DAY - 8.4 * HOUR
        outlook = quota_outlook('seven_day', 11.0, _iso(reset), [], now=NOW)

        self.assertEqual(outlook.method, 'average')
        self.assertEqual(outlook.status, 'ok')
        self.assertAlmostEqual(outlook.forecast_pct, 11.0 + 11.0 / 24 * (reset - NOW) / HOUR)

    def test_a_heavy_start_runs_out_before_the_reset(self):
        reset = NOW + 7 * DAY - 20 * HOUR
        outlook = quota_outlook('seven_day', 30.0, _iso(reset), [], now=NOW)

        self.assertEqual(outlook.status, 'limit')
        self.assertAlmostEqual(outlook.limit_at, NOW + 70 / (30 / 24) * HOUR)

    def test_after_the_first_day_the_real_average_counts(self):
        reset = NOW + 4 * DAY  # three days elapsed
        outlook = quota_outlook('seven_day', 30.0, _iso(reset), [], now=NOW)

        self.assertAlmostEqual(outlook.forecast_pct, 70.0)


class TestMultiDayHistory(unittest.TestCase):
    """Day-based windows follow what past cycles added after the same point."""

    PERIOD = 7 * DAY
    RESET = NOW + 4 * DAY  # three days into the current week

    def _past_cycle(self, weeks_ago: int, values: list[tuple[float, float]]) -> list[Sample]:
        reset = self.RESET - weeks_ago * self.PERIOD
        start = reset - self.PERIOD
        return [Sample(start + days * DAY, utilization, reset) for days, utilization in values]

    def test_median_growth_of_past_cycles(self):
        samples = (
            self._past_cycle(3, [(1, 10.0), (3, 20.0), (6, 60.0)])
            + self._past_cycle(2, [(1, 5.0), (3, 30.0), (6, 50.0)])
            + self._past_cycle(1, [(1, 8.0), (3, 25.0), (6, 95.0)])
        )
        outlook = quota_outlook('seven_day', 25.0, _iso(self.RESET), samples, now=NOW)

        # Growth after day three: 40, 20 and 70 points; the median is 40.
        self.assertEqual(outlook.method, 'history')
        self.assertEqual(outlook.cycles, 3)
        self.assertAlmostEqual(outlook.forecast_pct, 65.0)
        self.assertEqual(outlook.status, 'ok')
        self.assertIsNone(outlook.limit_at)

    def test_history_can_project_the_limit(self):
        samples = self._past_cycle(1, [(3, 10.0), (6, 90.0)])
        outlook = quota_outlook('seven_day', 40.0, _iso(self.RESET), samples, now=NOW)

        self.assertEqual(outlook.status, 'limit')
        self.assertIsNone(outlook.limit_at)

    def test_cycles_first_seen_after_the_same_point_are_skipped(self):
        samples = self._past_cycle(1, [(5, 40.0), (6, 60.0)])
        outlook = quota_outlook('seven_day', 25.0, _iso(self.RESET), samples, now=NOW)

        self.assertEqual(outlook.method, 'average')

    def test_the_current_cycle_is_not_its_own_history(self):
        samples = [Sample(NOW - 2 * DAY, 5.0, self.RESET), Sample(NOW - DAY, 15.0, self.RESET)]
        outlook = quota_outlook('seven_day', 25.0, _iso(self.RESET), samples, now=NOW)

        self.assertEqual(outlook.method, 'average')

    def test_day_end_growth_of_past_cycles(self):
        samples = self._past_cycle(1, [(3, 20.0), (3.25, 26.0), (6, 60.0)])
        outlook = quota_outlook('seven_day', 25.0, _iso(self.RESET), samples, now=NOW, day_end=NOW + 0.25 * DAY)

        self.assertAlmostEqual(outlook.day_end_pct, 31.0)


class TestUsageOutlooks(unittest.TestCase):
    def test_every_quota_field_gets_an_outlook(self):
        usage = {
            'five_hour': {'utilization': 20.0, 'resets_at': _iso(NOW + 2.5 * HOUR)},
            'seven_day_sonnet': {'utilization': 10.0, 'resets_at': _iso(NOW + 4 * DAY)},
            'seven_day_opus': None,
            'extra_usage': {'utilization': 50.0, 'resets_at': _iso(NOW + DAY), 'is_enabled': True},
            'iguana_necktie': {'utilization': 5.0},
            'error_note': 'text',
        }

        outlooks = usage_outlooks(usage, {}, now=NOW)

        self.assertEqual(sorted(outlooks), ['five_hour', 'seven_day_sonnet'])

    def test_history_of_each_field_is_used(self):
        """Without history the average pace runs out; the flat last half hour keeps it below the limit."""
        reset = NOW + 2.5 * HOUR
        usage = {'five_hour': {'utilization': 60.0, 'resets_at': _iso(reset)}}
        series = {'five_hour': [Sample(NOW - 30 * 60, 60.0, reset)]}

        self.assertEqual(usage_outlooks(usage, {}, now=NOW)['five_hour'].status, 'limit')
        self.assertEqual(usage_outlooks(usage, series, now=NOW)['five_hour'].status, 'ok')

    def test_null_reset_time_is_skipped(self):
        usage = {'five_hour': {'utilization': 0.0, 'resets_at': None}}

        self.assertEqual(usage_outlooks(usage, {}, now=NOW), {})


class TestWorstOutlook(unittest.TestCase):
    def test_empty_has_no_worst(self):
        self.assertIsNone(worst_outlook([]))

    def test_most_severe_status_wins(self):
        tight = _outlook('tight')
        limit = _outlook('limit', limit_at=NOW + HOUR)

        self.assertIs(worst_outlook([tight, limit, _outlook('ok')]), limit)

    def test_earlier_limit_wins_among_limits(self):
        later = _outlook('limit', limit_at=NOW + 3 * HOUR)
        sooner = _outlook('limit', limit_at=NOW + HOUR)

        self.assertIs(worst_outlook([later, sooner]), sooner)

    def test_longest_block_wins_among_reached_limits(self):
        session = _outlook('blocked', reset_at=NOW + HOUR)
        weekly = _outlook('blocked', reset_at=NOW + 3 * DAY)

        self.assertIs(worst_outlook([session, weekly]), weekly)


class TestBlockedUntil(unittest.TestCase):
    def test_not_blocked_below_every_limit(self):
        usage = {'five_hour': {'utilization': 99.0, 'resets_at': _iso(NOW + HOUR)}}

        self.assertIsNone(blocked_until(usage, now=NOW))

    def test_blocked_until_the_reset(self):
        usage = {'five_hour': {'utilization': 100.0, 'resets_at': _iso(NOW + HOUR)}}

        self.assertEqual(blocked_until(usage, now=NOW), NOW + HOUR)

    def test_every_reached_limit_has_to_reset(self):
        usage = {
            'five_hour': {'utilization': 100.0, 'resets_at': _iso(NOW + HOUR)},
            'seven_day': {'utilization': 100.0, 'resets_at': _iso(NOW + 2 * DAY)},
        }

        self.assertEqual(blocked_until(usage, now=NOW), NOW + 2 * DAY)

    def test_a_passed_reset_no_longer_blocks(self):
        usage = {'five_hour': {'utilization': 100.0, 'resets_at': _iso(NOW - 60)}}

        self.assertIsNone(blocked_until(usage, now=NOW))

    def test_reached_limit_without_a_reset_cannot_be_counted_down(self):
        usage = {
            'five_hour': {'utilization': 100.0, 'resets_at': _iso(NOW + HOUR)},
            'seven_day': {'utilization': 100.0, 'resets_at': ''},
        }

        self.assertIsNone(blocked_until(usage, now=NOW))

    def test_extra_usage_and_null_fields_are_ignored(self):
        usage = {
            'extra_usage': {'utilization': 100.0, 'resets_at': _iso(NOW + HOUR)},
            'seven_day_opus': None,
            'five_hour': {'utilization': None, 'resets_at': _iso(NOW + HOUR)},
        }

        self.assertIsNone(blocked_until(usage, now=NOW))


class TestQuotaCycles(unittest.TestCase):
    def test_reset_jitter_stays_in_one_cycle(self):
        cycles = quota_cycles([Sample(1.0, 5.0, 1000.0), Sample(2.0, 6.0, 1004.0), Sample(3.0, 1.0, 20000.0)])

        self.assertEqual([len(cycle.samples) for cycle in cycles], [2, 1])
        self.assertEqual(cycles[0].reset, 1004.0)

    def test_readings_without_reset_belong_to_no_cycle(self):
        cycles = quota_cycles([Sample(1.0, 0.0, None), Sample(2.0, 3.0, 1000.0)])

        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0].samples[0].ts, 2.0)

    def test_readings_are_ordered_by_time(self):
        cycles = quota_cycles([Sample(2.0, 6.0, 1000.0), Sample(1.0, 5.0, 1000.0)])

        self.assertEqual([sample.ts for sample in cycles[0].samples], [1.0, 2.0])


class TestResetTimestamp(unittest.TestCase):
    def test_parses_aware_iso_time(self):
        self.assertEqual(reset_timestamp(_iso(NOW)), NOW)

    def test_rejects_missing_invalid_and_naive_values(self):
        for value in (None, '', 'soon', '2033-05-18T04:33:20', 42, {'value': 1}):
            self.assertIsNone(reset_timestamp(value), value)


class TestNextLocalTime(unittest.TestCase):
    def test_later_today(self):
        now = datetime(2026, 1, 10, 17, 0).timestamp()

        self.assertEqual(next_local_time('18:00', now=now), datetime(2026, 1, 10, 18, 0).timestamp())

    def test_tomorrow_once_passed(self):
        now = datetime(2026, 1, 10, 19, 0).timestamp()

        self.assertEqual(next_local_time('18:00', now=now), datetime(2026, 1, 11, 18, 0).timestamp())

    def test_exactly_now_means_tomorrow(self):
        now = datetime(2026, 1, 10, 18, 0).timestamp()

        self.assertEqual(next_local_time('18:00', now=now), datetime(2026, 1, 11, 18, 0).timestamp())

    def test_invalid_times(self):
        for value in ('', '18', '24:00', '18:60', 'ab:cd', '-1:00'):
            self.assertIsNone(next_local_time(value, now=NOW), value)


if __name__ == '__main__':
    unittest.main()
