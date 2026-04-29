"""Rendering and taskbar-theme helpers for the tray icon."""
from __future__ import annotations

import ctypes
import functools
import os
import winreg
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFont

from .settings import ICON_DARK, ICON_LIGHT

__all__ = ['create_icon_image', 'create_status_image', 'load_font', 'taskbar_uses_light_theme', 'watch_theme_change']

THEME_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
THEME_REG_VALUE = 'SystemUsesLightTheme'
REG_NOTIFY_CHANGE_LAST_SET = 0x00000004
_SIZE = 64
_CLEAR = (0, 0, 0, 0)
_TRACK = (98, 104, 112, 95)
_CLAUDE = (237, 129, 92, 255)
_CODEX = (82, 148, 255, 255)
_WARN = (230, 80, 80, 255)


@functools.lru_cache(maxsize=None)
def load_font(size: int, symbol: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a Windows font, falling back to PIL's bitmap font."""
    windir = os.environ.get('WINDIR', r'C:\Windows')
    if symbol:
        choices = [fr'{windir}\Fonts\seguisym.ttf', 'seguisym.ttf']
    else:
        choices = [fr'{windir}\Fonts\arialbd.ttf', 'arialbd.ttf', fr'{windir}\Fonts\arial.ttf', 'arial.ttf']
    for name in choices:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def taskbar_uses_light_theme() -> bool:
    """Read the Windows taskbar theme flag. Missing values mean dark."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_REG_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, THEME_REG_VALUE)
    except OSError:
        return False
    return bool(value)


def watch_theme_change(callback: Callable[[], None]) -> None:
    """Wait for theme registry writes and call `callback` after each one."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_REG_KEY, 0, winreg.KEY_READ) as key:
        while True:
            status = ctypes.windll.advapi32.RegNotifyChangeKeyValue(
                int(key), False, REG_NOTIFY_CHANGE_LAST_SET, None, False,
            )
            if status:
                break
            callback()


def _palette(light_taskbar: bool) -> dict[str, tuple[int, int, int, int]]:
    return ICON_DARK if light_taskbar else ICON_LIGHT


def _usage_color(pct: float, base: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if pct >= 95:
        return _WARN
    return base


def _draw_usage_row(
    draw: ImageDraw.ImageDraw,
    label: str,
    pct: float,
    y: int,
    base_color: tuple[int, int, int, int],
    text_color: tuple[int, int, int, int],
) -> None:
    pct = max(0.0, min(100.0, pct))
    font = load_font(16)
    draw.text((1, y - 3), label, fill=text_color, font=font)

    left = 16
    top = y
    right = 57
    bottom = y + 17
    fill_right = left + int((right - left) * pct / 100)
    color = _usage_color(pct, base_color)
    draw.rounded_rectangle((left, top, right, bottom), radius=3, fill=_TRACK)
    if pct > 0:
        draw.rounded_rectangle((left, top, max(left + 2, fill_right), bottom), radius=3, fill=color)

    pct_text = f'{pct:.0f}'
    pct_font = load_font(12)
    box = draw.textbbox((0, 0), pct_text, font=pct_font)
    draw.text((_SIZE - (box[2] - box[0]) - 1, y + 17), pct_text, fill=text_color, font=pct_font)


def create_icon_image(
    pct_top: float,
    pct_bottom: float = 0,
    light_taskbar: bool = False,
    *,
    mode_top: str = 'utilization',
    mode_bottom: str = 'utilization',
    time_pct_top: float | None = None,
    time_pct_bottom: float | None = None,
) -> Image.Image:
    """Create a 64px RGBA icon with compact Claude and Codex usage rows."""
    colors = _palette(light_taskbar)
    image = Image.new('RGBA', (_SIZE, _SIZE), _CLEAR)
    draw = ImageDraw.Draw(image)

    _draw_usage_row(draw, 'C', pct_top, 5, _CLAUDE, colors['fg'])
    _draw_usage_row(draw, 'X', pct_bottom, 36, _CODEX, colors['fg'])
    return image


def create_status_image(text: str, light_taskbar: bool = False) -> Image.Image:
    """Create a centered text icon used for non-usage states."""
    image = Image.new('RGBA', (_SIZE, _SIZE), _CLEAR)
    draw = ImageDraw.Draw(image)
    font = load_font(46)
    box = draw.textbbox((0, 0), text, font=font)
    x = (_SIZE - (box[2] - box[0])) / 2 - box[0]
    y = (_SIZE - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), text, fill=_palette(light_taskbar)['fg_dim'], font=font)
    return image
