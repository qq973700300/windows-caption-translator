# -*- coding: utf-8 -*-
"""预下载 whisper small 模型（走国内镜像），使首次启动即用"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from faster_whisper import WhisperModel

print("downloading small model via", os.environ["HF_ENDPOINT"])
m = WhisperModel("small", device="cpu", compute_type="int8")
print("small model ready")

# base 模型也一并下载，作为快速档位
m = WhisperModel("base", device="cpu", compute_type="int8")
print("base model ready")
