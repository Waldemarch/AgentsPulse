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
    """Read the current access token from the Codex auth file."""
    if not CODEX_AUTH_FILE.exists():
        return None
    try:
        auth = json.loads(CODEX_AUTH_FILE.read_text(encoding='utf-8'))
        return auth.get('tokens', {}).get('access_token') or None
    except (json.JSONDecodeError, KeyError):
        return None


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
    headers = api_headers()
    if not headers:
        return {'error': T.get('codex_no_token', 'No Codex token. Log in to Codex CLI first.')}

    try:
        req = urllib.request.Request(API_URL_USAGE, headers=headers)
        with _opener.open(req, timeout=10) as resp:
            body = json.loads(resp.read().decode('utf-8'))
            return _normalize_usage(body)
    except urllib.error.HTTPError as e:
        code = e.code
        if code == 401:
            return {
                'error': T.get('codex_auth_expired', 'Codex session expired - please open Codex CLI to log in again.'),
                'auth_error': True,
            }
        if code == 429:
            extra: dict[str, Any] = {}
            retry = _parse_retry_after(e)
            if retry is not None:
                extra['retry_after'] = retry
            return {**extra, 'error': T['http_error'].format(code=429), 'rate_limited': True}
        if 500 <= code < 600:
            return {'error': T['server_error'].format(code=code)}
        return {'error': T['http_error'].format(code=code)}
    except urllib.error.URLError:
        return {'error': T.get('codex_connection_error', 'Could not connect to OpenAI API.')}
    except Exception:
        return {'error': T.get('codex_connection_error', 'Could not connect to OpenAI API.')}


def fetch_profile() -> dict[str, Any] | None:
    """Fetch Codex account profile embedded in the usage endpoint response."""
    headers = api_headers()
    if not headers:
        return None
    try:
        req = urllib.request.Request(API_URL_USAGE, headers=headers)
        with _opener.open(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode('utf-8'))
            return {
                'account': {'email': raw.get('email', '')},
                'organization': {'organization_type': raw.get('plan_type', '')},
            }
    except Exception:
        return None


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
