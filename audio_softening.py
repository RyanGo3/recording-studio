"""Pitch targeting and linked, dynamic spectral softening with bounded memory."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import numpy as np
from scipy import ndimage, signal
from scipy.fft import rfft, irfft

VERSION = "2.0.0"
HIDDEN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
SUPPORTED = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aif", ".aiff"}
SUPPORTED |= {".mp4", ".mkv", ".mov", ".webm", ".avi"}
MAX_SOURCE_BYTES = 2 * 1024 ** 3


class ProcessingError(Exception):
    pass


class Cancelled(ProcessingError):
    pass


class NoTargetFound(ProcessingError):
    pass


def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def file_hash(path):
    with Path(path).open("rb") as fp:
        return hashlib.file_digest(fp,"sha256").hexdigest()


def tool(name):
    suffix = ".exe" if os.name == "nt" else ""
    app_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    for root in [app_root, resource_root(), Path(sys.executable).parent]:
        for folder in [root, root / "tools"]:
            candidate = folder / (name + suffix)
            if candidate.is_file():
                return str(candidate)
    found = shutil.which(name)
    if not found:
        raise ProcessingError(f"缺少 {name}，请运行 scripts\\prepare-tools.ps1 或使用完整的程序文件夹。")
    return found


def check_cancel(event):
    if event.is_set():
        raise Cancelled("已取消，原文件未改动。")


def checked_run(args, event, timeout=180):
    """Drain diagnostic output to disk so large stderr cannot deadlock a worker."""
    with tempfile.TemporaryFile() as log:
        p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=log, **HIDDEN)
        deadline = time.monotonic() + timeout
        try:
            while p.poll() is None:
                check_cancel(event)
                if time.monotonic() > deadline:
                    raise ProcessingError("处理时间过长，请检查文件或改用较短的音频。")
                event.wait(.08)
            if p.returncode:
                log.seek(0)
                detail = log.read()[-3000:].decode("utf-8", "replace")
                raise ProcessingError("音频组件无法处理该文件。" + ("\n" + detail[-1000:] if detail else ""))
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()


def probe(path):
    try:
        p = subprocess.run([tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                           capture_output=True, timeout=30, **HIDDEN)
        if p.returncode:
            raise ValueError()
        data = json.loads(p.stdout)
        audio = next(s for s in data["streams"] if s["codec_type"] == "audio")
        duration = float(audio.get("duration") or data["format"].get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
        channels = int(audio["channels"])
        sample_rate = int(audio["sample_rate"])
        if channels < 1 or sample_rate < 4000:
            raise ValueError()
        return {"duration": duration, "channels": channels, "sample_rate": sample_rate, "codec": audio["codec_name"]}
    except (ValueError, KeyError, StopIteration, subprocess.TimeoutExpired):
        raise ProcessingError("无法读取音频，请确认文件可以正常播放，且没有损坏。") from None


def pitch_regions(mono_path, scratch, progress, cancel):
    """Bounded-memory autocorrelation tracker, matching the original treatment."""
    n = mono_path.stat().st_size//4
    if n < 160:
        return []
    filtered_path = scratch / "filtered.f32"
    sos = signal.butter(3, [70, 2200], "bandpass", fs=16000, output="sos")
    state = np.zeros((len(sos), 2))
    with mono_path.open("rb") as source, filtered_path.open("wb") as target:
        for start in range(0, n, 16000 * 20):
            check_cancel(cancel)
            end = min(n, start + 16000 * 20)
            block = np.frombuffer(source.read((end-start)*4),dtype="<f4")
            filtered, state = signal.sosfilt(sos,block,zi=state)
            target.write(filtered.astype("<f4").tobytes())
    frame_size, hop = 1024, 160
    count = n // hop + 1
    f0 = np.zeros(count, dtype=np.float32)
    confidence = np.zeros(count, dtype=np.float32)
    db = np.zeros(count, dtype=np.float32)
    lags = np.arange(13, 230)
    with mono_path.open("rb") as source, filtered_path.open("rb") as filtered_source:
        for first in range(0, count, 512):
            check_cancel(cancel)
            last = min(count, first + 512)
            a, b = first * hop - 512, (last - 1) * hop + 512
            left, right = max(0, -a), max(0, b - n)
            begin,end=max(0,a),min(n,b)
            source.seek(begin*4);filtered_source.seek(begin*4)
            yf=np.pad(np.frombuffer(filtered_source.read((end-begin)*4),dtype="<f4"),(left,right))
            xf=np.pad(np.frombuffer(source.read((end-begin)*4),dtype="<f4"),(left,right))
            frames = np.lib.stride_tricks.sliding_window_view(yf, frame_size)[::hop].copy()
            raw = np.lib.stride_tricks.sliding_window_view(xf, frame_size)[::hop]
            frames -= frames.mean(axis=1, keepdims=True)
            ac = irfft(np.abs(rfft(frames, n=2048, axis=1)) ** 2, n=2048, axis=1)[:, :frame_size]
            energy = np.concatenate([np.zeros((len(frames), 1)), np.cumsum(frames**2, axis=1)], axis=1)
            denominator = np.sqrt(np.maximum(energy[:,frame_size-lags] * (energy[:,frame_size,None]-energy[:,lags]), 0)) + 1e-12
            corr = ac[:,lags] / denominator
            peaks = (corr[:,1:-1] > corr[:,:-2]) & (corr[:,1:-1] >= corr[:,2:])
            peak_values = np.where(peaks, corr[:,1:-1], 0)
            best = peak_values.max(axis=1)
            eligible = peaks & (corr[:,1:-1] >= np.maximum(.70, best[:,None]*.93))
            idx = np.where(eligible.any(axis=1), eligible.argmax(axis=1), peak_values.argmax(axis=1)) + 1
            row = np.arange(len(idx))
            aa, bb, cc = corr[row,idx-1], corr[row,idx], corr[row,idx+1]
            lag = lags[idx] + np.clip(.5*(aa-cc)/(aa-2*bb+cc+1e-12), -.5, .5)
            f0[first:last] = 16000 / lag
            confidence[first:last] = np.clip(corr[row,idx], -1, 1)
            db[first:last] = 20*np.log10(np.sqrt(np.mean(raw**2, axis=1)+1e-12)+1e-10)
            progress(12 + 40 * last/count, "定位高音片段")
    candidate = (f0>460)&(f0<1100)&(confidence>.72)&(db>-38)
    candidate = ndimage.binary_opening(candidate, structure=np.ones(3))
    candidate = ndimage.binary_closing(candidate, structure=np.ones(40))
    labels, _ = ndimage.label(candidate)
    regions = []
    low = (f0<420)&(confidence>.88)&(db>-34)
    for label, bounds in enumerate(ndimage.find_objects(labels), 1):
        if bounds is None:
            continue
        check_cancel(cancel)
        a, b = bounds[0].start, bounds[0].stop
        anchor = (f0[a:b]>530)&(confidence[a:b]>.84)&(db[a:b]>-34)
        if anchor.sum() < 7:
            continue
        start, end = max(0, a*.01-.14), min(n/16000, (b-1)*.01+.22)
        before = np.flatnonzero(low[max(0,a-14):a])
        after = np.flatnonzero(low[b:min(count,b+22)])
        if len(before):
            start = max(start, (max(0,a-14)+int(before[-1]))*.01+.015)
        if len(after):
            end = min(end, (b+int(after[0]))*.01-.015)
        if end-start>.1:
            if regions and start <= regions[-1][1]:
                regions[-1][1] = max(end, regions[-1][1])
            else:
                regions.append([start,end])
    return regions


def peak_filter(frequency, q, gain, sr):
    a = 10**(gain/40)
    w = 2*np.pi*frequency/sr
    alpha = np.sin(w)/(2*q)
    denominator = np.array([1+alpha/a, -2*np.cos(w), 1-alpha/a])
    numerator = np.array([1+alpha*a, -2*np.cos(w), 1-alpha*a])
    return np.r_[numerator/denominator[0], denominator/denominator[0]]


def region_weights(a, b, spans, sr, mode):
    if mode == "whole":
        return np.ones(b-a, dtype=np.float64)
    weight = np.zeros(b-a, dtype=np.float64)
    for begin, end in spans:
        if begin >= b:
            break
        lo, hi = max(a, begin), min(b, end)
        if lo >= hi:
            continue
        indices = np.arange(lo, hi)
        fade = max(1, min(round(.065*sr), (end-begin)//3))
        phase = np.minimum(np.minimum((indices-begin)/fade, (end-1-indices)/fade), 1)
        weight[lo-a:hi-a] = np.maximum(weight[lo-a:hi-a], .5-.5*np.cos(np.pi*np.maximum(phase, 0)))
    return weight


def spectral_gains(spectra, frequencies, sr, hop, strength, state):
    """Detect excess presence/sibilance relative to vocal body; link both channels.

    This is an independent STFT implementation, not a port of a plugin. Real,
    smooth gains preserve phase; attack/release state survives every batch.
    """
    power = np.max(np.abs(spectra)**2, axis=2)
    body = power[:, (frequencies >= 100) & (frequencies < 1800)].sum(axis=1)
    factor = strength / 55
    bands = ((2200, 5000, -18., 6., .008, .110),
             (5000, 12000, -24., 8., .004, .085))
    curves = [
        np.exp(-.5*(np.log(np.maximum(frequencies, 1)/3300)/.36)**2),
        np.clip((frequencies-4200)/2000, 0, 1) *
        np.clip((16000-frequencies)/5000, 0, 1),
    ]
    # Protect the fundamental and low harmonics exactly.
    curves[0] *= np.clip((frequencies-1600)/600, 0, 1)
    gains_db = np.zeros((len(spectra), len(frequencies)))
    for index, ((low, high, threshold, depth, attack, release), curve) in enumerate(zip(bands, curves)):
        band = power[:, (frequencies >= low) & (frequencies < min(high, sr*.49))].sum(axis=1)
        excess = 10*np.log10((band+1e-20)/(body+1e-20)) - threshold
        # Six dB soft knee; hard upper bound prevents over-dulling consonants.
        knee = np.where(excess <= -3, 0, np.where(excess >= 3, excess, (excess+3)**2/12))
        desired = np.minimum(depth*factor, .75*factor*knee)
        desired[power.sum(axis=1) < 1e-10] = 0
        envelope = np.empty(len(spectra))
        for i, target in enumerate(desired):
            coefficient = math.exp(-hop/(sr*(attack if target > state[index] else release)))
            state[index] = coefficient*state[index] + (1-coefficient)*target
            envelope[i] = state[index]
        gains_db -= envelope[:, None]*curve[None, :]
    # The overlapping band skirts cannot stack beyond the overall depth cap.
    gains_db = np.maximum(gains_db, -9*factor)
    return 10**(gains_db/20), gains_db


def soften_blocks(raw_path, mixed_path, regions, sr, channels, strength, mode, progress, cancel, stats=None):
    """Streaming, centered Hann overlap-add, with sample-exact region bypass."""
    samples = raw_path.stat().st_size//(4*channels)
    nfft = max(256, min(8192, 2**round(math.log2(sr*.032))))
    hop = nfft//4
    window = signal.windows.hann(nfft, sym=False)
    frequencies = np.fft.rfftfreq(nfft, 1/sr)
    frames = (samples+nfft//2+hop-1)//hop
    spans = [(max(0,round(a*sr)),min(samples,round(b*sr))) for a,b in regions]
    state = np.zeros(2)
    carry = np.zeros((nfft-hop, channels))
    carry_norm = np.zeros(nfft-hop)
    source_energy = output_energy = max_cut = sum_cut = 0.
    measured = 0
    with raw_path.open("rb") as input_file, mixed_path.open("wb") as fp:
        for first in range(0, frames, 128):
            check_cancel(cancel)
            count = min(128, frames-first)
            start = first*hop-nfft//2
            size = (count-1)*hop+nfft
            lo, hi = max(0, start), min(samples, start+size)
            input_file.seek(lo*channels*4)
            slab = np.zeros((size, channels))
            slab[lo-start:hi-start] = np.frombuffer(
                input_file.read((hi-lo)*channels*4), dtype="<f4").reshape(-1, channels)
            if not np.isfinite(slab).all():
                raise ProcessingError("音频中存在无法处理的数据。")
            batch = np.lib.stride_tricks.sliding_window_view(slab, nfft, axis=0)[::hop].transpose(0, 2, 1)
            spectra = rfft(batch*window[None, :, None], axis=1)
            gains, db = spectral_gains(spectra, frequencies, sr, hop, strength, state)
            centers = (first+np.arange(count))*hop
            active = region_weights(int(centers[0]), int(centers[-1])+1, spans, sr, mode)[::hop] > .5
            if active.any():
                cuts = -db[active].min(axis=1)
                max_cut = max(max_cut, float(cuts.max()))
                sum_cut += float(cuts.sum())
                measured += len(cuts)
            processed = irfft(spectra*gains[:, :, None], n=nfft, axis=1)*window[None, :, None]
            ola = np.zeros((size, channels))
            norm = np.zeros(size)
            ola[:len(carry)] = carry
            norm[:len(carry_norm)] = carry_norm
            for i in range(count):
                offset = i*hop
                ola[offset:offset+nfft] += processed[i]
                norm[offset:offset+nfft] += window**2
            flush = count*hop
            a, b = max(0, start), min(samples, start+flush)
            if b > a:
                source = slab[a-start:b-start]
                filtered = ola[a-start:b-start]/np.maximum(norm[a-start:b-start, None], 1e-12)
                weight = region_weights(a, b, spans, sr, mode)
                out = source+(filtered-source)*weight[:, None]
                source_energy += float(np.sum(source**2))
                output_energy += float(np.sum(out**2))
                fp.write(out.astype("<f4").tobytes())
            carry, carry_norm = ola[flush:].copy(), norm[flush:].copy()
            progress(55+26*min(samples, start+flush)/max(samples, 1), "动态柔化刺耳声与齿音")
    if stats is not None:
        stats.update(max_band_reduction_db=round(max_cut, 3),
                     mean_band_reduction_db=round(sum_cut/max(measured, 1), 3),
                     rms_change_db=round(10*math.log10((output_energy+1e-20)/(source_energy+1e-20)), 3))
    return samples


def preview_rms(path, start, duration, sr, channels):
    with path.open("rb") as fp:
        fp.seek(round(start*sr)*channels*4)
        data = np.frombuffer(fp.read(round(duration*sr)*channels*4), dtype="<f4")
    return float(np.sqrt(np.mean(data.astype(np.float64)**2))) if data.size else 0.


def safe_stem(name):
    stem=Path(name.replace("\\","/")).stem
    stem=re.sub(r'[<>:"/\\|?*\x00-\x1f]',"_",stem).strip(" .")[:90] or "音频"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$",stem,re.I):
        stem="audio_"+stem
    return stem


def process(source, display_name, destination, options, progress=None, cancel=None):
    progress=progress or (lambda percent,stage: None)
    cancel=cancel or threading.Event()
    mode=options.get("mode","high")
    fmt=options.get("format","mp3")
    strength=options.get("strength",55)
    if mode not in {"high","whole"} or fmt not in {"mp3","wav","flac"}:
        raise ProcessingError("处理设置无效。")
    if isinstance(strength,bool) or not isinstance(strength,(int,float)) or not math.isfinite(strength) or not 15<=strength<=100:
        raise ProcessingError("柔化强度需在 15–100 之间。")
    source,destination=Path(source),Path(destination)
    if not source.is_file() or source.suffix.lower() not in SUPPORTED:
        raise ProcessingError("请选择支持的音频或录像文件。")
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ProcessingError("单个文件不能超过 2 GB，请先切分后重试。")
    destination.mkdir(parents=True,exist_ok=True)
    progress(1,"读取音频")
    check_cancel(cancel)
    info=probe(source)
    sr=min(info["sample_rate"],48000) if fmt=="mp3" else info["sample_rate"]
    if fmt=="mp3":
        sr=min([8000,11025,12000,16000,22050,24000,32000,44100,48000],key=lambda r:abs(r-sr))
    channels=min(info["channels"],2)
    timeout=max(180,info["duration"]*1.5)
    token=time.strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:6]
    prefix=safe_stem(display_name)+"_柔化_"+token
    output=destination/(prefix+"."+fmt)
    partial=destination/(prefix+".partial."+fmt)
    original_preview=destination/(prefix+"_原声试听.mp3")
    softened_preview=destination/(prefix+"_柔化试听.mp3")
    report_path=destination/(prefix+"_处理记录.json")
    created=[]
    try:
        with tempfile.TemporaryDirectory(prefix=".processing-",dir=destination) as tmp:
            scratch=Path(tmp)
            if mode=="high":
                mono=scratch/"mono.f32"
                progress(5,"分析原声")
                checked_run([tool("ffmpeg"),"-v","error","-y","-i",str(source),"-map","0:a:0","-vn","-ac","1","-ar","16000","-f","f32le",str(mono)],cancel,timeout)
                regions=pitch_regions(mono,scratch,progress,cancel)
                if not regions:
                    raise NoTargetFound("没有检测到明显的高音角色片段。可切换「整段柔化」后重试。")
            else:
                regions=[[0,info["duration"]]]
            raw,mixed=scratch/"source.f32",scratch/"mixed.f32"
            progress(53,"准备音频")
            checked_run([tool("ffmpeg"),"-v","error","-y","-i",str(source),"-map","0:a:0","-vn","-ac",str(channels),"-ar",str(sr),"-f","f32le",str(raw)],cancel,timeout)
            treatment = {}
            samples=soften_blocks(raw,mixed,regions,sr,channels,strength,mode,progress,cancel,treatment)
            duration=samples/sr
            progress(83,"导出柔化版")
            codec={"mp3":["-c:a","libmp3lame","-q:a","2","-id3v2_version","3"],"wav":["-c:a","pcm_s24le"],"flac":["-c:a","flac","-compression_level","5"]}[fmt]
            limiter="alimiter=limit=0.98:level=false:attack=3:release=60:latency=true"
            checked_run([tool("ffmpeg"),"-v","error","-y","-f","f32le","-ar",str(sr),"-ac",str(channels),"-i",str(mixed),"-af",limiter,*codec,str(partial)],cancel,timeout)
            created.append(partial)
            check_cancel(cancel)
            verified=probe(partial)
            if abs(verified["duration"]-duration)>.16 or verified["channels"]!=channels:
                raise ProcessingError("导出校验失败，请重新尝试。")
            progress(93,"制作对比试听")
            target=max(regions,key=lambda span: span[1]-span[0]) if mode=="high" else [min(10.,duration*.1),0]
            preview_start=max(0,min(target[0]-2,max(0,duration-30)))
            preview_duration=min(30.,duration-preview_start)
            levels = [preview_rms(path, preview_start, preview_duration, sr, channels) for path in (raw, mixed)]
            quietest = min(levels)
            preview_gains = [min(1., quietest/level) if level > 1e-10 else 1. for level in levels]
            for original,path in [(True,original_preview),(False,softened_preview)]:
                args=["-f","f32le","-ar",str(sr),"-ac",str(channels),"-ss",str(preview_start),"-i",str(raw if original else mixed)]
                created.append(path)
                gain = preview_gains[0 if original else 1]
                checked_run([tool("ffmpeg"),"-v","error","-y",*args,"-t",str(preview_duration),"-map","0:a:0","-vn","-ac",str(channels),"-ar","48000","-af",f"volume={gain:.9f},{limiter}","-c:a","libmp3lame","-q:a","3",str(path)],cancel,timeout)
            check_cancel(cancel)
            report={"version":VERSION,"source_name":display_name,"options":{"mode":mode,"strength":strength,"format":fmt},"duration_seconds":duration,"sample_rate":sr,"channels":channels,"region_count":len(regions),"affected_seconds":sum(b-a for a,b in regions),"regions":regions,"output":str(output),"output_sha256":file_hash(partial),"preview_start":preview_start,"preview_duration":preview_duration,"treatment":treatment,"preview_matching":{"method":"RMS attenuation to the quieter preview; no boost; same limiter","original_gain_db":round(20*math.log10(max(preview_gains[0],1e-10)),3),"softened_gain_db":round(20*math.log10(max(preview_gains[1],1e-10)),3)},"verification":{"duration":True,"channels":True,"method":"Linked dynamic spectral attenuation, soft knee and attack/release; automatic pitch-based targeting. No speaker isolation or synthesis.","listening_review":False}}
            created.append(report_path)
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            partial.replace(output)
            created.append(output)
            progress(100,"处理完成")
            return {**report,"export_path":str(output),"original_preview":str(original_preview),"softened_preview":str(softened_preview),"report_path":str(report_path)}
    except BaseException:
        # Remove only files created by this particular run, never the input.
        for path in [partial,*created]:
            path.unlink(missing_ok=True)
        raise
