"""Tests for local dashboard history and payload helpers."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
import socket
import time

from agentpulse.dashboard import DashboardHistory, DashboardServer, _status_payload


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
