"""
Codex CLI
==========

Discovers the Codex CLI installation and reports its version.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

CODEX_CLI_PATH = Path(os.environ.get('APPDATA', '')) / 'npm' / 'codex.cmd'

PROJECT_URL = 'https://github.com/openai/codex'

__all__ = ['CODEX_CLI_PATH', 'PROJECT_URL', 'codex_version']

_version_cache: dict[Path, tuple[float, str]] = {}


def codex_version() -> str:
    """Run ``codex --version`` and return the version string, or ''.

    Results are cached by file modification time so the subprocess is
    only spawned once per binary change.
    """
    path = CODEX_CLI_PATH
    if not path.is_file():
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
