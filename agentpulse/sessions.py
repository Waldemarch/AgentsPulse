"""
Session Timeline
================

The session windows of the last days, rebuilt from the usage history, and the
time spent at a limit: when each window reached 100% and how long it stayed
there until its reset.  A planner reads from the same history when the first
session of a workday usually starts, how often it runs out and how long before
its reset, and names the earlier start that would bring the reset to about
when it usually runs out.  The time a session runs out depends on the work
done since starting to work, not on when its window began, so an earlier start
only moves the reset.

A session window is a provider's shortest base quota window measured in hours
(its five-hour limit).  Everything comes from the usage history; nothing is
fetched.
"""
from __future__ import annotations

import statistics
from collections.abc import Collection, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from .forecast import Sample, quota_cycles
from .formatting import field_period, parse_field_name, popup_label

__all__ = ['SessionWindow', 'session_field', 'session_timeline', 'session_windows']

_HOUR = 3600
_DAY = 24 * _HOUR
_TIMELINE_DAYS = 7
_STATS_DAYS = 30
# A day's first session starts after this local time; earlier windows belong to the night before.
_DAY_STARTS = 4 * _HOUR
# The planner names a usual start only after this many workdays with a first session.
_MIN_DAYS = 3
# Moving the start by less than this is not worth a suggestion.
_MIN_LEAD = 15 * 60
# Usual times are rounded to this, since they are habits rather than moments.
_STEP = 5 * 60


@dataclass(frozen=True)
class SessionWindow:
    """One session window of a provider.

    Attributes
    ----------
    start, end
        Unix times the window began and reset.
    peak
        Highest utilization read in the window, in percent.
    blocked_at
        Unix time of the first reading at the limit, or None when the window never reached it.
    """

    start: float
    end: float
    peak: float
    blocked_at: float | None


def session_timeline(
    series: dict[str, dict[str, Sequence[Sample]]],
    *,
    now: float,
    workdays: Collection[int] = (),
    tz: tzinfo | None = None,
) -> dict[str, Any]:
    """Return every provider's session windows of the last week, its time at the limit and its planner.

    Parameters
    ----------
    series
        History readings per provider and field, oldest first.
    now
        Current time as a Unix timestamp.
    workdays
        Weekdays the planner looks at, Monday = 0; every day when empty.
    tz
        Time zone of the user's days; the local one by default.

    Returns
    -------
    dict
        ``days`` and ``from`` (start of the local day the timeline begins
        with) and ``providers``: ``id``, ``field``, ``label``,
        ``period_seconds``, ``windows`` (of the timeline, see
        :class:`SessionWindow`), ``blocked`` and ``blocked_week`` (``count``
        and ``seconds`` at the limit over the last 30 and 7 days) and
        ``planner`` (see :func:`_planner`).
    """
    timeline_from = _day_start(_local(now, tz).date() - timedelta(days=_TIMELINE_DAYS - 1), tz)
    providers = []
    for provider, fields in series.items():
        field = session_field(fields)
        period = field_period(field) if field is not None else None
        if field is None or not period:
            continue
        windows = session_windows(fields[field], period, since=now - _STATS_DAYS * _DAY, now=now)
        providers.append({
            'id': provider,
            'field': field,
            'label': popup_label(field),
            'period_seconds': period,
            'windows': [asdict(window) for window in windows if window.end > timeline_from],
            'blocked': _blocked(windows, since=now - _STATS_DAYS * _DAY, now=now),
            'blocked_week': _blocked(windows, since=now - _TIMELINE_DAYS * _DAY, now=now),
            'planner': _planner(windows, period=period, workdays=workdays, now=now, tz=tz),
        })
    return {'days': _TIMELINE_DAYS, 'from': timeline_from, 'providers': providers}


def session_field(fields: Iterable[str]) -> str | None:
    """Return the shortest base quota window measured in hours among ``fields``, or None."""
    best = None
    best_period = 0
    for name in sorted(fields):
        parsed = parse_field_name(name)
        period = field_period(name)
        if parsed is None or parsed[1] != 'hour' or parsed[2] is not None or not period:
            continue
        if best is None or period < best_period:
            best, best_period = name, period
    return best


def session_windows(samples: Sequence[Sample], period: int, *, since: float, now: float) -> list[SessionWindow]:
    """Return the session windows that overlap ``since`` to ``now``, oldest first.

    A window runs from its reset minus ``period`` to its reset.  One whose
    readings all show 0% held no session and is left out.
    """
    windows = []
    for cycle in quota_cycles(sample for sample in samples if sample.ts <= now):
        start = cycle.reset - period
        if cycle.reset <= since or start >= now:
            continue
        peak = max(sample.utilization for sample in cycle.samples)
        if peak <= 0:
            continue
        blocked_at = None
        for sample in cycle.samples:
            if sample.utilization >= 100:
                blocked_at = sample.ts
                break
        windows.append(SessionWindow(start, cycle.reset, peak, blocked_at))
    return windows


def _blocked(windows: Iterable[SessionWindow], *, since: float, now: float) -> dict[str, float]:
    """How often windows reached their limit since ``since``, and for how long in all until their resets."""
    count = 0
    seconds = 0.0
    for window in windows:
        if window.blocked_at is None or window.blocked_at < since:
            continue
        count += 1
        seconds += max(0.0, min(window.end, now) - window.blocked_at)
    return {'count': count, 'seconds': seconds}


def _planner(
    windows: Sequence[SessionWindow],
    *,
    period: int,
    workdays: Collection[int],
    now: float,
    tz: tzinfo | None,
) -> dict[str, Any] | None:
    """When the first session of a workday usually starts, how often it runs out, and the start that would avoid it.

    A workday's first session is its first window that began after 4:00 and
    has reset; times are seconds after local midnight, rounded to five
    minutes.

    Returns
    -------
    dict or None
        None before ``_MIN_DAYS`` workdays with a first session.  Otherwise
        ``start`` and ``reset`` (the usual first session's), ``days`` (the
        workdays compared), ``blocked`` (how many of their first sessions
        reached the limit), ``lead`` (the median time those spent at the
        limit before their reset, or None) and ``suggested`` (the start that
        would move the reset back by ``lead``, or None when that is less than
        a quarter of an hour or before 4:00).
    """
    firsts: dict[date, SessionWindow] = {}
    for window in windows:
        if window.end > now:
            continue
        local = _local(window.start, tz)
        if _time_of_day(local) < _DAY_STARTS or (workdays and local.weekday() not in workdays):
            continue
        firsts.setdefault(local.date(), window)
    if len(firsts) < _MIN_DAYS:
        return None

    start = _rounded(statistics.median(_time_of_day(_local(window.start, tz)) for window in firsts.values()))
    leads = [window.end - window.blocked_at for window in firsts.values() if window.blocked_at is not None]
    lead = _rounded(statistics.median(leads)) if leads else None
    suggested = None
    if lead is not None and lead >= _MIN_LEAD and start - lead >= _DAY_STARTS:
        suggested = start - lead
    return {'start': start, 'reset': start + period, 'days': len(firsts), 'blocked': len(leads), 'lead': lead, 'suggested': suggested}


def _time_of_day(moment: datetime) -> float:
    return moment.hour * _HOUR + moment.minute * 60 + moment.second


def _rounded(seconds: float) -> int:
    return round(seconds / _STEP) * _STEP


def _local(ts: float, tz: tzinfo | None) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz is not None else datetime.fromtimestamp(ts)


def _day_start(day: date, tz: tzinfo | None) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()
