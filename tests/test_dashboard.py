"""Tests for local dashboard history and payload helpers."""
from __future__ import annotations

import http.client
import json
import re
import socket
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentpulse.dashboard import (
    DashboardHistory, DashboardServer,
    _apply_autostart, _autostart_enabled, _cycle_trend, _dashboard_i18n, _history_payload, _needs_restart, _status_payload,
)
from agentpulse.forecast import Sample, next_local_time
from agentpulse.providers import SECONDARY_PROVIDERS_BY_NAME

_DASHBOARD_JS = Path(__file__).resolve().parent.parent / 'agentpulse' / 'dashboard' / 'dashboard.js'


def _fake_spec(version: str) -> MagicMock:
    """A stand-in SecondaryProviderSpec exposing only what dashboard.py reads."""
    return MagicMock(cli_version=lambda: version)


def _no_cli_versions():
    """Report no installed CLI for every non-Claude provider."""
    return {name: _fake_spec('') for name in SECONDARY_PROVIDERS_BY_NAME}


def _fake_autostart_module(enabled: bool = False) -> types.ModuleType:
    fake = types.ModuleType('agentpulse.autostart')
    fake.is_autostart_enabled = MagicMock(return_value=enabled)
    fake.set_autostart = MagicMock()
    return fake


class TestDashboardHistory(unittest.TestCase):
    def test_records_sanitized_usage_rows(self):
        history = DashboardHistory(max_age_seconds=1000, max_samples=10)
        history.record('claude', {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00+00:00'},
            'extra_usage': {'used_credits': 100},
        }, ts=1000)

        rows = history.rows('24h', now=1001)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['provider'], 'claude')
        self.assertEqual(rows[0]['field'], 'five_hour')
        self.assertEqual(rows[0]['utilization'], 42.0)
        self.assertNotIn('access_token', rows[0])

    def test_records_error_without_usage_payload(self):
        history = DashboardHistory()
        history.record('codex', {'error': 'failed'}, ts=1000)

        rows = history.rows('24h', now=1001)

        self.assertEqual(rows[0]['provider'], 'codex')
        self.assertEqual(rows[0]['field'], '')
        self.assertEqual(rows[0]['error'], 'failed')

    def test_prunes_by_age_and_max_samples(self):
        history = DashboardHistory(max_age_seconds=10, max_samples=2)
        for ts in (1, 2, 20):
            history.record('claude', {'five_hour': {'utilization': ts, 'resets_at': ''}}, ts=ts)

        rows = history.rows('30d', now=20)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['utilization'], 20.0)

    def test_csv_export_has_header_and_values(self):
        history = DashboardHistory()
        history.record('claude', {'five_hour': {'utilization': 12, 'resets_at': 'soon'}}, ts=time.time())

        csv_text = history.to_csv('24h')

        self.assertIn('timestamp,provider,field,utilization,resets_at,error', csv_text)
        self.assertIn('claude,five_hour,12.0,soon', csv_text)


class TestStatusPayload(unittest.TestCase):
    def test_payload_has_no_profile_or_token_data(self):
        snap = MagicMock()
        snap.usage = {'five_hour': {'utilization': 50, 'resets_at': ''}}
        snap.last_success_time = 1000
        snap.refreshing = False
        snap.last_error = None

        app = MagicMock()
        app.cache.snapshot = snap
        app.secondary_providers.return_value = []
        app.next_poll_time = 1200

        with patch('agentpulse.dashboard.find_installations', return_value=[]):
            payload = _status_payload(app)

        self.assertTrue(payload['privacy']['token_free'])
        self.assertNotIn('profile', payload['providers'][0])
        self.assertNotIn('access_token', str(payload).lower())
        self.assertEqual(payload['providers'][0]['usage'][0]['utilization'], 50.0)

    def _app_with_providers(self, *provider_names):
        snap = MagicMock()
        snap.usage = {'five_hour': {'utilization': 50, 'resets_at': ''}}
        snap.last_success_time = 1000
        snap.refreshing = False
        snap.last_error = None

        app = MagicMock()
        app.cache.snapshot = snap
        app.next_poll_time = 1200
        caches = []
        for name in provider_names:
            cache = MagicMock()
            cache.snapshot = snap
            caches.append((name, cache))
        app.secondary_providers.return_value = caches
        return app

    def test_only_claude_when_no_other_provider_is_active(self):
        app = self._app_with_providers()
        with patch('agentpulse.dashboard.find_installations', return_value=[]):
            payload = _status_payload(app)

        self.assertEqual([entry['id'] for entry in payload['providers']], ['claude'])

    def test_active_providers_follow_claude_in_order(self):
        app = self._app_with_providers('codex', 'kimi')
        with patch('agentpulse.dashboard.find_installations', return_value=[]), \
             patch.dict(SECONDARY_PROVIDERS_BY_NAME, _no_cli_versions()):
            payload = _status_payload(app)

        self.assertEqual([entry['id'] for entry in payload['providers']], ['claude', 'codex', 'kimi'])

    def test_provider_labels_are_display_names(self):
        app = self._app_with_providers('codex', 'kimi')
        with patch('agentpulse.dashboard.find_installations', return_value=[]), \
             patch.dict(SECONDARY_PROVIDERS_BY_NAME, _no_cli_versions()):
            payload = _status_payload(app)

        self.assertEqual([entry['label'] for entry in payload['providers']], ['Claude', 'Codex', 'Kimi'])

    def test_cli_version_reported_per_provider(self):
        app = self._app_with_providers('kimi')
        with patch('agentpulse.dashboard.find_installations', return_value=[]), \
             patch.dict(SECONDARY_PROVIDERS_BY_NAME, {'kimi': _fake_spec('2.0.1')}):
            payload = _status_payload(app)

        self.assertEqual(payload['providers'][1]['installations'], [{'name': 'CLI', 'version': '2.0.1'}])

    def test_missing_cli_version_yields_no_installation_rows(self):
        app = self._app_with_providers('kimi')
        with patch('agentpulse.dashboard.find_installations', return_value=[]), \
             patch.dict(SECONDARY_PROVIDERS_BY_NAME, _no_cli_versions()):
            payload = _status_payload(app)

        self.assertEqual(payload['providers'][1]['installations'], [])


class TestCycleTrend(unittest.TestCase):
    """Tests for _cycle_trend() - pace comparison against past quota cycles."""

    HOUR = 3600
    DAY = 24 * 3600
    WEEK = 7 * 24 * 3600

    def _record(self, history, provider, field, utilization, ts, resets_at):
        """Record one sample; ``resets_at`` is a Unix timestamp or None for no active window."""
        reset_text = datetime.fromtimestamp(resets_at, tz=timezone.utc).isoformat() if resets_at is not None else ''
        history.record(provider, {field: {'utilization': utilization, 'resets_at': reset_text}}, ts=ts)

    def _weekly_cycle(self, history, start, readings, provider='claude'):
        """Record ``(day offset, pct)`` readings of the weekly cycle that starts at ``start``."""
        for day, utilization in readings:
            self._record(history, provider, 'seven_day', utilization, ts=start + day * self.DAY, resets_at=start + self.WEEK)

    def test_none_with_fewer_than_two_samples(self):
        history = DashboardHistory()
        self._record(history, 'claude', 'seven_day', 10.0, ts=1000, resets_at=1000 + self.WEEK)

        self.assertIsNone(_cycle_trend(history, 'claude', 'seven_day', now=1000))

    def test_none_without_a_completed_previous_cycle(self):
        """A single, still-rising cycle has nothing to compare against."""
        history = DashboardHistory()
        self._weekly_cycle(history, 1_000_000, [(0, 10.0), (1, 20.0)])

        self.assertIsNone(_cycle_trend(history, 'claude', 'seven_day', now=1_000_000 + self.DAY))

    def test_compares_current_cycle_against_one_previous_cycle(self):
        history = DashboardHistory()
        base = 1_000_000
        # Previous cycle: 0 -> 50% at +1 day -> 100% at +2 days.
        self._weekly_cycle(history, base, [(0, 0.0), (1, 50.0), (2, 100.0)])
        # One day into the current cycle, usage is running hotter than last time.
        current_start = base + self.WEEK
        self._weekly_cycle(history, current_start, [(0, 0.0), (1, 65.0)])
        now = current_start + self.DAY

        trend = _cycle_trend(history, 'claude', 'seven_day', now=now)

        self.assertIsNotNone(trend)
        self.assertEqual(trend['current_pct'], 65.0)
        self.assertEqual(trend['historical_avg_pct'], 50.0)
        self.assertEqual(trend['delta_pct'], 15.0)
        self.assertEqual(trend['cycles_compared'], 1)

    def test_averages_across_multiple_previous_cycles(self):
        history = DashboardHistory()
        base = 1_000_000
        # Two previous cycles, each reaching a different pct one day in.
        for cycle_index, one_day_pct in enumerate((40.0, 60.0)):
            self._weekly_cycle(history, base + cycle_index * self.WEEK, [(0, 0.0), (1, one_day_pct), (2, 100.0)])
        current_start = base + 2 * self.WEEK
        self._weekly_cycle(history, current_start, [(0, 0.0), (1, 70.0)])

        trend = _cycle_trend(history, 'claude', 'seven_day', now=current_start + self.DAY)

        self.assertEqual(trend['cycles_compared'], 2)
        self.assertEqual(trend['historical_avg_pct'], 50.0)  # average of 40 and 60
        self.assertEqual(trend['delta_pct'], 20.0)

    def test_ignores_rows_from_a_different_provider_or_field(self):
        history = DashboardHistory()
        base = 1_000_000
        self._weekly_cycle(history, base, [(0, 0.0), (1, 50.0), (2, 100.0)])
        current_start = base + self.WEEK
        self._weekly_cycle(history, current_start, [(0, 0.0), (1, 65.0)])
        now = current_start + self.DAY
        # Noise that must not be mixed into the comparison.
        self._weekly_cycle(history, base, [(1, 5.0)], provider='codex')
        self._record(history, 'claude', 'five_hour', 90.0, ts=now, resets_at=now + self.HOUR)

        trend = _cycle_trend(history, 'claude', 'seven_day', now=now)

        self.assertEqual(trend['cycles_compared'], 1)
        self.assertEqual(trend['current_pct'], 65.0)

    def test_none_when_current_cycle_has_no_elapsed_time(self):
        history = DashboardHistory()
        base = 1_000_000
        self._weekly_cycle(history, base, [(0, 0.0), (1, 100.0)])
        current_start = base + self.WEEK
        self._weekly_cycle(history, current_start, [(0, 0.0)])

        # now == the current cycle's nominal start -> zero elapsed.
        self.assertIsNone(_cycle_trend(history, 'claude', 'seven_day', now=current_start))

    def test_idle_samples_without_reset_time_do_not_form_a_cycle(self):
        """Hours of 0% without an active window must not count as a long cycle."""
        history = DashboardHistory()
        base = 1_000_000
        first_reset = base + 5 * self.HOUR
        for hours, utilization in ((0, 0.0), (2, 30.0), (4, 80.0)):
            self._record(history, 'claude', 'five_hour', utilization, ts=base + hours * self.HOUR, resets_at=first_reset)
        for hours in range(18):
            self._record(history, 'claude', 'five_hour', 0.0, ts=first_reset + hours * self.HOUR, resets_at=None)
        current_start = first_reset + 18 * self.HOUR
        for hours, utilization in ((0, 5.0), (2, 40.0)):
            self._record(history, 'claude', 'five_hour', utilization, ts=current_start + hours * self.HOUR, resets_at=current_start + 5 * self.HOUR)

        trend = _cycle_trend(history, 'claude', 'five_hour', now=current_start + 2 * self.HOUR)

        self.assertEqual(trend['cycles_compared'], 1)
        self.assertEqual(trend['historical_avg_pct'], 30.0)  # the previous window two hours in
        self.assertEqual(trend['delta_pct'], 10.0)

    def test_none_while_no_window_is_active(self):
        """An idle session (no reset time now) reports no trend instead of a bogus deficit."""
        history = DashboardHistory()
        base = 1_000_000
        for window in range(3):
            start = base + window * self.DAY
            for hours, utilization in ((0, 0.0), (2, 50.0), (4, 90.0)):
                self._record(history, 'claude', 'five_hour', utilization, ts=start + hours * self.HOUR, resets_at=start + 5 * self.HOUR)
        idle_start = base + 2 * self.DAY + 5 * self.HOUR
        for hours in range(18):
            self._record(history, 'claude', 'five_hour', 0.0, ts=idle_start + hours * self.HOUR, resets_at=None)

        self.assertIsNone(_cycle_trend(history, 'claude', 'five_hour', now=idle_start + 18 * self.HOUR))

    def test_reset_time_jitter_does_not_split_a_cycle(self):
        """The APIs repeat the same reset with sub-minute noise; that is still one cycle."""
        history = DashboardHistory()
        base = 1_000_000
        reset = base + self.WEEK
        for day, utilization, jitter in ((0, 0.0, 0.4), (1, 50.0, -2.0), (2, 70.0, 1.5)):
            self._record(history, 'claude', 'seven_day', utilization, ts=base + day * self.DAY, resets_at=reset + jitter)
        current_start = base + self.WEEK
        self._weekly_cycle(history, current_start, [(0, 0.0), (1, 55.0)])

        trend = _cycle_trend(history, 'claude', 'seven_day', now=current_start + self.DAY)

        self.assertEqual(trend['cycles_compared'], 1)
        self.assertEqual(trend['historical_avg_pct'], 50.0)

    def test_new_reset_time_starts_a_cycle_without_a_utilization_drop(self):
        """A light cycle followed by a new one at a higher start value is still two cycles."""
        history = DashboardHistory()
        base = 1_000_000
        self._weekly_cycle(history, base, [(0, 1.0), (1, 3.0)])
        current_start = base + self.WEEK
        self._weekly_cycle(history, current_start, [(0, 5.0), (1, 9.0)])

        trend = _cycle_trend(history, 'claude', 'seven_day', now=current_start + self.DAY)

        self.assertEqual(trend['cycles_compared'], 1)
        self.assertEqual(trend['historical_avg_pct'], 3.0)
        self.assertEqual(trend['delta_pct'], 6.0)

    def test_none_for_field_without_known_window_length(self):
        history = DashboardHistory()
        base = 1_000_000
        for start in (base, base + self.WEEK):
            for day, utilization in ((0, 0.0), (1, 40.0)):
                self._record(history, 'claude', 'iguana_necktie', utilization, ts=start + day * self.DAY, resets_at=start + self.WEEK)

        self.assertIsNone(_cycle_trend(history, 'claude', 'iguana_necktie', now=base + self.WEEK + self.DAY))


class TestHistoryPayload(unittest.TestCase):
    """Tests for _history_payload() - aggregated chart rows plus field metadata."""

    NOW = 2_000_000_000.0

    def _record_series(self, history, field, values, *, start, step, reset):
        reset_text = datetime.fromtimestamp(reset, tz=timezone.utc).isoformat()
        for index, utilization in enumerate(values):
            history.record('claude', {field: {'utilization': utilization, 'resets_at': reset_text}}, ts=start + index * step)

    def test_24h_range_keeps_every_reading(self):
        history = DashboardHistory()
        self._record_series(history, 'five_hour', [1.0, 2.0, 3.0, 4.0], start=self.NOW - 3600, step=180, reset=self.NOW + 3600)

        payload = _history_payload(history, '24h', now=self.NOW)

        self.assertEqual(payload['bucket_seconds'], 0)
        self.assertEqual([row['utilization'] for row in payload['rows']], [1.0, 2.0, 3.0, 4.0])

    def test_30d_range_keeps_the_highest_reading_per_bucket(self):
        history = DashboardHistory()
        bucket_start = (self.NOW - 5 * 24 * 3600) // 1800 * 1800
        # Twenty readings three minutes apart fill exactly two 30-minute buckets.
        values = [float(index) for index in range(20)]
        self._record_series(history, 'seven_day', values, start=bucket_start, step=180, reset=self.NOW + 24 * 3600)

        payload = _history_payload(history, '30d', now=self.NOW)

        self.assertEqual(payload['bucket_seconds'], 1800)
        self.assertEqual([row['utilization'] for row in payload['rows']], [9.0, 19.0])

    def test_reset_inside_a_bucket_keeps_both_cycles(self):
        history = DashboardHistory()
        bucket_start = (self.NOW - 3 * 24 * 3600) // 600 * 600
        self._record_series(history, 'five_hour', [95.0], start=bucket_start, step=180, reset=bucket_start + 60)
        self._record_series(history, 'five_hour', [2.0], start=bucket_start + 180, step=180, reset=bucket_start + 5 * 3600)

        payload = _history_payload(history, '7d', now=self.NOW)

        self.assertEqual([row['utilization'] for row in payload['rows']], [95.0, 2.0])

    def test_error_rows_survive_aggregation(self):
        history = DashboardHistory()
        history.record('codex', {'error': 'failed'}, ts=self.NOW - 3 * 24 * 3600)

        payload = _history_payload(history, '7d', now=self.NOW)

        self.assertEqual(len(payload['rows']), 1)
        self.assertEqual(payload['rows'][0]['error'], 'failed')

    def test_fields_describe_label_period_and_variant(self):
        history = DashboardHistory()
        self._record_series(history, 'five_hour', [5.0], start=self.NOW - 60, step=60, reset=self.NOW + 3600)
        self._record_series(history, 'seven_day_sonnet', [7.0], start=self.NOW - 60, step=60, reset=self.NOW + 24 * 3600)

        fields = _history_payload(history, '24h', now=self.NOW)['fields']

        self.assertEqual(fields['five_hour']['period_seconds'], 5 * 3600)
        self.assertIsNone(fields['five_hour']['variant'])
        self.assertEqual(fields['seven_day_sonnet']['period_seconds'], 7 * 24 * 3600)
        self.assertEqual(fields['seven_day_sonnet']['variant'], 'sonnet')
        self.assertTrue(fields['seven_day_sonnet']['label'])

    def test_unknown_field_is_described_without_period(self):
        history = DashboardHistory()
        self._record_series(history, 'iguana_necktie', [5.0], start=self.NOW - 60, step=60, reset=self.NOW + 3600)

        fields = _history_payload(history, '24h', now=self.NOW)['fields']

        self.assertIsNone(fields['iguana_necktie']['period_seconds'])

    def test_unknown_range_falls_back_to_24h(self):
        payload = _history_payload(DashboardHistory(), 'forever', now=self.NOW)

        self.assertEqual(payload['range'], '24h')
        self.assertEqual(payload['bucket_seconds'], 0)
        self.assertEqual(payload['rows'], [])

    def test_csv_export_is_not_aggregated(self):
        history = DashboardHistory()
        now = time.time()
        reset_text = datetime.fromtimestamp(now + 3600, tz=timezone.utc).isoformat()
        for index in range(10):
            history.record('claude', {'seven_day': {'utilization': float(index), 'resets_at': reset_text}}, ts=now - 3000 + index * 180)

        lines = history.to_csv('30d').strip().splitlines()

        self.assertEqual(len(lines), 11)  # header plus every reading


class TestHistorySeries(unittest.TestCase):
    """Tests for DashboardHistory.series() - readings grouped for the forecasts."""

    def test_groups_readings_by_provider_and_field(self):
        history = DashboardHistory()
        reset = '2033-05-18T03:33:20+00:00'
        history.record('claude', {'five_hour': {'utilization': 5, 'resets_at': reset}, 'seven_day': {'utilization': 9, 'resets_at': ''}}, ts=100)
        history.record('kimi', {'five_hour': {'utilization': 7, 'resets_at': reset}}, ts=200)

        series = history.series()

        self.assertEqual(sorted(series), ['claude', 'kimi'])
        self.assertEqual(series['claude']['five_hour'][0], Sample(100, 5.0, 2_000_000_000.0))
        self.assertIsNone(series['claude']['seven_day'][0].reset)

    def test_since_leaves_out_older_readings(self):
        history = DashboardHistory()
        for ts in (100, 200, 300):
            history.record('claude', {'five_hour': {'utilization': ts / 10, 'resets_at': ''}}, ts=ts)

        samples = history.series(since=200)['claude']['five_hour']

        self.assertEqual([sample.ts for sample in samples], [200, 300])

    def test_error_only_provider_has_no_fields(self):
        history = DashboardHistory()
        history.record('codex', {'error': 'failed'}, ts=100)

        self.assertEqual(history.series(), {'codex': {}})


class TestUsageStatisticsPayload(unittest.TestCase):
    """Tests for the consumption bars and heatmap in _history_payload()."""

    TZ = timezone.utc
    NOW = datetime(2026, 1, 14, 12, 30, tzinfo=timezone.utc).timestamp()

    def _history(self):
        history = DashboardHistory()
        reset = datetime(2026, 1, 17, 12, 0, tzinfo=timezone.utc).isoformat()
        session = datetime(2026, 1, 14, 15, 0, tzinfo=timezone.utc).isoformat()
        readings = [(-26, 10.0, 1.0), (-25, 12.0, 4.0), (-2, 20.0, 6.0), (-1, 25.0, 40.0)]
        for hours, weekly, five in readings:
            history.record('claude', {
                'seven_day': {'utilization': weekly, 'resets_at': reset},
                'seven_day_sonnet': {'utilization': weekly / 2, 'resets_at': reset},
                'five_hour': {'utilization': five, 'resets_at': session},
            }, ts=self.NOW + hours * 3600)
        return history

    def test_daily_consumption_counts_the_longest_base_window_once(self):
        consumption = _history_payload(self._history(), '7d', now=self.NOW, tz=self.TZ)['consumption']

        self.assertEqual(consumption['unit'], 'day')
        self.assertEqual(len(consumption['starts']), 7)
        claude = consumption['providers'][0]
        self.assertEqual((claude['id'], claude['field']), ('claude', 'seven_day'))
        self.assertTrue(claude['label'])
        # 2 points yesterday, 8 + 5 today; the variant and the session window add nothing.
        self.assertEqual(claude['values'][-2:], [2.0, 13.0])
        self.assertEqual(sum(claude['values']), 15.0)

    def test_day_view_counts_hours(self):
        consumption = _history_payload(self._history(), '24h', now=self.NOW, tz=self.TZ)['consumption']

        self.assertEqual(consumption['unit'], 'hour')
        self.assertEqual(len(consumption['starts']), 24)
        self.assertEqual(consumption['starts'][-1], datetime(2026, 1, 14, 12, 0, tzinfo=timezone.utc).timestamp())

    def test_month_view_counts_thirty_days(self):
        consumption = _history_payload(self._history(), '30d', now=self.NOW, tz=self.TZ)['consumption']

        self.assertEqual((consumption['unit'], len(consumption['starts'])), ('day', 30))

    def test_heatmap_is_seven_days_of_twenty_four_hours(self):
        heatmap = _history_payload(self._history(), '24h', now=self.NOW, tz=self.TZ)['heatmap']

        self.assertEqual(heatmap['days'], 28)
        cells = heatmap['providers'][0]['cells']
        self.assertEqual((len(cells), {len(row) for row in cells}), (7, {24}))
        # Wednesday (row 2) 10:00-11:00 and 11:00-12:00 hold today's growth.
        self.assertEqual(cells[2][10], 8.0)
        self.assertEqual(cells[2][11], 5.0)

    def test_typical_week_follows_the_longest_base_window(self):
        weeks = _history_payload(self._history(), '24h', now=self.NOW, tz=self.TZ)['typical_week']['providers']

        self.assertEqual([(week['id'], week['field']) for week in weeks], [('claude', 'seven_day')])
        week = weeks[0]
        self.assertEqual(week['utilization'], 25.0)
        self.assertEqual(week['start'], datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(week['past'], [])

    def test_typical_week_is_the_same_for_every_range(self):
        history = self._history()

        weeks = [_history_payload(history, name, now=self.NOW, tz=self.TZ)['typical_week'] for name in ('24h', '7d', '30d')]

        self.assertEqual(weeks[0], weeks[1])
        self.assertEqual(weeks[0], weeks[2])

    def test_providers_without_readings_are_left_out(self):
        history = DashboardHistory()
        history.record('codex', {'error': 'failed'}, ts=self.NOW - 60)

        payload = _history_payload(history, '7d', now=self.NOW, tz=self.TZ)

        self.assertEqual(payload['consumption']['providers'], [])
        self.assertEqual(payload['heatmap']['providers'], [])
        self.assertEqual(payload['typical_week']['providers'], [])
        self.assertEqual(len(payload['consumption']['starts']), 7)


class TestStatusOutlooks(unittest.TestCase):
    """Tests for the outlook of every quota in _status_payload()."""

    def _app(self, usage, history=None):
        snap = MagicMock()
        snap.usage = usage
        snap.last_success_time = 1000
        snap.refreshing = False
        snap.last_error = None
        app = MagicMock()
        app.cache.snapshot = snap
        app.secondary_providers.return_value = []
        app.next_poll_time = None
        app.dashboard.history = history or DashboardHistory()
        return app

    def _iso(self, hours):
        return datetime.fromtimestamp(time.time() + hours * 3600, tz=timezone.utc).isoformat()

    def _payload(self, app, **settings):
        values = {'prediction_enabled': True, 'prediction_day_end_time': '18:00', 'heatmap_enabled': True}
        values.update(settings)
        with patch('agentpulse.dashboard.find_installations', return_value=[]), \
             patch('agentpulse.dashboard.dashboard_settings', return_value=values):
            return _status_payload(app)

    def test_each_quota_carries_its_outlook(self):
        app = self._app({'five_hour': {'utilization': 100.0, 'resets_at': self._iso(1)}})

        entry = self._payload(app)['providers'][0]['usage'][0]

        self.assertEqual(entry['outlook']['status'], 'blocked')
        self.assertIsNone(entry['variant'])

    def test_quota_without_reset_time_has_no_outlook(self):
        app = self._app({'five_hour': {'utilization': 20.0, 'resets_at': ''}})

        self.assertIsNone(self._payload(app)['providers'][0]['usage'][0]['outlook'])

    def test_quotas_are_listed_sessions_first(self):
        app = self._app({
            'seven_day_sonnet': {'utilization': 2.0, 'resets_at': self._iso(30)},
            'seven_day': {'utilization': 20.0, 'resets_at': self._iso(30)},
            'five_hour': {'utilization': 5.0, 'resets_at': self._iso(3)},
        })

        usage = self._payload(app)['providers'][0]['usage']

        self.assertEqual([entry['field'] for entry in usage], ['five_hour', 'seven_day', 'seven_day_sonnet'])
        self.assertEqual(usage[2]['variant'], 'sonnet')

    def test_history_shapes_the_outlook(self):
        usage = {'five_hour': {'utilization': 60.0, 'resets_at': self._iso(2.5)}}
        history = DashboardHistory()
        self.assertEqual(self._payload(self._app(usage, history))['providers'][0]['usage'][0]['outlook']['status'], 'limit')

        history.record('claude', usage, ts=time.time() - 25 * 60)

        self.assertEqual(self._payload(self._app(usage, history))['providers'][0]['usage'][0]['outlook']['status'], 'ok')

    def test_a_session_outlook_carries_its_band(self):
        usage = {
            'five_hour': {'utilization': 60.0, 'resets_at': self._iso(2.5)},
            'seven_day': {'utilization': 30.0, 'resets_at': self._iso(80)},
        }
        history = DashboardHistory()
        history.record('claude', {'five_hour': {'utilization': 50.0, 'resets_at': usage['five_hour']['resets_at']}}, ts=time.time() - 20 * 60)

        session, weekly = [entry['outlook'] for entry in self._payload(self._app(usage, history))['providers'][0]['usage']]

        self.assertEqual(session['status'], 'limit')
        self.assertLess(session['limit_earliest'], session['limit_at'])
        self.assertLess(session['limit_at'], session['limit_latest'])
        self.assertLessEqual(session['forecast_low_pct'], session['forecast_pct'])
        self.assertEqual(session['forecast_high_pct'], 100.0)
        self.assertEqual([weekly[key] for key in ('forecast_low_pct', 'forecast_high_pct', 'limit_earliest', 'limit_latest')], [None] * 4)

    def test_day_end_projection_before_a_later_reset(self):
        app = self._app({'seven_day': {'utilization': 30.0, 'resets_at': self._iso(80)}})
        end = datetime.fromtimestamp(time.time() + 3600).strftime('%H:%M')

        outlook = self._payload(app, prediction_day_end_time=end)['providers'][0]['usage'][0]['outlook']

        self.assertIsNotNone(outlook['day_end_pct'])
        self.assertLessEqual(outlook['day_end_pct'], outlook['forecast_pct'])

    def test_day_end_is_the_next_target_time(self):
        payload = self._payload(self._app({}), prediction_day_end_time='18:00')

        self.assertEqual(payload['day_end'], next_local_time('18:00', now=payload['now']))
        self.assertGreater(payload['day_end'], payload['now'])
        self.assertLessEqual(payload['day_end'] - payload['now'], 24 * 3600)

    def test_day_end_that_already_passed_moves_a_day_ahead(self):
        passed = datetime.fromtimestamp(time.time() - 3600).strftime('%H:%M')

        payload = self._payload(self._app({}), prediction_day_end_time=passed)

        self.assertGreater(payload['day_end'] - payload['now'], 12 * 3600)

    def test_predictions_off_has_no_day_end(self):
        self.assertIsNone(self._payload(self._app({}), prediction_enabled=False)['day_end'])

    def test_weekly_quota_carries_todays_budget(self):
        app = self._app({
            'five_hour': {'utilization': 30.0, 'resets_at': self._iso(2)},
            'seven_day': {'utilization': 40.0, 'resets_at': self._iso(80)},
        })

        usage = self._payload(app, budget_workdays=list(range(7)))['providers'][0]['usage']

        self.assertIsNone(usage[0]['budget'])
        self.assertEqual(set(usage[1]['budget']), {'used', 'allowance', 'days'})
        self.assertGreater(usage[1]['budget']['allowance'], 0)

    def test_no_budgets_without_workdays(self):
        app = self._app({'seven_day': {'utilization': 40.0, 'resets_at': self._iso(80)}})

        usage = self._payload(app, budget_workdays=[])['providers'][0]['usage']

        self.assertIsNone(usage[0]['budget'])

    def test_predictions_off_leaves_only_reached_limits(self):
        app = self._app({
            'five_hour': {'utilization': 95.0, 'resets_at': self._iso(1)},
            'seven_day': {'utilization': 100.0, 'resets_at': self._iso(30)},
        })

        payload = self._payload(app, prediction_enabled=False)
        usage = payload['providers'][0]['usage']

        self.assertFalse(payload['settings']['prediction_enabled'])
        self.assertEqual([entry['outlook']['status'] for entry in usage], ['ok', 'blocked'])
        self.assertIsNone(usage[0]['outlook']['forecast_pct'])
        self.assertIsNone(usage[0]['trend'])

    def test_payload_reports_the_server_time(self):
        before = time.time()

        payload = self._payload(self._app({}))

        self.assertGreaterEqual(payload['now'], before)


class TestNeedsRestart(unittest.TestCase):
    STARTUP = {'codex_enabled': True, 'kimi_enabled': True}

    def test_switching_a_provider_off_requires_restart(self):
        self.assertTrue(_needs_restart({'codex_enabled': False}, self.STARTUP))

    def test_keeping_provider_state_needs_no_restart(self):
        self.assertFalse(_needs_restart({'codex_enabled': True, 'kimi_enabled': True, 'quiet_hours_enabled': True}, self.STARTUP))

    def test_other_settings_apply_without_restart(self):
        self.assertFalse(_needs_restart({'alert_thresholds_five_hour': [50], 'on_reset_command': ['echo hi']}, self.STARTUP))


class TestAutostartHelpers(unittest.TestCase):
    def test_autostart_enabled_reads_registry_state(self):
        fake = _fake_autostart_module(enabled=True)
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            self.assertTrue(_autostart_enabled())

    def test_autostart_enabled_returns_false_on_registry_error(self):
        fake = _fake_autostart_module()
        fake.is_autostart_enabled.side_effect = OSError('denied')
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            self.assertFalse(_autostart_enabled())

    def test_apply_autostart_none_is_noop(self):
        fake = _fake_autostart_module()
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            self.assertEqual(_apply_autostart(None), [])
        fake.set_autostart.assert_not_called()

    def test_apply_autostart_rejects_non_boolean(self):
        fake = _fake_autostart_module()
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            errors = _apply_autostart('yes')
        self.assertEqual(errors, ['autostart: expected true or false'])
        fake.set_autostart.assert_not_called()

    def test_apply_autostart_enables_when_currently_disabled(self):
        fake = _fake_autostart_module(enabled=False)
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            self.assertEqual(_apply_autostart(True), [])
        fake.set_autostart.assert_called_once_with(True)

    def test_apply_autostart_disables_when_currently_enabled(self):
        fake = _fake_autostart_module(enabled=True)
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            self.assertEqual(_apply_autostart(False), [])
        fake.set_autostart.assert_called_once_with(False)

    def test_apply_autostart_skips_registry_write_when_state_matches(self):
        fake = _fake_autostart_module(enabled=True)
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            self.assertEqual(_apply_autostart(True), [])
        fake.set_autostart.assert_not_called()

    def test_apply_autostart_reports_registry_errors(self):
        fake = _fake_autostart_module(enabled=False)
        fake.set_autostart.side_effect = OSError('denied')
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            errors = _apply_autostart(True)
        self.assertEqual(errors, ['autostart: denied'])


def _temp_history_path(test_case: unittest.TestCase) -> Path:
    temp_dir = tempfile.TemporaryDirectory()
    test_case.addCleanup(temp_dir.cleanup)
    return Path(temp_dir.name) / 'history.jsonl'


class TestSettingsEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = DashboardServer(MagicMock(), port=0, history_path=_temp_history_path(self))
        self.url = self.server.start()
        self.addCleanup(self.server.stop)

    def _get_json(self, path, *, token=None):
        headers = {'X-AgentsPulse-Token': token if token is not None else self.server.token}
        request = urllib.request.Request(self.url.rstrip('/') + path, headers=headers)
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode('utf-8'))

    def _post_json(self, path, payload, *, token=None, origin=None):
        headers = {'Content-Type': 'application/json', 'X-AgentsPulse-Token': token if token is not None else self.server.token}
        if origin is not None:
            headers['Origin'] = origin
        request = urllib.request.Request(
            self.url.rstrip('/') + path,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method='POST',
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode('utf-8'))

    def test_get_settings_includes_autostart_state(self):
        fake = _fake_autostart_module(enabled=True)
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            data = self._get_json('/api/settings')

        self.assertTrue(data['settings']['autostart'])

    def test_get_settings_does_not_expose_filesystem_path(self):
        fake = _fake_autostart_module(enabled=True)
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            data = self._get_json('/api/settings')

        self.assertNotIn('path', data)

    def test_get_settings_rejected_without_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get_json('/api/settings', token='')
        self.assertEqual(ctx.exception.code, 403)

    def test_post_settings_applies_autostart_and_saves_remaining_keys(self):
        fake = _fake_autostart_module(enabled=False)
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}), \
                patch('agentpulse.dashboard.save_dashboard_settings', return_value=(True, [], Path('settings.json'))) as mock_save:
            result = self._post_json('/api/settings', {'autostart': True, 'codex_enabled': False})

        self.assertTrue(result['ok'])
        fake.set_autostart.assert_called_once_with(True)
        mock_save.assert_called_once_with({'codex_enabled': False})

    def test_post_settings_response_does_not_expose_filesystem_path(self):
        with patch('agentpulse.dashboard.save_dashboard_settings', return_value=(True, [], Path('C:/Users/someone/settings.json'))):
            result = self._post_json('/api/settings', {'quiet_hours_enabled': True})

        self.assertTrue(result['ok'])
        self.assertNotIn('path', result)
        self.assertNotIn('someone', json.dumps(result))

    def test_post_settings_requires_restart_only_for_provider_toggles(self):
        self.server.startup_settings.update({'codex_enabled': True, 'kimi_enabled': True})
        with patch('agentpulse.dashboard.save_dashboard_settings', return_value=(True, [], Path('settings.json'))):
            provider_change = self._post_json('/api/settings', {'codex_enabled': False})
            other_change = self._post_json('/api/settings', {'codex_enabled': True, 'quiet_hours_enabled': True})

        self.assertTrue(provider_change['restart_required'])
        self.assertFalse(other_change['restart_required'])

    def test_failed_save_never_asks_for_restart(self):
        self.server.startup_settings.update({'codex_enabled': True, 'kimi_enabled': True})
        with patch('agentpulse.dashboard.save_dashboard_settings', return_value=(False, ['codex_enabled: invalid value'], Path('settings.json'))):
            result = self._post_json('/api/settings', {'codex_enabled': False})

        self.assertFalse(result['ok'])
        self.assertFalse(result['restart_required'])

    def test_successful_save_applies_the_settings_to_the_tray(self):
        with patch('agentpulse.dashboard.save_dashboard_settings', return_value=(True, [], Path('settings.json'))):
            self._post_json('/api/settings', {'icon_style': 'rings'})

        self.server.app.apply_settings.assert_called_once_with()

    def test_failed_save_leaves_the_tray_alone(self):
        with patch('agentpulse.dashboard.save_dashboard_settings', return_value=(False, ['icon_style: invalid value'], Path('settings.json'))):
            self._post_json('/api/settings', {'icon_style': 'sparkles'})

        self.server.app.apply_settings.assert_not_called()

    def test_post_settings_reports_invalid_autostart_value(self):
        fake = _fake_autostart_module()
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}), \
                patch('agentpulse.dashboard.save_dashboard_settings', return_value=(True, [], Path('settings.json'))):
            result = self._post_json('/api/settings', {'autostart': 'yes'})

        self.assertFalse(result['ok'])
        self.assertIn('autostart: expected true or false', result['errors'])
        fake.set_autostart.assert_not_called()


class TestRequestValidation(unittest.TestCase):
    """CSRF and DNS-rebinding protection for the dashboard endpoints."""

    def setUp(self):
        snap = MagicMock()
        snap.usage = {}
        snap.last_success_time = None
        snap.refreshing = False
        snap.last_error = None
        self.app = MagicMock()
        self.app.cache.snapshot = snap
        self.app.secondary_providers.return_value = []
        self.app.next_poll_time = None
        self.server = DashboardServer(self.app, port=0, history_path=_temp_history_path(self))
        self.url = self.server.start()
        self.port = self.server._httpd.server_address[1]
        self.addCleanup(self.server.stop)

    def _raw_request(self, method, path, *, headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=headers or {})
        return connection.getresponse()

    def _post_status(self, path, payload, headers):
        merged = {'Content-Type': 'application/json', 'Host': f'127.0.0.1:{self.port}', **headers}
        return self._raw_request('POST', path, headers=merged, body=json.dumps(payload).encode('utf-8')).status

    def test_get_rejected_with_forged_host_header(self):
        response = self._raw_request('GET', '/api/status', headers={'Host': 'attacker.example'})
        self.assertEqual(response.status, 403)

    def test_get_allowed_with_loopback_host_header(self):
        response = self._raw_request('GET', '/api/status', headers={'Host': f'127.0.0.1:{self.port}'})
        self.assertEqual(response.status, 200)

    def test_post_rejected_without_token(self):
        status = self._post_status('/api/settings', {'codex_enabled': False}, {})
        self.assertEqual(status, 403)

    def test_post_rejected_with_wrong_token(self):
        status = self._post_status('/api/settings', {'codex_enabled': False}, {'X-AgentsPulse-Token': 'guessed'})
        self.assertEqual(status, 403)

    def test_post_rejected_with_cross_site_origin_despite_token(self):
        headers = {'X-AgentsPulse-Token': self.server.token, 'Origin': 'https://attacker.example'}
        status = self._post_status('/api/settings', {'codex_enabled': False}, headers)
        self.assertEqual(status, 403)

    def test_post_allowed_with_token_and_same_origin(self):
        headers = {'X-AgentsPulse-Token': self.server.token, 'Origin': f'http://127.0.0.1:{self.port}'}
        with patch('agentpulse.dashboard.save_dashboard_settings', return_value=(True, [], Path('settings.json'))):
            status = self._post_status('/api/settings', {'codex_enabled': False}, headers)
        self.assertEqual(status, 200)

    def test_csrf_style_test_event_post_does_not_run_commands(self):
        status = self._post_status('/api/test-event', {'event': 'threshold'}, {'Content-Type': 'text/plain'})
        self.assertEqual(status, 403)
        self.app.on_test_threshold_5h.assert_not_called()

    def test_open_passes_session_token_in_url(self):
        with patch('agentpulse.dashboard.webbrowser.open') as mock_open:
            self.server.open()
        opened_url = mock_open.call_args[0][0]
        self.assertIn(f'?token={self.server.token}', opened_url)

    def test_responses_carry_security_headers(self):
        response = self._raw_request('GET', '/api/status', headers={'Host': f'127.0.0.1:{self.port}'})
        self.assertEqual(response.status, 200)
        self.assertIn("default-src 'self'", response.getheader('Content-Security-Policy') or '')
        self.assertEqual(response.getheader('X-Frame-Options'), 'DENY')
        self.assertEqual(response.getheader('Referrer-Policy'), 'no-referrer')

    def test_static_assets_have_fixed_content_types(self):
        """Content types never come from the (user-editable) Windows registry."""
        expected = {
            '/': 'text/html; charset=utf-8',
            '/dashboard.css': 'text/css; charset=utf-8',
            '/dashboard.js': 'text/javascript; charset=utf-8',
        }
        with patch('mimetypes.guess_type', return_value=('text/plain', None)):
            for path, content_type in expected.items():
                response = self._raw_request('GET', path, headers={'Host': f'127.0.0.1:{self.port}'})
                response.read()
                self.assertEqual(response.status, 200, path)
                self.assertEqual(response.getheader('Content-Type'), content_type, path)
                self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff', path)

    def test_history_endpoint_returns_bucket_and_fields(self):
        response = self._raw_request('GET', '/api/history?range=7d', headers={'Host': f'127.0.0.1:{self.port}'})
        payload = json.loads(response.read().decode('utf-8'))

        self.assertEqual(payload['range'], '7d')
        self.assertEqual(payload['bucket_seconds'], 600)
        self.assertEqual(payload['rows'], [])
        self.assertEqual(payload['fields'], {})

    def test_i18n_endpoint_returns_translations(self):
        response = self._raw_request('GET', '/api/i18n', headers={'Host': f'127.0.0.1:{self.port}'})
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read().decode('utf-8'))
        self.assertIn('save_settings', payload)
        self.assertTrue(all(payload.values()))


class TestHistoryPersistence(unittest.TestCase):
    def setUp(self):
        self.path = _temp_history_path(self)

    def test_history_survives_restart(self):
        history = DashboardHistory(path=self.path)
        now = time.time()
        history.record('claude', {'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00+00:00'}}, ts=now)
        history.record('codex', {'error': 'failed'}, ts=now + 1)

        restored = DashboardHistory(path=self.path)
        rows = restored.rows('24h', now=now + 2)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['provider'], 'claude')
        self.assertEqual(rows[0]['utilization'], 42.0)
        self.assertEqual(rows[1]['error'], 'failed')

    def test_load_skips_corrupt_lines_and_compacts_file(self):
        now = time.time()
        valid = json.dumps({'ts': now, 'provider': 'claude', 'usage': {'five_hour': {'utilization': 10, 'resets_at': ''}}})
        self.path.write_text(f'{valid}\nnot json\n{{"ts": "bad"}}\n', encoding='utf-8')

        history = DashboardHistory(path=self.path)

        self.assertEqual(len(history.rows('24h', now=now + 1)), 1)
        self.assertEqual(len(self.path.read_text(encoding='utf-8').splitlines()), 1)

    def test_load_prunes_entries_older_than_max_age(self):
        now = time.time()
        old = json.dumps({'ts': now - 40 * 24 * 3600, 'provider': 'claude', 'usage': {'five_hour': {'utilization': 1, 'resets_at': ''}}})
        fresh = json.dumps({'ts': now, 'provider': 'claude', 'usage': {'five_hour': {'utilization': 2, 'resets_at': ''}}})
        self.path.write_text(f'{old}\n{fresh}\n', encoding='utf-8')

        history = DashboardHistory(path=self.path)
        rows = history.rows('30d', now=now + 1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['utilization'], 2.0)

    def test_load_sanitizes_tampered_usage_entries(self):
        now = time.time()
        tampered = json.dumps({'ts': now, 'provider': 'claude', 'usage': {'five_hour': {'utilization': 'high', 'resets_at': ''}, 'seven_day': {'utilization': 7, 'resets_at': 123}}})
        self.path.write_text(tampered + '\n', encoding='utf-8')

        history = DashboardHistory(path=self.path)
        rows = history.rows('24h', now=now + 1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['field'], 'seven_day')
        self.assertEqual(rows[0]['resets_at'], '')

    def test_compaction_bounds_file_growth(self):
        history = DashboardHistory(max_samples=2, path=self.path)
        with patch('agentpulse.dashboard._HISTORY_COMPACT_SLACK', 3):
            for index in range(20):
                history.record('claude', {'five_hour': {'utilization': index, 'resets_at': ''}}, ts=time.time())

        line_count = len(self.path.read_text(encoding='utf-8').splitlines())
        self.assertLessEqual(line_count, 6)

    def test_no_file_created_without_path(self):
        history = DashboardHistory()
        history.record('claude', {'five_hour': {'utilization': 5, 'resets_at': ''}}, ts=time.time())

        self.assertIsNone(history.path)
        self.assertFalse(self.path.exists())

    def test_write_errors_do_not_break_recording(self):
        history = DashboardHistory(path=self.path / 'missing-dir' / 'history.jsonl')
        history.record('claude', {'five_hour': {'utilization': 5, 'resets_at': ''}}, ts=time.time())

        self.assertEqual(len(history.rows('24h')), 1)


class TestStatuslineEndpoint(unittest.TestCase):
    """Tests for GET /api/statusline, the Claude Code status line."""

    def setUp(self):
        self.app = MagicMock()
        self.app.statusline_text.return_value = 'Claude 5h 42% ↺14:30'
        self.server = DashboardServer(self.app, port=0, history_path=_temp_history_path(self))
        self.server.start()
        self.port = self.server._httpd.server_address[1]
        self.addCleanup(self.server.stop)
        enabled = patch('agentpulse.settings.STATUSLINE_ENABLED', True)
        enabled.start()
        self.addCleanup(enabled.stop)

    def _get(self, path, *, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request('GET', path, headers={'Host': host or f'127.0.0.1:{self.port}'})
        response = connection.getresponse()
        return response, response.read()

    def test_serves_one_line_of_plain_text_without_a_session_token(self):
        response, body = self._get('/api/statusline')

        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Type'), 'text/plain; charset=utf-8')
        self.assertEqual(body.decode('utf-8'), 'Claude 5h 42% ↺14:30\n')
        self.app.statusline_text.assert_called_once_with(provider=None, color=True)

    def test_provider_and_color_parameters(self):
        response, _body = self._get('/api/statusline?provider=claude&color=0')

        self.assertEqual(response.status, 200)
        self.app.statusline_text.assert_called_once_with(provider='claude', color=False)

    def test_unknown_provider_is_rejected(self):
        response, _body = self._get('/api/statusline?provider=gemini')

        self.assertEqual(response.status, 400)
        self.app.statusline_text.assert_not_called()

    def test_not_served_while_turned_off(self):
        with patch('agentpulse.settings.STATUSLINE_ENABLED', False):
            response, _body = self._get('/api/statusline')

        self.assertEqual(response.status, 404)
        self.app.statusline_text.assert_not_called()

    def test_turning_it_on_needs_no_restart(self):
        with patch('agentpulse.settings.STATUSLINE_ENABLED', False):
            before, _body = self._get('/api/statusline')
        after, _body = self._get('/api/statusline')

        self.assertEqual((before.status, after.status), (404, 200))

    def test_forged_host_is_rejected(self):
        response, _body = self._get('/api/statusline', host='attacker.example')

        self.assertEqual(response.status, 403)
        self.app.statusline_text.assert_not_called()

    def test_web_pages_cannot_read_it(self):
        response, _body = self._get('/api/statusline')

        self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')


class TestDashboardServer(unittest.TestCase):
    def test_start_uses_next_port_when_configured_port_is_busy(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        busy_port = sock.getsockname()[1]
        server = DashboardServer(MagicMock(), port=busy_port, history_path=_temp_history_path(self))
        try:
            url = server.start()

            self.assertNotEqual(server._httpd.server_address[1], busy_port)
            self.assertTrue(url.startswith('http://127.0.0.1:'))
        finally:
            server.stop()
            sock.close()


class TestDashboardI18n(unittest.TestCase):
    def test_returns_non_empty_strings_for_every_key(self):
        strings = _dashboard_i18n()

        self.assertTrue(strings)
        for key, value in strings.items():
            self.assertIsInstance(value, str, key)
            self.assertNotEqual(value, '', key)

    def test_exposes_reused_and_dashboard_keys(self):
        strings = _dashboard_i18n()

        self.assertIn('save_settings', strings)
        self.assertIn('autostart', strings)
        for key in ('status_ok', 'status_tight', 'status_limit', 'status_limit_at', 'status_blocked', 'forecast_at_reset'):
            self.assertIn(key, strings)

    def test_weekdays_are_sent_monday_first(self):
        from agentpulse.i18n import T

        strings = _dashboard_i18n()

        self.assertEqual([strings[f'weekday_{index}'] for index in range(7)], T['weekdays'])

    def test_every_key_the_dashboard_script_asks_for_is_sent(self):
        """Each tr('key') and data-i18n key in the dashboard has a translation, apart from dynamic prefixes."""
        strings = _dashboard_i18n()
        script = _DASHBOARD_JS.read_text(encoding='utf-8')
        markup = (_DASHBOARD_JS.parent / 'index.html').read_text(encoding='utf-8')
        wanted = set(re.findall(r"tr\('([a-z0-9_]+)'", script))
        wanted |= set(re.findall(r'data-i18n(?:-label)?="([a-z0-9_]+)"', markup))

        self.assertEqual(sorted(wanted - set(strings)), [])

    def test_exposes_every_provider_settings_key(self):
        """Each provider's dashboard controls have a translated label."""
        strings = _dashboard_i18n()

        for key in ('codex_monitoring', 'kimi_monitoring', 'thr_codex_5h', 'thr_codex_7d', 'thr_kimi_5h', 'thr_kimi_7d'):
            self.assertIn(key, strings)

    def test_placeholder_strings_keep_their_tokens(self):
        strings = _dashboard_i18n()

        self.assertIn('{count}', strings['rows'])
        self.assertIn('{range}', strings['rows'])
        for token in ('{earliest}', '{latest}'):
            self.assertIn(token, strings['limit_between'])
        for token in ('{pct}', '{low}', '{high}'):
            self.assertIn(token, strings['forecast_at_reset_band'])

    def test_band_texts_are_shared_with_the_popup(self):
        from agentpulse.i18n import T

        strings = _dashboard_i18n()

        for key in ('forecast_at_reset_band', 'gap_before_reset'):
            self.assertEqual(strings[key], T[key])
        self.assertEqual(strings['forecast_band'], T['dash_forecast_band'])
        self.assertEqual(strings['limit_between'], T['dash_limit_between'])

    def test_save_messages_do_not_show_the_settings_path(self):
        strings = _dashboard_i18n()

        self.assertNotIn('{path}', strings['saved'])
        self.assertNotIn('{path}', strings['restart_required'])


class TestDashboardAssets(unittest.TestCase):
    """Static checks for dashboard.js constraints that the browser enforces silently."""

    @classmethod
    def setUpClass(cls):
        cls.script = _DASHBOARD_JS.read_text(encoding='utf-8')

    def test_script_writes_no_inline_style_attributes(self):
        """The CSP (default-src 'self') drops style attributes set through markup."""
        self.assertIsNone(re.search(r'style\s*=\s*["\']', self.script))

    def test_script_does_not_spread_data_into_math_min_max(self):
        """Spreading long history arrays into Math.min/max overflows the call stack."""
        self.assertIsNone(re.search(r'Math\.(min|max)\([^)]*\.\.\.', self.script))

    def test_script_has_no_hardcoded_quota_field_names(self):
        """Charts group quota fields by the period the server reports, never by a field name literal."""
        self.assertIsNone(re.search(r'[\'"](five_hour|seven_day|seven)[\'"]', self.script))


if __name__ == '__main__':
    unittest.main()
