# -*- coding: utf-8 -*-
"""
faster-whisper 语音识别封装（本地离线）
模型首次运行会自动下载；已在文件顶部设置国内镜像加速。
"""
import os
import re
import sys
import logging

# 必须在 import huggingface 相关库之前设置镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

logger = logging.getLogger("subtitle")


def _setup_cuda_dlls():
    """Windows 上把 pip 安装的 nvidia-* 包中的 DLL 目录加入搜索路径
    （cublas64_12.dll / cudnn64_9.dll 位于 site-packages/nvidia/*/bin）"""
    if sys.platform != "win32":
        return
    try:
        import sysconfig
        nvidia_dir = os.path.join(sysconfig.get_paths()["purelib"], "nvidia")
        if not os.path.isdir(nvidia_dir):
            return
        for sub in os.listdir(nvidia_dir):
            for folder in ("bin", "lib", os.path.join("lib", "x64")):
                d = os.path.join(nvidia_dir, sub, folder)
                if not os.path.isdir(d):
                    continue
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    except Exception as e:
        logger.debug("CUDA DLL 路径注册跳过: %s", e)


_setup_cuda_dlls()

# whisper 常见幻觉黑名单（识别出这些直接丢弃）
HALLUCINATION_BLACKLIST = (
    "字幕由", "Amara.org", "字幕组", "请不吝点赞", "订阅频道",
    "谢谢观看", "感谢观看", "明镜需要您的支持", "点赞关注",
    "Subtitles by", "Please subscribe", "字幕",
)


# ---------------------------------------------------------------------------
# 断句：把识别结果切成"一句一条字幕"
# 难点：中文识别结果常常没有句号，光靠标点切不开 -> 同时用 whisper 自身的分段边界 + 长度上限
_SENT_RE = re.compile(r"(?<=[。！？…；])\s*|(?<=[.!?])[ \t\u3000]+")
_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\uff00-\uffef]")
_END_PUNCT = ".。！？!?…；"
MAX_SUB_WEIGHT = 60      # 字幕块权重上限（CJK 字符按 2 计 -> 约 30 汉字 / 60 西文字符）
HARD_SPLIT_RATIO = 1.4   # 超过该倍率才在块内兜底切分（保证完整句不被切碎）
MAX_SUB_PARTS = 3        # 一次最多切成几条字幕


def split_sentences(text):
    """按句末标点切分（英文要求标点后有空格，避免 3.5 / Mr. 被误切）"""
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
    """把 whisper 的分段合并成适合一条字幕显示的块
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


def _is_garbage(text):
    """过滤重复字符型幻觉（如 වවවවව / ʕᴗᴗᴗᴗᴗ）——静音或噪声上的典型产物"""
    s = "".join(text.split())
    if len(s) < 3:
        return False
    from collections import Counter
    top = Counter(s).most_common(1)[0][1]
    if top / len(s) > 0.6:                  # 单字符占比过高
        return True
    return False

_MODEL_CACHE = {}
_ACTUAL_DEVICE = {"dev": None}


def detect_device(prefer="auto"):
    """返回实际使用的设备：cuda / cpu"""
    if prefer == "cpu":
        return "cpu"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def get_model(model_size="small", device="auto"):
    """加载/复用 whisper 模型（有 NVIDIA 显卡时自动用 GPU）"""
    key = (model_size, device)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    from faster_whisper import WhisperModel
    dev = detect_device(device)
    compute = "float16" if dev == "cuda" else "int8"
    # 显式设置 CPU 线程数：实测 24 核机器上 small 从 3.0s 降到 1.6s
    threads = max(4, min(16, (os.cpu_count() or 4)))
    logger.info("加载模型 %s (device=%s, compute=%s) ...", model_size, dev, compute)
    model = None
    for offline in (True, False):   # 先试本地缓存（免联网），失败再联网下载
        try:
            if offline:
                os.environ["HF_HUB_OFFLINE"] = "1"
            else:
                os.environ.pop("HF_HUB_OFFLINE", None)
            model = WhisperModel(model_size, device=dev, compute_type=compute,
                                 cpu_threads=threads)
            if dev == "cuda":
                # 实测一次极短推理，确认 GPU 真的可用（缺 cuBLAS/cuDNN 时会在这里失败）
                import numpy as np
                model.transcribe(np.zeros(4800, dtype=np.float32), beam_size=1)
            break
        except Exception as e:
            logger.warning("模型加载失败(dev=%s, offline=%s): %s", dev, offline, str(e)[:120])
            model = None
            if dev == "cuda":       # GPU 不可用 -> 自动回退 CPU
                logger.warning("GPU 不可用，回退 CPU")
                dev, compute = "cpu", "int8"
                offline = True
    if model is None:
        raise RuntimeError("模型加载失败")
    _MODEL_CACHE[(model_size, device)] = model
    _ACTUAL_DEVICE["dev"] = dev     # GPU 失败回退后这里会变成 cpu
    logger.info("模型 %s 加载完成 (device=%s)", model_size, dev)
    return model


def actual_device():
    """返回模型实际运行所在的设备（GPU 回退后为 cpu）"""
    return _ACTUAL_DEVICE.get("dev")


def transcribe_pieces(pcm16k, model_size="small", language="auto", device="auto",
                      max_weight=MAX_SUB_WEIGHT):
    """
    识别一段 float32 16k 单声道音频，返回 (字幕块列表, 检测到的语言, 语言置信度)
    字幕块已按"句末标点 + 长度上限"切好，可直接逐条显示
    language: "auto" 自动检测，或 "en"/"ja"/"ko"/"zh" 等
    """
    model = get_model(model_size, device)
    kwargs = dict(
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,  # 避免上下文污染产生幻觉
        vad_filter=False,                  # 上游已做 VAD 切句
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.0,   # 过滤重复型幻觉（如 这个这个这个）
    )
    if language and language != "auto":
        kwargs["language"] = language

    segments, info = model.transcribe(pcm16k, **kwargs)
    raw = [seg.text.strip() for seg in segments]
    text = "".join(raw).strip()

    lang = getattr(info, "language", None)
    prob = float(getattr(info, "language_probability", 1.0) or 1.0)
    if not text:
        return [], lang, prob
    # 过滤幻觉文本
    for bad in HALLUCINATION_BLACKLIST:
        if bad in text:
            logger.info("丢弃疑似幻觉: %s", text)
            return [], lang, prob
    if _is_garbage(text):
        logger.info("丢弃重复字符幻觉: %s", text[:40])
        return [], lang, prob
    # 过短结果多为噪声产物
    if len(text.strip()) < 3:
        logger.info("丢弃过短结果: %s", text)
        return [], lang, prob
    return group_pieces(raw, max_weight), lang, prob


def transcribe(pcm16k, model_size="small", language="auto", device="auto"):
    """兼容旧接口：返回 (整段文本, 语言, 置信度)"""
    pieces, lang, prob = transcribe_pieces(pcm16k, model_size, language, device)
    out = ""
    for p in pieces:
        out = _join(out, p)
    return out, lang, prob
