"""
Local Dashboard
===============

Private localhost dashboard with persistent usage history.
"""
from __future__ import annotations

import csv
import hmac
import json
import logging
import mimetypes
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .claude_cli import find_installations
from .codex_cli import codex_version
from .formatting import field_period, popup_label, time_until
from .settings import (
    DASHBOARD_HOST, DASHBOARD_PORT, HISTORY_PERSIST,
    dashboard_settings, history_write_path, save_dashboard_settings, settings_write_path,
)

if TYPE_CHECKING:
    from .app import AgentPulse

__all__ = ['DashboardHistory', 'DashboardServer']

log = logging.getLogger(__name__)

_DASHBOARD_DIR = Path(__file__).parent / 'dashboard'
_MAX_AGE_SECONDS = 30 * 24 * 3600
# 30 days of two providers polling every 180s is ~29k snapshots; leave headroom for fast-poll bursts.
_MAX_SAMPLES = 40000
# Stale history-file lines tolerated before the file is compacted (rewritten from memory).
_HISTORY_COMPACT_SLACK = 4000
_RANGES = {
    '24h': 24 * 3600,
    '7d': 7 * 24 * 3600,
    '30d': 30 * 24 * 3600,
}


@dataclass(frozen=True)
class _Snapshot:
    ts: float
    provider: str
    usage: dict[str, dict[str, Any]]
    error: str | None


class DashboardHistory:
    """Ring buffer of provider usage snapshots with optional JSONL persistence.

    When a ``path`` is given, snapshots are appended to that file and loaded
    back on startup, so dashboard history survives application restarts.
    The file holds only what :meth:`record` sanitizes - quota percentages,
    reset timestamps, and error strings.  It never contains tokens, account
    identifiers, or profile data.
    """

    def __init__(self, max_age_seconds: int = _MAX_AGE_SECONDS, max_samples: int = _MAX_SAMPLES, path: Path | None = None) -> None:
        self.max_age_seconds = max_age_seconds
        self.max_samples = max_samples
        self.path = path
        self._lock = threading.Lock()
        self._items: deque[_Snapshot] = deque()
        self._file_records = 0
        self._write_failed = False
        if path is not None:
            self._load()

    def record(self, provider: str, data: dict[str, Any], *, ts: float | None = None) -> None:
        """Record one sanitized provider snapshot.

        Tokens, account identifiers, and raw profile data are intentionally
        not stored.  Only quota percentages and reset timestamps are kept.
        """
        now = time.time() if ts is None else ts
        usage: dict[str, dict[str, Any]] = {}

        if 'error' not in data:
            for key, value in data.items():
                if key == 'extra_usage':
                    continue
                if not isinstance(value, dict) or value.get('utilization') is None:
                    continue
                usage[key] = {
                    'utilization': float(value.get('utilization') or 0),
                    'resets_at': value.get('resets_at', '') or '',
                }

        error = data.get('error') if isinstance(data.get('error'), str) else None
        snapshot = _Snapshot(ts=now, provider=provider, usage=usage, error=error)
        with self._lock:
            self._items.append(snapshot)
            self._prune_locked(now)
            self._persist_locked(snapshot)

    def rows(self, range_name: str = '24h', *, now: float | None = None) -> list[dict[str, Any]]:
        """Return flattened rows for the requested time range."""
        cutoff = (time.time() if now is None else now) - _RANGES.get(range_name, _RANGES['24h'])
        rows: list[dict[str, Any]] = []
        with self._lock:
            items = list(self._items)

        for item in items:
            if item.ts < cutoff:
                continue
            if not item.usage:
                rows.append({
                    'ts': item.ts, 'provider': item.provider, 'field': '',
                    'utilization': None, 'resets_at': '', 'error': item.error,
                })
                continue
            for field, entry in item.usage.items():
                rows.append({
                    'ts': item.ts,
                    'provider': item.provider,
                    'field': field,
                    'utilization': entry['utilization'],
                    'resets_at': entry['resets_at'],
                    'error': item.error,
                })
        return rows

    def to_csv(self, range_name: str = '24h') -> str:
        """Return history rows as CSV."""
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=['timestamp', 'provider', 'field', 'utilization', 'resets_at', 'error'])
        writer.writeheader()
        for row in self.rows(range_name):
            writer.writerow({
                'timestamp': datetime.fromtimestamp(row['ts'], tz=timezone.utc).isoformat(),
                'provider': row['provider'],
                'field': row['field'],
                'utilization': '' if row['utilization'] is None else row['utilization'],
                'resets_at': row['resets_at'],
                'error': row['error'] or '',
            })
        return output.getvalue()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.max_age_seconds
        while self._items and (len(self._items) > self.max_samples or self._items[0].ts < cutoff):
            self._items.popleft()

    def _load(self) -> None:
        """Restore snapshots from the history file, compacting it when stale."""
        assert self.path is not None
        try:
            lines = self.path.read_text(encoding='utf-8').splitlines()
        except OSError:
            return

        now = time.time()
        with self._lock:
            for line in lines:
                snapshot = _parse_history_line(line)
                if snapshot is not None:
                    self._items.append(snapshot)
            self._prune_locked(now)
            self._file_records = len(lines)
            if self._file_records != len(self._items):
                try:
                    self._rewrite_locked()
                except OSError:
                    pass

    def _persist_locked(self, snapshot: _Snapshot) -> None:
        """Append one snapshot to the history file, compacting when it grows stale."""
        if self.path is None:
            return
        try:
            if self._file_records - len(self._items) > _HISTORY_COMPACT_SLACK:
                self._rewrite_locked()
            else:
                with self.path.open('a', encoding='utf-8') as handle:
                    handle.write(_history_line(snapshot))
                self._file_records += 1
            self._write_failed = False
        except OSError as exc:
            if not self._write_failed:
                log.warning('history write failed (%s): %s', self.path, exc)
            self._write_failed = True

    def _rewrite_locked(self) -> None:
        """Rewrite the history file from the in-memory buffer."""
        assert self.path is not None
        temp_path = self.path.with_name(self.path.name + '.tmp')
        with temp_path.open('w', encoding='utf-8') as handle:
            for item in self._items:
                handle.write(_history_line(item))
        temp_path.replace(self.path)
        self._file_records = len(self._items)


def _history_line(snapshot: _Snapshot) -> str:
    record: dict[str, Any] = {'ts': snapshot.ts, 'provider': snapshot.provider, 'usage': snapshot.usage}
    if snapshot.error:
        record['error'] = snapshot.error
    return json.dumps(record, separators=(',', ':'), ensure_ascii=False) + '\n'


def _parse_history_line(line: str) -> _Snapshot | None:
    """Parse one JSONL history line, returning None for corrupt or foreign data."""
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None

    ts = record.get('ts')
    provider = record.get('provider')
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not isinstance(provider, str) or not provider:
        return None

    usage: dict[str, dict[str, Any]] = {}
    raw_usage = record.get('usage')
    if isinstance(raw_usage, dict):
        for field, entry in raw_usage.items():
            if not isinstance(field, str) or not isinstance(entry, dict):
                continue
            utilization = entry.get('utilization')
            if isinstance(utilization, bool) or not isinstance(utilization, (int, float)):
                continue
            resets_at = entry.get('resets_at')
            usage[field] = {
                'utilization': float(utilization),
                'resets_at': resets_at if isinstance(resets_at, str) else '',
            }

    error = record.get('error')
    return _Snapshot(ts=float(ts), provider=provider, usage=usage, error=error if isinstance(error, str) else None)


class DashboardServer:
    """Local HTTP dashboard bound to localhost only.

    A random per-run session token protects all POST endpoints against
    cross-site request forgery: browsers can be tricked into sending POST
    requests to localhost from malicious web pages, so the localhost bind
    alone is not sufficient.  The token is passed in the URL when the
    dashboard is opened from the tray menu and echoed back by the
    dashboard's JavaScript in a request header.
    """

    def __init__(self, app: AgentPulse, host: str = DASHBOARD_HOST, port: int = DASHBOARD_PORT, history_path: Path | None = None) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.token = secrets.token_urlsafe(32)
        if history_path is None and HISTORY_PERSIST:
            history_path = history_write_path()
        self.history = DashboardHistory(path=history_path)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        port = self._httpd.server_address[1] if self._httpd else self.port
        return f'http://{self.host}:{port}/'

    def start(self) -> str:
        """Start the dashboard server if needed and return its URL."""
        if self._httpd is not None:
            return self.url

        app = self.app
        history = self.history
        token = self.token

        class Handler(_DashboardHandler):
            dashboard_app = app
            dashboard_history = history
            dashboard_token = token

        last_error: OSError | None = None
        ports = [0] if self.port == 0 else range(self.port, min(self.port + 20, 65536))
        for port in ports:
            try:
                self._httpd = ThreadingHTTPServer((self.host, port), Handler)
                break
            except OSError as exc:
                last_error = exc
        if self._httpd is None:
            raise last_error or OSError(f'Could not start dashboard on {self.host}:{self.port}')

        bound_port = self._httpd.server_address[1]
        Handler.allowed_hosts = frozenset({self.host, f'{self.host}:{bound_port}', 'localhost', f'localhost:{bound_port}'})
        Handler.allowed_origins = frozenset({f'http://{self.host}:{bound_port}', f'http://localhost:{bound_port}'})

        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def open(self) -> None:
        """Start the dashboard and open it in the default browser."""
        webbrowser.open(f'{self.start()}?token={self.token}')

    def stop(self) -> None:
        """Stop the dashboard server."""
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None


class _DashboardHandler(BaseHTTPRequestHandler):
    dashboard_app: AgentPulse
    dashboard_history: DashboardHistory
    # Safe defaults: requests are rejected until DashboardServer.start() fills these in.
    dashboard_token: str = ''
    allowed_hosts: frozenset[str] = frozenset()
    allowed_origins: frozenset[str] = frozenset()

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _request_blocked(self) -> bool:
        """Reject non-loopback clients and forged Host headers (DNS rebinding)."""
        if self.client_address[0] not in {'127.0.0.1', '::1'}:
            return True
        host = (self.headers.get('Host') or '').strip().lower()
        return host not in self.allowed_hosts

    def _post_blocked(self) -> bool:
        """Reject cross-origin POSTs and requests without the session token (CSRF)."""
        if self._request_blocked():
            return True
        origin = (self.headers.get('Origin') or '').strip().lower()
        if origin and origin not in self.allowed_origins:
            return True
        if not self.dashboard_token:
            return True
        provided = self.headers.get('X-AgentsPulse-Token') or ''
        return not hmac.compare_digest(provided.encode('utf-8'), self.dashboard_token.encode('utf-8'))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if self._request_blocked():
            self.send_error(403)
            return

        if path == '/':
            self._send_file(_DASHBOARD_DIR / 'index.html')
        elif path == '/dashboard.css':
            self._send_file(_DASHBOARD_DIR / 'dashboard.css')
        elif path == '/dashboard.js':
            self._send_file(_DASHBOARD_DIR / 'dashboard.js')
        elif path == '/api/status':
            self._send_json(_status_payload(self.dashboard_app))
        elif path == '/api/history':
            range_name = params.get('range', ['24h'])[0]
            self._send_json({'range': range_name, 'rows': self.dashboard_history.rows(range_name)})
        elif path == '/api/history.csv':
            range_name = params.get('range', ['24h'])[0]
            self._send_bytes(
                self.dashboard_history.to_csv(range_name).encode('utf-8'),
                'text/csv; charset=utf-8',
                extra_headers={'Content-Disposition': f'attachment; filename="agentpulse-history-{range_name}.csv"'},
            )
        elif path == '/api/settings':
            settings = dict(dashboard_settings())
            settings['autostart'] = _autostart_enabled()
            self._send_json({
                'settings': settings,
                'path': str(settings_write_path()),
                'restart_required': True,
            })
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if self._post_blocked():
            self.send_error(403)
            return

        try:
            length = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return

        if parsed.path == '/api/settings':
            payload = payload if isinstance(payload, dict) else {}
            autostart_errors = _apply_autostart(payload.pop('autostart', None))
            ok, errors, path = save_dashboard_settings(payload)
            errors = autostart_errors + errors
            ok = ok and not errors
            self._send_json({'ok': ok, 'errors': errors, 'path': str(path), 'restart_required': ok})
        elif parsed.path == '/api/test-event':
            event = payload.get('event') if isinstance(payload, dict) else None
            if event == 'reset':
                self.dashboard_app.on_test_reset_5h()
                self._send_json({'ok': True})
            elif event == 'threshold':
                self.dashboard_app.on_test_threshold_5h()
                self._send_json({'ok': True})
            else:
                self._send_json({'ok': False, 'errors': ['event must be reset or threshold']})
        else:
            self.send_error(404)

    def _send_json(self, payload: dict[str, Any]) -> None:
        self._send_bytes(json.dumps(payload, separators=(',', ':')).encode('utf-8'), 'application/json; charset=utf-8')

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        self._send_bytes(path.read_bytes(), mime)

    def _send_bytes(self, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def _autostart_enabled() -> bool:
    """Return the current Run-key autostart state, False when unreadable."""
    from .autostart import is_autostart_enabled

    try:
        return is_autostart_enabled()
    except OSError:
        return False


def _apply_autostart(value: object) -> list[str]:
    """Apply an autostart toggle from the dashboard, returning any errors.

    The Run-key change takes effect immediately and is intentionally kept
    out of the settings file - the Windows registry is the single source
    of truth, shared with the tray-menu toggle.
    """
    if value is None:
        return []
    if not isinstance(value, bool):
        return ['autostart: expected true or false']

    from .autostart import is_autostart_enabled, set_autostart

    try:
        if value != is_autostart_enabled():
            set_autostart(value)
    except OSError as exc:
        return [f'autostart: {exc}']
    return []


def _status_payload(app: AgentPulse) -> dict[str, Any]:
    """Build a token-free dashboard status payload."""
    claude_snap = app.cache.snapshot
    codex_snap = app.codex_cache.snapshot if app.codex_cache is not None else None
    settings = dashboard_settings()
    return {
        'app': {'name': 'Agents Pulse', 'version': __version__},
        'privacy': {
            'bind': DASHBOARD_HOST,
            'token_free': True,
            'analytics': False,
        },
        'next_poll_time': app._next_poll_time,
        'settings': {
            'prediction_enabled': settings.get('prediction_enabled', True),
            'prediction_day_end_time': settings.get('prediction_day_end_time', '18:00'),
            'heatmap_enabled': settings.get('heatmap_enabled', True),
            'quiet_hours_enabled': settings.get('quiet_hours_enabled', False),
            'quiet_hours_start': settings.get('quiet_hours_start', '22:00'),
            'quiet_hours_end': settings.get('quiet_hours_end', '08:00'),
        },
        'providers': [
            _provider_payload('claude', claude_snap, [{'name': i.name, 'version': i.version} for i in find_installations()]),
            *([_provider_payload('codex', codex_snap, [{'name': 'CLI', 'version': codex_version()}] if codex_version() else [])] if codex_snap is not None else []),
        ],
    }


def _provider_payload(provider: str, snap: Any, installations: list[dict[str, str]]) -> dict[str, Any]:
    usage = []
    for key, value in snap.usage.items():
        if key == 'extra_usage':
            continue
        if not isinstance(value, dict) or value.get('utilization') is None:
            continue
        resets_at = value.get('resets_at', '') or ''
        usage.append({
            'field': key,
            'label': popup_label(key),
            'utilization': float(value.get('utilization') or 0),
            'resets_at': resets_at,
            'reset_text': time_until(resets_at) if resets_at else '',
            'period_seconds': field_period(key),
            'burn': _burn_payload(float(value.get('utilization') or 0), resets_at, field_period(key)),
        })

    return {
        'id': provider,
        'label': 'Claude' if provider == 'claude' else 'Codex',
        'enabled': True,
        'usage': usage,
        'last_success_time': snap.last_success_time,
        'refreshing': snap.refreshing,
        'error': snap.last_error,
        'installations': installations,
    }


def _burn_payload(utilization: float, resets_at: str, period_seconds: int | None) -> dict[str, Any] | None:
    from .formatting import burn_rate_info

    info = burn_rate_info(utilization, resets_at, period_seconds)
    if info is None:
        return None
    return {
        'burn_per_hour': info['burn_per_hour'],
        'eta_seconds': info['eta_seconds'],
        'healthy': info['healthy'],
        'pace_delta': info['pace_delta'],
    }
