# -*- coding: utf-8 -*-
"""生成多句测试音频：5 个短句，句间 0.6s 停顿；可选叠加背景音乐"""
import os
import wave

import numpy as np

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SR = 22050

SENTENCES = [
    "Hello everyone, welcome back to my channel.",
    "Today we are going to build a small robot.",
    "First, you need a motor and a battery.",
    "Then connect the wires to the controller.",
    "Finally, turn on the power and watch it move.",
]
GAP = 0.6  # 句间停顿（秒）


def tts_parts():
    """逐句合成，返回 [(pcm_float32, sr)]"""
    import pyttsx3
    import tempfile
    eng = pyttsx3.init()
    eng.setProperty("rate", 170)
    parts = []
    tmp = os.path.join(tempfile.gettempdir(), "_tts_part.wav")
    for s in SENTENCES:
        eng.save_to_file(s, tmp)
        eng.runAndWait()
        with wave.open(tmp, "rb") as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        parts.append((x.astype(np.float32) / 32768.0, sr))
        os.remove(tmp)
    return parts


def make_bgm(n, sr):
    """模拟背景音乐：低频和弦 + 缓慢起伏"""
    t = np.arange(n) / sr
    sig = (0.35 * np.sin(2 * np.pi * 220 * t)
           + 0.25 * np.sin(2 * np.pi * 277 * t)
           + 0.15 * np.sin(2 * np.pi * 330 * t))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * t)   # 4 秒一个起伏
    return (sig * env).astype(np.float32)


def build(with_bgm=False, out=None, bgm_level=0.02):
    parts = tts_parts()
    chunks = []
    silence = np.zeros(int(GAP * SR), dtype=np.float32)
    for i, (x, sr) in enumerate(parts):
        if sr != SR:
            idx = np.linspace(0, len(x) - 1, int(len(x) * SR / sr))
            x = np.interp(idx, np.arange(len(x)), x).astype(np.float32)
        chunks.append(x)
        if i < len(parts) - 1:
            chunks.append(silence)
    audio = np.concatenate(chunks)
    if with_bgm:
        bgm = make_bgm(len(audio), SR) * (bgm_level / 0.02)
        audio = np.clip(audio + bgm, -1.0, 1.0)
    # 前后各留 1s 静音
    pad = np.zeros(SR, dtype=np.float32)
    audio = np.concatenate([pad, audio, pad])
    pcm16 = (audio * 32767).astype(np.int16)
    out = out or os.path.join(OUT_DIR, "test_sentences.wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    print(f"{os.path.basename(out)}: {len(audio)/SR:.1f}s  bgm={with_bgm}")
    return out


if __name__ == "__main__":
    build(False, os.path.join(OUT_DIR, "test_sentences.wav"))
    build(True, os.path.join(OUT_DIR, "test_sentences_bgm.wav"))
