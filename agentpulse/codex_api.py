"""
Codex API Client
=================

Reads Codex CLI OAuth credentials and communicates with the
ChatGPT backend API.  This is the only module that handles Codex credentials.

Network communication exclusively with ``chatgpt.com``.
Credentials used only in HTTP Authorization headers.

Uses ``urllib.request`` instead of ``requests`` because Cloudflare's TLS
fingerprint checks reject the ``urllib3`` TLS handshake used by ``requests``.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .i18n import T

# Persistent opener with cookie jar - Cloudflare sets session cookies on first
# request that must be echoed back on subsequent requests.
_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))

# The usage endpoint returns both quota and profile fields in one payload, so
# usage and profile reads that happen back-to-back (startup, popup refresh)
# share a single round-trip instead of hitting this rate-limited endpoint twice.
_RAW_CACHE_TTL = 5.0
_raw_cache_lock = threading.Lock()
_raw_cache: dict[str, Any] = {'ts': 0.0, 'token': None, 'body': None}

__all__ = [
    'API_URL_USAGE', 'CODEX_CONFIG_DIR', 'CODEX_AUTH_FILE',
    'read_access_token', 'api_headers', 'fetch_usage', 'fetch_profile',
]

API_URL_USAGE = 'https://chatgpt.com/backend-api/codex/usage'
CODEX_CONFIG_DIR = (
    Path(os.environ['CODEX_CONFIG_DIR']) if os.environ.get('CODEX_CONFIG_DIR')
    else Path.home() / '.codex'
)
CODEX_AUTH_FILE = CODEX_CONFIG_DIR / 'auth.json'
_FALLBACK_USER_AGENT = 'codex-cli/0.124.0'


def read_access_token() -> str | None:
    """Read the current access token from the Codex auth file.

    Returns None when the file is missing, unreadable, or shaped
    differently than expected (e.g. ``tokens`` is ``null`` or the document
    root is not an object) rather than raising.
    """
    try:
        auth = json.loads(CODEX_AUTH_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(auth, dict):
        return None
    tokens = auth.get('tokens')
    token = tokens.get('access_token') if isinstance(tokens, dict) else None
    return token if isinstance(token, str) and token else None


def api_headers() -> dict[str, str] | None:
    """Return auth headers for the ChatGPT backend API, or None."""
    token = read_access_token()
    if not token:
        return None
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': _FALLBACK_USER_AGENT,
    }


def fetch_usage() -> dict[str, Any]:
    """Fetch usage data from the Codex usage API and normalize it to Claude-compatible format."""
    body, error = _request_usage()
    if error is not None:
        return error
    return _normalize_usage(body or {})


def fetch_profile() -> dict[str, Any] | None:
    """Return the Codex account profile embedded in the usage endpoint response."""
    body, error = _request_usage()
    if error is not None or body is None:
        return None
    return {
        'account': {'email': body.get('email', '')},
        'organization': {'organization_type': body.get('plan_type', '')},
    }


def _request_usage() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return the raw usage payload and an error dict, exactly one of which is set.

    A fresh payload is reused for ``_RAW_CACHE_TTL`` seconds so that a usage and
    profile read issued back-to-back only make one request to the endpoint.
    """
    headers = api_headers()
    if not headers:
        return None, {'error': T.get('codex_no_token', 'No Codex token. Log in to Codex CLI first.')}

    token = headers['Authorization']
    now = time.time()
    with _raw_cache_lock:
        if _raw_cache['body'] is not None and _raw_cache['token'] == token and now - _raw_cache['ts'] < _RAW_CACHE_TTL:
            return _raw_cache['body'], None

    try:
        req = urllib.request.Request(API_URL_USAGE, headers=headers)
        with _opener.open(req, timeout=10) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return None, _http_error(e)
    except urllib.error.URLError:
        return None, {'error': T.get('codex_connection_error', 'Could not connect to OpenAI API.')}
    except Exception:
        return None, {'error': T.get('codex_connection_error', 'Could not connect to OpenAI API.')}

    with _raw_cache_lock:
        _raw_cache.update(ts=time.time(), token=token, body=body)
    return body, None


def _http_error(error: urllib.error.HTTPError) -> dict[str, Any]:
    """Map an HTTP error from the usage endpoint to a normalized error dict."""
    code = error.code
    if code == 401:
        return {
            'error': T.get('codex_auth_expired', 'Codex session expired - please open Codex CLI to log in again.'),
            'auth_error': True,
        }
    if code == 429:
        extra: dict[str, Any] = {}
        retry = _parse_retry_after(error)
        if retry is not None:
            extra['retry_after'] = retry
        return {**extra, 'error': T['http_error'].format(code=429), 'rate_limited': True}
    if 500 <= code < 600:
        return {'error': T['server_error'].format(code=code)}
    return {'error': T['http_error'].format(code=code)}


# Helpers


def _normalize_usage(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert Codex API response to Claude-compatible format.

    Maps ``primary_window`` to ``five_hour`` and ``secondary_window`` to
    ``seven_day``.  Converts Unix timestamps to ISO 8601 strings.
    """
    result: dict[str, Any] = {}
    rate_limit = raw.get('rate_limit') or {}

    primary = rate_limit.get('primary_window')
    if primary:
        result['five_hour'] = {
            'utilization': float(primary.get('used_percent') or 0),
            'resets_at': _unix_to_iso(primary.get('reset_at')),
        }

    secondary = rate_limit.get('secondary_window')
    if secondary:
        result['seven_day'] = {
            'utilization': float(secondary.get('used_percent') or 0),
            'resets_at': _unix_to_iso(secondary.get('reset_at')),
        }

    return result


def _unix_to_iso(timestamp: Any) -> str:
    """Convert a Unix timestamp to an ISO 8601 string, or return ''."""
    if timestamp is None:
        return ''
    try:
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
    except Exception:
        return ''


def _parse_retry_after(error: urllib.error.HTTPError) -> int | None:
    """Parse the ``Retry-After`` header as an integer number of seconds."""
    raw = error.headers.get('Retry-After')
    if raw is None:
        return None
    try:
        return max(int(raw), 0)
    except (ValueError, TypeError):
        return None
