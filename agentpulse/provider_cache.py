"""
Provider Cache
==============

Shared thread-safe cache machinery for usage providers.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from .settings import MAX_BACKOFF, POLL_FAST, POLL_INTERVAL

__all__ = ['ProviderUpdateResult', 'UsageProvider', 'UsageSnapshot', 'ProviderUsageCache']

TRefresh = TypeVar('TRefresh')


@dataclass(frozen=True)
class UsageSnapshot:
    """Immutable, consistent snapshot of provider cache state."""

    usage: dict[str, Any]
    profile: dict[str, Any] | None
    last_success_time: float | None
    refreshing: bool
    last_error: str | None
    version: int


@dataclass(frozen=True)
class ProviderUpdateResult(Generic[TRefresh]):
    """Result of a provider cache update."""

    data: dict[str, Any] | None
    token_refresh: TRefresh | None = None


class UsageProvider(Protocol):
    """Minimal API surface needed by ``ProviderUsageCache``."""

    name: str
    log_prefix: str

    def read_access_token(self) -> str | None:
        """Return the current provider access token, or None."""

    def fetch_usage(self) -> dict[str, Any]:
        """Fetch provider usage data."""

    def fetch_profile(self) -> dict[str, Any] | None:
        """Fetch provider profile data."""


class ProviderUsageCache(Generic[TRefresh]):
    """Thread-safe cache managing usage data for a single provider."""

    snapshot_type = UsageSnapshot
    result_type = ProviderUpdateResult
    block_unchanged_failed_token = False

    def __init__(self, provider: UsageProvider) -> None:
        self.provider = provider
        self.log = logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._profile_lock = threading.Lock()
        self._usage: dict[str, Any] = {}
        self._profile: dict[str, Any] | None = None
        self._profile_token: str | None = None
        self._last_success_time: float | None = None
        self._refreshing = False
        self._last_error: str | None = None
        self._version = 0
        self._consecutive_errors = 0
        self._last_failed_token: str | None = None
        self._rate_limit_until: float = 0

    @property
    def usage(self) -> dict[str, Any]:
        """Last successful usage data (empty dict before first success)."""
        return self._usage

    @property
    def profile(self) -> dict[str, Any] | None:
        return self._profile

    @property
    def last_success_time(self) -> float | None:
        return self._last_success_time

    @property
    def refreshing(self) -> bool:
        return self._refreshing

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def version(self) -> int:
        """Change counter - incremented on every state change."""
        return self._version

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    @property
    def rate_limit_remaining(self) -> float:
        """Seconds remaining in the rate-limit backoff window, or 0."""
        return max(self._rate_limit_until - self._now(), 0)

    @property
    def snapshot(self) -> UsageSnapshot:
        """Return a consistent snapshot for UI display."""
        with self._state_lock:
            return self.snapshot_type(
                usage=self._usage,
                profile=self._profile,
                last_success_time=self._last_success_time,
                refreshing=self._refreshing,
                last_error=self._last_error,
                version=self._version,
            )

    def ensure_profile(self) -> None:
        """Fetch the account profile if missing, or if the access token changed."""
        current_token = self.provider.read_access_token()
        if self._profile is not None and self._profile_token == current_token:
            return

        with self._profile_lock:
            current_token = self.provider.read_access_token()
            if self._profile is not None and self._profile_token == current_token:
                return
            self.log.info('%s fetch_profile started', self.provider.log_prefix)
            with self._lock:
                profile = self.provider.fetch_profile()
            with self._state_lock:
                self._profile = profile
                self._profile_token = current_token
                self._version += 1
            self.log.info('%s fetch_profile -> %s', self.provider.log_prefix, 'OK' if profile else 'failed')

    def update(self) -> ProviderUpdateResult[TRefresh]:
        """Fetch usage data with lock and cooldown protection."""
        if not self._lock.acquire(blocking=False):
            self.log.debug('%s update skipped (another update in progress)', self.provider.log_prefix)
            return self.result_type(data=None)

        try:
            return self._update_locked()
        finally:
            self._lock.release()

    def _update_locked(self) -> ProviderUpdateResult[TRefresh]:
        """Execute the actual update while holding ``_lock``."""
        poll_fast = self._poll_fast()
        if self._last_success_time is not None and self._now() - self._last_success_time < poll_fast:
            self.log.debug(
                '%s update skipped (cooldown, %.0fs remaining)',
                self.provider.log_prefix, poll_fast - (self._now() - self._last_success_time),
            )
            return self.result_type(data=None)

        if self._now() < self._rate_limit_until:
            self.log.debug(
                '%s update skipped (rate-limit backoff, %.0fs remaining)',
                self.provider.log_prefix, self._rate_limit_until - self._now(),
            )
            return self.result_type(data=None)

        if self.block_unchanged_failed_token and self._last_failed_token is not None:
            if self.provider.read_access_token() == self._last_failed_token:
                self.log.debug('%s update skipped (token unchanged after auth failure)', self.provider.log_prefix)
                return self.result_type(data=None)
            self._last_failed_token = None

        with self._state_lock:
            self._refreshing = True
            self._version += 1

        try:
            return self._fetch_and_process()
        except Exception:
            with self._state_lock:
                self._refreshing = False
                self._version += 1
            raise

    def _fetch_and_process(self) -> ProviderUpdateResult[TRefresh]:
        """Fetch usage data and process success/error state."""
        token_before = self.provider.read_access_token()
        self.log.info('%s fetch_usage started', self.provider.log_prefix)
        data = self.provider.fetch_usage()

        if 'error' in data:
            self._record_error(data)

            if data.get('rate_limited'):
                retry_after = data.get('retry_after')
                if retry_after is not None and retry_after > 0:
                    delay = min(max(retry_after, self._poll_interval()), self._max_backoff())
                else:
                    delay = min(self._poll_interval() * (2 ** max(self._consecutive_errors - 1, 0)), self._max_backoff())
                self._rate_limit_until = self._now() + delay
                self.log.warning('%s fetch_usage -> rate limited, backoff %.0fs', self.provider.log_prefix, delay)

            token_refresh = None
            if data.get('auth_error'):
                token_refresh = self._handle_auth_error(token_before)
                if token_refresh is not None and self._last_error is None:
                    return self.result_type(data=self._usage, token_refresh=token_refresh)
                if token_refresh is None and self.block_unchanged_failed_token:
                    self._last_failed_token = token_before
            elif not data.get('rate_limited'):
                self.log.warning('%s fetch_usage -> error: %s', self.provider.log_prefix, data['error'])

            with self._state_lock:
                self._refreshing = False
                self._version += 1
            return self.result_type(data=data, token_refresh=token_refresh)

        pct_5h = (data.get('five_hour') or {}).get('utilization')
        pct_7d = (data.get('seven_day') or {}).get('utilization')
        self.log.info(
            '%s fetch_usage -> OK (5h: %s%%, 7d: %s%%)',
            self.provider.log_prefix,
            pct_5h if pct_5h is not None else '?',
            pct_7d if pct_7d is not None else '?',
        )
        self._record_success(data)
        return self.result_type(data=data)

    def _record_error(self, data: dict[str, Any], *, count: bool = True) -> None:
        """Apply common state updates after a failed provider response."""
        with self._state_lock:
            if count:
                self._consecutive_errors += 1
            error = data['error']
            server_msg = data.get('server_message')
            if server_msg:
                error += f'\n{server_msg}'
            self._last_error = error

    def _record_success(self, data: dict[str, Any]) -> None:
        """Apply common state updates after a successful provider response."""
        with self._state_lock:
            self._consecutive_errors = 0
            self._last_error = None
            self._last_success_time = self._now()
            self._rate_limit_until = 0
            self._last_failed_token = None
            self._usage = data
            self._refreshing = False
            self._version += 1

    def _handle_auth_error(self, token_before: str | None) -> TRefresh | None:
        """Handle provider auth errors; subclasses may refresh tokens."""
        return None

    def _now(self) -> float:
        """Return current time. Subclasses override for legacy test patch points."""
        return time.time()

    def _poll_fast(self) -> int:
        return POLL_FAST

    def _poll_interval(self) -> int:
        return POLL_INTERVAL

    def _max_backoff(self) -> int:
        return MAX_BACKOFF
