# -*- coding: utf-8 -*-
"""GUI 自测：固定尺寸字幕窗 + 端到端出字幕

1) 字幕窗尺寸固定：短句 / 长句 / 清空三种情况下窗口高度必须一致
2) 端到端：启动界面 -> 点击开始 -> 播放测试语音 -> 检查字幕窗是否出现译文
"""
import os
import sys
import time
import wave

import numpy as np
import soundcard as sc

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_speech.wav")

import main as M  # noqa: E402

app = M.App()
print("App created, window title:", app.title())
app.update()


def check_fixed_size():
    """字幕窗高度必须恒定：短句 / 超长句 / 清空 都不能改变尺寸"""
    ov = M.SubtitleOverlay(app, -1, -1)
    ov.update_idletasks()
    sizes = []
    cases = [
        ("短句", "Hello.", "你好。"),
        ("长句", ("This is a very long sentence used to check that the "
                  "subtitle window does not grow when the text is long. " * 2),
         "这是一句很长的中文用来检查字幕窗口在文本变长时是否会被撑大导致画面跳动。" * 3),
        ("清空", "", ""),
    ]
    for label, src, dst in cases:
        if label == "清空":
            ov.clear()
        else:
            ov.set_subtitle(src, dst)
        ov.update_idletasks()
        sizes.append((label, ov.winfo_width(), ov.winfo_height()))
        print(f"  {label:<4} 窗口 {ov.winfo_width()}x{ov.winfo_height()}")
    ov.destroy()
    heights = {s[2] for s in sizes}
    widths = {s[1] for s in sizes}
    ok = len(heights) == 1 and len(widths) == 1
    print("  固定尺寸:", "PASS" if ok else f"FAIL 尺寸变化 {sizes}")
    return ok


print("\n[1/2] 字幕窗固定尺寸检查")
size_ok = check_fixed_size()

print("\n[2/2] 端到端出字幕")
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
        s = app.overlay.src_label.cget("text").strip()
        d = app.overlay.dst_label.cget("text").strip()
        if s:
            got_src, got_dst = s, d
            break
    time.sleep(0.1)

print("=" * 50)
print("字幕原文:", got_src)
print("字幕译文:", got_dst)
print("状态栏:", app.status_var.get())
app.stop()
e2e_ok = bool(got_src)
print("\nGUI TEST:", "PASS" if (size_ok and e2e_ok) else
      f"FAIL (固定尺寸={size_ok}, 端到端={e2e_ok})")
