"""
Typical Week
============

This cycle of a multi-day quota window against the account's past cycles, for
the dashboard: every past cycle's readings by how far into its window they
came, the typical cycle (their median at every hour of the window), and the
forecast from now that follows the past cycles, with the lightest and the
busiest of them as its range.

The window is each provider's longest base quota window (its weekly limit),
as for the dashboard's consumption bars.  Everything comes from the usage
history; nothing is fetched.
"""
from __future__ import annotations

import bisect
import statistics
from collections.abc import Sequence
from typing import Any, NamedTuple

from .forecast import QuotaCycle, Sample, quota_cycles
from .formatting import field_period, parse_field_name, popup_label
from .usage_stats import consumption_field

__all__ = ['typical_week', 'typical_weeks']

# The chart's resolution: one point per hour of the window.
_STEP = 3600


class _Readings(NamedTuple):
    """One cycle's readings as seconds into its window and the highest utilization reached by each."""

    ages: list[float]
    highs: list[float]


def typical_weeks(series: dict[str, dict[str, Sequence[Sample]]], *, now: float) -> list[dict[str, Any]]:
    """Return the typical-week data of every provider whose longest base window spans days.

    Parameters
    ----------
    series
        History readings per provider and field, oldest first.
    now
        Current time as a Unix timestamp.

    Returns
    -------
    list
        One :func:`typical_week` dict per provider, with its ``id`` added.
    """
    weeks = []
    for provider, fields in series.items():
        field = consumption_field(fields)
        if field is None:
            continue
        week = typical_week(field, fields[field], now=now)
        if week is not None:
            weeks.append({'id': provider, **week})
    return weeks


def typical_week(field: str, samples: Sequence[Sample], *, now: float) -> dict[str, Any] | None:
    """Return the current cycle of one multi-day window against its past cycles.

    Points are ``[age, utilization]`` pairs, with the age in seconds since the
    start of their own cycle, so every cycle shares one axis.  Each cycle keeps
    its highest reading per hour.

    Parameters
    ----------
    field
        Quota field name; its window length comes from the name.
    samples
        History readings of this field, oldest first.
    now
        Current time as a Unix timestamp.

    Returns
    -------
    dict or None
        None when the window is not measured in days or none is running now.
        Otherwise ``field``, ``label``, ``period_seconds``, ``start`` (when the
        current cycle began), ``age`` (how far it has run), ``utilization``
        (its latest reading), ``current`` and ``past`` (the past cycles,
        oldest first) as points, ``typical`` (the median of the past cycles
        per hour of the window, wherever one was observed) and ``forecast``:
        ``likely``, ``low`` and ``high`` points from now to the reset - today's
        usage plus the median, the smallest and the largest growth the past
        cycles showed after the same point - empty without a past cycle
        observed by then.
    """
    parsed = parse_field_name(field)
    period = field_period(field)
    if parsed is None or parsed[1] != 'day' or not period:
        return None
    cycles = quota_cycles(sample for sample in samples if sample.ts <= now)
    if not cycles or cycles[-1].reset <= now:
        return None
    current = cycles[-1]
    start = current.reset - period
    age = now - start
    if age <= 0:
        return None

    past = [cycle for cycle in cycles[:-1] if cycle.reset <= now]
    readings = [_readings(cycle, period) for cycle in past]
    utilization = current.samples[-1].utilization
    return {
        'field': field,
        'label': popup_label(field),
        'period_seconds': period,
        'start': start,
        'age': age,
        'utilization': utilization,
        'current': _hourly(current, period),
        'past': [_hourly(cycle, period) for cycle in past],
        'typical': _typical(readings, period),
        'forecast': _forecast(readings, period, age, utilization),
    }


def _hourly(cycle: QuotaCycle, period: int) -> list[list[float]]:
    """The highest reading of each hour of the window, as ``[age, utilization]`` points."""
    start = cycle.reset - period
    kept: dict[int, tuple[float, float]] = {}
    for sample in cycle.samples:
        age = sample.ts - start
        if not 0 <= age <= period:
            continue
        hour = int(age // _STEP)
        best = kept.get(hour)
        if best is None or sample.utilization >= best[1]:
            kept[hour] = (age, sample.utilization)
    return [_point(*kept[hour]) for hour in sorted(kept)]


def _typical(readings: Sequence[_Readings], period: int) -> list[list[float]]:
    """The median of what the past cycles had reached at every hour of the window, over the cycles observed by then."""
    points = []
    for age in range(0, period + 1, _STEP):
        values = []
        for cycle in readings:
            reached = _reached(cycle, age)
            if reached is not None:
                values.append(reached)
        if values:
            points.append(_point(age, statistics.median(values)))
    return points


def _forecast(readings: Sequence[_Readings], period: int, age: float, utilization: float) -> dict[str, list[list[float]]]:
    """Today's usage plus the median, smallest and largest growth of the past cycles after ``age``, up to the reset."""
    forecast: dict[str, list[list[float]]] = {'likely': [], 'low': [], 'high': []}
    bases = []
    for cycle in readings:
        reached = _reached(cycle, age)
        if reached is not None:
            bases.append((cycle, reached))
    if not bases:
        return forecast

    ages = [age, *range(int(age // _STEP + 1) * _STEP, period + 1, _STEP)]
    for moment in ages:
        growth = []
        for cycle, base in bases:
            reached = _reached(cycle, moment)
            growth.append(0.0 if reached is None else reached - base)
        forecast['likely'].append(_point(moment, min(100.0, utilization + statistics.median(growth))))
        forecast['low'].append(_point(moment, min(100.0, utilization + min(growth))))
        forecast['high'].append(_point(moment, min(100.0, utilization + max(growth))))
    return forecast


def _readings(cycle: QuotaCycle, period: int) -> _Readings:
    start = cycle.reset - period
    ages: list[float] = []
    highs: list[float] = []
    for sample in cycle.samples:
        ages.append(sample.ts - start)
        highs.append(max(sample.utilization, highs[-1]) if highs else sample.utilization)
    return _Readings(ages, highs)


def _reached(cycle: _Readings, age: float) -> float | None:
    """The highest utilization a cycle had reached ``age`` seconds into its window, or None before its first reading."""
    index = bisect.bisect_right(cycle.ages, age)
    return cycle.highs[index - 1] if index else None


def _point(age: float, utilization: float) -> list[float]:
    return [round(age), round(utilization, 1)]
