# -*- coding: utf-8 -*-
"""
真流式 ASR 引擎（sherpa-onnx + 流式 Paraformer，ONNX 运行时，不依赖 torch）

与 Whisper 方案的本质区别：
  Whisper 是整段模型，只能"给一段音频 -> 出整段文字"，所以必须攒够 1.5 秒
  再整段重跑，延迟降不下去、算力还是 O(n²)。
  流式 Paraformer 维护内部状态，每喂 100ms 音频就能吐出增量文字，
  实测 RTF≈0.04（比实时快 25 倍），首个增量结果约 0.6 秒出现。

模型：csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en
首次使用会下载 int8 量化版（约 236MB），走 HF 国内镜像。
"""
import logging
import os
import sys

logger = logging.getLogger("subtitle")

REPO = "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en"
DIR_NAME = "sherpa-streaming-paraformer-zh-en"
# 只需要 int8 量化版，体积小、CPU 上更快
NEEDED_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt")


def _candidate_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    dirs = []
    env = os.environ.get("STREAMING_MODEL_DIR")
    if env:
        dirs.append(env)
    dirs.append(os.path.join(parent, "models", DIR_NAME))     # 源码运行
    if getattr(sys, "frozen", False):                          # 打包运行
        dirs.append(os.path.join(os.path.dirname(sys.executable), "models", DIR_NAME))
    return dirs


def find_model_dir():
    """找已下载的模型目录；找不到返回 None"""
    for d in _candidate_dirs():
        if all(os.path.isfile(os.path.join(d, f)) for f in NEEDED_FILES):
            return d
    return None


def download_model(progress=True):
    """从 HF 镜像下载流式模型，返回目录路径"""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    target = _candidate_dirs()[0] if os.environ.get("STREAMING_MODEL_DIR") \
        else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "models", DIR_NAME)
    from huggingface_hub import snapshot_download
    logger.info("下载流式模型到 %s ...", target)
    snapshot_download(
        REPO, local_dir=target,
        allow_patterns=[os.path.join(DIR_NAME, f) if False else f for f in NEEDED_FILES]
        + ["README.md"],
    )
    return target if all(os.path.isfile(os.path.join(target, f)) for f in NEEDED_FILES) else None


def available():
    """sherpa-onnx 是否安装"""
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:
        return False


class StreamingASR:
    """增量语音识别：喂 100ms 音频块，随时可取当前文本

    用法：
        start(preroll_blocks)   # 一句话开始（可带预卷音频，避免吃掉句首）
        feed(pcm16_block)       # 持续喂音频
        finish()                # 一句话结束，返回最终文本
    """

    def __init__(self, model_dir=None, num_threads=4):
        import sherpa_onnx
        self._dir = model_dir or find_model_dir()
        if not self._dir:
            raise RuntimeError("流式模型未下载")
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
            tokens=os.path.join(self._dir, "tokens.txt"),
            encoder=os.path.join(self._dir, "encoder.int8.onnx"),
            decoder=os.path.join(self._dir, "decoder.int8.onnx"),
            num_threads=num_threads,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            provider="cpu",
        )
        self.stream = None
        logger.info("流式 ASR 就绪: %s", self._dir)

    # ------------------------------------------------------------------
    def _new_stream(self):
        self.stream = self.recognizer.create_stream()

    def _decode(self):
        """把已积累的音频解码一遍，返回当前文本"""
        if self.stream is None:
            return ""
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        return self.recognizer.get_result(self.stream) or ""

    def start(self, preroll=None):
        """新的一句话：重置流，并先补回触发前的几块音频"""
        self._new_stream()
        for blk in (preroll or []):
            if blk is not None and len(blk):
                self.stream.accept_waveform(16000, blk)
        return self._decode()

    def feed(self, pcm16):
        """喂一个 100ms（1600 点）16k 音频块，返回当前增量文本"""
        if self.stream is None:
            self._new_stream()
        self.stream.accept_waveform(16000, pcm16)
        return self._decode()

    def finish(self):
        """一句话说完：补一段静音触发尾部解码，返回最终文本并重置"""
        if self.stream is None:
            return ""
        import numpy as np
        self.stream.accept_waveform(16000, np.zeros(3200, dtype=np.float32))
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        text = self.recognizer.get_result(self.stream) or ""
        self.stream = None
        return text

    def reset(self):
        """丢弃当前这句话（用于被判为误触发的过短音频段）"""
        self.stream = None
