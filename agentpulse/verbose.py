"""Verbose startup diagnostics for bundled and source runs."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import importlib.metadata
import locale
import os
import platform
import sys
import winreg
from pathlib import Path

__all__ = ['print_runtime_diagnostics', 'print_startup_diagnostics', 'setup_console']

_WEBVIEW2_GUIDS = [
    ('{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'Runtime'),
    ('{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}', 'Beta'),
    ('{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}', 'Developer'),
    ('{65C35B14-6C1D-4122-AC46-7148CC9D6497}', 'Canary'),
]


def setup_console() -> None:
    if not ctypes.windll.kernel32.AttachConsole(-1):
        ctypes.windll.kernel32.AllocConsole()
    sys.stdout = open('CONOUT$', 'w', encoding='utf-8')  # noqa: SIM115
    sys.stderr = open('CONOUT$', 'w', encoding='utf-8')  # noqa: SIM115
    os.environ['PYWEBVIEW_LOG'] = 'DEBUG'


def _section(title: str) -> None:
    print(f'\n  {title}')
    print(f'  {"-" * len(title)}')


def _row(label: str, value: str, indent: int = 4) -> None:
    print(f'{" " * indent}{label + ":":<22s} {value}')


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return 'not found'


def _webview2_version() -> str:
    paths = [
        r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}',
        r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}',
    ]
    for guid, channel in _WEBVIEW2_GUIDS:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for template in paths:
                try:
                    with winreg.OpenKey(root, template.format(guid=guid)) as key:
                        version, _kind = winreg.QueryValueEx(key, 'pv')
                except OSError:
                    continue
                if version and version != '0.0.0.0':
                    return version if channel == 'Runtime' else f'{version} ({channel})'
    return 'not found'


def _dotnet_version() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as key:
            release, _kind = winreg.QueryValueEx(key, 'Release')
    except OSError:
        return 'not found'
    known = [
        (533320, '4.8.1'), (528040, '4.8'), (461808, '4.7.2'),
        (461308, '4.7.1'), (460798, '4.7'), (394802, '4.6.2'),
        (394254, '4.6.1'), (393295, '4.6'),
    ]
    for minimum, label in known:
        if release >= minimum:
            return f'{label} (release {release})'
    return f'< 4.6 (release {release})'


def _dpi_info() -> tuple[str, str]:
    user32 = ctypes.windll.user32
    try:
        context = user32.GetThreadDpiAwarenessContext()
        awareness = user32.GetAwarenessFromDpiAwarenessContext(context)
        awareness_text = {0: 'Unaware', 1: 'System', 2: 'Per-Monitor V2'}.get(awareness, f'Unknown ({awareness})')
    except Exception:
        awareness_text = 'unavailable'
    try:
        dpi = user32.GetDpiForSystem()
        dpi_text = f'{dpi} ({round(dpi / 96 * 100)}%)'
    except Exception:
        dpi_text = 'unavailable'
    return awareness_text, dpi_text


def _screen_info() -> tuple[str, str, str]:
    user32 = ctypes.windll.user32
    try:
        monitors = str(user32.GetSystemMetrics(80))
    except Exception:
        monitors = 'unavailable'
    try:
        primary = f'{user32.GetSystemMetrics(0)} x {user32.GetSystemMetrics(1)}'
    except Exception:
        primary = 'unavailable'
    try:
        rect = ctypes.wintypes.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        work = f'{rect.right - rect.left} x {rect.bottom - rect.top} (left={rect.left}, top={rect.top})'
    except Exception:
        work = 'unavailable'
    return monitors, primary, work


def _redact_home(path_str: str) -> str:
    home = str(Path.home())
    return '~' + path_str[len(home):] if path_str.startswith(home) else path_str


def _credentials_status() -> str:
    config = Path(os.environ['CLAUDE_CONFIG_DIR']) if os.environ.get('CLAUDE_CONFIG_DIR') else Path.home() / '.claude'
    path = config / '.credentials.json'
    shown = _redact_home(str(path))
    return f'found ({shown})' if path.exists() else f'NOT FOUND ({shown})'


def print_startup_diagnostics() -> None:
    from . import __version__

    print(f'\n  Agents Pulse v{__version__} - Verbose Mode')
    print(f'  {"=" * 48}')

    _section('System')
    winver = sys.getwindowsversion()
    _row('OS', f'{platform.platform()} (build {winver.build})')
    _row('Architecture', platform.machine())
    _row('Admin', 'Yes' if ctypes.windll.shell32.IsUserAnAdmin() else 'No')

    _section('Python')
    _row('Version', sys.version.split()[0])
    _row('Executable', _redact_home(sys.executable))
    frozen = getattr(sys, 'frozen', False)
    _row('Frozen (PyInstaller)', str(frozen))
    if frozen:
        _row('Bundle dir', _redact_home(getattr(sys, '_MEIPASS', 'unknown')))

    _section('Locale')
    loc = locale.getlocale()
    _row('System locale', f'{loc[0]}, {loc[1]}' if loc[0] else 'not set')
    _row('Filesystem encoding', sys.getfilesystemencoding())
    _row('Default encoding', sys.getdefaultencoding())
    _row('CLAUDE_CONFIG_DIR', _redact_home(os.environ.get('CLAUDE_CONFIG_DIR', '')) or '(not set)')

    _section('Display')
    awareness, dpi = _dpi_info()
    _row('DPI awareness', awareness)
    _row('System DPI', dpi)
    monitors, primary, work = _screen_info()
    _row('Monitors', monitors)
    _row('Primary resolution', primary)
    _row('Work area', work)

    _section('Runtimes')
    _row('WebView2', _webview2_version())
    _row('.NET Framework', _dotnet_version())

    _section('Dependencies')
    for package in ('pywebview', 'pythonnet', 'clr-loader', 'pystray', 'Pillow', 'requests'):
        _row(package, _package_version(package))

    _section('Credentials')
    _row('File', _credentials_status())
    print()


def print_runtime_diagnostics() -> None:
    import webview  # type: ignore[import-untyped]

    _section('Runtime (post-init)')
    _row('Webview renderer', getattr(webview, 'renderer', None) or 'unknown')
    gui = getattr(webview, 'guilib', None)
    _row('GUI backend', gui.__name__ if gui else 'unknown')
    try:
        import pythonnet  # type: ignore[import-untyped]

        info = pythonnet.get_runtime_info()
        if info:
            _row('.NET runtime', f'{info.kind} {info.version}')
            _row('.NET initialized', str(info.initialized))
        else:
            _row('.NET runtime', 'info not available')
    except Exception as exc:
        _row('.NET runtime', f'error: {exc}')
    try:
        from System import Environment  # type: ignore[import-untyped]

        _row('.NET CLR version', str(Environment.Version))
    except Exception:
        pass
    print()
