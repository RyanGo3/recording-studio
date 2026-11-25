"""Seek-safe click zoom, cursor smoothing and a framed cinematic export."""
from __future__ import annotations

import bisect
import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class Effects:
    auto_zoom: bool = True
    smooth_cursor: bool = True
    cinematic: bool = True
    zoom: float = 1.65

    def __post_init__(self) -> None:
        if not 1 <= self.zoom <= 3:
            raise ValueError("缩放倍率必须介于 1 和 3 之间。")

    @property
    def enabled(self) -> bool:
        return self.auto_zoom or self.smooth_cursor or self.cinematic


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("hCursor", ctypes.c_void_p), ("ptScreenPos", POINT)]


class CursorCapture:
    def __init__(self, region: tuple[int, int, int, int]) -> None:
        self.region = region
        self.samples: list[dict] = []
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if sys.platform == "darwin":
            from macos_capture import cursor_state
            cursor_state()  # Surface native framework failures before starting a worker.
        self.started_at = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> list[dict]:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        return self.samples

    def _run(self) -> None:
        if sys.platform == "darwin":
            self._run_macos()
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.LoadCursorW.restype = ctypes.c_void_p
        user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
        text_cursor = user32.LoadCursorW(None, ctypes.c_void_p(32513))
        x, y, width, height = self.region
        last_buttons = [False, False]
        while not self.stop_event.is_set():
            info = CURSORINFO()
            info.cbSize = ctypes.sizeof(info)
            if user32.GetCursorInfo(ctypes.byref(info)):
                px, py = info.ptScreenPos.x - x, info.ptScreenPos.y - y
                inside = 0 <= px < width and 0 <= py < height
                # Read held state AND the low-bit transition to catch short clicks.
                states = [user32.GetAsyncKeyState(key) & 0xFFFF for key in (1, 2)]
                buttons = [bool(state & 0x8000) for state in states]
                clicked = any((down and not old) or (state & 1)
                              for state, down, old in zip(states, buttons, last_buttons))
                self.samples.append({
                    "t": time.monotonic() - self.started_at,
                    "x": px, "y": py,
                    "visible": bool(info.flags & 1) and inside,
                    "click": clicked and inside,
                    "kind": "text" if info.hCursor == text_cursor else "arrow",
                })
                last_buttons = buttons
            self.stop_event.wait(1 / 120)

    def _run_macos(self) -> None:
        from macos_capture import cursor_state
        x, y, width, height = self.region
        last_buttons = [False, False]
        while not self.stop_event.is_set():
            px, py, buttons = cursor_state()
            px, py = px - x, py - y
            inside = 0 <= px < width and 0 <= py < height
            self.samples.append({
                "t": time.monotonic() - self.started_at,
                "x": px, "y": py, "visible": inside, "kind": "arrow",
                "click": inside and any(down and not old for down, old in zip(buttons, last_buttons)),
            })
            last_buttons = buttons
            self.stop_event.wait(1 / 120)


def smoothstep(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3 - 2 * value)


class MotionPlan:
    """Precompute all transforms: frame results do not depend on seeking order."""
    def __init__(self, samples: list[dict], width: int, height: int,
                 fps: int, duration: float, effects: Effects) -> None:
        if not samples:
            raise RuntimeError("没有捕获到光标轨迹，无法生成动态效果。")
        self.effects = effects
        self.width, self.height, self.fps = width, height, fps
        self.samples = sorted(samples, key=lambda s: s["t"])
        sample_times = np.array([s["t"] for s in self.samples])
        self.times = sample_times.tolist()
        times = np.arange(max(1, math.ceil(duration * fps))) / fps
        positions = []
        last_visible = (width / 2, height / 2)
        for sample in self.samples:
            if sample.get("visible", True):
                last_visible = (sample["x"], sample["y"])
            positions.append(last_visible)
        self.x = np.interp(times, sample_times, [p[0] for p in positions])
        self.y = np.interp(times, sample_times, [p[1] for p in positions])
        if effects.smooth_cursor:
            # Symmetric Gaussian smoothing has no systematic cursor lag.
            radius = max(1, round(fps * 0.075))
            offsets = np.arange(-radius, radius + 1)
            kernel = np.exp(-0.5 * (offsets / max(0.8, fps * 0.035)) ** 2)
            kernel /= kernel.sum()
            self.x = np.convolve(np.pad(self.x, radius, mode="edge"), kernel, mode="valid")
            self.y = np.convolve(np.pad(self.y, radius, mode="edge"), kernel, mode="valid")
        self.clicks = [s["t"] for s in self.samples if s.get("click")]
        # Overlapping clicks extend the same shot, rather than zooming out and in.
        intervals: list[list[float]] = []
        for clicked_at in self.clicks:
            start, end = max(0.0, clicked_at - 0.25), clicked_at + 2.4
            if intervals and start <= intervals[-1][1]:
                intervals[-1][1] = end
            else:
                intervals.append([start, end])
        self.zoom = np.ones(len(times))
        if effects.auto_zoom:
            for start, end in intervals:
                mask = (times >= start) & (times <= end + 0.85)
                selected = times[mask]
                strength = [min(smoothstep((t - start) / 0.65),
                                1 - smoothstep((t - end) / 0.85)) for t in selected]
                self.zoom[mask] = 1 + (effects.zoom - 1) * np.array(strength)
        # Critically damped pan: smooth acceleration, stable follow and edge clamping.
        self.cx = np.full(len(times), width / 2.0)
        self.cy = np.full(len(times), height / 2.0)
        position = np.array([width / 2, height / 2], dtype=float)
        velocity = np.zeros(2)
        for index, zoom in enumerate(self.zoom):
            target = np.array([self.x[index], self.y[index]]) if zoom > 1.001 else np.array([width / 2, height / 2])
            # Exact critically damped spring update, stable at every frame rate.
            delta = position - target
            omega, dt = 10.0, 1 / fps
            term = velocity + omega * delta
            decay = math.exp(-omega * dt)
            position = target + (delta + term * dt) * decay
            velocity = (velocity - omega * term * dt) * decay
            half = np.array([width, height]) / (2 * zoom)
            clamped = np.clip(position, half, np.array([width, height]) - half)
            self.cx[index], self.cy[index] = clamped

    def cursor(self, index: int) -> dict:
        idx = min(index, len(self.x) - 1)
        t = idx / self.fps
        sample_index = max(0, bisect.bisect_right(self.times, t) - 1)
        sample = self.samples[sample_index]
        last = bisect.bisect_right(self.clicks, t) - 1
        age = t - self.clicks[last] if last >= 0 else 999
        return {"x": self.x[idx], "y": self.y[idx], "visible": sample.get("visible", True),
                "kind": sample.get("kind", "arrow"), "click_age": age}


class FrameRenderer:
    def __init__(self, width: int, height: int, effects: Effects) -> None:
        self.width, self.height, self.effects = width, height, effects
        margin = max(2, round(min(width, height) * 0.055)) if effects.cinematic else 0
        self.box = (margin, margin, width - margin, height - margin)
        self.stage_size = (width - margin * 2, height - margin * 2)
        self.background = Image.new("RGB", (width, height), "#111827")
        if effects.cinematic:
            draw = ImageDraw.Draw(self.background)
            for row in range(height):
                value = row / max(1, height - 1)
                draw.line((0, row, width, row), fill=(int(24 - 12 * value), int(35 - 17 * value), int(59 - 29 * value)))
            draw.rounded_rectangle((margin - 2, margin + 5, width - margin + 2, height - margin + 9),
                                   radius=max(3, margin // 3), fill="#080d17")
        self.mask = Image.new("L", self.stage_size, 255)
        if effects.cinematic:
            self.mask.paste(0, (0, 0, *self.stage_size))
            ImageDraw.Draw(self.mask).rounded_rectangle((0, 0, self.stage_size[0] - 1, self.stage_size[1] - 1),
                                                       radius=max(3, margin // 3), fill=255)

    def render(self, frame: Image.Image, plan: MotionPlan, index: int) -> Image.Image:
        index = min(index, len(plan.zoom) - 1)
        zoom = float(plan.zoom[index])
        left = plan.cx[index] - self.width / (2 * zoom)
        top = plan.cy[index] - self.height / (2 * zoom)
        sw, sh = self.stage_size
        crop_w, crop_h = self.width / zoom, self.height / zoom
        # This transform is only a crop + scale, not a general affine warp.
        # Pillow's separable resize is substantially faster at the same sampling quality.
        left = max(0.0, min(left, self.width - crop_w))
        top = max(0.0, min(top, self.height - crop_h))
        if zoom == 1.0 and self.stage_size == frame.size:
            stage = frame.copy()
        else:
            stage = frame.resize(self.stage_size, Image.Resampling.BICUBIC,
                                 box=(left, top, min(self.width, left + crop_w),
                                      min(self.height, top + crop_h)))
        cursor = plan.cursor(index)
        if cursor["visible"]:
            # Draw on the stage before its rounded mask: no cursor spills into the backdrop.
            px = (cursor["x"] - left) * sw / crop_w
            py = (cursor["y"] - top) * sh / crop_h
            draw = ImageDraw.Draw(stage)
            scale = max(0.65, min(self.width, self.height) / 900) * min(zoom, 1.5)
            age = cursor["click_age"]
            if 0 <= age < 0.45:
                radius = (10 + age * 32) * scale
                draw.ellipse((px - radius, py - radius, px + radius, py + radius),
                             outline="#60a5fa", width=max(1, round(2 * scale)))
            if cursor["kind"] == "text":
                size = 10 * scale
                draw.line((px, py - size, px, py + size), fill="white", width=max(2, round(2 * scale)))
                for cy in (py - size, py + size):
                    draw.line((px - 4 * scale, cy, px + 4 * scale, cy), fill="white", width=2)
            else:
                points = [(px + dx * scale, py + dy * scale)
                          for dx, dy in [(0, 0), (0, 23), (6, 18), (11, 28), (16, 25), (11, 16), (20, 16)]]
                draw.polygon(points, fill="white", outline="#111827", width=max(1, round(2 * scale)))
        if not self.effects.cinematic:
            return stage
        result = self.background.copy()
        result.paste(stage, self.box[:2], self.mask)
        return result


def read_frame(stream, size: int) -> bytes:
    chunks, remaining = [], size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if data and len(data) != size:
        raise RuntimeError("视频解码产生不完整的画面。")
    return data


def rendered_frames(stream, renderer: FrameRenderer, plan: MotionPlan):
    """Render in parallel, preserving order and bounding full-resolution frames in RAM."""
    width, height = renderer.width, renderer.height
    # Include input/output bytes and Pillow's RGB storage in the per-frame estimate.
    slots = max(1, (128 * 1024 * 1024) // (width * height * 16))
    workers = min(4, max(1, (os.cpu_count() or 2) // 2), slots)
    window = min(workers * 2, slots)

    def render(data: bytes, index: int) -> bytes:
        frame = Image.frombytes("RGB", (width, height), data)
        return renderer.render(frame, plan, index).tobytes()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video-render") as pool:
        pending = deque()
        index = 0
        while True:
            data = read_frame(stream, width * height * 3)
            if not data:
                break
            pending.append(pool.submit(render, data, index))
            index += 1
            if len(pending) >= window:
                yield pending.popleft().result()
        while pending:
            yield pending.popleft().result()


def render_video(ffmpeg: Path, source: Path, dest: Path, session: Path,
                 width: int, height: int, fps: int, duration: float,
                 samples: list[dict], effects: Effects, progress, popen_kwargs: dict) -> None:
    plan = MotionPlan(samples, width, height, fps, duration, effects)
    renderer = FrameRenderer(width, height, effects)
    # Decoder and encoder have separate logs: unread stderr pipes can deadlock.
    decoder = encoder = None
    try:
        with (session / "render_decode.log").open("wb") as decode_log, (session / "render_encode.log").open("wb") as encode_log:
            decoder = subprocess.Popen(
                [str(ffmpeg), "-v", "error", "-i", str(source), "-an", "-vf", f"fps={fps}",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                stdout=subprocess.PIPE, stderr=decode_log, **popen_kwargs)
            encoder = subprocess.Popen(
                [str(ffmpeg), "-v", "error", "-n", "-f", "rawvideo", "-pix_fmt", "rgb24",
                 "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                 str(dest)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encode_log, **popen_kwargs)
            index = 0
            frames = rendered_frames(decoder.stdout, renderer, plan)
            try:
                for data in frames:
                    encoder.stdin.write(data)
                    index += 1
                    if index % fps == 0:
                        progress(min(99, round(100 * index / max(1, duration * fps))))
            finally:
                frames.close()
            encoder.stdin.close()
            decoder.stdout.close()
            decoder_code = decoder.wait(timeout=60)
            encoder_code = encoder.wait(timeout=120)
            if not index or decoder_code or encoder_code:
                raise RuntimeError("电影感画面导出失败，请查看会话目录中的 render 日志。")
            progress(100)
    finally:
        for process in (decoder, encoder):
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                for stream in (process.stdin, process.stdout):
                    if stream and not stream.closed:
                        stream.close()


def save_metadata(path: Path, samples: list[dict], effects: Effects) -> None:
    path.write_text(json.dumps({"effects": asdict(effects), "samples": samples},
                               ensure_ascii=False), encoding="utf-8")
