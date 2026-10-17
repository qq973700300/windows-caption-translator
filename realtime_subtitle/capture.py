# -*- coding: utf-8 -*-
"""
系统音频捕获（WASAPI loopback）

只做一件事：把系统正在播放的声音切成 100ms 的块，原样往上送。
**不再切句**——句末判定交给 sherpa-onnx 的 recognizer（见 asr_stream.py），
这是官方示例的做法：音频流永不断开，切句只是"标记 + 重置识别状态"。

以前这里有一套自写的状态机（环形缓冲 + 预卷 + 手动 start/end 事件），
它会导致两个问题：
  1. 触发判定的那一块音频被丢掉，句首出现 100ms 空洞 -> 识别出乱码
  2. 切句阈值（0.4s 静音）比官方默认（1.2s）激进 3 倍，半句就被切碎
所以整段删掉了，只留采集 + 音量/静音监测（给 UI 和"选错设备"提示用）。

Silero VAD 仍然保留，但只用于两件事：
  · 给 UI 显示人声概率
  · 统计"连续多久没人声"，供长句在自然停顿处收尾、以及换片时重置上下文
它不再决定音频怎么切块。
"""
import logging
import threading
import time

import numpy as np
import soundcard as sc

CAPTURE_SR = 48000          # 采集采样率（大多数声卡为 48kHz）
TARGET_SR = 16000           # 识别模型采样率（重采样由 sherpa-onnx 内部完成）
BLOCK_MS = 100              # 每次采集 100ms
BLOCK_FRAMES = CAPTURE_SR * BLOCK_MS // 1000
SILENT_RMS = 1e-4           # 低于此值视为"完全没有声音"


def list_output_devices():
    """枚举可用的输出设备（用于 loopback 采集）"""
    return sc.all_speakers()


class AudioCapture(threading.Thread):
    """后台线程：loopback 采集 -> 100ms 块 -> 队列"""

    def __init__(self, audio_queue, device_id=None, use_vad=True):
        super().__init__(daemon=True)
        self.audio_queue = audio_queue
        self.device_id = device_id
        self.level_rms = 0.0        # 最近一块的 RMS（UI 音量条）
        self.speech_prob = 0.0      # Silero 输出的人声概率（仅用于观察）
        self.trailing_silence = 0.0  # 连续多少秒没人声
        self.silent_seconds = 0.0    # 连续多少秒完全没声音（选错设备的信号）
        self._quiet_run = 0
        self._speech_run = 0
        self._stop = threading.Event()
        self._silero = None
        if use_vad:
            try:
                from vad import SileroVAD, available
                if available():
                    self._silero = SileroVAD(threshold=0.5)
            except Exception as e:
                logging.getLogger("subtitle").warning("VAD 不可用（不影响识别）: %s", e)

    def stop(self):
        self._stop.set()

    @property
    def vad_mode(self):
        return "silero" if self._silero else "rms"

    # ------------------------------------------------------------------
    def feed_block(self, mono48):
        """喂一个 100ms 的 48k 单声道块；原样送队列，不做任何切分"""
        self.level_rms = float(np.sqrt(np.mean(mono48 * mono48)) + 1e-12)
        self._quiet_run = self._quiet_run + 1 if self.level_rms < SILENT_RMS else 0
        self.silent_seconds = self._quiet_run * BLOCK_MS / 1000.0

        if self._silero is not None:
            n = len(mono48) // 3 * 3
            mono16 = mono48[:n].reshape(-1, 3).mean(axis=1).astype(np.float32)
            try:
                probs = self._silero.feed(mono16)
                if probs:
                    self.speech_prob = float(np.mean(probs))
                    if self.speech_prob < self._silero.threshold:
                        self._speech_run += 1
                    else:
                        self._speech_run = 0
            except Exception:
                pass
        elif self.level_rms < SILENT_RMS * 30:
            self._speech_run += 1
        else:
            self._speech_run = 0
        self.trailing_silence = self._speech_run * BLOCK_MS / 1000.0

        try:
            self.audio_queue.put_nowait(("audio", mono48))
        except Exception:
            # 队列满说明下游卡住了：丢掉这一块，绝不让采集线程阻塞
            pass

    # ------------------------------------------------------------------
    def run(self):
        try:
            if self.device_id:
                mic = sc.get_microphone(self.device_id, include_loopback=True)
            else:
                spk = sc.default_speaker()
                mic = sc.get_microphone(spk.id, include_loopback=True)
            with mic.recorder(samplerate=CAPTURE_SR, channels=2) as rec:
                while not self._stop.is_set():
                    try:
                        block = rec.record(numframes=BLOCK_FRAMES)
                    except Exception:
                        time.sleep(0.1)
                        continue
                    self.feed_block(block.mean(axis=1).astype(np.float32))
        except Exception as e:
            logging.getLogger("subtitle").error("音频捕获失败: %s", e)
