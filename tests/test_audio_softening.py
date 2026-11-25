"""Behavior checks on generated signals, without modifying user recordings."""
from pathlib import Path
import subprocess
import sys
import uuid
import threading
import unittest
import wave
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import audio_softening as engine

def write_wav(path,audio,sr):
    if audio.ndim==1:
        audio=audio[:,None]
    with wave.open(str(path),"wb") as fp:
        fp.setnchannels(audio.shape[1]);fp.setsampwidth(2);fp.setframerate(sr)
        fp.writeframes((np.clip(audio,-1,1)*32767).astype("<i2").tobytes())

def decode(path,sr,channels):
    p=subprocess.run([engine.tool("ffmpeg"),"-v","error","-i",str(path),"-ar",str(sr),"-ac",str(channels),"-f","f32le","-"],capture_output=True,check=True,**engine.HIDDEN)
    return np.frombuffer(p.stdout,dtype="<f4").reshape(-1,channels)

class EngineBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT / "test-artifacts" / ("softening_" + uuid.uuid4().hex[:8])
        self.root.mkdir(parents=True)
    def test_high_voice_harshness_reduced_but_body_and_low_voice_preserved(self):
        sr=48000;t=np.arange(sr*6)/sr
        high=(t>=2)&(t<4)
        f=np.where(high,650,180)
        # Continuous phase avoids generating an artificial switch click.
        phase=np.cumsum(2*np.pi*f/sr)
        audio=.28*np.sin(phase)+.06*np.sin(2*np.pi*3200*t)
        source=self.root/"speaker.wav";write_wav(source,np.c_[audio,audio*.8],sr)
        before=engine.file_hash(source)
        result=engine.process(source,"speaker.wav",self.root/"out",{"mode":"high","strength":55,"format":"wav"})
        raw=decode(source,sr,2);out=decode(result["export_path"],sr,2)
        self.assertEqual(raw.shape,out.shape)
        low_slice=slice(sr//2,int(sr*1.5))
        self.assertLess(float(np.max(np.abs(raw[low_slice]-out[low_slice]))),2e-5)
        high_slice=slice(int(sr*2.5),int(sr*3.5))
        change=20*np.log10(np.sqrt(np.mean(out[high_slice]**2))/np.sqrt(np.mean(raw[high_slice]**2)))
        self.assertGreater(change, -1)  # A softer voice must not simply be quieter.
        def magnitude(data, hz):
            part = data[high_slice, 0]
            return abs(np.sum(part*np.exp(-2j*np.pi*hz*np.arange(len(part))/sr)))
        harsh_change = 20*np.log10(magnitude(out,3200)/magnitude(raw,3200))
        body_change = 20*np.log10(magnitude(out,650)/magnitude(raw,650))
        self.assertLess(harsh_change,-2)
        self.assertAlmostEqual(body_change,0,delta=.15)
        self.assertGreater(result["region_count"],0)
        self.assertAlmostEqual(result["duration_seconds"],6,places=4)
        self.assertEqual(before,engine.file_hash(source))
        self.assertIn("preview_matching",result)
    def test_whole_mode_handles_mono_44100_and_preserves_duration(self):
        sr=44100;t=np.arange(sr)/sr
        source=self.root/"mono.wav";write_wav(source,.2*np.sin(2*np.pi*200*t)+.1*np.sin(2*np.pi*3000*t),sr)
        result=engine.process(source,"mono.wav",self.root/"out",{"mode":"whole","strength":80,"format":"flac"})
        info=engine.probe(result["export_path"])
        self.assertEqual(info["channels"],1)
        self.assertEqual(info["sample_rate"],sr)
        self.assertAlmostEqual(info["duration"],1,places=3)
    def test_silence_does_not_claim_to_find_a_character(self):
        source=self.root/"silence.wav";write_wav(source,np.zeros(16000),16000)
        with self.assertRaises(engine.NoTargetFound):
            engine.process(source,"silence.wav",self.root/"out",{"mode":"high","strength":55,"format":"mp3"})
        self.assertFalse(list((self.root/"out").iterdir()))
    def test_pre_cancel_leaves_source_and_no_output(self):
        source=self.root/"source.wav";write_wav(source,np.zeros(1000),16000)
        event=threading.Event();event.set()
        with self.assertRaises(engine.Cancelled):
            engine.process(source,"source.wav",self.root/"out",{},cancel=event)
        self.assertTrue(source.is_file())
        self.assertFalse(list((self.root/"out").iterdir()))
    def test_bad_input_and_invalid_options_are_clear_errors(self):
        source=self.root/"broken.mp3";source.write_bytes(b"not audio")
        with self.assertRaises(engine.ProcessingError):engine.probe(source)
        with self.assertRaises(engine.ProcessingError):engine.process(source,"broken.mp3",self.root/"out",{"strength":float("nan")})
    def test_cancelling_during_analysis_releases_temporary_files(self):
        source=self.root/"cancel.wav";write_wav(source,np.zeros(16000*8),16000)
        event=threading.Event()
        def progress(percent,stage):
            if percent>=12:event.set()
        with self.assertRaises(engine.Cancelled):
            engine.process(source,"cancel.wav",self.root/"out",{"mode":"high","strength":55,"format":"wav"},progress,event)
        self.assertFalse(list((self.root/"out").iterdir()))
    def test_short_input_does_not_crash_pitch_tracker(self):
        source=self.root/"tiny.wav";write_wav(source,np.zeros(320),16000)
        with self.assertRaises(engine.NoTargetFound):
            engine.process(source,"tiny.wav",self.root/"out",{"mode":"high","strength":55,"format":"mp3"})
    def test_repeat_runs_do_not_overwrite_exports(self):
        source=self.root/"repeat.wav";write_wav(source,np.zeros(8000),16000)
        opts={"mode":"whole","strength":55,"format":"mp3"}
        a=engine.process(source,"repeat.wav",self.root/"out",opts)
        b=engine.process(source,"repeat.wav",self.root/"out",opts)
        self.assertNotEqual(a["export_path"],b["export_path"])
        self.assertTrue(Path(a["export_path"]).exists())
        self.assertTrue(Path(b["export_path"]).exists())

    def test_whole_strength_reduces_harsh_band_and_keeps_silence(self):
        sr=48000
        t=np.arange(sr)/sr
        source=self.root/"bright.wav"
        write_wav(source,.1*np.sin(2*np.pi*2800*t),sr)
        levels=[]
        for strength in (15,55,100):
            result=engine.process(source,source.name,self.root/"out",
                                  {"mode":"whole","strength":strength,"format":"wav"})
            audio=decode(result["export_path"],sr,1)
            levels.append(float(np.sqrt(np.mean(audio[sr//10:]**2))))
        self.assertGreater(levels[0],levels[1])
        self.assertGreater(levels[1],levels[2])
        silent=self.root/"silent.wav"
        write_wav(silent,np.zeros(sr),sr)
        result=engine.process(silent,silent.name,self.root/"out",
                              {"mode":"whole","strength":100,"format":"wav"})
        self.assertEqual(float(np.max(np.abs(decode(result["export_path"],sr,1)))),0)

    def test_video_audio_can_be_softened_without_modifying_video(self):
        source=self.root/"recording.mp4"
        subprocess.run([engine.tool("ffmpeg"),"-v","error","-n","-f","lavfi",
                        "-i","color=black:s=96x64:r=10:d=1","-f","lavfi",
                        "-i","sine=frequency=3000:duration=1","-c:v","libx264",
                        "-c:a","aac","-shortest",str(source)],check=True,**engine.HIDDEN)
        before=engine.file_hash(source)
        result=engine.process(source,source.name,self.root/"out",
                              {"mode":"whole","strength":55,"format":"mp3"})
        self.assertEqual(engine.file_hash(source),before)
        self.assertEqual(Path(result["export_path"]).suffix,".mp3")
        self.assertAlmostEqual(engine.probe(result["export_path"])["duration"],1,delta=.16)

    def test_oversized_input_and_invalid_settings_fail_before_decode(self):
        source=self.root/"source.wav"
        write_wav(source,np.zeros(100),16000)
        for options in ({"mode":"bad"},{"format":"mp4"},{"strength":True},
                        {"strength":14},{"strength":101}):
            with self.subTest(options=options),self.assertRaises(engine.ProcessingError):
                engine.process(source,source.name,self.root/"out",options)
        with patch.object(engine,"MAX_SOURCE_BYTES",1),self.assertRaises(engine.ProcessingError):
            engine.process(source,source.name,self.root/"out",{})


class SpectralBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "test-artifacts" / ("spectral_" + uuid.uuid4().hex[:8])
        self.root.mkdir(parents=True)

    def treat(self, audio, sr, strength=55, mode="whole", regions=None, progress=None, cancel=None):
        if audio.ndim == 1:
            audio = audio[:, None]
        raw = self.root / (uuid.uuid4().hex + ".f32")
        mixed = self.root / (uuid.uuid4().hex + ".f32")
        raw.write_bytes(audio.astype("<f4").tobytes())
        stats = {}
        count = engine.soften_blocks(raw, mixed, regions or [[0, len(audio)/sr]], sr,
                                     audio.shape[1], strength, mode,
                                     progress or (lambda *args: None), cancel or threading.Event(), stats)
        out = np.fromfile(mixed, dtype="<f4").reshape(-1, audio.shape[1])
        self.assertEqual(count, len(audio))
        self.assertEqual(out.shape, audio.shape)
        self.assertTrue(np.isfinite(out).all())
        return out, stats

    def test_overlap_add_reconstructs_short_long_and_unusual_rates(self):
        rng = np.random.default_rng(18)
        for sr, length in ((8000, 1), (16000, 319), (44100, 23451),
                           (48000, 48000*3+17), (96000, 96007)):
            with self.subTest(sr=sr, length=length):
                source = rng.normal(0, .03, (length, 2)).astype("<f4")
                out, _ = self.treat(source, sr, strength=0)
                self.assertLess(float(np.max(np.abs(out-source))), 1e-7)

    def test_bursts_are_softened_but_quiet_detail_and_vocal_body_survive(self):
        sr = 48000
        t = np.arange(sr*3)/sr
        burst = (t >= .8) & (t < 1.1)
        source = .25*np.sin(2*np.pi*300*t)+np.where(burst,.1,.006)*np.sin(2*np.pi*7000*t)
        out, stats = self.treat(source, sr)
        def db(hz, start, end):
            section = slice(round(start*sr), round(end*sr))
            basis = np.exp(-2j*np.pi*hz*t[section])
            return 20*np.log10(abs(np.sum(out[section,0]*basis))/abs(np.sum(source[section]*basis)))
        self.assertLess(db(7000,.9,1.05), -4)
        self.assertAlmostEqual(db(7000,.2,.6), 0, delta=.1)
        self.assertAlmostEqual(db(7000,1.8,2.4), 0, delta=.15)
        self.assertAlmostEqual(db(300,.9,1.05), 0, delta=.05)
        self.assertGreater(stats["rms_change_db"], -.5)

    def test_stereo_link_does_not_move_image_or_cancel_opposite_phase(self):
        sr = 44100
        t = np.arange(sr*2)/sr
        audio = .2*np.sin(2*np.pi*200*t)+.09*np.sin(2*np.pi*3400*t)
        out, stats = self.treat(np.c_[audio, -.65*audio], sr)
        self.assertLess(float(np.max(np.abs(out[:,1]+.65*out[:,0]))), 5e-8)
        self.assertGreater(stats["max_band_reduction_db"], 3)

    def test_target_bypass_is_exact_and_region_fades_do_not_click(self):
        sr = 48000
        t = np.arange(sr*4)/sr
        audio = (.2*np.sin(2*np.pi*300*t)+.06*np.sin(2*np.pi*3200*t)).astype("<f4")
        out, _ = self.treat(audio, sr, mode="high", regions=[[1, 3]])
        np.testing.assert_array_equal(out[:sr,0], audio[:sr])
        np.testing.assert_array_equal(out[3*sr:,0], audio[3*sr:])
        delta = out[:,0]-audio
        for boundary in (sr, 3*sr):
            self.assertLess(float(np.max(np.abs(np.diff(delta[boundary-100:boundary+100])))), .0002)

    def test_non_finite_input_rejected_and_cancel_checked_between_batches(self):
        with self.assertRaises(engine.ProcessingError):
            self.treat(np.array([0., float("nan")]), 48000)
        event = threading.Event()
        with self.assertRaises(engine.Cancelled):
            self.treat(np.zeros(48000*4), 48000, cancel=event,
                       progress=lambda *_: event.set())

if __name__=="__main__":
    unittest.main(verbosity=2)
