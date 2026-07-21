"""Tests for local dashboard history and payload helpers."""
from __future__ import annotations

import http.client
import json
import socket
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentpulse.dashboard import DashboardHistory, DashboardServer, _apply_autostart, _autostart_enabled, _dashboard_i18n, _status_payload


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
        self.app.codex_cache = None
        self.app._next_poll_time = None
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
        self.assertIn('pace_healthy', strings)

    def test_placeholder_strings_keep_their_tokens(self):
        strings = _dashboard_i18n()

        self.assertIn('{path}', strings['saved'])
        self.assertIn('{count}', strings['rows'])
        self.assertIn('{range}', strings['rows'])


if __name__ == '__main__':
    unittest.main()
