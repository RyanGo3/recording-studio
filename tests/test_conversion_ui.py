from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
import tkinter as tk
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
from audio_softening import Cancelled, ProcessingError


class ConversionUITests(unittest.TestCase):
    def setUp(self):
        app._enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.ui = app.RecorderApp(self.root)
        self.folder = ROOT / "test-artifacts" / ("conversion_ui_" + uuid.uuid4().hex[:8])
        self.folder.mkdir(parents=True)
        self.source = self.folder / "source.mp4"
        self.source.write_bytes(b"source")
        self.ui.folder_var.set(str(self.folder))
        self.root.update()

    def tearDown(self):
        for page in (self.ui.audio_convert_page, self.ui.compress_page, self.ui.soften_page):
            page.running = False
        self.ui.on_close()
        self.assertFalse(self.errors, self.errors)

    def wait(self, page):
        deadline = time.monotonic() + 3
        while page.running and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(page.running)

    def result(self, smaller=True):
        return {"export_path": str(self.folder / "result.mp4"), "source_bytes": 1000,
                "output_bytes": 500 if smaller else 1100, "saved_percent": 50 if smaller else -10,
                "smaller": smaller}

    def test_both_navigation_entries_current_file_and_small_window_scroll(self):
        self.ui.media_path = self.source
        for operation, key, title in (("audio", "audio_convert", "视频转音频"),
                                      ("compress", "compress", "视频压缩")):
            with self.subTest(operation=operation):
                self.ui.open_conversion_page(operation, use_current=True)
                page = self.ui.audio_convert_page if operation == "audio" else self.ui.compress_page
                self.assertEqual(page.files, [self.source])
                self.assertEqual(self.ui.page_var.get(), key)
                self.assertEqual(self.ui.breadcrumb_var.get(), "工作空间  /  " + title)
                self.root.geometry("1040x700")
                self.root.update()
                canvas = getattr(self.ui, operation + "_convert_canvas")
                canvas.yview_moveto(1)
                self.root.update()
                self.assertAlmostEqual(canvas.yview()[1], 1, places=2)
        self.assertEqual(self.ui.compress_page.codec.get(), "h265")
        self.assertEqual(self.ui.compress_page.quality.get(), "clear")
        self.ui.export_audio()
        self.assertEqual(self.ui.page_var.get(), "audio_convert")

    def test_queue_deduplicates_and_remove_does_not_delete_video(self):
        page = self.ui.compress_page
        page.add_files([self.source, self.source])
        self.assertEqual(page.files, [self.source])
        page.remove_selected()
        self.assertEqual(page.files, [])
        self.assertEqual(self.source.read_bytes(), b"source")

    def test_batch_continues_after_error_and_displays_larger_result_honestly(self):
        page = self.ui.compress_page
        second = self.folder / "second.mp4"
        second.write_bytes(b"second")
        page.add_files([self.source, second])
        page._set_running(True)
        with patch("conversion_ui.process_media",
                   side_effect=[ProcessingError("文件损坏"), self.result(smaller=False)]):
            page._worker(tuple(page.files), self.folder, {}, threading.Event())
        self.wait(page)
        self.assertIn("失败", page.labels[self.source])
        self.assertIn("未缩小", page.labels[second])
        self.assertIn("成功 1，失败 1", page.status.get())
        self.assertEqual(page.progress.get(), 100)
        page.file_list.selection_clear(0, "end")
        page.file_list.selection_set(1)
        page._refresh_result()
        self.assertIn("本次未缩小", page.result_text.get())
        self.assertTrue(page.open_button._enabled)

    def test_busy_jobs_exclude_each_other_and_recording_and_close_cancels(self):
        self.ui.compress_page._set_running(True)
        self.ui.audio_convert_page.add_files([self.source])
        self.ui.audio_convert_page.start()
        self.assertFalse(self.ui.audio_convert_page.running)
        self.ui.soften_page.add_files([self.source])
        self.ui.soften_page.start()
        self.assertFalse(self.ui.soften_page.running)
        with patch.object(app.messagebox, "showinfo") as info:
            self.ui.start_recording()
        info.assert_called_once()
        self.ui.on_close()
        self.assertTrue(self.ui.compress_page.cancel_event.is_set())
        self.assertTrue(self.root.winfo_exists())
        self.assertTrue(self.ui.close_after_save)

    def test_worker_cancellation_retains_completed_output_and_stops_queue(self):
        page = self.ui.audio_convert_page
        second = self.folder / "second.mp4"
        second.write_bytes(b"second")
        page.add_files([self.source, second])
        page._set_running(True)
        with patch("conversion_ui.process_media",
                   side_effect=[self.result(), Cancelled("已取消，未完成文件位置")]) as process:
            page._worker(tuple(page.files), self.folder, {}, threading.Event())
        self.wait(page)
        self.assertEqual(process.call_count, 2)
        self.assertIn(self.source, page.results)
        self.assertNotIn(second, page.results)
        self.assertIn("已取消", page.status.get())

    def test_start_captures_inputs_options_and_custom_directory(self):
        page = self.ui.audio_convert_page
        page.add_files([self.source])
        page.custom_folder = self.folder / "custom"
        page.format.set("flac")
        with patch("conversion_ui.tool", return_value="ffmpeg"), \
                patch("conversion_ui.process_media", return_value=self.result()) as process:
            page.start()
            page.worker.join(2)
            self.wait(page)
        self.assertEqual(process.call_args.args[0], self.source)
        self.assertEqual(process.call_args.args[1], page.custom_folder)
        self.assertEqual(process.call_args.args[2:4], ("audio", {"format": "flac"}))
        self.assertTrue(page.custom_folder.is_dir())


if __name__ == "__main__":
    unittest.main()
