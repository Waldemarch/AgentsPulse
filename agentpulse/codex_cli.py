"""
Codex CLI
==========

Discovers the Codex CLI installation and reports its version.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

# npm global installs place a shim here on Windows
# (`npm install -g @openai/codex`).
CODEX_CLI_PATH = Path(os.environ.get('APPDATA', '')) / 'npm' / 'codex.cmd'
CODEX_CLI_PATHS = (CODEX_CLI_PATH,)

PROJECT_URL = 'https://github.com/openai/codex'

__all__ = ['CODEX_CLI_PATH', 'CODEX_CLI_PATHS', 'PROJECT_URL', 'codex_cli_path', 'codex_version']

_version_cache: dict[Path, tuple[float, str]] = {}


def codex_cli_path() -> Path | None:
    """Return the installed Codex CLI path, or None when none is present.

    Tries the known npm shim location first, then falls back to a PATH
    lookup so the PowerShell installer (`irm .../install.ps1 | iex`) and
    manually placed binaries are found too - both add `codex` to PATH,
    which is the only install-location guarantee they make.
    """
    for path in CODEX_CLI_PATHS:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    found = shutil.which('codex')
    return Path(found) if found else None


def codex_version() -> str:
    """Run ``codex --version`` and return the version string, or ''.

    Results are cached by file modification time so the subprocess is
    only spawned once per binary change.
    """
    path = codex_cli_path()
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
