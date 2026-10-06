# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（CPU 精简版）

策略：只打必需运行时，不打包重型可选依赖
  - 排除 nvidia CUDA 运行库（cublas 737M + cudnn 1.1G）：需要 GPU 的用户走源码模式自行安装
  - 不打包 Whisper 模型：首次运行时按需下载到用户缓存目录
  - 源码模式（install.bat）仍具备完整的 GPU 加速能力
"""
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = [
    "soundcard",
    "cffi",
    "ctranslate2",
    "faster_whisper",
    "av",
    "tokenizers",
    "huggingface_hub",
    "deep_translator",
    "requests",
    "numpy",
]

# 收集这些包的全部数据文件与动态库（.dll / .pyd）
for _pkg in ("soundcard", "ctranslate2", "faster_whisper", "av", "tokenizers"):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception as _e:  # 包不存在时跳过，不阻断打包
        print("collect_all skip %s: %s" % (_pkg, _e))

# 明确排除：体积大且 CPU 版用不到
excludes = [
    "nvidia", "torch", "scipy", "matplotlib", "pandas", "PIL",
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

# 剔除被误收进来的 CUDA 运行库（若存在），控制体积
import os
import shutil

_SPEC_DIR = globals().get("SPECPATH") or os.getcwd()
_DIST = os.path.join(_SPEC_DIR, "dist", "SubtitleTranslator")
_BIG = ("cublas", "cudnn", "cufft", "curand", "cusparse", "cusolver", "nvcuvid")
_removed = []
for _root, _dirs, _files in os.walk(_DIST):
    for _f in list(_files):
        _low = _f.lower()
        if _low.endswith(".dll") and any(_b in _low for _b in _BIG):
            try:
                os.remove(os.path.join(_root, _f))
                _removed.append(_f)
            except Exception:
                pass
if _removed:
    print("removed CUDA dlls: %d files" % len(_removed))
