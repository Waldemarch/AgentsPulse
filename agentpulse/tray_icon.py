"""
Tray Icon
=========

Rendering and taskbar-theme helpers for the tray icon.
"""
from __future__ import annotations

import ctypes
import functools
import os
import winreg
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFont

from .settings import ICON_DARK, ICON_LIGHT

__all__ = [
    'create_countdown_image', 'create_icon_image', 'create_ready_image', 'create_status_image',
    'load_font', 'taskbar_uses_light_theme', 'watch_theme_change',
]

THEME_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
THEME_REG_VALUE = 'SystemUsesLightTheme'
REG_NOTIFY_CHANGE_LAST_SET = 0x00000004
_SIZE = 64
_SCALE = 4          # supersampling; draw at _SIZE*_SCALE, then downscale for anti-aliasing
_CANVAS = _SIZE * _SCALE
_CLEAR = (0, 0, 0, 0)
_WHITE = (255, 255, 255, 255)
_STATUS_NORMAL = (74, 158, 255, 255)   # blue - normal usage
_STATUS_WARN = (224, 128, 30, 255)     # orange - usage >= 80 %
_STATUS_CRIT = (230, 80, 80, 255)      # red - usage >= 95 %
_READY = (40, 170, 95, 255)            # green - a reached limit has just reset

# Bars, in the 4x supersampled space: outer margin, gap between bars, widest bar.
_BAR_MARGIN = 16
_BAR_GAP = 20
_BAR_MAX_WIDTH = 112
# A bar with any usage fills at least this share, so it stays visible at 16 px.
_BAR_MIN_SHARE = 0.06

# (outer radius, ring width) per ring, outermost first, in the 4x supersampled
# space; PIL grows an arc's width inwards from the bounding box, so the radius
# is the outer edge.  Divide by _SCALE for the size on a 64 px icon.
_RING_GEOMETRY: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((112, 52),),
    2: ((112, 40), (66, 38)),
    3: ((112, 30), (78, 30), (44, 30)),
}

# Number style: the meter under the digits, in the supersampled space.
_METER_BOX = (12, 212, _CANVAS - 12, 244)


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


def create_icon_image(percentages: list[float], light_taskbar: bool = False, style: str = 'bars') -> Image.Image:
    """Create a 64 px RGBA tray icon showing each provider's session usage.

    Parameters
    ----------
    percentages
        Utilisation per provider, in display order (the popup's tab order).
        Providers the user has disabled are expected to be absent.
    light_taskbar
        Whether the Windows taskbar uses the light theme.
    style
        ``'bars'`` - one vertical bar per provider, filled from the bottom;
        ``'rings'`` - one concentric ring per provider, the used share drawn
        clockwise from 12 o'clock and a ring at 95 % or more drawn solid; or
        ``'number'`` - the highest percentage as digits over its meter.
        Unknown styles fall back to bars.
    """
    if style == 'rings':
        return _rings_image(percentages, light_taskbar)
    if style == 'number':
        return _number_image(percentages, light_taskbar)
    return _bars_image(percentages, light_taskbar)


def create_countdown_image(text: str) -> Image.Image:
    """Create the icon shown while every provider is at its limit: the time left on a red disc.

    ``text`` is the compact time until the first provider is usable again,
    such as ``'47'`` (minutes), ``'5h'`` or ``'2d'``.
    """
    image = _disc_image(_STATUS_CRIT)
    _draw_centered_text(ImageDraw.Draw(image), text, _WHITE, box=(4, 4, _SIZE - 4, _SIZE - 4), start_size=40)
    return image


def create_ready_image() -> Image.Image:
    """Create the icon shown right after a reached limit resets: a check mark on a green disc."""
    canvas = Image.new('RGBA', (_CANVAS, _CANVAS), _CLEAR)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse([8, 8, _CANVAS - 8, _CANVAS - 8], fill=_READY)
    draw.line([(66, 134), (110, 178), (192, 88)], fill=_WHITE, width=30, joint='curve')
    return canvas.resize((_SIZE, _SIZE), Image.LANCZOS)


def create_status_image(text: str, light_taskbar: bool = False) -> Image.Image:
    """Create a centered text icon used for non-usage states (e.g. auth error)."""
    image = Image.new('RGBA', (_SIZE, _SIZE), _CLEAR)
    draw = ImageDraw.Draw(image)
    font = load_font(46)
    box = draw.textbbox((0, 0), text, font=font)
    x = (_SIZE - (box[2] - box[0])) / 2 - box[0]
    y = (_SIZE - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), text, fill=_palette(light_taskbar)['fg_dim'], font=font)
    return image


def _palette(light_taskbar: bool) -> dict[str, tuple[int, int, int, int]]:
    return ICON_DARK if light_taskbar else ICON_LIGHT


def _status_color(pct: float) -> tuple[int, int, int, int]:
    if pct >= 95:
        return _STATUS_CRIT
    if pct >= 80:
        return _STATUS_WARN
    return _STATUS_NORMAL


def _track_rgba(light_taskbar: bool) -> tuple[int, int, int, int]:
    """Semi-transparent track that works on both light and dark taskbars."""
    return (0, 0, 0, 70) if light_taskbar else (255, 255, 255, 55)


def _bars_image(percentages: list[float], light_taskbar: bool) -> Image.Image:
    canvas = Image.new('RGBA', (_CANVAS, _CANVAS), _CLEAR)
    count = len(percentages)
    if count:
        draw = ImageDraw.Draw(canvas)
        track = _track_rgba(light_taskbar)
        inner = _CANVAS - 2 * _BAR_MARGIN
        width = min(_BAR_MAX_WIDTH, (inner - _BAR_GAP * (count - 1)) // count)
        left = (_CANVAS - (width * count + _BAR_GAP * (count - 1))) // 2
        top, bottom = _BAR_MARGIN, _CANVAS - _BAR_MARGIN
        radius = min(width // 2, 24)
        for index, pct in enumerate(percentages):
            x0 = left + index * (width + _BAR_GAP)
            draw.rounded_rectangle([x0, top, x0 + width, bottom], radius=radius, fill=track)
            share = _visible_share(pct)
            if share <= 0:
                continue
            fill_top = round(bottom - (bottom - top) * share)
            draw.rounded_rectangle([x0, fill_top, x0 + width, bottom], radius=min(radius, (bottom - fill_top) // 2), fill=_status_color(pct))
    return canvas.resize((_SIZE, _SIZE), Image.LANCZOS)


def _visible_share(pct: float) -> float:
    """Share of a bar to fill: none at 0 %, at least ``_BAR_MIN_SHARE`` for any usage."""
    share = max(0.0, min(100.0, pct)) / 100.0
    return max(share, _BAR_MIN_SHARE) if share > 0 else 0.0


def _rings_image(percentages: list[float], light_taskbar: bool) -> Image.Image:
    canvas = Image.new('RGBA', (_CANVAS, _CANVAS), _CLEAR)
    draw = ImageDraw.Draw(canvas)
    track = _track_rgba(light_taskbar)
    centre = _CANVAS // 2
    geometry = _RING_GEOMETRY.get(len(percentages)) or _RING_GEOMETRY[max(_RING_GEOMETRY)]
    for pct, (radius, width) in zip(percentages, geometry):
        bbox = [centre - radius, centre - radius, centre + radius, centre + radius]
        draw.arc(bbox, start=0, end=359.9, fill=track, width=width)
        share = max(0.0, min(100.0, pct)) / 100.0
        # Almost out: the whole ring turns red, so the state cannot be missed at 16 px.
        if pct >= 95:
            draw.arc(bbox, start=0, end=359.9, fill=_STATUS_CRIT, width=width)
        elif share > 0:
            draw.arc(bbox, start=-90, end=-90 + share * 360.0, fill=_status_color(pct), width=width)
    return canvas.resize((_SIZE, _SIZE), Image.LANCZOS)


def _number_image(percentages: list[float], light_taskbar: bool) -> Image.Image:
    pct = max(percentages) if percentages else 0.0
    canvas = Image.new('RGBA', (_CANVAS, _CANVAS), _CLEAR)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(_METER_BOX, radius=16, fill=_track_rgba(light_taskbar))
    share = _visible_share(pct)
    if share > 0:
        left, top, right, bottom = _METER_BOX
        draw.rounded_rectangle([left, top, round(left + (right - left) * share), bottom], radius=16, fill=_status_color(pct))
    image = canvas.resize((_SIZE, _SIZE), Image.LANCZOS)

    # A reached limit shows as "!", because three digits do not fit a 16 px icon.
    text = '!' if pct >= 100 else str(max(0, round(pct)))
    color = _status_color(pct) if pct >= 80 else _palette(light_taskbar)['fg']
    _draw_centered_text(ImageDraw.Draw(image), text, color, box=(0, 0, _SIZE, _METER_BOX[1] // _SCALE - 2), start_size=50)
    return image


def _disc_image(color: tuple[int, int, int, int]) -> Image.Image:
    canvas = Image.new('RGBA', (_CANVAS, _CANVAS), _CLEAR)
    ImageDraw.Draw(canvas).ellipse([4, 4, _CANVAS - 4, _CANVAS - 4], fill=color)
    return canvas.resize((_SIZE, _SIZE), Image.LANCZOS)


def _draw_centered_text(draw: ImageDraw.ImageDraw, text: str, color: tuple[int, int, int, int], *, box: tuple[int, int, int, int], start_size: int) -> None:
    """Draw ``text`` centred in ``box``, shrinking the font until it fits."""
    left, top, right, bottom = box
    size = start_size
    font = load_font(size)
    bounds = draw.textbbox((0, 0), text, font=font)
    while size > 10 and (bounds[2] - bounds[0] > right - left or bounds[3] - bounds[1] > bottom - top):
        size -= 2
        font = load_font(size)
        bounds = draw.textbbox((0, 0), text, font=font)
    x = left + (right - left - (bounds[2] - bounds[0])) / 2 - bounds[0]
    y = top + (bottom - top - (bounds[3] - bounds[1])) / 2 - bounds[1]
    draw.text((x, y), text, fill=color, font=font)
