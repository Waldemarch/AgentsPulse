"""
Quota Forecast
==============

Status and reset-time forecast of quota windows, shared by the tray icon,
tooltip, popup and dashboard.

A window's forecast is the utilization it is projected to reach at its reset.
Windows measured in hours (sessions) are projected from their pace - a blend of
the last half hour and the window's average - because a session keeps its
momentum.  Windows measured in days (weekly limits) follow the account's own
rhythm: the median usage that past cycles added after the same point of their
window, when history holds such cycles, otherwise the average pace over at
least one full day, so a single working session is not extrapolated across
nights and weekends.

A forecast also carries a band, so a limit time comes with the range it will
most likely fall in.  A session's band is the same projection at a slow pace
(the lower of the last half hour and the window's average) and at a fast one
(the quickest quarter-hour of the window).  A projection from past cycles spans
the lightest and the busiest of them, and its limit time is the median of when
they would have reached the limit from today's usage.
"""
from __future__ import annotations

import bisect
import dataclasses
import functools
import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from .formatting import field_period, parse_field_name

__all__ = [
    'CYCLE_RESET_TOLERANCE', 'STATUS_SEVERITY',
    'Outlook', 'QuotaCycle', 'Sample',
    'blocked_until', 'next_local_time', 'quota_cycles', 'quota_outlook', 'reset_timestamp', 'usage_outlooks', 'worst_outlook',
]

# Samples whose reset times differ by less than this belong to one quota cycle:
# the APIs repeat the same reset moment with jitter of up to a few seconds on
# every poll, while a real reset moves it by hours or days.
CYCLE_RESET_TOLERANCE = 10 * 60
STATUS_SEVERITY = {'ok': 0, 'tight': 1, 'limit': 2, 'blocked': 3}
# A window projected to end at or above this share of its limit is tight.
_TIGHT_PCT = 90.0
# A session's average pace covers at least its first ten minutes, so the first
# reading of a window is not extrapolated from a few seconds of usage.
_MIN_SESSION_SECONDS = 10 * 60
# Recent session pace: the change since the oldest reading of the last half
# hour, once the readings span at least ten minutes.
_RECENT_SECONDS = 30 * 60
_RECENT_MIN_SECONDS = 10 * 60
_RECENT_WEIGHT = 0.6
# The fast end of a session's band: the quickest pace between two readings at
# least this far apart.
_QUARTER_HOUR = 15 * 60
# Multi-day windows without comparable history average their pace over at least a day.
_MIN_MULTI_DAY_SECONDS = 24 * 3600


class Sample(NamedTuple):
    """One reading of a quota series: Unix time, utilization in percent, reset as Unix time."""

    ts: float
    utilization: float
    reset: float | None


@dataclass
class QuotaCycle:
    """One quota window of a series: its reset time and readings, oldest first."""

    reset: float
    samples: list[Sample]


@dataclass(frozen=True)
class Outlook:
    """Status and forecast of one quota window.

    Attributes
    ----------
    status
        ``'ok'``, ``'tight'`` (projected to end within ten points of the limit),
        ``'limit'`` (projected to run out before the reset) or ``'blocked'``
        (the limit is reached).
    forecast_pct
        Projected utilization at the reset, at most 100, or None without a
        forecast.
    limit_at
        Unix time at which the limit is projected to be reached, when that is
        before the reset: at the projected pace, or the median of when past
        cycles would have reached it; otherwise None.
    reset_at
        Unix time of the reset.
    elapsed_pct
        Share of the window that has passed, 0-100.
    method
        ``'pace'`` (sessions), ``'history'`` or ``'average'`` (multi-day
        windows), ``'reached'`` for a blocked window and ``'none'`` when
        forecasts are turned off.
    cycles
        Number of past cycles behind a ``'history'`` projection.
    day_end_pct
        Projected utilization at the end of the user's day, when that comes
        before the reset.
    forecast_low_pct, forecast_high_pct
        Band of the projection at the reset, at most 100.  A ``'pace'``
        projection spans the slow pace (the lower of the last half hour and
        the window's average) and the fast one (the quickest quarter-hour of
        the window); a ``'history'`` projection the lightest and the busiest
        past cycle.  None for other projections.
    limit_earliest, limit_latest
        Unix times at which the busy and the light end of the band reach the
        limit; None when that end lasts until the reset, and without a band.
    """

    status: str
    forecast_pct: float | None
    limit_at: float | None
    reset_at: float
    elapsed_pct: float
    method: str
    cycles: int = 0
    day_end_pct: float | None = None
    forecast_low_pct: float | None = None
    forecast_high_pct: float | None = None
    limit_earliest: float | None = None
    limit_latest: float | None = None


class _Paces(NamedTuple):
    """Paces of a session window in utilization points per second."""

    slow: float
    likely: float
    fast: float


class _PastGrowth(NamedTuple):
    """What each past cycle added after the current point of its window.

    ``limit_ages`` holds, per cycle, the seconds into the window at which it
    would have reached the limit from today's usage, or None.
    """

    to_reset: list[float]
    to_day_end: list[float]
    limit_ages: list[float | None]


def usage_outlooks(
    usage: dict[str, Any],
    series: dict[str, Sequence[Sample]],
    *,
    now: float,
    day_end: float | None = None,
    forecast: bool = True,
) -> dict[str, Outlook]:
    """Return the outlook of every quota field in one provider's usage response.

    Parameters
    ----------
    usage
        The provider's usage response; quota fields are the entries with
        ``utilization`` and ``resets_at``.
    series
        The provider's history readings per field, oldest first.
    now
        Current time as a Unix timestamp.
    day_end
        Optional Unix time of the user's end of day, see :func:`quota_outlook`.
    forecast
        False reports only whether each limit is reached, without projections.

    Returns
    -------
    dict
        Outlook per field; fields without a known window or reset are absent.
    """
    outlooks: dict[str, Outlook] = {}
    for key, value in usage.items():
        if key == 'extra_usage' or not isinstance(value, dict) or value.get('utilization') is None or 'resets_at' not in value:
            continue
        outlook = quota_outlook(
            key, float(value['utilization']), value.get('resets_at') or '', series.get(key, ()),
            now=now, day_end=day_end, forecast=forecast,
        )
        if outlook is not None:
            outlooks[key] = outlook
    return outlooks


def quota_outlook(
    field: str,
    utilization: float,
    resets_at: str,
    samples: Sequence[Sample],
    *,
    now: float,
    day_end: float | None = None,
    forecast: bool = True,
) -> Outlook | None:
    """Project one quota window to its reset.

    Parameters
    ----------
    field
        Quota field name; its window length and unit come from the name.
    utilization
        Current usage in percent.
    resets_at
        ISO timestamp of the window's reset.
    samples
        History readings of this series, oldest first; may be empty.
    now
        Current time as a Unix timestamp.
    day_end
        Optional Unix time of the user's end of day; when it falls before the
        reset, the projection at that moment is reported as ``day_end_pct``.
    forecast
        False reports only whether the limit is reached, without projections.

    Returns
    -------
    Outlook or None
        None when the field has no known window length, the reset time is
        missing or has passed, or the window has not started yet.
    """
    period = field_period(field)
    reset = reset_timestamp(resets_at)
    if not period or reset is None or reset <= now:
        return None
    elapsed = now - (reset - period)
    if elapsed <= 0:
        return None
    elapsed_pct = min(100.0, elapsed / period * 100.0)
    if utilization >= 100:
        return Outlook('blocked', 100.0 if forecast else None, None, reset, elapsed_pct, 'reached')
    if not forecast:
        return Outlook('ok', None, None, reset, elapsed_pct, 'none')

    parsed = parse_field_name(field)
    multi_day = parsed is not None and parsed[1] == 'day'
    day_end_age = elapsed + (day_end - now) if day_end is not None and now < day_end < reset else None

    cycles = 0
    rate = None
    paces = None
    growth = _past_growth(samples, reset, period, elapsed, day_end_age, now, headroom=100.0 - utilization) if multi_day else None
    if growth is not None:
        method = 'history'
        cycles = len(growth.to_reset)
        at_reset = utilization + statistics.median(growth.to_reset)
        at_day_end = utilization + statistics.median(growth.to_day_end) if growth.to_day_end else None
    else:
        if multi_day:
            method = 'average'
            rate = utilization / max(elapsed, _MIN_MULTI_DAY_SECONDS)
        else:
            method = 'pace'
            paces = _session_paces(samples, utilization, reset, period, elapsed, now)
            rate = paces.likely
        at_reset = utilization + rate * (reset - now)
        at_day_end = None if day_end is None or day_end_age is None else utilization + rate * (day_end - now)

    limit_at = None
    if rate is not None and rate > 0 and at_reset >= 100:
        limit_at = now + (100.0 - utilization) / rate
    status = 'limit' if at_reset >= 100 else 'tight' if at_reset >= _TIGHT_PCT else 'ok'
    day_end_pct = None if at_day_end is None else min(100.0, at_day_end)
    outlook = Outlook(status, min(100.0, at_reset), limit_at, reset, elapsed_pct, method, cycles, day_end_pct)
    if paces is not None:
        return dataclasses.replace(
            outlook,
            forecast_low_pct=min(100.0, utilization + paces.slow * (reset - now)),
            forecast_high_pct=min(100.0, utilization + paces.fast * (reset - now)),
            limit_earliest=_limit_time(utilization, paces.fast, now, reset),
            limit_latest=_limit_time(utilization, paces.slow, now, reset),
        )
    if growth is not None:
        return _history_band(outlook, growth, utilization, reset - period)
    return outlook


def worst_outlook(outlooks: Iterable[Outlook]) -> Outlook | None:
    """Return the most urgent outlook, or None for none.

    The most severe status wins.  Among equally severe ones, a reached limit
    that lasts longest wins (it decides when the provider is usable again);
    otherwise the one whose limit or reset comes first.
    """
    worst: Outlook | None = None
    for outlook in outlooks:
        if worst is None or _urgency(outlook) > _urgency(worst):
            worst = outlook
    return worst


def blocked_until(usage: dict[str, Any], *, now: float) -> float | None:
    """Return the Unix time at which a provider can be used again, or None.

    Every quota at its limit has to reset before the provider is usable, so
    this is the latest reset among them.  None means no quota is at its limit,
    or one is at its limit without a future reset time to count down to.

    Parameters
    ----------
    usage
        The provider's usage response.
    now
        Current time as a Unix timestamp.
    """
    latest: float | None = None
    for key, value in usage.items():
        if key == 'extra_usage' or not isinstance(value, dict):
            continue
        utilization = value.get('utilization')
        if utilization is None or utilization < 100:
            continue
        reset = reset_timestamp(value.get('resets_at'))
        if reset is None:
            return None
        # Already past its reset: the next reading will show the fresh window.
        if reset <= now:
            continue
        latest = reset if latest is None else max(latest, reset)
    return latest


def next_local_time(hhmm: str, *, now: float) -> float | None:
    """Return the Unix time of the next local ``HH:MM`` after ``now``, or None for an invalid time."""
    hour_text, _sep, minute_text = hhmm.partition(':')
    if not (hour_text.isdigit() and minute_text.isdigit()):
        return None
    hour, minute = int(hour_text), int(minute_text)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    current = datetime.fromtimestamp(now)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target.timestamp() <= now:
        target += timedelta(days=1)
    return target.timestamp()


def quota_cycles(samples: Iterable[Sample]) -> list[QuotaCycle]:
    """Split the readings of one series into quota cycles, in time order.

    A new cycle starts when the reported reset time moves by more than
    ``CYCLE_RESET_TOLERANCE`` from the previous reading's.  Readings without a
    reset time belong to no cycle.  Each cycle keeps the latest reset time it
    reported.
    """
    cycles: list[QuotaCycle] = []
    previous: float | None = None
    for sample in sorted(samples, key=lambda item: item.ts):
        if sample.reset is None:
            continue
        if previous is None or abs(sample.reset - previous) > CYCLE_RESET_TOLERANCE:
            cycles.append(QuotaCycle(reset=sample.reset, samples=[]))
        cycles[-1].reset = sample.reset
        cycles[-1].samples.append(sample)
        previous = sample.reset
    return cycles


def reset_timestamp(value: object) -> float | None:
    """Parse an ISO ``resets_at`` value to a Unix timestamp; None when absent, invalid or timezone-naive."""
    if not isinstance(value, str) or not value:
        return None
    return _parse_reset(value)


# History repeats the same reset moment on every poll, so parsed values are cached.
@functools.lru_cache(maxsize=4096)
def _parse_reset(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def _urgency(outlook: Outlook) -> tuple[int, float]:
    if outlook.status == 'blocked':
        return STATUS_SEVERITY['blocked'], outlook.reset_at
    moment = outlook.limit_at if outlook.limit_at is not None else outlook.reset_at
    return STATUS_SEVERITY[outlook.status], -moment


def _same_window(sample: Sample, reset: float) -> bool:
    return sample.reset is not None and abs(sample.reset - reset) <= CYCLE_RESET_TOLERANCE


def _session_paces(samples: Sequence[Sample], utilization: float, reset: float, period: int, elapsed: float, now: float) -> _Paces:
    """Slow, likely and fast pace of a session window.

    The likely pace blends the last half hour with the window's average and
    the slow one is the lower of the two; without readings spanning the last
    ten minutes both are the average.  The fast pace is the quickest
    quarter-hour of the window, and at least the higher of the two.
    """
    average = utilization / max(elapsed, _MIN_SESSION_SECONDS)
    window = _window_readings(samples, reset, period, now)
    fastest = _fastest_quarter_hour(window, utilization, now)
    recent = _recent_pace(window, utilization, now)
    if recent is None:
        return _Paces(average, average, max(average, fastest))
    likely = _RECENT_WEIGHT * recent + (1 - _RECENT_WEIGHT) * average
    return _Paces(min(recent, average), likely, max(recent, average, fastest))


def _window_readings(samples: Sequence[Sample], reset: float, period: int, now: float) -> list[Sample]:
    """Readings of the window that resets at ``reset``, up to ``now``, oldest first."""
    first = bisect.bisect_left(samples, reset - period - CYCLE_RESET_TOLERANCE, key=lambda item: item.ts)
    window = []
    for sample in samples[first:]:
        if sample.ts > now:
            break
        if _same_window(sample, reset):
            window.append(sample)
    return window


def _recent_pace(window: Sequence[Sample], utilization: float, now: float) -> float | None:
    """Points per second since the oldest reading of the last half hour; None without one, or when it is under ten minutes old."""
    index = bisect.bisect_left(window, now - _RECENT_SECONDS, key=lambda item: item.ts)
    if index == len(window) or now - window[index].ts < _RECENT_MIN_SECONDS:
        return None
    oldest = window[index]
    return max(0.0, (utilization - oldest.utilization) / (now - oldest.ts))


def _fastest_quarter_hour(window: Sequence[Sample], utilization: float, now: float) -> float:
    """Highest pace, in points per second, between two readings at least a quarter-hour apart; 0 without such a pair.

    Every reading is compared with the latest one at least a quarter-hour
    before it, and the current utilization counts as a reading at ``now``.
    """
    readings = [*window, Sample(now, utilization, None)]
    fastest = 0.0
    earlier = 0
    for index, reading in enumerate(readings):
        while earlier + 1 < index and reading.ts - readings[earlier + 1].ts >= _QUARTER_HOUR:
            earlier += 1
        span = reading.ts - readings[earlier].ts
        if span >= _QUARTER_HOUR:
            fastest = max(fastest, (reading.utilization - readings[earlier].utilization) / span)
    return fastest


def _limit_time(utilization: float, rate: float, now: float, reset: float) -> float | None:
    """Unix time at which ``rate`` reaches the limit, or None when it lasts until the reset."""
    if rate <= 0:
        return None
    moment = now + (100.0 - utilization) / rate
    return moment if moment <= reset else None


def _past_growth(
    samples: Sequence[Sample],
    reset: float,
    period: int,
    age: float,
    day_end_age: float | None,
    now: float,
    *,
    headroom: float,
) -> _PastGrowth | None:
    """Growth that past complete cycles showed after the same point of their window.

    ``age`` is how far the current window has run.  For each past cycle, the
    growth to its reset is its highest reading minus what it had reached at
    ``age``; the growth to the end of the day is what it had reached at
    ``day_end_age`` minus the same; and its limit age is when it had added
    ``headroom`` points, the room today's usage leaves.  Cycles without a
    reading at or before ``age`` are skipped, because their start was not
    observed.

    Returns
    -------
    _PastGrowth or None
        One entry per compared cycle (none for the day's end without
        ``day_end_age``), or None without a comparable cycle.
    """
    growth = _PastGrowth([], [], [])
    for cycle in quota_cycles(samples):
        if cycle.reset > now or abs(cycle.reset - reset) <= CYCLE_RESET_TOLERANCE:
            continue
        start = cycle.reset - period
        reached = _reached(cycle.samples, start, age)
        if reached is None:
            continue
        final = max(sample.utilization for sample in cycle.samples)
        growth.to_reset.append(max(0.0, final - reached))
        growth.limit_ages.append(_limit_age(cycle.samples, start, age, reached + headroom))
        if day_end_age is not None:
            later = _reached(cycle.samples, start, day_end_age)
            growth.to_day_end.append(max(0.0, (reached if later is None else later) - reached))
    return growth if growth.to_reset else None


def _limit_age(samples: Sequence[Sample], start: float, age: float, limit: float) -> float | None:
    """Seconds into the window of the first reading after ``age`` at or above ``limit``, or None."""
    for sample in samples:
        if sample.ts - start > age and sample.utilization >= limit:
            return sample.ts - start
    return None


def _history_band(outlook: Outlook, growth: _PastGrowth, utilization: float, start: float) -> Outlook:
    """Add the band of past cycles to a projection from them.

    The band spans the lightest and the busiest cycle's growth.  The limit
    time is the median of when the cycles would have reached the limit, a
    cycle that would not have counting as after the reset; the band's ends
    are the first and the last of those times.
    """
    reached = sorted(age for age in growth.limit_ages if age is not None)
    ordered = reached + [math.inf] * (len(growth.limit_ages) - len(reached))
    likely = statistics.median(ordered)
    return dataclasses.replace(
        outlook,
        limit_at=start + likely if outlook.status == 'limit' and math.isfinite(likely) else None,
        forecast_low_pct=min(100.0, utilization + min(growth.to_reset)),
        forecast_high_pct=min(100.0, utilization + max(growth.to_reset)),
        limit_earliest=start + reached[0] if reached else None,
        limit_latest=start + reached[-1] if len(reached) == len(ordered) else None,
    )


def _reached(samples: Sequence[Sample], start: float, age: float) -> float | None:
    """Utilization of the last reading at most ``age`` seconds into the window, or None before the first."""
    reached = None
    for sample in samples:
        if sample.ts - start > age:
            break
        reached = sample.utilization
    return reached
