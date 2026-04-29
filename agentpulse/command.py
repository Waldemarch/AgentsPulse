"""Launch user event hooks without blocking the tray application."""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

from . import __version__

__all__ = ['run_event_command']


def _app_directory() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _event_environment(values: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        'AGENTPULSE_VERSION': __version__,
        'USAGE_MONITOR_VERSION': __version__,
    })
    env.update({key: str(value) for key, value in values.items()})
    return env


def run_event_command(commands: list[str], env_vars: dict[str, str]) -> None:
    """Start every configured command in the background."""
    if not commands:
        return

    env = _event_environment(env_vars)
    cwd = _app_directory()
    for command in commands:
        if not command:
            continue
        try:
            subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            traceback.print_exc()
