"""
Daily Budget
============

How much of a weekly quota today may use so that the quota lasts until its
reset: what was left at the start of today, divided by today and the
workdays that remain before the reset.  Shown as, for example,
``Today: 12 of 27 pp``.

Today always counts, even when it is not one of the configured workdays,
because someone using the quota today is working today.  Only the
provider's longest base quota window (its weekly limit) gets a budget.
"""
from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from .forecast import CYCLE_RESET_TOLERANCE, Sample, reset_timestamp
from .formatting import field_period, parse_field_name
from .usage_stats import consumption_field

__all__ = ['DailyBudget', 'daily_budget', 'usage_budgets']

# A window shorter than two days has no days to spread its quota over.
_MIN_PERIOD = 2 * 24 * 3600


@dataclass(frozen=True)
class DailyBudget:
    """Today's share of a weekly quota.

    Attributes
    ----------
    used
        Percentage points the quota grew since the start of today.
    allowance
        Percentage points today may use so the quota lasts until its reset.
    days
        Days the rest of the quota is spread over: today and the remaining
        workdays before the reset.
    """

    used: float
    allowance: float
    days: int


def usage_budgets(
    usage: dict[str, Any],
    series: dict[str, Sequence[Sample]],
    *,
    now: float,
    workdays: Collection[int],
    tz: tzinfo | None = None,
) -> dict[str, DailyBudget]:
    """Return today's budget of a provider's longest base quota window, keyed by its field.

    Parameters
    ----------
    usage
        The provider's usage response; quota fields are the entries with
        ``utilization`` and ``resets_at``.
    series
        The provider's history readings per field, oldest first.
    now
        Current time as a Unix timestamp.
    workdays
        Weekdays the quota is spread over, Monday = 0; empty turns budgets off.
    tz
        Time zone of the user's days; the local one by default.

    Returns
    -------
    dict
        ``{field: DailyBudget}`` with at most one entry, empty when the
        provider has no multi-day window or budgets are off.
    """
    quotas = [key for key, value in usage.items() if key != 'extra_usage' and _is_quota(value)]
    field = consumption_field(quotas)
    if field is None:
        return {}
    entry = usage[field]
    budget = daily_budget(field, float(entry['utilization']), entry.get('resets_at') or '', series.get(field, ()), now=now, workdays=workdays, tz=tz)
    return {field: budget} if budget is not None else {}


def daily_budget(
    field: str,
    utilization: float,
    resets_at: str,
    samples: Sequence[Sample],
    *,
    now: float,
    workdays: Collection[int],
    tz: tzinfo | None = None,
) -> DailyBudget | None:
    """Return today's budget of one multi-day quota window.

    Parameters
    ----------
    field
        Quota field name; its window length comes from the name.
    utilization
        Current usage in percent.
    resets_at
        ISO timestamp of the window's reset.
    samples
        History readings of this field, oldest first; may be empty.
    now
        Current time as a Unix timestamp.
    workdays
        Weekdays the quota is spread over, Monday = 0; empty turns budgets off.
    tz
        Time zone of the user's days; the local one by default.

    Returns
    -------
    DailyBudget or None
        None when budgets are off, the window is shorter than two days, or
        its reset is unknown or has passed.
    """
    parsed = parse_field_name(field)
    period = field_period(field)
    if not workdays or parsed is None or parsed[1] != 'day' or not period or period < _MIN_PERIOD:
        return None
    reset = reset_timestamp(resets_at)
    if reset is None or reset <= now:
        return None

    today = _local(now, tz).date()
    start_of_today = _day_start(today, tz)
    at_start = _usage_at(samples, reset, start_of_today, cycle_start=reset - period, current=utilization)
    days = 1 + _workdays_before(today + timedelta(days=1), reset, workdays, tz)
    return DailyBudget(used=max(0.0, utilization - at_start), allowance=max(0.0, 100.0 - at_start) / days, days=days)


def _is_quota(entry: Any) -> bool:
    return isinstance(entry, dict) and entry.get('utilization') is not None and 'resets_at' in entry


def _local(ts: float, tz: tzinfo | None) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz is not None else datetime.fromtimestamp(ts)


def _day_start(day: date, tz: tzinfo | None) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()


def _usage_at(samples: Sequence[Sample], reset: float, moment: float, *, cycle_start: float, current: float) -> float:
    """Usage of the current window at ``moment``: the last reading before it, else the first after it.

    A window that began after ``moment`` started empty.  Without any reading
    of this window, today's usage is counted from now.
    """
    if cycle_start >= moment:
        return 0.0
    before: Sample | None = None
    after: Sample | None = None
    for sample in samples:
        if sample.reset is None or abs(sample.reset - reset) > CYCLE_RESET_TOLERANCE:
            continue
        if sample.ts <= moment:
            before = sample
        elif after is None:
            after = sample
    reading = before or after
    return reading.utilization if reading is not None else current


def _workdays_before(first: date, reset: float, workdays: Collection[int], tz: tzinfo | None) -> int:
    """Count the workdays from ``first`` on that start before the reset."""
    count = 0
    day = first
    while _day_start(day, tz) < reset:
        if day.weekday() in workdays:
            count += 1
        day += timedelta(days=1)
    return count
