"""
Usage Statistics
================

Quota consumption per hour or day and a weekday-by-hour heatmap, computed from
quota history for the dashboard.

Consumption is measured on each provider's longest base quota window (its
weekly limit, for example), so work that counts against several quotas at
once is counted once.  Within one quota window it is the growth between
consecutive readings; the first reading of a new window counts in full,
because that window started empty.  The first reading of a series only sets
the baseline, since it is unknown when that usage happened.
"""
from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, tzinfo

from .forecast import CYCLE_RESET_TOLERANCE, Sample
from .formatting import field_period, parse_field_name

__all__ = ['HEATMAP_DAYS', 'bucket_starts', 'consumption_buckets', 'consumption_field', 'heatmap_cells', 'usage_increments']

HEATMAP_DAYS = 28
_HOUR = 3600


def consumption_field(fields: Iterable[str]) -> str | None:
    """Return the longest base quota window among ``fields``, or None.

    Model-specific variants (``seven_day_sonnet``) are skipped, because their
    usage is already part of the base window's.
    """
    best = None
    best_period = 0
    for name in sorted(fields):
        parsed = parse_field_name(name)
        period = field_period(name)
        if parsed is None or parsed[2] is not None or not period:
            continue
        if period > best_period:
            best, best_period = name, period
    return best


def usage_increments(samples: Sequence[Sample]) -> list[tuple[float, float]]:
    """Return ``(time, points consumed since the previous reading)`` for every reading after the first."""
    increments: list[tuple[float, float]] = []
    previous: Sample | None = None
    for sample in samples:
        if previous is not None:
            increments.append((sample.ts, _increment(previous, sample)))
        previous = sample
    return increments


def bucket_starts(unit: str, count: int, *, now: float, tz: tzinfo | None = None) -> list[float]:
    """Return the start times of the last ``count`` local hours or days, oldest first, ending with the current one.

    Parameters
    ----------
    unit
        ``'hour'`` or ``'day'``.
    count
        Number of buckets.
    now
        Current time as a Unix timestamp.
    tz
        Time zone for the bucket boundaries; the system's local time by default.
    """
    assert unit in {'hour', 'day'}, unit
    assert count > 0, count
    local = _local(now, tz)
    if unit == 'hour':
        # Local hours start an hour apart in absolute time, across DST changes too.
        current = local.replace(minute=0, second=0, microsecond=0).timestamp()
        return [current - (count - 1 - index) * _HOUR for index in range(count)]
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return [(midnight - timedelta(days=count - 1 - index)).timestamp() for index in range(count)]


def consumption_buckets(samples: Sequence[Sample], *, unit: str, count: int, now: float, tz: tzinfo | None = None) -> list[float]:
    """Return the points consumed in each of the last ``count`` local hours or days, oldest first.

    The buckets are those of :func:`bucket_starts`; usage after ``now`` is ignored.
    """
    starts = bucket_starts(unit, count, now=now, tz=tz)
    totals = [0.0] * count
    for ts, amount in usage_increments(samples):
        if amount <= 0 or ts < starts[0] or ts > now:
            continue
        totals[bisect.bisect_right(starts, ts) - 1] += amount
    return totals


def heatmap_cells(samples: Sequence[Sample], *, now: float, days: int = HEATMAP_DAYS, tz: tzinfo | None = None) -> list[list[float]]:
    """Return the average points consumed per local weekday and hour over the last ``days`` days.

    Each of the seven rows (Monday first) holds 24 hourly averages.  A slot is
    averaged over the times it occurred since the series' first reading within
    the period, so a short history is not diluted by weeks it never saw.
    """
    cells = [[0.0] * 24 for _ in range(7)]
    if not samples:
        return cells
    first = max(now - days * 24 * _HOUR, samples[0].ts)
    for ts, amount in usage_increments(samples):
        if amount <= 0 or ts <= first or ts > now:
            continue
        local = _local(ts, tz)
        cells[local.weekday()][local.hour] += amount

    occurrences = _slot_occurrences(first, now, tz)
    for weekday in range(7):
        for hour in range(24):
            seen = occurrences[weekday][hour]
            cells[weekday][hour] = cells[weekday][hour] / seen if seen else 0.0
    return cells


def _increment(previous: Sample, sample: Sample) -> float:
    if _same_window(previous, sample):
        return max(0.0, sample.utilization - previous.utilization)
    if sample.reset is None:
        return 0.0
    # The reset moved while the old window was still running: it was rescheduled, not renewed.
    if previous.reset is not None and previous.reset > sample.ts + CYCLE_RESET_TOLERANCE:
        return max(0.0, sample.utilization - previous.utilization)
    # A new window started empty, so its first reading is all new usage.
    return max(0.0, sample.utilization)


def _same_window(previous: Sample, sample: Sample) -> bool:
    if previous.reset is None or sample.reset is None:
        return previous.reset is None and sample.reset is None
    return abs(previous.reset - sample.reset) <= CYCLE_RESET_TOLERANCE


def _local(ts: float, tz: tzinfo | None) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz is not None else datetime.fromtimestamp(ts)


def _slot_occurrences(first: float, now: float, tz: tzinfo | None) -> list[list[int]]:
    """Count how often each local weekday-and-hour slot occurred between ``first`` and ``now``."""
    counts = [[0] * 24 for _ in range(7)]
    hour = _local(first, tz).replace(minute=0, second=0, microsecond=0).timestamp()
    while hour <= now:
        local = _local(hour, tz)
        counts[local.weekday()][local.hour] += 1
        hour += _HOUR
    return counts
