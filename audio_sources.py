"""Independent WASAPI readers; neither source can block the other."""
from __future__ import annotations

import queue
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundcard as sc

SAMPLE_RATE = 48_000
BLOCK_SIZE = 2_400
AUDIO_MODES = {"system", "mixed", "microphone"}


def wav_header(data_bytes: int) -> bytes:
    """Reserve ds64 space so long recordings can become RF64 without moving PCM."""
    riff_size = data_bytes + 72
    if riff_size > 0xFFFFFFFF:
        prefix = (b"RF64" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEds64" +
                  struct.pack("<IQQQI", 28, riff_size, data_bytes, data_bytes // 4, 0))
        size = 0xFFFFFFFF
    else:
        prefix = b"RIFF" + struct.pack("<I", riff_size) + b"WAVEJUNK" + struct.pack("<I", 28) + bytes(28)
        size = data_bytes
    return prefix + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 16) + b"data" + struct.pack("<I", size)


class PCMFile:
    """Normal WAV for small files, RF64 above 4 GiB (12-hour sessions)."""
    def __init__(self, path: Path) -> None:
        self.file = path.open("xb")
        self.data_bytes = 0
        self.file.write(wav_header(0))

    def __enter__(self):
        return self

    def writeframesraw(self, data: bytes) -> None:
        self.file.write(data)
        self.data_bytes += len(data)

    def __exit__(self, *_):
        try:
            self.file.seek(0)
            self.file.write(wav_header(self.data_bytes))
        finally:
            self.file.close()


def stereo(samples: np.ndarray) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim == 1:
        samples = samples[:, None]
    if samples.shape[1] == 1:
        return np.repeat(samples, 2, axis=1)
    return samples[:, :2]


def resolve_sources(mode: str, microphone_id: str | None = None) -> list[tuple[str, object]]:
    if mode not in AUDIO_MODES:
        raise ValueError("不支持的声音来源。")
    result = []
    if sys.platform == "darwin":
        if mode in {"system", "mixed"}:
            devices = [device for device in sc.all_microphones()
                       if device.name.casefold() == "blackhole 2ch"]
            if len(devices) != 1:
                raise RuntimeError(
                    "系统录音需要 BlackHole 2ch。请安装后在“音频 MIDI 设置”中创建"
                    "包含扬声器和 BlackHole 2ch 的多输出设备，并将其设为系统输出。"
                    "详见 docs/MACOS.md；仅录麦克风可选择“仅外部声音”。")
            result.append(("system", devices[0]))
        if mode in {"microphone", "mixed"}:
            source = sc.get_microphone(microphone_id) if microphone_id else sc.default_microphone()
            if source is None or "blackhole" in source.name.casefold():
                raise RuntimeError(
                    "请将 macOS 默认输入设为实际麦克风，并在系统设置中允许麦克风访问。")
            result.append(("microphone", source))
        return result
    if mode in {"system", "mixed"}:
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("没有找到默认播放设备。")
        # Match the endpoint ID, never silently choose a different output.
        loopbacks = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
        source = next((m for m in loopbacks if m.id == speaker.id), None)
        if source is None:
            source = next((m for m in loopbacks if m.name == speaker.name), None)
        if source is None:
            raise RuntimeError("默认播放设备没有可用的系统声音回放接口。")
        result.append(("system", source))
    if mode in {"microphone", "mixed"}:
        source = sc.get_microphone(microphone_id, include_loopback=False) if microphone_id else sc.default_microphone()
        if source is None or source.isloopback:
            raise RuntimeError("没有找到外部输入设备，请检查麦克风及 Windows 麦克风权限。")
        result.append(("microphone", source))
    return result


class AudioTracks:
    """Record each source into a separate, synchronized PCM track.

    The shared clock excludes pauses. Silence fills late device startup / gaps;
    mixing happens once at export rather than reading two devices sequentially.
    """
    def __init__(self, sources: list[tuple[str, object]], folder: Path) -> None:
        self.sources = sources
        self.paths = [folder / f"{name}.wav" for name, _ in sources]
        self.errors: queue.Queue[str] = queue.Queue()
        self.ready: queue.Queue[bool] = queue.Queue()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.active_since: float | None = None
        self.elapsed = 0.0
        self.active_epoch = 0
        self.threads: list[threading.Thread] = []

    def set_active(self, active: bool) -> None:
        with self.lock:
            now = time.monotonic()
            if active and self.active_since is None:
                self.active_since = now
                self.active_epoch += 1
            elif not active and self.active_since is not None:
                self.elapsed += now - self.active_since
                self.active_since = None

    def clock(self) -> tuple[bool, float]:
        with self.lock:
            active = self.active_since is not None
            return active, self.elapsed + (time.monotonic() - self.active_since if active else 0.0)

    def start(self) -> None:
        for (_, device), path in zip(self.sources, self.paths):
            thread = threading.Thread(target=self._reader, args=(device, path), daemon=True)
            self.threads.append(thread)
            thread.start()
        deadline = time.monotonic() + 12
        for _ in self.sources:
            try:
                self.ready.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                raise RuntimeError("声音设备启动超时。")
            self.check()

    def check(self) -> None:
        try:
            error = self.errors.get_nowait()
        except queue.Empty:
            return
        raise RuntimeError(error)

    def stop(self) -> None:
        self.set_active(False)
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=8)
        if any(thread.is_alive() for thread in self.threads):
            raise RuntimeError("声音设备未能及时关闭，已保留录制素材。")
        self.check()

    def _reader(self, device, path: Path) -> None:
        try:
            with PCMFile(path) as output:
                # Shared-mode WASAPI remixes native mono / surround endpoints to stereo.
                # Never request a single channel: SoundCard's Windows backend can
                # produce corrupt data for a mono capture stream.
                with device.recorder(samplerate=SAMPLE_RATE, channels=2, blocksize=BLOCK_SIZE) as recorder:
                    self.ready.put(True)
                    written = 0
                    epoch = None
                    while not self.stop_event.is_set():
                        samples = stereo(recorder.record(numframes=BLOCK_SIZE))
                        active, end = self.clock()
                        if not active:
                            continue
                        if epoch != self.active_epoch:
                            # Anchor only at start/resume. Device samples are contiguous:
                            # scheduler jitter is not missing audio. Inserting zeros or
                            # dropping samples on every block creates discontinuities.
                            epoch = self.active_epoch
                            end_frame = int(end * SAMPLE_RATE)
                            start_frame = max(0, end_frame - len(samples))
                            gap = max(0, start_frame - written)
                            while gap:
                                count = min(gap, SAMPLE_RATE)
                                output.writeframesraw(bytes(count * 4))
                                written += count
                                gap -= count
                            samples = samples[max(0, written - start_frame):]
                        samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
                        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
                        output.writeframesraw(pcm.tobytes())
                        written += len(samples)
        except Exception as error:
            self.errors.put(f"声音采集失败（{device.name}）：{error}")
            self.ready.put(False)
