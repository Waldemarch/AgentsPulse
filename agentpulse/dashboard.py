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
from .formatting import field_period, popup_label, time_until
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

        if path == '/':
            self._send_file(_DASHBOARD_DIR / 'index.html')
        elif path == '/dashboard.css':
            self._send_file(_DASHBOARD_DIR / 'dashboard.css')
        elif path == '/dashboard.js':
            self._send_file(_DASHBOARD_DIR / 'dashboard.js')
        elif path == '/api/i18n':
            self._send_json(_dashboard_i18n())
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


def _status_payload(app: AgentPulse) -> dict[str, Any]:
    """Build a token-free dashboard status payload."""
    claude_snap = app.cache.snapshot
    settings = dashboard_settings()
    history = app.dashboard.history
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
                'claude', claude_snap, [{'name': i.name, 'version': i.version} for i in find_installations()], history,
            ),
            *_secondary_provider_payloads(app, history),
        ],
    }


def _secondary_provider_payloads(app: AgentPulse, history: DashboardHistory) -> list[dict[str, Any]]:
    """Build the dashboard payload of every active non-Claude provider."""
    payloads = []
    for provider, cache in app.secondary_providers():
        version = SECONDARY_PROVIDERS_BY_NAME[provider].cli_version()
        installations = [{'name': 'CLI', 'version': version}] if version else []
        payloads.append(_provider_payload(provider, cache.snapshot, installations, history))
    return payloads


def _provider_payload(provider: str, snap: Any, installations: list[dict[str, str]], history: DashboardHistory) -> dict[str, Any]:
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
            'trend': _cycle_trend(history, provider, key),
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


# A drop this large between consecutive samples of the same field is treated
# as a quota reset (a new cycle) rather than ordinary fluctuation - usage
# only ever increases within a cycle.
_CYCLE_RESET_DROP = 5.0


def _cycle_trend(history: DashboardHistory, provider: str, field: str, *, now: float | None = None) -> dict[str, Any] | None:
    """Compare the current quota cycle's pace against past cycles at the same age.

    Splits up to 30 days of persisted history into cycles at each detected
    reset (a utilization drop), then compares the current cycle's
    utilization to what past cycles had reached by the same elapsed time.
    This is the insight a single cycle's burn rate can't give: whether
    *this* cycle is running ahead of or behind the account's usual pace,
    as opposed to whether it is on pace to exhaust the current window.

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
        None when there is no complete previous cycle to compare against.
        Otherwise a dict with ``current_pct``, ``historical_avg_pct``,
        ``delta_pct`` (positive means running ahead of the historical pace),
        and ``cycles_compared``.
    """
    now = time.time() if now is None else now
    samples = sorted(
        (row for row in history.rows('30d', now=now) if row['provider'] == provider and row['field'] == field and row['utilization'] is not None),
        key=lambda row: row['ts'],
    )
    if len(samples) < 2:
        return None

    cycles: list[list[dict[str, Any]]] = [[]]
    for index, row in enumerate(samples):
        if index > 0 and row['utilization'] < samples[index - 1]['utilization'] - _CYCLE_RESET_DROP:
            cycles.append([])
        cycles[-1].append(row)

    previous_cycles, current_cycle = cycles[:-1], cycles[-1]
    if not previous_cycles or not current_cycle:
        return None

    current_start = current_cycle[0]['ts']
    elapsed = now - current_start
    if elapsed <= 0:
        return None

    comparable: list[float] = []
    for cycle in previous_cycles:
        cycle_start = cycle[0]['ts']
        reached = [row for row in cycle if row['ts'] - cycle_start <= elapsed]
        if reached:
            comparable.append(reached[-1]['utilization'])

    if not comparable:
        return None

    current_pct = current_cycle[-1]['utilization']
    historical_avg_pct = sum(comparable) / len(comparable)
    return {
        'current_pct': current_pct,
        'historical_avg_pct': historical_avg_pct,
        'delta_pct': current_pct - historical_avg_pct,
        'cycles_compared': len(comparable),
    }
