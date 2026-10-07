# -*- coding: utf-8 -*-
"""
翻译上下文（P1：严格有界上下文）

解决的问题：
  一句话单独丢给翻译，模型不知道前面说了什么。代词（he 指谁）、
  人名（John 到底译作什么）、口语省略、剧情语境全都只能靠猜。

做法：
  把最近 N 句的「原文 + 已上屏译文」一起给模型当参考，
  但明确要求它**只输出当前句的译文**，不重翻历史。
  历史同时带原文和译文，是为了让新译文在术语和语气上跟屏幕上的保持一致
  （只给原文的话，模型很可能翻出另一个人名译法，跟已显示的打架）。

为什么叫"严格有界"：
  history 用 deque(maxlen=N) 封顶，token 不会随播放时长无限增长；
  换集/长时间静音时 reset()，避免上一部的剧情和人名污染当前内容。

注意：上下文只对 LLM 引擎有效。
  transmart / mymemory / google 都是单句接口，拿不到上下文，
  非 LLM 引擎下这里只做历史记录（为后续的术语一致性留出扩展点）。
"""
import logging
import threading
from collections import deque

logger = logging.getLogger("subtitle")

_LABELS = ["上一句", "上二句", "上三句", "上四句", "上五句"]


class TranslationContext:
    """最近若干句的双语历史 + 术语表 + 场景标识"""

    def __init__(self, max_turns=3, style="影视对白", glossary=None):
        """
        max_turns: 保留几轮历史（0 = 关闭上下文，退回单句 prompt）
        style:     译文风格要求
        glossary:  {"Stark Industries": "斯塔克工业"} —— P2 术语表，暂由外部注入
        """
        self.max_turns = max(0, int(max_turns))
        self.style = style
        self.glossary = dict(glossary or {})
        self.history = deque(maxlen=max(self.max_turns, 1))
        self.scene_id = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def reset(self):
        """场景切换（换集 / 长时间静音 / 重新开始）时清空上下文"""
        with self._lock:
            self.history.clear()
            self.scene_id += 1
            return self.scene_id

    def add(self, src, dst):
        """一句话翻译完成，记入历史"""
        if not src or not dst:
            return
        with self._lock:
            if self.max_turns > 0:
                self.history.append((src.strip(), dst.strip()))

    def snapshot(self):
        """取一份快照，避免构造 prompt 时加锁过久"""
        with self._lock:
            return list(self.history), dict(self.glossary)

    # ------------------------------------------------------------------
    def build_prompt(self, current, target_name="简体中文"):
        """构造三段式 prompt；没有上下文时返回 None（交回简版 prompt）"""
        hist, gloss = self.snapshot()
        if not hist and not gloss:
            return None
        if self.max_turns <= 0 and not gloss:
            return None

        lines = [f"你是{self.style}翻译器。把「当前句」翻译成{target_name}。"]

        if hist:
            lines.append("")
            lines.append("【参考历史】只用于理解上下文，不要翻译、不要输出这些内容：")
            n = len(hist)
            for i, (s, d) in enumerate(hist):
                k = n - 1 - i                      # 0 = 最近的一句
                label = _LABELS[k] if k < len(_LABELS) else f"上{k + 1}句"
                lines.append(f"{label}  EN: {s}")
                lines.append(f"{' ' * len(label)}  ZH: {d}")

        if gloss:
            lines.append("")
            lines.append("【术语表】必须严格沿用这些译法：")
            for k, v in gloss.items():
                lines.append(f"{k} = {v}")

        lines.append("")
        lines.append("【当前句】")
        lines.append(current)
        lines.append("")
        lines.append("要求：")
        lines.append("1. 只输出当前句的译文，不要重复历史句子，不要解释，不要引号。")
        lines.append("2. 人名、地名、专有名词必须沿用参考历史里已经用过的译法。")
        lines.append(f"3. 风格：自然的中文{self.style}字幕，简洁口语化。")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def __len__(self):
        with self._lock:
            return len(self.history)
