"""Formatting utilities shared by tray, popup, and notifications."""
from __future__ import annotations

import locale as _locale
from datetime import datetime, timedelta, timezone
from typing import Any

from . import settings as _settings
from .i18n import T
from .settings import CURRENCY_SYMBOL, PROVIDER_LABELS, _SYSTEM_CURRENCY_SYMBOL

__all__ = [
    'PERIOD_5H', 'PERIOD_7D',
    'burn_rate_info', 'elapsed_pct', 'expand_popup_fields', 'field_period',
    'format_burn_text', 'format_credits', 'format_tooltip',
    'midnight_positions', 'parse_field_name', 'period_to_field_name', 'popup_label',
    'time_until', 'tooltip_label',
]

PERIOD_5H = 5 * 60 * 60
PERIOD_7D = 7 * 24 * 60 * 60

_NUMBERS = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4,
    'five': 5, 'six': 6, 'seven': 7, 'eight': 8,
    'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12,
}
_UNITS = {'hour': ('h', 3600), 'day': ('d', 24 * 3600)}
_NUMBER_WORDS = {number: word for word, number in _NUMBERS.items()}
_TITLE_OVERRIDES = {'api': 'API', 'oauth': 'OAuth', 'ai': 'AI', 'omelette': ''}


def parse_field_name(field: str) -> tuple[int, str, str | None] | None:
    """Parse names like `five_hour` or `seven_day_sonnet`."""
    first, sep, rest = field.partition('_')
    if not sep:
        return None
    unit, sep, variant = rest.partition('_')
    number = _NUMBERS.get(first)
    if number is None or unit not in _UNITS:
        return None
    return number, unit, (variant if sep else None)


def _title_words(value: str) -> str:
    words = []
    for word in value.split('_'):
        mapped = _TITLE_OVERRIDES.get(word.lower())
        words.append(mapped if mapped is not None else word.title())
    return ' '.join(part for part in words if part)


def tooltip_label(field: str) -> str:
    parsed = parse_field_name(field)
    if parsed is None:
        return _title_words(field)
    number, unit, variant = parsed
    text = f'{number}{_UNITS[unit][0]}'
    if variant:
        text += f' {_title_words(variant)}'
    return text


def popup_label(field: str) -> str:
    parsed = parse_field_name(field)
    if parsed is None:
        return _title_words(field)

    number, unit, variant = parsed
    variant_text = _title_words(variant) if variant else ''
    if variant_text:
        suffix = variant_text
    elif unit == 'hour':
        suffix = f'{number}hr'
    else:
        suffix = f'{number} {unit}'
    template = 'session_label' if unit == 'hour' else 'weekly_label'
    return T[template].format(suffix=suffix)


def field_period(field: str) -> int | None:
    parsed = parse_field_name(field)
    if parsed is None:
        return None
    number, unit, _variant = parsed
    return number * _UNITS[unit][1]


def period_to_field_name(seconds: int) -> str | None:
    """Return the canonical field name for a quota window length.

    Inverse of :func:`field_period`.  Providers that report windows as a raw
    duration get the same field names as providers that report them by name,
    so no second table of window names is needed.

    Parameters
    ----------
    seconds
        Window length in seconds.

    Returns
    -------
    str or None
        A name such as ``'five_hour'`` or ``'seven_day'``, or None when the
        duration is not a whole number of hours or days that the shared
        vocabulary can express.
    """
    if seconds <= 0:
        return None
    for unit in ('day', 'hour'):
        unit_seconds = _UNITS[unit][1]
        if seconds < unit_seconds or seconds % unit_seconds:
            continue
        word = _NUMBER_WORDS.get(seconds // unit_seconds)
        if word is not None:
            return f'{word}_{unit}'
    return None


def _field_order(field: str) -> tuple[int, int, int, str]:
    parsed = parse_field_name(field)
    if parsed is None:
        return 2, 0, 0, field
    number, unit, variant = parsed
    return (0 if unit == 'hour' else 1, number, 0 if variant is None else 1, variant or '')


def expand_popup_fields(popup_fields: list[str], usage_data: dict[str, Any]) -> list[str]:
    available = {
        key for key, value in usage_data.items()
        if isinstance(value, dict)
        and value.get('utilization') is not None
        and 'resets_at' in value
    }
    chosen: list[str] = []
    seen: set[str] = set()
    for field in popup_fields:
        if field == '*':
            fields = sorted((name for name in available if name not in seen), key=_field_order)
        else:
            fields = [field] if field in available and field not in seen else []
        for name in fields:
            seen.add(name)
            chosen.append(name)
    return chosen


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed


def elapsed_pct(resets_at: str, period_seconds: int) -> float | None:
    if not resets_at or period_seconds <= 0:
        return None
    try:
        reset = _parse_time(resets_at)
        if reset.tzinfo is None:
            return None
        remaining = (reset - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return None
    elapsed = period_seconds - remaining
    return max(0.0, min(100.0, elapsed / period_seconds * 100.0))


def burn_rate_info(utilization: float, resets_at: str, period_seconds: int | None) -> dict[str, Any] | None:
    if not resets_at or not period_seconds or period_seconds <= 0:
        return None
    try:
        reset = _parse_time(resets_at)
        if reset.tzinfo is None:
            return None
        remaining = max(0.0, (reset - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None
    elapsed = max(0.0, period_seconds - remaining)
    if elapsed <= 0:
        return None
    time_pct = max(0.0, min(100.0, elapsed / period_seconds * 100.0))
    hourly = utilization / (elapsed / 3600.0)
    eta = None
    if 0 < utilization < 100 and hourly > 0:
        eta = (100.0 - utilization) / hourly * 3600.0
    return {
        'time_pct': time_pct,
        'burn_per_hour': hourly,
        'eta_seconds': eta,
        'healthy': utilization <= time_pct,
        'pace_delta': utilization - time_pct,
    }


def _duration_short(seconds: float) -> str:
    minutes = max(1, int(seconds / 60))
    if minutes < 60:
        return T['duration_m'].format(m=minutes)
    return T['duration_hm'].format(h=minutes // 60, m=minutes % 60)


def format_burn_text(utilization: float, resets_at: str, period_seconds: int | None) -> str:
    info = burn_rate_info(utilization, resets_at, period_seconds)
    if info is None:
        return ''
    pace = T.get('pace_healthy', 'on pace') if info['healthy'] else T.get('pace_ahead', 'ahead of pace')
    if info['eta_seconds'] is None:
        return pace
    return T.get('burn_eta', 'ETA {duration} - {pace}').format(duration=_duration_short(info['eta_seconds']), pace=pace)


def midnight_positions(resets_at: str, period_seconds: int) -> list[float]:
    if not resets_at or period_seconds <= 0:
        return []
    try:
        end = _parse_time(resets_at)
        if end.tzinfo is None:
            return []
        start = end - timedelta(seconds=period_seconds)
    except Exception:
        return []

    start_local = start.astimezone()
    end_local = end.astimezone()
    marker = (start_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    positions: list[float] = []
    while marker < end_local:
        relative = (marker - start_local).total_seconds() / period_seconds
        if relative > 0.003:
            positions.append(relative)
        marker += timedelta(days=1)
    return positions


def time_until(iso_str: str) -> str:
    try:
        reset = _parse_time(iso_str)
        if reset.tzinfo is None:
            return ''
    except Exception:
        return ''
    now = datetime.now(timezone.utc)
    total_minutes = max(0, int((reset - now).total_seconds() / 60))
    if total_minutes == 0:
        return ''

    local_reset = reset.astimezone()
    if local_reset.second >= 30:
        local_reset = local_reset.replace(second=0) + timedelta(minutes=1)
    else:
        local_reset = local_reset.replace(second=0)

    clock = local_reset.strftime('%H:%M')
    today = datetime.now().date()
    if local_reset.date() == today:
        if total_minutes >= 60:
            duration = T['duration_hm'].format(h=total_minutes // 60, m=total_minutes % 60)
        else:
            duration = T['duration_m'].format(m=total_minutes)
        return T['resets_in'].format(duration=duration, clock=clock)
    if local_reset.date() == today + timedelta(days=1):
        return T['resets_tomorrow'].format(clock=clock)
    return T['resets_weekday'].format(day=T['weekdays'][local_reset.weekday()], clock=clock)


def format_credits(cents: float) -> str:
    amount = cents / 100.0
    try:
        rendered = _locale.currency(amount, grouping=True)
    except (ValueError, _locale.Error):
        return f'{CURRENCY_SYMBOL}\u00a0{amount:.2f}' if CURRENCY_SYMBOL else f'{amount:.2f}'
    if CURRENCY_SYMBOL != _SYSTEM_CURRENCY_SYMBOL and _SYSTEM_CURRENCY_SYMBOL:
        rendered = rendered.replace(_SYSTEM_CURRENCY_SYMBOL, CURRENCY_SYMBOL)
    return rendered


def _format_provider_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in _settings.TOOLTIP_FIELDS:
        item = data.get(key)
        if not isinstance(item, dict) or item.get('utilization') is None:
            continue
        pct = f"{item.get('utilization', 0):.0f}%"
        line = f'{tooltip_label(key)}: {pct}'
        reset = time_until(item.get('resets_at', ''))
        if reset:
            line += f' ({reset})'
        burn = format_burn_text(item.get('utilization', 0), item.get('resets_at', ''), field_period(key))
        if burn:
            line += f' - {burn}'
        lines.append(line)
    return lines


def _format_compact_line(name: str, data: dict[str, Any]) -> str:
    """One-line 'Label 5h 34% 7d 80%' summary, without reset time or burn rate.

    Used when the verbose per-field form (with reset time and burn rate)
    doesn't fit every active provider within the tray tooltip's 128-char
    limit, so providers are compacted together instead of the ones listed
    last being silently dropped.
    """
    label = PROVIDER_LABELS.get(name, name.title())
    parts = []
    for key in _settings.TOOLTIP_FIELDS:
        item = data.get(key)
        if not isinstance(item, dict) or item.get('utilization') is None:
            continue
        parts.append(f"{tooltip_label(key)} {item.get('utilization', 0):.0f}%")
    return f'{label} {" ".join(parts)}' if parts else label


# Verbose tooltip heading per secondary provider, shown when it gets its own
# section.  Not currently translated (no ``tooltip_title_<name>`` locale key
# exists yet); the fallback covers any future provider too.
def _secondary_heading(name: str) -> str:
    fallback = f'{PROVIDER_LABELS.get(name, name.title())} Usage'
    return T.get(f'tooltip_title_{name}', fallback)


def format_tooltip(data: dict[str, Any], secondary: list[tuple[str, dict[str, Any]]] | None = None) -> str:
    """Render tooltip text within the Windows tray's 128-character limit.

    Parameters
    ----------
    data
        Claude usage data, always rendered first and without a heading.
    secondary
        Additional providers as ``(provider_name, usage_data)`` pairs (e.g.
        ``('codex', {...})``), rendered in order below the Claude section.

    When the verbose per-field lines (reset time, burn rate) for every
    active provider fit within the limit, they are shown in full - this is
    the common case with the default one or two tooltip fields.  When they
    don't (more providers, more configured fields), every provider is
    compacted onto a single summary line instead of the providers listed
    last being silently dropped.
    """
    others = secondary or []
    healthy = [(name, entry) for name, entry in others if entry and 'error' not in entry]
    if 'error' in data and not healthy:
        if data.get('auth_error'):
            return f"{T['auth_expired_label']}\n{T['auth_expired_short']}"
        error = str(data.get('error', ''))
        if data.get('server_message'):
            error = f"{error} {data['server_message']}"
        return f"{T['error_label']}\n{error[:80]}"

    sections: list[tuple[str | None, dict[str, Any]]] = []
    if 'error' not in data:
        sections.append((None, data))
    sections.extend(healthy)

    verbose_lines = [T['tooltip_title']]
    for name, entry in sections:
        if name:
            verbose_lines.append(_secondary_heading(name))
        verbose_lines.extend(_format_provider_lines(entry))
    text = '\n'.join(verbose_lines)
    if len(text) <= 128 or len(sections) <= 1:
        return _trim_to_line_boundary(text)

    compact_lines = [T['tooltip_title']]
    compact_lines.extend(_format_compact_line(name or 'claude', entry) for name, entry in sections)
    return _trim_to_line_boundary('\n'.join(compact_lines))


def _trim_to_line_boundary(text: str) -> str:
    """Drop whole trailing lines until `text` fits the 128-char tooltip limit."""
    while len(text) > 128 and '\n' in text:
        text = text.rsplit('\n', 1)[0]
    return text[:128]
