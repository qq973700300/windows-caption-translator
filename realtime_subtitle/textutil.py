# -*- coding: utf-8 -*-
"""
字幕分句与文本工具（纯文本处理，与识别引擎无关）

流式 Paraformer 输出的是连续文本，直接整段显示会变成一大坨，
这里按"句末标点优先 + 长度兜底"切成适合一条字幕显示的块。
"""
import re

# 句末标点切分（英文要求标点后有空格，避免 3.5 / Mr. 被误切）
_SENT_RE = re.compile(r"(?<=[。！？…；])\s*|(?<=[.!?])[ \t\u3000]+")
_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\uff00-\uffef]")
_END_PUNCT = ".。！？!?…；"
MAX_SUB_WEIGHT = 60      # 字幕块权重上限（CJK 字符按 2 计 -> 约 30 汉字 / 60 西文字符）
HARD_SPLIT_RATIO = 1.4   # 超过该倍率才在块内兜底切分（保证完整句不被切碎）
MAX_SUB_PARTS = 3        # 一次最多切成几条字幕


def split_sentences(text):
    """按句末标点切分"""
    return [p.strip() for p in _SENT_RE.split(text) if p and p.strip()]


def _weight(s):
    return len(s) + len(_CJK_RE.findall(s))


def _join(cur, nxt):
    """中日韩字符之间不加空格，西文之间加空格"""
    if not cur:
        return nxt
    if (cur[-1] in _END_PUNCT or cur[-1].isspace() or nxt[0].isspace()
            or _CJK_RE.match(cur[-1]) or _CJK_RE.match(nxt[0])):
        return cur + nxt
    return cur + " " + nxt


def _hard_split(u, max_weight):
    """块内长度兜底切分：优先在逗号等软标点处断，实在没有则到硬上限强切"""
    if _weight(u) <= max_weight:
        return [u]
    soft, hard = max_weight, max_weight * HARD_SPLIT_RATIO
    out, buf, w = [], "", 0
    for ch in u:
        buf += ch
        w += 1 + (1 if _CJK_RE.match(ch) else 0)
        if w >= hard or (w >= soft and ch in "，,、；; "):
            out.append(buf.rstrip())
            buf, w = "", 0
    if buf.strip():
        out.append(buf.strip())
    return out


def group_pieces(pieces, max_weight=MAX_SUB_WEIGHT, max_parts=MAX_SUB_PARTS):
    """把识别文本切成适合一条字幕显示的块
    优先在句末标点处断开；没有标点时按长度兜底（解决中文连成一大串的问题）"""
    units = []
    for p in pieces:
        for u in _SENT_RE.split(p):
            u = u.strip()
            if u:
                # 只有明显偏长才在块内强切，完整短句保持不被切碎
                if _weight(u) > max_weight * HARD_SPLIT_RATIO:
                    units.extend(_hard_split(u, max_weight))
                else:
                    units.append(u)
    if not units:
        return []
    groups, cur = [], ""
    for u in units:
        cur = _join(cur, u)
        # 句末标点一律收句；没有标点时靠长度兜底
        if u[-1] in _END_PUNCT or _weight(cur) >= max_weight:
            groups.append(cur)
            cur = ""
    if cur:
        groups.append(cur)
    # 过短的碎片并入上一条；条数超上限则合并尾部
    merged = []
    for g in groups:
        if merged and _weight(g) < 10:
            merged[-1] = _join(merged[-1], g)
        else:
            merged.append(g)
    if len(merged) > max_parts:
        tail = merged[max_parts - 1:]
        merged = merged[:max_parts - 1] + ["".join(tail)]
    return merged


def cjk_ratio(s):
    """CJK 字符占比：流式引擎不返回语言标签，用它判断是否需要翻译"""
    if not s:
        return 0.0
    return len(_CJK_RE.findall(s)) / len(s)


def looks_chinese(text):
    """汉字占比过半 -> 判定为中文（无需翻译，直接显示）"""
    if not text:
        return False
    return cjk_ratio(text) > 0.5


def is_garbage(text):
    """过滤重复字符型幻觉（如 වවවවව / ʕᴗᴗᴗᴗᴗ）——静音或噪声上的典型产物"""
    s = "".join(text.split())
    if len(s) < 3:
        return False
    from collections import Counter
    top = Counter(s).most_common(1)[0][1]
    return top / len(s) > 0.6
