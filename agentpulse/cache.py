"""
Usage Cache
============

Claude provider cache with token refresh support.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .api import fetch_profile, fetch_usage, read_access_token
from .claude_cli import RefreshResult, refresh_token
from .provider_cache import ProviderUpdateResult, ProviderUsageCache, UsageSnapshot
from .settings import MAX_BACKOFF, POLL_FAST, POLL_INTERVAL

__all__ = ['CacheSnapshot', 'UpdateResult', 'UsageCache']

log = logging.getLogger(__name__)


CacheSnapshot = UsageSnapshot
UpdateResult = ProviderUpdateResult[RefreshResult]


class _ClaudeProvider:
    name = 'claude'
    log_prefix = 'claude'

    def read_access_token(self) -> str | None:
        return read_access_token()

    def fetch_usage(self) -> dict[str, Any]:
        return fetch_usage()

    def fetch_profile(self) -> dict[str, Any] | None:
        return fetch_profile()


class UsageCache(ProviderUsageCache[RefreshResult]):
    """Thread-safe cache managing Claude usage data and token refresh."""

    block_unchanged_failed_token = True

    def __init__(self) -> None:
        super().__init__(_ClaudeProvider())
        self.log = log

    def _handle_auth_error(self, token_before: str | None) -> RefreshResult | None:
        return self._try_token_refresh(token_before)

    def _try_token_refresh(self, token_before: str | None) -> RefreshResult | None:
        """Attempt to refresh the Claude OAuth token via ``claude update``."""
        log.warning('fetch_usage -> auth error, attempting token refresh')
        result = refresh_token()
        if not result.success:
            log.info('token refresh failed: %s', result.error)
            return None

        if read_access_token() == token_before:
            log.info('token refresh succeeded but token unchanged')
            return None

        log.info('token changed, retrying fetch_usage')
        data = fetch_usage()
        if 'error' not in data:
            log.info('retry -> OK')
            self._record_success(data)
            return result

        log.warning('retry -> error: %s', data['error'])
        self._record_error(data, count=False)
        return result

    def _now(self) -> float:
        return time.time()

    def _poll_fast(self) -> int:
        return POLL_FAST

    def _poll_interval(self) -> int:
        return POLL_INTERVAL

    def _max_backoff(self) -> int:
        return MAX_BACKOFF
