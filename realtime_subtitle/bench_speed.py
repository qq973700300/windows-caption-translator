# -*- coding: utf-8 -*-
"""识别速度基准测试：对比各模型/线程数下识别同一段音频的耗时"""
import os
import sys
import time
import wave

import numpy as np

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # 必须在使用前设置
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_speech.wav")

with wave.open(WAV, "rb") as w:
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
# 重采样到 16k（线性插值）
n_out = int(len(x) * 16000 / sr)
pcm = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x).astype(np.float32)
print(f"audio: {len(pcm)/16000:.1f}s @16k, cpu cores: {os.cpu_count()}")

from faster_whisper import WhisperModel

for model_size in ["base", "small"]:
    for threads in [0, 8, 16]:
        kw = {"device": "cpu", "compute_type": "int8"}
        if threads:
            kw["cpu_threads"] = threads
        m = WhisperModel(model_size, **kw)
        # 预热
        m.transcribe(pcm[:8000], beam_size=1)
        t0 = time.time()
        segs, info = m.transcribe(pcm, beam_size=1, temperature=0.0,
                                  condition_on_previous_text=False)
        text = "".join(s.text for s in segs).strip()
        dt = time.time() - t0
        print(f"{model_size:6s} threads={threads or 'default':>7}  {dt:6.2f}s  "
              f"RTF={dt/(len(pcm)/16000):.2f}  | {text[:45]}")
