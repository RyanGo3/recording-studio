from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from audio_sources import AudioTracks, resolve_sources
from audio_processing import cleanup_filter
from cinematic import CursorCapture, Effects, render_video, save_metadata

MAX_SECONDS = 12 * 3600


def output_filename(name: str, mode: str, stamp: str) -> str:
    extension = ".mp3" if mode == "audio" else ".mp4"
    name = name.strip()
    if not name:
        return f"{'系统录音' if mode == 'audio' else '系统录屏'}_{stamp}{extension}"
    if name.lower().endswith((".mp3", ".mp4")):
        name = name[:-4]
    if (not name or name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.endswith((".", " "))):
        raise ValueError("文件名不能包含 Windows 非法字符，也不能以空格或句点结尾。")
    if re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", name.split(".")[0].rstrip(" ."), re.I):
        raise ValueError("这个名称是 Windows 保留名称，请换一个。")
    if len(name) > 180:
        raise ValueError("文件名过长，请控制在 180 个字符以内。")
    return name + extension


def available_path(folder: Path, filename: str) -> Path:
    path = folder / filename
    index = 2
    while path.exists():
        path = folder / f"{Path(filename).stem}_{index:02d}{Path(filename).suffix}"
        index += 1
    return path


class ScreenRecorder:
    def __init__(self, output_path: Path, events: queue.Queue, region: tuple[int, int, int, int],
                 ffmpeg_path: Path, audio_mode: str = "system", microphone_id: str | None = None,
                 effects: Effects | None = None, fps: int = 30,
                 noise_reduction: bool = True) -> None:
        self.output_path, self.events, self.region = output_path, events, region
        self.ffmpeg_path, self.audio_mode, self.microphone_id = ffmpeg_path, audio_mode, microphone_id
        self.effects = effects or Effects()
        self.fps = fps
        self.noise_reduction = noise_reduction
        self.stop_event, self.pause_event = threading.Event(), threading.Event()
        self.thread: threading.Thread | None = None
        self.recorded_seconds = 0.0
        self.finished_seconds = 0.0
        self._ffmpeg = None
        self._audio = None
        self._cursor = None
        self.session: Path | None = None
        self.clock_started: float | None = None
        self.part_started = 0.0
        self.parts: list[dict] = []

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

    @staticmethod
    def popen_kwargs() -> dict:
        if os.name != "nt":
            return {}
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": info, "creationflags": 0x08000000}

    def run(self, args: list[str]) -> None:
        process = subprocess.run([str(self.ffmpeg_path), "-v", "error", *args],
                                 capture_output=True, **self.popen_kwargs())
        if process.returncode:
            raise RuntimeError(process.stderr.decode("utf-8", errors="replace")[-2500:])

    def duration(self, path: Path) -> float:
        process = subprocess.run([str(self.ffmpeg_path), "-hide_banner", "-i", str(path)],
                                 capture_output=True, **self.popen_kwargs())
        match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", process.stderr.decode(errors="replace"))
        if not match:
            raise RuntimeError(f"无法读取录像片段：{path}")
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)

    def _start_part(self) -> None:
        x, y, width, height = self.region
        if sys.platform == "darwin":
            from macos_capture import capture_arguments
            capture = capture_arguments(self.region, self.fps, not self.effects.enabled)
        else:
            capture = [
                "-f", "gdigrab", "-framerate", str(self.fps),
                "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{width}x{height}",
                "-draw_mouse", "0" if self.effects.enabled else "1", "-i", "desktop",
            ]
        path = self.session / f"video_{len(self.parts):04d}.mp4"
        log = (self.session / "capture.log").open("ab")
        try:
            self._ffmpeg = subprocess.Popen(
                [str(self.ffmpeg_path), "-n", "-v", "error", *capture,
                 "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 "-pix_fmt", "yuv420p", str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log, **self.popen_kwargs())
        except Exception:
            log.close()
            raise
        self._ffmpeg._log_file = log
        self.part_started = time.monotonic()
        self.clock_started = self.part_started
        audio_start = self._audio.clock()[1]
        self._audio.set_active(True)
        if self.effects.enabled:
            self._cursor = CursorCapture(self.region)
            self._cursor.start()
        self.parts.append({"path": path, "samples": [], "wall_duration": 0.0, "audio_start": audio_start})
        time.sleep(0.3)
        self._check_capture()

    def _check_capture(self) -> None:
        self._audio.check()
        if self._ffmpeg and self._ffmpeg.poll() is not None:
            log = (self.session / "capture.log").read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(log[-2000:] or "屏幕采集意外结束。")

    def _finish_part(self) -> None:
        if self.clock_started is not None:
            elapsed = time.monotonic() - self.clock_started
            self.finished_seconds += elapsed
            self.recorded_seconds = self.finished_seconds
            self.clock_started = None
            self.parts[-1]["wall_duration"] = elapsed
        if self._audio:
            if self._ffmpeg and self.parts:
                self.parts[-1]["audio_end"] = self._audio.clock()[1]
            self._audio.set_active(False)
        if self._cursor:
            self.parts[-1]["samples"] = self._cursor.stop()
            self._cursor = None
        process, self._ffmpeg = self._ffmpeg, None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.stdin.write(b"q")
                process.stdin.flush()
                process.wait(timeout=15)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        finally:
            if process.stdin:
                process.stdin.close()
            process._log_file.close()

    def _align_audio(self) -> None:
        """Trim per-part encoder startup, so pause/restart skew cannot accumulate."""
        aligned = []
        count = len(self.parts)
        for path in self._audio.paths:
            graphs = []
            aligned_label = "aligned" if self.noise_reduction else "out"
            if count > 1:
                graphs.append("[0:a]asplit=" + str(count) + "".join(f"[s{i}]" for i in range(count)))
            for index, part in enumerate(self.parts):
                duration = part["duration"]
                start = part["audio_start"] + max(0, part["wall_duration"] - duration)
                end = min(part["audio_end"], start + duration)
                input_label = f"s{index}" if count > 1 else "0:a"
                graphs.append(f"[{input_label}]atrim=start={start:.6f}:end={end:.6f},"
                              f"asetpts=PTS-STARTPTS,apad,atrim=duration={duration:.6f}[p{index}]")
            if count > 1:
                graphs.append("".join(f"[p{i}]" for i in range(count)) +
                              f"concat=n={count}:v=0:a=1[{aligned_label}]")
            else:
                graphs.append(f"[p0]anull[{aligned_label}]")
            if self.noise_reduction:
                kind = "microphone" if path.stem == "microphone" else "system"
                graphs.append(f"[aligned]{cleanup_filter(kind)}[out]")
            suffix = "aligned_clean" if self.noise_reduction else "aligned"
            dest = self.session / f"{path.stem}_{suffix}.wav"
            self.run(["-n", "-i", str(path), "-filter_complex", ";".join(graphs),
                      "-map", "[out]", "-c:a", "pcm_s16le", "-rf64", "auto", str(dest)])
            aligned.append(dest)
        self._audio.paths = aligned

    def _mux(self, video: Path, dest: Path, duration: float,
             audio_source: Path | None = None) -> None:
        if dest.exists():
            raise FileExistsError(f"输出文件已存在，不会覆盖：{dest}")
        if audio_source is not None:
            # original.mp4 already contains aligned/mixed AAC: do not encode it twice.
            self.run(["-n", "-i", str(video), "-i", str(audio_source),
                      "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
                      "-t", f"{duration:.6f}", "-movflags", "+faststart", str(dest)])
            return
        inputs = ["-n", "-i", str(video)]
        for path in self._audio.paths:
            inputs += ["-i", str(path)]
        if len(self._audio.paths) == 2:
            # Equal gains + limiter provide headroom when both sources are loud.
            graph = "[1:a][2:a]amix=inputs=2:duration=longest:normalize=1,alimiter=limit=0.95:level=0:latency=1,apad[a]"
        else:
            graph = "[1:a]apad[a]"
        self.run(inputs + ["-filter_complex", graph, "-map", "0:v:0", "-map", "[a]",
                          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.6f}",
                          "-movflags", "+faststart", str(dest)])

    def _discard_session(self) -> None:
        """Remove working files after the final output is safely written."""
        session, self.session = self.session, None
        if session is None:
            return
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(session), 0x80)
        shutil.rmtree(session)

    def _record(self) -> None:
        try:
            if self.output_path.exists():
                raise RuntimeError("输出文件已存在，请更换名称后重试。")
            sources = resolve_sources(self.audio_mode, self.microphone_id)
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            # Never remove or reuse a session directory. Retain recovery assets.
            self.session = self.output_path.parent / f".{self.output_path.stem}_session_{uuid.uuid4().hex[:8]}"
            self.session.mkdir(exist_ok=False)
            if os.name == "nt":
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(self.session), 0x02)
            (self.session / "session.json").write_text(json.dumps({
                "output": str(self.output_path), "region": self.region, "fps": self.fps,
                "audio_mode": self.audio_mode, "sources": [device.name for _, device in sources],
                "noise_reduction": self.noise_reduction,
            }, ensure_ascii=False), encoding="utf-8")
            self._audio = AudioTracks(sources, self.session)
            self._audio.start()
            if self.stop_event.is_set():
                raise RuntimeError("录制在启动过程中已取消，素材目录已保留。")
            self._start_part()
            self.events.put(("started", " + ".join(device.name for _, device in sources)))
            while not self.stop_event.wait(0.04):
                current = self.finished_seconds + (time.monotonic() - self.clock_started if self.clock_started else 0)
                self.recorded_seconds = current
                if current >= MAX_SECONDS:
                    break
                if self.pause_event.is_set():
                    if self._ffmpeg:
                        self._finish_part()
                elif not self._ffmpeg:
                    self._start_part()
                self._check_capture()
            self._finish_part()
            self._audio.stop()
            self._audio.check()
            self.events.put(("processing", "正在合并原始素材…"))
            samples = []
            offset = 0.0
            for part in self.parts:
                duration = self.duration(part["path"])
                part["duration"] = duration
                for sample in part["samples"]:
                    item = dict(sample)
                    startup = max(0.0, part["wall_duration"] - duration)
                    item["t"] = offset + min(duration, max(0.0, sample["t"] - startup))
                    samples.append(item)
                offset += duration
            self.recorded_seconds = offset
            capture = self.session / "capture.mp4"
            if len(self.parts) == 1:
                # Keep the original part in place; another full video copy adds no value.
                capture = self.parts[0]["path"]
            else:
                concat = self.session / "concat.txt"
                lines = []
                for part in self.parts:
                    escaped = str(part["path"]).replace("\\", "/").replace("'", "'\\''")
                    lines.append(f"file '{escaped}'")
                concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self.run(["-n", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(capture)])
            save_metadata(self.session / "cursor.json", samples, self.effects)
            self.events.put(("processing", "正在对齐音轨并轻度降噪…" if self.noise_reduction
                             else "正在对齐音轨…"))
            self._align_audio()
            raw = self.session / "original.mp4"
            self._mux(capture, raw, offset)
            if self.effects.enabled:
                try:
                    self.events.put(("processing", "正在生成自动缩放 / 平滑光标 / 电影感画面…"))
                    rendered = self.session / "cinematic.mp4"
                    render_video(self.ffmpeg_path, capture, rendered, self.session,
                                 self.region[2], self.region[3], self.fps, offset, samples, self.effects,
                                 lambda percent: self.events.put(("render_progress", percent)), self.popen_kwargs())
                    # Rendered video already has exactly the same frame count / timebase.
                    self.events.put(("processing", "画面处理完成，正在快速封装音视频…"))
                    self._mux(rendered, self.output_path, self.duration(rendered), audio_source=raw)
                except Exception as error:
                    raise RuntimeError(f"动态效果处理失败，原始素材已保留：\n{self.session}\n{error}"
                                       "\n恢复录像：original.mp4（不含光标）；轨迹：cursor.json")
            else:
                # Exclusive creation prevents an external name collision from overwriting.
                with self.output_path.open("xb") as out, raw.open("rb") as source:
                    shutil.copyfileobj(source, out)
            self._discard_session()
            self.events.put(("stopped", {"path": self.output_path, "reached_limit": offset >= MAX_SECONDS,
                                        "session": None}))
        except Exception as error:
            recovery = f"\n素材保留在：{self.session}" if self.session else ""
            self.events.put(("error", str(error) + recovery))
        finally:
            self._finish_part()
            if self._audio and any(t.is_alive() for t in self._audio.threads):
                try:
                    self._audio.stop()
                except Exception:
                    pass
