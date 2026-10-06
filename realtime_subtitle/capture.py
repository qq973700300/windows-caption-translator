# -*- coding: utf-8 -*-
"""
系统音频捕获 + 能量 VAD 切句
从系统正在播放的声音（WASAPI loopback）持续采集，
检测到一段完整语音（出现静音边界）后作为一个片段送出。
"""
import threading
import time
import queue
from collections import deque

import numpy as np
import soundcard as sc

# 采集参数
CAPTURE_SR = 48000          # 采集采样率（大多数声卡为 48kHz）
TARGET_SR = 16000           # whisper 需要的采样率
BLOCK_MS = 100              # 每次采集 100ms
BLOCK_FRAMES = CAPTURE_SR * BLOCK_MS // 1000

# VAD 参数（能量阈值法）
SILENCE_EXIT_FRAMES = 4     # 连续 4 帧静音（约 0.4s）=> 一句话结束（原 0.8s，降低延迟）
SPEECH_ENTRY_FRAMES = 2     # 连续 2 帧有声 => 语音开始
MIN_SEGMENT_FRAMES = 5      # 语音段最短 0.5s，否则丢弃（过滤 "in the" 之类碎片）
PREROLL_FRAMES = 3          # 语音起始前补回 3 帧（0.3s），避免句首第一个词被吃掉
MAX_SEGMENT_FRAMES = 90     # 语音段最长 9s，强制切分（避免长段连成一串）

# 自适应噪声底噪（关键：视频常有背景音乐，固定阈值会永远判定为"有声"）
BASE_THRESHOLD = 0.010      # 绝对最小阈值
NOISE_FACTOR = 1.7          # 阈值至少为背景底噪的 1.7 倍
NOISE_WINDOW = 100          # 底噪统计窗口（100 帧 = 10s）
NOISE_PERCENTILE = 20       # 取窗口内 20 分位作为底噪（对连续说话鲁棒）
MAX_THRESHOLD = 0.08        # 阈值上限，避免底噪很大时完全切不出句子

# 实时预览（边说边出字幕）
PREVIEW_FRAMES = 15         # 语音进行中每 1.5s 出一次"部分结果"
PREVIEW_WINDOW = 60         # 预览取最近 6s 音频作为上下文


def list_output_devices():
    """枚举可用的输出设备（用于 loopback 采集）"""
    return sc.all_speakers()


class AudioSegmenter(threading.Thread):
    """后台线程：loopback 采集 + VAD 切句，产出 (pcm_float32_16k) 片段队列"""

    def __init__(self, out_queue, sensitivity=1.0, device_id=None,
                 silence_timeout=1.2, preview=True):
        super().__init__(daemon=True)
        self.out_queue = out_queue
        self.sensitivity = float(sensitivity)   # 灵敏度：阈值 = BASE / sensitivity
        self.device_id = device_id
        self.silence_timeout = silence_timeout  # 完全无声多久后放弃当前段
        self.preview = bool(preview)            # 是否边说边出部分结果
        self._stop = threading.Event()
        self.level_rms = 0.0                    # 最近一帧 RMS，供 UI 显示音量条
        # VAD 状态（抽成实例状态，便于离线喂数据做单元测试）
        self._noise_hist = deque(maxlen=NOISE_WINDOW)
        self._preroll = deque(maxlen=PREROLL_FRAMES)   # 语音起始前补回的音频
        self._in_speech = False
        self._speech_buf = []
        self._speech_frames = 0
        self._silence_run = 0
        self._speech_run = 0
        # 供测试/调试观察每次切句
        self.on_flush = None

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    def _noise_floor(self):
        """最近 10s 内的背景底噪（20 分位），0 表示尚未统计足够样本"""
        if len(self._noise_hist) < 10:
            return 0.0
        return float(np.percentile(np.fromiter(self._noise_hist, dtype=np.float32),
                                   NOISE_PERCENTILE))

    def current_threshold(self):
        """当前判定阈值：随背景噪声自适应上浮"""
        th = max(BASE_THRESHOLD, self._noise_floor() * NOISE_FACTOR)
        th /= max(self.sensitivity, 0.05)
        return min(th, MAX_THRESHOLD)

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
        self._noise_hist.append(rms)
        self._preroll.append(mono)
        threshold = self.current_threshold()

        if not self._in_speech:
            # 等待语音开始
            if rms >= threshold:
                self._speech_run += 1
                if self._speech_run >= SPEECH_ENTRY_FRAMES:
                    self._in_speech = True
                    # 连同判定前的几帧一起收进来，否则句首第一个词会被切掉
                    self._speech_buf = list(self._preroll)
                    self._speech_frames = len(self._speech_buf)
                    self._silence_run = 0
            else:
                self._speech_run = 0
            return

        # 语音段进行中
        if rms >= threshold:
            self._speech_buf.append(mono)
            self._speech_frames += 1
            self._silence_run = 0
            # 实时预览：每积累 PREVIEW_FRAMES 帧就先出一次部分结果
            if self.preview and self._speech_frames % PREVIEW_FRAMES == 0:
                self._flush(self._speech_buf, self._speech_frames, partial=True)
            if self._speech_frames >= MAX_SEGMENT_FRAMES:
                self._flush(self._speech_buf, self._speech_frames)
                self._reset()
        else:
            self._silence_run += 1
            self._speech_buf.append(mono)          # 静音帧也保留，保证句子完整
            self._speech_frames += 1
            if self._silence_run >= SILENCE_EXIT_FRAMES:
                self._flush(self._speech_buf, self._speech_frames)
                self._reset()

    def _reset(self):
        self._speech_buf, self._speech_frames, self._in_speech = [], 0, False
        self._speech_run, self._silence_run = 0, 0

    def _capture_loop(self, rec):
        """采集主循环（rec 为已进入的 recorder 上下文）"""
        while not self._stop.is_set():
            try:
                block = rec.record(numframes=BLOCK_FRAMES)
            except Exception:
                time.sleep(0.1)
                continue
            self.feed_block(block.mean(axis=1).astype(np.float32))

    def _flush(self, buf, frames, partial=False):
        """把积累的语音段送出识别队列
        partial=True 表示这是"边说边出"的部分结果，后面还会有完整版"""
        if frames < MIN_SEGMENT_FRAMES:
            return
        # 预览只取最近 PREVIEW_WINDOW 帧，避免无限增长
        audio = np.concatenate(buf[-PREVIEW_WINDOW:] if partial else buf, axis=0)
        pcm = self._downsample_to_16k(audio)
        peak = float(np.max(np.abs(pcm)) + 1e-9)
        if peak < 0.004:      # 几乎无声（底噪/纯静音）-> 直接丢弃，避免识别成乱码
            return
        # 归一化：最多放大 4 倍，否则静音段底噪会被放大成"语音"导致幻觉
        gain = min(0.98 / peak, 4.0)
        if gain > 1.0:
            pcm = np.clip(pcm * gain, -1.0, 1.0).astype(np.float32)
        if self.on_flush:                      # 测试/调试钩子
            try:
                self.on_flush(pcm, partial)
            except Exception:
                pass
        try:
            self.out_queue.put_nowait((pcm, partial))
        except queue.Full:
            # 队列满：丢掉最旧的一段，保留最新
            try:
                self.out_queue.get_nowait()
                self.out_queue.put_nowait((pcm, partial))
            except Exception:
                pass

    # ------------------------------------------------------------------
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
            import logging
            logging.getLogger("subtitle").error("音频捕获失败: %s", e)
