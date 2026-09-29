"""
Away Summary
============

The one notification that sums up an absence.  When the user comes back after
locking the workstation or leaving it idle, each provider's quotas are
compared with the readings from before they left: which windows reset and how
much usage the agents added in the background, for example
``Claude: 5h available again since 02:00, now 0%, 7d 88% → 90%``.  This one
summary takes the place of the reset and threshold alerts held back meanwhile.

Pure formatting: the caller passes both sets of readings and the times of the
absence.
"""
from __future__ import annotations

from typing import Any

from .forecast import reset_timestamp
from .formatting import field_sort_key, format_clock, format_duration, tooltip_label
from .i18n import T
from .settings import PROVIDER_LABELS

__all__ = ['away_summary']

# Windows shows at most 255 characters of a notification's text and 63 of its title.
_MESSAGE_LIMIT = 255
_TITLE_LIMIT = 63
# Usage that grew by less than one percentage point counts as unchanged.
_MIN_GROWTH = 1.0


def away_summary(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    *,
    started: float,
    ended: float,
) -> tuple[str, str] | None:
    """Return the notification that sums up an absence, or None when no quota changed.

    Parameters
    ----------
    before
        Each provider's usage response from when the user left, keyed by provider.
    after
        Each provider's usage response once they are back, in display order.
    started
        Unix time the user left.
    ended
        Unix time the user came back.

    Returns
    -------
    tuple of str or None
        ``(message, title)`` like the app's other notifications: one line per
        provider whose quotas reset or grew by at least a percentage point,
        with whole lines dropped from the end until Windows can show the text,
        and a title naming how long the user was away.
    """
    lines = []
    for provider, usage in after.items():
        changes = _quota_changes(before.get(provider) or {}, usage or {}, started, ended)
        if changes:
            label = PROVIDER_LABELS.get(provider, provider.title())
            lines.append(T['away_line'].format(provider=label, changes=', '.join(changes)))
    if not lines:
        return None

    title = T['away_title'].format(duration=format_duration(ended - started))
    return _fit(lines), title[:_TITLE_LIMIT]


def _quota_changes(before: dict[str, Any], after: dict[str, Any], started: float, ended: float) -> list[str]:
    """Describe every quota window of one provider that reset or grew during the absence, sessions first."""
    if not before or not after or 'error' in before or 'error' in after:
        return []
    changes = []
    for field in sorted(before.keys() & after.keys(), key=field_sort_key):
        old = before[field]
        new = after[field]
        if field == 'extra_usage' or not (_is_quota(old) or _is_quota(new)):
            continue
        label = tooltip_label(field)
        then = _utilization(old)
        now = _utilization(new)
        reset = reset_timestamp(old.get('resets_at') or '') if isinstance(old, dict) else None
        if reset is not None and started < reset <= ended:
            template = 'away_unblocked' if then >= 100 else 'away_renewed'
            changes.append(T[template].format(label=label, clock=format_clock(reset, now=ended), pct=f'{now:.0f}'))
        elif now - then >= _MIN_GROWTH:
            changes.append(T['away_used'].format(label=label, before=f'{then:.0f}', after=f'{now:.0f}'))
    return changes


def _is_quota(entry: Any) -> bool:
    return isinstance(entry, dict) and entry.get('utilization') is not None and 'resets_at' in entry


def _utilization(entry: Any) -> float:
    """Usage of a quota entry in percent; a window without a value (inactive or null) counts as 0."""
    return float(entry['utilization']) if _is_quota(entry) else 0.0


def _fit(lines: list[str]) -> str:
    """Join the lines, dropping whole lines from the end until Windows can show the text."""
    kept = list(lines)
    while len(kept) > 1 and len('\n'.join(kept)) > _MESSAGE_LIMIT:
        kept.pop()
    return '\n'.join(kept)[:_MESSAGE_LIMIT]
