# -*- coding: utf-8 -*-
"""GPU vs CPU 识别速度对比"""
import os
import sys
import time
import wave

import numpy as np

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_speech.wav")
with wave.open(WAV, "rb") as w:
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
n_out = int(len(x) * 16000 / sr)
pcm = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x).astype(np.float32)
dur = len(pcm) / 16000
print(f"audio {dur:.1f}s @16k\n")

from faster_whisper import WhisperModel
from transcribe import detect_device

print("detect_device(auto) ->", detect_device("auto"))
print("-" * 62)

for model_size, dev, compute in [
        ("small", "cuda", "float16"),
        ("small", "cpu", "int8"),
        ("base", "cuda", "float16"),
        ("medium", "cuda", "float16")]:
    try:
        t_load = time.time()
        m = WhisperModel(model_size, device=dev, compute_type=compute, cpu_threads=16)
        load = time.time() - t_load
        m.transcribe(pcm[:8000], beam_size=1)          # 预热
        t0 = time.time()
        segs, info = m.transcribe(pcm, beam_size=1, temperature=0.0,
                                  condition_on_previous_text=False)
        text = "".join(s.text for s in segs).strip()
        dt = time.time() - t0
        print(f"{model_size:6s} {dev:4s}/{compute:8s} 加载{load:5.1f}s "
              f"识别{dt:5.2f}s RTF={dt/dur:5.2f} | {text[:38]}")
    except Exception as e:
        print(f"{model_size:6s} {dev:4s}/{compute:8s} FAIL: {str(e)[:70]}")
