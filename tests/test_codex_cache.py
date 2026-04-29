"""Tests for codex_cache module."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from agentpulse.codex_cache import CodexCache, CodexSnapshot, CodexUpdateResult


def _make_usage(pct_5h=10.0, pct_7d=5.0):
    return {
        'five_hour': {'utilization': pct_5h, 'resets_at': '2026-01-01T10:00:00+00:00'},
        'seven_day': {'utilization': pct_7d, 'resets_at': '2026-01-08T10:00:00+00:00'},
    }


class TestCodexCacheInitialState(unittest.TestCase):
    def setUp(self):
        self.cache = CodexCache()

    def test_usage_empty_before_first_update(self):
        self.assertEqual(self.cache.usage, {})

    def test_profile_none_before_first_fetch(self):
        self.assertIsNone(self.cache.profile)

    def test_last_success_time_none_initially(self):
        self.assertIsNone(self.cache.last_success_time)

    def test_last_error_none_initially(self):
        self.assertIsNone(self.cache.last_error)

    def test_version_starts_at_zero(self):
        self.assertEqual(self.cache.version, 0)

    def test_snapshot_reflects_initial_state(self):
        snap = self.cache.snapshot
        self.assertEqual(snap.usage, {})
        self.assertIsNone(snap.profile)
        self.assertIsNone(snap.last_success_time)
        self.assertFalse(snap.refreshing)
        self.assertIsNone(snap.last_error)


class TestCodexCacheUpdate(unittest.TestCase):
    def setUp(self):
        self.cache = CodexCache()

    def test_successful_update_populates_usage(self):
        usage = _make_usage(pct_5h=42.0)
        with patch('agentpulse.codex_cache.fetch_usage', return_value=usage):
            result = self.cache.update()
        self.assertEqual(result.data, usage)
        self.assertEqual(self.cache.usage['five_hour']['utilization'], 42.0)
        self.assertIsNone(self.cache.last_error)

    def test_error_response_sets_last_error(self):
        with patch('agentpulse.codex_cache.fetch_usage', return_value={'error': 'oops'}):
            result = self.cache.update()
        self.assertIn('error', result.data)
        self.assertEqual(self.cache.last_error, 'oops')
        self.assertEqual(self.cache.usage, {})

    def test_second_update_skipped_within_cooldown(self):
        usage = _make_usage()
        with patch('agentpulse.codex_cache.fetch_usage', return_value=usage) as mock_fetch:
            self.cache.update()
            result = self.cache.update()
        self.assertIsNone(result.data)
        mock_fetch.assert_called_once()

    def test_lock_held_returns_none(self):
        usage = _make_usage()
        barrier = threading.Barrier(2)
        results = []

        def slow_fetch():
            barrier.wait()
            time.sleep(0.1)
            return usage

        def run_first():
            with patch('agentpulse.codex_cache.fetch_usage', side_effect=slow_fetch):
                results.append(self.cache.update())

        def run_second():
            barrier.wait()
            results.append(self.cache.update())

        t1 = threading.Thread(target=run_first)
        t2 = threading.Thread(target=run_second)
        t1.start(); t2.start()
        t1.join(); t2.join()

        none_results = [r for r in results if r.data is None]
        self.assertGreaterEqual(len(none_results), 1)

    def test_rate_limit_sets_backoff(self):
        with patch('agentpulse.codex_cache.fetch_usage',
                   return_value={'error': 'too many', 'rate_limited': True, 'retry_after': 300}):
            self.cache.update()
        self.assertGreater(self.cache.rate_limit_remaining, 0)

    def test_version_increments_on_success(self):
        v0 = self.cache.version
        with patch('agentpulse.codex_cache.fetch_usage', return_value=_make_usage()):
            self.cache.update()
        self.assertGreater(self.cache.version, v0)

    def test_snapshot_is_immutable_after_update(self):
        usage = _make_usage(pct_5h=20.0)
        with patch('agentpulse.codex_cache.fetch_usage', return_value=usage):
            self.cache.update()
        snap = self.cache.snapshot

        new_usage = _make_usage(pct_5h=99.0)
        with patch('agentpulse.codex_cache.fetch_usage', return_value=new_usage):
            with patch('agentpulse.codex_cache.POLL_FAST', 0):
                self.cache.update()

        self.assertEqual(snap.usage['five_hour']['utilization'], 20.0)


class TestCodexCacheEnsureProfile(unittest.TestCase):
    def setUp(self):
        self.cache = CodexCache()

    def test_fetches_profile_when_none(self):
        profile = {'account': {'email': 'a@b.com'}, 'organization': {'organization_type': 'plus'}}
        with patch('agentpulse.codex_cache.fetch_profile', return_value=profile):
            with patch('agentpulse.codex_cache.read_access_token', return_value='tok'):
                self.cache.ensure_profile()
        self.assertEqual(self.cache.profile, profile)

    def test_does_not_refetch_when_token_unchanged(self):
        profile = {'account': {'email': 'a@b.com'}, 'organization': {}}
        with patch('agentpulse.codex_cache.fetch_profile', return_value=profile) as mock_fp:
            with patch('agentpulse.codex_cache.read_access_token', return_value='tok'):
                self.cache.ensure_profile()
                self.cache.ensure_profile()
        mock_fp.assert_called_once()

    def test_refetches_when_token_changes(self):
        profile1 = {'account': {'email': 'a@b.com'}, 'organization': {}}
        profile2 = {'account': {'email': 'b@c.com'}, 'organization': {}}
        tokens = iter(['tok1', 'tok1', 'tok2', 'tok2'])
        with patch('agentpulse.codex_cache.fetch_profile', side_effect=[profile1, profile2]):
            with patch('agentpulse.codex_cache.read_access_token', side_effect=tokens):
                self.cache.ensure_profile()
                self.cache.ensure_profile()
        self.assertEqual(self.cache.profile, profile2)


class TestCodexSnapshotFrozen(unittest.TestCase):
    def test_snapshot_is_frozen_dataclass(self):
        snap = CodexSnapshot(usage={}, profile=None, last_success_time=None, refreshing=False, last_error=None, version=0)
        with self.assertRaises((AttributeError, TypeError)):
            snap.version = 99  # type: ignore[misc]


if __name__ == '__main__':
    unittest.main()
