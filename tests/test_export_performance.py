from __future__ import annotations

import io
import json
import queue
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cinematic import Effects, FrameRenderer, MotionPlan, rendered_frames
from recording_v2 import ScreenRecorder

ARTIFACTS = ROOT / "test-artifacts" / ("export_" + uuid.uuid4().hex[:8])
ARTIFACTS.mkdir(parents=True, exist_ok=False)
FFMPEG = ROOT / "tools" / "ffmpeg.exe"


class RenderPerformanceTests(unittest.TestCase):
    def test_unscaled_cursor_does_not_modify_source(self):
        effects = Effects(auto_zoom=False, smooth_cursor=False, cinematic=False)
        plan = MotionPlan([{"t": 0, "x": 20, "y": 20, "visible": True}],
                          96, 64, 30, 1, effects)
        frame = Image.new("RGB", (96, 64), "#2563eb")
        before = frame.tobytes()
        result = FrameRenderer(96, 64, effects).render(frame, plan, 0)
        self.assertEqual(frame.tobytes(), before)
        self.assertNotEqual(result.tobytes(), before)
        self.assertEqual(result.size, frame.size)

    def test_resize_keeps_constant_color_and_edges(self):
        for cinematic in (True, False):
            effects = Effects(cinematic=cinematic)
            for x, y in [(0, 0), (95, 63)]:
                plan = MotionPlan([{"t": 0, "x": x, "y": y, "visible": True, "click": True}],
                                  96, 64, 30, 3, effects)
                renderer = FrameRenderer(96, 64, effects)
                frame = Image.new("RGB", (96, 64), "#2563eb")
                for index in (0, 15, 40, 89):
                    rendered = renderer.render(frame, plan, index)
                    self.assertEqual(rendered.size, (96, 64))
                    pixels = np.asarray(rendered)
                    if not cinematic:
                        # Cropping at either edge must not expose a black border.
                        self.assertFalse(np.any(np.all(pixels == 0, axis=2)))

    def test_parallel_frames_are_in_order_and_match_serial(self):
        effects = Effects()
        track = [{"t": 0, "x": 30, "y": 20, "visible": True, "click": True}]
        plan = MotionPlan(track, 96, 64, 30, 1, effects)
        renderer = FrameRenderer(96, 64, effects)
        frames = [Image.new("RGB", (96, 64), (i * 10, 40, 80)) for i in range(12)]
        expected = [renderer.render(frame, plan, i).tobytes() for i, frame in enumerate(frames)]
        original = renderer.render

        def delayed(frame, motion, index):
            time.sleep(0.002 * (3 - index % 4))
            return original(frame, motion, index)

        with patch.object(renderer, "render", side_effect=delayed), \
             patch("cinematic.os.cpu_count", return_value=8):
            actual = list(rendered_frames(io.BytesIO(b"".join(f.tobytes() for f in frames)),
                                          renderer, plan))
        self.assertEqual(actual, expected)

    def test_parallel_worker_errors_are_not_swallowed(self):
        effects = Effects()
        plan = MotionPlan([{"t": 0, "x": 20, "y": 20}], 96, 64, 30, 1, effects)
        renderer = FrameRenderer(96, 64, effects)
        with patch.object(renderer, "render", side_effect=RuntimeError("render failed")), \
             self.assertRaisesRegex(RuntimeError, "render failed"):
            list(rendered_frames(io.BytesIO(bytes(96 * 64 * 3)), renderer, plan))

    def test_parallel_pipeline_is_bounded_and_can_be_closed_early(self):
        effects = Effects()
        plan = MotionPlan([{"t": 0, "x": 20, "y": 20}], 96, 64, 30, 1, effects)
        renderer = FrameRenderer(96, 64, effects)
        frame_bytes = 96 * 64 * 3
        stream = io.BytesIO(bytes(frame_bytes * 100))
        with patch("cinematic.os.cpu_count", return_value=8):
            frames = rendered_frames(stream, renderer, plan)
            self.assertEqual(len(next(frames)), frame_bytes)
            self.assertEqual(stream.tell(), frame_bytes * 8)
            frames.close()


class AudioReuseTests(unittest.TestCase):
    def test_final_mux_copies_both_streams(self):
        recorder = ScreenRecorder(ARTIFACTS / "unused.mp4", queue.Queue(),
                                  (0, 0, 96, 64), FFMPEG)
        with patch.object(recorder, "run") as run:
            recorder._mux(Path("rendered.mp4"), Path("result.mp4"), 5,
                          audio_source=Path("original.mp4"))
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-c") + 1], "copy")
        self.assertIn("1:a:0", args)
        self.assertNotIn("aac", args)
        self.assertNotIn("-filter_complex", args)
        self.assertIn("-n", args)

    def test_real_audio_packets_and_video_frame_count_preserved(self):
        recorder = ScreenRecorder(ARTIFACTS / "unused.mp4", queue.Queue(),
                                  (0, 0, 96, 64), FFMPEG)
        original, video, result = (ARTIFACTS / name for name in
                                   ("original.mp4", "rendered.mp4", "result.mp4"))
        recorder.run(["-n", "-f", "lavfi", "-i", "color=c=blue:s=96x64:r=30:d=1",
                      "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                      "-c:v", "libx264", "-c:a", "aac", "-shortest", str(original)])
        recorder.run(["-n", "-f", "lavfi", "-i", "color=c=red:s=96x64:r=30:d=1",
                      "-c:v", "libx264", str(video)])
        recorder._mux(video, result, 1, audio_source=original)
        for src in (original, result):
            recorder.run(["-n", "-i", str(src), "-vn", "-c:a", "copy",
                          str(src.with_suffix(".aac"))])
        self.assertEqual(original.with_suffix(".aac").read_bytes(),
                         result.with_suffix(".aac").read_bytes())
        decoded = subprocess.run(
            [str(FFMPEG), "-v", "error", "-i", str(result), "-an",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            capture_output=True, **recorder.popen_kwargs())
        self.assertEqual(decoded.returncode, 0, decoded.stderr)
        self.assertEqual(len(decoded.stdout), 30 * 96 * 64 * 3)
        self.assertAlmostEqual(recorder.duration(result), 1, delta=0.04)
        with self.assertRaises(FileExistsError):
            recorder._mux(video, result, 1, audio_source=original)

    def test_single_part_reuses_capture_and_retains_original(self):
        from test_studio import Device

        output = ARTIFACTS / "single-part.mp4"
        events = queue.Queue()
        recorder = ScreenRecorder(output, events, (0, 0, 96, 64), FFMPEG,
                                  effects=Effects(False, False, False))
        recorder.stop_event.set()

        class Tracks:
            def __init__(self, sources, session):
                self.paths = []
                self.threads = []

            def start(self):
                # _record checks this flag immediately after starting audio.
                recorder.stop_event.clear()

            def stop(self):
                pass

            def check(self):
                pass

            def set_active(self, active):
                pass

        def start():
            path = recorder.session / "video_0000.mp4"
            recorder.run(["-n", "-f", "lavfi", "-i", "color=s=96x64:r=30:d=1",
                          "-c:v", "libx264", str(path)])
            recorder.parts.append({"path": path, "samples": [], "wall_duration": 1})
            recorder.stop_event.set()

        seen = []

        def mux(video, dest, duration):
            seen.append(video)
            with dest.open("xb") as target:
                target.write(b"original-video")

        with patch("recording_v2.resolve_sources", return_value=[("system", Device())]), \
             patch("recording_v2.AudioTracks", Tracks), \
             patch.object(recorder, "_start_part", side_effect=start), \
             patch.object(recorder, "_align_audio"), \
             patch.object(recorder, "_mux", side_effect=mux):
            recorder._record()
        event_list = list(events.queue)
        self.assertEqual(event_list[-1][0], "stopped", event_list)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].name, "video_0000.mp4")
        self.assertTrue(seen[0].parent.name.startswith(".single-part_session_"))
        self.assertIsNone(recorder.session)
        self.assertFalse(any(output.parent.glob(".single-part_session_*")))
        self.assertEqual(output.read_bytes(), b"original-video")


if __name__ == "__main__":
    unittest.main()
