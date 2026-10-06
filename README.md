# 实时视频翻译字幕（Windows）

> Live subtitle translator for Windows: captures whatever your PC is playing, transcribes it locally with Whisper, translates it to Chinese, and shows it in a pinned floating caption bar.

播放视频时，把画面里的人说话**实时变成中文字幕**，浮在屏幕底部。
本地离线识别，不依赖任何在线字幕服务，任意播放器 / 网页视频都能用。

![流程](https://img.shields.io/badge/pipeline-capture%20%E2%86%92%20ASR%20%E2%86%92%20translate%20%E2%86%92%20overlay-blue)
![平台](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![识别](https://img.shields.io/badge/ASR-faster--whisper%20(offline)-green)
![加速](https://img.shields.io/badge/GPU-CUDA%20optional-orange)

---

## 一、快速开始

### 方式 A：下载打包版（免装 Python，推荐）

1. 到 [Releases](https://github.com/qq973700300/windows-caption-translator/releases) 下载 `SubtitleTranslator_v2.0.0.zip`
2. 解压到任意文件夹（建议路径不要含中文）
3. 双击 **`SubtitleTranslator.exe`** → 点「▶ 开始翻译」

> - 体积约 **250MB**（Python 运行时 + 识别引擎），**不含** CUDA 运行库与识别模型
> - **识别模型首次运行时自动下载**（约 100~500MB，走国内镜像），之后缓存在本地
> - 打包版运行在 CPU（识别 0.6~0.8 秒，日常够用）；**要 GPU 加速请用方式 B**

### 方式 B：源码运行（支持 GPU 加速）

#### 1. 安装依赖

双击 **`install.bat`** 即可（自动创建 `.venv`、安装依赖、预下载识别模型）。

或手动执行：

```bash
python -m venv .venv
.venv\Scripts\pip install -r realtime_subtitle\requirements.txt
```

> 有 NVIDIA 显卡时安装脚本会自动装 CUDA 运行库（`nvidia-cublas-cu12` / `nvidia-cudnn-cu12`），识别速度更快。

#### 2. 启动

双击 **`run.bat`** → 弹出控制面板。

### 3. 开始翻译

点击 **「▶ 开始翻译」**（首次加载模型约 1~8 秒），然后正常播放视频即可。

屏幕底部会出现**置顶半透明字幕条**：上方小字是识别原文，下方黄色大字是中文翻译。

| 字幕条操作 | 说明 |
|---|---|
| 按住拖动 | 移动到任意位置，位置会自动保存 |
| 双击 | 关闭字幕条 |
| 自动淡出 | 静音 9 秒后自动隐藏，有声音再出现 |

---

## 二、参数调节说明

控制面板各选项的作用与推荐值：

| 选项 | 作用 | 推荐值 |
|---|---|---|
| **识别模型** | 精度与速度的权衡 | `base（快，约 0.6s）` 追低延迟；`small（推荐，约 1.6s）` 平衡；`medium` 精度最高但慢 |
| **运行设备** | 用显卡还是 CPU 做识别 | `自动（优先 GPU）`。GPU 首次初始化约 8 秒；显卡被其他程序占满时可在界面看到提示，改选 CPU 即可。**打包版无 CUDA 运行库，会自动回退到 CPU** |
| **音频来源** | 采集哪个输出设备的声音 | 默认「系统默认输出设备」。**用耳机看视频时必须切换到耳机设备**，否则抓不到声音 |
| **视频语言** | 指定源语言可提升识别准确率 | `自动检测`；明确知道时选 `英语 / 日语 / 韩语` 等更准 |
| **翻译引擎** | 中文翻译走哪个通道 | `自动（免费在线回退链）`：腾讯 Transmart → MyMemory → Google 依次回退；也有 `LLM API`（OpenAI 兼容接口，质量更好，需填 Key） |
| **识别灵敏度** | 判定"有人在说话"的音量阈值 | 默认 `1.0`。**背景音乐大导致乱出字幕就调低**；说话声音小导致漏字就调高 |
| **实时预览** | 边说边出字幕（无需等整句说完） | 默认**开启**。关闭则只在句子说完后出一次完整字幕 |

### 关于延迟

典型延迟 **2 秒左右**：1.5 秒切片 + 识别 0.3~0.8 秒 + 翻译 0.3 秒。这是 Whisper 类模型的物理下限。

想再快：把模型切到 `base`、运行设备选 GPU。

---

## 三、常见问题排查

| 现象 | 原因 | 解决办法 |
|---|---|---|
| **双击 run.bat 没反应** | Python 环境缺 `tkinter`，而 `pythonw` 静默吞掉报错 | 跑 **`install.bat`** 用自带 tkinter 的 Python 重建环境；或双击 **`run_debug.bat`**（带控制台，会显示具体报错） |
| **完全没有字幕** | 抓的是系统声音，没抓到你听的那个设备 | 控制面板「音频来源」切换到你正在用的输出设备（耳机 / 扬声器 / HDMI） |
| **背景音乐、歌词被当成台词** | loopback 抓的是整个系统声音，这是该方案的固有特性 | 调低「识别灵敏度」 |
| **字幕延迟大** | 模型偏大或跑在 CPU 上 | 模型切 `base`；确认「运行设备」显示的是 GPU |
| **出现乱码 / 奇怪外文** | 静音或噪声上的识别幻觉 | 已内置四道过滤（静音丢弃、放大上限、重复字符检测、跨语言识别）。仍有残留就调低灵敏度 |
| **字幕连成一长串不断句** | 背景音乐导致静音检测失效 | 已内置自适应底噪阈值 + 无标点时按长度兜底切块。极吵的音源可调低灵敏度 |
| **句首第一个词被吃掉** | 语音起始判定丢帧 | 已用预卷缓冲修复；如仍出现请反馈 |
| **GPU 报错 / 卡住** | 缺 CUDA 运行库，或显存被占满 | 跑 `install.bat` 补装运行库；或在「运行设备」改选 CPU |

运行日志与配置保存在**程序所在目录**下（`subtitle.log` / `config.json`）；源码运行时在 `realtime_subtitle\` 目录。

---

## 四、工作原理与技术架构

```
视频/播放器 ──► 系统声音(WASAPI loopback) ──► VAD 切句 ──► Whisper 识别 ──► 翻译 ──► 悬浮字幕
                      capture.py                       transcribe.py    translate.py   main.py
```

| 环节 | 实现 | 说明 |
|---|---|---|
| **声音捕获** | `capture.py` · WASAPI loopback | 直接抓系统正在播放的声音，本地播放器、B站、腾讯视频、Netflix 都适用 |
| **切句（VAD）** | `capture.py` · 自适应能量阈值 | 取最近 10 秒音量的 20 分位作底噪，阈值 = 底噪 × 1.7；静音 0.4 秒判定句末，最长 9 秒强切 |
| **实时预览** | 语音进行中每 1.5 秒出一次部分结果 | 边说边显示，句子说完再刷新成完整版；队列防积压，识别跟不上时只处理最新片段 |
| **断句分块** | `transcribe.py` · `group_pieces()` | 优先按句末标点收句；中文识别常无标点，故按显示权重兜底切（约 30 汉字 / 60 西文字符一条） |
| **语音识别** | faster-whisper（本地离线） | 支持自动检测 英 / 日 / 韩 / 法 / 俄 等；GPU 用 float16，CPU 用 int8 量化 |
| **翻译** | `translate.py` · 多引擎回退链 | 腾讯 Transmart（国内直连、免 Key、质量好）→ MyMemory → Google；也支持 OpenAI 兼容的 LLM 接口 |
| **界面** | `main.py` · tkinter | 控制面板 + 无边框置顶半透明字幕窗，可拖动、自动淡出 |

### 幻觉治理

在静音/噪声上 Whisper 容易产出乱码（重复的 `වවවව`、凭空的韩语/俄语、`谢谢观看` 之类）。已加四道过滤：

1. 静音片段（峰值 < 0.004）直接丢弃，归一化最多放大 4 倍
2. `compression_ratio_threshold=2.0` 过滤重复型输出
3. 单字符占比 > 60% 判定为垃圾文本
4. 与本次主语言不符且置信度 < 0.95 判为跨语言噪声

---

## 目录结构

```
.
├─ run.bat                 启动（GUI，无控制台）
├─ run_debug.bat           启动（带控制台，排错用）
├─ install.bat             一键重建环境 + 装依赖 + 下载模型
├─ build_exe.spec          PyInstaller 打包配置（CPU 精简版）
└─ realtime_subtitle/
   ├─ main.py              控制面板 + 悬浮字幕窗
   ├─ capture.py           系统声音采集 + VAD 切句
   ├─ transcribe.py        faster-whisper 识别 + 断句分块
   ├─ translate.py         多引擎翻译回退链
   ├─ download_model.py    预下载识别模型
   ├─ requirements.txt     依赖清单
   └─ test_*.py            测试脚本（断句/延迟/速度/GPU 基准）
```

### 测试脚本

| 脚本 | 用途 |
|---|---|
| `test_vad.py` | 离线 VAD 断句单测（合成信号，不依赖声卡） |
| `test_offline.py` | 离线整管道：喂音频 → 切句 → 识别 → 分块，打印实际字幕 |
| `test_sentences.py` | 真实声卡端到端：4 句话 + 背景音乐，验证逐句出字幕 |
| `test_realtime.py` | 实时性测试：记录字幕出现的时间线 |
| `bench_speed.py` / `bench_gpu.py` | 识别速度与 GPU/CPU 对比基准 |

---

## 自行打包 exe

需要重打打包版时（改了代码之后）：

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python -m PyInstaller build_exe.spec --noconfirm --clean
```

产物在 `dist\SubtitleTranslator\`，约 250MB，压缩后约 100MB。

打包策略（见 `build_exe.spec`）：

| 处理 | 说明 |
|---|---|
| 排除 `nvidia` / `torch` / `scipy` 等 | CUDA 运行库合计约 2GB，全打进去会撑爆 GitHub 单文件 2GB 限制 |
| 不打包 Whisper 模型 | 首次运行按需下载到用户缓存，避免体积翻倍 |
| `console=False` | GUI 程序不弹控制台，运行日志写 `subtitle.log` |
| 冻结后路径修正 | `main.py` 检测 `sys.frozen`，把 `config.json` / `subtitle.log` 落在 exe 同级目录而非临时目录 |

---

## 说明

- 需要 **Windows 10/11**（依赖 WASAPI loopback 采集系统声音）。
- 识别完全在本地完成，不上传音频；只有**翻译**环节会把识别出的文字发给在线翻译服务。
- 腾讯 Transmart / MyMemory / Google 均为免费接口，请合理使用、避免高频调用。
- 本项目仅供个人学习与辅助使用，观看受版权保护的内容请遵守相关规定。
