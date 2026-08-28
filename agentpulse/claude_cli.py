"""Discovery and maintenance helpers for Claude Code installations."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

CLAUDE_CLI_PATH = Path.home() / '.local' / 'bin' / 'claude.exe'
# Install locations to try, in order.  The native installer places the CLI
# under the home directory above; a node/npm install
# (`npm install -g @anthropic-ai/claude-code`) puts it here instead.
CLAUDE_CLI_PATHS = (
    CLAUDE_CLI_PATH,
    Path(os.environ.get('APPDATA', '')) / 'npm' / 'claude.cmd',
)
CHANGELOG_URL = 'https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md'
PROJECT_URL = 'https://github.com/Waldemarch/AgentsPulse'

__all__ = [
    'CHANGELOG_URL', 'CLAUDE_CLI_PATH', 'CLAUDE_CLI_PATHS', 'PROJECT_URL',
    'ClaudeInstallation', 'RefreshResult', 'claude_cli_path', 'cli_version',
    'find_installations', 'refresh_token',
]

_EXTENSION_DIRS: list[tuple[str, Path]] = [
    ('VS Code', Path.home() / '.vscode' / 'extensions'),
    ('VS Code Insiders', Path.home() / '.vscode-insiders' / 'extensions'),
    ('Cursor', Path.home() / '.cursor' / 'extensions'),
    ('Windsurf', Path.home() / '.windsurf' / 'extensions'),
]
_EXTENSION_PREFIX = 'anthropic.claude-code-'
_EXTENSION_NAME = _EXTENSION_PREFIX
_SEMVER_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)')
_version_cache: dict[Path, tuple[float, str]] = {}


@dataclass(frozen=True)
class ClaudeInstallation:
    name: str
    version: str
    path: Path


@dataclass(frozen=True)
class RefreshResult:
    success: bool
    updated: bool
    old_version: str
    new_version: str
    error: str


def _run_hidden(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def cli_version(path: Path) -> str:
    """Read a Claude CLI executable version with mtime-based caching."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ''

    cached = _version_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        proc = _run_hidden([str(path), '--version'], timeout=10)
    except Exception:
        version = ''
    else:
        match = _SEMVER_RE.search(proc.stdout.strip())
        version = match.group(0) if match else ''

    _version_cache[path] = (mtime, version)
    return version


def claude_cli_path() -> Path | None:
    """Return the first installed Claude Code CLI, or None when none is present.

    Tries the known install locations first, then falls back to a PATH
    lookup so installers that place the CLI somewhere else - but add it to
    PATH, as every documented install method does - are still found.
    """
    for path in CLAUDE_CLI_PATHS:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    found = shutil.which('claude')
    return Path(found) if found else None


def _best_extension(root: Path) -> tuple[str, Path] | None:
    if not root.is_dir():
        return None
    candidates: list[tuple[tuple[int, int, int], str, Path]] = []
    for child in root.iterdir():
        if not child.name.startswith(_EXTENSION_NAME):
            continue
        match = _SEMVER_RE.search(child.name[len(_EXTENSION_NAME):])
        if match:
            version_tuple = tuple(int(part) for part in match.groups())
            candidates.append((version_tuple, match.group(0), child))
    if not candidates:
        return None
    _rank, version, path = max(candidates, key=lambda item: item[0])
    return version, path


def find_installations() -> list[ClaudeInstallation]:
    """Return known Claude Code CLI and IDE extension installations."""
    found: list[ClaudeInstallation] = []
    cli_path = claude_cli_path()
    if cli_path is not None:
        version = cli_version(cli_path)
        if version:
            found.append(ClaudeInstallation('CLI', version, cli_path))

    for label, root in _EXTENSION_DIRS:
        best = _best_extension(root)
        if best is not None:
            version, path = best
            found.append(ClaudeInstallation(label, version, path))
    return found


def refresh_token() -> RefreshResult:
    """Run `claude update` and summarize the result."""
    cli_path = claude_cli_path()
    if cli_path is None:
        return RefreshResult(False, False, '', '', 'CLI not found')
    try:
        proc = _run_hidden([str(cli_path), 'update'], timeout=60)
    except subprocess.TimeoutExpired:
        return RefreshResult(False, False, '', '', 'Timeout')
    except OSError as exc:
        return RefreshResult(False, False, '', '', str(exc))

    output = f'{proc.stdout}\n{proc.stderr}'
    updated = re.search(r'updated from (\S+) to (?:version )?(\S+)', output, re.IGNORECASE)
    if updated:
        return RefreshResult(True, True, updated.group(1), updated.group(2), '')

    current = re.search(r'up to date \((\S+)\)', output, re.IGNORECASE)
    if current:
        version = current.group(1)
        return RefreshResult(True, False, version, version, '')

    if proc.returncode == 0:
        return RefreshResult(True, False, '', '', '')
    return RefreshResult(False, False, '', '', output.strip()[:200])
