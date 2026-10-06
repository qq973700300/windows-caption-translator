# -*- coding: utf-8 -*-
"""离线整管道断句测试：把音频直接喂给 VAD（不经声卡），
打印每个切出片段的时长与识别文本 —— 用来判断"断句"是否干净"""
import os
import queue
import sys
import wave

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from capture import AudioSegmenter, CAPTURE_SR, BLOCK_FRAMES  # noqa: E402

WAV = os.path.join(HERE, os.environ.get("TEST_WAV", "test_speech.wav"))
REPEAT = int(os.environ.get("TEST_REPEAT", "4"))
GAP = float(os.environ.get("TEST_GAP", "0.7"))
BGM = float(os.environ.get("TEST_BGM", "0.0"))
PREVIEW = os.environ.get("TEST_PREVIEW", "0") == "1"
DEVICE = os.environ.get("TEST_DEVICE", "cpu")
MODEL = os.environ.get("TEST_MODEL", "base")

with wave.open(WAV, "rb") as w:
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

gap = np.zeros(int(GAP * sr), dtype=np.float32)
chunks = [np.zeros(int(0.8 * sr), dtype=np.float32)]
for i in range(REPEAT):
    chunks.append(x)
    chunks.append(gap)
audio = np.concatenate(chunks)
if BGM > 0:
    t = np.arange(len(audio)) / sr
    b = (0.35 * np.sin(2 * np.pi * 220 * t) + 0.25 * np.sin(2 * np.pi * 277 * t)
         + 0.15 * np.sin(2 * np.pi * 330 * t)) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * t))
    audio = np.clip(audio + (b / (np.abs(b).max() + 1e-9)) * BGM, -1, 1)
audio = audio.astype(np.float32)

# 重采样到 48k（采集端采样率）
n48 = int(len(audio) * CAPTURE_SR / sr)
audio48 = np.interp(np.linspace(0, len(audio) - 1, n48), np.arange(len(audio)), audio).astype(np.float32)

seg = AudioSegmenter(queue.Queue(), sensitivity=1.0, preview=PREVIEW)
flushed = []
seg.on_flush = lambda pcm, partial: flushed.append((pcm, partial))

for i in range(len(audio48) // BLOCK_FRAMES):
    seg.feed_block(audio48[i * BLOCK_FRAMES:(i + 1) * BLOCK_FRAMES])

from transcribe import transcribe, transcribe_pieces  # noqa: E402

print(f"音频 {len(audio)/sr:.1f}s = {REPEAT} 句话(句间{GAP}s) + BGM {BGM} | preview={PREVIEW}\n")
finals = [f for f in flushed if not f[1]]
print(f"切出成句片段 {len(finals)} 段（期望 {REPEAT} 段）:")
for i, (pcm, _) in enumerate(finals, 1):
    text, lang, prob = transcribe(pcm, model_size=MODEL, language="en", device=DEVICE)
    print(f"  #{i} 时长{len(pcm)/16000:4.1f}s  [{lang}] {text}")
print("\n预览片段（partial）:", len(flushed) - len(finals), "段")
print("=" * 60)
print("拆分后实际显示的字幕：")
for i, (pcm, _) in enumerate(finals, 1):
    pieces, lang, prob = transcribe_pieces(pcm, model_size=MODEL, language="en",
                                           device=DEVICE)
    for s in pieces:
        print(f"  - {s}")
