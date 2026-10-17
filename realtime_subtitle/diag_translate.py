# -*- coding: utf-8 -*-
"""翻译质量诊断：先分清是「识别错了」还是「翻译错了」

很多人说"翻译不准"，但问题可能出在识别环节：
  1. 原文就识别错了         -> 换模型 / 加热词，翻译再好也没用
  2. 原文对，但译文差       -> 才是翻译环节的问题
  3. 原文没标点、全小写     -> 机器翻译引擎最容易翻崩的情况（流式 ASR 的通病）

用法：
  python diag_translate.py                       默认 test_long.wav
  TEST_WAV=test_speech.wav python diag_translate.py
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
from textutil import looks_chinese, normalize_for_mt           # noqa: E402
import asr_stream                                              # noqa: E402

WAV = os.environ.get("TEST_WAV", "test_long.wav")
SR = CAPTURE_SR


def load(path, sr=SR):
    with wave.open(path, "rb") as w:
        s0 = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(len(x) * sr / s0)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def run_asr(audio):
    """跑完整的切句 + 流式识别，返回成句原文列表"""
    q = queue.Queue(maxsize=8192)
    seg = AudioSegmenter(q, sensitivity=1.0)
    events = []
    for i in range(len(audio) // BLOCK_FRAMES):
        seg.feed_block(audio[i * BLOCK_FRAMES:(i + 1) * BLOCK_FRAMES])
        while True:
            try:
                events.append(q.get_nowait())
            except queue.Empty:
                break
    engine = asr_stream.StreamingASR(num_threads=4)
    finals = []
    for kind, payload in events:
        if kind == "start":
            engine.start(payload)
        elif kind == "cancel":
            engine.reset()
        elif kind == "end":
            text = engine.finish().strip()
            if text:
                finals.append(normalize_for_mt(text))   # 与线上一致：整句一次翻译
        else:
            engine.feed(payload)
    return finals


def describe(text):
    """看看原文长什么样：这是判断 MT 会不会翻崩的关键"""
    has_end = text.rstrip()[-1:] in ".?!。？！"
    has_upper = any(c.isupper() for c in text)
    return ("句末标点" if has_end else "无句末标点",
            "有大写" if has_upper else "全小写")


def main():
    audio = load(os.path.join(HERE, WAV))
    print(f"音频 {WAV}（{len(audio)/SR:.1f}s）\n正在跑切句 + 流式识别 ...")
    finals = run_asr(audio)
    if not finals:
        print("没识别出内容")
        return 1

    print(f"\n识别出 {len(finals)} 句。先看**原文**质量：\n")
    for i, s in enumerate(finals, 1):
        p, u = describe(s)
        print(f"  {i}. [{p} / {u}] {s}")

    from translate import Translator
    engines = [("transmart", "腾讯 Transmart"), ("mymemory", "MyMemory"),
               ("google", "Google")]
    print("\n再用各引擎分别翻译同一句原文（对比译文质量）：\n")
    for i, s in enumerate(finals[:6], 1):
        if looks_chinese(s):
            print(f"  {i}. {s}\n     （中文，不翻译）\n")
            continue
        print(f"  {i}. {s}")
        for key, name in engines:
            tr = Translator(engine=key, target_lang="zh-CN")
            try:
                out = tr.translate(s, src_lang="en")
                print(f"     {name:<12} {out}")
            except Exception as e:
                print(f"     {name:<12} 失败: {str(e)[:60]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
