"""Local, cancellable video conversion using the bundled FFmpeg tools."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from audio_softening import Cancelled, HIDDEN, ProcessingError, check_cancel, safe_stem, tool

VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv",
                  ".m4v", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".3gp"}
AUDIO_FORMATS = {"mp3", "m4a", "wav", "flac"}
# Conservative defaults: keep dimensions / timing; spend encoding time on quality.
QUALITY = {"clear": {"h264": 18, "h265": 20},
           "balanced": {"h264": 23, "h265": 24},
           "small": {"h264": 28, "h265": 28}}


def _number(value, fallback=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def probe_media(path: Path) -> dict:
    try:
        result = subprocess.run(
            [tool("ffprobe"), "-v", "error", "-show_format", "-show_streams",
             "-of", "json", str(path)], capture_output=True, timeout=30, **HIDDEN)
        if result.returncode:
            raise ValueError()
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        videos = [s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")]
        audios = [s for s in streams if s.get("codec_type") == "audio"]
        duration = _number(data.get("format", {}).get("duration"),
                           max((_number(s.get("duration")) for s in streams), default=0))
        if not (videos or audios) or duration <= 0:
            raise ValueError()
        return {"duration": duration, "video": videos[0] if videos else None,
                "audio": audios, "format": data.get("format", {})}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        raise ProcessingError("无法读取媒体文件，请确认文件完整且可以播放。") from None


def audio_arguments(info: dict, fmt: str) -> list[str]:
    if fmt not in AUDIO_FORMATS:
        raise ProcessingError("请选择 MP3、M4A、WAV 或 FLAC。")
    if not info["audio"]:
        raise ProcessingError("这个视频没有音轨，无法转成音频。")
    audio = info["audio"][0]
    args = ["-map", f"0:{audio['index']}", "-vn", "-sn", "-dn"]
    if fmt == "mp3":
        rates = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
        sr = min(rates, key=lambda r: abs(r - min(int(audio.get("sample_rate", 48000)), 48000)))
        args += ["-c:a", "libmp3lame", "-q:a", "2", "-ar", str(sr),
                 "-ac", str(min(2, int(audio.get("channels", 2)))), "-id3v2_version", "3"]
    elif fmt == "m4a":
        args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    elif fmt == "wav":
        args += ["-c:a", "pcm_s24le"]
    else:
        args += ["-c:a", "flac", "-compression_level", "5"]
    return args


def compression_arguments(info: dict, codec: str, quality: str) -> list[str]:
    if codec not in {"h264", "h265"} or quality not in QUALITY:
        raise ProcessingError("视频压缩设置无效。")
    video = info["video"]
    if not video:
        raise ProcessingError("所选文件没有视频画面，无法压缩视频。")
    hdr = video.get("color_transfer") in {"smpte2084", "arib-std-b67"}
    if hdr and codec != "h265":
        raise ProcessingError("HDR 视频请使用 H.265，以保留高动态范围色彩。")
    ten_bit = hdr or any(bit in video.get("pix_fmt", "") for bit in ("10", "12", "16"))
    pixel_format = "yuv420p10le" if codec == "h265" and ten_bit else "yuv420p"
    args = ["-map", f"0:{video['index']}", "-map", "0:a?", "-sn", "-dn",
            "-c:v", "libx265" if codec == "h265" else "libx264",
            "-preset", "slow", "-crf", str(QUALITY[quality][codec]),
            "-pix_fmt", pixel_format, "-threads", "4",
            "-fps_mode", "passthrough",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-movflags", "+faststart"]
    if codec == "h265":
        args += ["-tag:v", "hvc1", "-x265-params", "log-level=error:pools=4:frame-threads=2"]
    for key, option in (("color_primaries", "-color_primaries"),
                        ("color_transfer", "-color_trc"), ("color_space", "-colorspace"),
                        ("color_range", "-color_range")):
        value = video.get(key)
        if value and value not in {"unknown", "reserved", "unspecified"}:
            args += [option, value]
    for index, audio in enumerate(info["audio"]):
        # Preserve compressed MP4-compatible audio without another lossy encode.
        if audio.get("codec_name") in {"aac", "mp3", "alac", "ac3", "eac3"}:
            args += [f"-c:a:{index}", "copy"]
        else:
            channels = int(audio.get("channels", 2))
            bitrate = "192k" if channels <= 2 else ("384k" if channels <= 6 else "512k")
            args += [f"-c:a:{index}", "aac", f"-b:a:{index}", bitrate]
    return args


def _transcode(args, work: Path, duration: float, progress, cancel):
    """Drain progress and diagnostics to files; polling remains cancellable."""
    log_path, progress_path = work / "ffmpeg.log", work / "progress.txt"
    with log_path.open("wb") as log, progress_path.open("wb") as stats:
        process = subprocess.Popen(
            [tool("ffmpeg"), "-hide_banner", "-v", "warning", "-nostdin", "-n",
             "-progress", "pipe:1", "-nostats", *args],
            stdin=subprocess.DEVNULL, stdout=stats, stderr=log, **HIDDEN)
        pending = ""
        try:
            with progress_path.open("r", encoding="utf-8", errors="replace") as reader:
                while True:
                    check_cancel(cancel)
                    pending += reader.read()
                    lines = pending.split("\n")
                    pending = lines.pop()
                    for line in lines:
                        if line.startswith("out_time_us="):
                            elapsed = _number(line.partition("=")[2]) / 1_000_000
                            progress(min(97., 3 + elapsed / duration * 94), "正在转换")
                    if process.poll() is not None:
                        break
                    cancel.wait(.1)
            if process.returncode:
                log.flush()
                with log_path.open("rb") as diagnostics:
                    diagnostics.seek(max(0, log_path.stat().st_size - 2500))
                    detail = diagnostics.read().decode("utf-8", "replace")
                raise ProcessingError("转换失败。\n" + detail)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def process_media(source, destination, operation, options=None, progress=None, cancel=None):
    """Create a new result, retaining diagnostics and interrupted partial outputs."""
    options = dict(options or {})
    progress = progress or (lambda _percent, _stage: None)
    cancel = cancel or threading.Event()
    if operation not in {"audio", "compress"}:
        raise ProcessingError("未知的媒体处理任务。")
    source, destination = Path(source).resolve(), Path(destination).expanduser().resolve()
    if not source.is_file():
        raise ProcessingError("源文件不存在，请重新选择。")
    check_cancel(cancel)
    progress(1, "读取文件信息")
    info = probe_media(source)
    check_cancel(cancel)
    if operation == "audio":
        fmt = options.get("format", "mp3")
        args = audio_arguments(info, fmt)
        label = "音频"
    else:
        fmt = "mp4"
        args = compression_arguments(info, options.get("codec", "h265"),
                                     options.get("quality", "clear"))
        label = "压缩"
    destination.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    prefix = f"{safe_stem(source.name)}_{label}_{time.strftime('%Y%m%d_%H%M%S')}_{token[:12]}"
    output = destination / f"{prefix}.{fmt}"
    work = destination / (".media_job_" + token)
    work.mkdir(exist_ok=False)
    partial = work / f"output.partial.{fmt}"
    try:
        _transcode(["-i", str(source), "-map_metadata", "0", *args, str(partial)],
                   work, info["duration"], progress, cancel)
        check_cancel(cancel)
        progress(98, "校验成品")
        verified = probe_media(partial)
        if operation == "audio":
            if not verified["audio"] or verified["video"]:
                raise ProcessingError("导出音频校验失败。")
            expected = _number(info["audio"][0].get("duration"))
            if expected and abs(verified["duration"] - expected) > .25:
                raise ProcessingError("导出音轨时长不一致，请检查处理记录。")
        else:
            if not verified["video"] or len(verified["audio"]) != len(info["audio"]):
                raise ProcessingError("导出视频或音轨校验失败。")
            if abs(verified["duration"] - info["duration"]) > .5:
                raise ProcessingError("导出视频时长不一致，请检查处理记录。")
        check_cancel(cancel)
        source_bytes, output_bytes = source.stat().st_size, partial.stat().st_size
        # Reserve exclusively before replacing our own empty reservation.
        with output.open("xb"):
            pass
        os.replace(partial, output)
        result = {
            "operation": operation, "source": str(source), "export_path": str(output),
            "source_bytes": source_bytes, "output_bytes": output_bytes,
            "saved_percent": (1 - output_bytes / source_bytes) * 100 if source_bytes else 0,
            "smaller": output_bytes < source_bytes, "duration": verified["duration"],
            "options": options, "work_dir": str(work), "video": verified["video"],
            "audio_tracks": len(verified["audio"]),
        }
        report = work / "result.json"
        result["report_path"] = str(report)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        progress(100, "处理完成")
        return result
    except Cancelled:
        raise Cancelled(f"已取消，原文件和已完成成品保留。\n未完成文件与日志：{work}") from None
    except Exception as error:
        raise ProcessingError(f"{error}\n处理记录：{work}") from error
