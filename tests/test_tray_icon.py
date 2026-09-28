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
        img = tray_icon_mod.create_icon_image([0, 0])

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_low_usage_renders_without_error(self):
        """Usage <= 50% renders successfully."""
        img = tray_icon_mod.create_icon_image([30, 20])

        self.assertEqual(img.size, (64, 64))

    def test_high_usage_renders_without_error(self):
        """Usage > 50% renders successfully."""
        img = tray_icon_mod.create_icon_image([75, 20])

        self.assertEqual(img.size, (64, 64))

    def test_full_usage_renders_without_error(self):
        """Usage >= 100% renders successfully."""
        img = tray_icon_mod.create_icon_image([100, 20])

        self.assertEqual(img.size, (64, 64))

    def test_dark_and_light_taskbar_produce_different_images(self):
        """Dark vs light taskbar produces different pixel data (different track colours)."""
        img_dark = tray_icon_mod.create_icon_image([50, 50], light_taskbar=False)
        img_light = tray_icon_mod.create_icon_image([50, 50], light_taskbar=True)

        self.assertEqual(img_dark.size, (64, 64))
        self.assertEqual(img_light.size, (64, 64))
        self.assertNotEqual(img_dark.tobytes(), img_light.tobytes())

    def test_zero_usage_renders_full_ring(self):
        """0 % usage renders a full ring (maximum coloured arc)."""
        img = tray_icon_mod.create_icon_image([0])
        self.assertEqual(img.size, (64, 64))

    def test_usage_percent_changes_icon(self):
        """Different usage levels produce visually distinct icons."""
        img_full = tray_icon_mod.create_icon_image([100])
        img_zero = tray_icon_mod.create_icon_image([0])
        self.assertNotEqual(img_full.tobytes(), img_zero.tobytes())

    def test_single_ring_vs_double_ring_differ(self):
        """A one-provider icon differs from a two-provider icon."""
        img_single = tray_icon_mod.create_icon_image([50])
        img_double = tray_icon_mod.create_icon_image([50, 50])
        self.assertNotEqual(img_single.tobytes(), img_double.tobytes())

    def test_inner_ring_usage_changes_icon(self):
        """The second provider's ring changes the icon independently."""
        img_low  = tray_icon_mod.create_icon_image([50, 0])
        img_high = tray_icon_mod.create_icon_image([50, 90])
        self.assertNotEqual(img_low.tobytes(), img_high.tobytes())

    def test_no_font_calls_for_ring_icon(self):
        """Ring rendering does not call load_font (no text drawn)."""
        with patch.object(tray_icon_mod, 'load_font') as mock_font:
            tray_icon_mod.create_icon_image([30, 20])
            mock_font.assert_not_called()

    def test_warn_threshold_changes_colour(self):
        """Usage ≥ 80 % produces a different icon colour than usage < 80 %."""
        img_normal = tray_icon_mod.create_icon_image([79])
        img_warn   = tray_icon_mod.create_icon_image([80])
        self.assertNotEqual(img_normal.tobytes(), img_warn.tobytes())

    def test_crit_threshold_changes_colour(self):
        """Usage ≥ 95 % produces a different icon colour than usage < 95 %."""
        img_warn = tray_icon_mod.create_icon_image([94])
        img_crit = tray_icon_mod.create_icon_image([95])
        self.assertNotEqual(img_warn.tobytes(), img_crit.tobytes())

    def test_three_rings_render(self):
        """Three providers produce a valid icon."""
        img = tray_icon_mod.create_icon_image([10, 50, 90])
        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_each_ring_count_has_its_own_layout(self):
        """One, two, and three providers each render differently."""
        images = [tray_icon_mod.create_icon_image([50] * count).tobytes() for count in (1, 2, 3)]
        self.assertEqual(len(set(images)), 3)

    def test_third_ring_usage_changes_icon(self):
        """The third provider's ring changes the icon independently."""
        img_low = tray_icon_mod.create_icon_image([50, 50, 10])
        img_high = tray_icon_mod.create_icon_image([50, 50, 90])
        self.assertNotEqual(img_low.tobytes(), img_high.tobytes())

    def test_three_rings_stay_inside_the_canvas(self):
        """Rings never touch the icon border, so the tray keeps its padding."""
        img = tray_icon_mod.create_icon_image([0, 0, 0])
        border = (
            [img.getpixel((x, 0)) for x in range(64)]
            + [img.getpixel((x, 63)) for x in range(64)]
            + [img.getpixel((0, y)) for y in range(64)]
            + [img.getpixel((63, y)) for y in range(64)]
        )
        self.assertTrue(all(pixel[3] == 0 for pixel in border))

    def test_more_providers_than_rings_falls_back_to_widest_layout(self):
        """A fourth provider does not crash; the known layout is reused."""
        img = tray_icon_mod.create_icon_image([10, 20, 30, 40])
        self.assertEqual(img.size, (64, 64))

    def test_empty_list_renders_blank_icon(self):
        """No providers with data renders an empty (fully transparent) icon."""
        img = tray_icon_mod.create_icon_image([])
        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.getextrema()[3], (0, 0))


def _coloured(pixel: tuple[int, int, int, int]) -> bool:
    """True for an opaque status colour (blue, orange, red, green), False for the grey track or transparency."""
    return pixel[3] > 150 and max(pixel[:3]) - min(pixel[:3]) > 60


class TestIconStyles(unittest.TestCase):
    """Tests for the bars, rings and number styles of create_icon_image()."""

    STYLES = ('bars', 'rings', 'number')

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()
        self._font = patch.object(tray_icon_mod, 'load_font', side_effect=lambda size, symbol=False: _real_font())
        self._font.start()

    def tearDown(self):
        self._font.stop()
        tray_icon_mod.load_font.cache_clear()

    def test_every_style_is_a_64px_rgba_image(self):
        for style in self.STYLES:
            with self.subTest(style=style):
                image = tray_icon_mod.create_icon_image([10, 50, 90], style=style)
                self.assertEqual((image.size, image.mode), ((64, 64), 'RGBA'))

    def test_styles_look_different(self):
        images = {tray_icon_mod.create_icon_image([30, 60], style=style).tobytes() for style in self.STYLES}

        self.assertEqual(len(images), 3)

    def test_unknown_style_falls_back_to_bars(self):
        bars = tray_icon_mod.create_icon_image([30, 60], style='bars')
        unknown = tray_icon_mod.create_icon_image([30, 60], style='sparkles')

        self.assertEqual(bars.tobytes(), unknown.tobytes())

    def test_bars_fill_from_the_bottom(self):
        image = tray_icon_mod.create_icon_image([50], style='bars')

        self.assertTrue(_coloured(image.getpixel((32, 55))))
        self.assertFalse(_coloured(image.getpixel((32, 10))))

    def test_bar_with_little_usage_stays_visible(self):
        image = tray_icon_mod.create_icon_image([1], style='bars')

        self.assertTrue(_coloured(image.getpixel((32, 58))))

    def test_zero_usage_shows_only_tracks(self):
        for style in ('bars', 'rings'):
            with self.subTest(style=style):
                image = tray_icon_mod.create_icon_image([0, 0], style=style)
                self.assertFalse(any(_coloured(pixel) for pixel in image.getdata()))

    def test_rings_fill_the_used_share_clockwise_from_the_top(self):
        image = tray_icon_mod.create_icon_image([25], style='rings')

        self.assertTrue(_coloured(image.getpixel((48, 16))))
        self.assertFalse(_coloured(image.getpixel((16, 16))))

    def test_ring_at_95_percent_or_more_turns_solid_red(self):
        almost = tray_icon_mod.create_icon_image([95], style='rings')
        full = tray_icon_mod.create_icon_image([100], style='rings')

        self.assertEqual(almost.tobytes(), full.tobytes())
        self.assertTrue(_coloured(almost.getpixel((16, 16))))

    def test_bars_and_rings_stay_inside_the_canvas(self):
        for style in ('bars', 'rings'):
            with self.subTest(style=style):
                image = tray_icon_mod.create_icon_image([100, 100, 100], style=style)
                border = [image.getpixel((x, y)) for x in range(64) for y in (0, 63)] + [image.getpixel((x, y)) for y in range(64) for x in (0, 63)]
                self.assertTrue(all(pixel[3] == 0 for pixel in border))

    def test_more_bars_than_the_usual_three(self):
        image = tray_icon_mod.create_icon_image([10, 20, 30, 40], style='bars')

        self.assertEqual(image.size, (64, 64))

    def test_number_shows_the_highest_percentage(self):
        with patch.object(tray_icon_mod, '_draw_centered_text') as mock_text:
            tray_icon_mod.create_icon_image([55, 97, 20], style='number')

        self.assertEqual(mock_text.call_args.args[1], '97')
        self.assertEqual(mock_text.call_args.args[2], tray_icon_mod._STATUS_CRIT)

    def test_number_uses_the_taskbar_colour_below_the_warning_level(self):
        with patch.object(tray_icon_mod, '_draw_centered_text') as mock_text:
            tray_icon_mod.create_icon_image([12.4], light_taskbar=False, style='number')

        self.assertEqual(mock_text.call_args.args[1], '12')
        self.assertEqual(mock_text.call_args.args[2], tray_icon_mod.ICON_LIGHT['fg'])

    def test_number_shows_an_exclamation_mark_at_the_limit(self):
        with patch.object(tray_icon_mod, '_draw_centered_text') as mock_text:
            tray_icon_mod.create_icon_image([100, 40], style='number')

        self.assertEqual(mock_text.call_args.args[1], '!')

    def test_number_without_providers_shows_zero(self):
        with patch.object(tray_icon_mod, '_draw_centered_text') as mock_text:
            tray_icon_mod.create_icon_image([], style='number')

        self.assertEqual(mock_text.call_args.args[1], '0')


class TestCountdownAndReadyImages(unittest.TestCase):
    """Tests for the countdown and check-mark icons."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_countdown_writes_the_time_left_on_a_red_disc(self):
        with patch.object(tray_icon_mod, '_draw_centered_text') as mock_text:
            image = tray_icon_mod.create_countdown_image('47')

        self.assertEqual(mock_text.call_args.args[1:3], ('47', tray_icon_mod._WHITE))
        pixel = image.getpixel((32, 4))
        self.assertTrue(_coloured(pixel) and pixel[0] > pixel[2])
        self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_countdown_text_renders_with_a_real_font(self):
        with patch.object(tray_icon_mod, 'load_font', side_effect=lambda size, symbol=False: _real_font()):
            image = tray_icon_mod.create_countdown_image('23h')

        self.assertEqual((image.size, image.mode), ((64, 64), 'RGBA'))

    def test_ready_is_a_check_mark_on_a_green_disc(self):
        image = tray_icon_mod.create_ready_image()

        pixel = image.getpixel((32, 6))
        self.assertTrue(_coloured(pixel) and pixel[1] > pixel[0] and pixel[1] > pixel[2])
        self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_ready_draws_no_text(self):
        with patch.object(tray_icon_mod, 'load_font') as mock_font:
            tray_icon_mod.create_ready_image()

        mock_font.assert_not_called()


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
