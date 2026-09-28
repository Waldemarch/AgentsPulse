"""Main tray application orchestration."""
from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Any

import pystray  # type: ignore[import-untyped]

from .api import api_headers
from .autostart import is_autostart_enabled, set_autostart, sync_autostart_path
from .cache import UsageCache
from .claude_cli import PROJECT_URL
from .codex_api import read_access_token as read_codex_access_token
from .codex_cache import CodexCache
from .command import run_event_command
from .dashboard import DashboardServer
from .forecast import Outlook, blocked_until, usage_outlooks
from .formatting import countdown_label, elapsed_pct, field_period, format_credits, format_tooltip, parse_field_name, popup_label
from .i18n import T
from .idle import get_idle_seconds, is_workstation_locked
from .kimi_api import read_access_token as read_kimi_access_token
from .kimi_cache import KimiCache
from .popup import UsagePopup
from . import settings as _settings
from .settings import (
    CODEX_ENABLED, DASHBOARD_PORT, IDLE_PAUSE,
    KIMI_ENABLED, POLL_ERROR, POLL_FAST, POLL_FAST_EXTRA, POLL_INTERVAL,
    get_alert_thresholds,
)
from .statusline import format_statusline
from .tray_icon import (
    create_countdown_image, create_icon_image, create_ready_image, create_status_image,
    taskbar_uses_light_theme, watch_theme_change,
)

__all__ = ['AgentPulse', 'UsageMonitorForClaude', 'crash_log']

# How long the tray shows its check mark after a countdown ends because a limit reset.
_READY_SECONDS = 10 * 60
# History the quota outlooks compare against: the dashboard keeps 30 days.
_OUTLOOK_HISTORY_SECONDS = 30 * 24 * 3600


def _future_iso(**delta: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _minutes_from_hhmm(value: str) -> int:
    hour, minute = value.split(':', 1)
    return int(hour) * 60 + int(minute)


def _dual_prefixed_env(values: dict[str, str]) -> dict[str, str]:
    """Build event-command env vars under both supported prefixes.

    ``AGENTPULSE_*`` is the current prefix; ``USAGE_MONITOR_*`` remains for
    backwards compatibility (see docs/event-commands.md).  Every value is
    set identically under both prefixes here so a call site can't let one
    drift out of sync with the other, as the hand-written dicts used to.
    """
    env: dict[str, str] = {}
    for key, value in values.items():
        env[f'AGENTPULSE_{key}'] = value
        env[f'USAGE_MONITOR_{key}'] = value
    return env


def _event_env(shared: dict[str, str], *, provider: str) -> dict[str, str]:
    """Build one event's full env: dual-prefixed `shared` vars plus the provider.

    The provider is AGENTPULSE_-only - ``USAGE_MONITOR_*`` predates
    multi-provider support and has no equivalent field.
    """
    env = _dual_prefixed_env(shared)
    env['AGENTPULSE_PROVIDER'] = provider
    return env


def _is_quiet_time(now: datetime | None = None) -> bool:
    if not _settings.QUIET_HOURS_ENABLED:
        return False
    current = now or datetime.now().astimezone()
    start = _minutes_from_hhmm(_settings.QUIET_HOURS_START)
    end = _minutes_from_hhmm(_settings.QUIET_HOURS_END)
    minute = current.hour * 60 + current.minute
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


class AgentPulse:
    """System tray controller for Claude, Codex, and Kimi usage data."""

    def __init__(self) -> None:
        self.running = True
        self.restart_requested = False

        self.cache = UsageCache()
        self.codex_cache = CodexCache() if CODEX_ENABLED and read_codex_access_token() else None
        self.kimi_cache = KimiCache() if KIMI_ENABLED and read_kimi_access_token() else None
        self.dashboard = DashboardServer(self)

        self._last_response: dict[str, Any] = {}
        self._secondary_responses: dict[str, dict[str, Any]] = {}
        self._prev_utilization: dict[str, float] = {}
        self._provider_prev_utilization: dict[str, dict[str, float]] = {'claude': self._prev_utilization}
        self._prev_account_uuid: str | None = None
        self._first_update_done = False
        self._notified_thresholds: dict[str, float] = {}
        self._deferred_notifications: dict[str, tuple[str, str]] = {}
        self._fast_polls_remaining = 0
        self._idle_reset_pending = False
        self._next_poll_time: float | None = None
        self._icon_key: tuple[Any, ...] | None = None
        self._countdown_shown = False
        self._ready_until = 0.0
        self._refresh_lock = threading.Lock()
        self._last_outlooks: dict[str, dict[str, Outlook]] = {}

        self._popup_lock = threading.Lock()
        self._popup_open = False
        self._popup_closed_at = 0.0
        self._light_taskbar = taskbar_uses_light_theme()

        self.icon = pystray.Icon(
            'usage_monitor',
            icon=create_icon_image([0], self._light_taskbar, _settings.ICON_STYLE),
            title=T['loading'],
            menu=self._menu(),
        )

    def _menu(self) -> Any:
        return pystray.Menu(
            pystray.MenuItem(T['menu_show'], self.on_show_popup, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                T['autostart'],
                self.on_toggle_autostart,
                checked=lambda _item: is_autostart_enabled(),
                visible=getattr(sys, 'frozen', False),
            ),
            pystray.MenuItem(
                T['test_commands'],
                pystray.Menu(
                    pystray.MenuItem(T['test_reset_5h'], self.on_test_reset_5h, enabled=bool(_settings.ON_RESET_COMMAND)),
                    pystray.MenuItem(T['test_reset_7d'], self.on_test_reset_7d, enabled=bool(_settings.ON_RESET_COMMAND)),
                    pystray.MenuItem(T['test_threshold_5h'], self.on_test_threshold_5h, enabled=bool(_settings.ON_THRESHOLD_COMMAND)),
                    pystray.MenuItem(T['test_threshold_7d'], self.on_test_threshold_7d, enabled=bool(_settings.ON_THRESHOLD_COMMAND)),
                ),
                enabled=bool(_settings.ON_RESET_COMMAND or _settings.ON_THRESHOLD_COMMAND),
            ),
            pystray.MenuItem(f"{T.get('open_dashboard', 'Open Dashboard')} (localhost:{DASHBOARD_PORT})", self.on_open_dashboard),
            pystray.MenuItem(T['restart'], self.on_restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(T['menu_project'], self.on_open_project),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(T['quit'], self.on_quit),
        )

    def on_show_popup(self, icon: Any = None, item: Any = None) -> None:
        with self._popup_lock:
            if self._popup_open or time.time() - self._popup_closed_at < 0.15:
                return
            self._popup_open = True
        threading.Thread(target=self._open_popup, daemon=True).start()

    def on_toggle_autostart(self, icon: Any = None, item: Any = None) -> None:
        set_autostart(not is_autostart_enabled())

    def on_restart(self, icon: Any = None, item: Any = None) -> None:
        self.restart_requested = True
        self.on_quit(icon, item)

    def on_open_project(self, icon: Any = None, item: Any = None) -> None:
        webbrowser.open(PROJECT_URL)

    def on_open_dashboard(self, icon: Any = None, item: Any = None) -> None:
        self.dashboard.open()

    def on_quit(self, icon: Any = None, item: Any = None) -> None:
        self.running = False
        self.dashboard.stop()
        self.icon.stop()

    def _test_env(self, event: str, variant: str, pct: str, threshold: str = '', prev: str = '', resets_at: str = '') -> dict[str, str]:
        values = {
            'EVENT': event,
            'VARIANT': variant,
            'UTILIZATION': pct,
            'RESETS_AT': resets_at,
        }
        if threshold:
            values['THRESHOLD'] = threshold
        if prev:
            values['PREV_UTILIZATION'] = prev
        values.setdefault('UTILIZATION_FIVE_HOUR', '0' if variant == 'five_hour' else '12')
        values.setdefault('UTILIZATION_SEVEN_DAY', '0' if variant == 'seven_day' else '45')
        return _event_env(values, provider='claude')

    def on_test_reset_5h(self, icon: Any = None, item: Any = None) -> None:
        env = self._test_env('reset', 'five_hour', '0', prev='95', resets_at=_future_iso(hours=5))
        env.update(_dual_prefixed_env({'TITLE': T['notify_reset_title'], 'MESSAGE': T['notify_reset']}))
        run_event_command(_settings.ON_RESET_COMMAND, env)

    def on_test_reset_7d(self, icon: Any = None, item: Any = None) -> None:
        env = self._test_env('reset', 'seven_day', '0', prev='99', resets_at=_future_iso(days=7))
        env.update(_dual_prefixed_env({'TITLE': T['notify_reset_title'], 'MESSAGE': T['notify_reset']}))
        run_event_command(_settings.ON_RESET_COMMAND, env)

    def on_test_threshold_5h(self, icon: Any = None, item: Any = None) -> None:
        message = T['notify_threshold_generic'].format(label=popup_label('five_hour'), pct='82')
        env = self._test_env('threshold', 'five_hour', '82', threshold='80', resets_at=_future_iso(hours=3))
        env.update(_dual_prefixed_env({'TITLE': T['notify_threshold_title'], 'MESSAGE': message}))
        run_event_command(_settings.ON_THRESHOLD_COMMAND, env)

    def on_test_threshold_7d(self, icon: Any = None, item: Any = None) -> None:
        message = T['notify_threshold_generic'].format(label=popup_label('seven_day'), pct='81')
        env = self._test_env('threshold', 'seven_day', '81', threshold='80', resets_at=_future_iso(days=4))
        env.update(_dual_prefixed_env({'TITLE': T['notify_threshold_title'], 'MESSAGE': message}))
        run_event_command(_settings.ON_THRESHOLD_COMMAND, env)

    def secondary_providers(self) -> list[tuple[str, Any]]:
        """Return the active non-Claude provider caches, in display order.

        Public: the dashboard and popup view layers use this (and
        :attr:`next_poll_time`) to read provider state instead of reaching
        into private attributes.
        """
        caches = [('codex', self.codex_cache), ('kimi', self.kimi_cache)]
        return [(name, cache) for name, cache in caches if cache is not None]

    @property
    def next_poll_time(self) -> float | None:
        """Unix timestamp of the next scheduled poll, or None before the first one."""
        return self._next_poll_time

    def _open_popup(self) -> None:
        try:
            refresh_claude = self.cache.last_success_time is None or time.time() - self.cache.last_success_time >= POLL_FAST
            needs_claude_profile = not self.cache.profile
            stale = {
                name for name, cache in self.secondary_providers()
                if cache.last_success_time is None or time.time() - cache.last_success_time >= POLL_FAST
            }
            missing_profiles = {name for name, cache in self.secondary_providers() if not cache.profile}
            if refresh_claude or needs_claude_profile or stale or missing_profiles:
                threading.Thread(
                    target=self._popup_refresh,
                    args=(refresh_claude, needs_claude_profile, stale, missing_profiles),
                    daemon=True,
                ).start()
            UsagePopup(self)
        finally:
            self._popup_closed_at = time.time()
            self._popup_open = False

    def _popup_refresh(self, refresh_claude: bool, claude_profile: bool, stale: set[str], missing_profiles: set[str]) -> None:
        if claude_profile:
            self.cache.ensure_profile()
        if refresh_claude:
            self.update()
        for name, cache in self.secondary_providers():
            if name in missing_profiles:
                cache.ensure_profile()
            if name in stale:
                self._update_secondary(name, cache)

    def _provider_entry(self, data: dict[str, Any], field: str) -> dict[str, Any]:
        entry = data.get(field)
        return entry if isinstance(entry, dict) else {}

    def _secondary_tooltip_sections(self) -> list[tuple[str, dict[str, Any]]]:
        """Return ``(provider_name, usage_data)`` pairs for the non-Claude providers."""
        sections = []
        for name, _cache in self.secondary_providers():
            data = self._secondary_responses.get(name)
            if data:
                sections.append((name, data))
        return sections

    def _render_tray(self) -> None:
        data = self._last_response
        sections = self._secondary_tooltip_sections()
        self._last_outlooks = self.quota_outlooks()
        self._refresh_icon(time.time())
        self.icon.title = format_tooltip(data, sections, self._last_outlooks)

    def _refresh_icon(self, now: float) -> None:
        """Redraw the tray icon when what it shows has changed."""
        key = self._icon_state(now)
        if key == self._icon_key:
            return
        self._icon_key = key
        kind = key[0]
        if kind == 'status':
            self.icon.icon = create_status_image(key[1], self._light_taskbar)
        elif kind == 'countdown':
            self.icon.icon = create_countdown_image(key[1])
        elif kind == 'ready':
            self.icon.icon = create_ready_image()
        else:
            self.icon.icon = create_icon_image(list(key[1]), light_taskbar=self._light_taskbar, style=key[2])

    def _icon_state(self, now: float) -> tuple[Any, ...]:
        """Decide what the tray icon shows now.

        An error mark when no provider has data; a countdown while every
        provider in the icon is at its limit; a check mark for a while after
        such a countdown ends; otherwise each provider's session usage in the
        configured icon style.
        """
        data = self._last_response
        available = [entry for _name, entry in self._secondary_tooltip_sections() if 'error' not in entry]
        if 'error' in data and not available:
            return ('status', 'C!' if data.get('auth_error') else '!', self._light_taskbar)

        usages = [data, *available]
        until = _usable_again(usages, now)
        if until is not None:
            self._countdown_shown = True
            return ('countdown', countdown_label(until - now))
        if self._countdown_shown:
            self._countdown_shown = False
            self._ready_until = now + _READY_SECONDS
        if now < self._ready_until:
            return ('ready',)
        return ('usage', tuple(_session_utilization(usage) for usage in usages), _settings.ICON_STYLE, self._light_taskbar)

    def quota_outlooks(self) -> dict[str, dict[str, Outlook]]:
        """Return the outlook of every quota of every active provider, keyed by provider and field.

        Public: the popup uses the same outlooks as the tooltip.
        """
        now = time.time()
        series = self.dashboard.history.series(since=now - _OUTLOOK_HISTORY_SECONDS)
        providers = [('claude', self.cache.snapshot), *((name, cache.snapshot) for name, cache in self.secondary_providers())]
        outlooks: dict[str, dict[str, Outlook]] = {}
        for name, snapshot in providers:
            outlooks[name] = usage_outlooks(snapshot.usage, series.get(name, {}), now=now, forecast=_settings.PREDICTION_ENABLED)
        return outlooks

    def statusline_text(self, *, provider: str | None = None, color: bool = True) -> str:
        """Return the one-line usage summary for the Claude Code status line.

        Public: the dashboard serves it at ``/api/statusline``.  It reuses the
        usage and outlooks of the last tray update, so a status line refresh
        costs neither an API request nor a forecast computation.

        Parameters
        ----------
        provider
            Show only this provider; every active provider by default.
        color
            Colour tight quotas and limits with ANSI escape sequences.
        """
        secondary = [(name, self._secondary_responses.get(name) or {}) for name, _cache in self.secondary_providers()]
        sections = [('claude', self._last_response), *secondary]
        if provider is not None:
            sections = [section for section in sections if section[0] == provider]
        return format_statusline(sections, self._last_outlooks, fields=_settings.TOOLTIP_FIELDS, now=time.time(), color=color)

    def refresh_now(self) -> None:
        """Fetch fresh usage of every provider in the background.

        Public: the popup's Refresh button calls this.  A provider updated less
        than ``poll_fast`` seconds ago keeps its data (the caches' cooldown), so
        repeated clicks cannot flood the APIs.
        """
        if not self._refresh_lock.acquire(blocking=False):
            return
        threading.Thread(target=self._refresh_in_background, daemon=True).start()

    def _refresh_in_background(self) -> None:
        try:
            self.update()
        finally:
            self._refresh_lock.release()

    def apply_settings(self) -> None:
        """Show settings saved from the dashboard right away, such as the icon style and tooltip fields.

        Public: the dashboard calls this after a successful save.
        """
        self._serve_statusline()
        if self._last_response:
            self._render_tray()

    def _serve_statusline(self) -> None:
        """Keep the dashboard server running for the Claude Code status line while that is turned on."""
        if not _settings.STATUSLINE_ENABLED:
            return
        try:
            self.dashboard.start()
        except OSError:
            # Every port the dashboard may use is taken; the status line stays empty.
            return

    def _on_theme_changed(self) -> None:
        light = taskbar_uses_light_theme()
        if light != self._light_taskbar:
            self._light_taskbar = light
            if self._last_response:
                self._render_tray()

    def update(self) -> None:
        result = self.cache.update()
        for name, cache in self.secondary_providers():
            self._update_secondary(name, cache)
        if result.data is None:
            return
        self._last_response = result.data
        self.dashboard.history.record('claude', result.data)
        self._render_tray()
        if result.token_refresh and result.token_refresh.updated:
            self.icon.notify(
                T['notify_update'].format(old=result.token_refresh.old_version, new=result.token_refresh.new_version),
                T['notify_update_title'],
            )
        if 'error' in result.data:
            return
        if self._account_changed():
            return
        fields = self._process_provider_alerts('claude', result.data)
        top_key = _settings.ICON_FIELDS[0].split(':', 1)[0]
        previous = self._prev_utilization.get(top_key)
        if previous is not None and fields.get(top_key, 0) > previous:
            self._fast_polls_remaining = POLL_FAST_EXTRA + 1
        elif self._fast_polls_remaining:
            self._fast_polls_remaining -= 1
        self._prev_utilization = fields
        self._provider_prev_utilization['claude'] = fields
        self._first_update_done = True

    def _account_changed(self) -> bool:
        self.cache.ensure_profile()
        profile = self.cache.profile if isinstance(self.cache.profile, dict) else {}
        account = profile.get('account', {}) if isinstance(profile, dict) else {}
        uuid = account.get('uuid') if isinstance(account, dict) else None
        if self._prev_account_uuid and uuid and uuid != self._prev_account_uuid:
            email = account.get('email', '') if isinstance(account, dict) else ''
            message = T['notify_account_switched'].format(email=email) if email else T['notify_account_switched_title']
            self._notify_or_defer('account_switched', message, T['notify_account_switched_title'])
            self._prev_utilization = {}
            self._provider_prev_utilization['claude'] = {}
            self._notified_thresholds.clear()
            self._prev_account_uuid = uuid
            return True
        self._prev_account_uuid = uuid
        return False

    def _update_secondary(self, provider: str, cache: Any) -> None:
        """Refresh one non-Claude provider and process its alerts."""
        result = cache.update()
        if result.data is None:
            return
        self._secondary_responses[provider] = result.data
        self.dashboard.history.record(provider, result.data)
        if 'error' not in result.data:
            self._process_provider_alerts(provider, result.data)

    def _quota_fields(self, data: dict[str, Any]) -> dict[str, float]:
        return {
            key: value.get('utilization', 0) or 0
            for key, value in data.items()
            if key != 'extra_usage' and isinstance(value, dict) and 'utilization' in value
        }

    def _process_provider_alerts(self, provider: str, data: dict[str, Any]) -> dict[str, float]:
        current = self._quota_fields(data)
        previous = self._prev_utilization if provider == 'claude' else self._provider_prev_utilization.get(provider, {})
        for key, pct in current.items():
            old = previous.get(key)
            parsed = parse_field_name(key)
            if old is None or parsed is None:
                continue
            reset_line = 95 if parsed[1] == 'hour' else 98
            blocked = any(other >= 99 for other_key, other in current.items() if other_key != key)
            if old > reset_line and pct < old and not blocked:
                self._notify_or_defer('reset' if provider == 'claude' else f'{provider}_reset', T['notify_reset'], T['notify_reset_title'])
            if pct < old:
                self._run_reset_command(key, pct, old, data=data, entry=data.get(key, {}), provider=provider)
                self._idle_reset_pending = False
        self._check_threshold_alerts(data, provider=provider)
        self._provider_prev_utilization[provider] = current
        return current

    def _notify_or_defer(self, category: str, message: str, title: str) -> None:
        if self._is_user_away() or _is_quiet_time():
            self._deferred_notifications[category] = (message, title)
        else:
            self.icon.notify(message, title)

    def _flush_deferred_notifications(self) -> None:
        if _is_quiet_time():
            return
        for message, title in self._deferred_notifications.values():
            self.icon.notify(message, title)
        self._deferred_notifications.clear()

    def _threshold_state_key(self, provider: str, variant_key: str) -> str:
        return variant_key if provider == 'claude' else f'{provider}:{variant_key}'

    def _check_threshold_alerts(self, data: dict[str, Any], provider: str = 'claude') -> None:
        for variant, entry in data.items():
            if variant == 'extra_usage' or not isinstance(entry, dict) or entry.get('utilization') is None:
                continue
            pct = entry['utilization']
            thresholds = get_alert_thresholds(variant, provider=provider)
            highest = max((threshold for threshold in thresholds if pct >= threshold), default=0)
            state_key = self._threshold_state_key(provider, variant)
            last = self._notified_thresholds.get(state_key, 0)
            if _settings.ALERT_TIME_AWARE and highest > last and highest < _settings.ALERT_TIME_AWARE_BELOW:
                period = field_period(variant)
                time_pct = elapsed_pct(entry.get('resets_at'), period) if period else None
                if time_pct is not None and pct <= time_pct:
                    self._notified_thresholds[state_key] = highest
                    continue
            if highest > last:
                title = T['notify_threshold_title']
                message = T['notify_threshold_generic'].format(label=popup_label(variant), pct=f'{pct:.0f}')
                key = f'threshold_{variant}' if provider == 'claude' else f'{provider}_threshold_{variant}'
                self._notify_or_defer(key, message, title)
                self._run_threshold_command(variant, pct, highest, entry, title, message, provider=provider)
                self._notified_thresholds[state_key] = highest
            elif highest < last:
                self._notified_thresholds[state_key] = highest
        if provider == 'claude':
            self._check_extra_usage_alerts(data)

    def _check_extra_usage_alerts(self, data: dict[str, Any]) -> None:
        extra = data.get('extra_usage')
        if not isinstance(extra, dict) or not extra.get('is_enabled'):
            return
        limit = extra.get('monthly_limit', 0) or 0
        if limit <= 0:
            return
        used = extra.get('used_credits', 0) or 0
        pct = used / limit * 100
        highest = max((threshold for threshold in get_alert_thresholds('extra_usage') if pct >= threshold), default=0)
        last = self._notified_thresholds.get('extra_usage', 0)
        if highest > last:
            title = T['notify_threshold_title']
            used_text = format_credits(used)
            limit_text = format_credits(limit)
            message = T['notify_threshold_extra_usage'].format(pct=f'{pct:.0f}', used=used_text, limit=limit_text)
            self._notify_or_defer('threshold_extra_usage', message, title)
            self._run_threshold_command('extra_usage', pct, highest, extra, title, message, extra_used=used_text, extra_limit=limit_text)
            self._notified_thresholds['extra_usage'] = highest
        elif highest < last:
            self._notified_thresholds['extra_usage'] = highest

    def _run_reset_command(
        self,
        variant: str,
        pct: float,
        prev_pct: float,
        *,
        data: dict[str, Any],
        entry: dict[str, Any],
        provider: str = 'claude',
    ) -> None:
        if not _settings.ON_RESET_COMMAND:
            return
        five = (data.get('five_hour') or {}).get('utilization', 0) or 0
        seven = (data.get('seven_day') or {}).get('utilization', 0) or 0
        env = _event_env({
            'EVENT': 'reset',
            'VARIANT': variant,
            'UTILIZATION': str(round(pct)),
            'PREV_UTILIZATION': str(round(prev_pct)),
            'UTILIZATION_FIVE_HOUR': str(round(five)),
            'UTILIZATION_SEVEN_DAY': str(round(seven)),
            'RESETS_AT': entry.get('resets_at', ''),
            'TITLE': T['notify_reset_title'],
            'MESSAGE': T['notify_reset'],
        }, provider=provider)
        run_event_command(_settings.ON_RESET_COMMAND, env)

    def _run_threshold_command(
        self,
        variant: str,
        pct: float,
        threshold: float,
        entry: dict[str, Any],
        title: str,
        message: str,
        *,
        extra_used: str = '',
        extra_limit: str = '',
        provider: str = 'claude',
    ) -> None:
        if not _settings.ON_THRESHOLD_COMMAND or not self._first_update_done:
            return
        shared = {
            'EVENT': 'threshold',
            'VARIANT': variant,
            'UTILIZATION': str(round(pct)),
            'THRESHOLD': str(round(threshold)),
            'RESETS_AT': entry.get('resets_at', ''),
            'TITLE': title,
            'MESSAGE': message,
        }
        if extra_used:
            shared['EXTRA_USED'] = extra_used
        if extra_limit:
            shared['EXTRA_LIMIT'] = extra_limit
        env = _event_env(shared, provider=provider)
        run_event_command(_settings.ON_THRESHOLD_COMMAND, env)

    def _seconds_until_next_reset(self) -> float | None:
        now = datetime.now(timezone.utc)
        upcoming: list[float] = []
        for entry in self._last_response.values():
            if not isinstance(entry, dict) or not entry.get('resets_at'):
                continue
            try:
                reset = datetime.fromisoformat(entry['resets_at'])
                seconds = (reset - now).total_seconds()
            except Exception:
                continue
            if seconds > 0:
                upcoming.append(seconds)
        return min(upcoming) if upcoming else None

    def _calculate_poll_interval(self) -> int:
        data = self._last_response
        if data.get('rate_limited'):
            remaining = self.cache.rate_limit_remaining
            interval = max(math.ceil(remaining), POLL_INTERVAL) if remaining > 0 else POLL_INTERVAL
        elif 'error' in data:
            interval = POLL_ERROR
        elif self._fast_polls_remaining > 0:
            interval = POLL_FAST
        else:
            interval = POLL_INTERVAL
        next_reset = self._seconds_until_next_reset()
        if next_reset is not None and next_reset + 5 <= interval * 1.5:
            interval = max(int(next_reset) + 5, POLL_FAST)
            self._fast_polls_remaining = max(self._fast_polls_remaining, 2)
        return interval

    def _is_user_away(self) -> bool:
        return is_workstation_locked() or (IDLE_PAUSE > 0 and get_idle_seconds() >= IDLE_PAUSE)

    def _wait_for_activity(self, until: float | None = None) -> None:
        while self.running and self._is_user_away():
            if until is not None and time.time() >= until:
                break
            time.sleep(2)

    def poll_loop(self) -> None:
        self.cache.ensure_profile()
        for _name, cache in self.secondary_providers():
            cache.ensure_profile()
        while self.running:
            self.update()
            if self._deferred_notifications and not self._is_user_away():
                self._flush_deferred_notifications()
            interval = self._calculate_poll_interval()
            target = time.time() + interval
            self._next_poll_time = target
            while self.running and time.time() < target:
                time.sleep(1)
                # Keeps a countdown icon ticking and ends the check mark on time.
                if self._icon_key is not None:
                    self._refresh_icon(time.time())
                last_success = self.cache.last_success_time
                if last_success is not None and last_success + interval > target:
                    target = last_success + interval
                    self._next_poll_time = target
                if self._is_user_away():
                    deadline = self._reset_deadline()
                    self._wait_for_activity(until=deadline)
                    if deadline is not None and self._is_user_away():
                        break
                    self._flush_deferred_notifications()
                    last_success = self.cache.last_success_time
                    if last_success is not None and time.time() - last_success >= interval:
                        break

    def _reset_deadline(self) -> float | None:
        if not _settings.ON_RESET_COMMAND:
            return None
        seconds = self._seconds_until_next_reset()
        if seconds is not None:
            self._idle_reset_pending = True
            return time.time() + seconds + 5
        if self._idle_reset_pending:
            return time.time() + POLL_INTERVAL
        return None

    def _on_icon_ready(self, icon: Any) -> None:
        try:
            icon.visible = True
            if getattr(sys, 'frozen', False):
                sync_autostart_path()
            if not api_headers():
                icon.notify(f"{T['warn_no_token']}\n{T['warn_login']}", T['popup_title'])
            threading.Thread(target=watch_theme_change, args=(self._on_theme_changed,), daemon=True).start()
            self._serve_statusline()
            self.poll_loop()
        except Exception:
            crash_log(traceback.format_exc())

    def run(self) -> None:
        self.icon.run(setup=self._on_icon_ready)


def _usable_again(usages: list[dict[str, Any]], now: float) -> float | None:
    """Unix time the first of these providers can be used again, or None unless all are at a limit."""
    moments = []
    for usage in usages:
        until = blocked_until(usage, now=now)
        if until is None:
            return None
        moments.append(until)
    return min(moments) if moments else None


def _session_utilization(usage: dict[str, Any]) -> float:
    """Utilization of the provider's shortest base quota window (its session), or 0 without a value.

    The shortest window is chosen among every field the response names, even
    a null one, so an idle session shows 0 % instead of the weekly usage.
    """
    shortest: tuple[int, Any] | None = None
    for key, value in usage.items():
        parsed = parse_field_name(key)
        period = field_period(key)
        if parsed is None or parsed[2] is not None or not period:
            continue
        if shortest is None or period < shortest[0]:
            shortest = (period, value)
    if shortest is None or not isinstance(shortest[1], dict) or shortest[1].get('utilization') is None:
        return 0.0
    return float(shortest[1]['utilization'])


UsageMonitorForClaude = AgentPulse


def crash_log(msg: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, msg[:2000], 'Agents Pulse - Error', 0x10)
