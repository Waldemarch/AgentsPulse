"""
Runaway Alert
=============

Spots a session quota that is being used much faster than usual, such as an
agent stuck in a loop or a background task that runs too aggressively, before
it eats the limit.

The pace is the growth of a session quota over the last half hour.  It counts
as a spike when it is at least ``SPIKE_FACTOR`` times the busiest half hours of
earlier sessions (their 95th percentile) and at least ``MIN_POINTS`` points, so
a quiet history cannot make an ordinary burst look alarming.  Nothing is
reported before three earlier sessions with usage have been seen.  Everything
comes from the usage history; nothing is fetched.
"""
from __future__ import annotations

import bisect
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from .forecast import Sample, quota_cycles

__all__ = ['MIN_POINTS', 'SPIKE_FACTOR', 'Spike', 'current_growth', 'find_spike']

_HALF_HOUR = 30 * 60
# Growth is measured over at least this much of the last half hour, so the first
# readings of a window are not extrapolated from a few seconds.
_MIN_SPAN = 10 * 60
# The newest reading has to be this recent for the pace to describe now.
_FRESH_SECONDS = 10 * 60
# Earlier sessions with usage needed for a reference, and readings from them.
_MIN_SESSIONS = 3
_MIN_READINGS = 20
_PERCENTILE_STEPS = 20
SPIKE_FACTOR = 1.5
MIN_POINTS = 15.0


@dataclass(frozen=True)
class Spike:
    """A session quota used unusually fast.

    Attributes
    ----------
    growth
        Points the quota grows per half hour at its current pace.
    typical
        The busiest half hours of earlier sessions, the 95th percentile of
        their growth, in points.
    utilization
        The quota's current utilization in percent.
    resets_at
        Unix time of the session's reset.
    """

    growth: float
    typical: float
    utilization: float
    resets_at: float


def current_growth(samples: Sequence[Sample], *, now: float) -> float | None:
    """Return the growth per half hour of the newest session window at its current pace, or None.

    None when the newest reading is older than ten minutes or belongs to a
    window that has reset, or when the window's readings of the last half hour
    span less than ten minutes.

    Parameters
    ----------
    samples
        Readings of one session quota, oldest first.
    now
        Current time as a Unix timestamp.
    """
    cycles = quota_cycles(sample for sample in samples if sample.ts <= now)
    if not cycles or cycles[-1].reset <= now or now - cycles[-1].samples[-1].ts > _FRESH_SECONDS:
        return None
    return _paces(cycles[-1].samples)[-1]


def find_spike(samples: Sequence[Sample], *, now: float) -> Spike | None:
    """Return the spike the newest session window shows, or None.

    Parameters
    ----------
    samples
        Readings of one session quota, oldest first, ideally the last 30 days.
    now
        Current time as a Unix timestamp.
    """
    growth = current_growth(samples, now=now)
    if growth is None or growth < MIN_POINTS:
        return None
    cycles = quota_cycles(sample for sample in samples if sample.ts <= now)
    earlier: list[float] = []
    sessions = 0
    for cycle in cycles[:-1]:
        active = [pace for pace in _paces(cycle.samples) if pace]
        sessions += bool(active)
        earlier.extend(active)
    if sessions < _MIN_SESSIONS or len(earlier) < _MIN_READINGS:
        return None

    typical = statistics.quantiles(earlier, n=_PERCENTILE_STEPS, method='inclusive')[-1]
    if growth < SPIKE_FACTOR * typical:
        return None
    newest = cycles[-1].samples[-1]
    return Spike(growth=growth, typical=typical, utilization=newest.utilization, resets_at=cycles[-1].reset)


def _paces(samples: Sequence[Sample]) -> list[float | None]:
    """Growth per half hour at every reading of one window, measured from the oldest reading of the last half hour.

    None where that reading is less than ten minutes older.
    """
    times = [sample.ts for sample in samples]
    paces: list[float | None] = []
    for index, sample in enumerate(samples):
        base = samples[bisect.bisect_left(times, sample.ts - _HALF_HOUR, 0, index + 1)]
        span = sample.ts - base.ts
        paces.append(None if span < _MIN_SPAN else max(0.0, (sample.utilization - base.utilization) / span * _HALF_HOUR))
    return paces
