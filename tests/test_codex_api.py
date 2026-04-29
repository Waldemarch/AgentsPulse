"""Tests for codex_api module."""
from __future__ import annotations

import json
import unittest
import urllib.error
from http.client import HTTPMessage
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentpulse.codex_api import (
    _normalize_usage, _unix_to_iso, _parse_retry_after,
    fetch_profile, fetch_usage, read_access_token,
)


class TestReadAccessToken(unittest.TestCase):
    def test_returns_none_when_file_missing(self):
        with patch('agentpulse.codex_api.CODEX_AUTH_FILE', Path('/nonexistent/auth.json')):
            self.assertIsNone(read_access_token())

    def test_returns_token_from_valid_file(self):
        auth = {'tokens': {'access_token': 'tok_abc'}}
        with patch('agentpulse.codex_api.CODEX_AUTH_FILE') as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps(auth)
            self.assertEqual(read_access_token(), 'tok_abc')

    def test_returns_none_on_invalid_json(self):
        with patch('agentpulse.codex_api.CODEX_AUTH_FILE') as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = 'not json'
            self.assertIsNone(read_access_token())

    def test_returns_none_when_token_missing_in_json(self):
        with patch('agentpulse.codex_api.CODEX_AUTH_FILE') as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps({'tokens': {}})
            self.assertIsNone(read_access_token())


class TestNormalizeUsage(unittest.TestCase):
    def _raw(self, primary_pct=10, primary_reset=1000000, secondary_pct=5, secondary_reset=2000000):
        return {
            'rate_limit': {
                'primary_window': {'used_percent': primary_pct, 'reset_at': primary_reset},
                'secondary_window': {'used_percent': secondary_pct, 'reset_at': secondary_reset},
            }
        }

    def test_maps_primary_to_five_hour(self):
        result = _normalize_usage(self._raw(primary_pct=42))
        self.assertEqual(result['five_hour']['utilization'], 42.0)

    def test_maps_secondary_to_seven_day(self):
        result = _normalize_usage(self._raw(secondary_pct=77))
        self.assertEqual(result['seven_day']['utilization'], 77.0)

    def test_converts_unix_timestamp_to_iso(self):
        result = _normalize_usage(self._raw(primary_reset=0))
        self.assertIn('T', result['five_hour']['resets_at'])

    def test_handles_none_rate_limit(self):
        result = _normalize_usage({'rate_limit': None})
        self.assertEqual(result, {})

    def test_handles_missing_windows(self):
        result = _normalize_usage({'rate_limit': {}})
        self.assertNotIn('five_hour', result)
        self.assertNotIn('seven_day', result)

    def test_zero_utilization(self):
        result = _normalize_usage(self._raw(primary_pct=0, secondary_pct=0))
        self.assertEqual(result['five_hour']['utilization'], 0.0)
        self.assertEqual(result['seven_day']['utilization'], 0.0)

    def test_null_used_percent_treated_as_zero(self):
        raw = {'rate_limit': {'primary_window': {'used_percent': None, 'reset_at': 1000000}}}
        result = _normalize_usage(raw)
        self.assertEqual(result['five_hour']['utilization'], 0.0)


class TestUnixToIso(unittest.TestCase):
    def test_converts_known_timestamp(self):
        iso = _unix_to_iso(0)
        self.assertTrue(iso.startswith('1970-01-01'))

    def test_returns_empty_for_none(self):
        self.assertEqual(_unix_to_iso(None), '')

    def test_returns_empty_for_invalid(self):
        self.assertEqual(_unix_to_iso('not-a-number'), '')

    def test_accepts_float(self):
        iso = _unix_to_iso(1700000000.5)
        self.assertIn('T', iso)


class TestFetchUsageErrors(unittest.TestCase):
    def _make_http_error(self, code, headers=None):
        msg = HTTPMessage()
        if headers:
            for k, v in headers.items():
                msg[k] = v
        err = urllib.error.HTTPError(
            url='https://chatgpt.com/backend-api/codex/usage',
            code=code, msg='', hdrs=msg, fp=BytesIO(b''),
        )
        return err

    def test_no_token_returns_error(self):
        with patch('agentpulse.codex_api.read_access_token', return_value=None):
            result = fetch_usage()
        self.assertIn('error', result)

    def test_401_returns_auth_error(self):
        with patch('agentpulse.codex_api.read_access_token', return_value='tok'):
            with patch('agentpulse.codex_api._opener') as mock_opener:
                mock_opener.open.side_effect = self._make_http_error(401)
                result = fetch_usage()
        self.assertTrue(result.get('auth_error'))
        self.assertIn('error', result)

    def test_429_returns_rate_limited(self):
        with patch('agentpulse.codex_api.read_access_token', return_value='tok'):
            with patch('agentpulse.codex_api._opener') as mock_opener:
                mock_opener.open.side_effect = self._make_http_error(429, {'Retry-After': '60'})
                result = fetch_usage()
        self.assertTrue(result.get('rate_limited'))
        self.assertEqual(result.get('retry_after'), 60)

    def test_500_returns_server_error(self):
        with patch('agentpulse.codex_api.read_access_token', return_value='tok'):
            with patch('agentpulse.codex_api._opener') as mock_opener:
                mock_opener.open.side_effect = self._make_http_error(500)
                result = fetch_usage()
        self.assertIn('error', result)
        self.assertNotIn('auth_error', result)

    def test_url_error_returns_connection_error(self):
        with patch('agentpulse.codex_api.read_access_token', return_value='tok'):
            with patch('agentpulse.codex_api._opener') as mock_opener:
                mock_opener.open.side_effect = urllib.error.URLError('timeout')
                result = fetch_usage()
        self.assertIn('error', result)

    def test_success_returns_normalized_data(self):
        raw = {
            'email': 'a@b.com', 'plan_type': 'plus',
            'rate_limit': {
                'primary_window': {'used_percent': 50, 'reset_at': 1700000000},
                'secondary_window': {'used_percent': 10, 'reset_at': 1700600000},
            },
        }
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(raw).encode()

        with patch('agentpulse.codex_api.read_access_token', return_value='tok'):
            with patch('agentpulse.codex_api._opener') as mock_opener:
                mock_opener.open.return_value = mock_resp
                result = fetch_usage()

        self.assertNotIn('error', result)
        self.assertEqual(result['five_hour']['utilization'], 50.0)
        self.assertEqual(result['seven_day']['utilization'], 10.0)


class TestParseRetryAfter(unittest.TestCase):
    def _make_error(self, retry_after_value):
        msg = HTTPMessage()
        if retry_after_value is not None:
            msg['Retry-After'] = retry_after_value
        return urllib.error.HTTPError(url='', code=429, msg='', hdrs=msg, fp=BytesIO(b''))

    def test_parses_integer(self):
        self.assertEqual(_parse_retry_after(self._make_error('120')), 120)

    def test_clamps_negative_to_zero(self):
        self.assertEqual(_parse_retry_after(self._make_error('-5')), 0)

    def test_returns_none_when_header_missing(self):
        self.assertIsNone(_parse_retry_after(self._make_error(None)))

    def test_returns_none_for_non_integer(self):
        self.assertIsNone(_parse_retry_after(self._make_error('soon')))


if __name__ == '__main__':
    unittest.main()
