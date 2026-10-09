# -*- coding: utf-8 -*-
"""
系统音频捕获（WASAPI loopback）+ Silero VAD 切句

只做两件事：
  1. 持续采集系统正在播放的声音，切成 100ms 的块降采样到 16k
  2. 用 Silero VAD 判断"有没有人在说话"，把一句话的边界告诉下游

音频不再攒成整段再送出，而是按块持续推给流式识别引擎，
下游拿到的事件是：("start", 预卷块) / ("audio", 16k块) / ("end", None) / ("cancel", None)
"""
import logging
import threading
import time
import queue
from collections import deque

import numpy as np
import soundcard as sc

# 采集参数
CAPTURE_SR = 48000          # 采集采样率（大多数声卡为 48kHz）
TARGET_SR = 16000           # 识别模型需要的采样率
BLOCK_MS = 100              # 每次采集 100ms
BLOCK_FRAMES = CAPTURE_SR * BLOCK_MS // 1000

# 切句参数（单位：100ms 帧）
SPEECH_ENTRY_FRAMES = 2     # 连续 2 帧有人声 => 一句话开始
SILENCE_EXIT_FRAMES = 4     # 连续 4 帧无人声（0.4s）=> 一句话结束
MIN_SEGMENT_FRAMES = 5      # 短于 0.5s 的段丢弃（过滤误触发的碎片）
MAX_SEGMENT_FRAMES = 50     # 单句最长 5s 强制切分：连续说话不停顿时也能及时出一次译文
                            # （原来是 9s，说话密时会憋很久才看到翻译）
RING16_FRAMES = 8           # 最近 8 个 16k 块（0.8s）作为预卷缓冲
SILENT_RMS = 1e-4           # 低于此值视为"没声音"（语音通常 0.01~0.2，静音底噪约 1e-12）


def list_output_devices():
    """枚举可用的输出设备（用于 loopback 采集）"""
    return sc.all_speakers()


class AudioSegmenter(threading.Thread):
    """后台线程：loopback 采集 + Silero VAD 切句，把音频块推进流式队列"""

    def __init__(self, stream_queue, sensitivity=1.0, device_id=None):
        super().__init__(daemon=True)
        # 元素为 ("start", [预卷16k块...]) / ("audio", 16k块) / ("end", None) / ("cancel", None)
        self.stream_queue = stream_queue
        self.sensitivity = float(sensitivity)   # 灵敏度：越大 -> 判定阈值越低 -> 越灵敏
        self.device_id = device_id
        self._stop = threading.Event()
        self.level_rms = 0.0                    # 最近一帧 RMS，供 UI 显示音量条
        self.speech_prob = 0.0                  # Silero 输出的人声概率（供 UI 观察）
        # 切句状态（抽成实例状态，便于离线喂数据做单元测试）
        self._ring16 = deque(maxlen=RING16_FRAMES)     # 最近 0.8s 的 16k 块
        self._in_speech = False
        self._speech_frames = 0
        self._silence_run = 0
        self._speech_run = 0
        self._silent_run = 0        # 连续"没声音"的帧数（用于提示选错了设备）
        self._silero = self._init_silero()
        # 供测试/调试观察每次切句：on_event(kind)
        self.on_event = None

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    def _init_silero(self):
        """初始化 Silero VAD；模型/onnxruntime 缺失时直接抛错（无旧方案回退）"""
        from vad import SileroVAD, available
        if not available():
            raise RuntimeError(
                "Silero VAD 不可用：缺少 onnxruntime 或 assets/silero_vad.onnx")
        thr = min(0.9, max(0.15, 0.5 / max(self.sensitivity, 0.05)))
        return SileroVAD(threshold=thr)

    @property
    def vad_mode(self):
        return "silero"

    @property
    def silent_seconds(self):
        """已经连续多少秒没采集到声音（供 UI 判断是不是选错了设备）"""
        return self._silent_run * BLOCK_MS / 1000.0

    # ------------------------------------------------------------------
    @staticmethod
    def _downsample_to_16k(mono):
        """48k -> 16k：每 3 点取均值池化（整数比，质量足够）"""
        n = len(mono) // 3 * 3
        return mono[:n].reshape(-1, 3).mean(axis=1).astype(np.float32)

    def feed_block(self, mono):
        """喂入一个 100ms 的单声道块（float32 @ CAPTURE_SR），推进 VAD 状态机
        抽成独立方法：既被采集循环调用，也方便离线喂数据做断句测试"""
        rms = float(np.sqrt(np.mean(mono * mono)) + 1e-12)
        self.level_rms = rms
        # 持续没声音通常不是"视频没在播"，而是选错了音频来源
        # （选中的设备并非真正在出声的那个），UI 据此给出提示
        self._silent_run = self._silent_run + 1 if rms < SILENT_RMS else 0
        mono16 = self._downsample_to_16k(mono)
        self._ring16.append(mono16)
        speech, prob = self._silero.is_speech(mono16)
        self.speech_prob = prob

        if not self._in_speech:
            # 等待一句话开始
            if speech:
                self._speech_run += 1
                if self._speech_run >= SPEECH_ENTRY_FRAMES:
                    self._in_speech = True
                    self._speech_frames = 0
                    self._silence_run = 0
                    # 把判定前 0.8s 的音频作为预卷补进去，否则句首第一个词会被吃掉
                    preroll = list(self._ring16)[:-1]
                    self._stream_put(("start", preroll))
                    self._notify("start")
            else:
                self._speech_run = 0
            return

        # 一句话进行中：每个块都立刻送出去，不等句末
        self._stream_put(("audio", mono16))
        self._speech_frames += 1
        if speech:
            self._silence_run = 0
            if self._speech_frames >= MAX_SEGMENT_FRAMES:
                self._end_utterance()      # 太长，强制切一刀
        else:
            self._silence_run += 1
            if self._silence_run >= SILENCE_EXIT_FRAMES:
                self._end_utterance()

    # ------------------------------------------------------------------
    def _end_utterance(self):
        """结束当前这句话：够长就定稿，太短判定为误触发则丢弃"""
        long_enough = self._speech_frames >= MIN_SEGMENT_FRAMES
        self._stream_put(("end" if long_enough else "cancel", None))
        self._notify("end" if long_enough else "cancel")
        self._speech_frames = 0
        self._speech_run = 0
        self._silence_run = 0
        self._in_speech = False
        self._silero.reset()      # 一句话结束，清空 LSTM 状态

    def _stream_put(self, item):
        try:
            self.stream_queue.put_nowait(item)
        except Exception:
            try:
                self.stream_queue.get_nowait()      # 满了就丢最旧的，保证实时性
                self.stream_queue.put_nowait(item)
            except Exception:
                pass

    def _notify(self, kind):
        if self.on_event:
            try:
                self.on_event(kind)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _capture_loop(self, rec):
        """采集主循环（rec 为已进入的 recorder 上下文）"""
        while not self._stop.is_set():
            try:
                block = rec.record(numframes=BLOCK_FRAMES)
            except Exception:
                time.sleep(0.1)
                continue
            self.feed_block(block.mean(axis=1).astype(np.float32))

    def run(self):
        try:
            if self.device_id:
                mic = sc.get_microphone(self.device_id, include_loopback=True)
            else:
                spk = sc.default_speaker()
                mic = sc.get_microphone(spk.id, include_loopback=True)
            # 必须进入 recorder 上下文采集（直接 mic.record 无效）
            with mic.recorder(samplerate=CAPTURE_SR, channels=2) as rec:
                self._capture_loop(rec)
        except Exception as e:
            logging.getLogger("subtitle").error("音频捕获失败: %s", e)
