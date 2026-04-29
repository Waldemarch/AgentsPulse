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


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, y: int, color: tuple[int, ...], stroke: int = 0) -> None:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    width = box[2] - box[0]
    draw.text(((_SIZE - width) / 2 - box[0], y - box[1]), text, fill=color, font=font, stroke_width=stroke, stroke_fill=color)


def _percent_text(pct: float) -> str:
    pct = max(0.0, min(999.0, pct))
    return f'{pct:.0f}%'


def _percent_font(draw: ImageDraw.ImageDraw, text: str) -> ImageFont.ImageFont:
    for size in (31, 29, 27, 25, 23, 21):
        font = load_font(size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= _SIZE - 2 and box[3] - box[1] <= _SIZE - 2:
            return font
    return load_font(20)


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
    """Create a 64px RGBA icon showing the 5-hour session usage percent."""
    colors = _palette(light_taskbar)
    image = Image.new('RGBA', (_SIZE, _SIZE), _CLEAR)
    draw = ImageDraw.Draw(image)

    text = _percent_text(pct_top)
    font = _percent_font(draw, text)
    box = draw.textbbox((0, 0), text, font=font)
    y = (_SIZE - (box[3] - box[1])) / 2 - box[1]
    _fit_text(draw, text, font, int(y), colors['fg'])
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
