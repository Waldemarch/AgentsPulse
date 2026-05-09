"""
Local Dashboard
===============

Private localhost dashboard with SQLite-backed usage history.
"""
from __future__ import annotations

import csv
import json
import mimetypes
import sqlite3
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .claude_cli import find_installations
from .codex_cli import codex_version
from .formatting import field_period, popup_label, time_until
from .settings import DASHBOARD_HOST, DASHBOARD_PORT, dashboard_settings, save_dashboard_settings, settings_write_path

if TYPE_CHECKING:
    from .app import AgentPulse

__all__ = ['DashboardHistory', 'DashboardServer']

_DASHBOARD_DIR = Path(__file__).parent / 'dashboard'
_MAX_AGE_SECONDS = 30 * 24 * 3600
_MAX_SAMPLES = 12000
_RANGES = {
    '24h': 24 * 3600,
    '7d': 7 * 24 * 3600,
    '30d': 30 * 24 * 3600,
}


class DashboardHistory:
    """SQLite-backed storage for provider usage snapshots."""

    def __init__(
        self,
        max_age_seconds: int = _MAX_AGE_SECONDS,
        max_samples: int = _MAX_SAMPLES,
        db_path: Path | None = None,
    ) -> None:
        self.max_age_seconds = max_age_seconds
        self.max_samples = max_samples
        self._db_path = db_path if db_path is not None else settings_write_path().parent / 'agentpulse-history.db'
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self._db_path), check_same_thread=False)
        try:
            con.execute(
                'CREATE TABLE IF NOT EXISTS snapshots ('
                '    id INTEGER PRIMARY KEY AUTOINCREMENT,'
                '    ts REAL NOT NULL,'
                '    provider TEXT NOT NULL,'
                '    field TEXT NOT NULL DEFAULT \'\','
                '    utilization REAL,'
                '    resets_at TEXT NOT NULL DEFAULT \'\','
                '    error TEXT'
                ')'
            )
            con.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts)')
            con.commit()
        finally:
            con.close()

    def record(self, provider: str, data: dict[str, Any], *, ts: float | None = None) -> None:
        """Record one sanitized provider snapshot.

        Tokens, account identifiers, and raw profile data are intentionally
        not stored.  Only quota percentages and reset timestamps are kept.
        """
        now = time.time() if ts is None else ts
        error = data.get('error') if isinstance(data.get('error'), str) else None

        rows_to_insert: list[tuple[float, str, str, float | None, str, str | None]] = []
        if 'error' in data:
            rows_to_insert.append((now, provider, '', None, '', error))
        else:
            for key, value in data.items():
                if key == 'extra_usage':
                    continue
                if not isinstance(value, dict) or value.get('utilization') is None:
                    continue
                rows_to_insert.append((
                    now,
                    provider,
                    key,
                    float(value.get('utilization') or 0),
                    value.get('resets_at', '') or '',
                    None,
                ))

        cutoff = now - self.max_age_seconds
        with self._lock:
            con = sqlite3.connect(str(self._db_path), check_same_thread=False)
            try:
                con.executemany(
                    'INSERT INTO snapshots (ts, provider, field, utilization, resets_at, error) VALUES (?,?,?,?,?,?)',
                    rows_to_insert,
                )
                con.execute('DELETE FROM snapshots WHERE ts < ?', (cutoff,))
                con.commit()
            finally:
                con.close()

    def rows(self, range_name: str = '24h', *, now: float | None = None) -> list[dict[str, Any]]:
        """Return flattened rows for the requested time range."""
        cutoff = (time.time() if now is None else now) - _RANGES.get(range_name, _RANGES['24h'])
        con = sqlite3.connect(str(self._db_path), check_same_thread=False)
        try:
            con.row_factory = sqlite3.Row
            cursor = con.execute(
                'SELECT ts, provider, field, utilization, resets_at, error FROM snapshots WHERE ts >= ? ORDER BY ts',
                (cutoff,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            con.close()

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


class DashboardServer:
    """Local HTTP dashboard bound to localhost only."""

    def __init__(self, app: AgentPulse, host: str = DASHBOARD_HOST, port: int = DASHBOARD_PORT) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.history = DashboardHistory()
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

        class Handler(_DashboardHandler):
            dashboard_app = app
            dashboard_history = history

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
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def open(self) -> None:
        """Start the dashboard and open it in the default browser."""
        webbrowser.open(self.start())

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

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if self.client_address[0] not in {'127.0.0.1', '::1'}:
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
            self._send_json({
                'settings': dashboard_settings(),
                'path': str(settings_write_path()),
                'restart_required': True,
            })
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if self.client_address[0] not in {'127.0.0.1', '::1'}:
            self.send_error(403)
            return

        try:
            length = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return

        if parsed.path == '/api/settings':
            ok, errors, path = save_dashboard_settings(payload if isinstance(payload, dict) else {})
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
