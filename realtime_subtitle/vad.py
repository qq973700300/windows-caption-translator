# -*- coding: utf-8 -*-
"""
Silero VAD（ONNX 版）

为什么要用它替代能量阈值：
  能量 VAD 只看音量大小，视频有背景音乐时音量永远降不到阈值以下，
  只能靠"自适应底噪"勉强猜，遇到低语音/音乐交叉就会切错。
  Silero 是一个 2MB 的神经网络，直接输出"这段是人声"的概率，
  对音乐、噪声天然鲁棒。

依赖只有 onnxruntime（约 40MB），不依赖 torch。
模型文件随仓库提供：assets/silero_vad.onnx
"""
import logging
import os
import sys

import numpy as np

logger = logging.getLogger("subtitle")

_SILERO_SR = 16000
# 注意：silero v6 的窗口是 576 点（36ms），不是网上常见的 512（那是 v4/v5）。
# 喂 512 点模型会输出恒 ~0 的概率（曾排查过：512 -> 0.0006，576 -> 0.8754）。
_SILERO_WINDOW = 576
_STATE_SHAPE = (2, 1, 128)    # LSTM 状态维度


def model_path():
    """ONNX 模型位置

    源码运行：与模块同级的 assets/
    打包运行：_MEIPASS 下（PyInstaller 里 __file__ 不一定指向真实目录），
              再退到 exe 同级 assets/
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, "assets", "silero_vad.onnx")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(os.path.join(meipass, "assets", "silero_vad.onnx"))
        cands.append(os.path.join(meipass, "realtime_subtitle", "assets",
                                  "silero_vad.onnx"))
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(os.path.dirname(sys.executable), "assets",
                                  "silero_vad.onnx"))
    for p in cands:
        if os.path.isfile(p):
            return p
    return cands[0]


def available():
    """onnxruntime 与模型文件是否都就绪"""
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return os.path.isfile(model_path())


class SileroVAD:
    """按 36ms 窗口输出人声概率；调用方按块（如 100ms）聚合使用"""

    def __init__(self, threshold=0.5, sr=_SILERO_SR):
        import onnxruntime as ort
        path = model_path()
        self.sr = sr
        self.threshold = float(threshold)
        opts = ort.SessionOptions()
        opts.log_severity_level = 3          # 屏蔽 onnxruntime 的冗余日志
        self.session = ort.InferenceSession(
            path, sess_options=opts, providers=["CPUExecutionProvider"])
        self.sr_arr = np.array(sr, dtype=np.int64)
        self.reset()

    def reset(self):
        """重置 LSTM 状态（新的一句话开始时调用）"""
        self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._carry = np.zeros(0, dtype=np.float32)

    def feed(self, pcm16):
        """喂入任意长度的 16k 单声道音频，返回每个 32ms 窗口的人声概率列表"""
        if pcm16 is None or len(pcm16) == 0:
            return []
        buf = np.concatenate([self._carry, np.asarray(pcm16, dtype=np.float32)])
        n = len(buf) // _SILERO_WINDOW * _SILERO_WINDOW
        self._carry = buf[n:]
        probs = []
        for start in range(0, n, _SILERO_WINDOW):
            x = buf[start:start + _SILERO_WINDOW].reshape(1, -1)
            out, state_n = self.session.run(
                None, {"input": x, "state": self._state, "sr": self.sr_arr})
            self._state = state_n
            probs.append(float(out.reshape(-1)[0]))
        return probs

    def is_speech(self, pcm16):
        """块级判定：块内窗口概率的均值是否过阈值
        用均值而非"任一窗口过阈值"，避免单个尖峰导致误判"""
        probs = self.feed(pcm16)
        if not probs:
            return False, 0.0
        mean_p = float(np.mean(probs))
        return mean_p >= self.threshold, mean_p
