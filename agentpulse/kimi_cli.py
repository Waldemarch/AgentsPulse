"""
Kimi CLI
=========

Discovers the Kimi Code CLI installation and reports its version.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .kimi_api import KIMI_CODE_HOME

# Install locations to try, in order.  The Kimi Code installer ships a
# self-contained binary inside the CLI home directory; the npm package is the
# fallback for installs done through node.
KIMI_CLI_PATHS = (
    KIMI_CODE_HOME / 'bin' / 'kimi.exe',
    Path(os.environ.get('APPDATA', '')) / 'npm' / 'kimi.cmd',
)

PROJECT_URL = 'https://github.com/MoonshotAI/kimi-cli'

__all__ = ['KIMI_CLI_PATHS', 'PROJECT_URL', 'kimi_cli_path', 'kimi_version']

_version_cache: dict[Path, tuple[float, str]] = {}


def kimi_cli_path() -> Path | None:
    """Return the first installed Kimi Code CLI, or None when none is present."""
    for path in KIMI_CLI_PATHS:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def kimi_version() -> str:
    """Run ``kimi --version`` and return the version string, or ''.

    Results are cached by file modification time so the subprocess is
    only spawned once per binary change.
    """
    path = kimi_cli_path()
    if path is None:
        return ''
    try:
        mtime = path.stat().st_mtime
        cached = _version_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]

        proc = subprocess.run(
            [str(path), '--version'],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        match = re.search(r'(\d+\.\d+\.\d+)', proc.stdout + proc.stderr)
        version = match.group(1) if match else ''
        _version_cache[path] = (mtime, version)
        return version
    except Exception:
        return ''
