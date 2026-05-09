"""Claude OAuth API access for Agents Pulse."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from .i18n import T

__all__ = [
    'API_URL_PROFILE', 'API_URL_USAGE', 'CLAUDE_CONFIG_DIR', 'CLAUDE_CREDENTIALS',
    'api_headers', 'fetch_profile', 'fetch_usage', 'read_access_token',
]

API_URL_USAGE = 'https://api.anthropic.com/api/oauth/usage'
API_URL_PROFILE = 'https://api.anthropic.com/api/oauth/profile'
_BETA_HEADER = 'oauth-2025-04-20'
_DEFAULT_UA = 'claude-code/2.1.85'


def _config_dir() -> Path:
    configured = os.environ.get('CLAUDE_CONFIG_DIR')
    return Path(configured) if configured else Path.home() / '.claude'


CLAUDE_CONFIG_DIR = _config_dir()
CLAUDE_CREDENTIALS = CLAUDE_CONFIG_DIR / '.credentials.json'


def _load_credentials() -> dict[str, Any] | None:
    try:
        raw = CLAUDE_CREDENTIALS.read_text(encoding='utf-8')
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_access_token() -> str | None:
    """Return the Claude OAuth access token from the local CLI state."""
    data = _load_credentials()
    oauth = data.get('claudeAiOauth') if data else None
    if not isinstance(oauth, dict):
        return None
    token = oauth.get('accessToken')
    return token if isinstance(token, str) and token else None


def _user_agent() -> str:
    try:
        from .claude_cli import CLAUDE_CLI_PATH, cli_version

        version = cli_version(CLAUDE_CLI_PATH)
    except Exception:
        version = ''
    return f'claude-code/{version}' if version else _DEFAULT_UA


def api_headers() -> dict[str, str] | None:
    """Build request headers, or None when the user is not logged in."""
    token = read_access_token()
    if token is None:
        return None
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': _user_agent(),
        'anthropic-beta': _BETA_HEADER,
    }


def _server_message(response: requests.Response | None) -> str | None:
    if response is None:
        return None
    try:
        body = response.json()
    except Exception:
        return None
    error = body.get('error') if isinstance(body, dict) else None
    message = error.get('message') if isinstance(error, dict) else None
    if not isinstance(message, str):
        return None
    for suffix in (' Please try again later.', ' Please try again later'):
        message = message.removesuffix(suffix)
    message = message.strip()
    return message or None


def _retry_after(response: requests.Response | None) -> int | None:
    if response is None:
        return None
    value = response.headers.get('Retry-After')
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_server_message(response: requests.Response | None) -> str | None:
    return _server_message(response)


def _parse_retry_after(response: requests.Response | None) -> int | None:
    return _retry_after(response)


# Delays (seconds) between retry attempts for transient failures; first entry is always 0 (initial attempt).
_RETRY_DELAYS = (0, 2, 4)


def _request_json(url: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    headers = api_headers()
    if headers is None:
        return None, {'error': T['no_token']}

    last_error: dict[str, Any] = {'error': T['connection_error']}
    for delay in _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            return (data if isinstance(data, dict) else {}, None)
        except requests.ConnectionError:
            last_error = {'error': T['connection_error']}
        except requests.HTTPError as exc:
            response = exc.response
            code = response.status_code if response is not None else 0
            details: dict[str, Any] = {}
            message = _server_message(response)
            if message:
                details['server_message'] = message
            if code == 401:
                details.update({'error': T['auth_expired'], 'auth_error': True})
                return None, details  # auth errors are handled at cache level; no retry
            elif code == 429:
                retry = _retry_after(response)
                if retry is not None:
                    details['retry_after'] = retry
                details.update({'error': T['http_error'].format(code=429), 'rate_limited': True})
                return None, details  # backoff managed by cache; no retry here
            elif 500 <= code < 600:
                last_error = dict(details, error=T['server_error'].format(code=code))
            else:
                details['error'] = T['http_error'].format(code=code or '?')
                return None, details  # other 4xx are definitive; no retry
        except Exception:
            last_error = {'error': T['connection_error']}

    return None, last_error


def fetch_usage() -> dict[str, Any]:
    """Fetch Claude usage, returning either the payload or an error dict."""
    data, error = _request_json(API_URL_USAGE)
    return error if error is not None else data or {}


def fetch_profile() -> dict[str, Any] | None:
    """Fetch Claude account profile. Errors are intentionally silent."""
    data, error = _request_json(API_URL_PROFILE)
    return None if error is not None else data
