"""Portable regression checks for macOS routing and packaging (no native capture)."""
from __future__ import annotations

import ast
import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import audio_sources
import macos_capture
from scripts import build_macos


class MacCaptureTests(unittest.TestCase):
    def test_retina_capture_scales_before_crop_and_selects_screen(self):
        with patch.object(macos_capture, "main_display", return_value=(0, 0, 1440, 900)), \
             patch.object(macos_capture, "screen_index", return_value=2):
            args = macos_capture.capture_arguments((100, 80, 640, 480), 30, False)
        self.assertEqual(args[args.index("-i") + 1], "Capture screen 2:none")
        self.assertEqual(args[args.index("-capture_cursor") + 1], "0")
        self.assertEqual(args[args.index("-vf") + 1],
                         "scale=1440:900,crop=640:480:100:80,setsar=1")
        self.assertNotIn("gdigrab", args)

    def test_cross_display_region_is_rejected(self):
        with patch.object(macos_capture, "main_display", return_value=(0, 0, 1440, 900)):
            for region in ((-10, 0, 640, 480), (1200, 600, 640, 480), (0, 0, 0, 0)):
                with self.subTest(region=region), self.assertRaises(ValueError):
                    macos_capture.capture_arguments(region, 30, True)

    def test_main_display_is_not_assumed_to_be_first(self):
        api = Mock()
        api.CGMainDisplayID.return_value = 42

        def displays(_limit, values, count):
            values[0], values[1] = 11, 42
            ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = 2
            return 0

        api.CGGetActiveDisplayList.side_effect = displays
        with patch.object(macos_capture, "quartz", return_value=api):
            self.assertEqual(macos_capture.screen_index(), 1)

    def test_permission_denial_requests_access_and_explains_restart(self):
        api = Mock()
        api.CGPreflightScreenCaptureAccess.return_value = False
        with patch.object(macos_capture, "quartz", return_value=api):
            with self.assertRaisesRegex(RuntimeError, "退出并重新打开"):
                macos_capture.require_screen_permission()
        api.CGRequestScreenCaptureAccess.assert_called_once()

    def test_granted_permission_does_not_prompt(self):
        api = Mock()
        api.CGPreflightScreenCaptureAccess.return_value = True
        with patch.object(macos_capture, "quartz", return_value=api):
            macos_capture.require_screen_permission()
        api.CGRequestScreenCaptureAccess.assert_not_called()

    def test_cursor_event_is_released_even_if_read_fails(self):
        api, cf = Mock(), Mock()
        api.CGEventCreate.return_value = 123
        api.CGEventGetLocation.side_effect = RuntimeError("event failed")
        with patch.object(macos_capture, "quartz", return_value=api), \
             patch.object(macos_capture, "core_foundation", return_value=cf):
            with self.assertRaises(RuntimeError):
                macos_capture.cursor_state()
        cf.CFRelease.assert_called_once_with(123)


class MacAudioTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch.object(audio_sources.sys, "platform", "darwin")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        # Mac microphones need not expose the Windows loopback property.
        self.blackhole = SimpleNamespace(name="BlackHole 2ch", id="bh")
        self.microphone = SimpleNamespace(name="MacBook Pro Microphone", id="mic")

    def test_system_and_microphone_are_independent_tracks(self):
        with patch.object(audio_sources.sc, "all_microphones", return_value=[self.blackhole]), \
             patch.object(audio_sources.sc, "default_microphone", return_value=self.microphone):
            self.assertEqual(audio_sources.resolve_sources("mixed"),
                             [("system", self.blackhole), ("microphone", self.microphone)])

    def test_missing_blackhole_never_records_microphone_as_system_audio(self):
        with patch.object(audio_sources.sc, "all_microphones", return_value=[self.microphone]):
            with self.assertRaisesRegex(RuntimeError, "BlackHole 2ch"):
                audio_sources.resolve_sources("system")

    def test_microphone_only_does_not_require_blackhole(self):
        with patch.object(audio_sources.sc, "all_microphones") as enumeration, \
             patch.object(audio_sources.sc, "default_microphone", return_value=self.microphone):
            self.assertEqual(audio_sources.resolve_sources("microphone"),
                             [("microphone", self.microphone)])
        enumeration.assert_not_called()

    def test_mixed_rejects_blackhole_as_default_microphone(self):
        with patch.object(audio_sources.sc, "all_microphones", return_value=[self.blackhole]), \
             patch.object(audio_sources.sc, "default_microphone", return_value=self.blackhole):
            with self.assertRaisesRegex(RuntimeError, "实际麦克风"):
                audio_sources.resolve_sources("mixed")


class MacBuildTests(unittest.TestCase):
    def test_spec_collects_tools_and_declares_permission_strings(self):
        root = Path(__file__).resolve().parents[1]
        spec = build_macos.spec_source(root / "builds" / "test",
                                       {"ffmpeg": root / "tools" / "ffmpeg",
                                        "ffprobe": root / "tools" / "ffprobe"}, "2.1.1")
        ast.parse(spec)
        for text in ("BUNDLE(", "ffmpeg", "ffprobe", "NSMicrophoneUsageDescription",
                     "macos_capture", "com.ryango3.potatofactory", "console=False"):
            self.assertIn(text, spec)

    def test_spec_records_build_hosts_minimum_system_version(self):
        root = Path(__file__).resolve().parents[1]
        spec = build_macos.spec_source(root, {}, "2.1.1", "26.0")
        self.assertIn("'LSMinimumSystemVersion': '26.0'", spec)

    def test_foreign_ffmpeg_architecture_is_rejected(self):
        with patch.object(build_macos.shutil, "which", return_value="/opt/homebrew/bin/ffmpeg"), \
             patch.object(build_macos, "checked_output", return_value="x86_64\n"):
            with self.assertRaisesRegex(SystemExit, "架构"):
                build_macos.find_tools("arm64")

    def test_windows_cannot_silently_produce_a_fake_macos_app(self):
        with patch.object(build_macos.sys, "platform", "win32"):
            with self.assertRaisesRegex(SystemExit, "Mac"):
                build_macos.main()


class MacIntegrationTests(unittest.TestCase):
    def test_app_geometry_and_default_output_use_macos_conventions(self):
        import app
        with patch.object(app.sys, "platform", "darwin"), \
             patch.object(macos_capture, "main_display", return_value=(0, 0, 1440, 900)):
            self.assertEqual(app.virtual_screen(), (0, 0, 1440, 900))
            self.assertEqual(app.primary_monitor_rect(), (0, 0, 1440, 900))
            with patch.object(Path, "is_dir", return_value=False):
                self.assertEqual(app.default_output_folder(), Path.home() / "Movies" / app.APP_TITLE)

    def test_trackpad_small_deltas_actually_scroll(self):
        import app
        canvas = Mock()
        ui = SimpleNamespace(page_var=Mock(get=lambda: "record"),
                             record_canvas=canvas, edit_canvas=canvas, soften_canvas=canvas,
                             audio_convert_canvas=canvas, compress_convert_canvas=canvas)
        with patch.object(app.sys, "platform", "darwin"):
            app.RecorderApp._scroll_record_page(ui, SimpleNamespace(delta=2))
        canvas.yview_scroll.assert_called_once_with(-2, "units")

    def test_system_audio_only_uses_the_same_mac_source_routing(self):
        import app
        microphone = SimpleNamespace(name="BlackHole 2ch")
        with patch.object(app.sys, "platform", "darwin"), \
             patch.object(app, "resolve_sources", return_value=[("system", microphone)]) as resolve:
            self.assertEqual(app.SystemAudioRecorder.find_loopback_microphone(),
                             (microphone, microphone))
        resolve.assert_called_once_with("system")


if __name__ == "__main__":
    unittest.main()
