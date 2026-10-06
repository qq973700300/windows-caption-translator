# -*- coding: utf-8 -*-
"""离线 Silero VAD 断句测试：不依赖声卡，直接把音频喂给切句状态机

关注点：一句话能不能被干净地切出来（段数、时长），
尤其是背景音乐 / 小声说话这两个最容易失效的场景。

用法：
  python test_vad.py                  合成噪声爆发（确定性）
  python test_vad.py real             真实 TTS 语音（更接近实际场景）
"""
import os
import queue
import sys
import wave

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from capture import AudioSegmenter, CAPTURE_SR, BLOCK_FRAMES  # noqa: E402

SR = CAPTURE_SR
BLOCK = BLOCK_FRAMES            # 4800 frames = 100ms


def speech_burst(dur, sr=SR, amp=0.25):
    """模拟语音：带音节起伏的低通噪声（确定性，便于回归）"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    env = (0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t)) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * t))
    x = np.random.default_rng(0).standard_normal(n)
    b = np.ones(24) / 24
    x = np.convolve(x, b, mode="same")
    return (x / (np.abs(x).max() + 1e-9) * env * amp).astype(np.float32)


def real_speech(sr=SR):
    """真实 TTS 语音（test_speech.wav），重采样到采集采样率"""
    with wave.open(os.path.join(HERE, "test_speech.wav"), "rb") as w:
        s0 = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(len(x) * sr / s0)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def bgm(dur, sr=SR, level=0.02):
    """模拟背景音乐：持续和弦 + 缓慢起伏"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    s = (0.35 * np.sin(2 * np.pi * 220 * t)
         + 0.25 * np.sin(2 * np.pi * 277 * t)
         + 0.15 * np.sin(2 * np.pi * 330 * t))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * t)
    return (s / (np.abs(s).max() + 1e-9) * env * level).astype(np.float32)


def build_audio(bgm_level=0.0, gap=0.6, source="synth", amp=0.25):
    """多句话，句间 gap 秒停顿"""
    if source == "real":
        sp = real_speech()
        piece = int(1.1 * SR)          # 每句约 1.1s
        chunks = []
        for i in range(4):
            s = sp[i * piece:(i + 1) * piece]
            if len(s) < piece // 2:
                break
            chunks.append(s)
            chunks.append(np.zeros(int(gap * SR), dtype=np.float32))
        audio = np.concatenate(chunks)
    else:
        durs = [1.4, 1.6, 1.2, 1.5, 1.3]
        chunks = []
        for i, d in enumerate(durs):
            chunks.append(speech_burst(d, amp=amp))
            if i < len(durs) - 1:
                chunks.append(np.zeros(int(gap * SR), dtype=np.float32))
        audio = np.concatenate(chunks)
    audio = np.concatenate([np.zeros(SR, dtype=np.float32), audio,
                            np.zeros(SR, dtype=np.float32)])
    if bgm_level > 0:
        audio = np.clip(audio + bgm(len(audio) / SR, level=bgm_level), -1, 1)
    return audio.astype(np.float32)


def segments_of(audio, sensitivity=1.0):
    """跑一遍切句器，返回 [(时长秒, 是否定稿)]"""
    q = queue.Queue(maxsize=4096)
    seg = AudioSegmenter(q, sensitivity=sensitivity)
    # audio 块不触发回调，段时长要用块索引推算：一个块 = 100ms
    out = []
    state = {"i": 0, "start": None}

    def on_event(kind):
        if kind == "start":
            state["start"] = state["i"]
        elif kind in ("end", "cancel"):
            if state["start"] is not None:
                out.append(((state["i"] - state["start"]) * 0.1, kind == "end"))
            state["start"] = None

    seg.on_event = on_event
    nblk = len(audio) // BLOCK
    for i in range(nblk):
        state["i"] = i
        seg.feed_block(audio[i * BLOCK:(i + 1) * BLOCK])
        while True:                      # 同步取空，避免大音频把队列撑爆
            try:
                q.get_nowait()
            except queue.Empty:
                break
    return out


def run(audio, expect, title, sensitivity=1.0):
    segs = segments_of(audio, sensitivity)
    finals = [s for s in segs if s[1]]
    durs = [f"{d:.1f}s" for d, _ in finals]
    ok = "OK" if len(finals) == expect else f"偏差{abs(len(finals) - expect)}"
    print(f"  {title:<28} 定稿段数={len(finals):<3} 期望={expect} ({ok}) 时长={durs}")
    return len(finals)


if __name__ == "__main__":
    src = "real" if len(sys.argv) > 1 and sys.argv[1] == "real" else "synth"
    expect = 4 if src == "real" else 5
    print(f"数据源：{'真实 TTS 语音' if src == 'real' else '合成噪声爆发'}"
          f" -> 期望切出 {expect} 段\n")

    print("--- 无背景音乐 ---")
    run(build_audio(0.0, source=src), expect, "静音底噪")
    print("--- 有背景音乐（最容易切错的场景）---")
    for lv in (0.01, 0.02, 0.04, 0.08):
        run(build_audio(lv, source=src), expect, f"BGM 电平 {lv}")
    print("--- 小声说话 + 背景音乐 ---")
    a = build_audio(0.02, source=src)
    run(a * 0.5, expect, "语音音量 x0.5")
    run(a * 0.3, expect, "语音音量 x0.3")
