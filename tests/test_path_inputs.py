from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import time
import tkinter as tk
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
from path_inputs import collect_files, parse_paths, single_path


class PathParsingTests(unittest.TestCase):
    def test_quotes_spaces_underscores_and_multiline(self):
        first = ROOT / "中文 空格_目录" / "素材 A.mp4"
        second = ROOT / "另一个 文件.mkv"
        self.assertEqual(parse_paths(f'"{first}" "{second}"\n"{first}"'), [first, second])
        self.assertEqual(parse_paths(str(first)), [first])
        self.assertEqual(single_path('"片段_素材.mp4"', ROOT), ROOT / "片段_素材.mp4")
        with self.assertRaises(ValueError):
            single_path(f'"{first}" "{second}"')
        with self.assertRaises(ValueError):
            single_path("bad\0path")

    def test_environment_variable_expansion(self):
        with patch.dict(os.environ, {"POTATO_PATH_TEST": str(ROOT)}):
            self.assertEqual(single_path(r"%POTATO_PATH_TEST%\中文_视频.mp4"),
                             ROOT / "中文_视频.mp4")

    def test_folder_expansion_filters_deduplicates_and_reports(self):
        folder = ROOT / "test-artifacts" / ("paths_" + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True)
        video = folder / "中文_视频 A.mp4"
        video.write_bytes(b"fixture")
        (folder / "说明.txt").write_text("fixture", encoding="utf-8")
        sub = folder / "子目录"
        sub.mkdir()
        (sub / "不递归.mp4").write_bytes(b"fixture")
        files, problems = collect_files([folder, video, folder / "不存在.mp4"], {".mp4"})
        self.assertEqual(files, [video])
        self.assertEqual(len(problems), 1)
        self.assertTrue(video.is_file())


class PathInputUITests(unittest.TestCase):
    def setUp(self):
        app._enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.ui = app.RecorderApp(self.root)
        self.folder = ROOT / "test-artifacts" / ("path_ui_" + uuid.uuid4().hex[:8])
        self.folder.mkdir(parents=True)
        self.source = self.folder / "中文_视频 带空格.mp4"
        self.source.write_bytes(b"fixture")
        self.ui.folder_var.set(str(self.folder))
        self.root.update()

    def tearDown(self):
        for page in (self.ui.soften_page, self.ui.audio_convert_page, self.ui.compress_page):
            page.running = False
        self.ui.on_close()
        self.assertFalse(self.errors, self.errors)

    def test_brand_and_author_visible(self):
        self.assertEqual(self.root.title(), "土豆工厂")
        widgets = [self.root]
        texts = []
        while widgets:
            widget = widgets.pop()
            widgets.extend(widget.winfo_children())
            if isinstance(widget, tk.Label):
                texts.append(widget.cget("text"))
        self.assertIn("大土豆|Ryan", texts)

    def test_picker_confirm_uses_pasted_directory_without_go(self):
        selected = []
        dialog = app.PathPickerDialog(self.root, "保存", ROOT, selected.append)
        dialog.address_var.set(f'"{self.folder}"')
        dialog.confirm()
        self.assertEqual(selected, [self.folder])
        self.assertIsNone(self.root.grab_current())

    def test_picker_pasted_file_and_relative_directory(self):
        selected = []
        dialog = app.PathPickerDialog(self.root, "打开", ROOT, selected.append, "media")
        dialog.address_var.set(f'"{self.source}"')
        self.assertTrue(dialog.confirm_button._enabled)
        dialog.go_to_address()
        self.assertEqual(dialog.current_dir, self.folder)
        dialog.confirm()
        self.assertEqual(selected, [self.source])
        dialog = app.PathPickerDialog(self.root, "保存", self.folder, selected.append)
        dialog.address_var.set("..")
        dialog.go_to_address()
        self.assertEqual(dialog.current_dir, self.folder.parent)
        dialog.close()

    def test_picker_multi_select_and_directory_add(self):
        second = self.folder / "第二个.mp4"
        second.write_bytes(b"fixture")
        selected = []
        dialog = self.ui.pick_input_files({".mp4"}, selected.append)
        dialog.listbox.selection_set(0, "end")
        dialog.confirm()
        self.assertEqual(set(selected[0]), {self.source, second})
        dialog = self.ui.pick_input_files({".mp4"}, selected.append)
        dialog._drop_paths([self.folder, self.source])
        self.assertEqual(selected[1], [self.folder, self.source])

    def test_invalid_picker_path_does_not_select_old_directory(self):
        selected = []
        dialog = app.PathPickerDialog(self.root, "保存", ROOT, selected.append)
        dialog.address_var.set(str(self.folder / "不存在"))
        dialog.confirm()
        self.assertEqual(selected, [])
        self.assertIn("不存在", dialog.selection_var.get())
        dialog.close()

    def test_all_batch_pages_accept_paste_and_folder_and_reject_busy(self):
        for page in (self.ui.soften_page, self.ui.audio_convert_page, self.ui.compress_page):
            page.path_input.value.set(f'"{self.folder}"')
            page.path_input.submit()
            self.assertEqual(page.files, [self.source])
            with patch("path_inputs.clipboard_paths", return_value=[self.source]):
                page.path_input.paste()
            self.assertEqual(page.files, [self.source])
            page._set_running(True)
            page.add_files([ROOT / "extra.mp4"])
            self.assertEqual(page.files, [self.source])
            self.assertEqual(page.path_input.entry.cget("state"), "disabled")
            self.assertEqual(page.folder_input.field.cget("state"), "disabled")
            page._set_running(False)

    def test_file_surface_switches_empty_queue_and_optional_path_entry(self):
        for key, page in (("soften", self.ui.soften_page), ("audio_convert", self.ui.audio_convert_page),
                          ("compress", self.ui.compress_page)):
            self.ui._show_page(key)
            self.root.update()
            panel = page.file_queue
            self.assertTrue(panel.empty.winfo_ismapped())
            self.assertFalse(page.path_input.winfo_ismapped())
            self.assertFalse(page.file_list.winfo_ismapped())
            panel.toggle_paths()
            self.root.update()
            self.assertTrue(page.path_input.winfo_ismapped())
            page.path_input.value.set(str(self.source))
            page.path_input.submit()
            self.root.update()
            self.assertFalse(panel.empty.winfo_ismapped())
            self.assertTrue(page.file_list.winfo_ismapped())
            self.assertEqual(panel.count.get(), "1 个文件")
            page._set_running(True)
            self.assertFalse(panel.paste_button._enabled)
            self.assertFalse(panel.path_button._enabled)
            page._set_running(False)
            panel.toggle_paths()
            page.remove_selected()
            self.root.update()
            self.assertTrue(panel.empty.winfo_ismapped())
            self.assertTrue(self.source.is_file())

    def test_output_directory_drag_and_quoted_typed_path(self):
        for page in (self.ui.soften_page, self.ui.audio_convert_page, self.ui.compress_page):
            self.ui._show_page("soften" if page is self.ui.soften_page else
                               "compress" if page is self.ui.compress_page else "audio_convert")
            self.ui._drop_paths([self.folder], page.folder_input.field.entry)
            self.assertEqual(page.folder_input.value.get(), str(self.folder))
            new = self.folder / "新目录_有 空格"
            page.folder_input.value.set(f'"{new}"')
            self.assertEqual(page.folder_input.destination(lambda: ROOT), new)
            self.assertFalse(new.exists())

    def test_drop_routes_to_active_page_and_record_directory(self):
        for key, page in (("soften", self.ui.soften_page), ("audio_convert", self.ui.audio_convert_page),
                          ("compress", self.ui.compress_page)):
            self.ui._show_page(key)
            self.ui._drop_paths([self.folder])
            self.assertEqual(page.files, [self.source])
        self.ui._show_page("record")
        self.ui._drop_paths([ROOT])
        self.assertEqual(self.ui.folder_var.get(), str(ROOT))
        self.ui.state = "recording"
        self.ui._drop_paths([self.folder])
        self.assertEqual(self.ui.folder_var.get(), str(ROOT))
        self.ui.state = "idle"
        self.ui._show_page("edit")
        with patch.object(self.ui, "load_media") as load:
            self.ui._drop_paths([self.source, self.source])
            load.assert_not_called()
            self.assertIn("一次", self.ui.export_status_var.get())
            self.ui._drop_paths([self.source])
            load.assert_called_once_with(self.source)

    @unittest.skipUnless(os.name == "nt", "Windows shell drop protocol")
    def test_real_wm_dropfiles_to_tk_child_window(self):
        self.ui._show_page("compress")
        self.root.update()
        drop = self.ui.file_drop
        self.assertTrue(drop.enabled)
        target = self.ui.compress_page.file_list.winfo_id()
        self.assertIn(target, drop.handles)
        # Same HDROP memory layout used by Explorer; ownership passes to the receiver.
        class DROPFILES(ctypes.Structure):
            _fields_ = [("pFiles", wintypes.DWORD), ("pt", wintypes.POINT),
                        ("fNC", wintypes.BOOL), ("fWide", wintypes.BOOL)]
        header = DROPFILES(ctypes.sizeof(DROPFILES), wintypes.POINT(4, 4), False, True)
        data = bytes(header) + (str(self.folder) + "\0" + str(self.source) + "\0\0").encode("utf-16-le")
        kernel = ctypes.WinDLL("kernel32")
        kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        handle = kernel.GlobalAlloc(0x42, len(data))
        self.assertTrue(handle)
        pointer = kernel.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel.GlobalUnlock(handle)
        user = ctypes.WinDLL("user32")
        user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.SendMessageW.restype = ctypes.c_ssize_t
        user.SendMessageW(target, 0x0233, handle, 0)
        deadline = time.monotonic() + 1
        while not self.ui.compress_page.files and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertEqual(self.ui.compress_page.files, [self.source])


if __name__ == "__main__":
    unittest.main()
