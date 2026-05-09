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
_SCALE = 4          # supersampling; draw at _SIZE*_SCALE, then downscale for anti-aliasing
_CLEAR = (0, 0, 0, 0)
_RING_NORMAL = (74, 158, 255, 255)   # blue  — normal usage
_RING_WARN   = (224, 128, 30, 255)   # orange — usage ≥ 80 %
_RING_CRIT   = (230, 80, 80, 255)    # red   — usage ≥ 95 %


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


def _ring_color(pct: float) -> tuple[int, int, int, int]:
    if pct >= 95:
        return _RING_CRIT
    if pct >= 80:
        return _RING_WARN
    return _RING_NORMAL


def _track_rgba(light_taskbar: bool) -> tuple[int, int, int, int]:
    """Semi-transparent ring track that works on both light and dark taskbars."""
    return (0, 0, 0, 70) if light_taskbar else (255, 255, 255, 55)


def _draw_ring(
    draw: ImageDraw.ImageDraw,
    bbox: list[int],
    pct: float,
    ring_width: int,
    track: tuple[int, int, int, int],
) -> None:
    """Draw a single circular progress ring.

    A full ring represents 0 % usage.  As pct increases the ring is erased
    counter-clockwise starting from 12 o'clock, leaving only the remaining
    capacity visible.  At 100 % only the grey track is shown.
    """
    pct = max(0.0, min(100.0, pct))
    # Grey track — always a complete circle
    draw.arc(bbox, start=0, end=359.9, fill=track, width=ring_width)
    # Coloured arc = remaining capacity (1 – utilisation)
    remaining = 1.0 - pct / 100.0
    if remaining < 0.002:
        return
    # Arc starts at 12 o'clock (–90°) and extends clockwise for `remaining * 360°`.
    # As usage grows, the end angle retreats counter-clockwise, erasing the arc from
    # the 12 o'clock position going CCW.
    end_angle = -90.0 + remaining * 360.0
    draw.arc(bbox, start=-90, end=end_angle, fill=_ring_color(pct), width=ring_width)


def create_icon_image(
    pct_top: float,
    pct_bottom: float | None = None,
    light_taskbar: bool = False,
    *,
    mode_top: str = 'utilization',
    mode_bottom: str = 'utilization',
    time_pct_top: float | None = None,
    time_pct_bottom: float | None = None,
) -> Image.Image:
    """Create a 64 px RGBA tray icon with circular ring(s).

    pct_top    — Claude 5 h utilisation; always the outer (or only) ring.
    pct_bottom — Codex 5 h utilisation; when provided a second, inner ring is
                 drawn concentrically inside the Claude ring.  Pass ``None``
                 (the default) to show a single ring.
    """
    S = _SIZE * _SCALE          # canvas size at 4× for anti-aliasing
    img = Image.new('RGBA', (S, S), _CLEAR)
    draw = ImageDraw.Draw(img)
    track = _track_rgba(light_taskbar)
    c = S // 2                  # centre coordinate

    if pct_bottom is not None:
        # ── Two concentric rings ──────────────────────────────────────────
        # Outer ring (Claude 5 h)
        #   arc centre-line radius = 100, ring width = 48
        #   outer edge ≈ 4 px from image border (at 4×) → 1 px at 64 px
        _draw_ring(draw, [c - 100, c - 100, c + 100, c + 100], pct_top,    48, track)
        # Inner ring (Codex 5 h)
        #   arc centre-line radius = 50, ring width = 40
        #   ≈ 1.5 px gap between the two rings at 64 px
        _draw_ring(draw, [c - 50,  c - 50,  c + 50,  c + 50],  pct_bottom, 40, track)
    else:
        # ── Single ring ───────────────────────────────────────────────────
        #   arc centre-line radius = 94, ring width = 60  (≈ 15 px at 64 px)
        _draw_ring(draw, [c - 94, c - 94, c + 94, c + 94], pct_top, 60, track)

    return img.resize((_SIZE, _SIZE), Image.LANCZOS)


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
