"""Shared native pages for video-to-audio conversion and video compression."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from apple_ui import Card, Fonts, PillButton, SegmentedControl, SlimProgressbar, Theme
from audio_softening import Cancelled, ProcessingError, tool
from media_conversion import VIDEO_SUFFIXES, process_media
from path_inputs import FileQueue, FolderInput, collect_files, single_path


def format_size(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024


class MediaConversionPage(tk.Frame):
    def __init__(self, app, operation):
        super().__init__(app.main, bg=Theme.bg)
        self.app, self.operation = app, operation
        self.title = "视频转音频" if operation == "audio" else "视频压缩"
        self.files: list[Path] = []
        self.labels: dict[Path, str] = {}
        self.results: dict[Path, dict] = {}
        self.running = False
        self.cancel_event = threading.Event()
        self.custom_folder: Path | None = None
        self.last_folder: Path | None = None
        self.format = tk.StringVar(value="mp3")
        self.codec = tk.StringVar(value="h265")
        self.quality = tk.StringVar(value="clear")
        self.status = tk.StringVar(value="添加视频，或使用剪辑页当前的录像。")
        self.folder_text = tk.StringVar(value=f"默认保存到录制目录下的「{self.title}」文件夹")
        self.result_text = tk.StringVar(value="完成后，在列表中选择文件查看成品。")
        self.progress = tk.DoubleVar(value=0)
        self.controls = []
        self._build()

    def _build(self):
        inner = self.app._scroll_area(self, self.operation + "_convert_canvas")
        title_row = tk.Frame(inner, bg=Theme.bg)
        title_row.pack(fill="x")
        tk.Label(title_row, text=self.title, bg=Theme.bg, fg=Theme.text,
                 font=Fonts.title).pack(side="left")
        self.action_bar = tk.Frame(title_row, bg=Theme.bg)
        self.action_bar.pack(side="right", pady=4)
        subtitle = ("把视频中的声音单独保存，可批量转换。"
                    if self.operation == "audio" else
                    "让视频更轻便，默认保留原分辨率与帧率。")
        tk.Label(inner, text=subtitle, bg=Theme.bg, fg=Theme.muted,
                 font=Fonts.subtitle).pack(anchor="w", pady=(5, 18))
        inputs = Card(inner)
        inputs.pack(fill="x")
        self.file_queue = FileQueue(inputs, self.add_files, self.status, self.choose_files,
                                    self.use_current, self.remove_selected,
                                    "MP4 / MKV / MOV 等视频 · 支持批量 · 文件夹仅导入本层")
        self.file_queue.pack(fill="x")
        self.path_input = self.file_queue.path_input
        self.file_list = self.file_queue.file_list
        self.add_button = self.file_queue.add_button
        self.current_button = self.file_queue.current_button
        self.remove_button = self.file_queue.remove_button
        self.file_list.bind("<<ListboxSelect>>", lambda _e: self._refresh_result())

        settings = Card(inner)
        settings.pack(fill="x", pady=14)
        tk.Label(settings, text="导出设置", bg=Theme.card, fg=Theme.text,
                 font=Fonts.section).pack(anchor="w")
        if self.operation == "audio":
            control = SegmentedControl(settings, [(fmt, fmt.upper()) for fmt in
                                                  ("mp3", "m4a", "wav", "flac")], self.format)
            control.pack(anchor="w", pady=(12, 8))
            self.controls.append(control)
            hint = "MP3 使用高品质可变码率；M4A 使用 AAC 192 kbps。\nWAV / FLAC 保留解码后的音频质量；默认提取第一条音轨。"
        else:
            for options, variable in (
                ([("h265", "H.265 · 高效压缩"), ("h264", "H.264 · 兼容优先")], self.codec),
                ([("clear", "清晰优先"), ("balanced", "均衡"), ("small", "体积优先")], self.quality),
            ):
                control = SegmentedControl(settings, options, variable)
                control.pack(anchor="w", pady=(12, 0))
                self.controls.append(control)
            hint = "输出 MP4，不主动降低分辨率或帧率。\n清晰优先保留更多细节；体积优先会损失更多细节。"
        tk.Label(settings, text=hint, bg=Theme.card, fg=Theme.muted,
                 font=Fonts.small, justify="left").pack(anchor="w", pady=(10, 0))
        folder = tk.Frame(settings, bg=Theme.card)
        folder.pack(fill="x", pady=(12, 0))
        tk.Label(folder, textvariable=self.folder_text, bg=Theme.card, fg=Theme.text_dim,
                 font=Fonts.small, wraplength=650, justify="left").pack(anchor="w")
        self.folder_input = FolderInput(folder, self.app, self.status)
        self.folder_input.pack(fill="x", pady=(6, 0))
        self.folder_input.value.trace_add("write", self._folder_edited)
        self.choose_folder_button = self.folder_input.button
        tk.Label(folder, text="留空跟随默认目录；可粘贴、输入新目录，或拖入现有文件夹。",
                 bg=Theme.card, fg=Theme.muted, font=Fonts.small).pack(anchor="w", pady=(5, 0))
        actions = self.action_bar
        self.start_button = PillButton(actions, "开始转换" if self.operation == "audio" else "开始压缩",
                                       self.start, width=12)
        self.start_button.pack(side="left")
        self.cancel_button = PillButton(actions, "取消处理", self.cancel, variant="ghost", width=10)
        self.cancel_button.pack(side="left", padx=8)
        self.folder_button = PillButton(actions, "输出文件夹", self.open_folder, variant="ghost", width=12)
        self.folder_button.pack(side="right")
        SlimProgressbar(inner, self.progress).pack(fill="x", pady=(14, 8))
        tk.Label(inner, textvariable=self.status, bg=Theme.bg, fg=Theme.text_dim,
                 font=Fonts.small, wraplength=700, justify="left").pack(anchor="w")
        results = Card(inner)
        results.pack(fill="x", pady=(14, 0))
        tk.Label(results, text="处理结果", bg=Theme.card, fg=Theme.text,
                 font=Fonts.section).pack(anchor="w")
        tk.Label(results, textvariable=self.result_text, bg=Theme.card, fg=Theme.text_dim,
                 font=Fonts.small, wraplength=660, justify="left").pack(anchor="w", pady=10)
        self.open_button = PillButton(results, "打开成品", self.open_result, variant="success", width=12)
        self.open_button.pack(anchor="w")
        self._set_running(False)

    def choose_files(self):
        return self.app.pick_input_files(VIDEO_SUFFIXES, self.add_files, "添加视频")

    def add_files(self, paths):
        if self.running:
            self.status.set("请等待处理结束后添加文件。")
            return
        files, rejected = collect_files(paths, VIDEO_SUFFIXES)
        count = len(self.files)
        for path in files:
            if path not in self.files:
                self.files.append(path)
                self.labels[path] = "等待处理"
        self._refresh_list()
        self.status.set(f"新增 {len(self.files) - count} 个文件，共 {len(self.files)} 个。"
                        + (" 未添加：" + "、".join(rejected[:5]) if rejected else ""))

    def use_current(self):
        source = self.app.media_path
        if source is None or source.suffix.lower() not in VIDEO_SUFFIXES:
            self.status.set("请先在剪辑页打开录像，或点击「添加视频」。")
            return
        self.add_files([source])

    def remove_selected(self):
        selected = self.file_list.curselection()
        if not self.running and selected:
            path = self.files.pop(selected[0])
            self.results.pop(path, None)
            self.labels.pop(path, None)
            self._refresh_list()

    def _refresh_list(self):
        selected = self.file_list.curselection()
        index = selected[0] if selected else 0
        self.file_list.delete(0, "end")
        for path in self.files:
            self.file_list.insert("end", f"{self.labels[path]}  ·  {path.name}")
        if self.files:
            self.file_list.selection_set(min(index, len(self.files) - 1))
        self.file_queue.refresh(len(self.files))
        self._refresh_result()

    def selected_path(self):
        selected = self.file_list.curselection()
        return self.files[selected[0]] if selected else None

    def _refresh_result(self):
        source = self.selected_path()
        result = self.results.get(source)
        self.open_button.set_enabled(bool(result) and not self.running)
        if result:
            size = f"{format_size(result['source_bytes'])} → {format_size(result['output_bytes'])}"
            if self.operation == "compress":
                effect = (f"减少 {result['saved_percent']:.1f}%" if result["smaller"] else
                          "本次未缩小；原片可能已经较紧凑，可尝试「均衡」或「体积优先」。")
                size += "\n" + effect
            self.result_text.set(f"{size}\n成品：{result['export_path']}")
        elif source and self.labels[source].startswith(("失败：", "已取消")):
            self.result_text.set(self.labels[source])
        else:
            self.result_text.set("完成后，在列表中选择文件查看成品。")

    def _set_running(self, running):
        self.running = running
        self.file_queue.set_enabled(not running)
        self.folder_input.set_enabled(not running)
        for button in (self.add_button, self.current_button, self.remove_button,
                       self.choose_folder_button, self.start_button):
            button.set_enabled(not running)
        self.file_queue.refresh(len(self.files))
        for control in self.controls:
            control.set_enabled(not running)
        self.cancel_button.set_enabled(running)
        self._refresh_result()

    def choose_folder(self):
        self.folder_input.choose()

    def _folder_edited(self, *_):
        self.custom_folder = None

    def start(self):
        if self.running:
            return
        if self.app.state != "idle" or self.app.background_processing():
            self.status.set("请等待当前录制或处理完成，也可先取消当前处理。")
            return
        if not self.files:
            self.status.set("请先添加视频。")
            return
        folder = self.app.folder_var.get().strip()
        if self.custom_folder is None and not folder and not self.folder_input.value.get().strip():
            self.status.set("请先选择保存目录。")
            return
        try:
            destination = self.folder_input.destination(
                lambda: self.custom_folder or single_path(folder) / self.title)
            tool("ffmpeg")
            tool("ffprobe")
            destination.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError, ProcessingError) as error:
            self.status.set(str(error))
            return
        self.app.stop_playback()
        self.app.soften_page.stop_preview()
        self.last_folder = destination
        self.progress.set(0)
        self.cancel_event = threading.Event()
        self._set_running(True)
        self.status.set("正在准备…")
        options = ({"format": self.format.get()} if self.operation == "audio" else
                   {"codec": self.codec.get(), "quality": self.quality.get()})
        self.worker = threading.Thread(target=self._worker,
                                       args=(tuple(self.files), destination, options, self.cancel_event),
                                       daemon=True)
        self.worker.start()

    def _emit(self, event, payload):
        self.app.events.put(("media_job", (self, event, payload)))

    def _worker(self, files, destination, options, cancel):
        succeeded = failed = not_smaller = 0
        try:
            for index, path in enumerate(files):
                if cancel.is_set():
                    raise Cancelled("剩余文件未处理。")
                self._emit("item", (path, "正在处理"))

                def progress(percent, stage, index=index, path=path):
                    self._emit("progress", ((index * 100 + percent) / len(files),
                                           f"{index + 1}/{len(files)} · {path.name} · {stage} {percent:.0f}%"))
                try:
                    result = process_media(path, destination, self.operation, options, progress, cancel)
                except Cancelled as error:
                    self._emit("item", (path, str(error)))
                    raise
                except Exception as error:
                    failed += 1
                    self._emit("item", (path, f"失败：{error}"))
                else:
                    succeeded += 1
                    not_smaller += int(self.operation == "compress" and not result["smaller"])
                    self._emit("result", (path, result))
            summary = f"处理完成：成功 {succeeded}，失败 {failed}"
            if not_smaller:
                summary += f"，其中 {not_smaller} 个视频未缩小"
            self._emit("progress", (100, summary))
        except Cancelled:
            summary = f"已取消，保留 {succeeded} 个已完成成品；原文件保留。"
        except Exception as error:
            summary = f"处理失败：{error}"
        self._emit("finished", summary)

    def handle_event(self, event, payload):
        if event == "progress":
            percent, text = payload
            self.progress.set(percent)
            self.status.set(text)
        elif event == "item":
            path, label = payload
            self.labels[path] = label
            if label == "正在处理":
                self.results.pop(path, None)
            self._refresh_list()
        elif event == "result":
            path, result = payload
            self.results[path] = result
            self.labels[path] = ("已完成 · 未缩小" if self.operation == "compress"
                                 and not result["smaller"] else "已完成")
            self._refresh_list()
        elif event == "finished":
            self._set_running(False)
            self.status.set(payload)
            if self.app.close_after_save:
                self.app.root.after(0, self.app.on_close)

    def cancel(self):
        if self.running:
            self.cancel_event.set()
            self.status.set("正在取消，已完成成品和原文件均保留…")
            self.cancel_button.set_enabled(False)

    def open_folder(self):
        result = self.results.get(self.selected_path())
        folder = Path(result["export_path"]).parent if result else self.last_folder
        if folder:
            self.app._open_folder(folder)
        else:
            self.status.set("请先完成一次处理。")

    def open_result(self):
        result = self.results.get(self.selected_path())
        if not result or self.running:
            return
        path = Path(result["export_path"])
        try:
            if path.suffix.lower() in {".mp3", ".mp4"}:
                self.app.load_media(path)
                self.app._show_page("edit")
            else:
                os.startfile(str(path))
        except OSError as error:
            messagebox.showerror(self.title, f"无法打开成品：\n{error}")
