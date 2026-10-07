# -*- coding: utf-8 -*-
"""
术语表（P2：专有名词译法一致性）

解决的问题：
  同一个人名/地名，LLM 每次都可能给出不同译法（John 一会儿"约翰"一会儿"乔恩"，
  Stark Industries 一会儿"斯塔克工业"一会儿"史塔克企业"），而且和历史上下文里
  已经上屏的译法打架。

做法（手工维护，不做自动抽取）：
  glossary.json 里写 {原文: 固定译法}，LLM 翻译时注入 prompt 强制沿用。

为什么不自动抽大写词：
  按首字母大写判断专有名词会误伤大量普通词（I / You / The / USA / NASA…），
  而且句首单词天然大写。第一版只做手工维护，后面看实际使用再考虑辅助抽取。

两个关键约束：
  1. 有界注入 —— 只把「当前句或最近历史里真正出现过」的条目放进 prompt。
     词典可以越加越长，但单次 prompt 里最多 MAX_TERMS 条，token 不会失控。
  2. 版本号 —— 词典任何改动都会 +1，并参与翻译缓存 key，
     否则改了词典却命中旧译文，屏幕上还是错的那个译法。
"""
import json
import logging
import os
import re
import sys

logger = logging.getLogger("subtitle")

# 模块目录：打包后为 _MEIPASS 内部
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
# 数据目录：源码运行=代码目录；打包后=exe 同级目录（与 config.json 一致）
BASE_DIR = (os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False) else _MODULE_DIR)
GLOSSARY_PATH = os.path.join(BASE_DIR, "glossary.json")

MAX_TERMS = 30          # 单次 prompt 最多注入多少条
_HAS_ASCII = re.compile(r"[A-Za-z0-9]")


class Glossary:
    """原词 -> 固定译法（手工维护）"""

    def __init__(self, path=None, data=None):
        self.path = path or GLOSSARY_PATH
        self.data = {}          # 保序：先加的先注入
        self._version = 1       # 改动计数，供翻译缓存失效用
        self._pat = {}          # 术语 -> 编译好的匹配正则
        if data:
            for k, v in data.items():
                self.set(k, v)

    # ------------------------------------------------------------ 持久化
    def load(self):
        """读 glossary.json。文件不存在属正常（首次运行），不报错"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            logger.info("术语表不存在，使用空表: %s", self.path)
            return self.data
        except Exception as e:
            logger.warning("术语表读取失败 %s: %s", self.path, e)
            return self.data
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, str):
                    self.set(k, v)
        logger.info("术语表已加载 %d 条: %s", len(self.data), self.path)
        return self.data

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.warning("术语表保存失败 %s: %s", self.path, e)
            return False

    # -------------------------------------------------------------- 编辑
    @property
    def version(self):
        return self._version

    def items(self):
        return list(self.data.items())

    def __len__(self):
        return len(self.data)

    def set(self, src, dst):
        """新增或修改一条。返回是否真的改变了内容"""
        src = (src or "").strip()
        dst = (dst or "").strip()
        if not src or not dst:
            return False
        changed = self.data.get(src) != dst
        self.data[src] = dst
        self._pat.pop(src, None)
        if changed:
            self._version += 1
        return changed

    def remove(self, src):
        if src in self.data:
            del self.data[src]
            self._pat.pop(src, None)
            self._version += 1
            return True
        return False

    # -------------------------------------------------------------- 匹配
    def _pattern(self, term):
        """西文术语要求词边界（避免 I 命中 Iceland），中文术语直接子串匹配"""
        p = self._pat.get(term)
        if p is None:
            esc = re.escape(term)
            if _HAS_ASCII.search(term):
                p = re.compile(r"(?<![A-Za-z0-9_])" + esc + r"(?![A-Za-z0-9_])",
                               re.IGNORECASE)
            else:
                p = re.compile(esc)
            self._pat[term] = p
        return p

    def match(self, *texts):
        """只返回在给定文本里真正出现过的条目（有界注入）

        texts: 当前句原文 + 最近几句的原文/译文
        """
        blob = "\n".join(t for t in texts if t)
        if not blob or not self.data:
            return []
        out = []
        for k, v in self.data.items():
            if len(out) >= MAX_TERMS:
                break
            if self._pattern(k).search(blob):
                out.append((k, v))
        return out
