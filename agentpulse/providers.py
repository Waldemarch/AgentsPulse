"""
Secondary Provider Registry
=============================

Central metadata for the non-Claude usage providers (Codex, Kimi).

Claude is the primary provider and is wired directly into the modules that
need it; Codex and Kimi are optional, symmetrical add-ons, so their
per-provider metadata - the CLI version lookup and the popup's installed-
versions heading - lives here once instead of being duplicated across
``dashboard.py`` and ``popup.py``.  Adding a third non-Claude provider means
adding one entry here, not touching every view module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .codex_cli import codex_version
from .kimi_cli import kimi_version

__all__ = ['SECONDARY_PROVIDERS', 'SECONDARY_PROVIDERS_BY_NAME', 'SecondaryProviderSpec']


@dataclass(frozen=True)
class SecondaryProviderSpec:
    """Static metadata for one non-Claude usage provider."""

    name: str
    cli_version: Callable[[], str]
    install_title_key: str
    install_title_fallback: str


SECONDARY_PROVIDERS: tuple[SecondaryProviderSpec, ...] = (
    SecondaryProviderSpec('codex', codex_version, 'codex_cli', 'CODEX CLI'),
    SecondaryProviderSpec('kimi', kimi_version, 'kimi_cli', 'KIMI CLI'),
)

SECONDARY_PROVIDERS_BY_NAME: dict[str, SecondaryProviderSpec] = {spec.name: spec for spec in SECONDARY_PROVIDERS}
