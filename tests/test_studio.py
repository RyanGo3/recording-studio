from __future__ import annotations

import io
import json
import queue
import subprocess
import sys
import threading
import time
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audio_sources import AudioTracks, resolve_sources, stereo, wav_header
from cinematic import CursorCapture, Effects, FrameRenderer, MotionPlan, read_frame, render_video
from recording_v2 import ScreenRecorder, available_path, output_filename

ARTIFACTS = ROOT / "test-artifacts" / uuid.uuid4().hex[:8]
ARTIFACTS.mkdir(parents=True, exist_ok=False)
FFMPEG = ROOT / "tools" / "ffmpeg.exe"


class Device:
    def __init__(self, name="fake microphone", id="mic", loopback=False, value=0.1):
        self.name, self.id, self.isloopback, self.value = name, id, loopback, value

    def recorder(self, **kwargs):
        self.requested = kwargs
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def record(self, numframes):
        time.sleep(numframes / 48_000)
        return np.full((numframes, 1), self.value, dtype=np.float32)


def samples(duration=5, width=640, height=360):
    return [{"t": i / 120, "x": width * (0.15 + 0.7 * min(1, i / (120 * duration))),
             "y": height * 0.45, "visible": True, "click": i == 60, "kind": "arrow"}
            for i in range(int(duration * 120))]


class NamingTests(unittest.TestCase):
    def test_named_and_auto(self):
        self.assertEqual(output_filename("产品演示", "fullscreen", "20260930_120000"), "产品演示.mp4")
        self.assertEqual(output_filename("讲解.MP4", "region", "stamp"), "讲解.mp4")
        self.assertEqual(output_filename("声音.mp4", "audio", "stamp"), "声音.mp3")
        self.assertEqual(output_filename(" ", "audio", "stamp"), "系统录音_stamp.mp3")

    def test_invalid_names(self):
        for name in ["../outside", r"C:\outside", "a/b", "a?b", "a:b", "CON", "con .txt", "nul.txt", "LPT1.mp4", "a.", "..", ".mp4", "a\nb"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                output_filename(name, "region", "stamp")

    def test_existing_file_is_preserved(self):
        folder = ARTIFACTS / "names"
        folder.mkdir()
        (folder / "演示.mp4").write_bytes(b"original")
        (folder / "演示_02.mp4").write_bytes(b"second")
        self.assertEqual(available_path(folder, "演示.mp4").name, "演示_03.mp4")
        self.assertEqual((folder / "演示.mp4").read_bytes(), b"original")


class AudioTests(unittest.TestCase):
    def test_long_session_rf64_header(self):
        import struct
        header = wav_header(48_000 * 4 * 12 * 3600)
        self.assertEqual(len(header), 80)
        self.assertEqual(header[:4], b"RF64")
        self.assertEqual(header[12:16], b"ds64")
        self.assertEqual(struct.unpack("<Q", header[28:36])[0], 48_000 * 4 * 12 * 3600)
        small = io.BytesIO(wav_header(400) + bytes(400))
        with wave.open(small) as audio:
            self.assertEqual(audio.getnframes(), 100)

    def test_routes(self):
        speaker = Device("default", "speaker")
        loopback = Device("default", "speaker", True)
        wrong = Device("other", "other", True)
        mic = Device()
        with patch("audio_sources.sc.default_speaker", return_value=speaker), \
             patch("audio_sources.sc.all_microphones", return_value=[wrong, loopback]), \
             patch("audio_sources.sc.default_microphone", return_value=mic):
            self.assertEqual(resolve_sources("system"), [("system", loopback)])
            self.assertEqual(resolve_sources("mixed"), [("system", loopback), ("microphone", mic)])
            self.assertEqual(resolve_sources("microphone"), [("microphone", mic)])

    def test_missing_microphone_no_silent_fallback(self):
        with patch("audio_sources.sc.default_microphone", return_value=None), self.assertRaises(RuntimeError):
            resolve_sources("microphone")

    def test_missing_system_endpoint_no_wrong_speaker(self):
        with patch("audio_sources.sc.default_speaker", return_value=Device("expected", "wanted")), \
             patch("audio_sources.sc.all_microphones", return_value=[Device("wrong", "wrong", True)]), \
             self.assertRaises(RuntimeError):
            resolve_sources("system")

    def test_stereo(self):
        mono = np.array([0.1, -0.2], dtype=np.float32)
        np.testing.assert_array_equal(stereo(mono), np.column_stack([mono, mono]))
        self.assertEqual(stereo(np.zeros((4, 6))).shape, (4, 2))

    def test_tracks_pause_and_dual_readers(self):
        folder = ARTIFACTS / "audio"
        folder.mkdir()
        tracks = AudioTracks([("system", Device(value=0.2)), ("microphone", Device(value=-0.3))], folder)
        tracks.start()
        tracks.set_active(True)
        time.sleep(0.22)
        tracks.set_active(False)
        paused_time = tracks.clock()[1]
        time.sleep(0.12)
        self.assertAlmostEqual(tracks.clock()[1], paused_time, places=3)
        tracks.set_active(True)
        time.sleep(0.22)
        tracks.stop()
        for _, device in tracks.sources:
            self.assertEqual(device.requested["channels"], 2)
        for path in tracks.paths:
            with wave.open(str(path)) as data:
                self.assertEqual(data.getnchannels(), 2)
                duration = data.getnframes() / data.getframerate()
                self.assertGreater(duration, 0.34)
                self.assertLess(duration, 0.55)

    def test_driver_failure_surfaces(self):
        class Broken(Device):
            def __enter__(self):
                raise RuntimeError("access denied")
        folder = ARTIFACTS / "broken_audio"
        folder.mkdir()
        tracks = AudioTracks([("microphone", Broken())], folder)
        with self.assertRaisesRegex(RuntimeError, "access denied"):
            tracks.start()
        tracks.stop_event.set()


class MotionTests(unittest.TestCase):
    def test_click_zoom_then_return(self):
        plan = MotionPlan(samples(5), 640, 360, 30, 5, Effects())
        self.assertAlmostEqual(plan.zoom[0], 1)
        self.assertGreater(max(plan.zoom), 1.6)
        self.assertAlmostEqual(plan.zoom[-1], 1)

    def test_no_click_no_zoom(self):
        track = samples()
        for sample in track:
            sample["click"] = False
        plan = MotionPlan(track, 640, 360, 30, 5, Effects())
        np.testing.assert_array_equal(plan.zoom, np.ones(150))

    def test_edges_and_outside(self):
        track = samples()
        for sample in track:
            sample.update(x=0, y=0)
        track[-1].update(x=-5000, y=-5000, visible=False)
        plan = MotionPlan(track, 640, 360, 30, 5, Effects())
        self.assertTrue(np.all(plan.cx - 640 / (2 * plan.zoom) >= -0.0001))
        self.assertTrue(np.all(plan.cy - 360 / (2 * plan.zoom) >= -0.0001))
        self.assertTrue(np.all(plan.cx + 640 / (2 * plan.zoom) <= 640.0001))

    def test_smoothing_reduces_jitter(self):
        track = samples(1)
        for i, sample in enumerate(track):
            sample["x"] = 300 + 15 * np.sin(i * 2 * np.pi * 12 / 120)
        plain = MotionPlan(track, 640, 360, 30, 1, Effects(smooth_cursor=False))
        smooth = MotionPlan(track, 640, 360, 30, 1, Effects(smooth_cursor=True))
        self.assertLess(np.std(smooth.x[3:-3]), np.std(plain.x[3:-3]) + 0.001)

    def test_seek_safe_output_and_separate_options(self):
        track = samples(3)
        frame = Image.new("RGB", (640, 360), "#609060")
        for effects in [Effects(), Effects(auto_zoom=False, cinematic=False), Effects(smooth_cursor=False)]:
            plan = MotionPlan(track, 640, 360, 30, 3, effects)
            renderer = FrameRenderer(640, 360, effects)
            first = renderer.render(frame, plan, 35).tobytes()
            renderer.render(frame, plan, 5)
            self.assertEqual(renderer.render(frame, plan, 35).tobytes(), first)
            self.assertEqual(renderer.render(frame, plan, 0).size, (640, 360))

    def test_partial_pipe_reads(self):
        class Partial(io.BytesIO):
            def read(self, count):
                return super().read(min(3, count))
        self.assertEqual(read_frame(Partial(b"0123456789"), 10), b"0123456789")
        with self.assertRaises(RuntimeError):
            read_frame(Partial(b"short"), 10)


class FFmpegTests(unittest.TestCase):
    def test_real_render_and_mux_all_three_modes(self):
        session = ARTIFACTS / "render"
        session.mkdir()
        source, rendered = session / "source.mp4", session / "rendered.mp4"
        kwargs = ScreenRecorder.popen_kwargs()
        result = subprocess.run(
            [str(FFMPEG), "-v", "error", "-n", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], capture_output=True, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        percents = []
        track = samples(2)
        render_video(FFMPEG, source, rendered, session, 640, 360, 30, 2, track, Effects(), percents.append, kwargs)
        self.assertEqual(percents[-1], 100)
        recorder = ScreenRecorder(session / "unused.mp4", queue.Queue(), (0, 0, 640, 360), FFMPEG)
        self.assertAlmostEqual(recorder.duration(source), recorder.duration(rendered), delta=0.04)
        paths = []
        for name, hz in [("system", 440), ("microphone", 880)]:
            path = session / f"{name}.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(2)
                audio.setsampwidth(2)
                audio.setframerate(48000)
                t = np.arange(48000) / 48000
                signal = (np.sin(t * 2 * np.pi * hz) * 20000).astype("<i2")
                audio.writeframes(np.column_stack([signal, signal]).tobytes())
            paths.append(path)
        for mode, inputs in [("system", paths[:1]), ("microphone", paths[1:]), ("mixed", paths)]:
            recorder._audio = type("Tracks", (), {"paths": inputs})()
            dest = session / f"{mode}.mp4"
            recorder._mux(rendered, dest, 2)
            self.assertAlmostEqual(recorder.duration(dest), 2, delta=0.05)
            info = subprocess.run([str(FFMPEG), "-hide_banner", "-i", str(dest)], capture_output=True, **kwargs)
            self.assertIn(b"Audio: aac", info.stderr)
            decoded = subprocess.run([str(FFMPEG), "-v", "error", "-i", str(dest), "-vn", "-t", "0.75",
                                      "-f", "f32le", "-ac", "1", "-ar", "48000", "pipe:1"],
                                     capture_output=True, **kwargs)
            self.assertEqual(decoded.returncode, 0)
            audio = np.frombuffer(decoded.stdout, dtype="<f4")
            self.assertGreater(np.max(np.abs(audio)), 0.1)
            frequencies = np.fft.rfftfreq(len(audio), 1 / 48000)
            spectrum = np.abs(np.fft.rfft(audio))
            def energy(hz):
                return np.max(spectrum[np.abs(frequencies - hz) < 5])
            if mode == "system":
                self.assertGreater(energy(440), energy(880) * 10)
            elif mode == "microphone":
                self.assertGreater(energy(880), energy(440) * 10)
            else:
                self.assertGreater(energy(440), 100)
                self.assertGreater(energy(880), 100)
            self.assertLess(np.max(np.abs(audio)), 1.02)
        recorder.run(["-n", "-ss", "1.0", "-i", str(rendered), "-frames:v", "1", str(session / "preview.png")])


class RecorderTests(unittest.TestCase):
    def test_pause_time_is_not_double_counted(self):
        recorder = ScreenRecorder(ARTIFACTS / "clock.mp4", queue.Queue(), (0, 0, 640, 360), FFMPEG)
        recorder.parts = [{"samples": [], "wall_duration": 0}]
        recorder.clock_started = time.monotonic() - 2
        recorder.recorded_seconds = 2
        recorder._finish_part()
        self.assertAlmostEqual(recorder.recorded_seconds, 2, delta=0.02)
        recorder._finish_part()
        self.assertAlmostEqual(recorder.recorded_seconds, 2, delta=0.02)

    def test_existing_output_not_overwritten(self):
        output = ARTIFACTS / "exists.mp4"
        output.write_bytes(b"must survive")
        events = queue.Queue()
        recorder = ScreenRecorder(output, events, (0, 0, 640, 360), FFMPEG)
        recorder._record()
        self.assertEqual(events.get()[0], "error")
        self.assertEqual(output.read_bytes(), b"must survive")

@unittest.skipUnless(__import__("os").environ.get("STUDIO_DESKTOP_TESTS") == "1",
                     "设置 STUDIO_DESKTOP_TESTS=1 后运行真实桌面集成测试")
class WindowsIntegrationTests(unittest.TestCase):
    def test_cursor_capture_native_api(self):
        capture = CursorCapture((0, 0, 1920, 1080))
        capture.start()
        time.sleep(0.12)
        track = capture.stop()
        self.assertGreater(len(track), 0)
        self.assertIn("kind", track[0])
        self.assertEqual(sorted(s["t"] for s in track), [s["t"] for s in track])

    def test_real_screen_capture_pause_and_effect_export(self):
        import tkinter as tk
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        root = tk.Tk()
        root.title("录制工坊 V2 验证画面（自动关闭）")
        root.geometry("640x360+80+80")
        root.attributes("-topmost", True)
        canvas = tk.Canvas(root, bg="#0f172a", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_text(320, 60, text="RECORDING STUDIO V2", fill="white", font=("Consolas", 24, "bold"))
        canvas.create_rectangle(90, 110, 550, 280, fill="#2563eb", outline="")
        canvas.create_text(320, 180, text="Zoom / Smooth / Cinematic", fill="white", font=("Consolas", 18))
        root.update()
        folder = ARTIFACTS / "capture"
        folder.mkdir()
        events = queue.Queue()
        region = (root.winfo_rootx(), root.winfo_rooty(), 640, 360)
        recorder = ScreenRecorder(folder / "提前命名_演示.mp4", events, region, FFMPEG, audio_mode="mixed")
        class SyntheticCursor:
            def __init__(self, region):
                pass
            def start(self):
                self.started = time.monotonic()
            def stop(self):
                duration = time.monotonic() - self.started
                return samples(duration)
        def wait_for(event_name, deadline=20):
            until = time.monotonic() + deadline
            while time.monotonic() < until:
                root.update()
                try:
                    event, payload = events.get_nowait()
                except queue.Empty:
                    time.sleep(0.02)
                    continue
                if event == "error":
                    self.fail(str(payload))
                if event == event_name:
                    return payload
            self.fail(f"Timeout waiting for {event_name}")
        try:
            with patch("recording_v2.resolve_sources", return_value=[("system", Device()), ("microphone", Device())]), \
                 patch("recording_v2.CursorCapture", SyntheticCursor):
                recorder.start()
                wait_for("started")
                time.sleep(0.45)
                recorder.pause()
                time.sleep(0.35)
                paused_at = recorder.recorded_seconds
                time.sleep(0.2)
                self.assertAlmostEqual(recorder.recorded_seconds, paused_at, delta=0.05)
                recorder.resume()
                time.sleep(0.75)
                recorder.stop()
                details = wait_for("stopped", deadline=45)
                recorder.join(2)
            self.assertTrue(recorder.output_path.is_file())
            self.assertEqual(len(recorder.parts), 2)
            self.assertGreater(recorder.duration(recorder.output_path), 1.1)
            self.assertLess(recorder.duration(recorder.output_path), 2.5)
            self.assertIsNone(details["session"])
            self.assertIsNone(recorder.session)
            self.assertFalse(any(folder.glob(".提前命名_演示_session_*")))
            self.assertTrue(any(s["click"] for part in recorder.parts for s in part["samples"]))
            recorder.run(["-n", "-ss", "0.8", "-i", str(recorder.output_path), "-frames:v", "1",
                          str(folder / "capture-preview.png")])
        finally:
            recorder.stop()
            recorder.join(10)
            root.destroy()


if __name__ == "__main__":
    unittest.main()
