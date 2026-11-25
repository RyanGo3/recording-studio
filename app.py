from __future__ import annotations

import ctypes
import io
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import lameenc
from PIL import Image, ImageTk
import numpy as np
import pygame
import soundcard as sc
from mutagen.mp3 import MP3
from audio_processing import cleanup_filter
from audio_sources import PCMFile
from softening_ui import AudioSoftenerPage
from conversion_ui import MediaConversionPage
from path_inputs import (
    WindowsFileDrop, PathInput, collect_files, parse_paths, single_path, clipboard_paths,
)


APP_TITLE = "土豆工厂"
APP_AUTHOR = "大土豆|Ryan"
EDITOR_SUFFIXES = {".mp3", ".mp4", ".mov", ".mkv", ".m4a"}
SAMPLE_RATE = 48_000
CHANNELS = 2
BLOCK_SIZE = 4_800
BIT_RATE = 128
MAX_RECORDING_SECONDS = 12 * 60 * 60
SCREEN_FPS = 30
CREATE_NO_WINDOW = 0x08000000


from apple_ui import (
    Theme, Fonts, PillButton, SegmentedControl, NavButton, Card,
    Switch, RoundedEntry, Badge, SlimScrollbar, PlaybackTimeline, studio_icon,
)


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info


def _popen_kwargs() -> dict:
    kwargs: dict = {"startupinfo": _startupinfo()}
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return kwargs


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_ffmpeg() -> Path:
    candidates: list[Path] = [
        app_dir() / "ffmpeg.exe",
        app_dir() / "tools" / "ffmpeg.exe",
        Path(sys.executable).resolve().parent / "ffmpeg.exe",
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "ffmpeg.exe")
    which = shutil.which("ffmpeg")
    if which:
        candidates.append(Path(which))
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("没有找到 ffmpeg.exe，录屏、视频分段、分离音频和声音柔化都需要它。")


def format_time(seconds: float, show_milliseconds: bool = False) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    text = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    if show_milliseconds:
        text += f".{int((seconds - whole) * 10):01d}"
    return text


def parse_time(value: str) -> float:
    value = value.strip()
    if not value:
        raise ValueError("请输入切分时间。")
    try:
        parts = [float(part) for part in value.split(":")]
    except ValueError as error:
        raise ValueError("时间格式不正确，例如：01:23 或 01:23:45。") from error
    if len(parts) == 1:
        seconds = parts[0]
    elif len(parts) == 2:
        seconds = parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
    else:
        raise ValueError("时间格式不正确，例如：01:23 或 01:23:45。")
    if seconds < 0:
        raise ValueError("时间不能为负数。")
    return seconds


def filename_time(seconds: float) -> str:
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}-{minutes:02d}-{secs:02d}"


def even_size(width: int, height: int) -> tuple[int, int]:
    return max(16, width - (width % 2)), max(16, height - (height % 2))


def default_output_folder() -> Path:
    drive = Path("D:/")
    base = drive if drive.exists() else Path.home() / "Videos"
    # Keep existing recordings discoverable; never move the user's files.
    legacy = base / "录制工坊"
    return legacy if legacy.is_dir() else base / APP_TITLE


def clamp_region(region: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    vx, vy, vw, vh = virtual_screen()
    x, y, width, height = region
    width = max(16, width - (width % 2))
    height = max(16, height - (height % 2))
    x = min(max(x, vx), vx + vw - width)
    y = min(max(y, vy), vy + vh - height)
    width = min(width, vx + vw - x)
    height = min(height, vy + vh - y)
    width -= width % 2
    height -= height % 2
    return x, y, max(16, width), max(16, height)


def window_hwnd(widget: tk.Misc) -> int:
    widget.update_idletasks()
    hwnd = int(widget.winfo_id())
    parent = ctypes.windll.user32.GetParent(hwnd)
    return parent or hwnd


def exclude_window_from_capture(widget: tk.Misc) -> None:
    if os.name != "nt":
        return
    hwnd = window_hwnd(widget)
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_uint),
    ]


def virtual_screen() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    return (
        user32.GetSystemMetrics(76),
        user32.GetSystemMetrics(77),
        user32.GetSystemMetrics(78),
        user32.GetSystemMetrics(79),
    )


def list_monitors() -> list[dict[str, int | bool]]:
    monitors: list[dict[str, int | bool]] = []
    if os.name != "nt":
        return [{"x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True}]

    def callback(hmon, _hdc, _lprect, _lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        rect = info.rcMonitor
        monitors.append(
            {
                "x": int(rect.left),
                "y": int(rect.top),
                "w": int(rect.right - rect.left),
                "h": int(rect.bottom - rect.top),
                "primary": bool(info.dwFlags & 1),
            }
        )
        return 1

    enum_proc = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(RECT),
        ctypes.c_void_p,
    )
    ctypes.windll.user32.EnumDisplayMonitors(0, 0, enum_proc(callback), 0)
    if not monitors:
        user32 = ctypes.windll.user32
        monitors.append(
            {
                "x": 0,
                "y": 0,
                "w": int(user32.GetSystemMetrics(0)),
                "h": int(user32.GetSystemMetrics(1)),
                "primary": True,
            }
        )
    return monitors


def primary_monitor_rect() -> tuple[int, int, int, int]:
    monitors = list_monitors()
    primary = next((item for item in monitors if item["primary"]), monitors[0])
    return int(primary["x"]), int(primary["y"]), int(primary["w"]), int(primary["h"])


def run_ffmpeg(args: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[bytes]:
    ffmpeg = find_ffmpeg()
    process = subprocess.run(
        [str(ffmpeg), "-hide_banner", *args],
        capture_output=True,
        timeout=timeout,
        **_popen_kwargs(),
    )
    return process


def ffmpeg_error_text(process: subprocess.CompletedProcess[bytes]) -> str:
    text = process.stderr.decode("utf-8", errors="replace").strip()
    return text or process.stdout.decode("utf-8", errors="replace").strip() or "ffmpeg 执行失败。"


def media_duration(path: Path) -> float:
    process = run_ffmpeg(["-i", str(path)])
    text = process.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", text)
    if not match:
        raise RuntimeError("无法读取媒体时长。")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def media_has_audio(path: Path) -> bool:
    process = run_ffmpeg(["-i", str(path)])
    text = process.stderr.decode("utf-8", errors="replace")
    return "Audio:" in text


def concat_file_line(path: Path) -> str:
    escaped = str(path).replace("\\", "/").replace("'", "'\\''")
    return f"file '{escaped}'"


class SystemAudioRecorder:
    def __init__(self, output_path: Path, events: queue.Queue[tuple[str, object]],
                 noise_reduction: bool = True, ffmpeg_path: Path | None = None) -> None:
        self.output_path = output_path
        self.events = events
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.recorded_seconds = 0.0
        self.noise_reduction = noise_reduction
        self.ffmpeg_path = ffmpeg_path
        self.session: Path | None = None

    @staticmethod
    def find_loopback_microphone() -> tuple[sc.Speaker, sc.Microphone]:
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("没有找到默认播放设备。")

        loopbacks = [
            microphone
            for microphone in sc.all_microphones(include_loopback=True)
            if microphone.isloopback
        ]
        microphone = next(
            (item for item in loopbacks if item.name == speaker.name),
            None,
        )
        if microphone is None:
            microphone = next(
                (item for item in loopbacks if speaker.name in item.name),
                None,
            )
        if microphone is None and loopbacks:
            microphone = loopbacks[0]
        if microphone is None:
            raise RuntimeError("没有找到系统声音回放设备。")
        return speaker, microphone

    def start(self) -> None:
        self.thread = threading.Thread(target=self._record, daemon=True)
        self.thread.start()

    def pause(self) -> None:
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()

    def stop(self) -> None:
        self.stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self.thread:
            self.thread.join(timeout)

    def _record(self) -> None:
        try:
            speaker, microphone = self.find_loopback_microphone()
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            if self.output_path.exists():
                raise FileExistsError(f"输出文件已存在，不会覆盖：{self.output_path}")
            if self.noise_reduction:
                ffmpeg = self.ffmpeg_path or find_ffmpeg()
                self.session = self.output_path.parent / f".{self.output_path.stem}_session_{uuid.uuid4().hex[:8]}"
                self.session.mkdir(exist_ok=False)
                if os.name == "nt":
                    ctypes.windll.kernel32.SetFileAttributesW(str(self.session), 0x02)
                raw = self.session / "system.wav"

            encoder = lameenc.Encoder()
            encoder.set_bit_rate(BIT_RATE)
            encoder.set_in_sample_rate(SAMPLE_RATE)
            encoder.set_channels(CHANNELS)
            encoder.set_quality(2)

            self.events.put(("started", speaker.name))
            with (PCMFile(raw) if self.noise_reduction else self.output_path.open("xb")) as output:
                with microphone.recorder(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    blocksize=BLOCK_SIZE,
                ) as recorder:
                    while (
                        not self.stop_event.is_set()
                        and self.recorded_seconds < MAX_RECORDING_SECONDS
                    ):
                        samples = recorder.record(numframes=BLOCK_SIZE)
                        if self.pause_event.is_set():
                            continue

                        samples = self._normalize_channels(samples)
                        samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
                        pcm = (
                            np.clip(samples, -1.0, 1.0) * np.iinfo(np.int16).max
                        ).astype("<i2", copy=False)
                        if self.noise_reduction:
                            output.writeframesraw(pcm.tobytes())
                        else:
                            encoded = encoder.encode(pcm.tobytes())
                            if encoded:
                                output.write(encoded)
                        self.recorded_seconds += len(samples) / SAMPLE_RATE

                if not self.noise_reduction:
                    tail = encoder.flush()
                    if tail:
                        output.write(tail)

            if self.noise_reduction:
                self.events.put(("processing", "正在轻度降噪并保存 MP3…"))
                if self.output_path.exists():
                    raise FileExistsError(f"输出文件已存在，不会覆盖：{self.output_path}")
                process = subprocess.run(
                    [str(ffmpeg), "-v", "error", "-n", "-i", str(raw),
                     "-af", cleanup_filter("system"), "-c:a", "libmp3lame",
                     "-b:a", f"{BIT_RATE}k", str(self.output_path)],
                    capture_output=True, **_popen_kwargs())
                if process.returncode or not self.output_path.is_file():
                    raise RuntimeError(process.stderr.decode("utf-8", errors="replace")[-2500:]
                                       or "降噪音频保存失败。")
                session, self.session = self.session, None
                if session is not None:
                    if os.name == "nt":
                        ctypes.windll.kernel32.SetFileAttributesW(str(session), 0x80)
                    shutil.rmtree(session)
            self.events.put(
                (
                    "stopped",
                    {
                        "path": self.output_path,
                        "reached_limit": self.recorded_seconds >= MAX_RECORDING_SECONDS,
                    },
                )
            )
        except Exception as error:
            recovery = f"\n原始声音保留在：{self.session}" if self.session else ""
            self.events.put(("error", str(error) + recovery))

    @staticmethod
    def _normalize_channels(samples: np.ndarray) -> np.ndarray:
        if samples.ndim == 1:
            return np.column_stack((samples, samples))
        if samples.shape[1] == 1:
            return np.repeat(samples, CHANNELS, axis=1)
        return samples[:, :CHANNELS]


from recording_v2 import ScreenRecorder, output_filename, available_path
from cinematic import Effects


class Mp3FrameSplitter:
    MPEG1_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
    MPEG2_BITRATES = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
    SAMPLE_RATES = [44_100, 48_000, 32_000]

    @classmethod
    def split(
        cls,
        source: Path,
        outputs: list[Path],
        bounds: list[float],
        progress: Callable[[int, int], None],
    ) -> None:
        output_index = 0
        elapsed = 0.0
        found_frame = False
        output = None
        try:
            with source.open("rb") as input_file:
                cls._skip_id3v2(input_file)
                output = outputs[0].open("xb")
                progress(1, len(outputs))

                while True:
                    position = input_file.tell()
                    header = input_file.read(4)
                    if len(header) < 4:
                        break
                    frame = cls._parse_header(header)
                    if frame is None:
                        input_file.seek(position + 1)
                        continue

                    frame_length, samples_per_frame, sample_rate = frame
                    body = input_file.read(frame_length - 4)
                    if len(body) != frame_length - 4:
                        break
                    found_frame = True

                    while (
                        output_index < len(outputs) - 1
                        and elapsed >= bounds[output_index + 1]
                    ):
                        output.close()
                        output_index += 1
                        output = outputs[output_index].open("xb")
                        progress(output_index + 1, len(outputs))

                    output.write(header)
                    output.write(body)
                    elapsed += samples_per_frame / sample_rate
        finally:
            if output is not None:
                output.close()

        if not found_frame:
            raise RuntimeError("没有识别到有效的 MP3 音频帧。")

    @staticmethod
    def _skip_id3v2(input_file) -> None:
        header = input_file.read(10)
        if len(header) == 10 and header[:3] == b"ID3":
            size = (
                (header[6] & 0x7F) << 21
                | (header[7] & 0x7F) << 14
                | (header[8] & 0x7F) << 7
                | (header[9] & 0x7F)
            )
            input_file.seek(10 + size + (10 if header[5] & 0x10 else 0))
        else:
            input_file.seek(0)

    @classmethod
    def _parse_header(cls, header: bytes) -> tuple[int, int, int] | None:
        first, second, third, _fourth = header
        if first != 0xFF or second & 0xE0 != 0xE0:
            return None
        version_bits = (second >> 3) & 0x03
        layer_bits = (second >> 1) & 0x03
        bitrate_index = (third >> 4) & 0x0F
        sample_rate_index = (third >> 2) & 0x03
        padding = (third >> 1) & 0x01
        if (
            version_bits == 0x01
            or layer_bits != 0x01
            or bitrate_index in {0, 15}
            or sample_rate_index == 3
        ):
            return None

        if version_bits == 0x03:
            bitrate = cls.MPEG1_BITRATES[bitrate_index]
            sample_rate = cls.SAMPLE_RATES[sample_rate_index]
            samples_per_frame = 1152
            frame_length = int(144_000 * bitrate / sample_rate) + padding
        else:
            bitrate = cls.MPEG2_BITRATES[bitrate_index]
            divisor = 2 if version_bits == 0x02 else 4
            sample_rate = cls.SAMPLE_RATES[sample_rate_index] // divisor
            samples_per_frame = 576
            frame_length = int(72_000 * bitrate / sample_rate) + padding
        return frame_length, samples_per_frame, sample_rate


class VideoClipSplitter:
    @staticmethod
    def split(
        source: Path,
        outputs: list[Path],
        bounds: list[float],
        progress: Callable[[int, int], None],
    ) -> None:
        total = len(outputs)
        for index, (start, end, dest) in enumerate(zip(bounds, bounds[1:], outputs), start=1):
            progress(index, total)
            duration = max(0.05, end - start)
            process = run_ffmpeg(
                [
                    "-y",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    str(source),
                    "-t",
                    f"{duration:.3f}",
                    "-c",
                    "copy",
                    "-avoid_negative_ts",
                    "make_zero",
                    "-movflags",
                    "+faststart",
                    str(dest),
                ]
            )
            if process.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
                process = run_ffmpeg(
                    [
                        "-y",
                        "-ss",
                        f"{start:.3f}",
                        "-i",
                        str(source),
                        "-t",
                        f"{duration:.3f}",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "23",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-movflags",
                        "+faststart",
                        str(dest),
                    ]
                )
                if process.returncode != 0:
                    process = run_ffmpeg(
                        [
                            "-y",
                            "-ss",
                            f"{start:.3f}",
                            "-i",
                            str(source),
                            "-t",
                            f"{duration:.3f}",
                            "-c:v",
                            "libx264",
                            "-preset",
                            "veryfast",
                            "-crf",
                            "23",
                            "-an",
                            "-movflags",
                            "+faststart",
                            str(dest),
                        ]
                    )
                    if process.returncode != 0:
                        raise RuntimeError(ffmpeg_error_text(process))


class MediaTools:
    @staticmethod
    def extract_audio(source: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        process = run_ffmpeg(
            [
                "-y",
                "-i",
                str(source),
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(dest),
            ]
        )
        if process.returncode != 0:
            raise RuntimeError(ffmpeg_error_text(process))
        if not dest.exists() or dest.stat().st_size == 0:
            raise RuntimeError("这段录像里没有可分离的音频。")

    @staticmethod
    def extract_frame(source: Path, seconds: float, width: int = 720) -> bytes:
        process = run_ffmpeg(
            [
                "-ss",
                f"{max(0.0, seconds):.3f}",
                "-i",
                str(source),
                "-an",
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:-2",
                "-f",
                "image2pipe",
                "-vcodec",
                "gif",
                "pipe:1",
            ]
        )
        if process.returncode != 0 or not process.stdout:
            raise RuntimeError(ffmpeg_error_text(process) or "无法提取预览画面。")
        return process.stdout


class RegionSelector:
    def __init__(self, parent: tk.Tk, on_done: Callable[[tuple[int, int, int, int] | None], None]) -> None:
        self.parent = parent
        self.on_done = on_done
        self.start: tuple[int, int] | None = None
        self.rect: tuple[int, int, int, int] | None = None
        self.vx, self.vy, self.vw, self.vh = virtual_screen()

        self.window = tk.Toplevel(parent)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-alpha", 0.32)
        except tk.TclError:
            pass
        self.window.configure(bg=Theme.overlay)
        self.window.geometry(f"{self.vw}x{self.vh}+{self.vx}+{self.vy}")
        self.window.bind("<Escape>", lambda _e: self._finish(None))
        self.window.bind("<Return>", lambda _e: self._confirm())

        self.canvas = tk.Canvas(
            self.window,
            bg=Theme.overlay,
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", lambda _e: self._confirm())

        self.hint = self.canvas.create_text(
            self.vw // 2,
            48,
            text="拖动鼠标选择录制区域    Enter 确认    Esc 取消",
            fill="white",
            font=("Microsoft YaHei UI", 16, "bold"),
        )
        self.box = self.canvas.create_rectangle(0, 0, 0, 0, outline="#7aa3ff", width=3)
        self.size_label = self.canvas.create_text(0, 0, fill="white", font=Fonts.ui_bold)

        self.toolbar = tk.Frame(self.window, bg=Theme.header)
        tk.Label(
            self.toolbar,
            text="选择区域后确认即可开始框选录屏",
            bg=Theme.header,
            fg=Theme.text_dim,
            font=Fonts.ui,
        ).pack(side="left", padx=16, pady=10)
        PillButton(self.toolbar, "取消", lambda: self._finish(None), variant="ghost", width=8).pack(
            side="right", padx=(0, 12), pady=8
        )
        PillButton(self.toolbar, "确认区域", self._confirm, variant="primary", width=10).pack(
            side="right", padx=8, pady=8
        )
        self.toolbar.place(x=self.vw // 2 - 240, y=self.vh - 78, width=480, height=56)

        self.window.grab_set()
        self.window.focus_force()

    def _press(self, event: tk.Event) -> None:
        self.start = (event.x, event.y)
        self.rect = None

    def _drag(self, event: tk.Event) -> None:
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        self.rect = (left, top, right, bottom)
        self.canvas.coords(self.box, left, top, right, bottom)
        width, height = even_size(right - left, bottom - top)
        self.canvas.coords(self.size_label, left + 8, max(20, top - 18))
        self.canvas.itemconfigure(self.size_label, text=f"{width} × {height}", anchor="w")

    def _release(self, event: tk.Event) -> None:
        self._drag(event)

    def _confirm(self) -> None:
        if self.rect is None:
            return
        left, top, right, bottom = self.rect
        width, height = even_size(right - left, bottom - top)
        if width < 32 or height < 32:
            return
        region = (self.vx + left, self.vy + top, width, height)
        self._finish(region)

    def _finish(self, region: tuple[int, int, int, int] | None) -> None:
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        self.window.destroy()
        self.on_done(region)


class RecordingHUD:
    EXPANDED_W = 408
    EXPANDED_H = 108
    COLLAPSED_W = 28
    COLLAPSED_H = 120

    def __init__(
        self,
        parent: tk.Tk,
        duration_var: tk.StringVar,
        region: tuple[int, int, int, int],
        on_pause: Callable[[], None],
        on_stop: Callable[[], None],
        filename_var: tk.StringVar,
    ) -> None:
        self.on_pause = on_pause
        self.on_stop = on_stop
        self.duration_var = duration_var
        self.expanded = True
        self._hide_job: str | None = None
        self._auto_collapse_job: str | None = None
        self.monitor = self._monitor_for_region(region)
        self._paused = False

        self.window = tk.Toplevel(parent)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=Theme.header)
        self.window.protocol("WM_DELETE_WINDOW", on_stop)

        self.expanded_frame = tk.Frame(self.window, bg=Theme.header, padx=12, pady=8)
        controls = tk.Frame(self.expanded_frame, bg=Theme.header)
        controls.pack(fill="x")
        self.dot = tk.Canvas(controls, width=14, height=14, bg=Theme.header, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 8))
        self.dot.create_oval(2, 2, 12, 12, fill=Theme.record, outline="")
        tk.Label(controls, text="REC", bg=Theme.header, fg=Theme.record, font=Fonts.ui_bold).pack(
            side="left"
        )
        tk.Label(
            controls,
            textvariable=duration_var,
            bg=Theme.header,
            fg=Theme.text,
            font=Fonts.mono,
        ).pack(side="left", padx=12)
        self.pause_button = PillButton(controls, "暂停", on_pause, variant="ghost", width=6)
        self.pause_button.pack(side="left", padx=(8, 6))
        self.pause_button.set_enabled(False)
        self.stop_button = PillButton(controls, "停止", on_stop, variant="danger", width=6)
        self.stop_button.pack(side="left")
        name_row = tk.Frame(self.expanded_frame, bg=Theme.header)
        name_row.pack(fill="x", pady=(8, 0))
        tk.Label(name_row, text="保存名称", bg=Theme.header, fg=Theme.text_dim,
                 font=Fonts.small).pack(side="left", padx=(0, 10))
        self.filename_entry = RoundedEntry(name_row, filename_var, width=24)
        self.filename_entry.pack(side="left", fill="x", expand=True)
        self.filename_entry.entry.bind("<FocusOut>", lambda _e: self._schedule_hide())

        self.collapsed_frame = tk.Frame(self.window, bg=Theme.record)
        tab = tk.Frame(self.collapsed_frame, bg=Theme.header)
        tab.pack(fill="both", expand=True, padx=(3, 0))
        self.collapsed_dot = tk.Canvas(tab, width=12, height=self.COLLAPSED_H, bg=Theme.header, highlightthickness=0)
        self.collapsed_dot.pack(fill="both", expand=True)
        self.collapsed_dot.create_oval(3, 18, 13, 28, fill=Theme.record, outline="")
        self.collapsed_dot.create_text(
            8,
            68,
            text="REC",
            fill=Theme.text_dim,
            font=("Microsoft YaHei UI", 8, "bold"),
            angle=90,
        )

        self.window.bind("<Enter>", self._on_enter)
        self.window.bind("<Leave>", self._on_leave)
        for widget in (self.expanded_frame, self.collapsed_frame, tab, self.collapsed_dot):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

        try:
            self.window.wm_attributes("-toolwindow", True)
        except tk.TclError:
            pass
        self._show_expanded()
        self.window.update_idletasks()
        exclude_window_from_capture(self.window)
        self._auto_collapse_job = self.window.after(1600, self._collapse_if_outside)

    @staticmethod
    def _monitor_for_region(region: tuple[int, int, int, int]) -> dict[str, int | bool]:
        cx = region[0] + region[2] // 2
        cy = region[1] + region[3] // 2
        for monitor in list_monitors():
            x, y, width, height = int(monitor["x"]), int(monitor["y"]), int(monitor["w"]), int(monitor["h"])
            if x <= cx < x + width and y <= cy < y + height:
                return monitor
        monitors = list_monitors()
        return next((item for item in monitors if item["primary"]), monitors[0])

    def _edge_pos(self, width: int, height: int) -> tuple[int, int]:
        mx, my = int(self.monitor["x"]), int(self.monitor["y"])
        mw, mh = int(self.monitor["w"]), int(self.monitor["h"])
        x = mx + mw - width
        y = my + max(8, (mh - height) // 2)
        y = min(y, my + mh - height - 8)
        return x, y

    def _show_expanded(self) -> None:
        self.expanded = True
        self.collapsed_frame.pack_forget()
        self.expanded_frame.pack(fill="both", expand=True)
        x, y = self._edge_pos(self.EXPANDED_W, self.EXPANDED_H)
        self.window.geometry(f"{self.EXPANDED_W}x{self.EXPANDED_H}+{x}+{y}")

    def _show_collapsed(self) -> None:
        self.expanded = False
        self.expanded_frame.pack_forget()
        self.collapsed_frame.pack(fill="both", expand=True)
        x, y = self._edge_pos(self.COLLAPSED_W, self.COLLAPSED_H)
        self.window.geometry(f"{self.COLLAPSED_W}x{self.COLLAPSED_H}+{x}+{y}")

    def _pointer_inside(self) -> bool:
        try:
            px, py = self.window.winfo_pointerxy()
            wx, wy = self.window.winfo_rootx(), self.window.winfo_rooty()
            ww, wh = self.window.winfo_width(), self.window.winfo_height()
        except tk.TclError:
            return False
        return wx <= px < wx + ww and wy <= py < wy + wh

    def _on_enter(self, _event: tk.Event | None = None) -> None:
        self._cancel_hide()
        if not self.expanded:
            self._expand()

    def _on_leave(self, _event: tk.Event | None = None) -> None:
        self._schedule_hide()

    def _cancel_hide(self) -> None:
        if self._hide_job is not None:
            try:
                self.window.after_cancel(self._hide_job)
            except tk.TclError:
                pass
            self._hide_job = None

    def _schedule_hide(self) -> None:
        self._cancel_hide()
        try:
            self._hide_job = self.window.after(280, self._collapse_if_outside)
        except tk.TclError:
            pass

    def _collapse_if_outside(self) -> None:
        self._hide_job = None
        if self.window.focus_get() == self.filename_entry.entry:
            return
        if not self._pointer_inside():
            self._collapse()

    def _expand(self) -> None:
        if self.expanded:
            return
        self._show_expanded()

    def _collapse(self) -> None:
        if not self.expanded:
            return
        self._show_collapsed()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.pause_button.configure_text("继续" if paused else "暂停")
        color = Theme.warning if paused else Theme.record
        self.dot.delete("all")
        self.dot.create_oval(2, 2, 12, 12, fill=color, outline="")
        self.collapsed_dot.delete("all")
        self.collapsed_dot.create_oval(3, 18, 13, 28, fill=color, outline="")
        self.collapsed_dot.create_text(
            8,
            68,
            text="REC",
            fill=Theme.text_dim,
            font=("Microsoft YaHei UI", 8, "bold"),
            angle=90,
        )
        self.collapsed_frame.configure(bg=color)

    def close(self) -> None:
        self._cancel_hide()
        if self._auto_collapse_job is not None:
            try:
                self.window.after_cancel(self._auto_collapse_job)
            except tk.TclError:
                pass
            self._auto_collapse_job = None
        try:
            self.window.destroy()
        except tk.TclError:
            pass


class PathPickerDialog:
    def __init__(
        self,
        parent: tk.Tk,
        title: str,
        initial_dir: Path,
        on_select: Callable[[Path], None],
        select_mode: str = "folder",
        suffixes=None,
        multiple: bool = False,
    ) -> None:
        self.parent = parent
        self.on_select = on_select
        self.select_mode = select_mode
        self.suffixes = suffixes
        self.multiple = multiple
        self.current_dir = self._nearest_existing(initial_dir)
        self.items: list[tuple[Path, bool]] = []

        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("720x500")
        self.window.minsize(600, 420)
        self.window.configure(bg=Theme.bg)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.grab_set()

        self.address_var = tk.StringVar(value=str(self.current_dir))
        self.selection_var = tk.StringVar(value="")
        self._build_ui()
        self.address_var.trace_add("write", lambda *_: self.confirm_button.set_enabled(True))
        self._load_directory(self.current_dir)
        self.drop = WindowsFileDrop(self.window, self._drop_paths)
        self._focus_job = self.window.after(50, self.window.focus_force)

    def _build_ui(self) -> None:
        frame = tk.Frame(self.window, bg=Theme.bg, padx=18, pady=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)

        common = tk.Frame(frame, bg=Theme.bg)
        common.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        tk.Label(common, text="磁盘", bg=Theme.bg, fg=Theme.muted, font=Fonts.small).pack(
            side="left", padx=(0, 8)
        )
        for drive in self._available_drives():
            self._chip(common, str(drive), lambda target=drive: self._load_directory(target)).pack(
                side="left", padx=(0, 6)
            )
        places = [
            ("桌面", Path.home() / "Desktop"),
            ("下载", Path.home() / "Downloads"),
            ("文档", Path.home() / "Documents"),
            ("视频", Path.home() / "Videos"),
            ("音乐", Path.home() / "Music"),
        ]
        for label, path in places:
            if path.is_dir():
                self._chip(common, label, lambda target=path: self._load_directory(target)).pack(
                    side="left", padx=(0, 6)
                )

        address = tk.Frame(frame, bg=Theme.bg)
        address.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        address.columnconfigure(1, weight=1)
        PillButton(address, "上一级", self.go_up, variant="ghost", width=8).grid(row=0, column=0, padx=(0, 8))
        self.address_entry = entry = tk.Entry(
            address,
            textvariable=self.address_var,
            bg=Theme.input_bg,
            fg=Theme.text,
            insertbackground=Theme.text,
            relief="flat",
            highlightthickness=1,
            highlightbackground=Theme.border,
            highlightcolor=Theme.accent,
            font=Fonts.ui,
        )
        entry.grid(row=0, column=1, sticky="ew", ipady=7)
        entry.bind("<Return>", lambda _event: self.go_to_address())
        entry.bind("<Control-a>", lambda _e: (entry.selection_range(0, "end"), "break")[-1])
        self.address_menu = tk.Menu(entry, tearoff=False)
        for label, event in (("剪切", "<<Cut>>"), ("复制", "<<Copy>>"), ("粘贴", "<<Paste>>")):
            self.address_menu.add_command(label=label, command=lambda event=event: entry.event_generate(event))
        self.address_menu.add_command(label="全选", command=lambda: entry.selection_range(0, "end"))
        entry.bind("<Button-3>", lambda event: self.address_menu.tk_popup(event.x_root, event.y_root))
        PillButton(address, "转到", self.go_to_address, variant="ghost", width=6).grid(
            row=0, column=2, padx=(8, 0)
        )

        hint = {
            "folder": "可粘贴完整目录后直接确认，或把目录拖入此窗口。",
            "audio": "可粘贴完整文件路径，或拖入 MP3 文件。",
            "media": "地址栏可粘贴完整路径并回车；也可直接拖入文件。",
        }[self.select_mode]
        if self.multiple:
            hint = "Ctrl / Shift 多选；粘贴路径后回车定位；拖入文件或目录可直接添加（仅本层）。"
        tk.Label(frame, text=hint, bg=Theme.bg, fg=Theme.muted, font=Fonts.small).grid(
            row=2, column=0, sticky="w"
        )

        list_frame = tk.Frame(frame, bg=Theme.card, highlightbackground=Theme.border, highlightthickness=1)
        list_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 10))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(
            list_frame,
            borderwidth=0,
            highlightthickness=0,
            background=Theme.card,
            foreground=Theme.text,
            selectbackground=Theme.accent_soft,
            selectforeground=Theme.text,
            font=Fonts.ui,
            activestyle="none",
            selectmode="extended" if self.multiple else "browse",
        )
        self.listbox.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.listbox.bind("<<ListboxSelect>>", self._selection_changed)
        self.listbox.bind("<Double-Button-1>", self._open_selected)
        scrollbar = SlimScrollbar(
            list_frame,
            orient="vertical",
            command=self.listbox.yview,
            bg=Theme.card_alt,
            troughcolor=Theme.card,
            activebackground=Theme.accent,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        tk.Label(
            frame, textvariable=self.selection_var, bg=Theme.bg, fg=Theme.muted, font=Fonts.small
        ).grid(row=4, column=0, sticky="w", pady=(0, 10))

        actions = tk.Frame(frame, bg=Theme.bg)
        actions.grid(row=5, column=0, sticky="ew")
        if self.select_mode == "folder":
            PillButton(actions, "新建文件夹", self.create_folder, variant="ghost", width=10).pack(side="left")
        elif self.multiple:
            PillButton(actions, "添加当前目录", lambda: self._finish([self.current_dir]),
                       variant="ghost", width=13).pack(side="left")
        PillButton(actions, "取消", self.close, variant="ghost", width=8).pack(side="right")
        confirm_text = "使用此文件夹" if self.select_mode == "folder" else "打开文件"
        self.confirm_button = PillButton(
            actions,
            confirm_text,
            self.confirm,
            variant="primary",
            width=12,
        )
        self.confirm_button.pack(side="right", padx=(0, 8))
        if self.select_mode != "folder":
            self.confirm_button.set_enabled(False)

    def _chip(self, master: tk.Misc, text: str, command: Callable[[], None]) -> tk.Label:
        label = tk.Label(
            master,
            text=text,
            bg=Theme.card,
            fg=Theme.text_dim,
            font=Fonts.small,
            padx=10,
            pady=5,
            cursor="hand2",
        )
        label.bind("<Button-1>", lambda _e: command())
        label.bind("<Enter>", lambda _e: label.configure(bg=Theme.card_alt, fg=Theme.text))
        label.bind("<Leave>", lambda _e: label.configure(bg=Theme.card, fg=Theme.text_dim))
        return label

    @staticmethod
    def _nearest_existing(path: Path) -> Path:
        path = path.expanduser()
        while not path.is_dir() and path != path.parent:
            path = path.parent
        return path if path.is_dir() else Path.home()

    @staticmethod
    def _available_drives() -> list[Path]:
        if os.name != "nt":
            return [Path("/")]
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [Path(f"{chr(ord('A') + index)}:\\") for index in range(26) if mask & (1 << index)]

    def _accepted_file(self, name: str) -> bool:
        lower = name.lower()
        if self.suffixes is not None:
            return Path(name).suffix.lower() in self.suffixes
        if self.select_mode == "audio":
            return lower.endswith(".mp3")
        if self.select_mode == "media":
            return Path(name).suffix.lower() in EDITOR_SUFFIXES
        return False

    def _load_directory(self, path: Path) -> None:
        path = self._nearest_existing(path)
        try:
            entries = list(os.scandir(path))
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"无法打开这个位置：\n{error}", parent=self.window)
            return

        folders = sorted((item for item in entries if item.is_dir()), key=lambda item: item.name.casefold())
        files = []
        if self.select_mode != "folder":
            files = sorted(
                (item for item in entries if item.is_file() and self._accepted_file(item.name)),
                key=lambda item: item.name.casefold(),
            )

        self.current_dir = path
        self.address_var.set(str(path))
        self.selection_var.set("")
        self.items = [(Path(item.path), True) for item in folders]
        self.items.extend((Path(item.path), False) for item in files)
        self.listbox.delete(0, tk.END)
        for item, is_folder in self.items:
            if is_folder:
                prefix = "📁"
            elif item.suffix.lower() == ".mp3":
                prefix = "🎵"
            else:
                prefix = "🎬"
            self.listbox.insert(tk.END, f"{prefix}  {item.name}")
        if self.select_mode != "folder":
            self.confirm_button.set_enabled(False)

    def _selected_item(self) -> tuple[Path, bool] | None:
        selected = self.listbox.curselection()
        if not selected:
            return None
        index = int(selected[0])
        return self.items[index] if index < len(self.items) else None

    def _selection_changed(self, _event: tk.Event) -> None:
        item = self._selected_item()
        if item is None:
            return
        path, is_folder = item
        self.selection_var.set(str(path))
        if self.select_mode != "folder":
            self.confirm_button.set_enabled(
                any(not self.items[i][1] for i in self.listbox.curselection()))

    def _open_selected(self, _event: tk.Event) -> None:
        item = self._selected_item()
        if item is None:
            return
        path, is_folder = item
        if is_folder:
            self._load_directory(path)
        elif self.select_mode != "folder":
            self._finish(path)

    def go_up(self) -> None:
        self._load_directory(self.current_dir.parent)

    def go_to_address(self) -> None:
        try:
            path = single_path(self.address_var.get(), self.current_dir)
        except (ValueError, OSError) as error:
            self.selection_var.set(str(error))
            return
        if path.is_dir():
            self._load_directory(path)
        elif path.is_file() and self.select_mode != "folder" and self._accepted_file(path.name):
            self._load_directory(path.parent)
            index = next(i for i, item in enumerate(self.items) if item[0] == path)
            self.listbox.selection_set(index)
            self.listbox.see(index)
            self._selection_changed(None)
        else:
            self.selection_var.set("路径不存在，或此入口不支持该文件格式。")

    def _drop_paths(self, paths, _target=None):
        if self.multiple:
            self._finish(paths)
        elif len(paths) != 1:
            self.selection_var.set("此入口一次只能选择一个文件或目录。")
        else:
            self.address_var.set(str(paths[0]))
            self.go_to_address()

    def create_folder(self) -> None:
        name = simpledialog.askstring(APP_TITLE, "新文件夹名称：", parent=self.window)
        if not name:
            return
        if any(character in name for character in '<>:"/\\|?*'):
            messagebox.showwarning(APP_TITLE, "文件夹名称包含无效字符。", parent=self.window)
            return
        try:
            (self.current_dir / name).mkdir(exist_ok=False)
            self._load_directory(self.current_dir)
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"无法创建文件夹：\n{error}", parent=self.window)

    def confirm(self) -> None:
        # An edited address takes precedence over the old directory/list selection.
        try:
            address = single_path(self.address_var.get(), self.current_dir)
        except (ValueError, OSError) as error:
            self.selection_var.set(str(error))
            return
        if self.select_mode == "folder":
            if address.is_dir():
                self._finish(address)
            else:
                self.selection_var.set("保存目录不存在，请先使用「新建文件夹」。")
            return
        if address != self.current_dir:
            self.go_to_address()
            if address.is_file() and self._accepted_file(address.name):
                self._finish([address] if self.multiple else address)
            return
        if self.select_mode != "folder":
            if self.multiple:
                paths = [self.items[i][0] for i in self.listbox.curselection()
                         if not self.items[i][1]]
                if paths:
                    self._finish(paths)
                return
            item = self._selected_item()
            if item is None or item[1]:
                return
            self._finish(item[0])
        else:
            self._finish(self.current_dir)

    def _finish(self, path: Path) -> None:
        if self.multiple and isinstance(path, Path):
            path = [path]
        self.drop.close()
        self.window.after_cancel(self._focus_job)
        self.window.grab_release()
        self.window.destroy()
        self.on_select(path)

    def close(self) -> None:
        self.drop.close()
        self.window.after_cancel(self._focus_job)
        self.window.grab_release()
        self.window.destroy()


class RecorderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self._app_icons = [ImageTk.PhotoImage(studio_icon().resize((size, size), Image.Resampling.LANCZOS),
                                             master=root) for size in (16, 32, 48, 64)]
        self.root.iconphoto(True, *self._app_icons)
        self.root.geometry("1140x850")
        self.root.minsize(1040, 700)
        self.root.configure(bg=Theme.bg)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.recorder: SystemAudioRecorder | ScreenRecorder | None = None
        self.state = "idle"
        self.media_path: Path | None = None
        self.last_recording_path: Path | None = None
        self._recording_initial_name = ""
        self._recording_stamp = ""
        self.media_kind = "audio"
        self.audio_duration = 0.0
        self.split_points: list[float] = []
        self.player_state = "stopped"
        self.player_base = 0.0
        self.slider_dragging = False
        self.mixer_ready = False
        self.preview_generation = 0
        self.last_preview_at = 0.0
        self.play_started_at = 0.0
        self.region: tuple[int, int, int, int] | None = None
        self.pending_start = False
        self.pulse_on = False
        self.hud: RecordingHUD | None = None
        self.hidden_for_capture = False
        self._poll_job = None
        self._timer_job = None

        default_folder = default_output_folder()
        self.folder_var = tk.StringVar(value=str(default_folder))
        self.status_var = tk.StringVar(value="就绪")
        self.duration_var = tk.StringVar(value="00:00:00")
        self.device_var = tk.StringVar(value="启动后自动捕获 Windows 默认播放设备")
        self.path_var = tk.StringVar(value="还没有开始录制")
        self.region_var = tk.StringVar(value="全屏将录制主显示器")
        self.audio_file_var = tk.StringVar(value="还没有载入文件")
        self.player_time_var = tk.StringVar(value="00:00:00 / 00:00:00")
        self.split_time_var = tk.StringVar(value="")
        self.export_status_var = tk.StringVar(value="")
        self.mode_var = tk.StringVar(value="audio")
        self.audio_source_var = tk.StringVar(value="system")
        self.noise_reduction_var = tk.BooleanVar(value=True)
        self.filename_var = tk.StringVar(value="")
        self.auto_zoom_var = tk.BooleanVar(value=True)
        self.smooth_cursor_var = tk.BooleanVar(value=True)
        self.cinematic_var = tk.BooleanVar(value=True)
        self.zoom_var = tk.StringVar(value="1.65")
        self.close_after_save = False
        self.format_var = tk.StringVar(value="MP3 · 128 kbps")
        self.breadcrumb_var = tk.StringVar(value="工作空间  /  录制")
        self.page_var = tk.StringVar(value="record")

        self._build_ui()
        self.file_drop = WindowsFileDrop(self.root, self._drop_paths)
        self.root.bind("<Control-v>", self._paste_files, add="+")
        self._poll_events()
        self._update_timers()
        self._refresh_mode()

    def _build_ui(self) -> None:
        toolbar = tk.Frame(self.root, bg=Theme.header, height=60)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)
        brand = tk.Frame(toolbar, bg=Theme.header)
        brand.pack(side="left", padx=22, pady=12)
        icon = tk.Canvas(brand, width=34, height=34, bg=Theme.header, highlightthickness=0)
        icon.pack(side="left")
        self._brand_icon = ImageTk.PhotoImage(
            studio_icon().resize((34, 34), Image.Resampling.LANCZOS), master=self.root)
        icon.create_image(0, 0, image=self._brand_icon, anchor="nw")
        tk.Label(brand, text=APP_TITLE, bg=Theme.header, fg=Theme.text,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=(10, 8))
        tk.Label(brand, text="POTATO FACTORY", bg=Theme.header, fg=Theme.muted,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Label(toolbar, textvariable=self.breadcrumb_var, bg=Theme.header, fg=Theme.muted,
                 font=Fonts.small).pack(side="left", padx=34)
        self.header_chip = Badge(toolbar, "就绪")
        self.header_chip.pack(side="right", padx=24)
        tk.Frame(self.root, bg=Theme.border, height=1).pack(fill="x")
        body = tk.Frame(self.root, bg=Theme.bg)
        body.pack(fill="both", expand=True)
        sidebar = tk.Frame(body, bg=Theme.sidebar, width=208)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="工作空间", bg=Theme.sidebar, fg=Theme.muted,
                 font=Fonts.small).pack(anchor="w", padx=24, pady=(28, 12))
        self.nav_record = NavButton(sidebar, "录制", lambda: self._show_page("record"))
        self.nav_record.pack(fill="x", padx=12)
        self.nav_edit = NavButton(sidebar, "剪辑与导出", lambda: self._show_page("edit"), icon="edit")
        self.nav_edit.pack(fill="x", padx=12, pady=(6, 0))
        self.nav_soften = NavButton(sidebar, "声音柔化", lambda: self._show_page("soften"), icon="audio")
        self.nav_soften.pack(fill="x", padx=12, pady=(6, 0))
        self.nav_audio_convert = NavButton(sidebar, "视频转音频",
                                           lambda: self.open_conversion_page("audio"), icon="convert")
        self.nav_audio_convert.pack(fill="x", padx=12, pady=(6, 0))
        self.nav_compress = NavButton(sidebar, "视频压缩",
                                      lambda: self.open_conversion_page("compress"), icon="compress")
        self.nav_compress.pack(fill="x", padx=12, pady=(6, 0))
        tk.Frame(sidebar, bg=Theme.border, height=1).pack(fill="x", padx=24, pady=(24, 16))
        tk.Label(sidebar, text="你的本地媒体工作台", bg=Theme.sidebar, fg=Theme.text_dim,
                 font=Fonts.small).pack(anchor="w", padx=24)
        tk.Label(sidebar, text="录下灵感\n整理声音\n轻装分享", bg=Theme.sidebar, fg=Theme.muted,
                 font=Fonts.small, justify="left", height=5).pack(anchor="w", padx=24, pady=(4, 0))
        footer = tk.Frame(sidebar, bg=Theme.sidebar)
        footer.pack(side="bottom", fill="x", padx=24, pady=24)
        tk.Label(footer, text=APP_AUTHOR, bg=Theme.sidebar, fg=Theme.text_dim,
                 font=Fonts.small).pack(anchor="w", pady=(0, 12))
        self.open_recordings_button = PillButton(
            footer, "打开录制文件夹", self.open_recordings_folder, variant="ghost", width=14)
        self.open_recordings_button.pack(anchor="w", pady=(0, 14))
        tk.Label(footer, text="所有素材，留在本地。", bg=Theme.sidebar, fg=Theme.text_dim,
                 font=Fonts.small).pack(anchor="w")
        tk.Label(footer, text="MP3 / MP4  ·  最长 12 小时", bg=Theme.sidebar, fg=Theme.muted,
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(6, 0))
        tk.Frame(body, bg=Theme.border, width=1).pack(side="left", fill="y")
        self.main = tk.Frame(body, bg=Theme.bg)
        self.main.pack(side="left", fill="both", expand=True)
        self.record_page = tk.Frame(self.main, bg=Theme.bg)
        self.edit_page = tk.Frame(self.main, bg=Theme.bg)
        self._build_record_page()
        self._build_edit_page()
        self.soften_page = AudioSoftenerPage(self)
        self.audio_convert_page = MediaConversionPage(self, "audio")
        self.compress_page = MediaConversionPage(self, "compress")
        self.root.bind("<MouseWheel>", self._scroll_record_page, add="+")
        self._show_page("record")

    def _scroll_area(self, page: tk.Frame, attribute: str) -> tk.Frame:
        canvas = tk.Canvas(page, bg=Theme.bg, highlightthickness=0)
        setattr(self, attribute, canvas)
        scrollbar = SlimScrollbar(page, command=canvas.yview, troughcolor=Theme.bg)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        padding = tk.Frame(canvas, bg=Theme.bg)
        window = canvas.create_window(0, 0, window=padding, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        padding.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        inner = tk.Frame(padding, bg=Theme.bg)
        inner.pack(fill="both", expand=True, padx=28, pady=24)
        return inner

    def _build_record_page(self) -> None:
        inner = self._scroll_area(self.record_page, "record_canvas")
        inner.columnconfigure(0, weight=1, uniform="settings")
        inner.columnconfigure(1, weight=1, uniform="settings")
        heading = tk.Frame(inner, bg=Theme.bg)
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 20))
        titles = tk.Frame(heading, bg=Theme.bg)
        titles.pack(side="left")
        tk.Label(titles, text="准备，开始精彩。", bg=Theme.bg, fg=Theme.text, font=Fonts.title).pack(anchor="w")
        tk.Label(titles, text="声音、画面与镜头，一次准备就绪。", bg=Theme.bg,
                 fg=Theme.muted, font=Fonts.subtitle).pack(anchor="w", pady=(5, 0))
        self.modes = SegmentedControl(heading, [("audio", "仅录音"), ("fullscreen", "全屏"), ("region", "区域")],
                                      self.mode_var, command=self._refresh_mode)
        self.modes.pack(side="right", anchor="s", pady=(0, 2))
        hero = Card(inner)
        hero.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        hero.columnconfigure(0, weight=1)
        top = tk.Frame(hero, bg=Theme.card)
        top.grid(row=0, column=0, sticky="ew")
        self.status_dot = tk.Canvas(top, width=14, height=14, bg=Theme.card, highlightthickness=0)
        self.status_dot.pack(side="left")
        self._draw_status_dot(Theme.success)
        tk.Label(top, textvariable=self.status_var, bg=Theme.card, fg=Theme.text_dim, font=Fonts.small).pack(side="left", padx=6)
        tk.Label(top, textvariable=self.format_var, bg=Theme.card, fg=Theme.muted, font=("Segoe UI", 9)).pack(side="right")
        tk.Label(hero, textvariable=self.duration_var, bg=Theme.card, fg=Theme.text,
                 font=Fonts.timer).grid(row=1, column=0, pady=(2, 0))
        tk.Label(hero, textvariable=self.region_var, bg=Theme.card, fg=Theme.muted,
                 font=Fonts.small).grid(row=2, column=0, pady=(0, 14))
        buttons = tk.Frame(hero, bg=Theme.card)
        buttons.grid(row=3, column=0)
        self.start_button = PillButton(buttons, "开始录制", self.start_recording, variant="primary", width=14)
        self.start_button.pack(side="left")
        self.pause_button = PillButton(buttons, "暂停", self.toggle_pause, variant="ghost", width=8)
        self.pause_button.pack(side="left", padx=8)
        self.pause_button.set_enabled(False)
        self.stop_button = PillButton(buttons, "停止", self.stop_recording, variant="ghost", width=8)
        self.stop_button.pack(side="left")
        self.stop_button.set_enabled(False)
        self.region_button = PillButton(buttons, "框选区域", self.choose_region, variant="ghost", width=10)
        self.region_button.pack(side="left", padx=(8, 0))

        settings = Card(inner)
        settings.grid(row=2, column=0, sticky="nsew", padx=(0, 8))
        settings.columnconfigure(0, weight=1)
        tk.Label(settings, text="录制与保存", bg=Theme.card, fg=Theme.text, font=Fonts.section).grid(row=0, column=0, sticky="w")
        tk.Label(settings, text="声音来源", bg=Theme.card, fg=Theme.text_dim, font=Fonts.small).grid(row=1, column=0, sticky="w", pady=(16, 6))
        self.audio_sources = SegmentedControl(settings, [("system", "系统声音"), ("mixed", "系统 + 外部"), ("microphone", "仅外部")],
                                              self.audio_source_var, command=self._refresh_audio_hint)
        self.audio_sources.grid(row=2, column=0, sticky="w")
        tk.Label(settings, text="文件名称", bg=Theme.card, fg=Theme.text_dim, font=Fonts.small).grid(row=3, column=0, sticky="w", pady=(14, 6))
        self.filename_entry = RoundedEntry(settings, self.filename_var)
        self.filename_entry.grid(row=4, column=0, sticky="ew")
        tk.Label(settings, text="录制中也可修改；留空自动命名，同名自动编号。", bg=Theme.card,
                 fg=Theme.muted, font=("Microsoft YaHei UI", 8)).grid(row=5, column=0, sticky="w", pady=(5, 0))
        tk.Label(settings, text="保存位置", bg=Theme.card, fg=Theme.text_dim, font=Fonts.small).grid(row=6, column=0, sticky="w", pady=(12, 6))
        folder_row = tk.Frame(settings, bg=Theme.card)
        folder_row.grid(row=7, column=0, sticky="ew")
        folder_row.columnconfigure(0, weight=1)
        self.folder_entry = RoundedEntry(folder_row, self.folder_var, width=12)
        self.folder_entry.grid(row=0, column=0, sticky="ew")
        self.folder_entry.drop_paths = self._set_record_folder
        self.browse_button = PillButton(folder_row, "选择", self.choose_folder, variant="ghost", width=5)
        self.browse_button.grid(row=0, column=1, padx=(8, 0))
        tk.Label(folder_row, text="可粘贴完整目录或拖入文件夹", bg=Theme.card, fg=Theme.muted,
                 font=("Microsoft YaHei UI", 8)).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))
        audio_row = tk.Frame(settings, bg=Theme.card)
        audio_row.grid(row=8, column=0, sticky="ew", pady=(16, 4))
        tk.Label(audio_row, text="声音降噪", bg=Theme.card, fg=Theme.text_dim,
                 font=Fonts.ui).pack(side="left")
        self.noise_reduction_switch = Switch(audio_row, self.noise_reduction_var)
        self.noise_reduction_switch.pack(side="right")
        tk.Label(settings, text="录完轻度降噪；处理完成后自动清理临时文件，音乐录制可关闭。",
                 bg=Theme.card, fg=Theme.muted, font=("Microsoft YaHei UI", 8),
                 wraplength=330, justify="left").grid(row=9, column=0, sticky="w", pady=(3, 0))

        effects = Card(inner)
        effects.grid(row=2, column=1, sticky="nsew", padx=(8, 0))
        effects.columnconfigure(0, weight=1)
        title_row = tk.Frame(effects, bg=Theme.card)
        title_row.grid(row=0, column=0, sticky="ew")
        tk.Label(title_row, text="画面增强", bg=Theme.card, fg=Theme.text, font=Fonts.section).pack(side="left")
        tk.Label(title_row, text="录完自动生成", bg=Theme.card, fg=Theme.muted,
                 font=("Microsoft YaHei UI", 8)).pack(side="right")
        self.effect_checks = []
        for index, (title, description, variable) in enumerate([
            ("自动缩放", "点击后自然推近，停留后拉远", self.auto_zoom_var),
            ("光标平滑", "更顺滑、更稳定的指针轨迹", self.smooth_cursor_var),
            ("电影感运镜", "柔和缓动，配合圆角背景与阴影", self.cinematic_var),
        ]):
            row = tk.Frame(effects, bg=Theme.card)
            row.grid(row=index * 2 + 1, column=0, sticky="ew", pady=(14, 10))
            copy = tk.Frame(row, bg=Theme.card)
            copy.pack(side="left", fill="x", expand=True)
            tk.Label(copy, text=title, bg=Theme.card, fg=Theme.text_dim, font=Fonts.ui).pack(anchor="w")
            tk.Label(copy, text=description, bg=Theme.card, fg=Theme.muted,
                     font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(3, 0))
            switch = Switch(row, variable)
            switch.pack(side="right", padx=(8, 0))
            self.effect_checks.append(switch)
            if index < 2:
                tk.Frame(effects, bg="#f0f0f3", height=1).grid(row=index * 2 + 2, column=0, sticky="ew")
        zoom_row = tk.Frame(effects, bg=Theme.card)
        zoom_row.grid(row=7, column=0, sticky="ew", pady=(3, 0))
        tk.Label(zoom_row, text="缩放倍率", bg=Theme.card, fg=Theme.muted, font=Fonts.small).pack(side="left")
        self.zoom_menu = SegmentedControl(zoom_row, [("1.35", "1.35×"), ("1.65", "1.65×"), ("2.0", "2.0×")], self.zoom_var)
        self.zoom_menu.pack(side="right")
        info = tk.Frame(inner, bg=Theme.bg)
        info.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        tk.Label(info, textvariable=self.device_var, bg=Theme.bg, fg=Theme.muted,
                 font=Fonts.small, wraplength=810, justify="left").pack(anchor="w")
        tk.Label(info, textvariable=self.path_var, bg=Theme.bg, fg=Theme.muted,
                 font=("Microsoft YaHei UI", 8), wraplength=810, justify="left").pack(anchor="w", pady=(4, 0))

    def _build_edit_page(self) -> None:
        inner = self._scroll_area(self.edit_page, "edit_canvas")
        inner.columnconfigure(0, weight=1)
        heading = tk.Frame(inner, bg=Theme.bg)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        titles = tk.Frame(heading, bg=Theme.bg)
        titles.pack(side="left")
        tk.Label(titles, text="精彩，值得留下。", bg=Theme.bg, fg=Theme.text, font=Fonts.title).pack(anchor="w")
        tk.Label(titles, text="预览、切分与音轨分离，让素材更好用。", bg=Theme.bg,
                 fg=Theme.muted, font=Fonts.subtitle).pack(anchor="w", pady=(5, 0))
        PillButton(heading, "打开文件", self.choose_media, variant="primary", width=10).pack(side="right", anchor="s")
        preview_card = Card(inner)
        preview_card.grid(row=1, column=0, sticky="ew")
        preview_card.columnconfigure(0, weight=1)
        file_row = tk.Frame(preview_card, bg=Theme.card)
        file_row.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.open_media_folder_button = PillButton(
            file_row, "打开所在文件夹", self.open_media_folder, variant="ghost", width=13)
        self.open_media_folder_button.pack(side="right", padx=(12, 0))
        self.open_media_folder_button.set_enabled(False)
        tk.Label(file_row, textvariable=self.audio_file_var, bg=Theme.card, fg=Theme.text_dim,
                 font=Fonts.small, anchor="w", wraplength=480, justify="left").pack(
                     side="left", fill="x", expand=True)
        self.preview_canvas = tk.Canvas(preview_card, width=720, height=240, bg="#f4f4f7", highlightthickness=0)
        self.preview_canvas.grid(row=1, column=0, sticky="ew")
        self._show_preview_message("打开一段录音或录像，开始整理你的精彩片段。")
        self.preview_canvas.bind("<Configure>", self._resize_preview)
        self.editor_path_input = PathInput(preview_card, self._open_input_paths, self.export_status_var)
        self.editor_path_input.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        tk.Label(preview_card, text="可粘贴路径或拖入文件；目录可直接定位后选择，剪辑一次打开一个文件。",
                 bg=Theme.card, fg=Theme.muted, font=Fonts.small).grid(
                     row=4, column=0, sticky="w", pady=(6, 0))
        tk.Label(preview_card, textvariable=self.export_status_var, bg=Theme.card, fg=Theme.text_dim,
                 font=Fonts.small, wraplength=660, justify="left").grid(row=5, column=0, sticky="w")
        player = tk.Frame(preview_card, bg=Theme.card)
        player.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        player.columnconfigure(1, weight=1)
        self.play_button = PillButton(player, "播放", self.toggle_playback, variant="primary", width=8)
        self.play_button.grid(row=0, column=0, padx=(0, 12))
        self.progress_scale = PlaybackTimeline(player)
        self.progress_scale.grid(row=0, column=1, sticky="ew")
        self.progress_scale.bind("<<SeekStart>>", self._begin_seek)
        self.progress_scale.bind("<<SeekPreview>>", self._preview_seek)
        self.progress_scale.bind("<<SeekCommit>>", self._end_seek)
        tk.Label(player, textvariable=self.player_time_var, bg=Theme.card, fg=Theme.text,
                 font=("Consolas", 11, "bold")).grid(row=0, column=2, padx=(12, 0))
        split_card = Card(inner)
        split_card.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        tk.Label(split_card, text="片段切分", bg=Theme.card, fg=Theme.text, font=Fonts.section).pack(anchor="w")
        controls = tk.Frame(split_card, bg=Theme.card)
        controls.pack(fill="x", pady=(12, 10))
        PillButton(controls, "在当前位置切分", self.add_current_split, variant="primary", width=14).pack(side="left")
        tk.Label(controls, text="或输入时间", bg=Theme.card, fg=Theme.muted, font=Fonts.small).pack(side="left", padx=(16, 8))
        time_entry = RoundedEntry(controls, self.split_time_var, font=Fonts.mono, width=10)
        time_entry.pack(side="left")
        time_entry.entry.bind("<Return>", lambda _e: self.add_typed_split())
        PillButton(controls, "添加", self.add_typed_split, variant="ghost", width=6).pack(side="left", padx=8)
        tk.Label(controls, text="例：01:23:45", bg=Theme.card, fg=Theme.muted, font=Fonts.small).pack(side="left")
        list_row = tk.Frame(split_card, bg=Theme.card)
        list_row.pack(fill="x")
        self.split_listbox = tk.Listbox(list_row, height=3, borderwidth=0, highlightthickness=0,
            background=Theme.input_bg, foreground=Theme.text_dim, selectbackground=Theme.accent_soft,
            selectforeground=Theme.accent, font=Fonts.small, activestyle="none")
        self.split_listbox.pack(side="left", fill="x", expand=True)
        scrollbar = SlimScrollbar(list_row, command=self.split_listbox.yview, troughcolor=Theme.input_bg)
        scrollbar.pack(side="right", fill="y")
        self.split_listbox.configure(yscrollcommand=scrollbar.set)
        self._refresh_split_list()
        bottom = tk.Frame(inner, bg=Theme.bg)
        bottom.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        PillButton(bottom, "移除切分点", self.remove_selected_splits, variant="ghost", width=12).pack(side="left")
        self.export_audio_button = PillButton(bottom, "视频转音频", self.export_audio, variant="success", width=12)
        self.export_audio_button.pack(side="right")
        self.export_button = PillButton(bottom, "导出片段", self.export_segments, variant="primary", width=12)
        self.export_button.pack(side="right", padx=(0, 8))
        PillButton(bottom, "声音柔化", self.soften_current_media, variant="ghost", width=10).pack(side="left", padx=8)
        PillButton(bottom, "视频压缩", lambda: self.open_conversion_page("compress", use_current=True),
                   variant="ghost", width=10).pack(side="left")
        tk.Label(inner, textvariable=self.export_status_var, bg=Theme.bg, fg=Theme.muted,
                 font=Fonts.small).grid(row=4, column=0, sticky="w", pady=(8, 0))

    def _show_page(self, page: str) -> None:
        if self.state != "idle" and page != "record":
            return
        self.soften_page.stop_preview()
        if page != "edit":
            self.stop_playback()
        self.page_var.set(page)
        titles = {"record": "录制", "edit": "剪辑与导出", "soften": "声音柔化",
                  "audio_convert": "视频转音频", "compress": "视频压缩"}
        self.breadcrumb_var.set("工作空间  /  " + titles[page])
        pages = {"record": self.record_page, "edit": self.edit_page, "soften": self.soften_page,
                 "audio_convert": self.audio_convert_page, "compress": self.compress_page}
        for item in pages.values():
            item.pack_forget()
        pages[page].pack(fill="both", expand=True)
        self.nav_record.set_active(page == "record")
        self.nav_edit.set_active(page == "edit")
        self.nav_soften.set_active(page == "soften")
        self.nav_audio_convert.set_active(page == "audio_convert")
        self.nav_compress.set_active(page == "compress")

    def background_processing(self) -> bool:
        return any(page.running for page in
                   (self.soften_page, self.audio_convert_page, self.compress_page))

    def open_conversion_page(self, operation: str, use_current: bool = False) -> None:
        if self.state != "idle":
            return
        page = self.audio_convert_page if operation == "audio" else self.compress_page
        if use_current:
            page.use_current()
        self._show_page("audio_convert" if operation == "audio" else "compress")

    def soften_current_media(self) -> None:
        if self.state != "idle":
            return
        self.soften_page.use_current()
        self._show_page("soften")

    def _draw_status_dot(self, color: str) -> None:
        self.status_dot.delete("all")
        self.status_dot.create_oval(2, 2, 12, 12, fill=color, outline="")

    def _set_chip(self, text: str, fg: str, bg: str) -> None:
        names = {"IDLE": "就绪", "REC": "录制中", "PAUSED": "已暂停", "RENDER": "生成中", "SAVED": "已保存", "ERROR": "出错了"}
        self.header_chip.configure(text=names.get(text, text), fg=fg, bg=bg)

    def _refresh_mode(self) -> None:
        mode = self.mode_var.get()
        self.format_var.set("MP3 · 128 kbps" if mode == "audio" else "MP4 · 30 fps")
        self._refresh_audio_hint()
        if mode == "audio":
            self.region_var.set("仅录制系统正在播放的声音，生成 MP3")
            self.region_button.pack_forget()
            self._set_screen_options(False)
        elif mode == "fullscreen":
            x, y, width, height = primary_monitor_rect()
            width, height = even_size(width, height)
            self.region = (x, y, width, height)
            self.region_var.set(f"全屏录制主显示器  {width}×{height}")
            self.region_button.pack_forget()
            self._set_screen_options(self.state == "idle")
        else:
            if self.region:
                _x, _y, width, height = self.region
                self.region_var.set(f"已选择区域  {width}×{height}    可重新框选")
            else:
                self.region_var.set("还没有选择区域，开始前会先进入框选")
            self.region_button.pack(side="left", padx=(16, 0))
            self._set_screen_options(self.state == "idle")

    def _scroll_record_page(self, event: tk.Event) -> None:
        canvas = {"record": self.record_canvas, "edit": self.edit_canvas,
                  "soften": self.soften_canvas, "audio_convert": self.audio_convert_canvas,
                  "compress": self.compress_convert_canvas}[self.page_var.get()]
        canvas.yview_scroll(-int(event.delta / 120), "units")

    def _set_screen_options(self, enabled: bool) -> None:
        self.audio_sources.set_enabled(enabled)
        for check in self.effect_checks:
            check.configure(state="normal" if enabled else "disabled")
        self.zoom_menu.set_enabled(enabled)

    def _refresh_audio_hint(self) -> None:
        if self.state != "idle":
            return
        mode = "system" if self.mode_var.get() == "audio" else self.audio_source_var.get()
        hints = {
            "system": "系统声音（默认） · 捕获 Windows 默认播放设备",
            "mixed": "系统声音 + 外部声音 · 同时录入默认播放设备与默认麦克风",
            "microphone": "仅外部声音 · 使用 Windows 默认麦克风 / 输入设备",
        }
        self.device_var.set(hints[mode])

    def choose_folder(self) -> None:
        PathPickerDialog(
            self.root,
            "选择保存位置",
            self._nearest_existing_folder(self.folder_var.get()),
            lambda selected: self.folder_var.set(str(selected)),
        )

    def pick_input_files(self, suffixes, on_select, title="添加文件"):
        return PathPickerDialog(self.root, title,
                                self._nearest_existing_folder(self.folder_var.get()),
                                on_select, select_mode="media", suffixes=suffixes, multiple=True)

    def pick_output_folder(self, initial, on_select):
        return PathPickerDialog(self.root, "选择保存目录",
                                self._nearest_existing_folder(str(initial)), on_select)

    def _set_record_folder(self, paths):
        if self.state != "idle" or self.background_processing():
            self.status_var.set("请等待当前录制或处理结束后修改目录。")
            return
        if len(paths) == 1 and Path(paths[0]).is_dir():
            self.folder_var.set(str(paths[0]))
            self.status_var.set("已更新保存目录")
        else:
            self.status_var.set("保存位置请拖入一个文件夹。")

    def _open_input_paths(self, paths):
        if self.state != "idle" or self.background_processing():
            self.export_status_var.set("请等待当前录制或处理结束后打开文件。")
            return
        if len(paths) != 1:
            self.export_status_var.set("剪辑一次打开一个文件；批量处理请使用声音柔化或视频工具。")
            return
        path = single_path(str(paths[0]))
        self._show_page("edit")
        if path.is_dir():
            PathPickerDialog(self.root, "选择要剪辑的文件", path, self.load_media, select_mode="media")
        elif path.is_file() and path.suffix.lower() in EDITOR_SUFFIXES:
            self.load_media(path)
        else:
            self.export_status_var.set("文件不存在或格式不支持；剪辑支持 MP3、MP4、MOV、MKV、M4A。")

    def _drop_paths(self, paths, target=None):
        page = {"soften": self.soften_page, "audio_convert": self.audio_convert_page,
                "compress": self.compress_page}.get(self.page_var.get())
        if self.state != "idle" or self.background_processing():
            (page.status if page else self.status_var).set("请等待当前录制或处理完成后添加。")
            return
        widget = target
        while widget is not None:
            handler = getattr(widget, "drop_paths", None)
            if handler:
                handler(paths)
                return
            widget = getattr(widget, "master", None)
        if page:
            page.add_files(paths)
        elif self.page_var.get() == "record" and len(paths) == 1 and Path(paths[0]).is_dir():
            self._set_record_folder(paths)
        else:
            self._open_input_paths(paths)

    def _paste_files(self, event):
        # Entry/Text class bindings already implement ordinary text paste.
        if event.widget.winfo_class() in {"Entry", "TEntry", "Text", "Spinbox", "TCombobox"}:
            return
        try:
            paths = clipboard_paths(self.root)
            if paths:
                self._drop_paths(paths)
        except (tk.TclError, ValueError, OSError):
            self.status_var.set("剪贴板中没有可添加的文件路径。")
        return "break"

    def _open_folder(self, folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder.resolve()))
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"无法打开文件夹：\n{error}")

    def open_recordings_folder(self) -> None:
        if self.recorder is not None:
            folder = self.recorder.output_path.parent
        elif self.last_recording_path is not None:
            folder = self.last_recording_path.parent
        else:
            value = self.folder_var.get().strip()
            if not value:
                messagebox.showwarning(APP_TITLE, "请先选择保存位置。")
                return
            try:
                folder = single_path(value)
            except (ValueError, OSError) as error:
                messagebox.showwarning(APP_TITLE, str(error))
                return
        self._open_folder(folder)

    def open_media_folder(self) -> None:
        if self.media_path is not None:
            self._open_folder(self.media_path.parent)

    @staticmethod
    def _nearest_existing_folder(value: str) -> Path:
        try:
            folder = single_path(value) if value.strip() else Path.home()
        except (ValueError, OSError):
            folder = Path.home()
        while not folder.is_dir() and folder != folder.parent:
            folder = folder.parent
        return folder if folder.is_dir() else Path.home()

    def choose_region(self, start_after: bool = False) -> None:
        self.pending_start = start_after
        self.root.withdraw()

        def done(region: tuple[int, int, int, int] | None) -> None:
            if region is not None:
                self.region = clamp_region(region)
                self.mode_var.set("region")
                self._refresh_mode()
                if self.pending_start:
                    self.pending_start = False
                    self.start_recording()
                    return
            self.pending_start = False
            self._show_main_window()

        self.root.after(80, lambda: RegionSelector(self.root, done))

    def _show_main_window(self) -> None:
        try:
            self.root.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        self.root.deiconify()
        self.root.lift()

    def choose_media(self) -> None:
        initial = self.media_path.parent if self.media_path else Path(self.folder_var.get())
        PathPickerDialog(
            self.root,
            "选择要剪辑的录音或录像",
            self._nearest_existing_folder(str(initial)),
            self.load_media,
            select_mode="media",
        )

    def _ensure_mixer(self) -> None:
        if not self.mixer_ready:
            pygame.mixer.init(frequency=SAMPLE_RATE, channels=CHANNELS)
            self.mixer_ready = True

    def _show_preview_message(self, text: str) -> None:
        self._preview_message = text
        self._preview_payload = None
        self.preview_canvas.delete("all")
        width = max(int(self.preview_canvas.cget("width")), self.preview_canvas.winfo_width())
        height = max(1, int(self.preview_canvas.cget("height")))
        self.preview_canvas.create_text(width // 2, height // 2, text=text, fill=Theme.muted,
                                       font=Fonts.ui, width=max(100, width - 60), justify="center")
        self._preview_photo = None

    def _resize_preview(self, _event=None) -> None:
        if self._preview_payload is not None:
            self._set_preview_image(self._preview_payload)
        else:
            self._show_preview_message(self._preview_message)

    def load_media(self, path: Path) -> None:
        self.soften_page.stop_preview()
        self.stop_playback()
        suffix = path.suffix.lower()
        try:
            if suffix == ".mp3":
                self._ensure_mixer()
                duration = float(MP3(path).info.length)
                pygame.mixer.music.load(str(path))
                kind = "audio"
            else:
                find_ffmpeg()
                duration = media_duration(path)
                kind = "video"
        except Exception as error:
            messagebox.showerror(APP_TITLE, f"无法打开这个文件：\n{error}")
            return

        self.media_path = path
        self.open_media_folder_button.set_enabled(True)
        self.media_kind = kind
        self.audio_duration = duration
        self.player_base = 0.0
        self.player_state = "stopped"
        self.play_started_at = 0.0
        self.split_points.clear()
        icon = "录像" if kind == "video" else "录音"
        self.audio_file_var.set(f"{icon}：{path.name}")
        self.progress_scale.configure(to=max(duration, 0.001))
        self.progress_scale.set_enabled(True)
        self.progress_scale.set(0)
        self.player_time_var.set(f"00:00:00 / {format_time(duration)}")
        self.play_button.configure_text("播放")
        self.export_status_var.set("")
        self._refresh_split_list()
        if kind == "audio":
            self._show_preview_message("音频文件  ·  可试听并按时间切分成多个 MP3")
        else:
            self._show_preview_message("正在提取预览画面…")
            self._request_preview_frame(0.0, force=True)

    def _request_preview_frame(self, seconds: float, force: bool = False) -> None:
        if self.media_kind != "video" or self.media_path is None:
            return
        now = time.monotonic()
        if not force and now - self.last_preview_at < 0.35:
            return
        self.last_preview_at = now
        self.preview_generation += 1
        generation = self.preview_generation
        threading.Thread(
            target=self._extract_preview_frame,
            args=(self.media_path, seconds, generation),
            daemon=True,
        ).start()

    def _extract_preview_frame(self, path: Path, seconds: float, generation: int) -> None:
        try:
            payload = MediaTools.extract_frame(path, seconds)
            self.events.put(("preview_frame", (generation, payload)))
        except Exception:
            pass

    def _set_preview_image(self, payload: bytes) -> None:
        try:
            with Image.open(io.BytesIO(payload)) as source:
                image = source.convert("RGB")
            width = max(1, self.preview_canvas.winfo_width())
            height = max(1, self.preview_canvas.winfo_height())
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image, master=self.preview_canvas)
        except (OSError, tk.TclError):
            return
        self._preview_payload = payload
        self._preview_photo = photo
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(width // 2, height // 2, image=photo)

    def toggle_playback(self) -> None:
        if self.media_path is None:
            messagebox.showwarning(APP_TITLE, "请先打开一个 MP3 或 MP4 文件。")
            return
        try:
            if self.player_state == "playing":
                self.player_base = self.current_playback_position()
                if self.media_kind == "audio":
                    pygame.mixer.music.pause()
                self.player_state = "paused"
                self.play_button.configure_text("继续播放")
                return
            if self.player_base >= self.audio_duration - 0.05:
                self.player_base = 0.0
            if self.media_kind == "audio":
                self._ensure_mixer()
                pygame.mixer.music.play(start=self.player_base)
            else:
                self.play_started_at = time.monotonic()
                self._request_preview_frame(self.player_base, force=True)
            self.player_state = "playing"
            self.play_button.configure_text("暂停播放")
        except Exception as error:
            messagebox.showerror(APP_TITLE, f"播放失败：\n{error}")

    def stop_playback(self) -> None:
        if self.mixer_ready:
            pygame.mixer.music.stop()
        self.player_state = "stopped"
        self.player_base = 0.0
        if hasattr(self, "play_button"):
            self.play_button.configure_text("播放")

    def current_playback_position(self) -> float:
        if self.player_state == "playing":
            if self.media_kind == "video":
                elapsed = time.monotonic() - self.play_started_at
            else:
                elapsed = max(0.0, pygame.mixer.music.get_pos() / 1000.0)
            return min(self.player_base + elapsed, self.audio_duration)
        return min(self.player_base, self.audio_duration)

    def _begin_seek(self, _event: tk.Event) -> None:
        self.slider_dragging = True

    def _preview_seek(self, _event: tk.Event) -> None:
        position = float(self.progress_scale.get())
        self.player_time_var.set(f"{format_time(position)} / {format_time(self.audio_duration)}")
        self._request_preview_frame(position)

    def _end_seek(self, _event: tk.Event) -> None:
        self.slider_dragging = False
        self.player_base = float(self.progress_scale.get())
        self.player_time_var.set(f"{format_time(self.player_base)} / {format_time(self.audio_duration)}")
        if self.player_state == "playing":
            if self.media_kind == "audio":
                pygame.mixer.music.play(start=self.player_base)
            else:
                self.play_started_at = time.monotonic()
        self._request_preview_frame(self.player_base, force=True)

    def add_current_split(self) -> None:
        self._add_split_point(self.current_playback_position())

    def add_typed_split(self) -> None:
        try:
            seconds = parse_time(self.split_time_var.get())
        except ValueError as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return
        self._add_split_point(seconds)
        self.split_time_var.set("")

    def _add_split_point(self, seconds: float) -> None:
        if self.media_path is None:
            messagebox.showwarning(APP_TITLE, "请先打开一个文件。")
            return
        if seconds <= 0.05 or seconds >= self.audio_duration - 0.05:
            messagebox.showwarning(APP_TITLE, "切分点必须位于开始和结束之间。")
            return
        if any(abs(point - seconds) < 0.1 for point in self.split_points):
            messagebox.showwarning(APP_TITLE, "这个位置已经存在切分点。")
            return
        self.split_points.append(seconds)
        self.split_points.sort()
        self._refresh_split_list()

    def _refresh_split_list(self) -> None:
        self.split_listbox.delete(0, tk.END)
        if not self.split_points:
            self.split_listbox.insert(tk.END, "还没有切分点")
            return
        bounds = [0.0, *self.split_points, self.audio_duration]
        for index, point in enumerate(self.split_points, start=1):
            self.split_listbox.insert(
                tk.END,
                f"切分点 {index:02d}        {format_time(point, True)}",
            )
        self.split_listbox.insert(
            tk.END,
            f"将导出 {len(bounds) - 1} 个片段",
        )

    def remove_selected_splits(self) -> None:
        if not self.split_points:
            return
        selected = list(self.split_listbox.curselection())
        for index in reversed(selected):
            if index < len(self.split_points):
                self.split_points.pop(index)
        self._refresh_split_list()

    def export_segments(self) -> None:
        if self.media_path is None:
            messagebox.showwarning(APP_TITLE, "请先打开一个文件。")
            return
        if not self.split_points:
            messagebox.showwarning(APP_TITLE, "请至少添加一个切分点。")
            return
        PathPickerDialog(
            self.root,
            "选择片段保存位置",
            self.media_path.parent,
            self._start_export,
        )

    def export_audio(self) -> None:
        self.open_conversion_page("audio", use_current=True)

    def _start_export(self, parent: Path) -> None:
        if self.media_path is None:
            return
        export_folder = parent / f"{self.media_path.stem}_片段_{datetime.now():%Y%m%d_%H%M%S}"
        export_folder.mkdir(parents=True, exist_ok=False)
        bounds = [0.0, *self.split_points, self.audio_duration]
        self.export_button.set_enabled(False)
        self.export_audio_button.set_enabled(False)
        self.export_status_var.set("正在导出片段…")
        threading.Thread(target=self._export_worker, args=(export_folder, bounds), daemon=True).start()

    def _start_audio_export(self, parent: Path) -> None:
        if self.media_path is None:
            return
        dest = parent / f"{self.media_path.stem}_音频.mp3"
        if dest.exists():
            dest = parent / f"{self.media_path.stem}_音频_{datetime.now():%Y%m%d_%H%M%S}.mp3"
        self.export_button.set_enabled(False)
        self.export_audio_button.set_enabled(False)
        self.export_status_var.set("正在分离音频…")
        threading.Thread(target=self._audio_export_worker, args=(dest,), daemon=True).start()

    def _export_worker(self, folder: Path, bounds: list[float]) -> None:
        assert self.media_path is not None
        try:
            extension = ".mp4" if self.media_kind == "video" else ".mp3"
            outputs = []
            for index, (start, end) in enumerate(zip(bounds, bounds[1:]), start=1):
                name = (
                    f"{self.media_path.stem}_{index:02d}_"
                    f"{filename_time(start)}-{filename_time(end)}{extension}"
                )
                outputs.append(folder / name)
            splitter = VideoClipSplitter if self.media_kind == "video" else Mp3FrameSplitter
            splitter.split(
                self.media_path,
                outputs,
                bounds,
                lambda current, total: self.events.put(("split_progress", (current, total))),
            )
            self.events.put(("split_done", folder))
        except Exception as error:
            self.events.put(("split_error", str(error)))

    def _audio_export_worker(self, dest: Path) -> None:
        assert self.media_path is not None
        try:
            MediaTools.extract_audio(self.media_path, dest)
            self.events.put(("audio_done", dest))
        except Exception as error:
            self.events.put(("audio_error", str(error)))

    def start_recording(self) -> None:
        if self.state != "idle":
            return
        if self.background_processing():
            messagebox.showinfo(APP_TITLE, "正在处理媒体，请等待完成或取消后再录制。")
            return
        self.soften_page.stop_preview()
        folder_text = self.folder_var.get().strip()
        if not folder_text:
            messagebox.showwarning(APP_TITLE, "请先选择保存位置。")
            return
        mode = self.mode_var.get()
        try:
            folder = single_path(folder_text)
            self.folder_var.set(str(folder))
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._recording_stamp = stamp
            filename = output_filename(self.filename_var.get(), mode, stamp)
            if folder.exists() and not folder.is_dir():
                raise ValueError("保存位置不是文件夹。")
            output_path = available_path(folder, filename)
            ffmpeg = find_ffmpeg() if mode != "audio" or self.noise_reduction_var.get() else None
        except (ValueError, OSError) as error:
            messagebox.showerror(APP_TITLE, str(error))
            return
        if mode == "region" and self.region is None:
            self.choose_region(start_after=True)
            return
        self.stop_playback()
        if mode == "audio":
            self.recorder = SystemAudioRecorder(output_path, self.events,
                                                noise_reduction=self.noise_reduction_var.get(),
                                                ffmpeg_path=ffmpeg)
            self._arm_recorder(output_path)
            return
        if mode == "fullscreen" or self.region is None:
            self.region = primary_monitor_rect()
        self.region = clamp_region(self.region)
        self.recorder = ScreenRecorder(
            output_path, self.events, self.region, ffmpeg_path=ffmpeg,
            audio_mode=self.audio_source_var.get(),
            noise_reduction=self.noise_reduction_var.get(),
            effects=Effects(auto_zoom=self.auto_zoom_var.get(), smooth_cursor=self.smooth_cursor_var.get(),
                            cinematic=self.cinematic_var.get(), zoom=float(self.zoom_var.get())),
        )
        self._hide_for_capture()
        self._arm_recorder(output_path)
        self.root.after(280, self._start_hidden_recorder)

    def _start_hidden_recorder(self) -> None:
        if self.recorder is not None and self.state == "starting":
            self.recorder.start()

    def _arm_recorder(self, output_path: Path) -> None:
        self._recording_initial_name = self.filename_var.get().strip()
        self.state = "starting"
        self.status_var.set("正在启动…")
        self.path_var.set(f"文件：{output_path}")
        self._set_controls(recording=True)
        self.pause_button.set_enabled(False)
        if self.mode_var.get() == "audio" and self.recorder:
            self.recorder.start()

    def _hide_for_capture(self) -> None:
        self.hidden_for_capture = True
        if self.hud is not None:
            self.hud.close()
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.root.deiconify()
        self.root.update_idletasks()
        assert self.region is not None
        self.hud = RecordingHUD(
            self.root,
            self.duration_var,
            self.region,
            self.toggle_pause,
            self.stop_recording,
            self.filename_var,
        )
        self.root.withdraw()
        try:
            self.root.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        self.hud.window.deiconify()
        self.hud.window.lift()
        self.hud.window.attributes("-topmost", True)

    def _restore_from_capture(self) -> None:
        if self.hud is not None:
            self.hud.close()
            self.hud = None
        if self.hidden_for_capture:
            self._show_main_window()
            self.hidden_for_capture = False

    def toggle_pause(self) -> None:
        if self.recorder is None:
            return
        if self.state == "recording":
            self.recorder.pause()
            self.state = "paused"
            self.status_var.set("已暂停")
            self.pause_button.configure_text("继续")
            self._set_chip("PAUSED", Theme.warning, Theme.warning_soft)
            self._draw_status_dot(Theme.warning)
            if self.hud:
                self.hud.set_paused(True)
        elif self.state == "paused":
            self.recorder.resume()
            self.state = "recording"
            self.status_var.set("正在录制")
            self.pause_button.configure_text("暂停")
            self._set_chip("REC", "white", Theme.record)
            self._draw_status_dot(Theme.record)
            if self.hud:
                self.hud.set_paused(False)

    def stop_recording(self) -> None:
        if self.recorder is None or self.state in {"idle", "stopping"}:
            return
        try:
            output_filename(self.filename_var.get(), self.mode_var.get(), self._recording_stamp)
        except ValueError as error:
            messagebox.showwarning(APP_TITLE, str(error))
            entry = self.hud.filename_entry if self.hud else self.filename_entry
            entry.entry.focus_set()
            return
        if self.recorder.thread is None:
            self.state = "idle"
            self.status_var.set("已取消")
            self._set_controls(recording=False)
            self.recorder = None
            self._restore_from_capture()
            return
        self.state = "stopping"
        self.filename_entry.configure(state="disabled")
        self.status_var.set("正在保存…")
        self.pause_button.set_enabled(False)
        self.stop_button.set_enabled(False)
        if self.hud:
            self.hud.pause_button.set_enabled(False)
            self.hud.stop_button.set_enabled(False)
            self.hud.filename_entry.configure(state="disabled")
        self.recorder.stop()

    def _finalize_recording_name(self, path: Path) -> Path:
        name = self.filename_var.get().strip()
        if name == self._recording_initial_name:
            return path
        mode = "audio" if path.suffix.lower() == ".mp3" else "fullscreen"
        filename = output_filename(name, mode, self._recording_stamp)
        if filename == path.name:
            return path
        while True:
            destination = available_path(path.parent, filename)
            try:
                # Windows rename fails if a competing process creates the target.
                return path.rename(destination)
            except FileExistsError:
                continue

    def _set_controls(self, recording: bool) -> None:
        self.start_button.set_enabled(not recording)
        self.browse_button.set_enabled(not recording)
        self.region_button.set_enabled(not recording)
        self.folder_entry.configure(state="normal" if not recording else "disabled")
        self.filename_entry.configure(state="normal")
        self.modes.set_enabled(not recording)
        self.noise_reduction_switch.configure(state="disabled" if recording else "normal")
        self._set_screen_options(not recording and self.mode_var.get() != "audio")
        self.stop_button.set_enabled(recording)
        if not recording:
            self.pause_button.set_enabled(False)
            self.pause_button.configure_text("暂停")
            self.nav_edit.set_active(self.page_var.get() == "edit")

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "media_job":
                    page, kind, data = payload
                    page.handle_event(kind, data)
                elif event.startswith("soften_"):
                    self.soften_page.handle_event(event, payload)
                elif event == "started":
                    if self.state == "stopping":
                        continue
                    self.state = "recording"
                    self.status_var.set("正在录制")
                    self.device_var.set(f"捕获源：{payload}")
                    self.pause_button.set_enabled(True)
                    if self.hud:
                        self.hud.pause_button.set_enabled(True)
                    self._set_chip("REC", "white", Theme.record)
                    self._draw_status_dot(Theme.record)
                elif event == "processing":
                    self.state = "processing"
                    self.filename_entry.configure(state="disabled")
                    self.status_var.set(str(payload))
                    self.pause_button.set_enabled(False)
                    self.stop_button.set_enabled(False)
                    self._restore_from_capture()
                    self._set_chip("RENDER", Theme.accent, Theme.accent_soft)
                elif event == "render_progress":
                    self.status_var.set(f"正在生成电影感画面… {payload}%")
                elif event == "stopped":
                    details = payload
                    output_path = Path(details["path"])
                    try:
                        output_path = self._finalize_recording_name(output_path)
                    except (ValueError, OSError) as error:
                        messagebox.showwarning(
                            APP_TITLE, f"已按原名称保存：\n{output_path}\n\n更改名称失败：{error}")
                    self.last_recording_path = output_path
                    self.state = "idle"
                    self.status_var.set("已达到 12 小时并自动保存" if details["reached_limit"] else "已保存")
                    self.path_var.set(f"已保存：{output_path}")
                    self._set_controls(recording=False)
                    self.recorder = None
                    self._restore_from_capture()
                    self._set_chip("SAVED", Theme.success, Theme.success_soft)
                    self._draw_status_dot(Theme.success)
                    self.load_media(output_path)
                    self._show_page("edit")
                    if self.close_after_save:
                        self.root.after(0, self.on_close)
                elif event == "error":
                    self.state = "idle"
                    self.status_var.set("录制失败")
                    self._set_controls(recording=False)
                    self.recorder = None
                    self._restore_from_capture()
                    self._set_chip("ERROR", Theme.record, Theme.error_soft)
                    self._draw_status_dot(Theme.record)
                    messagebox.showerror(APP_TITLE, str(payload))
                    if self.close_after_save:
                        self.root.after(0, self.on_close)
                elif event == "split_progress":
                    current, total = payload
                    self.export_status_var.set(f"正在导出第 {current}/{total} 段…")
                elif event == "split_done":
                    self.export_button.set_enabled(True)
                    self.export_audio_button.set_enabled(True)
                    self.export_status_var.set(f"导出完成：{payload}")
                    messagebox.showinfo(APP_TITLE, f"片段导出完成：\n{payload}")
                elif event == "split_error":
                    self.export_button.set_enabled(True)
                    self.export_audio_button.set_enabled(True)
                    self.export_status_var.set("片段导出失败")
                    messagebox.showerror(APP_TITLE, f"片段导出失败：\n{payload}")
                elif event == "audio_done":
                    self.export_button.set_enabled(True)
                    self.export_audio_button.set_enabled(True)
                    self.export_status_var.set(f"音频已保存：{payload}")
                    messagebox.showinfo(APP_TITLE, f"音频分离完成：\n{payload}")
                elif event == "audio_error":
                    self.export_button.set_enabled(True)
                    self.export_audio_button.set_enabled(True)
                    self.export_status_var.set("音频分离失败")
                    messagebox.showerror(APP_TITLE, f"音频分离失败：\n{payload}")
                elif event == "preview_frame":
                    generation, data = payload
                    if generation == self.preview_generation:
                        self._set_preview_image(data)
        except queue.Empty:
            pass
        self._poll_job = self.root.after(100, self._poll_events)

    def _update_timers(self) -> None:
        seconds = int(self.recorder.recorded_seconds) if self.recorder else 0
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.duration_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

        if self.state == "recording":
            self.pulse_on = not self.pulse_on
            self._draw_status_dot(Theme.record if self.pulse_on else "#ffb1ac")

        if self.media_path is not None:
            position = self.current_playback_position()
            ended = False
            if self.player_state == "playing":
                if self.media_kind == "video":
                    ended = position >= self.audio_duration - 0.05
                elif self.mixer_ready and not pygame.mixer.music.get_busy():
                    ended = True
            if ended:
                position = self.audio_duration
                self.player_base = position
                self.player_state = "stopped"
                self.play_button.configure_text("重新播放")
            if not self.slider_dragging:
                self.progress_scale.set(position)
            elif self.media_kind == "video":
                self._request_preview_frame(float(self.progress_scale.get()))
            shown_position = float(self.progress_scale.get()) if self.slider_dragging else position
            self.player_time_var.set(f"{format_time(shown_position)} / {format_time(self.audio_duration)}")
            if self.player_state == "playing" and self.media_kind == "video":
                self._request_preview_frame(position)
        self._timer_job = self.root.after(200, self._update_timers)

    def on_close(self) -> None:
        if self.background_processing():
            self.close_after_save = True
            for page in (self.soften_page, self.audio_convert_page, self.compress_page):
                page.cancel()
            return
        if self.recorder and self.state != "idle":
            if self.state in {"processing", "stopping"}:
                self.close_after_save = True
                messagebox.showinfo(APP_TITLE, "正在保存 / 生成效果，完成后会自动退出。素材不会被删除。")
                return
            if not messagebox.askyesno(APP_TITLE, "录制尚未结束。是否停止、保存完成后退出？"):
                return
            self.close_after_save = True
            self.stop_recording()
            if self.state != "idle":
                return
        self._restore_from_capture()
        self.soften_page.stop_preview()
        self.stop_playback()
        if self.mixer_ready:
            pygame.mixer.quit()
        for job in (self._poll_job, self._timer_job):
            if job is not None:
                self.root.after_cancel(job)
        self.file_drop.close()
        self.root.destroy()


def main() -> None:
    _enable_dpi_awareness()
    root = tk.Tk()
    RecorderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
