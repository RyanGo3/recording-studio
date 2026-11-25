from __future__ import annotations

import io
from pathlib import Path
import sys
import tkinter as tk
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageColor, ImageTk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
from apple_ui import Card, Theme


class AppleUITests(unittest.TestCase):
    def setUp(self):
        app._enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.ui = app.RecorderApp(self.root)
        self.root.update()

    def tearDown(self):
        self.root.update()
        self.ui.on_close()
        self.assertFalse(self.errors, self.errors)

    def screen_mode(self):
        self.ui.mode_var.set("fullscreen")
        self.ui._refresh_mode()
        self.root.update()

    def test_defaults_and_screen_options(self):
        self.assertEqual(self.ui.audio_source_var.get(), "system")
        self.assertFalse(self.ui.audio_sources.enabled)
        self.screen_mode()
        self.assertTrue(self.ui.audio_sources.enabled)
        self.assertEqual(set(self.ui.audio_sources.buttons), {"system", "mixed", "microphone"})
        self.assertEqual(set(self.ui.zoom_menu.buttons), {"1.35", "1.65", "2.0"})
        self.assertEqual(self.ui.format_var.get(), "MP4 · 30 fps")
        self.assertTrue(self.ui.noise_reduction_var.get())
        self.assertTrue(self.ui.noise_reduction_switch.enabled)

    def test_recording_locks_configuration_but_allows_filename_editing(self):
        self.screen_mode()
        self.ui._set_controls(True)
        before = self.ui.auto_zoom_var.get()
        self.ui.noise_reduction_switch._toggle()
        self.assertTrue(self.ui.noise_reduction_var.get())
        self.assertFalse(self.ui.noise_reduction_switch.enabled)
        self.ui.effect_checks[0]._toggle()
        self.ui.modes._select("audio")
        self.ui.audio_sources._select("microphone")
        self.assertEqual(self.ui.auto_zoom_var.get(), before)
        self.assertEqual(self.ui.mode_var.get(), "fullscreen")
        self.assertEqual(self.ui.audio_source_var.get(), "system")
        self.assertEqual(self.ui.filename_entry.cget("state"), "normal")
        self.ui.filename_var.set("录制中输入的名称")
        self.assertEqual(self.ui.folder_entry.cget("state"), "disabled")
        self.ui._set_controls(False)
        self.assertTrue(self.ui.modes.enabled)
        self.assertEqual(self.ui.filename_entry.cget("state"), "normal")
        self.assertTrue(self.ui.noise_reduction_switch.enabled)

    def test_playback_track_click_drag_and_keyboard_seek(self):
        self.ui._show_page("edit")
        self.root.update()
        timeline = self.ui.progress_scale
        timeline.configure(to=100)
        self.ui.audio_duration = 100
        self.ui.media_kind = "video"
        timeline.set_enabled(True)
        width = timeline.winfo_width()
        with patch.object(self.ui, "_request_preview_frame"):
            timeline.event_generate("<ButtonPress-1>", x=width // 2, y=18)
            self.assertTrue(self.ui.slider_dragging)
            self.assertAlmostEqual(timeline.get(), 50, delta=1)
            timeline.event_generate("<B1-Motion>", x=width - 16, y=18)
            self.assertEqual(timeline.get(), 100)
            self.assertEqual(self.ui.player_time_var.get(), "00:01:40 / 00:01:40")
            timeline.event_generate("<ButtonRelease-1>", x=16, y=18)
            self.assertFalse(self.ui.slider_dragging)
            self.assertEqual(self.ui.player_base, 0)
            timeline._key(SimpleNamespace(keysym="Right"))
            self.assertEqual(self.ui.player_base, 5)
            timeline._key(SimpleNamespace(keysym="End"))
            self.assertEqual(self.ui.player_base, 100)
        self.assertEqual(float(timeline.itemcget("played", "width")), 10)
        thumb = timeline.coords("thumb")
        self.assertEqual(thumb[2] - thumb[0], 20)

    def test_disabled_track_does_not_seek(self):
        self.ui._show_page("edit")
        self.root.update()
        self.ui.progress_scale.event_generate("<ButtonPress-1>", x=150, y=18)
        self.assertEqual(self.ui.progress_scale.get(), 0)
        self.assertFalse(self.ui.slider_dragging)

    def artifact_folder(self):
        folder = ROOT / "test-artifacts" / f"playback_save_{uuid.uuid4().hex[:8]}"
        folder.mkdir(parents=True, exist_ok=False)
        return folder

    def test_recording_name_changes_preserve_existing_files_and_extension(self):
        folder = self.artifact_folder()
        original = folder / "original.mp3"
        original.write_bytes(b"recorded audio")
        (folder / "会议.mp3").write_bytes(b"existing")
        self.ui._recording_initial_name = ""
        self.ui.filename_var.set("会议.mp4")
        renamed = self.ui._finalize_recording_name(original)
        self.assertEqual(renamed.name, "会议_02.mp3")
        self.assertEqual(renamed.read_bytes(), b"recorded audio")
        self.assertEqual((folder / "会议.mp3").read_bytes(), b"existing")

    def test_blank_name_uses_recording_start_time(self):
        folder = self.artifact_folder()
        original = folder / "预先命名.mp4"
        original.write_bytes(b"recorded video")
        self.ui._recording_initial_name = "预先命名"
        self.ui._recording_stamp = "20261110_123456"
        self.ui.filename_var.set("")
        renamed = self.ui._finalize_recording_name(original)
        self.assertEqual(renamed.name, "系统录屏_20261110_123456.mp4")

    def test_stop_validates_name_and_locks_it_before_saving(self):
        recorder = Mock(thread=Mock())
        self.ui.recorder = recorder
        self.ui.state = "recording"
        self.ui.filename_var.set("无效/名称")
        with patch.object(app.messagebox, "showwarning") as warning:
            self.ui.stop_recording()
        warning.assert_called_once()
        recorder.stop.assert_not_called()
        self.ui.filename_var.set("有效名称")
        self.ui.stop_recording()
        recorder.stop.assert_called_once()
        self.assertEqual(self.ui.filename_entry.cget("state"), "disabled")
        self.ui.recorder = None
        self.ui.state = "idle"

    def test_stopped_event_opens_renamed_recording_and_remembers_folder(self):
        folder = self.artifact_folder()
        original = folder / "original.mp4"
        original.write_bytes(b"video")
        self.ui.filename_var.set("最后填写的名称")
        self.root.after_cancel(self.ui._poll_job)
        self.ui.events.put(("stopped", {"path": original, "reached_limit": False}))
        with patch.object(self.ui, "load_media") as load:
            self.ui._poll_events()
        expected = folder / "最后填写的名称.mp4"
        load.assert_called_once_with(expected)
        self.assertEqual(self.ui.last_recording_path, expected)
        self.assertEqual(self.ui.path_var.get(), f"已保存：{expected}")
        self.assertEqual(expected.read_bytes(), b"video")

    def test_failed_rename_keeps_saved_original(self):
        folder = self.artifact_folder()
        original = folder / "original.mp3"
        original.write_bytes(b"audio")
        self.root.after_cancel(self.ui._poll_job)
        self.ui.events.put(("stopped", {"path": original, "reached_limit": True}))
        with patch.object(self.ui, "_finalize_recording_name", side_effect=OSError("locked")), \
                patch.object(app.messagebox, "showwarning") as warning, \
                patch.object(self.ui, "load_media") as load:
            self.ui._poll_events()
        warning.assert_called_once()
        load.assert_called_once_with(original)
        self.assertEqual(original.read_bytes(), b"audio")

    def test_folder_buttons_follow_recording_and_current_media(self):
        folder = self.artifact_folder()
        self.ui.folder_var.set(str(folder / "新的保存位置"))
        with patch.object(app.os, "startfile") as launch:
            self.ui.open_recordings_folder()
            launch.assert_called_with(str((folder / "新的保存位置").resolve()))
            self.ui.last_recording_path = folder / "saved.mp3"
            self.ui.open_recordings_folder()
            launch.assert_called_with(str(folder.resolve()))
            self.ui.media_path = ROOT / "different.mp4"
            self.ui.open_media_folder()
            launch.assert_called_with(str(ROOT.resolve()))

    def test_hud_filename_is_shared_and_editing_prevents_collapse(self):
        hud = app.RecordingHUD(self.root, self.ui.duration_var, (0, 0, 640, 480),
                               lambda: None, lambda: None, self.ui.filename_var)
        try:
            self.root.update()
            hud.filename_entry.entry.insert(0, "录屏中命名")
            self.assertEqual(self.ui.filename_var.get(), "录屏中命名")
            self.assertLessEqual(hud.filename_entry.winfo_y() + hud.filename_entry.winfo_height(),
                                 hud.expanded_frame.winfo_height())
            with patch.object(hud.window, "focus_get", return_value=hud.filename_entry.entry), \
                    patch.object(hud, "_pointer_inside", return_value=False):
                hud._collapse_if_outside()
            self.assertTrue(hud.expanded)
        finally:
            hud.close()

    def test_noise_reduction_can_be_toggled_in_audio_and_video_modes(self):
        self.ui.noise_reduction_switch._toggle()
        self.assertFalse(self.ui.noise_reduction_var.get())
        self.screen_mode()
        self.ui.noise_reduction_switch._toggle()
        self.assertTrue(self.ui.noise_reduction_var.get())

    def test_switches_and_keyboard_bindings(self):
        self.screen_mode()
        switch = self.ui.effect_checks[0]
        self.assertTrue(switch.bind("<space>"))
        self.assertTrue(switch.bind("<Return>"))
        switch._toggle()
        self.assertFalse(self.ui.auto_zoom_var.get())
        switch._toggle()
        self.assertTrue(self.ui.auto_zoom_var.get())
        self.ui.zoom_menu._select("2.0")
        self.assertEqual(self.ui.zoom_var.get(), "2.0")
        self.assertTrue(self.ui.modes.buttons["region"].bind("<Right>"))

    def test_card_background_covers_padding_and_has_rounded_corners(self):
        stack = [self.ui.record_page]
        cards = []
        while stack:
            widget = stack.pop()
            if isinstance(widget, Card):
                cards.append(widget)
                self.assertEqual(widget._surface.winfo_x(), 0)
                self.assertEqual(widget._surface.winfo_y(), 0)
                self.assertEqual(widget._surface.winfo_width(), widget.winfo_width())
                self.assertEqual(widget._surface.winfo_height(), widget.winfo_height())
                photo = ImageTk.getimage(widget._photo).convert("RGB")
                self.assertEqual(photo.getpixel((0, 0)), ImageColor.getrgb(Theme.bg))
            stack.extend(widget.winfo_children())
        self.assertEqual(len(cards), 3)

    def test_softening_slider_pointer_keyboard_bounds_and_busy_lock(self):
        self.ui._show_page("soften")
        self.root.update()
        page = self.ui.soften_page
        slider = page.strength_control
        slider._seek(SimpleNamespace(x=-100))
        self.assertEqual(page.strength.get(), 15)
        slider._seek(SimpleNamespace(x=slider.track.winfo_width()+100))
        self.assertEqual(page.strength.get(), 100)
        slider._key(-1)
        self.assertEqual(page.strength.get(), 99)
        self.assertTrue(slider.track.bind("<Right>"))
        page._set_running(True)
        slider._seek(SimpleNamespace(x=0))
        slider._key(-1)
        self.assertEqual(page.strength.get(), 99)
        page._set_running(False)

    def test_editor_controls_reachable_and_breadcrumb_tracks_page(self):
        self.ui._show_page("edit")
        for geometry in ("1160x860", "1040x700"):
            self.root.geometry(geometry)
            self.root.update()
            bbox = self.ui.edit_canvas.bbox("all")
            if bbox[3] > self.ui.edit_canvas.winfo_height() + 20:
                self.ui.edit_canvas.yview_moveto(1)
                self.root.update()
                top, bottom = self.ui.edit_canvas.yview()
                self.assertGreater(top, 0)
                self.assertAlmostEqual(bottom, 1, places=2)
            else:
                self.assertLessEqual(bbox[3], self.ui.edit_canvas.winfo_height() + 20)
        self.assertEqual(self.ui.breadcrumb_var.get(), "工作空间  /  裁剪与导出")
        self.assertTrue(self.ui.nav_edit.active)
        self.assertFalse(self.ui.nav_record.active)

    def test_preview_preserves_aspect_ratio_and_resizes(self):
        self.ui._show_page("edit")
        self.root.update()
        payload = io.BytesIO()
        Image.new("RGB", (800, 400), "blue").save(payload, format="PNG")
        self.ui._set_preview_image(payload.getvalue())
        self.assertLessEqual(self.ui._preview_photo.width(), self.ui.preview_canvas.winfo_width())
        self.assertLessEqual(self.ui._preview_photo.height(), self.ui.preview_canvas.winfo_height())
        self.assertAlmostEqual(self.ui._preview_photo.width() / self.ui._preview_photo.height(), 2, delta=0.01)
        self.root.geometry("1040x700")
        self.root.update()
        self.assertLessEqual(self.ui._preview_photo.width(), self.ui.preview_canvas.winfo_width())

    def test_small_window_scrolls_and_status_badges_update(self):
        self.root.geometry("1040x700")
        self.root.update()
        self.ui.record_canvas.yview_moveto(1)
        self.root.update()
        self.assertGreater(self.ui.record_canvas.yview()[0], 0)
        self.ui._set_chip("PAUSED", Theme.warning, Theme.warning_soft)
        self.assertEqual(self.ui.header_chip.text, "已暂停")
        self.ui._set_chip("REC", "white", Theme.record)
        self.assertEqual(self.ui.header_chip.text, "录制中")

    def test_file_picker_uses_light_theme_and_closes(self):
        dialog = app.PathPickerDialog(self.root, "选择保存位置", ROOT, lambda _path: None)
        self.root.update()
        self.assertEqual(dialog.listbox.cget("background"), Theme.card)
        self.assertTrue(dialog.confirm_button._enabled)
        dialog.close()
        self.root.update()
        self.assertIsNone(self.root.grab_current())


if __name__ == "__main__":
    unittest.main()
