"""Windows Run-key integration for optional autostart."""
from __future__ import annotations

import sys
import winreg

__all__ = [
    'AUTOSTART_REG_KEY', 'AUTOSTART_REG_NAME', 'LEGACY_AUTOSTART_REG_NAMES',
    'is_autostart_enabled', 'set_autostart', 'sync_autostart_path',
]

AUTOSTART_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
AUTOSTART_REG_NAME = 'Agents Pulse'
LEGACY_AUTOSTART_REG_NAMES = ('AgentPulse', 'UsageMonitorForClaude')


def _all_names() -> tuple[str, ...]:
    return (AUTOSTART_REG_NAME, *LEGACY_AUTOSTART_REG_NAMES)


def _command() -> str:
    return f'"{sys.executable}"'


def is_autostart_enabled() -> bool:
    """Return True when any current or legacy Run value is present."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY) as key:
            for name in _all_names():
                try:
                    winreg.QueryValueEx(key, name)
                    return True
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        return False
    return False


def set_autostart(enable: bool) -> None:
    """Create or remove the Run-key value for this executable."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, AUTOSTART_REG_NAME, 0, winreg.REG_SZ, _command())
            names = LEGACY_AUTOSTART_REG_NAMES
        else:
            names = _all_names()
        for name in names:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


def sync_autostart_path() -> None:
    """Refresh the stored executable path after a portable EXE move."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY) as key:
            stored = None
            for name in _all_names():
                try:
                    stored, _kind = winreg.QueryValueEx(key, name)
                    break
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        return
    if stored is not None and stored != _command():
        set_autostart(True)
