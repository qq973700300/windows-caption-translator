# -*- coding: utf-8 -*-
"""
真流式 ASR 引擎（sherpa-onnx + 常驻 OnlineStream）

形态对齐官方示例
`sherpa-onnx/python-api-examples/speech-recognition-from-microphone-with-endpoint-detection.py`：

  · 音频流永不重建：create_stream() 只建一次，句末只 recognizer.reset(stream)
  · 48k 音频直接 accept_waveform(48000, ...)，由 sherpa-onnx 内部 resampler 降采样
  · 句末判定交给 recognizer.is_endpoint()（rule1 / rule2 / rule3），不再自己数帧
  · 没有准备段（preroll）、没有环形缓冲、没有 start/end 事件：
    以前的实现会在句首重建流并丢掉触发那一块，句首因此出现 100ms 空洞，
    识别结果直接变成 "wthem to to this channe" 这种乱码。

模型选择：优先 X-ASR（中英混说 + 自带标点），退回流式 Paraformer。
两者都是 sherpa-onnx 的 OnlineRecognizer，只是工厂方法不同。
"""
import glob
import logging
import os
import sys

logger = logging.getLogger("subtitle")

# 模型目录（按优先级排列）：放在项目根的 models/ 下
MODEL_CANDIDATES = [
    # X-ASR：Zipformer-Transducer，中英自由混说，自带标点，480ms 块，Apache-2.0
    "sherpa-onnx-x-asr-480ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05",
    # 旧的中英双语 Paraformer 流式模型（无标点，混说容易串字）
    "sherpa-streaming-paraformer-zh-en",
]

# 只给「自动下载」用：Paraformer 在 HuggingFace 上，走国内镜像
REPO = "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en"
NEEDED_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt")


def _roots():
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    roots = []
    env = os.environ.get("STREAMING_MODEL_DIR")
    if env:
        roots.append(os.path.dirname(env) or env)
    roots.append(os.path.join(parent, "models"))
    if getattr(sys, "frozen", False):
        roots.append(os.path.join(os.path.dirname(sys.executable), "models"))
    return roots


def _first(patterns, d):
    for p in patterns:
        hit = sorted(glob.glob(os.path.join(d, p)))
        if hit:
            return hit[0]
    return None


def model_files(d):
    """在模型目录里认出实际文件名（不同发布包的命名不一致，用通配兜住）

    返回 (kind, files)：
      kind = "transducer"（encoder + decoder + joiner）或 "paraformer"（encoder + decoder）
    """
    if not d or not os.path.isdir(d):
        return None, None
    tokens = os.path.join(d, "tokens.txt")
    if not os.path.isfile(tokens):
        return None, None
    enc = _first(("*encoder*int8*.onnx", "*encoder*.onnx"), d)
    dec = _first(("*decoder*int8*.onnx", "*decoder*.onnx"), d)
    joiner = _first(("*joiner*int8*.onnx", "*joiner*.onnx"), d)
    if enc and dec and joiner:
        return "transducer", {"tokens": tokens, "encoder": enc,
                              "decoder": dec, "joiner": joiner}
    # Paraformer 发布包里 encoder/decoder 也可能叫 model.onnx
    para = _first(("model.int8.onnx", "model.onnx"), d)
    if para and tokens:
        return "paraformer", {"tokens": tokens, "encoder": para,
                              "decoder": para}
    if enc and dec:
        return "paraformer", {"tokens": tokens, "encoder": enc, "decoder": dec}
    return None, None


def find_model_dir():
    """按优先级找可用模型目录

    优先级：环境变量指定 > 目录名带 x-asr（中英混说+标点）> 其他 transducer > paraformer
    这样用户把新模型随便放进 models/ 子目录就能生效，不用改代码。
    """
    env = os.environ.get("STREAMING_MODEL_DIR")
    if env:
        kind, _ = model_files(env)
        if kind:
            return env
    dirs = []
    for root in _roots():
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if os.path.isdir(d):
                dirs.append(d)
    # 1) 名字里带 x-asr 的优先（自带标点，中英自由混说）
    for d in dirs:
        if "x-asr" in os.path.basename(d).lower():
            if model_files(d)[0] == "transducer":
                return d
    # 2) 其他 transducer（Zipformer 系列都比 Paraformer 强）
    for d in dirs:
        if model_files(d)[0] == "transducer":
            return d
    # 3) 兜底的 Paraformer
    for d in dirs:
        if model_files(d)[0] == "paraformer":
            return d
    return None


def available():
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:
        return False


def download_model(progress=True):
    """兜底：下载流式 Paraformer（X-ASR 请手动下载，见 README）"""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    target = os.path.join(_roots()[-1 if getattr(sys, "frozen", False) else 0],
                          MODEL_CANDIDATES[1])
    from huggingface_hub import snapshot_download
    logger.info("下载流式模型到 %s ...", target)
    snapshot_download(REPO, local_dir=target, allow_patterns=list(NEEDED_FILES) + ["README.md"])
    return target if os.path.isdir(target) else None


class StreamingASR:
    """常驻流的增量识别（官方示例形态）"""

    def __init__(self, model_dir=None, num_threads=4, rule1=2.4, rule2=0.8,
                 hard_cap=12.0, sensitivity=1.0):
        import sherpa_onnx

        self.dir = model_dir or find_model_dir()
        kind, files = model_files(self.dir)
        if not kind:
            raise RuntimeError(f"没有可用的流式模型：{self.dir}")
        self.kind = kind
        # 断句灵敏度：越大 -> 句末静音要求越短 -> 断句越早
        self.rule2 = max(0.5, min(1.8, float(rule2) / max(0.3, sensitivity)))
        self.hard_cap = float(hard_cap)
        self._t = 0.0            # 当前这句已经解码了多少秒
        self._sr = 48000         # 实际喂进来的采样率（首次 accept 时确定）

        common = dict(
            tokens=files["tokens"],
            num_threads=num_threads,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            provider="cpu",
            # 端点检测交给 recognizer：rule1 管"一直没人说话"，
            # rule2 管"说完停了一下"，rule3 是我们买的保险（防止一句无限长）
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=rule1,
            rule2_min_trailing_silence=self.rule2,
            rule3_min_utterance_length=max(self.hard_cap, 10.0),
        )
        if kind == "transducer":
            self.rec = sherpa_onnx.OnlineRecognizer.from_transducer(
                encoder=files["encoder"], decoder=files["decoder"],
                joiner=files["joiner"], **common)
        else:
            self.rec = sherpa_onnx.OnlineRecognizer.from_paraformer(
                encoder=files["encoder"], decoder=files["decoder"], **common)
        self.stream = self.rec.create_stream()
        logger.info("流式 ASR 就绪: %s (%s, rule2=%.2fs, cap=%.0fs)",
                    self.dir, kind, self.rule2, self.hard_cap)

    # ------------------------------------------------------------------
    def accept(self, waveform, sample_rate=48000):
        """喂一块音频（默认 48k，内部重采样），返回当前累计文本"""
        self._sr = int(sample_rate)
        self.stream.accept_waveform(self._sr, waveform)
        self._t += len(waveform) / float(self._sr)
        while self.rec.is_ready(self.stream):
            self.rec.decode_stream(self.stream)
        return self.result()

    def result(self):
        return self.rec.get_result(self.stream) or ""

    def endpoint(self):
        """这句话说完了吗（官方端点规则 + 我们自己的硬上限兜底）"""
        try:
            if self.rec.is_endpoint(self.stream):
                return True
        except Exception:
            pass
        return self._t >= self.hard_cap

    def finish(self):
        """取整句文本并复位，准备下一句

        不额外补静音：端点是 recognizer 自己算出来的，那一刻文本已经定稿；
        再往流里塞零会把解码状态搅乱（实测会吐 "</s>" 和重复词）。
        """
        text = self.result()
        self.reset()
        return text

    def reset(self):
        """只重置识别状态，音频流本身不断（这是官方示例的关键做法）"""
        try:
            self.rec.reset(self.stream)
        except Exception:
            self.stream = self.rec.create_stream()
        self._t = 0.0

    @property
    def elapsed(self):
        return self._t
