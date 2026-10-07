# -*- coding: utf-8 -*-
"""
翻译工作线程 —— 让 ASR 永远不等翻译（架构底线）

为什么必须有这一层：
  翻译是网络请求，耗时不可控（在线引擎 0.2~0.5s，LLM 0.5~2s，慢的时候能到超时 10s）。
  v3.0 把翻译直接写在 ASR worker 里同步调用，翻译期间整条流水线是停的：
  音频块不再喂给流式引擎、VAD 事件不再处理，stream_queue（256 块 ≈ 25.6s）
  被塞满后开始丢音频，表现为字幕断流 + 识别质量变差。LLM 引擎下尤其明显。

现在的分工：
  ASR 线程  —— 只把成句文本投递到有界队列，立刻返回，绝不等待
  翻译线程  —— 串行地慢慢翻，结果通过 disp_queue 回 UI
  队列满     —— 说明翻译严重跟不上，丢最旧的一条（宁可少翻一句，不能拖垮 ASR）

附带修掉的另一个问题：
  v3.0 在预览阶段对 committed 每增长一次就翻译一次，一条长句会打 5~10 次请求。
  现在只在 VAD 判定"这句话说完了"（end）时才入队一次。
"""
import logging
import queue
import threading
import time

logger = logging.getLogger("subtitle")


class TranslationWorker:
    """串行翻译队列：一条 VAD 成句 = 一次翻译请求"""

    def __init__(self, out_queue, translator, src_lang=None,
                 maxsize=32, min_gap=0.0, is_zh=None, context=None):
        """
        out_queue:  UI 的显示队列（结果直接回 UI）
        translator: Translator 实例（只在本线程内使用）
        src_lang:   源语言，None 表示自动
        maxsize:    待翻译队列上限
        min_gap:    队列里仍有积压时的最小输出间隔（避免同一段多句互相盖掉）
        is_zh:      判断"已是中文"的函数（命中则直显，不打 API）
        context:    TranslationContext（P1：把最近几句原文+译文一起给 LLM 参考）
        """
        self.q = queue.Queue(maxsize=maxsize)
        self.out = out_queue
        self.tr = translator
        self.src_lang = src_lang
        self.min_gap = min_gap
        self.is_zh = is_zh
        self.ctx = context
        self._stop = threading.Event()
        self.thread = None
        self.dropped = 0     # 因队列满被丢弃的请求数
        self.done = 0        # 完成的翻译数

    # ------------------------------------------------------------------
    def start(self):
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="translator")
        self.thread.start()

    def stop(self, wait=1.0):
        self._stop.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(wait)
        self.thread = None
        with self.q.mutex:                 # 清空积压，下次启动是干净的
            self.q.queue.clear()

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    # ------------------------------------------------------------------
    def submit(self, seq, text):
        """ASR 线程调用：非阻塞。队列满则丢最旧的一条，绝不等待"""
        if not text or not text.strip():
            return False
        try:
            self.q.put_nowait((seq, text))
            return True
        except queue.Full:
            try:
                self.q.get_nowait()        # 腾一个位置给最新的句子
                self.dropped += 1
            except queue.Empty:
                pass
            try:
                self.q.put_nowait((seq, text))
            except queue.Full:
                self.dropped += 1
            if self.dropped % 10 == 1:     # 节流：队列持续满时别刷屏
                logger.warning("翻译跟不上，已丢弃 %d 条旧请求", self.dropped)
            return True

    # ------------------------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            try:
                seq, text = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handle(seq, text)
            except Exception as e:
                logger.warning("翻译处理异常: %s", str(e)[:200])
                self.out.put(("subtitle", text, "", 0.0, True, seq))
            # 只有队列里还压着别的句子时才间隔输出，
            # 否则单句会被白白拖慢 min_gap 秒
            if self.min_gap > 0 and not self.q.empty():
                time.sleep(self.min_gap)

    def _handle(self, seq, text):
        if self.is_zh is not None and self.is_zh(text):
            dst = text                     # 识别出来就是中文：直接显示
        else:
            t0 = time.time()
            # 上下文只对 LLM 引擎有效；其他引擎是单句接口，传了也用不上
            dst = self.tr.translate(text, src_lang=self.src_lang,
                                    context=self.ctx) or ""
            logger.info("翻译 seq=%d 用时 %.2fs → %s",
                        seq, time.time() - t0, (dst or "")[:30])
        if self.ctx is not None:
            self.ctx.add(text, dst)        # 记入历史，供下一句参考
        self.done += 1
        # 带上原文一起回 UI：即使中间被预览字幕覆盖，最终显示的 src/dst 也是配对的
        self.out.put(("subtitle", text, dst, 0.0, True, seq))
