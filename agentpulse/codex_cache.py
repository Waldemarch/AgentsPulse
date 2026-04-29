"""
Codex Usage Cache
==================

Codex provider cache.  Codex CLI manages its own token lifecycle, so this
cache uses the shared provider machinery without an automatic refresh hook.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .codex_api import fetch_profile, fetch_usage, read_access_token
from .provider_cache import ProviderUpdateResult, ProviderUsageCache, UsageSnapshot
from .settings import MAX_BACKOFF, POLL_FAST, POLL_INTERVAL

__all__ = ['CodexSnapshot', 'CodexUpdateResult', 'CodexCache']

log = logging.getLogger(__name__)


CodexSnapshot = UsageSnapshot
CodexUpdateResult = ProviderUpdateResult[None]


class _CodexProvider:
    name = 'codex'
    log_prefix = 'codex'

    def read_access_token(self) -> str | None:
        return read_access_token()

    def fetch_usage(self) -> dict[str, Any]:
        return fetch_usage()

    def fetch_profile(self) -> dict[str, Any] | None:
        return fetch_profile()


class CodexCache(ProviderUsageCache[None]):
    """Thread-safe cache managing Codex usage data."""

    def __init__(self) -> None:
        super().__init__(_CodexProvider())
        self.log = log

    def _now(self) -> float:
        return time.time()

    def _poll_fast(self) -> int:
        return POLL_FAST

    def _poll_interval(self) -> int:
        return POLL_INTERVAL

    def _max_backoff(self) -> int:
        return MAX_BACKOFF
