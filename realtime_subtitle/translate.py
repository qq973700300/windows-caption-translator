# -*- coding: utf-8 -*-
"""
翻译引擎：免费在线引擎回退链 + 可选 LLM API
- 引擎链（auto）：mymemory -> google（任一成功即返回）
  * mymemory：api.mymemory.translated.net 直连，国内网络实测可用（匿名约 5 万字符/天）
  * google：deep-translator（translate.google.com，开了系统代理时可用）
- 可选 LLM：OpenAI 兼容接口（DeepSeek / 智谱 / 通义等均可），配置后优先使用
"""
import logging
import time

import requests

logger = logging.getLogger("subtitle")

TIMEOUT = 10  # 每个引擎的请求超时（秒）
RETRIES = 1   # 每个引擎失败后重试次数

_TS_HEADER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
}
_TS_CLIENT_KEY = "browser-chromium-Win32-882c22ae-5fab-4a27-a5bd-7b3d5a38afd4"

# MyMemory 语言对映射（目标固定中文）
MM_SRC = {"en": "en", "en-GB": "en-GB"}


class Translator:
    def __init__(self, engine="auto", target_lang="zh-CN",
                 llm_config=None):
        """
        engine: "auto"（回退链）| "google" | "mymemory" | "llm"
        target_lang: 目标语言（当前固定 zh-CN）
        llm_config: {"base_url":..., "api_key":..., "model":...}
        """
        self.engine = engine
        self.target = target_lang
        self.llm_config = llm_config or {}
        self._cache = {}          # 文本翻译缓存
        self._fail_until = {}     # 引擎临时熔断：失败后 30s 内不再尝试
        self.last_engine = None   # 最近一次成功的引擎（供 UI 显示）

    # ------------------------------------------------------------------
    def _mark_fail(self, engine):
        self._fail_until[engine] = time.time() + 30

    def _is_failing(self, engine):
        return time.time() < self._fail_until.get(engine, 0)

    # ------------------------------------------------------------------
    def _transmart(self, text, src_lang=None):
        """腾讯交互翻译 Transmart（国内直连稳定，质量好）"""
        src = "zh" if src_lang and src_lang.startswith("zh") else (src_lang or "en")
        tgt = "zh" if self.target.startswith("zh") else self.target.split("-")[0]
        if src == tgt:
            return text
        r = requests.post(
            "https://transmart.qq.com/api/imt",
            headers=_TS_HEADER,
            json={
                "header": {"fn": "auto_translation", "session": "",
                           "client_key": _TS_CLIENT_KEY},
                "source": {"text_list": [text[:800]], "lang": src},
                "target": {"lang": tgt},
                "model_type": "normal",
            },
            timeout=TIMEOUT)
        r.raise_for_status()
        out = r.json().get("auto_translation", [])
        if not out or not out[0]:
            raise RuntimeError("transmart 无结果")
        return out[0]

    def _mymemory(self, text, src_lang=None):
        """MyMemory 免费 API 直连（实测国内可用）"""
        src = "en-GB" if not src_lang or src_lang.startswith("en") else src_lang.split("-")[0]
        tgt = "zh-CN" if self.target.startswith("zh") else self.target
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:480], "langpair": f"{src}|{tgt}"},
            timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("responseStatus") == 200 or data.get("responseData", {}).get("translatedText"):
            out = data["responseData"]["translatedText"]
            # MyMemory 对未收录文本会返回大写警告文本
            if out and not out.startswith("MYMEMORY WARNING") and not out.isupper():
                return out
        raise RuntimeError("mymemory 无结果")

    def _google(self, text):
        from deep_translator import GoogleTranslator
        t = GoogleTranslator(source="auto", target=self.target)
        return t.translate(text)

    def _gtx(self, text):
        """Google gtx 免费接口直连（googleapis 域名）"""
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": self.target,
                    "dt": "t", "q": text},
            timeout=TIMEOUT)
        r.raise_for_status()
        return "".join(x[0] for x in r.json()[0] if x and x[0])

    def _llm(self, text, src_lang, context=None):
        base = self.llm_config.get("base_url", "").rstrip("/")
        key = self.llm_config.get("api_key", "")
        model = self.llm_config.get("model", "")
        if not (base and key and model):
            raise RuntimeError("LLM 配置不完整")
        tgt_name = "简体中文" if self.target.startswith("zh") else self.target
        # 有上下文就用三段式 prompt（历史只作参考，只翻当前句）
        # 注意用 is not None：上下文对象即使历史为空也是有效的（术语表仍要注入）
        prompt = context.build_prompt(text, tgt_name) if context is not None else None
        use_ctx = prompt is not None
        if prompt is None:
            prompt = (
                f"你是实时字幕翻译器。把下面这段语音识别文本翻译成{tgt_name}，"
                f"只输出译文本身，不要解释、不要引号。\n\n{text}"
            )
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 500,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        out = resp.json()["choices"][0]["message"]["content"].strip()
        logger.debug("LLM 翻译（上下文=%s）: %s -> %s", use_ctx, text[:20], out[:20])
        return out

    # ------------------------------------------------------------------
    def translate(self, text, src_lang=None, context=None):
        """带缓存与回退的翻译入口。失败返回 None（UI 显示原文）

        context: TranslationContext，仅 LLM 引擎会用到（其他引擎是单句接口）
        """
        if not text or not text.strip():
            return None
        # 源语言即目标语言：原样返回
        if src_lang and src_lang.startswith("zh") and self.target.startswith("zh"):
            return text
        key = self._cache_key(text, context)
        if key in self._cache:
            return self._cache[key]

        result = None
        engines = []
        if self.engine == "llm":
            engines = ["llm"]
        elif self.engine == "google":
            engines = ["google", "gtx"]
        elif self.engine == "mymemory":
            engines = ["mymemory"]
        elif self.engine == "transmart":
            engines = ["transmart"]
        else:  # auto：transmart 国内最稳，mymemory/google 系备用
            engines = ["transmart", "mymemory", "google", "gtx"]

        for eng in engines:
            if self._is_failing(eng):
                continue
            for attempt in range(RETRIES + 1):
                try:
                    t0 = time.time()
                    if eng == "llm":
                        result = self._llm(text, src_lang, context=context)
                    elif eng == "transmart":
                        result = self._transmart(text, src_lang)
                    elif eng == "mymemory":
                        result = self._mymemory(text, src_lang)
                    elif eng == "google":
                        result = self._google(text)
                    elif eng == "gtx":
                        result = self._gtx(text)
                    if result:
                        self.last_engine = eng
                        logger.info("翻译引擎 %s 用时 %.2fs", eng, time.time() - t0)
                        break
                except Exception as e:
                    logger.warning("翻译引擎 %s 第 %d 次失败: %s",
                                   eng, attempt + 1, str(e)[:150])
                    if attempt >= RETRIES:
                        self._mark_fail(eng)
            if result:  # 成功则跳出引擎链（break 只能跳出内层循环）
                break

        if result:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[key] = result
        return result

    # ------------------------------------------------------------------
    def _cache_key(self, text, context=None):
        """P2：语境缓存键

        单句引擎（transmart / mymemory / google）的译文只取决于这句话本身，
        所以仍用纯文本 key —— 缓存命中率不受影响。

        LLM 会把上下文和术语表一起看，同一句英文在不同对话里可能该翻成
        不同的意思，所以 key 里要带上 场景号 / 词典版本 / 上下文指纹，
        避免"上一集的译文被这一集复用"。
        """
        key = (text or "").strip()
        if context is None or self.engine != "llm":
            return key
        try:
            return context.cache_key(key)
        except Exception:
            return key

    def clear_cache(self):
        self._cache.clear()
