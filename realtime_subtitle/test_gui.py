# -*- coding: utf-8 -*-
"""GUI 自测：启动界面 -> 点击开始 -> 播放测试语音 -> 检查字幕窗是否出现译文"""
import os
import sys
import time
import wave

import numpy as np
import soundcard as sc

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_speech.wav")

import main as M

app = M.App()
print("App created, window title:", app.title())
app.update()

# 测试时可强制指定设备：compute_device = "cpu" / "cuda" / "auto"
app.cfg["compute_device"] = os.environ.get("TEST_DEVICE", "cpu")
app.start()          # 等价于点击「开始翻译」
app.update()
print("started, overlay:", bool(app.overlay))

with wave.open(WAV, "rb") as w:
    sr = w.getframerate()
    data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0

sc.default_speaker().play(data, samplerate=sr)
print("played test speech, waiting for subtitle...")

got_src = got_dst = None
for _ in range(300):     # 最多等 30 秒
    app.update()
    if app.overlay:
        s = app.overlay.src_label.cget("text")
        d = app.overlay.dst_label.cget("text")
        if s:
            got_src, got_dst = s, d
            break
    time.sleep(0.1)

print("=" * 50)
print("字幕原文:", got_src)
print("字幕译文:", got_dst)
print("状态栏:", app.status_var.get())
print("实际计算设备:", getattr(app, "_compute_dev", None))
app.stop()
print("GUI TEST:", "PASS" if got_src else "FAIL")
