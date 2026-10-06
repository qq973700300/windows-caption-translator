# -*- coding: utf-8 -*-
"""断句测试：把现有语音重复 4 遍、句间插 0.7s 停顿（可叠加背景音乐），
播放后检查字幕是否"一句一条"，而不是连成一串"""
import os
import sys
import time
import wave

import numpy as np
import soundcard as sc

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WAV = os.path.join(HERE, "test_speech.wav")   # 一句英文："The weather is very nice today..."
REPEAT = 4
GAP = 0.7
BGM_LEVEL = float(os.environ.get("TEST_BGM", "0.0"))   # 背景音乐电平，0 = 不加

with wave.open(WAV, "rb") as w:
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
print(f"单句语音 {len(x)/sr:.1f}s x{REPEAT} 遍, 句间停顿 {GAP}s, BGM={BGM_LEVEL}")

gap = np.zeros(int(GAP * sr), dtype=np.float32)
chunks = [np.zeros(int(0.8 * sr), dtype=np.float32)]
for i in range(REPEAT):
    chunks.append(x)
    chunks.append(gap)
audio = np.concatenate(chunks)
if BGM_LEVEL > 0:
    t = np.arange(len(audio)) / sr
    b = (0.35 * np.sin(2 * np.pi * 220 * t) + 0.25 * np.sin(2 * np.pi * 277 * t)
         + 0.15 * np.sin(2 * np.pi * 330 * t)) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * t))
    audio = np.clip(audio + (b / (np.abs(b).max() + 1e-9)) * BGM_LEVEL, -1, 1)
audio = audio.astype(np.float32)
total = len(audio) / sr

import main as M  # noqa: E402

app = M.App()
app.dev_cb.set(M.DEVICES_INV.get(os.environ.get("TEST_DEVICE", "cpu"), "自动（优先GPU）"))
app.preview_var.set(os.environ.get("TEST_PREVIEW", "1") == "1")
app.model_cb.set(M.MODELS_INV.get(os.environ.get("TEST_MODEL", "base"), "base (快, 延迟约0.6s)"))
app.update()
app.start()
app.update()

t0 = time.time()
events = []
last = ""
threading_ok = False


def _play():
    sc.default_speaker().play(audio, samplerate=sr)


import threading  # noqa: E402
threading.Thread(target=_play, daemon=True).start()
print("playing, watching subtitles...\n")

while time.time() - t0 < total + 30:
    app.update()
    if app.overlay:
        txt = app.overlay.src_label.cget("text")
        if txt and txt != last:
            partial = txt.rstrip().endswith("…")
            dst = app.overlay.dst_label.cget("text")
            events.append((time.time() - t0, partial, txt.replace("  …", ""), dst))
            print(f"  +{time.time()-t0:5.2f}s [{'实时' if partial else '成句'}] "
                  f"{txt.replace('  …','')[:50]} -> {dst[:38]}", flush=True)
            last = txt
    time.sleep(0.05)

app.stop()
print("=" * 62)
finals = [e for e in events if not e[1]]
print(f"成句字幕 {len(finals)} 条 / 共 {len(events)} 次更新（期望成句 >= {REPEAT}）")
for e in finals:
    print(f"   +{e[0]:5.2f}s  {e[2][:60]}")
