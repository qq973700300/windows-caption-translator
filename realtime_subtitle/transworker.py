# -*- coding: utf-8 -*-
"""
翻译工作线程 —— 让 ASR 永远不等翻译（架构底线）

为什么必须有这一层：
  翻译是网络请求/推理，耗时不可控（在线引擎 0.2~0.5s，LLM 0.5~2s，慢时能到超时）。
  如果在 ASR 线程里同步调用，翻译期间整条流水线是停的：音频块不再喂给流式引擎，
  stream_queue 塞满后开始丢音频，表现为字幕断流 + 识别质量变差。

三种投递方式：
  1. submit(seq, text)             整句翻译（默认）—— 说完翻一次，质量最好
  2. submit_simul(seq, partial)    边说边译 —— ASR 每次给出更长的半成品时调用，
                                   内部按「标点 / 8 个词」切块，翻一块追加一块，
                                   已经翻过的内容绝不重翻（所以不会闪）
  3. submit(seq, text, simul=True) 这段话说完了：把尾巴翻掉并定稿

为什么分块不重翻：
  重翻译（每 200ms 整句重来）会让译文来回改字，还会出现幻觉
  （实测 70% 的中间态把 "从你认识他那天起" 编成了 "从一开始"）。
  只追加不修改，屏幕上就只会「变长」。
"""
import logging
import queue
import threading
import time

from textutil import normalize_for_mt

logger = logging.getLogger("subtitle")

CHUNK_WORDS = 5          # 攒够这么多词就提交一块
END_PUNCT = ".!?。！？…"


class TranslationWorker:
    """串行翻译队列：整句一次，或「边说边译」按块追加"""

    def __init__(self, out_queue, translator, src_lang=None,
                 maxsize=32, min_gap=0.0, is_zh=None, context=None):
        """
        out_queue:  UI 的显示队列（结果直接回 UI）
        translator: Translator 实例（只在本线程内使用）
        src_lang:   源语言，None 表示自动
        maxsize:    待翻译队列上限
        min_gap:    队列里仍有积压时的最小输出间隔（避免同一段多句互相盖掉）
        is_zh:      判断"已是中文"的函数（命中则直显，不打 API）
        context:    TranslationContext（把最近几句原文+译文一起给模型参考）
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
        self._live = {}      # 同传模式：seq -> {sent, pending, acc}
        self._cur_seq = -1

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
        self._live.clear()

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    # ------------------------------------------------------------------
    def submit(self, seq, text, simul=False):
        """ASR 线程调用：非阻塞。队列满则丢最旧的一条，绝不等待"""
        if not text or not text.strip():
            return False
        return self._put(("final", seq, text, bool(simul)))

    def submit_simul(self, seq, partial):
        """边说边译：把当前半成品交给翻译线程，由它决定翻哪一块"""
        if not partial or not partial.strip():
            return False
        return self._put(("chunk", seq, partial, False))

    def _put(self, item):
        try:
            self.q.put_nowait(item)
            return True
        except queue.Full:
            try:
                self.q.get_nowait()        # 腾一个位置给最新的句子
                self.dropped += 1
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(item)
            except queue.Full:
                self.dropped += 1
            if self.dropped % 10 == 1:     # 节流：队列持续满时别刷屏
                logger.warning("翻译跟不上，已丢弃 %d 条旧请求", self.dropped)
            return True

    # ------------------------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            try:
                kind, seq, text, simul = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if kind == "chunk":
                    self._handle_chunk(seq, text)
                else:
                    self._handle_final(seq, text, simul)
            except Exception as e:
                logger.warning("翻译处理异常: %s", str(e)[:200])
                self.out.put(("subtitle", text, "", 0.0, True, seq))
            # 只有队列里还压着别的句子时才间隔输出，
            # 否则单句会被白白拖慢 min_gap 秒
            if self.min_gap > 0 and not self.q.empty():
                time.sleep(self.min_gap)

    # ------------------------------------------------------------------
    def _translate(self, text, prefix=""):
        """统一翻译调用（带流式回调，译文逐字浮现）

        prefix：同传模式下已经上屏的译文。流式回调必须带上它，
        否则屏幕上会先闪现新片段、再把整句拼回来（看起来像在跳字）。
        """
        def _on_delta(part):
            self.out.put(("partial_dst", self._cur_seq, prefix + part))

        delta_cb = _on_delta if getattr(self.tr, "engine", "") == "llm" else None
        try:
            return self.tr.translate(text, src_lang=self.src_lang,
                                     context=self.ctx, on_delta=delta_cb) or ""
        except TypeError:
            # 第三方/桩翻译器可能不接受 on_delta，退回普通调用
            return self.tr.translate(text, src_lang=self.src_lang,
                                     context=self.ctx) or ""

    # ------------------------------------------------------------------
    def _handle_chunk(self, seq, partial):
        """边说边译：新增文本攒着，够一块就翻一块并追加显示"""
        self._cur_seq = seq
        st = self._live.setdefault(seq, {"sent": "", "pending": "", "acc": ""})
        # 注意：这里**不能**先 normalize_for_mt —— 它会自动补句号，
        # 那样每个半成品都以句号结尾，会被误判成"一个完整句子"而逐词提交
        # （实测切出来是"这个。""病人。""报告。"）。规整只在真正提交片段时做。
        text = partial.strip()

        if text.startswith(st["sent"]):
            delta = text[len(st["sent"]):].strip()
        else:
            # ASR 回改了前缀（罕见）：只取长度增长的部分，避免重复翻译
            delta = text[len(st["sent"]):].strip() if len(text) > len(st["sent"]) else ""
        st["sent"] = text
        if delta:
            st["pending"] = (st["pending"] + " " + delta).strip()

        pending = st["pending"]
        if not pending:
            return
        words = len(pending.split())
        if words >= CHUNK_WORDS or pending[-1] in END_PUNCT:
            chunk = normalize_for_mt(pending)
            st["pending"] = ""
            if self.is_zh is not None and self.is_zh(chunk):
                dst = chunk
            else:
                dst = self._translate(chunk, prefix=st["acc"])
            if dst:
                st["acc"] = (st["acc"] + dst).strip()
                self.out.put(("partial_dst", seq, st["acc"]))
                logger.info("同传片段 seq=%d（%d 词）→ %s", seq, words, dst[:24])

    def _handle_final(self, seq, text, simul):
        """一句结束：同传模式只补尾巴；普通模式整句翻译"""
        self._cur_seq = seq
        src = normalize_for_mt(text)

        if simul:
            st = self._live.pop(seq, None) or {"sent": "", "pending": "", "acc": ""}
            more = src[len(st["sent"]):].strip() if src.startswith(st["sent"]) else ""
            tail = (st["pending"] + " " + more).strip()
            if tail:
                if self.is_zh is not None and self.is_zh(tail):
                    dst = tail
                else:
                    dst = self._translate(tail, prefix=st["acc"])
                if dst:
                    st["acc"] = (st["acc"] + dst).strip()
            dst = st["acc"]
            if not dst:                     # 一句没翻出任何片段：退回整句翻译
                dst = self._translate(src)
            self._finish(seq, src, dst)
            return

        if self.is_zh is not None and self.is_zh(text):
            dst = src                      # 识别出来就是中文：直接显示
        else:
            dst = self._translate(src)
        self._finish(seq, src, dst)

    def _finish(self, seq, src, dst):
        if self.ctx is not None:
            self.ctx.add(src, dst)         # 记入历史，供下一句参考
        self.done += 1
        # 带上原文一起回 UI：即使中间被预览字幕覆盖，最终显示的 src/dst 也是配对的
        self.out.put(("subtitle", src, dst, 0.0, True, seq))
