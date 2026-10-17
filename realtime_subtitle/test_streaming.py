# -*- coding: utf-8 -*-
"""流式链路离线测试（不依赖声卡）

形态与官方示例一致：音频一条流喂到黑，句末由 recognizer 的端点检测判定，
只 reset 识别状态，不重建缓冲区。

关注三件事：
  1. 句首第一个词完整（旧实现会丢 100ms，首词变成 "wthem"）
  2. 句末不吞词（最后一句要完整收尾）
  3. 长句有上限，不会一句话拖到 20 秒

用法：
  python test_streaming.py                   默认 test_long.wav
  TEST_WAV=test_speech.wav python test_streaming.py
"""
import os
import queue
import sys
import time
import wave

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from capture import AudioCapture, CAPTURE_SR, BLOCK_FRAMES  # noqa: E402
import asr_stream  # noqa: E402

WAV = os.environ.get("TEST_WAV", "test_long.wav")
MAX_SEG = float(os.environ.get("TEST_MAX_SEG", "8"))
TRAIL = float(os.environ.get("TEST_TRAIL", "0.8"))


def load(path):
    with wave.open(path, "rb") as w:
        s0 = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(len(x) * CAPTURE_SR / s0)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def main():
    path = WAV if os.path.isabs(WAV) else os.path.join(HERE, WAV)
    audio = load(path)
    print(f"音频 {path} {len(audio)/CAPTURE_SR:.1f}s")

    if not asr_stream.available():
        print("未安装 sherpa-onnx（pip install sherpa-onnx）")
        return 1
    q = queue.Queue(maxsize=8192)
    cap = AudioCapture(q, use_vad=True)
    engine = asr_stream.StreamingASR(num_threads=4, rule2=1.2,
                                     hard_cap=max(10.0, MAX_SEG + 4.0))
    print(f"模型 {os.path.basename(engine.dir)} ({engine.kind}) "
          f"rule2={engine.rule2:.2f}s cap={engine.hard_cap:.0f}s\n")

    segs, first_at, t0 = [], None, time.time()
    for i in range(len(audio) // BLOCK_FRAMES):
        cap.feed_block(audio[i * BLOCK_FRAMES:(i + 1) * BLOCK_FRAMES])
        while True:
            try:
                _, blk = q.get_nowait()
            except queue.Empty:
                break
            engine.accept(blk, CAPTURE_SR)
            done = engine.endpoint() or (engine.elapsed >= MAX_SEG
                                         and cap.trailing_silence >= TRAIL)
            if done:
                text = engine.finish().strip()
                if text:
                    if first_at is None:
                        first_at = i * 0.1
                    segs.append((i * 0.1, engine.elapsed, text))
                    print(f"  [+{i*0.1:5.1f}s] {text}")
    # 文件末尾没有静音，模型最后几个 token 还没吐出来：补 0.3s 静音再收尾
    # （真实播放时后面自然会有静音，等价于这个操作）
    for _ in range(3):
        engine.accept(np.zeros(BLOCK_FRAMES, dtype=np.float32), CAPTURE_SR)
    rest = engine.finish().strip()
    if rest:
        segs.append((len(audio) / CAPTURE_SR, 0, rest))
        print(f"  [ 结尾 ] {rest}")

    full = " ".join(s[2] for s in segs)
    first_word_ok = bool(full) and full.split()[0].lower().startswith("welcome")
    tail_ok = bool(full) and full.lower().rstrip(".").endswith("engineering")
    no_stutter = "to to" not in full.lower()
    print("\n" + "=" * 60)
    print(f"句数 {len(segs)} | 首句在 {first_at:.1f}s | 用时 {time.time()-t0:.1f}s")
    print(f"句首首词完整: {first_word_ok} | 句尾完整: {tail_ok} | 无重复词: {no_stutter}")
    ok = first_word_ok and tail_ok and no_stutter
    print("判定:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
