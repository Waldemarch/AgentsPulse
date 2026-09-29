"""
Usage Popup
===========

HTML popup window and popup data projection.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import webview  # type: ignore[import-untyped]

from . import __version__
from .claude_cli import CHANGELOG_URL, find_installations
from .formatting import (
    elapsed_pct, expand_popup_fields, field_period, format_credits, format_duration,
    format_outlook, limit_band, midnight_positions, popup_label, time_until,
)
from .i18n import T
from .provider_cache import UsageSnapshot
from .providers import SECONDARY_PROVIDERS_BY_NAME
from . import settings
from .settings import (
    BAR_BG, BAR_DIVIDER, BAR_FG, BAR_FG_TIGHT, BAR_FG_WARN, BAR_MARKER, BG,
    FG, FG_DIM, FG_HEADING, FG_LINK, POLL_FAST, POPUP_FIELDS, PROVIDER_COLORS, PROVIDER_LABELS,
    save_dashboard_settings,
)

if TYPE_CHECKING:
    from .app import AgentPulse
    from .budget import DailyBudget
    from .cache import CacheSnapshot
    from .forecast import Outlook

__all__ = ['UsagePopup']

_POPUP_DIR = Path(__file__).parent / 'popup'
_BASELINE_DPI = 96
_GWL_EXSTYLE = -20
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_LWA_ALPHA = 0x00000002
# Providers without an entry in PROVIDER_COLORS share a neutral colour.
_OTHER_PROVIDER_COLOR = '#898781'


def _profile_view(profile: dict[str, Any] | None) -> dict[str, str] | None:
    if not profile:
        return None
    account = profile.get('account', {}) if isinstance(profile, dict) else {}
    org = profile.get('organization', {}) if isinstance(profile, dict) else {}
    return {
        'email': account.get('email', '') if isinstance(account, dict) else '',
        'plan': (org.get('organization_type', '') if isinstance(org, dict) else '').replace('_', ' ').title(),
    }


def _usage_entries(usage: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None, int | None, str]]:
    """Return ``(label, entry, window length, field)`` for every popup field with usage, in display order."""
    entries: list[tuple[str, dict[str, Any] | None, int | None, str]] = []
    for key in expand_popup_fields(POPUP_FIELDS, usage):
        value = usage.get(key)
        if isinstance(value, dict) and value.get('utilization') is not None:
            entries.append((popup_label(key), value, field_period(key), key))
    return entries


def _bar_view(
    label: str,
    entry: dict[str, Any],
    period: int | None,
    outlook: Outlook | None = None,
    budget: DailyBudget | None = None,
) -> dict[str, Any]:
    """Build one usage bar: fill, forecast to the reset and its band, status, reset text, time markers and today's budget.

    Without an outlook (no reset time or unknown window) the bar only tells a
    reached limit from a usable one.  Only a weekly quota has a daily budget,
    and only a session's forecast has a band.
    """
    pct = entry.get('utilization', 0) or 0
    resets_at = entry.get('resets_at', '') or ''
    time_pct = elapsed_pct(resets_at, period) if period else None
    if outlook is not None:
        status = outlook.status
        status_text = format_outlook(outlook)
        forecast = outlook.forecast_pct
    else:
        status = 'blocked' if pct >= 100 else 'ok'
        status_text = T['status_blocked'] if pct >= 100 else ''
        forecast = None
    shown = forecast is not None and status in {'ok', 'tight'}
    has_band = outlook is not None and outlook.forecast_low_pct is not None and outlook.forecast_high_pct is not None
    return {
        'label': label,
        'pct_text': f'{pct:.0f}%',
        'fill_pct': max(0.0, min(1.0, pct / 100)),
        'status': status,
        'status_text': status_text,
        'forecast_pct': None if forecast is None else max(0.0, min(1.0, forecast / 100)),
        # A projected limit already names its time, so only calm and tight bars add the forecast.
        'forecast_text': T['forecast_short'].format(pct=f'{forecast:.0f}') if shown else '',
        'forecast_title': _forecast_title(forecast, outlook) if shown else '',
        'band_low': max(0.0, min(1.0, outlook.forecast_low_pct / 100)) if has_band else None,
        'band_high': max(0.0, min(1.0, outlook.forecast_high_pct / 100)) if has_band else None,
        'limit_text': _limit_text(outlook),
        'reset_text': time_until(resets_at) if resets_at else '',
        'midnights': midnight_positions(resets_at, period) if period else [],
        'marker_rel': max(0.0, min(1.0, time_pct / 100)) if time_pct is not None else None,
        'budget_text': _budget_text(budget),
        'budget_over': budget is not None and round(budget.used) > round(budget.allowance),
    }


def _forecast_title(forecast: float, outlook: Outlook) -> str:
    """Hover text of a calm or tight bar: the forecast at the reset, with its band when that spans more than a point."""
    low, high = outlook.forecast_low_pct, outlook.forecast_high_pct
    if low is None or high is None or round(low) == round(high):
        return T['forecast_at_reset'].format(pct=f'{forecast:.0f}')
    return T['forecast_at_reset_band'].format(pct=f'{forecast:.0f}', low=f'{low:.0f}', high=f'{high:.0f}')


def _limit_text(outlook: Outlook | None) -> str:
    """The line under a bar projected to run out: the band its limit falls in, and how long it lasts before the reset; '' otherwise."""
    if outlook is None or outlook.status != 'limit' or outlook.limit_at is None:
        return ''
    parts = []
    clocks = limit_band(outlook)
    if clocks is not None:
        parts.append(T['limit_between'].format(earliest=clocks[0], latest=clocks[1]))
    parts.append(T['gap_before_reset'].format(duration=format_duration(outlook.reset_at - outlook.limit_at)))
    return ' \u00b7 '.join(parts)


def _budget_text(budget: DailyBudget | None) -> str:
    if budget is None:
        return ''
    return T['budget_today'].format(used=f'{budget.used:.0f}', allowance=f'{budget.allowance:.0f}')


def _usage_view(
    usage: dict[str, Any] | None,
    outlooks: dict[str, Outlook] | None = None,
    budgets: dict[str, DailyBudget] | None = None,
) -> list[dict[str, Any]]:
    if not usage:
        return []
    known = outlooks or {}
    daily = budgets or {}
    return [_bar_view(label, entry, period, known.get(key), daily.get(key)) for label, entry, period, key in _usage_entries(usage) if entry]


def _status_view(usage: dict[str, Any] | None, last_error: str | None, last_success_time: float | None, refreshing: bool, next_poll_time: float | None) -> dict[str, Any]:
    if not usage:
        if last_error:
            return {'text': last_error[:120], 'is_error': True}
        return {'text': T['status_refreshing'], 'is_error': False, 'refreshing': True}
    return {
        'last_success_time': last_success_time,
        'next_poll_time': next_poll_time,
        'refreshing': refreshing,
        'error': last_error[:120] if last_error else None,
    }


def _extra_view(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    if not usage:
        return None
    extra = usage.get('extra_usage')
    if not isinstance(extra, dict) or not extra.get('is_enabled'):
        return None
    limit = extra.get('monthly_limit', 0) or 0
    if limit <= 0:
        return None
    used = extra.get('used_credits', 0) or 0
    pct = used / limit * 100
    return {
        'pct_text': f'{pct:.0f}%',
        'fill_pct': max(0.0, min(1.0, pct / 100)),
        'spent_text': T['extra_usage_spent'].format(used=format_credits(used), limit=format_credits(limit)),
    }


def _snapshot_to_dict(
    snap: CacheSnapshot,
    installations: list[dict[str, str]] | None = None,
    next_poll_time: float | None = None,
    outlooks: dict[str, Outlook] | None = None,
    budgets: dict[str, DailyBudget] | None = None,
) -> dict[str, Any]:
    if installations is None:
        installations = [{'name': item.name, 'version': item.version} for item in find_installations()]
    return {
        'profile': _profile_view(snap.profile),
        'usage': _usage_view(snap.usage, outlooks, budgets),
        'extra': _extra_view(snap.usage),
        'installations': installations,
        'status': _status_view(snap.usage, snap.last_error, snap.last_success_time, snap.refreshing, next_poll_time),
    }


def _secondary_snapshot_to_dict(
    snap: UsageSnapshot,
    cli_version: str | None = None,
    outlooks: dict[str, Outlook] | None = None,
    budgets: dict[str, DailyBudget] | None = None,
) -> dict[str, Any]:
    """Build the popup view-model for a non-Claude provider."""
    installations = [{'name': 'CLI', 'version': cli_version}] if cli_version else []
    return {
        'profile': _profile_view(snap.profile),
        'usage': _usage_view(snap.usage, outlooks, budgets),
        'extra': None,
        'installations': installations,
        'status': _status_view(snap.usage, snap.last_error, snap.last_success_time, snap.refreshing, None),
    }


def _provider_entries(claude_data: dict[str, Any], secondary: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Build the provider list the popup renders as tabs, Claude first."""
    entries = [{
        'id': 'claude',
        'label': PROVIDER_LABELS['claude'],
        'color': PROVIDER_COLORS.get('claude', _OTHER_PROVIDER_COLOR),
        'install_title': T['claude_code'],
        'data': claude_data,
    }]
    for provider, data in secondary:
        spec = SECONDARY_PROVIDERS_BY_NAME[provider]
        entries.append({
            'id': provider,
            'label': PROVIDER_LABELS[provider],
            'color': PROVIDER_COLORS.get(provider, _OTHER_PROVIDER_COLOR),
            'install_title': T.get(spec.install_title_key, spec.install_title_fallback),
            'data': data,
        })
    return entries


def _init_config(
    snap: CacheSnapshot,
    secondary: list[tuple[str, dict[str, Any]]] | None = None,
    next_poll_time: float | None = None,
    outlooks: dict[str, Outlook] | None = None,
    budgets: dict[str, dict[str, DailyBudget]] | None = None,
) -> dict[str, Any]:
    return {
        'colors': {
            'bg': BG,
            'fg': FG,
            'fg_dim': FG_DIM,
            'fg_heading': FG_HEADING,
            'fg_link': FG_LINK,
            'bar_bg': BAR_BG,
            'bar_fg': BAR_FG,
            'bar_fg_tight': BAR_FG_TIGHT,
            'bar_fg_warn': BAR_FG_WARN,
            'bar_divider': BAR_DIVIDER,
            'bar_marker': BAR_MARKER,
        },
        't': {
            'title': T['popup_title'],
            'account': T['account'],
            'email': T['email'],
            'plan': T['plan'],
            'usage': T['usage'],
            'extra_usage': T['extra_usage'],
            'claude_code': T['claude_code'],
            'changelog': T['changelog'],
            'tab_all': T.get('tab_all', 'All'),
            'status_updated_s': T['status_updated_s'],
            'status_updated': T['status_updated'],
            'status_next_update': T['status_next_update'],
            'status_refreshing': T['status_refreshing'],
            'duration_hm': T['duration_hm'],
            'duration_m': T['duration_m'],
            'duration_s': T['duration_s'],
            'settings_panel': T.get('settings_panel', 'SETTINGS'),
            'show_install_label': T.get('show_install_label', 'Show Claude Code versions'),
            'email_display_label': T.get('email_display_label', 'Email'),
            'email_show': T.get('email_show', 'Show'),
            'email_blur': T.get('email_blur', 'Blur'),
            'email_hide': T.get('email_hide', 'Hide'),
            'open_dashboard': T['popup_open_dashboard'],
            'refresh': T['popup_refresh'],
            'refresh_fresh': T['popup_refresh_fresh'],
        },
        'app_version': __version__,
        # The caches skip a provider updated this recently, so Refresh waits until then.
        'refresh_cooldown': POLL_FAST,
        'popup_settings': {
            # Read via the module so values saved from the popup (which call
            # settings.reload()) are reflected the next time it opens.
            'show_install_section': settings.SHOW_INSTALL_SECTION,
            'email_display': settings.EMAIL_DISPLAY,
        },
        'providers': _provider_entries(
            _snapshot_to_dict(snap, next_poll_time=next_poll_time, outlooks=(outlooks or {}).get('claude'), budgets=(budgets or {}).get('claude')),
            secondary or [],
        ),
    }


class _PopupApi:
    def __init__(self, popup: UsagePopup) -> None:
        self._popup = popup

    def close(self) -> None:
        self._popup._close()

    def open_url(self) -> None:
        webbrowser.open(CHANGELOG_URL)

    def report_height(self, height: int) -> None:
        if height and height != self._popup._last_height:
            self._popup._last_height = height
            self._popup._resize_and_position(height)
            if not self._popup._shown:
                self._popup._show_window()

    def save_popup_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        ok, errors, _ = save_dashboard_settings(data)
        return {'ok': ok, 'errors': errors}

    def open_dashboard(self) -> None:
        self._popup.app.on_open_dashboard()
        self._popup._close()

    def refresh(self) -> None:
        self._popup.app.refresh_now()


class UsagePopup:
    """A small frameless WebView popup pinned near the tray."""

    WIDTH = 340
    _CHECK_MS = 2000

    def __init__(self, app: AgentPulse) -> None:
        self.app = app
        self._running = True
        self._closed = threading.Event()
        self._popup_hwnd = 0
        self._last_height = 400
        self._shown = False
        snap = app.cache.snapshot
        self._last_version = snap.version
        self._secondary = app.secondary_providers()
        self._last_secondary_versions = {name: cache.snapshot.version for name, cache in self._secondary}
        self._cli_versions = {name: SECONDARY_PROVIDERS_BY_NAME[name].cli_version() for name, _cache in self._secondary}

        api = _PopupApi(self)
        self._window = webview.create_window(
            '',
            url=str(_POPUP_DIR / 'popup.html'),
            width=self.WIDTH,
            height=self._last_height,
            resizable=False,
            frameless=True,
            shadow=False,
            easy_drag=False,
            on_top=True,
            hidden=True,
            background_color=BG,
            js_api=api,
        )
        self._window.events.loaded += self._on_loaded
        self._window.events.closed += self._on_window_closed
        threading.Thread(target=self._dismiss_watch, daemon=True).start()
        self._closed.wait()

    def _on_loaded(self) -> None:
        outlooks, budgets = self.app.quota_insights()
        config = _init_config(
            self.app.cache.snapshot,
            secondary=self._secondary_views(outlooks, budgets),
            next_poll_time=self.app.next_poll_time,
            outlooks=outlooks,
            budgets=budgets,
        )
        self._window.evaluate_js(f'init({json.dumps(config)})')
        self._popup_hwnd = self._window.native.Handle.ToInt32()
        self._prepare_native_window()
        self._window.show()

    def _prepare_native_window(self) -> None:
        style = ctypes.windll.user32.GetWindowLongW(self._popup_hwnd, _GWL_EXSTYLE)
        style = (style | _WS_EX_TOOLWINDOW | _WS_EX_LAYERED) & ~_WS_EX_APPWINDOW
        ctypes.windll.user32.SetWindowLongW(self._popup_hwnd, _GWL_EXSTYLE, style)
        ctypes.windll.user32.SetLayeredWindowAttributes(self._popup_hwnd, 0, 0, _LWA_ALPHA)

    def _show_window(self) -> None:
        style = ctypes.windll.user32.GetWindowLongW(self._popup_hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(self._popup_hwnd, _GWL_EXSTYLE, style & ~_WS_EX_LAYERED)
        self._shown = True
        threading.Thread(target=self._update_loop, daemon=True).start()

    def _dismiss_watch(self) -> None:
        thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        WM_QUIT = 0x0012

        def close_from_hook() -> None:
            if self._shown:
                ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)

        call_next = ctypes.windll.user32.CallNextHookEx
        call_next.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        call_next.restype = ctypes.c_long

        class MouseInfo(ctypes.Structure):
            _fields_ = [
                ('pt', ctypes.wintypes.POINT),
                ('mouseData', ctypes.wintypes.DWORD),
                ('flags', ctypes.wintypes.DWORD),
                ('time', ctypes.wintypes.DWORD),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
            ]

        class KeyInfo(ctypes.Structure):
            _fields_ = [
                ('vkCode', ctypes.wintypes.DWORD),
                ('scanCode', ctypes.wintypes.DWORD),
                ('flags', ctypes.wintypes.DWORD),
                ('time', ctypes.wintypes.DWORD),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
            ]

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        def mouse_proc(code, wparam, lparam):
            if code >= 0 and wparam == 0x0201 and self._popup_hwnd:
                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self._popup_hwnd, ctypes.byref(rect))
                info = ctypes.cast(lparam, ctypes.POINTER(MouseInfo)).contents
                inside = rect.left <= info.pt.x <= rect.right and rect.top <= info.pt.y <= rect.bottom
                if not inside:
                    close_from_hook()
            return call_next(None, code, wparam, lparam)

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        def key_proc(code, wparam, lparam):
            if code >= 0 and wparam == 0x0100:
                info = ctypes.cast(lparam, ctypes.POINTER(KeyInfo)).contents
                if info.vkCode == 0x1B:
                    close_from_hook()
            return call_next(None, code, wparam, lparam)

        mouse_hook = ctypes.windll.user32.SetWindowsHookExW(14, mouse_proc, None, 0)
        key_hook = ctypes.windll.user32.SetWindowsHookExW(13, key_proc, None, 0)
        try:
            msg = ctypes.wintypes.MSG()
            while self._running and ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            if mouse_hook:
                ctypes.windll.user32.UnhookWindowsHookEx(mouse_hook)
            if key_hook:
                ctypes.windll.user32.UnhookWindowsHookEx(key_hook)
        self._close()

    def _on_window_closed(self) -> None:
        self._running = False
        self._closed.set()

    def _close(self) -> None:
        self._running = False
        try:
            self._window.destroy()
        except Exception:
            pass
        self._closed.set()

    def _update_loop(self) -> None:
        installations = [{'name': item.name, 'version': item.version} for item in find_installations()]
        last_poll = self.app.next_poll_time
        while self._running:
            time.sleep(self._CHECK_MS / 1000)
            if not self._running:
                return
            try:
                snap = self.app.cache.snapshot
                versions = {name: cache.snapshot.version for name, cache in self._secondary}
                poll_now = self.app.next_poll_time
                changed = snap.version != self._last_version or versions != self._last_secondary_versions or poll_now != last_poll
                if not changed:
                    continue
                if snap.version != self._last_version:
                    self._last_version = snap.version
                    installations = [{'name': item.name, 'version': item.version} for item in find_installations()]
                self._last_secondary_versions = versions
                last_poll = poll_now
                outlooks, budgets = self.app.quota_insights()
                claude_data = _snapshot_to_dict(
                    snap, installations=installations, next_poll_time=poll_now, outlooks=outlooks.get('claude'), budgets=budgets.get('claude'),
                )
                entries = [{'id': 'claude', 'data': claude_data}]
                entries.extend({'id': name, 'data': data} for name, data in self._secondary_views(outlooks, budgets))
                self._window.evaluate_js(f'updateProviders({json.dumps(entries)})')
            except Exception:
                return

    def _secondary_views(
        self,
        outlooks: dict[str, dict[str, Outlook]],
        budgets: dict[str, dict[str, DailyBudget]] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Build the popup view-model of every active non-Claude provider."""
        daily = budgets or {}
        return [
            (name, _secondary_snapshot_to_dict(cache.snapshot, self._cli_versions.get(name), outlooks.get(name), daily.get(name)))
            for name, cache in self._secondary
        ]

    def _tray_position(self, physical_width: int, physical_height: int) -> tuple[int, int]:
        area = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0)
        dpi = ctypes.windll.user32.GetDpiForWindow(self._popup_hwnd) or ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / _BASELINE_DPI
        margin = 12
        x = area.left + margin if area.left > 0 else area.right - physical_width - margin
        y = area.top + margin if area.top > 0 else area.bottom - physical_height - margin
        return int(x / scale), int(y / scale)

    def _resize_and_position(self, height: int) -> None:
        dpi = ctypes.windll.user32.GetDpiForWindow(self._popup_hwnd) or ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / _BASELINE_DPI
        width = int(self.WIDTH * scale)
        physical_height = int(height * scale)
        self._window.resize(width, physical_height)
        x, y = self._tray_position(width, physical_height)
        self._window.move(x, y)
