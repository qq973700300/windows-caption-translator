# -*- coding: utf-8 -*-
"""
LocalAgreement 增量定稿

解决的问题：
  流式识别每 100ms 吐一次增量结果，尾部会不断变化
  （"I will go" -> "I will go to" -> "I will go to Beijing"）。
  如果每次都整行覆盖显示，字幕就会反复跳变；翻译也会被反复调用。

做法（不改模型，只在输出侧做）：
  连续两次识别结果一致的前缀 = 已经稳定的部分，可以定稿、可以送去翻译；
  尾巴部分允许继续变化，暂不翻译。
  这样字幕只会单向增长，不会回改。
"""
import logging
import re

logger = logging.getLogger("subtitle")

_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\uff00-\uffef]")
# 可以作为"完整词边界"的字符：空格与常见标点
_BOUNDARY = " \t\u3000.,;:!?，。、；：！？…"


def _cjk_ratio(s):
    if not s:
        return 0.0
    return len(_CJK_RE.findall(s)) / len(s)


def common_prefix_len(a, b):
    """两个字符串的公共前缀长度（按字符）"""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class LocalAgreement:
    """维护一句话的"已定稿前缀 + 待定稿尾巴"

    update(hyp)  -> (committed, pending)
    finalize(hyp) -> 强制整句定稿（一句话说完时调用）
    """

    def __init__(self, margin=1):
        self.margin = margin          # 西文额外留的字符余量（防提交半个词）
        self.committed = ""
        self.prev = None
        self.last_hyp = ""

    def reset(self):
        self.committed = ""
        self.prev = None
        self.last_hyp = ""

    def _commit_point(self, s, n):
        """在公共前缀 s[:n] 中找合适的提交点：优先落在完整词边界上"""
        if n <= 0:
            return 0
        if s[n - 1] in _BOUNDARY:         # 恰好停在空格/标点 -> 直接提交
            return n
        if _cjk_ratio(s[:n]) > 0.4:       # 中文没有空格，按字符提交
            return max(0, n - self.margin)
        i = s.rfind(" ", 0, n)            # 西文回退到最后一个空格之后
        return i + 1 if i >= 0 else 0

    def update(self, hyp):
        """喂入本次整句识别结果，返回 (已定稿文本, 待定稿尾巴)"""
        hyp = (hyp or "").strip()
        self.last_hyp = hyp
        if not hyp:
            return self.committed, ""
        if self.prev is None:
            self.prev = hyp               # 第一次没有可比较对象，全部待定稿
            return self.committed, hyp
        n = common_prefix_len(self.prev, hyp)
        target = self._commit_point(hyp, n)
        # 单调增长：只允许提交点前进，不允许回退
        if target > len(self.committed) and hyp.startswith(hyp[:target]):
            self.committed = hyp[:target]
        self.prev = hyp
        if hyp.startswith(self.committed):
            pending = hyp[len(self.committed):]
        else:                              # 结果整体变了，保守处理：全部算尾巴
            pending = hyp
        return self.committed, pending

    def finalize(self, hyp=None):
        """一句话说完：剩余部分全部定稿"""
        if hyp:
            hyp = hyp.strip()
            if hyp:
                self.last_hyp = hyp
                self.committed = hyp
        self.prev = None
        return self.committed, ""
