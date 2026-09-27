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
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .claude_cli import find_installations
from .formatting import field_period, parse_field_name, popup_label, time_until
from .i18n import T
from .providers import SECONDARY_PROVIDERS_BY_NAME
from .settings import (
    DASHBOARD_HOST, DASHBOARD_PORT, HISTORY_PERSIST, PROVIDER_LABELS,
    dashboard_settings, history_write_path, save_dashboard_settings,
)

if TYPE_CHECKING:
    from .app import AgentPulse

__all__ = ['DashboardHistory', 'DashboardServer']

log = logging.getLogger(__name__)

_DASHBOARD_DIR = Path(__file__).parent / 'dashboard'
_MAX_AGE_SECONDS = 30 * 24 * 3600
# 30 days of three providers polling every 180s is ~43k snapshots; the buffer keeps the most recent ones.
_MAX_SAMPLES = 60000
# Stale history-file lines tolerated before the file is compacted (rewritten from memory).
_HISTORY_COMPACT_SLACK = 4000
_RANGES = {
    '24h': 24 * 3600,
    '7d': 7 * 24 * 3600,
    '30d': 30 * 24 * 3600,
}
# Chart resolution per range: the highest reading per bucket is kept, so limit
# hits survive while the 7- and 30-day payloads stay small (roughly one bucket
# per pixel of a full-width chart).  The CSV export always has every sample.
_CHART_BUCKETS = {
    '24h': 0,
    '7d': 10 * 60,
    '30d': 30 * 60,
}
# The dashboard's own assets with fixed content types.  ``mimetypes`` reads the
# Windows registry, where other software can register ``.js`` as ``text/plain``,
# which browsers then refuse to execute because of ``nosniff``.
_STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/dashboard.css': ('dashboard.css', 'text/css; charset=utf-8'),
    '/dashboard.js': ('dashboard.js', 'text/javascript; charset=utf-8'),
}
# Dashboard settings that only take effect after a restart, because provider
# caches are created once at startup.  Every other setting applies immediately.
_RESTART_KEYS = ('codex_enabled', 'kimi_enabled')
# Samples whose reset times differ by less than this belong to one quota cycle:
# the APIs repeat the same reset moment with jitter of up to a few seconds on
# every poll, while a real reset moves it by hours or days.
_CYCLE_RESET_TOLERANCE = 10 * 60
# Sent on every response. The dashboard loads only its own same-origin assets,
# so a strict policy needs no exceptions.  ``no-referrer`` keeps the per-run
# session token (passed in the open URL before the page strips it) out of any
# Referer header; ``frame-ancestors``/``X-Frame-Options`` block clickjacking.
_SECURITY_HEADERS = {
    'Content-Security-Policy': "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
}


@dataclass(frozen=True)
class _Snapshot:
    ts: float
    provider: str
    usage: dict[str, dict[str, Any]]
    error: str | None


@dataclass
class _Cycle:
    """One quota window of a series: its reset time and ``(ts, utilization)`` samples."""

    reset: float
    samples: list[tuple[float, float]]


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
        # Created together with the app, so these are the values it runs with.
        current = dashboard_settings()
        self.startup_settings = {key: current[key] for key in _RESTART_KEYS}
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
        startup = self.startup_settings

        class Handler(_DashboardHandler):
            dashboard_app = app
            dashboard_history = history
            dashboard_token = token
            startup_settings = startup

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
    startup_settings: dict[str, object] = {}

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _request_blocked(self) -> bool:
        """Reject non-loopback clients and forged Host headers (DNS rebinding)."""
        if self.client_address[0] not in {'127.0.0.1', '::1'}:
            return True
        host = (self.headers.get('Host') or '').strip().lower()
        return host not in self.allowed_hosts

    def _token_ok(self) -> bool:
        """Return True when the request carries the valid per-run session token."""
        if not self.dashboard_token:
            return False
        provided = self.headers.get('X-AgentsPulse-Token') or ''
        return hmac.compare_digest(provided.encode('utf-8'), self.dashboard_token.encode('utf-8'))

    def _post_blocked(self) -> bool:
        """Reject cross-origin POSTs and requests without the session token (CSRF)."""
        if self._request_blocked():
            return True
        origin = (self.headers.get('Origin') or '').strip().lower()
        if origin and origin not in self.allowed_origins:
            return True
        return not self._token_ok()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if self._request_blocked():
            self.send_error(403)
            return

        if path in _STATIC_FILES:
            name, content_type = _STATIC_FILES[path]
            self._send_file(_DASHBOARD_DIR / name, content_type)
        elif path == '/api/i18n':
            self._send_json(_dashboard_i18n())
        elif path == '/api/status':
            self._send_json(_status_payload(self.dashboard_app))
        elif path == '/api/history':
            range_name = params.get('range', ['24h'])[0]
            self._send_json(_history_payload(self.dashboard_history, range_name))
        elif path == '/api/history.csv':
            range_name = params.get('range', ['24h'])[0]
            self._send_bytes(
                self.dashboard_history.to_csv(range_name).encode('utf-8'),
                'text/csv; charset=utf-8',
                extra_headers={'Content-Disposition': f'attachment; filename="agentpulse-history-{range_name}.csv"'},
            )
        elif path == '/api/settings':
            # Settings carry the user's configured event commands, so this read
            # requires the session token (the filesystem path with the account
            # username is intentionally never exposed here).
            if not self._token_ok():
                self.send_error(403)
                return
            settings = dict(dashboard_settings())
            settings['autostart'] = _autostart_enabled()
            self._send_json({'settings': settings})
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
            # The settings path contains the Windows user name, so it stays server-side.
            ok, errors, _path = save_dashboard_settings(payload)
            errors = autostart_errors + errors
            ok = ok and not errors
            restart_required = ok and _needs_restart(payload, self.startup_settings)
            self._send_json({'ok': ok, 'errors': errors, 'restart_required': restart_required})
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

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        self._send_bytes(path.read_bytes(), content_type)

    def _send_bytes(self, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in _SECURITY_HEADERS.items():
            self.send_header(key, value)
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


def _needs_restart(saved: dict[str, Any], startup: dict[str, object]) -> bool:
    """Return True when a saved restart-only setting differs from what the app started with."""
    return any(key in saved and saved[key] != startup.get(key) for key in _RESTART_KEYS)


def _dashboard_i18n() -> dict[str, str]:
    """Return the translated strings the dashboard renders in the browser.

    The dashboard is a static page served locally, so its text is localized
    on the client: it fetches this map once and applies it to both the static
    labels and the dynamically rendered widgets.  Reused keys (``usage``,
    ``pace_healthy`` ...) share the same translations as the tray popup.
    """
    keys = [
        'subtitle', 'range_24h', 'range_7d', 'range_30d', 'export_csv',
        'usage_history', 'burn_rate', 'predictions', 'heatmap', 'diagnostics', 'settings',
        'diag_sub', 'restart_required', 'pp_per_hour', 'heatmap_meta', 'day_target', 'rows',
        'waiting', 'waiting_usage', 'waiting_enough', 'waiting_history', 'no_reset', 'not_detected',
        'by_time', 'by_reset', 'vs_usual_pace', 'ago',
        'diag_app', 'diag_bind', 'diag_analytics', 'diag_tokens', 'diag_next_update',
        'enabled', 'disabled', 'not_exposed', 'check_config', 'unknown', 'cli',
        'codex_monitoring', 'kimi_monitoring', 'quiet_hours', 'tooltip_fields',
        'thr_claude_5h', 'thr_claude_7d', 'thr_codex_5h', 'thr_codex_7d',
        'thr_kimi_5h', 'thr_kimi_7d',
        'predict_until', 'quiet_starts', 'quiet_ends', 'reset_command', 'threshold_command',
        'save_settings', 'test_reset', 'test_threshold',
        'saved', 'error', 'session_expired', 'test_fired', 'test_failed', 'unknown_error',
        'connection_lost',
    ]
    strings = {key: T[f'dash_{key}'] for key in keys}
    strings['autostart'] = T['autostart']
    strings['pace_healthy'] = T['pace_healthy']
    strings['pace_ahead'] = T['pace_ahead']
    return strings


def _history_payload(history: DashboardHistory, range_name: str, *, now: float | None = None) -> dict[str, Any]:
    """Build the chart payload for one history range.

    Rows are aggregated to the range's ``_CHART_BUCKETS`` size, and every quota
    field in the range is described once in ``fields`` (display label, window
    length, model variant), so the dashboard can group and label its charts
    without knowing any field name.

    Parameters
    ----------
    history
        The dashboard's usage history.
    range_name
        ``'24h'``, ``'7d'`` or ``'30d'``; anything else falls back to ``'24h'``.
    now
        Current time as a Unix timestamp; defaults to :func:`time.time`.

    Returns
    -------
    dict
        ``range``, ``bucket_seconds``, ``rows`` and ``fields``.
    """
    if range_name not in _RANGES:
        range_name = '24h'
    rows = history.rows(range_name, now=now)
    bucket_seconds = _CHART_BUCKETS[range_name]
    return {
        'range': range_name,
        'bucket_seconds': bucket_seconds,
        'rows': _aggregate_rows(rows, bucket_seconds),
        'fields': _field_metadata(row['field'] for row in rows if row['field']),
    }


def _aggregate_rows(rows: list[dict[str, Any]], bucket_seconds: int) -> list[dict[str, Any]]:
    """Keep the highest reading per provider, field, quota cycle and time bucket.

    Readings of different quota cycles never share a bucket, so a reset inside
    a bucket still shows up as a drop.  Error rows keep one row per provider
    and bucket.  With ``bucket_seconds <= 0`` the rows are returned unchanged.
    """
    if bucket_seconds <= 0:
        return rows
    kept: dict[tuple[str, str, int, int | None], dict[str, Any]] = {}
    for row in rows:
        reset = _reset_timestamp(row['resets_at'])
        cycle = None if reset is None else int(reset // _CYCLE_RESET_TOLERANCE)
        key = (row['provider'], row['field'], int(row['ts'] // bucket_seconds), cycle)
        best = kept.get(key)
        higher = row['utilization'] is not None and (best is None or best['utilization'] is None or row['utilization'] >= best['utilization'])
        if best is None or higher:
            kept[key] = row
    return sorted(kept.values(), key=lambda row: row['ts'])


def _field_metadata(fields: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Describe quota fields for the charts: display label, window length and model variant."""
    metadata: dict[str, dict[str, Any]] = {}
    for name in fields:
        if name in metadata:
            continue
        parsed = parse_field_name(name)
        metadata[name] = {
            'label': popup_label(name),
            'period_seconds': field_period(name),
            'variant': parsed[2] if parsed else None,
        }
    return metadata


def _status_payload(app: AgentPulse) -> dict[str, Any]:
    """Build a token-free dashboard status payload."""
    claude_snap = app.cache.snapshot
    settings = dashboard_settings()
    series = _rows_by_series(app.dashboard.history.rows('30d'))
    return {
        'app': {'name': 'Agents Pulse', 'version': __version__},
        'privacy': {
            'bind': DASHBOARD_HOST,
            'token_free': True,
            'analytics': False,
        },
        'next_poll_time': app.next_poll_time,
        'settings': {
            'prediction_enabled': settings.get('prediction_enabled', True),
            'prediction_day_end_time': settings.get('prediction_day_end_time', '18:00'),
            'heatmap_enabled': settings.get('heatmap_enabled', True),
            'quiet_hours_enabled': settings.get('quiet_hours_enabled', False),
            'quiet_hours_start': settings.get('quiet_hours_start', '22:00'),
            'quiet_hours_end': settings.get('quiet_hours_end', '08:00'),
        },
        'providers': [
            _provider_payload(
                'claude', claude_snap, [{'name': i.name, 'version': i.version} for i in find_installations()], series,
            ),
            *_secondary_provider_payloads(app, series),
        ],
    }


def _rows_by_series(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group history rows by ``(provider, field)`` so each series is scanned once."""
    series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        series.setdefault((row['provider'], row['field']), []).append(row)
    return series


def _secondary_provider_payloads(app: AgentPulse, series: dict[tuple[str, str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Build the dashboard payload of every active non-Claude provider."""
    payloads = []
    for provider, cache in app.secondary_providers():
        version = SECONDARY_PROVIDERS_BY_NAME[provider].cli_version()
        installations = [{'name': 'CLI', 'version': version}] if version else []
        payloads.append(_provider_payload(provider, cache.snapshot, installations, series))
    return payloads


def _provider_payload(
    provider: str,
    snap: Any,
    installations: list[dict[str, str]],
    series: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    now = time.time()
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
            'trend': _series_trend(series.get((provider, key), []), key, now),
        })

    return {
        'id': provider,
        'label': PROVIDER_LABELS.get(provider, provider.title()),
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


def _cycle_trend(history: DashboardHistory, provider: str, field: str, *, now: float | None = None) -> dict[str, Any] | None:
    """Compare the current quota cycle's pace against past cycles at the same age.

    A cycle is the run of samples that report the same ``resets_at`` moment;
    its nominal start is that reset minus the field's window length (five
    hours for ``five_hour``, seven days for ``seven_day``).  Samples without
    a reset time - an idle session with no active window - belong to no
    cycle.  The current cycle's utilization is compared to what each previous
    cycle had reached at the same age.  This is the insight a single cycle's
    burn rate can't give: whether *this* cycle is running ahead of or behind
    the account's usual pace, as opposed to whether it is on pace to exhaust
    the current window.

    Parameters
    ----------
    history
        The dashboard's persisted usage history.
    provider, field
        Identify which quota series to analyze (e.g. ``'claude'``, ``'seven_day'``).
    now
        Current time as a Unix timestamp; defaults to :func:`time.time`. Tests
        pass this explicitly for determinism.

    Returns
    -------
    dict or None
        None when the field has no known window length, no quota cycle is
        active now, or no previous cycle was observed at the current cycle's
        age.  Otherwise a dict with ``current_pct``, ``historical_avg_pct``,
        ``delta_pct`` (positive means running ahead of the historical pace),
        and ``cycles_compared``.
    """
    now = time.time() if now is None else now
    rows = [row for row in history.rows('30d', now=now) if row['provider'] == provider and row['field'] == field]
    return _series_trend(rows, field, now)


def _series_trend(rows: list[dict[str, Any]], field: str, now: float) -> dict[str, Any] | None:
    """Pace comparison for the history rows of one series; see :func:`_cycle_trend`."""
    period = field_period(field)
    if not period:
        return None
    cycles = _quota_cycles(rows)
    if len(cycles) < 2:
        return None

    current = cycles[-1]
    age = now - (current.reset - period)
    if current.reset <= now or age <= 0:
        return None

    comparable: list[float] = []
    for cycle in cycles[:-1]:
        start = cycle.reset - period
        reached = [utilization for ts, utilization in cycle.samples if 0 <= ts - start <= age]
        if reached:
            comparable.append(reached[-1])

    if not comparable:
        return None

    current_pct = current.samples[-1][1]
    historical_avg_pct = sum(comparable) / len(comparable)
    return {
        'current_pct': current_pct,
        'historical_avg_pct': historical_avg_pct,
        'delta_pct': current_pct - historical_avg_pct,
        'cycles_compared': len(comparable),
    }


def _quota_cycles(rows: Iterable[dict[str, Any]]) -> list[_Cycle]:
    """Split the history rows of one series into quota cycles, in time order.

    A new cycle starts when the reported reset time moves by more than
    ``_CYCLE_RESET_TOLERANCE`` from the previous sample's.  Samples without a
    reset time belong to no cycle.  Each cycle keeps the latest reset time
    it reported.
    """
    cycles: list[_Cycle] = []
    previous: float | None = None
    for row in sorted(rows, key=lambda item: item['ts']):
        reset = _reset_timestamp(row['resets_at'])
        if reset is None or row['utilization'] is None:
            continue
        if previous is None or abs(reset - previous) > _CYCLE_RESET_TOLERANCE:
            cycles.append(_Cycle(reset=reset, samples=[]))
        cycles[-1].reset = reset
        cycles[-1].samples.append((row['ts'], row['utilization']))
        previous = reset
    return cycles


def _reset_timestamp(value: object) -> float | None:
    """Parse an ISO ``resets_at`` value to a Unix timestamp; None when absent, invalid or naive."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()
