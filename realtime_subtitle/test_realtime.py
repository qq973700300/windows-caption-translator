# -*- coding: utf-8 -*-
"""实时性测试：播放一段约 20 秒连续语音，记录字幕出现的时间线"""
import os
import sys
import time
import wave

import numpy as np
import soundcard as sc

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WAV = os.path.join(HERE, "test_long.wav")
TEXT = ("Welcome to this channel. Today we are going to talk about how artificial "
        "intelligence is changing our daily lives, from the way we work to the way "
        "we communicate with each other. Let me show you a few examples that will "
        "probably surprise you, and explain why this technology matters so much "
        "for the future of software engineering.")

# 1) 生成约 20 秒语音
if not os.path.exists(WAV):
    import pyttsx3
    e = pyttsx3.init()
    e.save_to_file(TEXT, WAV)
    e.runAndWait()
    time.sleep(1)

with wave.open(WAV, "rb") as w:
    sr = w.getframerate()
    data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
print(f"speech: {len(data)/sr:.1f}s  (device={os.environ.get('TEST_DEVICE','cpu')})")

import main as M

app = M.App()
# 必须通过 UI 控件设置，否则会被 start() 里的 _save_now() 覆盖
app.dev_cb.set(M.DEVICES_INV.get(os.environ.get("TEST_DEVICE", "cpu"), "自动（优先GPU）"))
app.preview_var.set(os.environ.get("TEST_PREVIEW", "1") == "1")
app.model_cb.set(M.MODELS_INV.get(os.environ.get("TEST_MODEL", "small"), "small (推荐, 延迟约1.6s)"))
app.update()
app.start()
app.update()

import threading
t0 = time.time()
events = []
last = ""


def _play():
    sc.default_speaker().play(data, samplerate=sr)


# 播放必须放后台线程：play() 是阻塞的，否则主线程无法刷新界面、字幕会被压着不显示
threading.Thread(target=_play, daemon=True).start()
print("playing in background, watching subtitles...")

while time.time() - t0 < (len(data) / sr) + 25:
    app.update()
    if app.overlay:
        txt = app.overlay.src_label.cget("text")
        if txt and txt != last:
            partial = txt.rstrip().endswith("…")
            dst = app.overlay.dst_label.cget("text")
            events.append((time.time() - t0, partial, txt.replace("  …", ""), dst))
            print(f"  +{time.time()-t0:5.2f}s  [{'实时' if partial else '成句'}] "
                  f"{txt.replace('  …','')[:52]}  ->  {dst[:40]}", flush=True)
            last = txt
    time.sleep(0.05)

app.stop()
print("=" * 60)
if events:
    print(f"首次字幕出现: {events[0][0]:.2f}s  末次: {events[-1][0]:.2f}s  共 {len(events)} 次更新")
first_partial = next((e for e in events if e[1]), None)
print("首个实时预览:", f"{first_partial[0]:.2f}s" if first_partial else "无")
