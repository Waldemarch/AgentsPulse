"""Tests for kimi_api module."""
from __future__ import annotations

import json
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

from agentpulse import kimi_api


def _usage_payload(weekly_limit='2048', weekly_used='214', window_limit='200', window_used='139'):
    return {
        'usage': {
            'limit': weekly_limit,
            'used': weekly_used,
            'remaining': '1834',
            'resetTime': '2026-01-09T15:23:13.716839300Z',
        },
        'limits': [{
            'window': {'duration': 300, 'timeUnit': 'TIME_UNIT_MINUTE'},
            'detail': {
                'limit': window_limit,
                'used': window_used,
                'remaining': '61',
                'resetTime': '2026-01-06T13:33:02.717479433Z',
            },
        }],
    }


def _clear_raw_cache():
    kimi_api._raw_cache.update(ts=0.0, token=None, body=None)


class TestReadAccessToken(unittest.TestCase):
    """Tests for read_access_token()."""

    def setUp(self):
        _clear_raw_cache()

    def _with_file(self, content):
        return patch.object(type(kimi_api.KIMI_CREDENTIALS), 'read_text', return_value=content)

    def test_flat_access_token(self):
        with self._with_file(json.dumps({'access_token': 'tok'})):
            self.assertEqual(kimi_api.read_access_token(), 'tok')

    def test_nested_under_tokens(self):
        with self._with_file(json.dumps({'tokens': {'access_token': 'tok'}})):
            self.assertEqual(kimi_api.read_access_token(), 'tok')

    def test_nested_under_data(self):
        with self._with_file(json.dumps({'data': {'access_token': 'tok'}})):
            self.assertEqual(kimi_api.read_access_token(), 'tok')

    def test_missing_file_returns_none(self):
        with patch.object(type(kimi_api.KIMI_CREDENTIALS), 'read_text', side_effect=OSError):
            self.assertIsNone(kimi_api.read_access_token())

    def test_corrupt_json_returns_none(self):
        with self._with_file('{not json'):
            self.assertIsNone(kimi_api.read_access_token())

    def test_unknown_shape_returns_none(self):
        with self._with_file(json.dumps({'credential': {'secret': 'tok'}})):
            self.assertIsNone(kimi_api.read_access_token())

    def test_non_object_json_returns_none(self):
        with self._with_file(json.dumps(['tok'])):
            self.assertIsNone(kimi_api.read_access_token())

    def test_empty_token_returns_none(self):
        with self._with_file(json.dumps({'access_token': ''})):
            self.assertIsNone(kimi_api.read_access_token())

    def test_expired_unix_seconds_returns_none(self):
        with self._with_file(json.dumps({'access_token': 'tok', 'expires_at': time.time() - 10})):
            self.assertIsNone(kimi_api.read_access_token())

    def test_valid_unix_seconds_returns_token(self):
        with self._with_file(json.dumps({'access_token': 'tok', 'expires_at': time.time() + 600})):
            self.assertEqual(kimi_api.read_access_token(), 'tok')

    def test_expired_unix_milliseconds_returns_none(self):
        with self._with_file(json.dumps({'access_token': 'tok', 'expires_at': (time.time() - 10) * 1000})):
            self.assertIsNone(kimi_api.read_access_token())

    def test_valid_unix_milliseconds_returns_token(self):
        with self._with_file(json.dumps({'access_token': 'tok', 'expires_at': (time.time() + 600) * 1000})):
            self.assertEqual(kimi_api.read_access_token(), 'tok')

    def test_expired_iso_timestamp_returns_none(self):
        with self._with_file(json.dumps({'access_token': 'tok', 'expires_at': '2020-01-01T00:00:00Z'})):
            self.assertIsNone(kimi_api.read_access_token())

    def test_unparsable_expiry_is_ignored(self):
        with self._with_file(json.dumps({'access_token': 'tok', 'expires_at': 'never'})):
            self.assertEqual(kimi_api.read_access_token(), 'tok')


class TestApiHeaders(unittest.TestCase):
    """Tests for api_headers()."""

    def test_none_without_token(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value=None):
            self.assertIsNone(kimi_api.api_headers())

    def test_bearer_authorization(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'):
            headers = kimi_api.api_headers()
        self.assertEqual(headers['Authorization'], 'Bearer tok')

    def test_token_only_in_authorization_header(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'):
            headers = kimi_api.api_headers()
        other_values = [value for key, value in headers.items() if key != 'Authorization']
        self.assertNotIn('tok', ' '.join(other_values))


class TestNormalizeUsage(unittest.TestCase):
    """Tests for the response normalization."""

    def test_weekly_quota_maps_to_seven_day(self):
        result = kimi_api._normalize_usage(_usage_payload())
        self.assertAlmostEqual(result['seven_day']['utilization'], 214 / 2048 * 100)

    def test_five_hour_window_from_300_minutes(self):
        result = kimi_api._normalize_usage(_usage_payload())
        self.assertAlmostEqual(result['five_hour']['utilization'], 139 / 200 * 100)

    def test_reset_times_are_parseable_iso(self):
        from datetime import datetime

        result = kimi_api._normalize_usage(_usage_payload())
        for field in ('five_hour', 'seven_day'):
            parsed = datetime.fromisoformat(result[field]['resets_at'])
            self.assertIsNotNone(parsed.tzinfo)

    def test_numeric_values_accepted(self):
        payload = _usage_payload()
        payload['usage']['limit'] = 2048
        payload['usage']['used'] = 512
        result = kimi_api._normalize_usage(payload)
        self.assertAlmostEqual(result['seven_day']['utilization'], 25.0)

    def test_hour_time_unit(self):
        payload = _usage_payload()
        payload['limits'][0]['window'] = {'duration': 5, 'timeUnit': 'TIME_UNIT_HOUR'}
        result = kimi_api._normalize_usage(payload)
        self.assertIn('five_hour', result)

    def test_day_time_unit(self):
        payload = _usage_payload()
        payload['limits'][0]['window'] = {'duration': 1, 'timeUnit': 'TIME_UNIT_DAY'}
        result = kimi_api._normalize_usage(payload)
        self.assertIn('one_day', result)

    def test_second_time_unit(self):
        payload = _usage_payload()
        payload['limits'][0]['window'] = {'duration': 18000, 'timeUnit': 'TIME_UNIT_SECOND'}
        result = kimi_api._normalize_usage(payload)
        self.assertIn('five_hour', result)

    def test_unknown_time_unit_skipped(self):
        payload = _usage_payload()
        payload['limits'][0]['window'] = {'duration': 5, 'timeUnit': 'TIME_UNIT_FORTNIGHT'}
        result = kimi_api._normalize_usage(payload)
        self.assertEqual(set(result), {'seven_day'})

    def test_unnameable_window_skipped(self):
        payload = _usage_payload()
        payload['limits'][0]['window'] = {'duration': 36, 'timeUnit': 'TIME_UNIT_HOUR'}
        result = kimi_api._normalize_usage(payload)
        self.assertEqual(set(result), {'seven_day'})

    def test_zero_limit_skipped(self):
        result = kimi_api._normalize_usage(_usage_payload(weekly_limit='0'))
        self.assertNotIn('seven_day', result)

    def test_missing_usage_section(self):
        payload = _usage_payload()
        del payload['usage']
        result = kimi_api._normalize_usage(payload)
        self.assertEqual(set(result), {'five_hour'})

    def test_null_usage_section(self):
        payload = _usage_payload()
        payload['usage'] = None
        result = kimi_api._normalize_usage(payload)
        self.assertEqual(set(result), {'five_hour'})

    def test_missing_limits_section(self):
        payload = _usage_payload()
        del payload['limits']
        result = kimi_api._normalize_usage(payload)
        self.assertEqual(set(result), {'seven_day'})

    def test_null_limits_section(self):
        payload = _usage_payload()
        payload['limits'] = None
        result = kimi_api._normalize_usage(payload)
        self.assertEqual(set(result), {'seven_day'})

    def test_empty_payload(self):
        self.assertEqual(kimi_api._normalize_usage({}), {})

    def test_malformed_limits_entry_skipped(self):
        payload = _usage_payload()
        payload['limits'].append('not a dict')
        result = kimi_api._normalize_usage(payload)
        self.assertEqual(set(result), {'five_hour', 'seven_day'})

    def test_weekly_quota_wins_over_matching_window(self):
        payload = _usage_payload()
        payload['limits'].append({
            'window': {'duration': 7, 'timeUnit': 'TIME_UNIT_DAY'},
            'detail': {'limit': '100', 'used': '100', 'resetTime': ''},
        })
        result = kimi_api._normalize_usage(payload)
        self.assertAlmostEqual(result['seven_day']['utilization'], 214 / 2048 * 100)

    def test_utilization_clamped_to_100(self):
        result = kimi_api._normalize_usage(_usage_payload(weekly_limit='100', weekly_used='150'))
        self.assertEqual(result['seven_day']['utilization'], 100.0)

    def test_unparseable_reset_time_becomes_empty(self):
        payload = _usage_payload()
        payload['usage']['resetTime'] = 'tomorrow'
        result = kimi_api._normalize_usage(payload)
        self.assertEqual(result['seven_day']['resets_at'], '')

    def test_naive_reset_time_gets_utc(self):
        payload = _usage_payload()
        payload['usage']['resetTime'] = '2026-01-09T15:23:13'
        result = kimi_api._normalize_usage(payload)
        self.assertTrue(result['seven_day']['resets_at'].endswith('+00:00'))


class TestFetchUsage(unittest.TestCase):
    """Tests for fetch_usage() and its error mapping."""

    def setUp(self):
        _clear_raw_cache()

    def _response(self, payload, status=200):
        response = MagicMock(spec=requests.Response)
        response.status_code = status
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def _http_error(self, status, headers=None):
        response = MagicMock(spec=requests.Response)
        response.status_code = status
        response.headers = headers or {}
        error = requests.HTTPError(response=response)
        response.raise_for_status.side_effect = error
        return response

    def test_no_token_returns_error(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value=None):
            result = kimi_api.fetch_usage()
        self.assertIn('error', result)

    def test_successful_fetch(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._response(_usage_payload())):
            result = kimi_api.fetch_usage()
        self.assertIn('five_hour', result)
        self.assertIn('seven_day', result)

    def test_401_marks_auth_error(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._http_error(401)):
            result = kimi_api.fetch_usage()
        self.assertTrue(result['auth_error'])

    def test_429_marks_rate_limited(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._http_error(429)):
            result = kimi_api.fetch_usage()
        self.assertTrue(result['rate_limited'])

    def test_429_parses_retry_after(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._http_error(429, {'Retry-After': '90'})):
            result = kimi_api.fetch_usage()
        self.assertEqual(result['retry_after'], 90)

    def test_429_ignores_invalid_retry_after(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._http_error(429, {'Retry-After': 'soon'})):
            result = kimi_api.fetch_usage()
        self.assertNotIn('retry_after', result)

    def test_500_is_a_plain_error(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._http_error(503)):
            result = kimi_api.fetch_usage()
        self.assertIn('error', result)
        self.assertNotIn('auth_error', result)
        self.assertNotIn('rate_limited', result)

    def test_connection_failure_returns_error(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', side_effect=requests.ConnectionError):
            result = kimi_api.fetch_usage()
        self.assertIn('error', result)

    def test_non_object_body_returns_error(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._response(['nope'])):
            result = kimi_api.fetch_usage()
        self.assertIn('error', result)

    def test_back_to_back_reads_share_one_request(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._response(_usage_payload())) as mock_get:
            kimi_api.fetch_usage()
            kimi_api.fetch_profile()
        mock_get.assert_called_once()

    def test_cache_not_reused_for_a_different_token(self):
        with patch('agentpulse.kimi_api.read_access_token', side_effect=['tok1', 'tok2']), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._response(_usage_payload())) as mock_get:
            kimi_api.fetch_usage()
            kimi_api.fetch_usage()
        self.assertEqual(mock_get.call_count, 2)

    def test_cache_expires(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._response(_usage_payload())) as mock_get:
            kimi_api.fetch_usage()
            kimi_api._raw_cache['ts'] = time.time() - kimi_api._RAW_CACHE_TTL - 1
            kimi_api.fetch_usage()
        self.assertEqual(mock_get.call_count, 2)


class TestFetchProfile(unittest.TestCase):
    """Tests for fetch_profile()."""

    def setUp(self):
        _clear_raw_cache()

    def _response(self, payload):
        response = MagicMock(spec=requests.Response)
        response.status_code = 200
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_none_on_error(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value=None):
            self.assertIsNone(kimi_api.fetch_profile())

    def test_tier_derived_from_weekly_limit(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._response(_usage_payload(weekly_limit='7168'))):
            profile = kimi_api.fetch_profile()
        self.assertEqual(profile['organization']['organization_type'], 'Allegretto')

    def test_unknown_limit_yields_no_tier(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._response(_usage_payload(weekly_limit='999'))):
            profile = kimi_api.fetch_profile()
        self.assertEqual(profile['organization']['organization_type'], '')

    def test_profile_carries_no_email(self):
        with patch('agentpulse.kimi_api.read_access_token', return_value='tok'), \
             patch('agentpulse.kimi_api.requests.get', return_value=self._response(_usage_payload())):
            profile = kimi_api.fetch_profile()
        self.assertEqual(profile['account']['email'], '')


class TestEndpointConstant(unittest.TestCase):
    """The endpoint must stay a literal so it can be audited at a glance."""

    def test_usage_url(self):
        self.assertEqual(kimi_api.API_URL_USAGE, 'https://api.kimi.com/coding/v1/usages')

    def test_credentials_path_is_read_only_location(self):
        self.assertEqual(kimi_api.KIMI_CREDENTIALS.name, 'kimi-code.json')
        self.assertEqual(kimi_api.KIMI_CREDENTIALS.parent.name, 'credentials')


if __name__ == '__main__':
    unittest.main()
