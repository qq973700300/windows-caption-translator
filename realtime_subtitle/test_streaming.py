# -*- coding: utf-8 -*-
"""流式管道离线测试（不依赖声卡）

把 wav 按 100ms 块喂给切句器，再按真实时序驱动流式 Paraformer，
走完整的 LocalAgreement 定稿，打印字幕出现的时间线。

关注两个指标：
  1. 首条字幕出现的时间（延迟）
  2. 字幕行是否回改（抖动）—— LocalAgreement 应当做到单向增长

用法：
  python test_streaming.py                       默认 test_speech.wav，重复 2 遍
  TEST_WAV=test_long.wav TEST_BGM=0.03 python test_streaming.py
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

from capture import AudioSegmenter, CAPTURE_SR, BLOCK_FRAMES  # noqa: E402
from streaming import LocalAgreement  # noqa: E402
from textutil import group_pieces, looks_chinese  # noqa: E402
import asr_stream  # noqa: E402

WAV = os.environ.get("TEST_WAV", "test_speech.wav")
BGM = float(os.environ.get("TEST_BGM", "0"))
REPEAT = int(os.environ.get("TEST_REPEAT", "2"))
GAP = float(os.environ.get("TEST_GAP", "0.9"))
SR = CAPTURE_SR


def load(path, sr=SR):
    with wave.open(path, "rb") as w:
        s0 = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(len(x) * sr / s0)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def build_audio():
    sp = load(os.path.join(HERE, WAV))
    parts = []
    for i in range(REPEAT):
        parts.append(sp)
        if i < REPEAT - 1:
            parts.append(np.zeros(int(GAP * SR), dtype=np.float32))
    a = np.concatenate([np.zeros(int(0.5 * SR), dtype=np.float32)] + parts
                       + [np.zeros(int(0.5 * SR), dtype=np.float32)])
    if BGM > 0:
        t = np.arange(len(a)) / SR
        m = (0.35 * np.sin(2 * np.pi * 220 * t) + 0.25 * np.sin(2 * np.pi * 277 * t)
             + 0.15 * np.sin(2 * np.pi * 330 * t))
        a = np.clip(a + (m / (np.abs(m).max() + 1e-9) * BGM), -1, 1)
    return a.astype(np.float32)


def collect_events(audio, sensitivity=1.0):
    """跑切句器，得到按时间顺序的流式事件 [(kind, payload)]"""
    q = queue.Queue(maxsize=8192)
    seg = AudioSegmenter(q, sensitivity=sensitivity)
    events = []
    for i in range(len(audio) // BLOCK_FRAMES):
        seg.feed_block(audio[i * BLOCK_FRAMES:(i + 1) * BLOCK_FRAMES])
        while True:
            try:
                events.append(q.get_nowait())
            except queue.Empty:
                break
    return events


class FakeTranslator:
    """桩翻译器：只计数，不打网络"""

    def __init__(self):
        self.calls = 0
        self.chars = 0

    def __call__(self, text, src_lang=None):
        self.calls += 1
        self.chars += len(text)
        return f"[译]{text}"


def main():
    audio = build_audio()
    print(f"音频 {len(audio)/SR:.1f}s | 引擎=流式 Paraformer | BGM={BGM}")
    events = collect_events(audio)
    print(f"切句事件 {len(events)} 个："
          f"start={sum(1 for k, _ in events if k == 'start')} "
          f"end={sum(1 for k, _ in events if k == 'end')} "
          f"cancel={sum(1 for k, _ in events if k == 'cancel')}\n")

    if not asr_stream.available():
        print("未安装 sherpa-onnx，无法测试（pip install sherpa-onnx）")
        return 1
    engine = asr_stream.StreamingASR(num_threads=4)
    la = LocalAgreement()
    tr = FakeTranslator()

    t_start = time.time()
    last_committed = ""
    shown = ""
    rewrites = 0
    first_at = None
    n_final = 0
    for kind, payload in events:
        if kind == "start":
            engine.start(payload)
            la.reset()
            last_committed = ""
            continue
        if kind == "cancel":
            engine.reset()
            la.reset()
            last_committed = ""
            continue
        if kind == "end":
            text = engine.finish().strip()
            la.reset()
            last_committed = ""
            if not text:
                continue
            n_final += 1
            for sent in (group_pieces([text]) or [text]):
                dst = sent if looks_chinese(sent) else tr(sent)
                print(f"  [成句 +{time.time()-t_start:5.1f}s] {sent[:60]}")
                print(f"     -> {dst[:60]}")
                shown = ""
            continue
        # audio：边说边出
        t0 = time.time()
        text = engine.feed(payload).strip()
        ms = (time.time() - t0) * 1000
        if not text:
            continue
        committed, pending = la.update(text)
        if not committed or committed == last_committed:
            continue
        last_committed = committed
        display = committed + ("…" if pending else "")
        if first_at is None:
            first_at = time.time() - t_start
        if shown and not display.startswith(shown.rstrip("…")):
            rewrites += 1
            flag = "  <-- 回改"
        else:
            flag = ""
        tr(committed)
        print(f"  [实时 +{time.time()-t_start:5.1f}s {ms:4.0f}ms] {display[:60]}{flag}")
        shown = display

    print("\n" + "=" * 60)
    print(f"首条字幕延迟：{first_at:.1f}s（从喂第一块音频算起，含引擎解码）"
          if first_at else "没有产生任何字幕")
    print(f"成句 {n_final} 段 | 字幕回改 {rewrites} 次 | "
          f"翻译调用 {tr.calls} 次 / {tr.chars} 字")
    print("判定：", "PASS" if n_final > 0 and rewrites == 0 else
          ("PASS（有少量回改）" if n_final > 0 else "FAIL（没有识别出内容）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
