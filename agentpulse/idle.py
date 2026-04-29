"""Small Win32 probes for user-idle and lock-screen state."""
from __future__ import annotations

import ctypes
import ctypes.wintypes

__all__ = ['get_idle_seconds', 'is_workstation_locked']

ctypes.windll.kernel32.GetTickCount.restype = ctypes.wintypes.DWORD


class _LastInputInfo(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.wintypes.UINT),
        ('dwTime', ctypes.wintypes.DWORD),
    ]


def get_idle_seconds() -> float:
    """Return keyboard/mouse idle time in seconds, or 0.0 if unavailable."""
    info = _LastInputInfo(ctypes.sizeof(_LastInputInfo), 0)
    ok = ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    if not ok:
        return 0.0
    elapsed_ms = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


def is_workstation_locked() -> bool:
    """Detect the secure desktop by trying to open the input desktop."""
    desktop = ctypes.windll.user32.OpenInputDesktop(0, False, 0)
    if not desktop:
        return True
    ctypes.windll.user32.CloseDesktop(desktop)
    return False
