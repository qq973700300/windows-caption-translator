# -*- coding: utf-8 -*-
"""端到端自检：TTS 语音 -> 扬声器播放 -> loopback 采集 -> whisper 识别 -> 翻译"""
import queue
import sys
import time
import wave

import numpy as np
import soundcard as sc

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:/翻译软件/realtime_subtitle")

WAV_PATH = sys.argv[1] if len(sys.argv) > 1 else r"D:/翻译软件/realtime_subtitle/test_speech.wav"

# 读取 TTS 生成的 wav
with wave.open(WAV_PATH, "rb") as w:
    sr = w.getframerate()
    raw = w.readframes(w.getnframes())
data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
if w.getnchannels() == 2:
    data = data.reshape(-1, 2)
print(f"wav: {sr}Hz, {len(data)/sr:.1f}s")

# 1) 启动 loopback 采集
from capture import AudioSegmenter
from transcribe import transcribe
from translate import Translator

q = queue.Queue(maxsize=8)
seg = AudioSegmenter(q, sensitivity=1.0)
seg.start()

# 2) 扬声器播放
spk = sc.default_speaker()
spk.play(data, samplerate=sr)
print("played. collecting segments...")

# 3) 收集片段并识别翻译
tr = Translator(engine="auto")
deadline = time.time() + 15
got = []
while time.time() < deadline and len(got) < 3:
    try:
        pcm = q.get(timeout=1)
    except queue.Empty:
        continue
    text, lang = transcribe(pcm, model_size="small", language="en")
    if text:
        zh = tr.translate(text, src_lang=lang)
        got.append((text, zh))
        print(f"[{len(pcm)/16000:.1f}s {lang}] {text}")
        print(f"   -> {zh}")

seg.stop()
print("E2E RESULT:", "PASS" if got else "FAIL")
