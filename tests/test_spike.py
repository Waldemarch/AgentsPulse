"""Tests for the runaway alert (agentpulse/spike.py)."""
from __future__ import annotations

import unittest

from agentpulse.forecast import Sample
from agentpulse.spike import MIN_POINTS, SPIKE_FACTOR, current_growth, find_spike

NOW = 2_000_000_000.0
MINUTE = 60
HOUR = 3600
PERIOD = 5 * HOUR
# The session that is running now began two hours ago.
RESET = NOW + 3 * HOUR


def _window(reset: float, readings: list[tuple[float, float]]) -> list[Sample]:
    """Readings of the five-hour window that resets at ``reset``, at (minutes into it, utilization)."""
    start = reset - PERIOD
    return [Sample(start + minutes * MINUTE, utilization, reset) for minutes, utilization in readings]


def _steady(reset: float, per_half_hour: float) -> list[Sample]:
    """A window that grows ``per_half_hour`` points every half hour, read every five minutes for two and a half hours."""
    return _window(reset, [(minutes, per_half_hour * minutes / 30) for minutes in range(0, 151, 5)])


def _history(per_half_hour: float = 10.0, sessions: int = 4) -> list[Sample]:
    samples: list[Sample] = []
    for index in range(sessions, 0, -1):
        samples += _steady(RESET - index * (PERIOD + HOUR), per_half_hour)
    return samples


def _running(per_half_hour: float) -> list[Sample]:
    """The window running now: 120 minutes in, growing ``per_half_hour`` per half hour."""
    return _window(RESET, [(minutes, per_half_hour * minutes / 30) for minutes in range(0, 121, 5)])


class TestCurrentGrowth(unittest.TestCase):
    """Tests for current_growth()."""

    def test_growth_over_the_last_half_hour(self):
        # 5 points every five minutes: 30 points per half hour.
        self.assertAlmostEqual(current_growth(_running(30.0), now=NOW), 30.0)

    def test_a_pause_shows_no_growth(self):
        samples = _window(RESET, [(60, 20.0), (90, 20.0), (105, 20.0), (120, 20.0)])

        self.assertEqual(current_growth(samples, now=NOW), 0.0)

    def test_short_spans_are_not_extrapolated(self):
        samples = _window(RESET, [(115, 10.0), (120, 40.0)])

        self.assertIsNone(current_growth(samples, now=NOW))

    def test_a_pace_measured_over_ten_minutes_is_scaled_to_a_half_hour(self):
        samples = _window(RESET, [(110, 10.0), (120, 20.0)])

        self.assertAlmostEqual(current_growth(samples, now=NOW), 30.0)

    def test_a_stale_newest_reading_says_nothing_about_now(self):
        self.assertIsNone(current_growth(_running(30.0), now=NOW + 15 * MINUTE))

    def test_a_window_that_has_reset_is_not_current(self):
        self.assertIsNone(current_growth(_running(30.0), now=RESET + MINUTE))

    def test_only_the_newest_window_counts(self):
        samples = _history(60.0) + _window(RESET, [(100, 5.0), (110, 5.0), (120, 5.0)])

        self.assertEqual(current_growth(samples, now=NOW), 0.0)

    def test_readings_after_now_are_ignored(self):
        samples = _running(30.0) + [Sample(NOW + 5 * MINUTE, 100.0, RESET)]

        self.assertAlmostEqual(current_growth(samples, now=NOW), 30.0)

    def test_no_readings(self):
        self.assertIsNone(current_growth([], now=NOW))


class TestFindSpike(unittest.TestCase):
    """Tests for find_spike()."""

    def test_a_pace_far_above_the_history_is_a_spike(self):
        spike = find_spike(_history(10.0) + _running(30.0), now=NOW)

        self.assertIsNotNone(spike)
        self.assertAlmostEqual(spike.growth, 30.0)
        self.assertAlmostEqual(spike.typical, 10.0)
        self.assertEqual((spike.utilization, spike.resets_at), (120.0, RESET))

    def test_the_busiest_half_hours_of_the_history_set_the_bar(self):
        """The 95th percentile of 10 and 12 points per half hour is above the mean, so 17 points is no spike."""
        samples = _history(10.0, sessions=2) + _steady(RESET - 3 * (PERIOD + HOUR), 12.0) + _steady(RESET - 4 * (PERIOD + HOUR), 12.0)

        self.assertIsNone(find_spike(samples + _running(17.0), now=NOW))
        self.assertIsNotNone(find_spike(samples + _running(19.0), now=NOW))

    def test_a_pace_below_the_factor_is_no_spike(self):
        pace = 10.0 * SPIKE_FACTOR - 1.0
        self.assertGreaterEqual(pace, MIN_POINTS - 1.0)

        self.assertIsNone(find_spike(_history(20.0) + _running(pace), now=NOW))

    def test_a_quiet_history_needs_a_real_burst(self):
        """Five points per half hour is triple the usual two, but far below the minimum of a spike."""
        self.assertIsNone(find_spike(_history(2.0) + _running(5.0), now=NOW))
        self.assertIsNone(find_spike(_history(2.0) + _running(MIN_POINTS - 1), now=NOW))
        self.assertIsNotNone(find_spike(_history(2.0) + _running(MIN_POINTS), now=NOW))

    def test_no_reference_before_three_earlier_sessions(self):
        self.assertIsNone(find_spike(_history(10.0, sessions=2) + _running(60.0), now=NOW))
        self.assertIsNotNone(find_spike(_history(10.0, sessions=3) + _running(60.0), now=NOW))

    def test_idle_sessions_are_no_reference(self):
        idle = _window(RESET - 6 * HOUR, [(0, 0.0), (60, 0.0), (120, 0.0)])
        samples = _history(10.0, sessions=2) + idle + _running(60.0)

        self.assertIsNone(find_spike(samples, now=NOW))

    def test_a_session_with_few_readings_is_no_reference(self):
        samples = []
        for index in range(3, 0, -1):
            samples += _window(RESET - index * (PERIOD + HOUR), [(0, 0.0), (30, 10.0), (60, 20.0), (90, 30.0)])

        self.assertIsNone(find_spike(samples + _running(60.0), now=NOW))

    def test_the_running_window_is_not_its_own_reference(self):
        self.assertIsNone(find_spike(_running(60.0), now=NOW))

    def test_no_spike_without_current_readings(self):
        self.assertIsNone(find_spike(_history(10.0), now=NOW))
        self.assertIsNone(find_spike([], now=NOW))


if __name__ == '__main__':
    unittest.main()
