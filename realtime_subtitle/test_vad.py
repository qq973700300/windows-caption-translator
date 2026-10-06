# -*- coding: utf-8 -*-
"""离线 VAD 断句测试：不依赖播放，直接把音频喂给切句状态机
验证在有/无背景音乐时，能否把"逐句说话"正确切成一段一段"""
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capture import AudioSegmenter, CAPTURE_SR, BLOCK_FRAMES  # noqa: E402

SR = CAPTURE_SR
BLOCK = BLOCK_FRAMES            # 4800 frames = 100ms


def speech_burst(dur, sr=SR, amp=0.25):
    """模拟语音：带音节起伏的宽带噪声"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    env = (0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * t))
    x = np.random.default_rng(0).standard_normal(n)
    # 低通一点更像人声
    b = np.ones(24) / 24
    x = np.convolve(x, b, mode="same")
    return (x / (np.abs(x).max() + 1e-9) * env * amp).astype(np.float32)


def bgm(dur, sr=SR, level=0.02):
    """模拟背景音乐：持续和弦 + 缓慢起伏"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    s = (0.35 * np.sin(2 * np.pi * 220 * t)
         + 0.25 * np.sin(2 * np.pi * 277 * t)
         + 0.15 * np.sin(2 * np.pi * 330 * t))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * t)
    return (s / (np.abs(s).max() + 1e-9) * env * level).astype(np.float32)


def build_audio(bgm_level=0.0, gap=0.6):
    """5 句话，句间 gap 秒停顿"""
    durs = [1.4, 1.6, 1.2, 1.5, 1.3]
    chunks = []
    for i, d in enumerate(durs):
        chunks.append(speech_burst(d))
        if i < len(durs) - 1:
            chunks.append(np.zeros(int(gap * SR), dtype=np.float32))
    audio = np.concatenate(chunks)
    audio = np.concatenate([np.zeros(SR, dtype=np.float32), audio,
                            np.zeros(SR, dtype=np.float32)])
    if bgm_level > 0:
        audio = np.clip(audio + bgm(len(audio) / SR, level=bgm_level), -1, 1)
    return audio.astype(np.float32)


def run(audio, sensitivity=1.0, preview=False, label=""):
    seg = AudioSegmenter(__import__("queue").Queue(), sensitivity=sensitivity,
                         preview=preview)
    flushes = []
    seg.on_flush = lambda pcm, partial: flushes.append(
        (len(pcm) / 16000.0, partial, float(np.abs(pcm).max())))
    nblk = len(audio) // BLOCK
    for i in range(nblk):
        seg.feed_block(audio[i * BLOCK:(i + 1) * BLOCK])
    finals = [f for f in flushes if not f[1]]
    durs = [f"{f[0]:.1f}s" for f in finals]
    print(f"{label:<28} 成句段数={len(finals):<3} 时长={durs}")
    return len(finals)


if __name__ == "__main__":
    print(f"音频设计：5 句话（1.2~1.6s），句间 0.6s 停顿 -> 期望切出 5 段\n")
    print("--- 无背景音乐 ---")
    run(build_audio(0.0), label="安静环境")
    print("\n--- 有背景音乐（固定阈值时代最容易失效的场景）---")
    for lv in (0.01, 0.02, 0.04, 0.08):
        run(build_audio(lv), label=f"BGM 电平 {lv}")
    print("\n--- 小声说话 + 背景音乐 ---")
    a = build_audio(0.02)
    run(a * 0.5, label="语音音量 x0.5")
    run(a * 0.5, sensitivity=2.0, label="语音x0.5 + 灵敏度2.0")
