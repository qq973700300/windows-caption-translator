# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（流式方案，纯 CPU）

策略：只打必需运行时，不打包重型可选依赖
  - 识别：sherpa-onnx 流式 Paraformer（int8，CPU 即可跑满实时），不需要 CUDA
  - VAD：Silero ONNX 模型（2MB）随包带上
  - 不打包流式识别模型（约 240MB）：首次运行时按需下载到 models/
"""
import os

from PyInstaller.utils.hooks import collect_all

datas = [("realtime_subtitle/assets/silero_vad.onnx", "assets")]
binaries = []
hiddenimports = [
    "soundcard",
    "cffi",
    "sherpa_onnx",
    "onnxruntime",
    "huggingface_hub",      # 首次运行下载流式模型用
    "deep_translator",
    "requests",
    "numpy",
]

# 收集这些包的全部数据文件与动态库（.dll / .pyd）
for _pkg in ("soundcard", "sherpa_onnx", "onnxruntime"):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception as _e:  # 包不存在时跳过，不阻断打包
        print("collect_all skip %s: %s" % (_pkg, _e))

# 明确排除：旧方案残留与体积大户
excludes = [
    "nvidia", "torch", "faster_whisper", "ctranslate2", "tokenizers", "av",
    "scipy", "matplotlib", "pandas", "PIL",
    "pytest", "pyttsx3", "IPython", "notebook", "jupyter",
]

block_cipher = None

a = Analysis(
    ["realtime_subtitle/main.py"],
    pathex=["realtime_subtitle"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SubtitleTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 程序，不弹控制台；运行日志写 subtitle.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SubtitleTranslator",
)
