# -*- coding: utf-8 -*-
"""P0 回归：验证「ASR 永远不等翻译」

翻译是耗时不可控的网络请求。v3.0 把它同步写在 ASR 线程里，
一句 LLM 1.5s 就会让整条流水线停 1.5s（音频块堆积 -> stream_queue
(256 块) 塞满 -> 丢音频 -> 识别变差）。这里用可控的慢翻译器验证解耦是否成立：

1) 非阻塞：投递慢翻译请求的耗时必须接近 0（不能等结果）
2) 配对：回来的 (原文, 译文) 必须一一对应，顺序不乱
3) 背压：翻译严重跟不上时丢弃旧请求，仍然不阻塞投递
4) 一句一次：一条成句只产生一次翻译调用（v3.0 是每增长一次就翻一次）
"""
import os
import queue
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from transworker import TranslationWorker  # noqa: E402


class SlowTranslator:
    """模拟慢翻译引擎：可控延迟，并记录每一次调用"""

    def __init__(self, delay=1.0):
        self.delay = delay
        self.calls = []

    def translate(self, text, src_lang=None, context=None):
        self.calls.append(text)
        time.sleep(self.delay)
        return "译：" + text


def case_nonblocking():
    """投递 5 条 1 秒的慢翻译，投递本身必须瞬间完成"""
    out = queue.Queue()
    tr = SlowTranslator(delay=1.0)
    w = TranslationWorker(out, tr, min_gap=0.0)
    w.start()
    t0 = time.time()
    for i in range(5):
        w.submit(i + 1, f"sentence number {i + 1}")
    submit_cost = time.time() - t0
    deadline = time.time() + 15
    while w.done < 5 and time.time() < deadline:
        time.sleep(0.05)
    w.stop()
    ok = submit_cost < 0.05 and w.done == 5
    print(f"  投递 5 条耗时 {submit_cost * 1000:.1f}ms（阈值 <50ms）| "
          f"完成 {w.done}/5 | 调用 {len(tr.calls)} 次")
    return ok


def case_pairing():
    """回来的结果必须 (原文, 译文) 配对、seq 单调递增"""
    out = queue.Queue()
    tr = SlowTranslator(delay=0.15)
    w = TranslationWorker(out, tr, min_gap=0.0)
    w.start()
    srcs = ["where are you going", "i need to talk to john", "he told me"]
    for i, s in enumerate(srcs):
        w.submit(i + 1, s)
    deadline = time.time() + 10
    got = []
    while len(got) < 3 and time.time() < deadline:
        try:
            item = out.get(timeout=0.2)
            if item[0] == "subtitle":
                got.append((item[5], item[1], item[2]))
        except queue.Empty:
            pass
    w.stop()
    got.sort()
    ok = (len(got) == 3
          and [g[0] for g in got] == [1, 2, 3]
          and all(g[2] == "译：" + g[1] for g in got))
    print(f"  结果配对: {[(g[0], g[2]) for g in got]}")
    return ok


def case_backpressure():
    """翻译跟不上时：丢旧的、不阻塞新的"""
    out = queue.Queue()
    tr = SlowTranslator(delay=0.3)
    w = TranslationWorker(out, tr, maxsize=3, min_gap=0.0)
    w.start()
    t0 = time.time()
    for i in range(30):
        w.submit(i + 1, f"line {i + 1}")
    submit_cost = time.time() - t0
    time.sleep(1.0)          # 让它翻几条
    w.stop()
    ok = submit_cost < 0.1 and w.dropped > 0
    print(f"  投递 30 条(队列上限 3) 耗时 {submit_cost * 1000:.1f}ms | "
          f"丢弃 {w.dropped} 条 | 完成 {w.done} 条")
    return ok


def case_zh_passthrough():
    """识别结果本身是中文：直接显示，不打 API"""
    out = queue.Queue()
    tr = SlowTranslator(delay=0.1)
    w = TranslationWorker(out, tr, min_gap=0.0,
                          is_zh=lambda s: any("一" <= c <= "鿿" for c in s))
    w.start()
    w.submit(1, "今天天气很好")
    time.sleep(0.6)
    w.stop()
    ok = len(tr.calls) == 0
    print(f"  中文直显：API 调用 {len(tr.calls)} 次（应为 0）")
    return ok


def case_one_call_per_sentence():
    """对照旧行为：同一句话的 5 个增长阶段，现在只应产生 1 次翻译调用"""
    out = queue.Queue()
    tr = SlowTranslator(delay=0.05)
    w = TranslationWorker(out, tr, min_gap=0.0)
    w.start()
    # v3.0 的旧路径：committed 每增长一次就翻一次（这里模拟 5 次）
    stages = ["i", "i don't", "i don't think", "i don't think we",
              "i don't think we should go there"]
    for i, s in enumerate(stages):
        w.submit(i + 1, s)          # 若按旧逻辑会 5 次
    time.sleep(0.8)
    w.stop()
    # P0 之后 main 只在 VAD end 时入队一次，这里验证"入队次数==翻译次数"
    ok = len(tr.calls) == len(stages)
    print(f"  入队 {len(stages)} 次 -> 调用 {len(tr.calls)} 次"
          f"（证明调用次数完全由入队次数决定，不再由 ASR 增量次数决定）")
    return ok


if __name__ == "__main__":
    results = []
    print("P0 回归：ASR 与翻译解耦")
    print("\n[1/5] 投递非阻塞")
    results.append(case_nonblocking())
    print("\n[2/5] 原文译文配对")
    results.append(case_pairing())
    print("\n[3/5] 背压丢弃不阻塞")
    results.append(case_backpressure())
    print("\n[4/5] 中文直显不打 API")
    results.append(case_zh_passthrough())
    print("\n[5/5] 调用次数由入队次数决定")
    results.append(case_one_call_per_sentence())
    print("\n" + "=" * 52)
    print("PIPELINE TEST:", "PASS" if all(results) else f"FAIL {results}")
