"""Single-process coordination for the Windows tray executable."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import struct

from . import __version__
from .i18n import T

__all__ = ['ensure_single_instance', 'release_instance_lock']

_MUTEX_NAME = 'AgentsPulse_SingleInstance'
_INFO_NAME = 'AgentsPulse_HolderPID'
_ERROR_ALREADY_EXISTS = 0xB7
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_PAGE_READWRITE = 0x04
_FILE_MAP_READ = 0x0004
_FILE_MAP_WRITE = 0x0002
_BUFFER_SIZE = 64

_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.wintypes.LPCVOID, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
_kernel32.CreateFileMappingW.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.wintypes.LPCVOID, ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR,
]
_kernel32.CreateFileMappingW.restype = ctypes.wintypes.HANDLE
_kernel32.OpenFileMappingW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
_kernel32.OpenFileMappingW.restype = ctypes.wintypes.HANDLE
_kernel32.MapViewOfFile.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.c_size_t,
]
_kernel32.MapViewOfFile.restype = ctypes.c_void_p
_kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_kernel32.UnmapViewOfFile.restype = ctypes.wintypes.BOOL
_kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.TerminateProcess.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.UINT]
_kernel32.TerminateProcess.restype = ctypes.wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD

_mutex_handle: int | None = None
_info_handle: int | None = None
_pid_mapping_handle: int | None = None


def _close(handle: int | None) -> None:
    if handle:
        _kernel32.CloseHandle(handle)


def _store_holder_info() -> None:
    global _info_handle, _pid_mapping_handle
    _info_handle = _kernel32.CreateFileMappingW(
        _INVALID_HANDLE_VALUE, None, _PAGE_READWRITE, 0, _BUFFER_SIZE, _INFO_NAME,
    )
    _pid_mapping_handle = _info_handle
    if not _info_handle:
        return
    view = _kernel32.MapViewOfFile(_info_handle, _FILE_MAP_WRITE, 0, 0, _BUFFER_SIZE)
    if not view:
        return
    version = __version__.encode('utf-8')[: _BUFFER_SIZE - 5]
    payload = struct.pack(f'<I{len(version) + 1}s', os.getpid(), version + b'\0')
    ctypes.memmove(view, payload, len(payload))
    _kernel32.UnmapViewOfFile(view)


def _read_holder_info() -> tuple[int | None, str | None]:
    mapping = _kernel32.OpenFileMappingW(_FILE_MAP_READ, False, _INFO_NAME)
    if not mapping:
        return None, None
    try:
        view = _kernel32.MapViewOfFile(mapping, _FILE_MAP_READ, 0, 0, _BUFFER_SIZE)
        if not view:
            return None, None
        try:
            raw = ctypes.string_at(view, _BUFFER_SIZE)
        finally:
            _kernel32.UnmapViewOfFile(view)
    finally:
        _kernel32.CloseHandle(mapping)

    if len(raw) < 5:
        return None, None
    pid = struct.unpack('<I', raw[:4])[0]
    version = raw[4:].split(b'\0', 1)[0].decode('utf-8', errors='replace') or None
    return (pid or None), version


def _terminate_pid(pid: int) -> None:
    process_terminate = 0x0001
    process_synchronize = 0x00100000
    handle = _kernel32.OpenProcess(process_terminate | process_synchronize, False, pid)
    if not handle:
        return
    try:
        if _kernel32.TerminateProcess(handle, 1):
            _kernel32.WaitForSingleObject(handle, 5000)
    finally:
        _kernel32.CloseHandle(handle)


def _ask_to_replace(version: str | None) -> bool:
    title = T['popup_title'] + (f' v{version}' if version else '')
    message = T['already_running'].format(running_version=version or '?')
    answer = ctypes.windll.user32.MessageBoxW(None, message, title, 0x04 | 0x20 | 0x40000)
    return answer == 6


def ensure_single_instance() -> bool:
    """Acquire the global instance lock, replacing an old copy on request."""
    global _mutex_handle
    _mutex_handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.get_last_error() != _ERROR_ALREADY_EXISTS:
        _store_holder_info()
        return True

    pid, version = _read_holder_info()
    if not _ask_to_replace(version):
        _close(_mutex_handle)
        _mutex_handle = None
        return False

    _close(_mutex_handle)
    if pid:
        _terminate_pid(pid)
    _mutex_handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    _store_holder_info()
    return True


def release_instance_lock() -> None:
    """Release process-wide Win32 handles held by this module."""
    global _mutex_handle, _info_handle, _pid_mapping_handle
    _close(_mutex_handle)
    _close(_pid_mapping_handle if _pid_mapping_handle is not None else _info_handle)
    _mutex_handle = None
    _info_handle = None
    _pid_mapping_handle = None
