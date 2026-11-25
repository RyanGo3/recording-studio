"""Native Studio page for the migrated local audio softening engine."""
from __future__ import annotations

from pathlib import Path
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import pygame

import audio_softening as engine
from apple_ui import Card, Fonts, PillButton, SegmentedControl, SlimProgressbar, Theme, ValueSlider
from path_inputs import FileQueue, FolderInput, collect_files, single_path


class AudioSoftenerPage(tk.Frame):
    def __init__(self, app):
        super().__init__(app.main, bg=Theme.bg)
        self.app = app
        self.files: list[Path] = []
        self.results: dict[Path, dict] = {}
        self.labels: dict[Path, str] = {}
        self.running = False
        self.cancel_event = threading.Event()
        self.preview_channel = None
        self.preview_sound = None
        self.mode = tk.StringVar(value="high")
        self.strength = tk.IntVar(value=55)
        self.format = tk.StringVar(value="mp3")
        self.status = tk.StringVar(value="添加音频，或使用剪辑页当前文件。")
        self.result_info = tk.StringVar(value="处理后可试听同一时间范围的原声和柔化版。")
        self.progress = tk.DoubleVar(value=0)
        self.output_folder: Path | None = None
        self._build()

    def _build(self):
        inner = self.app._scroll_area(self, "soften_canvas")
        heading = tk.Frame(inner, bg=Theme.bg)
        heading.pack(fill="x", pady=(0, 18))
        title_row = tk.Frame(heading, bg=Theme.bg)
        title_row.pack(fill="x")
        tk.Label(title_row, text="声音柔化", bg=Theme.bg, fg=Theme.text,
                 font=Fonts.title).pack(side="left")
        self.action_bar = tk.Frame(title_row, bg=Theme.bg)
        self.action_bar.pack(side="right", pady=4)
        tk.Label(heading, text="动态抚平刺耳声与齿音，让声音柔和，台词依然清楚。",
                 bg=Theme.bg, fg=Theme.muted, font=Fonts.subtitle).pack(anchor="w", pady=(5, 0))

        inputs = Card(inner)
        inputs.pack(fill="x")
        self.file_queue = FileQueue(inputs, self.add_files, self.status, self.choose_files,
                                    self.use_current, self.remove_selected,
                                    "音频 / 视频 · 单个文件 ≤ 2 GB · 文件夹仅导入本层")
        self.file_queue.pack(fill="x")
        self.path_input = self.file_queue.path_input
        self.file_list = self.file_queue.file_list
        self.add_button = self.file_queue.add_button
        self.current_button = self.file_queue.current_button
        self.remove_button = self.file_queue.remove_button
        self.file_list.bind("<<ListboxSelect>>", self._selection_changed)

        settings = Card(inner)
        settings.pack(fill="x", pady=14)
        tk.Label(settings, text="柔化设置", bg=Theme.card, fg=Theme.text,
                 font=Fonts.section).pack(anchor="w")
        self.mode_control = SegmentedControl(
            settings, [("high", "高音角色"), ("whole", "整段柔化")], self.mode)
        self.mode_control.pack(anchor="w", pady=(12, 8))
        tk.Label(settings, text="高音角色：自动寻找偏高发声，可能影响其他高音说话者。\n"
                 "整段柔化：所有说话者和背景声音都会处理。",
                 bg=Theme.card, fg=Theme.muted, font=Fonts.small, justify="left").pack(anchor="w")
        row = tk.Frame(settings, bg=Theme.card)
        row.pack(fill="x", pady=(10, 4))
        tk.Label(row, text="柔化强度", bg=Theme.card, fg=Theme.text_dim,
                 font=Fonts.ui).pack(side="left")
        self.strength_control = ValueSlider(row, self.strength)
        self.strength_control.pack(side="left", fill="x", expand=True, padx=12)
        tk.Label(settings, text="建议从 55 开始；越高，刺耳声和齿音抑制越明显。保留低频与主体音量。",
                 bg=Theme.card, fg=Theme.muted, font=Fonts.small).pack(anchor="w")
        self.format_control = SegmentedControl(
            settings, [("mp3", "MP3"), ("wav", "WAV · 24 位"), ("flac", "FLAC")], self.format)
        self.format_control.pack(anchor="w", pady=(12, 0))
        tk.Label(settings, text="保存目录（留空保存到录制目录下的「声音柔化」）",
                 bg=Theme.card, fg=Theme.text_dim, font=Fonts.small).pack(anchor="w", pady=(12, 6))
        self.folder_input = FolderInput(settings, self.app, self.status)
        self.folder_input.pack(fill="x")
        tk.Label(settings, text="支持粘贴路径、输入新目录或拖入现有文件夹。",
                 bg=Theme.card, fg=Theme.muted, font=Fonts.small).pack(anchor="w", pady=(5, 0))

        actions = self.action_bar
        self.start_button = PillButton(actions, "开始柔化", self.start, width=12)
        self.start_button.pack(side="left")
        self.cancel_button = PillButton(actions, "取消处理", self.cancel, variant="ghost", width=10)
        self.cancel_button.pack(side="left", padx=8)
        self.folder_button = PillButton(actions, "输出文件夹", self.open_output, variant="ghost", width=12)
        self.folder_button.pack(side="right")
        SlimProgressbar(inner, self.progress).pack(fill="x", pady=(14, 8))
        tk.Label(inner, textvariable=self.status, bg=Theme.bg, fg=Theme.text_dim,
                 font=Fonts.small, wraplength=700, justify="left").pack(anchor="w")

        comparison = Card(inner)
        comparison.pack(fill="x", pady=(14, 0))
        tk.Label(comparison, text="对比试听 · 音量已匹配", bg=Theme.card, fg=Theme.text,
                 font=Fonts.section).pack(anchor="w")
        tk.Label(comparison, textvariable=self.result_info, bg=Theme.card, fg=Theme.muted,
                 font=Fonts.small, wraplength=650, justify="left").pack(anchor="w", pady=8)
        previews = tk.Frame(comparison, bg=Theme.card)
        previews.pack(fill="x")
        self.original_button = PillButton(previews, "原声", lambda: self.play_preview("original_preview"),
                                          variant="ghost", width=8)
        self.original_button.pack(side="left")
        self.softened_button = PillButton(previews, "柔化后", lambda: self.play_preview("softened_preview"),
                                          variant="success", width=8)
        self.softened_button.pack(side="left", padx=8)
        PillButton(previews, "停止试听", self.stop_preview, variant="ghost", width=10).pack(side="left")
        self.edit_button = PillButton(previews, "到剪辑页打开", self.open_in_editor, variant="ghost", width=13)
        self.edit_button.pack(side="right")
        self._set_running(False)

    def choose_files(self):
        return self.app.pick_input_files(engine.SUPPORTED, self.add_files, "添加需要声音柔化的文件")

    def add_files(self, paths):
        if self.running:
            self.status.set("请等待处理结束后添加文件。")
            return
        files, rejected = collect_files(paths, engine.SUPPORTED)
        count = len(self.files)
        for path in files:
            if path not in self.files:
                self.files.append(path)
                self.labels[path] = "等待处理"
        self._refresh_list()
        self.status.set(f"新增 {len(self.files) - count} 个文件，共 {len(self.files)} 个。"
                        + (" 未添加：" + "、".join(rejected[:5]) if rejected else ""))

    def use_current(self):
        if self.app.media_path is None:
            self.status.set("请先在剪辑页打开文件，或点击「添加文件」。")
            return
        self.add_files([self.app.media_path])

    def remove_selected(self):
        if self.running:
            return
        selected = self.file_list.curselection()
        if selected:
            self.stop_preview()
            # This only removes a queue entry; exported files stay on disk.
            self.files.pop(selected[0])
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

    def selected_result(self):
        selected = self.file_list.curselection()
        return self.results.get(self.files[selected[0]]) if selected else None

    def _selection_changed(self, _event=None):
        self.stop_preview()
        self._refresh_result()

    def _refresh_result(self):
        result = self.selected_result()
        enabled = bool(result) and not self.running
        self.original_button.set_enabled(enabled)
        self.softened_button.set_enabled(enabled)
        # The existing editor splits MP3 and MP4; WAV / FLAC remain available for export.
        self.edit_button.set_enabled(enabled and Path(result["export_path"]).suffix == ".mp3"
                                     if result else False)
        if result:
            self.result_info.set(
                f"处理 {result['region_count']} 个片段，共 {result['affected_seconds']:.1f} 秒。\n"
                f"试听起点 {result['preview_start']:.1f} 秒，两版各 {result['preview_duration']:.1f} 秒；已匹配平均音量。\n"
                f"成品：{result['export_path']}")
        else:
            selected = self.file_list.curselection()
            label = self.labels[self.files[selected[0]]] if selected else ""
            self.result_info.set(label if label.startswith("失败：") else
                                 "处理后可试听同一时间范围的原声和柔化版。")

    def _set_running(self, running):
        self.running = running
        self.file_queue.set_enabled(not running)
        self.folder_input.set_enabled(not running)
        for button in (self.add_button, self.current_button, self.remove_button, self.start_button):
            button.set_enabled(not running)
        self.file_queue.refresh(len(self.files))
        self.cancel_button.set_enabled(running)
        self.mode_control.set_enabled(not running)
        self.format_control.set_enabled(not running)
        self.strength_control.configure(state="disabled" if running else "normal")
        self._refresh_result()

    def start(self):
        if self.running:
            return
        if self.app.state != "idle" or self.app.background_processing():
            self.status.set("请等待当前录制或处理结束，也可先取消当前处理。")
            return
        if not self.files:
            self.status.set("请先添加需要柔化的文件。")
            return
        folder_text = self.app.folder_var.get().strip()
        if not folder_text and not self.folder_input.value.get().strip():
            self.status.set("请先在录制页选择保存目录。")
            return
        try:
            destination = self.folder_input.destination(lambda: single_path(folder_text) / "声音柔化")
            engine.tool("ffmpeg")
            engine.tool("ffprobe")
            destination.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError, engine.ProcessingError) as error:
            self.status.set(str(error))
            return
        self.app.stop_playback()
        self.stop_preview()
        self.output_folder = destination
        self.cancel_event = threading.Event()
        self.progress.set(0)
        self._set_running(True)
        self.status.set("正在准备声音柔化…")
        options = {"mode": self.mode.get(), "strength": self.strength.get(), "format": self.format.get()}
        self.worker = threading.Thread(target=self._worker,
                                       args=(tuple(self.files), destination, options, self.cancel_event),
                                       daemon=True)
        self.worker.start()

    def _worker(self, files, destination, options, cancel):
        succeeded = failed = 0
        try:
            for index, path in enumerate(files):
                engine.check_cancel(cancel)
                self.app.events.put(("soften_item", (path, "正在处理")))

                def progress(percent, stage, index=index, path=path):
                    self.app.events.put(("soften_progress",
                                         ((index * 100 + percent) / len(files),
                                          f"{index + 1}/{len(files)} · {path.name} · {stage} {percent:.0f}%")))
                try:
                    result = engine.process(path, path.name, destination, options, progress, cancel)
                except engine.Cancelled:
                    raise
                except Exception as error:
                    failed += 1
                    self.app.events.put(("soften_item", (path, f"失败：{error}")))
                else:
                    succeeded += 1
                    self.app.events.put(("soften_result", (path, result)))
            summary = f"处理完成：成功 {succeeded}，失败 {failed}。成品保存在 {destination}"
            self.app.events.put(("soften_progress", (100, summary)))
        except engine.Cancelled:
            summary = f"已取消，已完成 {succeeded} 个文件；成品保留在 {destination}"
        except Exception as error:
            summary = f"处理失败：{error}"
        self.app.events.put(("soften_finished", summary))

    def handle_event(self, event, payload):
        if event == "soften_progress":
            percent, text = payload
            self.progress.set(percent)
            self.status.set(text)
        elif event == "soften_item":
            path, label = payload
            if label == "正在处理":
                self.results.pop(path, None)
            self.labels[path] = label
            self._refresh_list()
        elif event == "soften_result":
            path, result = payload
            self.results[path] = result
            self.labels[path] = "已完成"
            self._refresh_list()
        elif event == "soften_finished":
            for path in self.files:
                if self.labels[path] == "正在处理":
                    self.labels[path] = "已取消"
            self._set_running(False)
            self._refresh_list()
            self.status.set(payload)
            if self.app.close_after_save:
                self.app.root.after(0, self.app.on_close)

    def cancel(self):
        if self.running:
            self.cancel_event.set()
            self.status.set("正在取消，已完成的成品会保留…")
            self.cancel_button.set_enabled(False)

    def play_preview(self, key):
        result = self.selected_result()
        if not result or self.running:
            return
        try:
            self.app.stop_playback()
            self.stop_preview()
            self.app._ensure_mixer()
            self.preview_sound = pygame.mixer.Sound(result[key])
            self.preview_channel = self.preview_sound.play()
            if self.preview_channel is None:
                raise RuntimeError("音频播放通道不可用。")
        except Exception as error:
            messagebox.showerror("声音柔化", f"无法试听：\n{error}")

    def stop_preview(self):
        if self.preview_channel is not None:
            self.preview_channel.stop()
            self.preview_channel = None
        self.preview_sound = None

    def open_output(self):
        result = self.selected_result()
        folder = Path(result["export_path"]).parent if result else self.output_folder
        if folder:
            self.app._open_folder(folder)
        else:
            self.status.set("处理完成后可在这里打开输出文件夹。")

    def open_in_editor(self):
        result = self.selected_result()
        if result and Path(result["export_path"]).suffix == ".mp3":
            self.stop_preview()
            self.app.load_media(Path(result["export_path"]))
            self.app._show_page("edit")
