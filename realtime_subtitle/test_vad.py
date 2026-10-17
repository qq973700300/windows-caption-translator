# -*- coding: utf-8 -*-
"""Silero VAD + 采集监测的离线测试（不依赖声卡）

注意：切句已经交给 sherpa-onnx 的 recognizer 端点检测了（见 asr_stream.py）。
VAD 现在只负责两件事：UI 的人声概率、以及"连续多久没人声"这个监测值
（长句在自然停顿处收尾、换片时重置翻译上下文都要用它）。

用法：
  python test_vad.py
"""
import os
import queue
import sys
import wave

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from capture import AudioCapture, CAPTURE_SR, BLOCK_FRAMES  # noqa: E402

SR = CAPTURE_SR
BLOCK = BLOCK_FRAMES


def real_speech(sr=SR):
    with wave.open(os.path.join(HERE, "test_speech.wav"), "rb") as w:
        s0 = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(len(x) * sr / s0)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def bgm(dur, level=0.02):
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = (0.35 * np.sin(2 * np.pi * 220 * t) + 0.25 * np.sin(2 * np.pi * 277 * t)
         + 0.15 * np.sin(2 * np.pi * 330 * t))
    return (s / (np.abs(s).max() + 1e-9) * level).astype(np.float32)


def feed(audio):
    """跑一遍采集层（只推到队列，不跑识别），返回捕手"""
    q = queue.Queue(maxsize=4096)
    cap = AudioCapture(q, use_vad=True)
    probs, trail = [], []
    for i in range(len(audio) // BLOCK):
        cap.feed_block(audio[i * BLOCK:(i + 1) * BLOCK])
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
        probs.append(cap.speech_prob)
        trail.append(cap.trailing_silence)
    return cap, probs, trail


def case_silence():
    """纯静音：人声概率应当很低，且 trailing_silence 持续增长"""
    audio = np.zeros(SR * 2, dtype=np.float32)
    cap, probs, trail = feed(audio)
    ok = np.mean(probs) < 0.2 and trail[-1] >= 1.8
    print(f"  静音 2s -> 平均概率 {np.mean(probs):.3f}, trailing {trail[-1]:.1f}s")
    return ok


def case_speech():
    """真实语音：概率应当高，trailing_silence 归零"""
    audio = real_speech()
    cap, probs, trail = feed(audio)
    ok = np.max(probs) > 0.5 and min(trail) < 0.5
    print(f"  TTS 语音 {len(audio)/SR:.1f}s -> 最大概率 {np.max(probs):.3f}, "
          f"最小 trailing {min(trail):.2f}s")
    return ok


def case_bgm_only():
    """只有背景音乐、没人说话：概率不该被音乐骗成"有人声" """
    cap, probs, _ = feed(bgm(3.0, level=0.05))
    ok = np.mean(probs) < 0.4
    print(f"  纯 BGM 3s -> 平均概率 {np.mean(probs):.3f}")
    return ok


def case_silent_hint():
    """完全没有声音 -> silent_seconds 累加（UI 据此提示"设备选错了"）"""
    cap, _, _ = feed(np.zeros(SR * 6, dtype=np.float32))
    ok = cap.silent_seconds >= 5.5
    print(f"  连续静音 6s -> silent_seconds {cap.silent_seconds:.1f}s（>=5 会提示）")
    return ok


def main():
    print("Silero VAD / 采集监测")
    print("\n[1/4] 纯静音")
    a = case_silence()
    print("\n[2/4] 真实语音")
    b = case_speech()
    print("\n[3/4] 纯背景音乐")
    c = case_bgm_only()
    print("\n[4/4] 没采到声音的提示")
    d = case_silent_hint()
    print("\n" + "=" * 52)
    ok = all([a, b, c, d])
    print("VAD TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
