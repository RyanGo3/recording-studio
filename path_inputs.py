"""Shared path input and Windows Explorer file drops; no extra runtime package."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import queue
import re
import sys
import tkinter as tk

from apple_ui import Fonts, PillButton, RoundedEntry, Theme


def parse_paths(text: str, base: Path | None = None) -> list[Path]:
    """Keep spaces/underscores in paths, including Explorer's quoted path list."""
    paths = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Multiple quoted paths on one line, or one unquoted path per line.
        quoted = re.fullmatch(r'\s*(?:"[^"]+"\s*)+', line)
        values = re.findall(r'"([^"]+)"', line) if quoted else [line]
        for value in values:
            value = value.strip().strip('"')
            if not value or "\0" in value:
                raise ValueError("路径为空或包含无效字符。")
            path = Path(os.path.expandvars(value)).expanduser()
            if base is not None and not path.is_absolute():
                path = base / path
            paths.append(path.resolve())
    return list(dict.fromkeys(paths))


def single_path(text: str, base: Path | None = None) -> Path:
    paths = parse_paths(text, base)
    if len(paths) != 1:
        raise ValueError("请输入一个完整路径。多个路径请在添加文件时使用。")
    return paths[0]


def collect_files(paths, suffixes):
    """Import immediate children only; report inaccessible/unsupported locations."""
    files, problems = [], []
    for item in paths:
        try:
            path = single_path(str(item))
            if path.is_dir():
                matches = []
                with os.scandir(path) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 10000:
                            problems.append(f"{path.name}：目录超过一万项，请选择更小的目录")
                            break
                        if entry.is_file() and Path(entry.name).suffix.lower() in suffixes:
                            matches.append(Path(entry.path).resolve())
                files.extend(sorted(matches, key=lambda p: p.name.casefold()))
                if not matches:
                    problems.append(f"{path.name}：本层没有支持的文件")
            elif path.is_file() and path.suffix.lower() in suffixes:
                files.append(path)
            else:
                problems.append(f"{path.name}：不存在或格式不支持")
        except (OSError, ValueError) as error:
            problems.append(f"{item}：{error}")
    return list(dict.fromkeys(files)), problems


def clipboard_paths(widget):
    """Called only by an explicit paste action; also support copied Explorer files."""
    if os.name == "nt":
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetClipboardData.argtypes = [wintypes.UINT]
        user.GetClipboardData.restype = wintypes.HANDLE
        if user.OpenClipboard(None):
            try:
                handle = user.GetClipboardData(15)  # CF_HDROP; clipboard owns the handle.
                if handle:
                    return _query_drop(handle)
            finally:
                user.CloseClipboard()
    return parse_paths(widget.clipboard_get())


class PathInput(tk.Frame):
    """Visible paste/type/Enter input shared by every batch page."""
    def __init__(self, master, on_add, status):
        super().__init__(master, bg=Theme.card)
        self.on_add, self.status = on_add, status
        self.value = tk.StringVar()
        entry = RoundedEntry(self, self.value, width=12)
        entry.pack(side="left", fill="x", expand=True)
        self.entry = entry.entry
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.add_button = PillButton(self, "添加路径", self.submit, variant="ghost", width=9)
        self.add_button.pack(side="left", padx=6)
        self.paste_button = PillButton(self, "粘贴添加", self.paste, variant="ghost", width=9)
        self.paste_button.pack(side="left")

    def submit(self):
        try:
            paths = parse_paths(self.value.get())
            if not paths:
                raise ValueError("请粘贴文件或文件夹的完整路径。")
            self.on_add(paths)
        except (ValueError, OSError) as error:
            self.status.set(str(error))

    def paste(self):
        try:
            paths = clipboard_paths(self)
            if not paths:
                raise ValueError("剪贴板中没有文件路径。")
            self.on_add(paths)
        except (tk.TclError, ValueError, OSError) as error:
            self.status.set(f"无法添加剪贴板内容：{error}")

    def set_enabled(self, enabled):
        self.entry.configure(state="normal" if enabled else "disabled")
        self.add_button.set_enabled(enabled)
        self.paste_button.set_enabled(enabled)


class FileQueue(tk.Frame):
    """One file surface: empty drop target becomes the queue, paths expand on demand."""
    def __init__(self, master, on_add, status, choose, current, remove, hint):
        super().__init__(master, bg=Theme.card)
        self.enabled = True
        self.paths_open = False
        self.drop_paths = on_add
        header = tk.Frame(self, bg=Theme.card)
        header.pack(fill="x", pady=(0, 12))
        tk.Label(header, text="素材文件", bg=Theme.card, fg=Theme.text,
                 font=Fonts.section).pack(side="left")
        self.count = tk.StringVar(value="尚未添加")
        tk.Label(header, textvariable=self.count, bg=Theme.card, fg=Theme.muted,
                 font=Fonts.small).pack(side="left", padx=12)
        self.add_button = PillButton(header, "＋ 添加文件", choose, width=10)
        self.add_button.pack(side="right")
        self.current_button = PillButton(header, "使用当前文件", current, variant="ghost", width=12)
        self.current_button.pack(side="right", padx=8)
        self.surface = tk.Frame(self, bg=Theme.input_bg, highlightthickness=1,
                                highlightbackground=Theme.border)
        self.surface.pack(fill="x")
        self.empty = tk.Canvas(self.surface, height=104, bg=Theme.input_bg,
                               highlightthickness=0, cursor="hand2", takefocus=True)
        self.empty.pack(fill="x")
        self.empty.bind("<Configure>", self._paint_empty)
        self.empty.bind("<Button-1>", lambda _e: choose() if self.enabled else None)
        self.empty.bind("<Return>", lambda _e: choose() if self.enabled else None)
        self.empty.bind("<Control-v>", lambda _e: self._paste())
        if sys.platform == "darwin":
            self.empty.bind("<Command-v>", lambda _e: self._paste())
        self.list_frame = tk.Frame(self.surface, bg=Theme.input_bg)
        self.file_list = tk.Listbox(
            self.list_frame, height=4, borderwidth=0, highlightthickness=0,
            exportselection=False, bg=Theme.input_bg, fg=Theme.text,
            selectbackground=Theme.accent_soft, selectforeground=Theme.accent,
            font=Fonts.ui, activestyle="none")
        self.file_list.grid(row=0, column=0, sticky="nsew", padx=10, pady=(8, 0))
        self.list_frame.columnconfigure(0, weight=1)
        from tkinter import ttk
        horizontal = ttk.Scrollbar(self.list_frame, orient="horizontal", command=self.file_list.xview)
        horizontal.grid(row=1, column=0, sticky="ew", padx=10)
        vertical = ttk.Scrollbar(self.list_frame, orient="vertical", command=self.file_list.yview)
        vertical.grid(row=0, column=1, sticky="ns", pady=8)
        self.file_list.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.file_list.bind("<Control-v>", lambda _e: self._paste())
        if sys.platform == "darwin":
            self.file_list.bind("<Command-v>", lambda _e: self._paste())
        footer = tk.Frame(self, bg=Theme.card)
        footer.pack(fill="x", pady=(8, 0))
        self.path_button = PillButton(footer, "输入路径", self.toggle_paths, variant="ghost", width=9)
        self.path_button.pack(side="left")
        self.paste_button = PillButton(footer, "粘贴添加", self._paste, variant="ghost", width=9)
        self.paste_button.pack(side="left", padx=6)
        self.remove_button = PillButton(footer, "移出列表", remove, variant="ghost", width=9)
        self.remove_button.pack(side="right")
        self.path_input = PathInput(self, on_add, status)
        # The footer already offers clipboard import.
        self.path_input.paste_button.pack_forget()
        self.path_hint = tk.Label(self, text=hint, bg=Theme.card, fg=Theme.muted,
                                  font=Fonts.small, anchor="w")
        self.path_hint.pack(fill="x", pady=(8, 0))

    def _paint_empty(self, _event=None):
        canvas = self.empty
        width = max(200, canvas.winfo_width())
        canvas.delete("all")
        x = width/2
        canvas.create_line(x, 10, x, 29, fill=Theme.accent, width=2, arrow="first")
        canvas.create_line(x-13, 24, x-13, 34, x+13, 34, x+13, 24,
                           fill=Theme.accent, width=2)
        canvas.create_text(x, 55, text="拖入文件或文件夹，开始处理", fill=Theme.text,
                           font=Fonts.ui_bold)
        canvas.create_text(x, 80, text="也可点击选择文件 · 支持粘贴完整路径", fill=Theme.muted,
                           font=Fonts.small)

    def _paste(self):
        if self.enabled:
            self.path_input.paste()
        return "break"

    def toggle_paths(self):
        if not self.enabled:
            return
        self.paths_open = not self.paths_open
        if self.paths_open:
            self.path_input.pack(fill="x", before=self.path_hint, pady=(10, 0))
            self.path_input.entry.focus_set()
        else:
            self.path_input.pack_forget()
        self.path_button.configure_text("收起路径" if self.paths_open else "输入路径")

    def refresh(self, count):
        self.count.set(f"{count} 个文件" if count else "尚未添加")
        if count:
            self.empty.pack_forget()
            self.list_frame.pack(fill="x")
        else:
            self.list_frame.pack_forget()
            self.empty.pack(fill="x")
        self.remove_button.set_enabled(bool(count) and self.enabled)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.path_input.set_enabled(enabled)
        self.path_button.set_enabled(enabled)
        self.paste_button.set_enabled(enabled)
        self.empty.configure(cursor="hand2" if enabled else "arrow", takefocus=enabled)
        self.remove_button.set_enabled(enabled and self.file_list.size() > 0)


class FolderInput(tk.Frame):
    def __init__(self, master, app, status):
        super().__init__(master, bg=Theme.card)
        self.app, self.status = app, status
        self.value = tk.StringVar()
        self.field = RoundedEntry(self, self.value, width=12)
        self.field.pack(side="left", fill="x", expand=True)
        self.button = PillButton(self, "选择目录", self.choose, variant="ghost", width=10)
        self.button.pack(side="left", padx=(8, 0))
        self.drop_paths = self.accept

    def choose(self):
        self.app.pick_output_folder(self.value.get() or self.app.folder_var.get(),
                                    lambda path: self.value.set(str(path)))

    def accept(self, paths):
        if self.field.cget("state") == "disabled":
            self.status.set("请等待处理结束后修改保存目录。")
        elif len(paths) == 1 and Path(paths[0]).is_dir():
            self.value.set(str(paths[0]))
            self.status.set("已更新保存目录")
        else:
            self.status.set("保存目录请拖入一个文件夹。")

    def destination(self, default):
        return single_path(self.value.get()) if self.value.get().strip() else default()

    def set_enabled(self, enabled):
        self.field.configure(state="normal" if enabled else "disabled")
        self.button.set_enabled(enabled)


def _shell():
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell.DragQueryFileW.restype = wintypes.UINT
    shell.DragFinish.argtypes = [wintypes.HANDLE]
    shell.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell.DragQueryPoint.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.POINT)]
    return shell


def _query_drop(handle):
    shell = _shell()
    paths = []
    for index in range(shell.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)):
        length = shell.DragQueryFileW(handle, index, None, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell.DragQueryFileW(handle, index, buffer, length + 1)
        paths.append(Path(buffer.value))
    return paths


class WindowsFileDrop:
    """Subclass Tk's existing HWNDs. Dispatch through a queue outside the WndProc."""
    def __init__(self, window, on_drop):
        self.window, self.on_drop = window, on_drop
        self.handles = set()
        self.pending = queue.SimpleQueue()
        self.job = None
        self.enabled = False
        if os.name != "nt":
            return
        self.shell = _shell()
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetParent.argtypes = [wintypes.HWND]
        self.user.GetParent.restype = wintypes.HWND
        self.user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.comctl = ctypes.WinDLL("comctl32", use_last_error=True)
        proc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM,
                                 ctypes.c_size_t, ctypes.c_size_t)
        self.comctl.SetWindowSubclass.argtypes = [wintypes.HWND, proc, ctypes.c_size_t, ctypes.c_size_t]
        self.comctl.SetWindowSubclass.restype = wintypes.BOOL
        self.comctl.RemoveWindowSubclass.argtypes = [wintypes.HWND, proc, ctypes.c_size_t]
        self.comctl.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT,
                                               wintypes.WPARAM, wintypes.LPARAM]
        self.comctl.DefSubclassProc.restype = ctypes.c_ssize_t
        self.proc = proc(self._message)
        self.ident = id(self)
        window.update_idletasks()
        widgets = [window]
        while widgets:
            widget = widgets.pop()
            if widget is not window and isinstance(widget, tk.Toplevel):
                continue
            widgets.extend(widget.winfo_children())
            self._attach(widget.winfo_id())
        self._attach(self.user.GetParent(window.winfo_id()))
        self.enabled = bool(self.handles)
        self.job = window.after(60, self._drain)

    def _attach(self, hwnd):
        if hwnd and hwnd not in self.handles:
            if self.comctl.SetWindowSubclass(hwnd, self.proc, self.ident, 0):
                self.handles.add(hwnd)
                self.shell.DragAcceptFiles(hwnd, True)

    def _message(self, hwnd, message, wparam, lparam, _ident, _data):
        if message == 0x0233:  # WM_DROPFILES
            try:
                paths = _query_drop(wparam)
                point = wintypes.POINT()
                self.shell.DragQueryPoint(wparam, ctypes.byref(point))
                self.user.ClientToScreen(hwnd, ctypes.byref(point))
                self.pending.put((paths, point.x, point.y))
            finally:
                self.shell.DragFinish(wparam)
            return 0
        if message == 0x0082:  # WM_NCDESTROY; detach before Tk releases the HWND.
            self.comctl.RemoveWindowSubclass(hwnd, self.proc, self.ident)
            self.handles.discard(hwnd)
        return self.comctl.DefSubclassProc(hwnd, message, wparam, lparam)

    def _drain(self):
        self.job = None
        while not self.pending.empty():
            paths, x, y = self.pending.get()
            try:
                target = self.window.winfo_containing(x, y)
                self.on_drop(paths, target)
            except Exception:
                self.window._root().report_callback_exception(*sys.exc_info())
        self.job = self.window.after(60, self._drain)

    def close(self):
        if self.job is not None:
            self.window.after_cancel(self.job)
            self.job = None
        for hwnd in tuple(self.handles):
            self.shell.DragAcceptFiles(hwnd, False)
            self.comctl.RemoveWindowSubclass(hwnd, self.proc, self.ident)
        self.handles.clear()
        self.enabled = False
