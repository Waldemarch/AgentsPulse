"""
Tray Icon Tests
================

Unit tests for tray icon rendering and theme detection.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import agentpulse.tray_icon as tray_icon_mod


class TestLoadFont(unittest.TestCase):
    """Tests for load_font()."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_loads_arial_bold_for_normal_text(self, mock_image_font):
        """Default call loads Arial Bold font."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_font)
        mock_image_font.truetype.assert_called_once_with(r'C:\Windows\Fonts\arialbd.ttf', 42)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_loads_segoe_symbol_for_symbol_text(self, mock_image_font):
        """symbol=True loads Segoe UI Symbol font."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        result = tray_icon_mod.load_font(36, symbol=True)

        self.assertIs(result, mock_font)
        mock_image_font.truetype.assert_called_once_with(r'C:\Windows\Fonts\seguisym.ttf', 36)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_falls_back_to_default_when_all_fail(self, mock_image_font):
        """Falls back to load_default() when no TrueType font found."""
        mock_image_font.truetype.side_effect = OSError
        mock_default = MagicMock()
        mock_image_font.load_default.return_value = mock_default

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_default)
        mock_image_font.load_default.assert_called_once()

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_tries_fallback_names_on_failure(self, mock_image_font):
        """Tries alternative font names when first attempt fails."""
        mock_font = MagicMock()
        mock_image_font.truetype.side_effect = [OSError, mock_font]

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_font)
        self.assertEqual(mock_image_font.truetype.call_count, 2)
        mock_image_font.truetype.assert_called_with('arialbd.ttf', 42)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_lru_cache_returns_same_instance(self, mock_image_font):
        """Cached: same size returns same font object without second truetype call."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        first = tray_icon_mod.load_font(42)
        second = tray_icon_mod.load_font(42)

        self.assertIs(first, second)
        mock_image_font.truetype.assert_called_once()

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {'WINDIR': r'C:\Windows'})
    def test_different_sizes_cached_separately(self, mock_image_font):
        """Different sizes produce separate cache entries."""
        mock_image_font.truetype.return_value = MagicMock()

        tray_icon_mod.load_font(36)
        tray_icon_mod.load_font(42)

        self.assertEqual(mock_image_font.truetype.call_count, 2)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.dict('os.environ', {}, clear=True)
    def test_uses_default_windir_when_not_set(self, mock_image_font):
        """Falls back to C:\\Windows when WINDIR is not set."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        tray_icon_mod.load_font(42)

        mock_image_font.truetype.assert_called_once_with(r'C:\Windows\Fonts\arialbd.ttf', 42)


class TestTaskbarUsesLightTheme(unittest.TestCase):
    """Tests for taskbar_uses_light_theme()."""

    @patch.object(tray_icon_mod, 'winreg')
    def test_returns_true_for_light_theme(self, mock_winreg):
        """Registry value 1 means light theme."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (1, 4)

        self.assertTrue(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, 'winreg')
    def test_returns_false_for_dark_theme(self, mock_winreg):
        """Registry value 0 means dark theme."""
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock(return_value=mock_key)
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (0, 4)

        self.assertFalse(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, 'winreg')
    def test_returns_false_on_os_error(self, mock_winreg):
        """OSError (missing key, permissions) defaults to dark."""
        mock_winreg.OpenKey.side_effect = OSError

        self.assertFalse(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, 'winreg')
    def test_reads_correct_registry_path(self, mock_winreg):
        """Opens the Personalize registry key."""
        mock_winreg.OpenKey.return_value.__enter__ = MagicMock()
        mock_winreg.OpenKey.return_value.__exit__ = MagicMock(return_value=False)
        mock_winreg.QueryValueEx.return_value = (0, 4)

        tray_icon_mod.taskbar_uses_light_theme()

        mock_winreg.OpenKey.assert_called_once_with(
            mock_winreg.HKEY_CURRENT_USER, tray_icon_mod.THEME_REG_KEY,
        )


def _real_font():
    """Return a real PIL font for rendering tests."""
    from PIL import ImageFont

    try:
        return ImageFont.truetype('arial.ttf', 20)
    except OSError:
        return ImageFont.load_default()


class TestCreateIconImage(unittest.TestCase):
    """Tests for create_icon_image()."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_returns_64x64_rgba_image(self):
        """Icon is always 64x64 RGBA."""
        img = tray_icon_mod.create_icon_image(0, 0)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_low_usage_renders_without_error(self):
        """Usage <= 50% renders successfully."""
        img = tray_icon_mod.create_icon_image(30, 20)

        self.assertEqual(img.size, (64, 64))

    def test_high_usage_renders_without_error(self):
        """Usage > 50% renders successfully."""
        img = tray_icon_mod.create_icon_image(75, 20)

        self.assertEqual(img.size, (64, 64))

    def test_full_usage_renders_without_error(self):
        """Usage >= 100% renders successfully."""
        img = tray_icon_mod.create_icon_image(100, 20)

        self.assertEqual(img.size, (64, 64))

    def test_dark_and_light_taskbar_produce_different_images(self):
        """Dark vs light taskbar produces different pixel data."""
        img_dark = tray_icon_mod.create_icon_image(50, 50, light_taskbar=False)
        img_light = tray_icon_mod.create_icon_image(50, 50, light_taskbar=True)

        self.assertEqual(img_dark.size, (64, 64))
        self.assertEqual(img_light.size, (64, 64))
        self.assertNotEqual(img_dark.tobytes(), img_light.tobytes())

    def test_zero_usage_no_bar_fill(self):
        """Zero usage has no filled bar pixels beyond the half-tone background."""
        img = tray_icon_mod.create_icon_image(0, 0)

        self.assertEqual(img.size, (64, 64))

    def test_full_bar_fill_at_100_percent(self):
        """100% usage fills the entire bar width."""
        img_full = tray_icon_mod.create_icon_image(100, 100)
        img_zero = tray_icon_mod.create_icon_image(0, 0)

        # The bar area pixels should differ between 0% and 100%
        self.assertNotEqual(img_full.tobytes(), img_zero.tobytes())

    def test_boundary_50_differs_from_51(self):
        """50% and 51% produce different icons (text mode switch)."""
        img_50 = tray_icon_mod.create_icon_image(50, 0)
        img_51 = tray_icon_mod.create_icon_image(51, 0)

        self.assertNotEqual(img_50.tobytes(), img_51.tobytes())

    @patch.object(tray_icon_mod, 'load_font')
    def test_low_usage_calls_font_size_42(self, mock_font):
        """Usage <= 50% requests size 42 font for 'C' letter."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(30, 20)

        mock_font.assert_any_call(42)

    @patch.object(tray_icon_mod, 'load_font')
    def test_high_usage_calls_font_size_40(self, mock_font):
        """Usage > 50% requests size 40 font for percentage."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(75, 20)

        mock_font.assert_any_call(40)

    @patch.object(tray_icon_mod, 'load_font')
    def test_full_usage_calls_symbol_font(self, mock_font):
        """Usage >= 100% requests size 36 symbol font for cross."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 20)

        mock_font.assert_any_call(36, symbol=True)


class TestCreateIconImageOverageMode(unittest.TestCase):
    """Tests for create_icon_image() overage-mode bars.

    Overage mode shows how far usage has gone into the over-budget zone.
    The bar is empty when pct <= time_pct (on pace or ahead), and full when
    pct reaches 100%. Formula: fill_ratio = clamp((pct - time_pct) / (100 - time_pct), 0, 1)
    """

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_overage_mode_returns_64x64_rgba(self):
        """Overage mode still produces a 64x64 RGBA image."""
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_overage_mode_time_pct_at_100_falls_back_to_utilization(self):
        """time_pct=100 (period over) falls back to normal utilization display."""
        img_fallback = tray_icon_mod.create_icon_image(50, 50, mode_top='overage', mode_bottom='overage', time_pct_top=100, time_pct_bottom=100)
        img_util = tray_icon_mod.create_icon_image(50, 50)

        self.assertEqual(img_fallback.tobytes(), img_util.tobytes())

    def test_on_pace_produces_empty_bar(self):
        """Usage exactly at time_pct means on pace - bar pixels are not fully opaque (no fill)."""
        # pct=60, time_pct=60 -> overage=0 -> fill_ratio=0 -> no fill
        img = tray_icon_mod.create_icon_image(60, 60, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h
        pixels = img.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            # No pixel in the bar should be fully opaque (fill_w=0)
            self.assertNotEqual(pixels[0, mid_y][3], 255, f'Expected no fill at x=0, y={mid_y}')

    def test_below_pace_produces_empty_bar(self):
        """Usage below time_pct (ahead of schedule) also produces an empty bar."""
        # pct=40 < time_pct=60 -> overage=0 -> no fill; same result as pct=60
        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h

        img_ahead = tray_icon_mod.create_icon_image(40, 40, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)
        pixels = img_ahead.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            self.assertNotEqual(pixels[0, mid_y][3], 255, f'Expected no fill at x=0, y={mid_y}')

    def test_half_fill_at_midpoint_of_over_budget_range(self):
        """pct halfway between time_pct and 100% produces a half-filled bar."""
        # time_pct=60, pct=80 -> (80-60)/(100-60) = 0.5 -> fill_w = 32px
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h
        pixels = img.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            # x=31 (last pixel of left half) should be filled (fg, alpha=255)
            self.assertEqual(pixels[31, mid_y][3], 255, f'Expected filled pixel at x=31, y={mid_y}')
            # x=32 (first pixel of right half) should not be filled (bg, alpha<255)
            self.assertNotEqual(pixels[32, mid_y][3], 255, f'Expected unfilled pixel at x=32, y={mid_y}')

    def test_full_bar_at_100_percent_usage(self):
        """100% usage fills the entire bar regardless of time_pct."""
        # time_pct=60, pct=100 -> (100-60)/(100-60) = 1.0 -> full bar
        img = tray_icon_mod.create_icon_image(100, 100, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h
        pixels = img.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            self.assertEqual(pixels[S - 1, mid_y][3], 255, f'Expected fully filled bar at y={mid_y}')

    def test_mixed_modes_top_overage_bottom_utilization(self):
        """Top bar in overage mode, bottom bar in utilization mode produces valid image."""
        img = tray_icon_mod.create_icon_image(80, 50, mode_top='overage', mode_bottom='utilization', time_pct_top=60, time_pct_bottom=None)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')


class TestCreateStatusImage(unittest.TestCase):
    """Tests for create_status_image()."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_returns_64x64_rgba_image(self):
        """Status icon is always 64x64 RGBA."""
        img = tray_icon_mod.create_status_image('!')

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    @patch.object(tray_icon_mod, 'load_font')
    def test_uses_size_46_font(self, mock_font):
        """Status text uses size 46 font."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_status_image('?')

        mock_font.assert_called_with(46)

    def test_light_taskbar_variant(self):
        """Light taskbar produces a valid image."""
        img = tray_icon_mod.create_status_image('!', light_taskbar=True)

        self.assertEqual(img.size, (64, 64))


if __name__ == '__main__':
    unittest.main()
