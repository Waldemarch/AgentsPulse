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
from .formatting import elapsed_pct, field_period, format_credits, format_tooltip, parse_field_name, popup_label
from .i18n import T
from .idle import get_idle_seconds, is_workstation_locked
from .popup import UsagePopup
from .settings import (
    ALERT_TIME_AWARE, ALERT_TIME_AWARE_BELOW, CODEX_ENABLED, DASHBOARD_PORT, ICON_FIELDS, IDLE_PAUSE,
    ON_RESET_COMMAND, ON_THRESHOLD_COMMAND, POLL_ERROR, POLL_FAST, POLL_FAST_EXTRA,
    POLL_INTERVAL, QUIET_HOURS_ENABLED, QUIET_HOURS_END, QUIET_HOURS_START,
    get_alert_thresholds,
)
from .tray_icon import create_icon_image, create_status_image, taskbar_uses_light_theme, watch_theme_change

__all__ = ['AgentPulse', 'UsageMonitorForClaude', 'crash_log']


def _future_iso(**delta: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _minutes_from_hhmm(value: str) -> int:
    hour, minute = value.split(':', 1)
    return int(hour) * 60 + int(minute)


def _is_quiet_time(now: datetime | None = None) -> bool:
    if not QUIET_HOURS_ENABLED:
        return False
    current = now or datetime.now().astimezone()
    start = _minutes_from_hhmm(QUIET_HOURS_START)
    end = _minutes_from_hhmm(QUIET_HOURS_END)
    minute = current.hour * 60 + current.minute
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


class AgentPulse:
    """System tray controller for Claude and Codex usage data."""

    def __init__(self) -> None:
        self.running = True
        self.restart_requested = False

        self.cache = UsageCache()
        self.codex_cache = CodexCache() if CODEX_ENABLED and read_codex_access_token() else None
        self.dashboard = DashboardServer(self)

        self._last_response: dict[str, Any] = {}
        self._last_codex_response: dict[str, Any] = {}
        self._prev_utilization: dict[str, float] = {}
        self._provider_prev_utilization: dict[str, dict[str, float]] = {'claude': self._prev_utilization}
        self._prev_account_uuid: str | None = None
        self._first_update_done = False
        self._notified_thresholds: dict[str, float] = {}
        self._deferred_notifications: dict[str, tuple[str, str]] = {}
        self._fast_polls_remaining = 0
        self._idle_reset_pending = False
        self._next_poll_time: float | None = None

        self._popup_lock = threading.Lock()
        self._popup_open = False
        self._popup_closed_at = 0.0
        self._light_taskbar = taskbar_uses_light_theme()

        self.icon = pystray.Icon(
            'usage_monitor',
            icon=create_icon_image(0, 0, self._light_taskbar),
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
                    pystray.MenuItem(T['test_reset_5h'], self.on_test_reset_5h, enabled=bool(ON_RESET_COMMAND)),
                    pystray.MenuItem(T['test_reset_7d'], self.on_test_reset_7d, enabled=bool(ON_RESET_COMMAND)),
                    pystray.MenuItem(T['test_threshold_5h'], self.on_test_threshold_5h, enabled=bool(ON_THRESHOLD_COMMAND)),
                    pystray.MenuItem(T['test_threshold_7d'], self.on_test_threshold_7d, enabled=bool(ON_THRESHOLD_COMMAND)),
                ),
                enabled=bool(ON_RESET_COMMAND or ON_THRESHOLD_COMMAND),
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
        env = {
            'USAGE_MONITOR_EVENT': event,
            'USAGE_MONITOR_VARIANT': variant,
            'USAGE_MONITOR_UTILIZATION': pct,
            'USAGE_MONITOR_RESETS_AT': resets_at,
        }
        if threshold:
            env['USAGE_MONITOR_THRESHOLD'] = threshold
        if prev:
            env['USAGE_MONITOR_PREV_UTILIZATION'] = prev
        env.setdefault('USAGE_MONITOR_UTILIZATION_FIVE_HOUR', '0' if variant == 'five_hour' else '12')
        env.setdefault('USAGE_MONITOR_UTILIZATION_SEVEN_DAY', '0' if variant == 'seven_day' else '45')
        return env

    def on_test_reset_5h(self, icon: Any = None, item: Any = None) -> None:
        env = self._test_env('reset', 'five_hour', '0', prev='95', resets_at=_future_iso(hours=5))
        env.update({'USAGE_MONITOR_TITLE': T['notify_reset_title'], 'USAGE_MONITOR_MESSAGE': T['notify_reset']})
        run_event_command(ON_RESET_COMMAND, env)

    def on_test_reset_7d(self, icon: Any = None, item: Any = None) -> None:
        env = self._test_env('reset', 'seven_day', '0', prev='99', resets_at=_future_iso(days=7))
        env.update({'USAGE_MONITOR_TITLE': T['notify_reset_title'], 'USAGE_MONITOR_MESSAGE': T['notify_reset']})
        run_event_command(ON_RESET_COMMAND, env)

    def on_test_threshold_5h(self, icon: Any = None, item: Any = None) -> None:
        message = T['notify_threshold_generic'].format(label=popup_label('five_hour'), pct='82')
        env = self._test_env('threshold', 'five_hour', '82', threshold='80', resets_at=_future_iso(hours=3))
        env.update({'USAGE_MONITOR_TITLE': T['notify_threshold_title'], 'USAGE_MONITOR_MESSAGE': message})
        run_event_command(ON_THRESHOLD_COMMAND, env)

    def on_test_threshold_7d(self, icon: Any = None, item: Any = None) -> None:
        message = T['notify_threshold_generic'].format(label=popup_label('seven_day'), pct='81')
        env = self._test_env('threshold', 'seven_day', '81', threshold='80', resets_at=_future_iso(days=4))
        env.update({'USAGE_MONITOR_TITLE': T['notify_threshold_title'], 'USAGE_MONITOR_MESSAGE': message})
        run_event_command(ON_THRESHOLD_COMMAND, env)

    def _open_popup(self) -> None:
        try:
            refresh_claude = self.cache.last_success_time is None or time.time() - self.cache.last_success_time >= POLL_FAST
            refresh_codex = self.codex_cache is not None and (
                self.codex_cache.last_success_time is None or time.time() - self.codex_cache.last_success_time >= POLL_FAST
            )
            needs_claude_profile = not self.cache.profile
            needs_codex_profile = self.codex_cache is not None and not self.codex_cache.profile
            if refresh_claude or refresh_codex or needs_claude_profile or needs_codex_profile:
                threading.Thread(
                    target=self._popup_refresh,
                    args=(refresh_claude, refresh_codex, needs_claude_profile, needs_codex_profile),
                    daemon=True,
                ).start()
            UsagePopup(self)
        finally:
            self._popup_closed_at = time.time()
            self._popup_open = False

    def _popup_refresh(self, refresh_claude: bool, refresh_codex: bool, claude_profile: bool, codex_profile: bool) -> None:
        if claude_profile:
            self.cache.ensure_profile()
        if refresh_claude:
            self.update()
        if self.codex_cache is not None:
            if codex_profile:
                self.codex_cache.ensure_profile()
            if refresh_codex:
                self._update_codex()

    def _provider_entry(self, data: dict[str, Any], field: str) -> dict[str, Any]:
        entry = data.get(field)
        return entry if isinstance(entry, dict) else {}

    def _render_tray(self) -> None:
        data = self._last_response
        codex = self._last_codex_response
        codex_available = bool(codex) and 'error' not in codex
        if 'error' in data and not codex_available:
            self.icon.icon = create_status_image('C!' if data.get('auth_error') else '!', self._light_taskbar)
            self.icon.title = format_tooltip(data, codex or None)
            return

        claude_session = self._provider_entry(data, 'five_hour')
        bottom_entry = self._provider_entry(codex, 'five_hour') if codex_available else self._provider_entry(data, 'seven_day')

        self.icon.icon = create_icon_image(
            claude_session.get('utilization', 0) or 0,
            bottom_entry.get('utilization', 0) or 0,
            light_taskbar=self._light_taskbar,
        )
        self.icon.title = format_tooltip(data, codex if codex else None)

    def _on_theme_changed(self) -> None:
        light = taskbar_uses_light_theme()
        if light != self._light_taskbar:
            self._light_taskbar = light
            if self._last_response:
                self._render_tray()

    def update(self) -> None:
        result = self.cache.update()
        self._update_codex()
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
        top_key = ICON_FIELDS[0].split(':', 1)[0]
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

    def _update_codex(self) -> None:
        if self.codex_cache is None:
            return
        result = self.codex_cache.update()
        if result.data is None:
            return
        self._last_codex_response = result.data
        self.dashboard.history.record('codex', result.data)
        if 'error' not in result.data:
            self._process_provider_alerts('codex', result.data)

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
            if ALERT_TIME_AWARE and highest > last and highest < ALERT_TIME_AWARE_BELOW:
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
        if not ON_RESET_COMMAND:
            return
        five = (data.get('five_hour') or {}).get('utilization', 0) or 0
        seven = (data.get('seven_day') or {}).get('utilization', 0) or 0
        env = {
            'AGENTPULSE_EVENT': 'reset',
            'AGENTPULSE_PROVIDER': provider,
            'AGENTPULSE_VARIANT': variant,
            'AGENTPULSE_UTILIZATION': str(round(pct)),
            'AGENTPULSE_PREV_UTILIZATION': str(round(prev_pct)),
            'AGENTPULSE_UTILIZATION_FIVE_HOUR': str(round(five)),
            'AGENTPULSE_UTILIZATION_SEVEN_DAY': str(round(seven)),
            'AGENTPULSE_RESETS_AT': entry.get('resets_at', ''),
            'AGENTPULSE_TITLE': T['notify_reset_title'],
            'AGENTPULSE_MESSAGE': T['notify_reset'],
            'USAGE_MONITOR_EVENT': 'reset',
            'USAGE_MONITOR_VARIANT': variant,
            'USAGE_MONITOR_UTILIZATION': str(round(pct)),
            'USAGE_MONITOR_PREV_UTILIZATION': str(round(prev_pct)),
            'USAGE_MONITOR_UTILIZATION_FIVE_HOUR': str(round(five)),
            'USAGE_MONITOR_UTILIZATION_SEVEN_DAY': str(round(seven)),
            'USAGE_MONITOR_RESETS_AT': entry.get('resets_at', ''),
            'USAGE_MONITOR_TITLE': T['notify_reset_title'],
            'USAGE_MONITOR_MESSAGE': T['notify_reset'],
        }
        run_event_command(ON_RESET_COMMAND, env)

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
        if not ON_THRESHOLD_COMMAND or not self._first_update_done:
            return
        env = {
            'AGENTPULSE_EVENT': 'threshold',
            'AGENTPULSE_PROVIDER': provider,
            'AGENTPULSE_VARIANT': variant,
            'AGENTPULSE_UTILIZATION': str(round(pct)),
            'AGENTPULSE_THRESHOLD': str(round(threshold)),
            'AGENTPULSE_RESETS_AT': entry.get('resets_at', ''),
            'AGENTPULSE_TITLE': title,
            'AGENTPULSE_MESSAGE': message,
            'USAGE_MONITOR_EVENT': 'threshold',
            'USAGE_MONITOR_VARIANT': variant,
            'USAGE_MONITOR_UTILIZATION': str(round(pct)),
            'USAGE_MONITOR_THRESHOLD': str(round(threshold)),
            'USAGE_MONITOR_RESETS_AT': entry.get('resets_at', ''),
            'USAGE_MONITOR_TITLE': title,
            'USAGE_MONITOR_MESSAGE': message,
        }
        if extra_used:
            env.update({'AGENTPULSE_EXTRA_USED': extra_used, 'USAGE_MONITOR_EXTRA_USED': extra_used})
        if extra_limit:
            env.update({'AGENTPULSE_EXTRA_LIMIT': extra_limit, 'USAGE_MONITOR_EXTRA_LIMIT': extra_limit})
        run_event_command(ON_THRESHOLD_COMMAND, env)

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
        if self.codex_cache is not None:
            self.codex_cache.ensure_profile()
        while self.running:
            self.update()
            if self._deferred_notifications and not self._is_user_away():
                self._flush_deferred_notifications()
            interval = self._calculate_poll_interval()
            target = time.time() + interval
            self._next_poll_time = target
            while self.running and time.time() < target:
                time.sleep(1)
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
        if not ON_RESET_COMMAND:
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
            self.poll_loop()
        except Exception:
            crash_log(traceback.format_exc())

    def run(self) -> None:
        self.icon.run(setup=self._on_icon_ready)


UsageMonitorForClaude = AgentPulse


def crash_log(msg: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, msg[:2000], 'Agents Pulse - Error', 0x10)
