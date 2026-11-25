from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
import tkinter as tk
import unittest
import uuid
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
import audio_softening as engine


class SofteningUITests(unittest.TestCase):
    def setUp(self):
        app._enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.ui = app.RecorderApp(self.root)
        self.page = self.ui.soften_page
        self.folder = ROOT / "test-artifacts" / ("softening_ui_" + uuid.uuid4().hex[:8])
        self.folder.mkdir(parents=True)
        self.ui.folder_var.set(str(self.folder))
        self.root.update()

    def tearDown(self):
        self.page.running = False
        self.ui.on_close()
        self.assertFalse(self.errors, self.errors)

    def source(self, name="source.mp3"):
        path = self.folder / name
        path.write_bytes(b"source")
        return path

    def result(self, name="softened.mp3"):
        return {"export_path": str(self.folder / name), "original_preview": "original.mp3",
                "softened_preview": "softened.mp3", "region_count": 2,
                "affected_seconds": 3., "preview_start": 1., "preview_duration": 5.}

    def test_navigation_current_media_and_small_window_scroll(self):
        source = self.source("recording.mp4")
        self.ui.media_path = source
        self.ui.soften_current_media()
        self.root.geometry("1040x700")
        self.root.update()
        self.assertEqual(self.page.files, [source])
        self.assertEqual(self.ui.page_var.get(), "soften")
        self.assertEqual(self.ui.breadcrumb_var.get(), "工作空间  /  声音柔化")
        self.assertTrue(self.ui.nav_soften.active)
        self.assertFalse(self.ui.nav_edit.active)
        self.ui.soften_canvas.yview_moveto(1)
        self.root.update()
        self.assertAlmostEqual(self.ui.soften_canvas.yview()[1], 1, places=2)
        self.assertGreater(self.ui.soften_canvas.yview()[0], 0)
        self.assertEqual(self.page.mode.get(), "high")
        self.page.mode_control._select("whole")
        self.page.format_control._select("flac")
        self.assertEqual(self.page.mode.get(), "whole")
        self.assertEqual(self.page.format.get(), "flac")

    def test_queue_deduplicates_and_remove_keeps_source(self):
        source = self.source()
        invalid = self.source("other.txt")
        self.page.add_files([source, source, invalid])
        self.assertEqual(self.page.files, [source])
        self.page.remove_selected()
        self.assertEqual(self.page.files, [])
        self.assertEqual(source.read_bytes(), b"source")
        self.assertTrue(invalid.is_file())

    def test_batch_continues_after_missing_target_and_reports_results(self):
        first, second = self.source("low.wav"), self.source("high.wav")
        self.page.add_files([first, second])
        result = self.result()
        self.page._set_running(True)
        with patch.object(engine, "process",
                          side_effect=[engine.NoTargetFound("没有检测到高音"), result]):
            self.page._worker((first, second), self.folder, {}, threading.Event())
        deadline = time.monotonic() + 3
        while self.page.running and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.page.running)
        self.assertIn("失败：", self.page.labels[first])
        self.assertEqual(self.page.labels[second], "已完成")
        self.assertEqual(self.page.results[second], result)
        self.assertIn("成功 1，失败 1", self.page.status.get())
        self.assertIn("没有检测到高音", self.page.result_info.get())
        self.page.file_list.selection_clear(0, "end")
        self.page.file_list.selection_set(1)
        self.page._refresh_result()
        self.assertTrue(self.page.original_button._enabled)
        self.assertTrue(self.page.edit_button._enabled)
        self.page.results[second] = self.result("softened.flac")
        self.page._refresh_result()
        self.assertFalse(self.page.edit_button._enabled)

    def test_running_locks_options_and_blocks_recording(self):
        self.page._set_running(True)
        self.assertFalse(self.page.start_button._enabled)
        self.assertFalse(self.page.mode_control.enabled)
        with patch.object(app.messagebox, "showinfo") as info:
            self.ui.start_recording()
        info.assert_called_once()
        self.assertIsNone(self.ui.recorder)
        self.page.cancel()
        self.assertTrue(self.page.cancel_event.is_set())

    def test_close_cancels_worker_and_waits_for_finished_event(self):
        self.page._set_running(True)
        self.ui.on_close()
        self.assertTrue(self.page.cancel_event.is_set())
        self.assertTrue(self.ui.close_after_save)
        self.assertTrue(self.root.winfo_exists())
        self.ui.close_after_save = False
        self.page.handle_event("soften_finished", "已取消")
        self.assertFalse(self.page.running)

    def test_comparison_stops_previous_preview_and_opens_mp3_in_editor(self):
        source = self.source()
        self.page.add_files([source])
        result = self.result()
        self.page.results[source] = result
        channel = Mock()
        sound = Mock()
        sound.play.return_value = channel
        with patch.object(self.ui, "_ensure_mixer"), patch("softening_ui.pygame.mixer.Sound",
                                                         return_value=sound) as load:
            self.page.play_preview("original_preview")
            load.assert_called_with(result["original_preview"])
            self.page.play_preview("softened_preview")
            channel.stop.assert_called_once()
            load.assert_called_with(result["softened_preview"])
        with patch.object(self.ui, "load_media") as load_media:
            self.page.open_in_editor()
        load_media.assert_called_once_with(Path(result["export_path"]))
        self.assertEqual(self.ui.page_var.get(), "edit")


if __name__ == "__main__":
    unittest.main()
