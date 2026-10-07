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

术语表（P2）：
  词典本身可能很大，但只把「当前句 / 最近历史里出现过」的条目注入 prompt，
  有界、不撑爆 token。词典任何改动都会让 glossary.version +1，参与缓存 key。

注意：上下文和术语表都只对 LLM 引擎有效。
  transmart / mymemory / google 都是单句接口，拿不到上下文，
  非 LLM 引擎下这里只做历史记录（为后续的术语一致性留出扩展点）。
"""
import hashlib
import logging
import threading
from collections import deque

from glossary import Glossary

logger = logging.getLogger("subtitle")

_LABELS = ["上一句", "上二句", "上三句", "上四句", "上五句"]


class TranslationContext:
    """最近若干句的双语历史 + 术语表 + 场景标识"""

    def __init__(self, max_turns=3, style="影视对白", glossary=None):
        """
        max_turns: 保留几轮历史（0 = 关闭上下文，退回单句 prompt）
        style:     译文风格要求
        glossary:  Glossary 实例，或 {"Stark Industries": "斯塔克工业"} 这样的字典
        """
        self.max_turns = max(0, int(max_turns))
        self.style = style
        self.glossary = (glossary if isinstance(glossary, Glossary)
                         else Glossary(data=glossary))
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
            return list(self.history), self.glossary.items()

    # ------------------------------------------------------------------
    def select_terms(self, hist, current):
        """挑出本次真正用得上的术语（当前句或最近历史里出现过）"""
        return self.glossary.match(current,
                                   *[s for s, _ in hist],
                                   *[d for _, d in hist])

    def build_prompt(self, current, target_name="简体中文"):
        """构造三段式 prompt；没有上下文也没有命中术语时返回 None（交回简版）"""
        hist, _all = self.snapshot()
        terms = self.select_terms(hist, current)
        if not hist and not terms:
            return None
        if self.max_turns <= 0 and not terms:
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

        if terms:
            lines.append("")
            lines.append("【术语表】必须严格沿用这些译法：")
            for k, v in terms:
                lines.append(f"{k} = {v}")

        lines.append("")
        lines.append("【当前句】")
        lines.append(current)
        lines.append("")
        lines.append("要求：")
        n = 0
        n += 1
        lines.append(f"{n}. 只输出当前句的译文，不要重复历史句子，不要解释，不要引号。")
        n += 1
        lines.append(f"{n}. 人名、地名、专有名词必须沿用参考历史里已经用过的译法。")
        if terms:
            n += 1
            lines.append(f"{n}. 出现在【术语表】里的词，必须使用术语表给出的译法，不得另译。")
        n += 1
        lines.append(f"{n}. 风格：自然的中文{self.style}字幕，简洁口语化。")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 缓存键（P2：语境缓存）
    #   旧版 _cache[text]：同一句英文无论在哪一集、哪段对话里出现都复用同一译文。
    #   现在把「场景 / 词典版本 / 上下文指纹」一起编进 key：
    #     - 换集（scene_id +1）-> 旧译文不再命中
    #     - 改了词典（version +1）-> 旧译文失效，否则改了还是显示错的那个
    #     - 上下文不同 -> 同一句话在不同语境里可能该翻成不同的意思
    #   注意：只对 LLM 生效，单句引擎的译文与上下文无关，仍用纯文本 key。
    def fingerprint(self, hist=None):
        """最近几轮历史的指纹；没有历史时为空串"""
        if hist is None:
            hist, _ = self.snapshot()
        if not hist:
            return ""
        raw = "\n".join(f"{s}\t{d}" for s, d in hist)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]

    def cache_key(self, text):
        return (f"s{self.scene_id}|g{self.glossary.version}"
                f"|c{self.fingerprint()}|t{(text or '').strip()}")

    # ------------------------------------------------------------------
    def __len__(self):
        with self._lock:
            return len(self.history)

    def __bool__(self):
        """必须是 True：类里定义了 __len__，否则「历史为空」会被当成假值，
        外面的 `if context:` 会整个跳过上下文/术语表（P2 实测踩到）"""
        return True
