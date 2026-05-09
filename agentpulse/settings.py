"""Runtime settings for Agents Pulse."""
from __future__ import annotations

import ctypes
import json
import locale as _locale
import os
import sys
from pathlib import Path
from typing import Any

__all__ = [
    'ALERT_TIME_AWARE', 'ALERT_TIME_AWARE_BELOW',
    'BAR_BG', 'BAR_DIVIDER', 'BAR_FG', 'BAR_FG_WARN', 'BAR_MARKER', 'BG',
    'CODEX_ENABLED', 'CURRENCY_SYMBOL',
    'DASHBOARD_HOST', 'DASHBOARD_PORT',
    'FG', 'FG_DIM', 'FG_HEADING', 'FG_LINK',
    'HEATMAP_ENABLED',
    'ICON_DARK', 'ICON_FIELDS', 'ICON_LIGHT', 'IDLE_PAUSE',
    'LANGUAGE', 'LEGACY_SETTINGS_FILENAMES', 'MAX_BACKOFF',
    'ON_RESET_COMMAND', 'ON_THRESHOLD_COMMAND',
    'POLL_ERROR', 'POLL_FAST', 'POLL_FAST_EXTRA', 'POLL_INTERVAL',
    'POPUP_FIELDS', 'PREDICTION_DAY_END_TIME', 'PREDICTION_ENABLED',
    'QUIET_HOURS_ENABLED', 'QUIET_HOURS_END', 'QUIET_HOURS_START',
    'SETTINGS_FILENAME', 'TOOLTIP_FIELDS',
    'dashboard_settings', 'get_alert_thresholds', 'reload', 'save_dashboard_settings', 'settings_write_path',
]

SETTINGS_FILENAME = 'agentpulse-settings.json'
LEGACY_SETTINGS_FILENAMES = ('usage-monitor-settings.json',)

_MIN_INTS = {
    'poll_interval': 1,
    'poll_fast': 1,
    'poll_fast_extra': 1,
    'poll_error': 1,
    'max_backoff': 1,
    'idle_pause': 0,
}
_COLORS = {'bg', 'fg', 'fg_dim', 'fg_heading', 'fg_link', 'bar_bg', 'bar_fg', 'bar_fg_warn', 'bar_divider', 'bar_marker'}
_BOOLEANS = {'alert_time_aware', 'codex_enabled', 'prediction_enabled', 'heatmap_enabled', 'quiet_hours_enabled'}
_STRINGS = {'currency_symbol', 'language'}
_TIMES = {'prediction_day_end_time', 'quiet_hours_start', 'quiet_hours_end'}
_COMMANDS = {'on_reset_command', 'on_threshold_command'}
_FIELD_LISTS = {'tooltip_fields'}
_WILDCARD_LISTS = {'popup_fields'}
_ICON_COLORS = {'icon_light', 'icon_dark'}
_THRESHOLD_PREFIX = 'alert_thresholds_'
_BAR_MODES = {'utilization', 'overage'}
_DASHBOARD_KEYS = {
    'codex_enabled', 'icon_fields', 'tooltip_fields',
    'on_reset_command', 'on_threshold_command',
    'prediction_enabled', 'prediction_day_end_time',
    'heatmap_enabled', 'quiet_hours_enabled', 'quiet_hours_start', 'quiet_hours_end',
}


def _app_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _settings_search_dirs() -> list[Path]:
    home = Path.home() / '.claude'
    dirs = [_app_dir()]
    custom = os.environ.get('CLAUDE_CONFIG_DIR')
    if custom:
        custom_path = Path(custom)
        if custom_path != home:
            dirs.append(custom_path)
    dirs.append(home)
    return dirs


def settings_write_path() -> Path:
    return _settings_search_dirs()[0] / SETTINGS_FILENAME


def _candidate_files() -> list[Path]:
    names = (SETTINGS_FILENAME, *LEGACY_SETTINGS_FILENAMES)
    return [directory / name for directory in _settings_search_dirs() for name in names]


def _message_box(title: str, body: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, body, title, 0x30)
    except Exception:
        pass


def _valid_time(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split(':')
    if len(parts) != 2 or any(not part.isdigit() for part in parts):
        return False
    hour, minute = (int(part) for part in parts)
    return 0 <= hour < 24 and 0 <= minute < 60


def _valid_rgba(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 255 for item in value)
    )


def _dedupe_strings(items: list[Any], allow_wildcard: bool = False) -> list[str] | None:
    if any(not isinstance(item, str) or not item for item in items):
        return None
    if not allow_wildcard and '*' in items:
        return None
    if allow_wildcard and items.count('*') > 1:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item == '*' or item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _validate_icon_fields(value: object) -> tuple[bool, str | None]:
    if not isinstance(value, list):
        return False, 'expected an array'
    if len(value) != 2:
        return False, f'expected exactly 2 entries, got {len(value)}'
    if any(not isinstance(item, str) or not item for item in value):
        return False, 'all entries must be non-empty strings'
    bad = [item for item in value if ':' in item and item.split(':', 1)[1] not in _BAR_MODES]
    if bad:
        return False, 'unknown bar mode in: ' + ', '.join(bad)
    return True, None


def _validate(data: dict[str, Any], path: Path) -> dict[str, Any]:
    cleaned = dict(data)
    errors: list[str] = []

    def reject(key: str, reason: str) -> None:
        errors.append(f'  {key}: {reason}')
        cleaned.pop(key, None)

    for key, value in list(cleaned.items()):
        if key in _MIN_INTS:
            if isinstance(value, bool) or not isinstance(value, int):
                reject(key, f'expected an integer, got {type(value).__name__}')
            elif value < _MIN_INTS[key]:
                reject(key, f'must be >= {_MIN_INTS[key]}, got {value}')
        elif key in _COLORS:
            if not isinstance(value, str):
                reject(key, f'expected a color string, got {type(value).__name__}')
        elif key == 'alert_time_aware_below':
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 100:
                reject(key, 'expected a number between 1 and 100')
        elif key in _STRINGS:
            if not isinstance(value, str):
                reject(key, f'expected a string, got {type(value).__name__}')
        elif key in _TIMES:
            if not _valid_time(value):
                reject(key, 'expected HH:MM time')
        elif key in _BOOLEANS:
            if not isinstance(value, bool):
                reject(key, f'expected true or false, got {type(value).__name__}')
        elif key in _COMMANDS:
            if isinstance(value, str):
                cleaned[key] = [value]
            elif not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
                reject(key, f'expected a string or array of strings, got {type(value).__name__}')
        elif key in _FIELD_LISTS or key in _WILDCARD_LISTS:
            if not isinstance(value, list):
                reject(key, f'expected an array, got {type(value).__name__}')
            else:
                deduped = _dedupe_strings(value, allow_wildcard=key in _WILDCARD_LISTS)
                if deduped is None:
                    reject(key, 'expected non-empty strings')
                else:
                    cleaned[key] = deduped
        elif key == 'icon_fields':
            ok, reason = _validate_icon_fields(value)
            if not ok:
                reject(key, reason or 'invalid value')
        elif key in _ICON_COLORS:
            if not isinstance(value, dict):
                reject(key, f'expected an object, got {type(value).__name__}')
            else:
                for color_key, color_value in list(value.items()):
                    if not _valid_rgba(color_value):
                        errors.append(f'  {key}.{color_key}: expected [R, G, B, A] with integers 0-255')
                        value.pop(color_key, None)
        elif key.startswith(_THRESHOLD_PREFIX):
            if not isinstance(value, list):
                reject(key, f'expected an array, got {type(value).__name__}')
            elif any(isinstance(item, bool) or not isinstance(item, (int, float)) or not 1 <= item <= 100 for item in value):
                reject(key, 'all values must be numbers between 1 and 100')
            else:
                cleaned[key] = sorted(set(value))

    if errors:
        _message_box('Agents Pulse - Settings Error', f'Invalid values in settings file:\n{path}\n\n' + '\n'.join(errors))
    return cleaned


def _load_settings() -> dict[str, Any]:
    for path in _candidate_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8').strip()
            if not text:
                return {}
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError(f'Expected a JSON object, got {type(data).__name__}')
            return _validate(data, path)
        except (json.JSONDecodeError, ValueError) as exc:
            _message_box('Agents Pulse - Settings Error', f'Invalid JSON in settings file:\n{path}\n\n{exc}')
            return {}
        except OSError:
            return {}
    return {}


def _currency_symbol() -> str:
    try:
        _locale.setlocale(_locale.LC_MONETARY, '')
        return _locale.localeconv().get('currency_symbol', '') or ''
    except _locale.Error:
        return ''


def _icon_colors(key: str, defaults: dict[str, tuple[int, int, int, int]]) -> dict[str, tuple[int, int, int, int]]:
    raw = _S.get(key)
    if not isinstance(raw, dict):
        return defaults
    palette = dict(defaults)
    for name, value in raw.items():
        if _valid_rgba(value):
            palette[name] = tuple(value)  # type: ignore[assignment]
    return palette


def _clean_dashboard_settings(data: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    accepted: dict[str, object] = {}
    errors: list[str] = []
    for key, value in data.items():
        if not (key in _DASHBOARD_KEYS or key.startswith(_THRESHOLD_PREFIX)):
            errors.append(f'{key}: unsupported')
            continue
        if key == 'icon_fields':
            ok, _reason = _validate_icon_fields(value)
            if ok:
                accepted[key] = value
            else:
                errors.append(f'{key}: invalid value')
        elif key in {'tooltip_fields'}:
            if isinstance(value, list):
                deduped = _dedupe_strings(value)
                if deduped is not None:
                    accepted[key] = deduped
                else:
                    errors.append(f'{key}: invalid value')
            else:
                errors.append(f'{key}: invalid value')
        elif key in _COMMANDS:
            if isinstance(value, str):
                accepted[key] = [value] if value else []
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                accepted[key] = value
            else:
                errors.append(f'{key}: invalid value')
        elif key in {'codex_enabled', 'prediction_enabled', 'heatmap_enabled', 'quiet_hours_enabled'}:
            if isinstance(value, bool):
                accepted[key] = value
            else:
                errors.append(f'{key}: invalid value')
        elif key in _TIMES:
            if _valid_time(value):
                accepted[key] = value
            else:
                errors.append(f'{key}: invalid value')
        elif key.startswith(_THRESHOLD_PREFIX):
            if isinstance(value, list) and all(not isinstance(item, bool) and isinstance(item, (int, float)) and 1 <= item <= 100 for item in value):
                accepted[key] = sorted(set(value))
            else:
                errors.append(f'{key}: invalid value')
        else:
            errors.append(f'{key}: unsupported')
    return accepted, errors


def dashboard_settings() -> dict[str, object]:
    return {
        'codex_enabled': CODEX_ENABLED,
        'icon_fields': ICON_FIELDS,
        'tooltip_fields': TOOLTIP_FIELDS,
        'alert_thresholds_five_hour': get_alert_thresholds('five_hour'),
        'alert_thresholds_seven_day': get_alert_thresholds('seven_day'),
        'alert_thresholds_codex_five_hour': get_alert_thresholds('five_hour', provider='codex'),
        'alert_thresholds_codex_seven_day': get_alert_thresholds('seven_day', provider='codex'),
        'on_reset_command': ON_RESET_COMMAND,
        'on_threshold_command': ON_THRESHOLD_COMMAND,
        'prediction_enabled': PREDICTION_ENABLED,
        'prediction_day_end_time': PREDICTION_DAY_END_TIME,
        'heatmap_enabled': HEATMAP_ENABLED,
        'quiet_hours_enabled': QUIET_HOURS_ENABLED,
        'quiet_hours_start': QUIET_HOURS_START,
        'quiet_hours_end': QUIET_HOURS_END,
    }


def save_dashboard_settings(data: dict[str, object]) -> tuple[bool, list[str], Path]:
    cleaned, errors = _clean_dashboard_settings(data)
    path = settings_write_path()
    if errors:
        return False, errors, path

    existing: dict[str, object] = {}
    try:
        loaded = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if isinstance(loaded, dict):
            existing = loaded
    except (OSError, json.JSONDecodeError):
        existing = {}

    existing.update(cleaned)
    try:
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    except OSError as exc:
        return False, [str(exc)], path
    reload()
    return True, [], path


_S = _load_settings()

POLL_INTERVAL = _S.get('poll_interval', 180)
POLL_FAST = _S.get('poll_fast', 120)
POLL_FAST_EXTRA = _S.get('poll_fast_extra', 2)
POLL_ERROR = _S.get('poll_error', 30)
MAX_BACKOFF = _S.get('max_backoff', 900)
IDLE_PAUSE = _S.get('idle_pause', 300)

DASHBOARD_HOST = '127.0.0.1'
DASHBOARD_PORT = 8766
PREDICTION_ENABLED = _S.get('prediction_enabled', True)
PREDICTION_DAY_END_TIME = _S.get('prediction_day_end_time', '18:00')
HEATMAP_ENABLED = _S.get('heatmap_enabled', True)
QUIET_HOURS_ENABLED = _S.get('quiet_hours_enabled', False)
QUIET_HOURS_START = _S.get('quiet_hours_start', '22:00')
QUIET_HOURS_END = _S.get('quiet_hours_end', '08:00')

BG = _S.get('bg', '#1e1e1e')
FG = _S.get('fg', '#cccccc')
FG_DIM = _S.get('fg_dim', '#888888')
FG_HEADING = _S.get('fg_heading', '#ffffff')
FG_LINK = _S.get('fg_link', '#4a9eff')
BAR_BG = _S.get('bar_bg', '#333333')
BAR_FG = _S.get('bar_fg', '#4a9eff')
BAR_FG_WARN = _S.get('bar_fg_warn', '#e05050')
BAR_DIVIDER = _S.get('bar_divider', '#000c')
BAR_MARKER = _S.get('bar_marker', '#fffc')

ICON_LIGHT = _icon_colors('icon_light', {
    'fg': (255, 255, 255, 255),
    'fg_half': (255, 255, 255, 80),
    'fg_dim': (255, 255, 255, 140),
})
ICON_DARK = _icon_colors('icon_dark', {
    'fg': (0, 0, 0, 255),
    'fg_half': (0, 0, 0, 80),
    'fg_dim': (0, 0, 0, 140),
})

ICON_FIELDS = _S.get('icon_fields', ['five_hour', 'seven_day'])
TOOLTIP_FIELDS = _S.get('tooltip_fields', ['five_hour', 'seven_day'])
POPUP_FIELDS = _S.get('popup_fields', ['*'])
ALERT_TIME_AWARE = _S.get('alert_time_aware', True)
ALERT_TIME_AWARE_BELOW = _S.get('alert_time_aware_below', 90)
_SYSTEM_CURRENCY_SYMBOL = _currency_symbol()
CURRENCY_SYMBOL = _S.get('currency_symbol', _SYSTEM_CURRENCY_SYMBOL)
LANGUAGE = _S.get('language', '')
ON_RESET_COMMAND = _S.get('on_reset_command', [])
ON_THRESHOLD_COMMAND = _S.get('on_threshold_command', [])
CODEX_ENABLED = _S.get('codex_enabled', True)

_ALERT_THRESHOLDS: dict[str, list[float]] = {
    'five_hour': [50, 80, 95],
    'seven_day': [95],
    'extra_usage': [50, 80, 95],
}


def get_alert_thresholds(variant_key: str, provider: str = 'claude') -> list[float]:
    """Return configured alert thresholds with period-level fallback."""
    provider_key = f'{_THRESHOLD_PREFIX}{provider}_{variant_key}'
    if provider != 'claude' and provider_key in _S:
        return _S[provider_key]

    exact_key = f'{_THRESHOLD_PREFIX}{variant_key}'
    if exact_key in _S:
        return _S[exact_key]
    if variant_key in _ALERT_THRESHOLDS:
        return _ALERT_THRESHOLDS[variant_key]

    parts = variant_key.split('_', 2)
    if len(parts) == 3:
        base = f'{parts[0]}_{parts[1]}'
        provider_base = f'{_THRESHOLD_PREFIX}{provider}_{base}'
        if provider != 'claude' and provider_base in _S:
            return _S[provider_base]
        base_key = f'{_THRESHOLD_PREFIX}{base}'
        if base_key in _S:
            return _S[base_key]
        return _ALERT_THRESHOLDS.get(base, [])
    return []


def reload() -> None:
    """Re-read the settings file and update module-level variables in place.

    Called automatically after a successful dashboard save so that changes
    take effect immediately without restarting the application.
    Only dashboard-configurable keys are updated; static values such as
    poll intervals require a restart (they are read at import time by other
    modules that cache them locally).
    """
    global _S
    global QUIET_HOURS_ENABLED, QUIET_HOURS_START, QUIET_HOURS_END
    global ON_RESET_COMMAND, ON_THRESHOLD_COMMAND
    global PREDICTION_ENABLED, PREDICTION_DAY_END_TIME
    global HEATMAP_ENABLED, CODEX_ENABLED
    global ICON_FIELDS, TOOLTIP_FIELDS
    global ALERT_TIME_AWARE, ALERT_TIME_AWARE_BELOW

    _S = _load_settings()

    QUIET_HOURS_ENABLED = _S.get('quiet_hours_enabled', False)
    QUIET_HOURS_START = _S.get('quiet_hours_start', '22:00')
    QUIET_HOURS_END = _S.get('quiet_hours_end', '08:00')
    ON_RESET_COMMAND = _S.get('on_reset_command', [])
    ON_THRESHOLD_COMMAND = _S.get('on_threshold_command', [])
    PREDICTION_ENABLED = _S.get('prediction_enabled', True)
    PREDICTION_DAY_END_TIME = _S.get('prediction_day_end_time', '18:00')
    HEATMAP_ENABLED = _S.get('heatmap_enabled', True)
    CODEX_ENABLED = _S.get('codex_enabled', True)
    ICON_FIELDS = _S.get('icon_fields', ['five_hour', 'seven_day'])
    TOOLTIP_FIELDS = _S.get('tooltip_fields', ['five_hour', 'seven_day'])
    ALERT_TIME_AWARE = _S.get('alert_time_aware', True)
    ALERT_TIME_AWARE_BELOW = _S.get('alert_time_aware_below', 90)
