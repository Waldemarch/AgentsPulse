"""
Kimi API Client
================

Reads Kimi Code CLI credentials and communicates with the Kimi Code
platform API.  This is the only module that handles Kimi credentials.

Network communication exclusively with ``api.kimi.com``.
Credentials used only in HTTP Authorization headers.

The credential file belongs to the Kimi Code CLI and is only ever read:
the refresh token is never used and the file is never rewritten, so an
expired session is renewed by signing in with the CLI again.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .formatting import period_to_field_name
from .i18n import T

__all__ = [
    'API_URL_USAGE', 'KIMI_CODE_HOME', 'KIMI_CREDENTIALS',
    'read_access_token', 'api_headers', 'fetch_usage', 'fetch_profile',
]

API_URL_USAGE = 'https://api.kimi.com/coding/v1/usages'

# The endpoint carries the weekly quota and the rolling windows in one payload,
# so usage and profile reads that happen back-to-back (startup, popup refresh)
# share a single round-trip.
_RAW_CACHE_TTL = 5.0
_raw_cache_lock = threading.Lock()
_raw_cache: dict[str, Any] = {'ts': 0.0, 'token': None, 'body': None}

_USER_AGENT = 'kimi-code-cli'
_WEEKLY_FIELD = 'seven_day'
# Window lengths reported by the API, expressed in seconds.
_TIME_UNIT_SECONDS = {
    'TIME_UNIT_SECOND': 1,
    'TIME_UNIT_MINUTE': 60,
    'TIME_UNIT_HOUR': 3600,
    'TIME_UNIT_DAY': 24 * 3600,
}
# Weekly request allowance of each Kimi For Coding membership tier.  Used only
# to name the plan in the profile view; an unknown allowance yields no name.
_TIER_BY_WEEKLY_LIMIT = {1024: 'Andante', 2048: 'Moderato', 7168: 'Allegretto'}
# Credential keys the Kimi Code CLI is known to use, in the order they are tried.
_TOKEN_PATHS = (('access_token',), ('tokens', 'access_token'), ('data', 'access_token'))
_EXPIRY_PATHS = (('expires_at',), ('tokens', 'expires_at'), ('data', 'expires_at'))


def _config_dir() -> Path:
    configured = os.environ.get('KIMI_CODE_HOME')
    return Path(configured) if configured else Path.home() / '.kimi-code'


KIMI_CODE_HOME = _config_dir()
KIMI_CREDENTIALS = KIMI_CODE_HOME / 'credentials' / 'kimi-code.json'


def read_access_token() -> str | None:
    """Return the access token stored by the Kimi Code CLI, or None.

    Returns None when the file is missing, unreadable, shaped differently
    than expected, or when it declares the token as already expired.
    """
    try:
        credentials = json.loads(KIMI_CREDENTIALS.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(credentials, dict):
        return None

    token = _first_string(credentials, _TOKEN_PATHS)
    if token is None:
        return None
    if _is_expired(credentials):
        return None
    return token


def api_headers() -> dict[str, str] | None:
    """Return auth headers for the Kimi Code API, or None."""
    token = read_access_token()
    if not token:
        return None
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': _USER_AGENT,
    }


def fetch_usage() -> dict[str, Any]:
    """Fetch usage data from the Kimi Code API and normalize it to Claude-compatible format."""
    body, error = _request_usage()
    if error is not None:
        return error
    return _normalize_usage(body or {})


def fetch_profile() -> dict[str, Any] | None:
    """Return the Kimi account profile derived from the usage endpoint response.

    The endpoint carries no account identity, so only the membership tier is
    reported, inferred from the weekly request allowance.
    """
    body, error = _request_usage()
    if error is not None or body is None:
        return None
    weekly = body.get('usage') or {}
    return {
        'account': {'email': ''},
        'organization': {'organization_type': _tier_name(weekly.get('limit'))},
    }


def _request_usage() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return the raw usage payload and an error dict, exactly one of which is set.

    A fresh payload is reused for ``_RAW_CACHE_TTL`` seconds so that a usage and
    profile read issued back-to-back only make one request to the endpoint.
    """
    headers = api_headers()
    if not headers:
        return None, {'error': T.get('kimi_no_token', 'No Kimi token. Log in to Kimi Code CLI first.')}

    token = headers['Authorization']
    now = time.time()
    with _raw_cache_lock:
        if _raw_cache['body'] is not None and _raw_cache['token'] == token and now - _raw_cache['ts'] < _RAW_CACHE_TTL:
            return _raw_cache['body'], None

    try:
        response = requests.get(API_URL_USAGE, headers=headers, timeout=10)
        response.raise_for_status()
        body = response.json()
    except requests.HTTPError as exc:
        return None, _http_error(exc.response)
    except Exception:
        return None, {'error': T.get('kimi_connection_error', 'Could not connect to Kimi API.')}

    if not isinstance(body, dict):
        return None, {'error': T.get('kimi_connection_error', 'Could not connect to Kimi API.')}

    with _raw_cache_lock:
        _raw_cache.update(ts=time.time(), token=token, body=body)
    return body, None


def _http_error(response: requests.Response | None) -> dict[str, Any]:
    """Map an HTTP error from the usage endpoint to a normalized error dict."""
    code = response.status_code if response is not None else 0
    if code == 401:
        return {
            'error': T.get('kimi_auth_expired', 'Kimi session expired - please open Kimi Code CLI to log in again.'),
            'auth_error': True,
        }
    if code == 429:
        extra: dict[str, Any] = {}
        retry = _parse_retry_after(response)
        if retry is not None:
            extra['retry_after'] = retry
        return {**extra, 'error': T['http_error'].format(code=429), 'rate_limited': True}
    if 500 <= code < 600:
        return {'error': T['server_error'].format(code=code)}
    return {'error': T['http_error'].format(code=code or '?')}


# Helpers


def _normalize_usage(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a Kimi usage response to Claude-compatible format.

    The top-level ``usage`` object is the weekly request allowance and maps to
    ``seven_day``.  Each entry of ``limits`` describes a rolling window by its
    raw duration, which is translated into the matching field name (a 300 minute
    window becomes ``five_hour``).  Windows the shared field vocabulary cannot
    name are skipped rather than given an invented name.
    """
    result: dict[str, Any] = {}

    for entry in raw.get('limits') or []:
        if not isinstance(entry, dict):
            continue
        field = _window_field(entry.get('window'))
        if field is None or field in result:
            continue
        quota = _quota_entry(entry.get('detail'))
        if quota is not None:
            result[field] = quota

    weekly = _quota_entry(raw.get('usage'))
    if weekly is not None:
        result[_WEEKLY_FIELD] = weekly

    return result


def _window_field(window: Any) -> str | None:
    """Return the quota field name for a ``{'duration', 'timeUnit'}`` window."""
    if not isinstance(window, dict):
        return None
    unit_seconds = _TIME_UNIT_SECONDS.get(window.get('timeUnit'))
    if unit_seconds is None:
        return None
    duration = _to_float(window.get('duration'))
    if duration is None or duration <= 0:
        return None
    return period_to_field_name(int(duration * unit_seconds))


def _quota_entry(detail: Any) -> dict[str, Any] | None:
    """Convert a ``{'limit', 'used', 'resetTime'}`` object into a quota field."""
    if not isinstance(detail, dict):
        return None
    limit = _to_float(detail.get('limit'))
    used = _to_float(detail.get('used'))
    if limit is None or used is None or limit <= 0:
        return None
    return {
        'utilization': max(0.0, min(100.0, used / limit * 100.0)),
        'resets_at': _normalize_reset_time(detail.get('resetTime')),
    }


def _normalize_reset_time(value: Any) -> str:
    """Return an ISO 8601 timestamp Python can parse, or ''.

    Kimi reports nanosecond precision, which ``datetime.fromisoformat``
    rejects, so the fractional part is truncated to microseconds.
    """
    if not isinstance(value, str) or not value:
        return ''
    text = value.replace('Z', '+00:00')
    head, sep, tail = text.partition('.')
    if sep:
        digits = ''
        for character in tail:
            if not character.isdigit():
                break
            digits += character
        text = f'{head}.{digits[:6]}{tail[len(digits):]}'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return ''
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _tier_name(weekly_limit: Any) -> str:
    """Return the membership tier matching a weekly request allowance, or ''."""
    limit = _to_float(weekly_limit)
    if limit is None:
        return ''
    return _TIER_BY_WEEKLY_LIMIT.get(int(limit), '')


def _to_float(value: Any) -> float | None:
    """Parse a number that the API may send as a string."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_string(credentials: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> str | None:
    """Return the first non-empty string found at any of the given key paths."""
    for path in paths:
        value = _lookup(credentials, path)
        if isinstance(value, str) and value:
            return value
    return None


def _is_expired(credentials: dict[str, Any]) -> bool:
    """Report whether the credential file declares the token as expired."""
    for path in _EXPIRY_PATHS:
        expires_at = _expiry_seconds(_lookup(credentials, path))
        if expires_at is not None:
            return expires_at <= time.time()
    return False


def _expiry_seconds(value: Any) -> float | None:
    """Parse an expiry given as Unix seconds, Unix milliseconds, or ISO 8601."""
    if isinstance(value, str) and not value.replace('.', '', 1).isdigit():
        parsed = _normalize_reset_time(value)
        if not parsed:
            return None
        return datetime.fromisoformat(parsed).timestamp()
    seconds = _to_float(value)
    if seconds is None or seconds <= 0:
        return None
    # Values far beyond the year 3000 are milliseconds, not seconds.
    return seconds / 1000.0 if seconds > 1e11 else seconds


def _lookup(credentials: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = credentials
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _parse_retry_after(response: requests.Response | None) -> int | None:
    """Parse the ``Retry-After`` header as an integer number of seconds."""
    if response is None:
        return None
    raw = response.headers.get('Retry-After')
    if raw is None:
        return None
    try:
        return max(int(raw), 0)
    except (ValueError, TypeError):
        return None
