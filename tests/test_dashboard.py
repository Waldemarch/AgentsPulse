"""Tests for local dashboard history and payload helpers."""
from __future__ import annotations

import json
import socket
import sys
import time
import types
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentpulse.dashboard import DashboardHistory, DashboardServer, _apply_autostart, _autostart_enabled, _status_payload


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
        app.codex_cache = None
        app._next_poll_time = 1200

        with patch('agentpulse.dashboard.find_installations', return_value=[]):
            payload = _status_payload(app)

        self.assertTrue(payload['privacy']['token_free'])
        self.assertNotIn('profile', payload['providers'][0])
        self.assertNotIn('access_token', str(payload).lower())
        self.assertEqual(payload['providers'][0]['usage'][0]['utilization'], 50.0)


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


class TestSettingsEndpoint(unittest.TestCase):
    def setUp(self):
        self.server = DashboardServer(MagicMock(), port=0)
        self.url = self.server.start()
        self.addCleanup(self.server.stop)

    def _get_json(self, path):
        with urllib.request.urlopen(self.url.rstrip('/') + path) as response:
            return json.loads(response.read().decode('utf-8'))

    def _post_json(self, path, payload):
        request = urllib.request.Request(
            self.url.rstrip('/') + path,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode('utf-8'))

    def test_get_settings_includes_autostart_state(self):
        fake = _fake_autostart_module(enabled=True)
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}):
            data = self._get_json('/api/settings')

        self.assertTrue(data['settings']['autostart'])

    def test_post_settings_applies_autostart_and_saves_remaining_keys(self):
        fake = _fake_autostart_module(enabled=False)
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}), \
                patch('agentpulse.dashboard.save_dashboard_settings', return_value=(True, [], Path('settings.json'))) as mock_save:
            result = self._post_json('/api/settings', {'autostart': True, 'codex_enabled': False})

        self.assertTrue(result['ok'])
        fake.set_autostart.assert_called_once_with(True)
        mock_save.assert_called_once_with({'codex_enabled': False})

    def test_post_settings_reports_invalid_autostart_value(self):
        fake = _fake_autostart_module()
        with patch.dict(sys.modules, {'agentpulse.autostart': fake}), \
                patch('agentpulse.dashboard.save_dashboard_settings', return_value=(True, [], Path('settings.json'))):
            result = self._post_json('/api/settings', {'autostart': 'yes'})

        self.assertFalse(result['ok'])
        self.assertIn('autostart: expected true or false', result['errors'])
        fake.set_autostart.assert_not_called()


class TestDashboardServer(unittest.TestCase):
    def test_start_uses_next_port_when_configured_port_is_busy(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        busy_port = sock.getsockname()[1]
        server = DashboardServer(MagicMock(), port=busy_port)
        try:
            url = server.start()

            self.assertNotEqual(server._httpd.server_address[1], busy_port)
            self.assertTrue(url.startswith('http://127.0.0.1:'))
        finally:
            server.stop()
            sock.close()


if __name__ == '__main__':
    unittest.main()
