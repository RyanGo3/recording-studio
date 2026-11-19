from __future__ import annotations

import json
import queue
import subprocess
import sys
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audio_processing import cleanup_filter
from audio_sources import AudioTracks, SAMPLE_RATE
from recording_v2 import ScreenRecorder

ARTIFACTS = ROOT / "test-artifacts" / ("audio_cleanup_" + uuid.uuid4().hex[:8])
ARTIFACTS.mkdir(parents=True)
FFMPEG = ROOT / "tools" / "ffmpeg.exe"


class CleanupTests(unittest.TestCase):
    def process(self, signal, kind="system"):
        signal = np.asarray(signal, dtype="<f4")
        process = subprocess.run(
            [str(FFMPEG), "-v", "error", "-f", "f32le", "-ar", str(SAMPLE_RATE),
             "-ac", "1", "-i", "pipe:0", "-af", cleanup_filter(kind),
             "-f", "f32le", "pipe:1"], input=signal.tobytes(), capture_output=True,
            **ScreenRecorder.popen_kwargs())
        self.assertEqual(process.returncode, 0, process.stderr)
        return np.frombuffer(process.stdout, dtype="<f4")

    def test_noise_reduced_and_tone_preserved(self):
        t = np.arange(5 * SAMPLE_RATE) / SAMPLE_RATE
        rng = np.random.default_rng(11)
        signal = (0.2 * np.sin(2 * np.pi * 1000 * t) * ((t > 1) & (t < 4))
                  + 0.004 * rng.normal(size=len(t)) + 0.02 * np.sin(2 * np.pi * 20 * t))
        metrics = {}
        for kind in ("system", "microphone"):
            result = self.process(signal, kind)
            self.assertEqual(len(result), len(signal))
            quiet = slice(SAMPLE_RATE // 2, SAMPLE_RATE)
            loud = slice(SAMPLE_RATE * 2, SAMPLE_RATE * 3)
            ratio = float(np.std(result[quiet]) / np.std(signal[quiet]))
            self.assertLess(ratio, 0.5)
            self.assertGreater(np.std(result[loud]), 0.09)
            self.assertLess(np.std(result[loud]), 0.17)
            self.assertTrue(np.isfinite(result).all())
            metrics[kind] = {"noise_rms_ratio": ratio,
                             "tone_rms": float(np.std(result[loud]))}
        (ARTIFACTS / "metrics.json").write_text(json.dumps(metrics, indent=2))

    def test_latency_and_tail_are_preserved(self):
        signal = np.zeros(SAMPLE_RATE, dtype="<f4")
        signal[10000] = 0.5
        signal[-100] = 0.5
        for kind in ("system", "microphone"):
            result = self.process(signal, kind)
            self.assertEqual(len(result), len(signal))
            self.assertLessEqual(abs(np.argmax(np.abs(result[:20000])) - 10000), 1)
            self.assertGreater(np.max(np.abs(result[-200:])), 0.2)

    def test_silence_is_not_amplified(self):
        for kind in ("system", "microphone"):
            result = self.process(np.zeros(SAMPLE_RATE), kind)
            self.assertEqual(len(result), SAMPLE_RATE)
            self.assertEqual(np.max(np.abs(result)), 0)

    def test_alignment_filters_each_track_once_and_can_be_disabled(self):
        for enabled in (True, False):
            recorder = ScreenRecorder(ARTIFACTS / "unused.mp4", queue.Queue(),
                                      (0, 0, 96, 64), FFMPEG, noise_reduction=enabled)
            recorder.session = ARTIFACTS
            recorder.parts = [{"duration": 1, "audio_start": 0, "audio_end": 1,
                               "wall_duration": 1}]
            recorder._audio = type("Tracks", (), {"paths": [Path("system.wav"),
                                                           Path("microphone.wav")]})()
            with patch.object(recorder, "run") as run:
                recorder._align_audio()
            self.assertEqual(run.call_count, 2)
            for call, kind in zip(run.call_args_list, ("system", "microphone")):
                args = call.args[0]
                graph = args[args.index("-filter_complex") + 1]
                self.assertEqual(graph.count("afftdn"), int(enabled))
                if enabled:
                    self.assertIn(cleanup_filter(kind), graph)
                self.assertIn("_aligned_clean" if enabled else "_aligned", args[-1])

    def test_real_alignment_keeps_pause_duration_and_raw_wav(self):
        folder = ARTIFACTS / "alignment"
        folder.mkdir()
        raw = folder / "system.wav"
        t = np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE
        pcm = (np.sin(2 * np.pi * 440 * t) * 9000).astype("<i2")
        with wave.open(str(raw), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(np.column_stack([pcm, pcm]).tobytes())
        original = raw.read_bytes()
        recorder = ScreenRecorder(folder / "unused.mp4", queue.Queue(), (0, 0, 96, 64), FFMPEG)
        recorder.session = folder
        recorder._audio = type("Tracks", (), {"paths": [raw]})()
        recorder.parts = [{"duration": 0.7, "audio_start": 0, "audio_end": 0.8,
                           "wall_duration": 0.8},
                          {"duration": 0.8, "audio_start": 0.8, "audio_end": 1.8,
                           "wall_duration": 1.0}]
        recorder._align_audio()
        self.assertEqual(raw.read_bytes(), original)
        with wave.open(str(recorder._audio.paths[0])) as audio:
            self.assertEqual(audio.getnframes(), int(1.5 * SAMPLE_RATE))
            self.assertEqual(audio.getnchannels(), 2)


class CaptureContinuityTests(unittest.TestCase):
    def test_scheduler_jitter_does_not_insert_silence_or_cut_samples(self):
        folder = ARTIFACTS / "continuity"
        folder.mkdir()
        tracks = AudioTracks([], folder)
        tracks.active_epoch = 1
        blocks = []

        class Device:
            name = "test"

            def recorder(self, **kwargs):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def record(self, numframes):
                i = len(blocks)
                block = np.full((numframes, 2), 0.1 + 0.05 * i, dtype=np.float32)
                blocks.append(block)
                if i == 3:
                    tracks.stop_event.set()
                return block

        path = folder / "system.wav"
        # Device data is contiguous, but arrival times have +- several ms jitter.
        with patch.object(tracks, "clock",
                          side_effect=[(True, 0.05), (True, 0.108),
                                       (True, 0.151), (True, 0.203)]):
            tracks._reader(Device(), path)
        tracks.check()
        with wave.open(str(path)) as audio:
            actual = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        expected = (np.concatenate(blocks) * 32767).astype("<i2").ravel()
        np.testing.assert_array_equal(actual, expected)


class AudioOnlyTests(unittest.TestCase):
    def test_mp3_cleanup_keeps_raw_and_disabled_mode_stays_direct(self):
        import app

        for enabled in (True, False):
            output = ARTIFACTS / f"audio_only_{enabled}.mp3"
            events = queue.Queue()
            recorder = app.SystemAudioRecorder(output, events, noise_reduction=enabled,
                                                ffmpeg_path=FFMPEG)

            class Device:
                name = "fake system"

                def recorder(self, **kwargs):
                    self.kwargs = kwargs
                    return self

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    pass

                def record(self, numframes):
                    recorder.stop_event.set()
                    return np.full((numframes, 2), 0.01, dtype=np.float32)

            device = Device()
            with patch.object(recorder, "find_loopback_microphone",
                              return_value=(device, device)):
                recorder._record()
            recorded_events = list(events.queue)
            self.assertEqual(recorded_events[-1][0], "stopped", recorded_events)
            self.assertEqual(any(e[0] == "processing" for e in recorded_events), enabled)
            self.assertGreater(output.stat().st_size, 0)
            self.assertEqual(device.kwargs["channels"], 2)
            if enabled:
                self.assertIsNone(recorder.session)
                self.assertFalse(any(output.parent.glob(f".{output.stem}_session_*")))
            else:
                self.assertIsNone(recorder.session)


if __name__ == "__main__":
    unittest.main()
