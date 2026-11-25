from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import media_conversion as media
from audio_softening import Cancelled, HIDDEN, ProcessingError, tool


class MediaConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = ROOT / "test-artifacts" / ("conversion_" + uuid.uuid4().hex[:8])
        cls.folder.mkdir(parents=True)
        cls.source = cls.folder / "测试视频 多音轨.mkv"
        cls.run_ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2",
            "-map", "0:v", "-map", "1:a", "-map", "2:a",
            "-c:v", "ffv1", "-c:a", "pcm_s16le",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=zho", str(cls.source))
        cls.source_hash = hashlib.sha256(cls.source.read_bytes()).hexdigest()

    @classmethod
    def run_ffmpeg(cls, *args):
        return subprocess.run(
            [tool("ffmpeg"), "-hide_banner", "-v", "error", "-n", *args],
            capture_output=True, check=True, **HIDDEN)

    def setUp(self):
        self.output = self.folder / uuid.uuid4().hex[:8]

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)

    def test_extracts_all_four_audio_formats_with_duration_and_first_track(self):
        expected = {"mp3": "mp3", "m4a": "aac", "wav": "pcm_s24le", "flac": "flac"}
        for fmt, codec in expected.items():
            with self.subTest(fmt=fmt):
                result = media.process_media(self.source, self.output, "audio", {"format": fmt})
                info = media.probe_media(Path(result["export_path"]))
                self.assertIsNone(info["video"])
                self.assertEqual(len(info["audio"]), 1)
                self.assertEqual(info["audio"][0]["codec_name"], codec)
                self.assertAlmostEqual(info["duration"], 2, delta=.1)
                self.assertEqual(info["audio"][0]["channels"], 1)
                raw = self.run_ffmpeg("-i", result["export_path"], "-f", "f32le",
                                      "-ar", "48000", "-ac", "1", "-").stdout
                import numpy as np
                samples = np.frombuffer(raw, dtype="<f4")
                spectrum = abs(np.fft.rfft(samples))
                frequency = np.argmax(spectrum) * 48000 / len(samples)
                self.assertAlmostEqual(frequency, 440, delta=2)

    def test_h265_and_h264_shrink_preserve_dimensions_timing_tracks_and_quality(self):
        for codec, name in (("h265", "hevc"), ("h264", "h264")):
            with self.subTest(codec=codec):
                progress = []
                result = media.process_media(
                    self.source, self.output, "compress", {"codec": codec, "quality": "clear"},
                    lambda percent, stage: progress.append(percent))
                video = result["video"]
                self.assertEqual(video["codec_name"], name)
                self.assertEqual((video["width"], video["height"]), (320, 180))
                self.assertEqual(video["avg_frame_rate"], "24/1")
                self.assertAlmostEqual(result["duration"], 2, delta=.15)
                self.assertEqual(result["audio_tracks"], 2)
                self.assertTrue(result["smaller"])
                self.assertGreater(result["saved_percent"], 50)
                self.assertEqual(progress[-1], 100)
                info = media.probe_media(Path(result["export_path"]))
                self.assertEqual([s.get("tags", {}).get("language") for s in info["audio"]],
                                 ["eng", "zho"])
                measured = subprocess.run(
                    [tool("ffmpeg"), "-hide_banner", "-i", str(self.source), "-i", result["export_path"],
                     # Matroska's millisecond timestamps round 24 fps frame times.
                     # Compare matching frame indices, not the previous rounded timestamp.
                     "-lavfi", "[0:v:0]settb=1/24,setpts=N[a];"
                               "[1:v:0]settb=1/24,setpts=N[b];[a][b]ssim",
                     "-an", "-f", "null", "-"],
                    capture_output=True, check=True, **HIDDEN)
                ssim = float(re.findall(rb"All:([0-9.]+)", measured.stderr)[-1])
                self.assertGreater(ssim, .99)
                for path in (self.source, result["export_path"]):
                    frames = subprocess.run(
                        [tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
                         "-count_frames", "-show_entries", "stream=nb_read_frames",
                         "-of", "json", str(path)], capture_output=True, check=True, **HIDDEN)
                    self.assertEqual(int(json.loads(frames.stdout)["streams"][0]["nb_read_frames"]), 48)
                Path(result["work_dir"], "quality-check.json").write_text(
                    json.dumps({"ssim": ssim, "saved_percent": result["saved_percent"]}))

    def test_silent_video_compresses_and_reports_missing_audio(self):
        silent = self.folder / ("silent_" + uuid.uuid4().hex[:8] + ".mp4")
        self.run_ffmpeg("-f", "lavfi", "-i", "color=black:size=96x64:rate=10:duration=1",
                        "-c:v", "libx264", str(silent))
        with self.assertRaisesRegex(ProcessingError, "没有音轨"):
            media.process_media(silent, self.output, "audio")
        result = media.process_media(silent, self.output, "compress",
                                     {"codec": "h264", "quality": "clear"})
        self.assertEqual(result["audio_tracks"], 0)
        self.assertEqual(result["smaller"], result["output_bytes"] < result["source_bytes"])
        # This tiny source is already compact: a larger output must never be labelled smaller.
        self.assertAlmostEqual(result["saved_percent"],
                               (1 - result["output_bytes"]/result["source_bytes"]) * 100)

    def test_compatible_audio_is_copied_without_reencoding(self):
        source = self.folder / ("aac_" + uuid.uuid4().hex[:8] + ".mp4")
        self.run_ffmpeg("-i", str(self.source), "-map", "0:v:0", "-map", "0:a:0",
                        "-c:v", "libx264", "-c:a", "aac", str(source))
        result = media.process_media(source, self.output, "compress", {"codec": "h264"})
        def audio_hash(path):
            return self.run_ffmpeg("-i", str(path), "-map", "0:a:0", "-c", "copy",
                                   "-f", "hash", "-hash", "sha256", "-").stdout
        self.assertEqual(audio_hash(source), audio_hash(result["export_path"]))

    def test_cancel_stops_running_encoder_and_retains_diagnostics(self):
        event = threading.Event()
        with patch.object(media, "probe_media", wraps=media.probe_media):
            def cancel_when_started(percent, stage):
                if percent >= 3:
                    event.set()
            with self.assertRaises(Cancelled):
                media.process_media(self.source, self.output, "compress", {},
                                    cancel_when_started, event)
        self.assertFalse(list(self.output.glob("*.mp4")))
        self.assertTrue(list(self.output.glob(".media_job_*/ffmpeg.log")))

    def test_precancel_invalid_media_and_settings_do_not_create_results(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            media.process_media(self.source, self.output, "compress", cancel=event)
        for operation, options in (("bad", {}), ("audio", {"format": "bad"}),
                                   ("compress", {"codec": "bad"}),
                                   ("compress", {"quality": "bad"})):
            with self.subTest(options=options), self.assertRaises(ProcessingError):
                media.process_media(self.source, self.output, operation, options)
        broken = self.folder / ("broken_" + uuid.uuid4().hex[:8] + ".mp4")
        broken.write_bytes(b"not a video")
        with self.assertRaises(ProcessingError):
            media.process_media(broken, self.output, "compress")
        self.assertFalse(self.output.exists())

    def test_repeated_runs_keep_existing_files(self):
        first = media.process_media(self.source, self.output, "audio", {"format": "flac"})
        first_bytes = Path(first["export_path"]).read_bytes()
        second = media.process_media(self.source, self.output, "audio", {"format": "flac"})
        self.assertNotEqual(first["export_path"], second["export_path"])
        self.assertEqual(Path(first["export_path"]).read_bytes(), first_bytes)

    def test_variable_frame_timing_is_preserved(self):
        source = self.folder / ("vfr_" + uuid.uuid4().hex[:8] + ".mkv")
        self.run_ffmpeg("-f", "lavfi", "-i", "testsrc2=size=96x64:rate=24:duration=2",
                        "-vf", "select=not(mod(n\\,3))+eq(mod(n\\,3)\\,1)",
                        "-fps_mode", "vfr", "-c:v", "ffv1", str(source))
        result = media.process_media(source, self.output, "compress", {"codec": "h264"})
        def timestamps(path):
            probe = subprocess.run(
                [tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_frames",
                 "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)],
                capture_output=True, check=True, **HIDDEN)
            return [float(f["best_effort_timestamp_time"]) for f in json.loads(probe.stdout)["frames"]]
        original, converted = timestamps(source), timestamps(result["export_path"])
        self.assertEqual(len(original), len(converted))
        self.assertLess(max(abs(a-b) for a, b in zip(original, converted)), .0011)


if __name__ == "__main__":
    unittest.main()
