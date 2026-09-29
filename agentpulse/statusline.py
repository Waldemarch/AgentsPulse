"""
Claude Code Statusline
======================

The one-line usage summary the dashboard serves at ``/api/statusline`` for the
Claude Code status line, for example
``Claude 5h 42% ↺14:30 · 7d 61% | Codex 5h 10% ↺16:05 · 7d 3%``.

Every provider shows the tray tooltip's quota fields.  Session windows name
the local time of their reset; a quota that is tight or heading for its limit
adds its forecast status (a session's limit time with the band it most likely
falls in), and a quota at its limit also names when it resets.
Pure formatting: the caller passes the usage data and the quota outlooks the
app already holds, so a status line refresh never reaches a provider's API.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .formatting import format_clock, format_outlook, parse_field_name, tooltip_label
from .i18n import T
from .settings import PROVIDER_LABELS

if TYPE_CHECKING:
    from .forecast import Outlook

__all__ = ['format_statusline']

# ANSI SGR escape sequences; the Claude Code status line renders them as colours.
_ANSI_YELLOW = '\x1b[33m'
_ANSI_RED = '\x1b[31m'
_ANSI_RESET = '\x1b[0m'
_STATUS_COLORS = {'tight': _ANSI_YELLOW, 'limit': _ANSI_RED, 'blocked': _ANSI_RED}
_FIELD_SEPARATOR = ' · '
_PROVIDER_SEPARATOR = ' | '
_RESET_MARK = '↺'


def format_statusline(
    sections: Sequence[tuple[str, dict[str, Any]]],
    outlooks: dict[str, dict[str, Outlook]],
    *,
    fields: Sequence[str],
    now: float,
    color: bool = True,
) -> str:
    """Return the status line text for the given providers.

    Parameters
    ----------
    sections
        ``(provider, usage)`` pairs in display order, with usage as the
        provider's latest API response: quota fields, or an ``error`` entry.
        An empty usage dict means no reading has arrived yet.
    outlooks
        Quota outlooks per provider and field (see :mod:`agentpulse.forecast`).
    fields
        Quota fields to show, in order (the tray tooltip's fields).
    now
        Current Unix time, for naming reset and limit times relative to today.
    color
        Colour tight quotas yellow and limits red with ANSI escape sequences.

    Returns
    -------
    str
        One line without a trailing newline.  Providers with an error are
        left out; when none has data, a loading or error text takes their
        place, and the line is empty when ``sections`` is.
    """
    provider_parts = []
    waiting = False
    claude_auth_error = False
    failed = False
    for provider, usage in sections:
        if not usage:
            waiting = True
            continue
        if 'error' in usage:
            failed = True
            claude_auth_error = claude_auth_error or (provider == 'claude' and bool(usage.get('auth_error')))
            continue
        quotas = _quota_parts(usage, outlooks.get(provider, {}), fields, now, color)
        if quotas:
            provider_parts.append(f'{PROVIDER_LABELS.get(provider, provider.title())} {_FIELD_SEPARATOR.join(quotas)}')

    if provider_parts:
        return _PROVIDER_SEPARATOR.join(provider_parts)
    if claude_auth_error:
        return T['auth_expired_label']
    if failed:
        return T['error_label']
    if waiting:
        return T['loading']
    return ''


def _quota_parts(usage: dict[str, Any], outlooks: dict[str, Outlook], fields: Sequence[str], now: float, color: bool) -> list[str]:
    """Return one ``'5h 42% ↺14:30'`` text per shown quota field that has a value."""
    parts = []
    for field in fields:
        entry = usage.get(field)
        if not isinstance(entry, dict) or entry.get('utilization') is None:
            continue
        text = f"{tooltip_label(field)} {entry['utilization']:.0f}%"
        outlook = outlooks.get(field)
        if outlook is None:
            parts.append(text)
            continue

        parsed = parse_field_name(field)
        is_session = parsed is not None and parsed[1] == 'hour'
        if is_session:
            # A session resets within hours, so the local time alone is unambiguous.
            text += f" {_RESET_MARK}{datetime.fromtimestamp(outlook.reset_at).strftime('%H:%M')}"
        elif outlook.status == 'blocked':
            text += f' {_RESET_MARK}{format_clock(outlook.reset_at, now=now)}'
        if outlook.status != 'ok':
            text += f' {format_outlook(outlook, now=now, band=True)}'
            if color:
                text = f"{_STATUS_COLORS.get(outlook.status, '')}{text}{_ANSI_RESET}"
        parts.append(text)
    return parts
