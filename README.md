# 实时视频翻译字幕（Windows）

> Live subtitle translator for Windows: captures whatever your PC is playing, transcribes it with a **streaming** on-device ASR, translates it to Chinese, and shows it in a pinned floating caption bar.

播放视频时，把画面里的人说话**实时变成中文字幕**，浮在屏幕底部。
识别完全在本地完成，任意播放器 / 网页视频都能用。

![流程](https://img.shields.io/badge/pipeline-capture%20%E2%86%92%20VAD%20%E2%86%92%20streaming%20ASR%20%E2%86%92%20translate-blue)
![平台](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![识别](https://img.shields.io/badge/ASR-sherpa--onnx%20Paraformer%20(streaming)-green)
![延迟](https://img.shields.io/badge/latency-%3C1s-orange)

---

## 一、快速开始

### 方式 A：下载打包版（免装 Python，推荐）

1. 到 [Releases](https://github.com/qq973700300/windows-caption-translator/releases) 下载最新的 `SubtitleTranslator_v*.zip`
2. 解压到任意文件夹（建议路径不要含中文）
3. 双击 **`SubtitleTranslator.exe`** → 点「▶ 开始翻译」

> - **流式识别模型（约 240MB）首次运行时自动下载**，走国内镜像，之后缓存在本地 `models\`
> - CPU 即可跑满实时（RTF ≈ 0.04，比实时快 25 倍），不需要 GPU

### 方式 B：源码运行

#### 1. 安装依赖

双击 **`install.bat`** 即可（自动创建 `.venv`、安装依赖、预下载流式模型）。

或手动执行：

```bash
python -m venv .venv
.venv\Scripts\pip install -r realtime_subtitle\requirements.txt
```

#### 2. 启动

双击 **`run.bat`** → 弹出控制面板。（排错时双击 `run_debug.bat`，带控制台看报错）

### 3. 开始翻译

点击 **「▶ 开始翻译」**（首次加载模型约 2~5 秒），然后正常播放视频即可。

屏幕底部会出现**置顶半透明字幕条**：上方小字是识别原文，下方黄色大字是中文翻译。
**字幕条尺寸固定**（原文 1 行 + 译文 2 行），长句短句都不会让画面上下跳动；超长时保留最新内容并在前面加 `…`。

| 字幕条操作 | 说明 |
|---|---|
| 按住拖动 | 移动到任意位置，位置会自动保存 |
| 双击 | 关闭字幕条 |
| 自动淡出 | 静音 9 秒后清空内容（框体仍在，不会消失后重排） |

---

## 二、参数调节说明

| 选项 | 作用 | 推荐值 |
|---|---|---|
| **音频来源** | 采集哪个输出设备的声音 | 默认「系统默认输出设备」。**用耳机看视频时必须切换到耳机设备**，否则抓不到声音 |
| **视频语言** | 指定源语言可提升翻译准确率 | `自动检测`；明确知道时选 `英语 / 日语 / 韩语` 等 |
| **翻译引擎** | 中文翻译走哪个通道 | `自动（免费在线回退链）`：腾讯 Transmart → MyMemory → Google 依次回退；也支持 `LLM API`（OpenAI 兼容接口，质量更好，需填 Key） |
| **识别灵敏度** | 判定"有人在说话"的严格程度 | 默认 `1.0`。**背景音乐导致乱出字幕就调低**；说话声音小导致漏字就调高 |
| **实时预览** | 边说边出字幕（不等整句说完） | 默认**开启**。关闭则只在句子说完后出一次完整字幕 |

### 关于延迟

流式方案不再需要"攒够一段音频再整段重跑"，每 100ms 喂一次音频、随时吐增量文字，
**首字延迟约 0.6~1 秒**，实测单块解码耗时远低于实时（RTF ≈ 0.04）。

定稿策略用 **LocalAgreement**：连续两次结果一致的前缀才算稳定，
所以原文只会**单向增长**，不会来回改字（离线实测回改 0 次）。
译文等这句话说完（VAD 判定句末）才一次性翻译并回填——**预览阶段只滚原文**，
避免"半句话先翻一遍、说完再翻一遍"导致的重复请求和译文跳变。

---

## 三、常见问题排查

| 现象 | 原因 | 解决办法 |
|---|---|---|
| **双击 run.bat 没反应** | Python 环境缺 `tkinter`，`pythonw` 静默吞掉报错 | 跑 **`install.bat`** 用自带 tkinter 的 Python 重建环境；或双击 **`run_debug.bat`** 看具体报错 |
| **完全没有字幕** | 抓的是系统声音，没抓到你听的那个设备 | 「音频来源」切换到你正在用的输出设备（耳机 / 扬声器 / HDMI）。状态栏会提示「⚠ 5 秒没采到声音」 |
| **原文在滚动但始终没有译文** | 翻译请求慢或全部失败 | 看状态栏「已翻译 M 句（待译 K）」；K 持续增大说明翻译跟不上，换更快的引擎或检查网络 |
| **提示缺少 sherpa-onnx / 模型下载失败** | 依赖没装或网络问题 | 跑 `install.bat`；或手动 `pip install sherpa-onnx` 后重开 |
| **背景音乐、歌词被当成台词** | loopback 抓的是整个系统声音，是该方案的固有特性 | 调低「识别灵敏度」 |
| **字幕偶尔重复词（a a / for for）** | 流式模型在极低信噪比下的常见现象 | 调低灵敏度；或关闭「实时预览」，只显示定稿整句 |
| **字幕连成一长串不断句** | 单句超过 9 秒未停顿 | 已内置 9 秒强切 + 无标点时按长度兜底分块 |
| **句首第一个词被吃掉** | 语音起始判定丢帧 | 已用 0.8 秒预卷缓冲修复 |

运行日志与配置保存在**程序所在目录**下（`subtitle.log` / `config.json`）；源码运行时在 `realtime_subtitle\` 目录。

---

## 四、工作原理与技术架构

```
视频/播放器 ──► 系统声音(loopback) ──► Silero VAD ──► 流式 Paraformer ──► LocalAgreement ──► 固定字幕窗
                    capture.py            vad.py        asr_stream.py       streaming.py       main.py
                                                                                  │
                                        一句话说完(VAD end) ──► 翻译队列 ──► 翻译线程 ──► 回填译文
                                                                transworker.py
```

| 环节 | 实现 | 说明 |
|---|---|---|
| **声音捕获** | `capture.py` · WASAPI loopback | 抓系统正在播放的声音，本地播放器、B站、腾讯视频、Netflix 都适用 |
| **切句（VAD）** | `vad.py` · Silero VAD（ONNX，2MB） | 神经网络直接输出"是人声"的概率，对背景音乐天然鲁棒；静音 0.4 秒判句末，最长 9 秒强切，短于 0.5 秒判误触发丢弃 |
| **流式识别** | `asr_stream.py` · sherpa-onnx Paraformer | 维护内部状态，每 100ms 音频吐一次增量；int8 量化，CPU 上 RTF ≈ 0.04 |
| **增量定稿** | `streaming.py` · LocalAgreement | 两次结果一致的公共前缀才算稳定；原文边说边滚，尾巴允许继续变化 |
| **断句分块** | `textutil.py` · `group_pieces()` | 优先按句末标点收句；中文常无标点，按显示权重兜底切（约 30 汉字 / 60 西文字符一条） |
| **翻译（独立线程）** | `transworker.py` + `translate.py` | 见下方「异步翻译」 |
| **界面** | `main.py` · tkinter | 控制面板 + 无边框置顶半透明字幕窗，尺寸固定、可拖动、自动淡出 |

### 异步翻译：ASR 永远不等翻译

架构底线。翻译是耗时不可控的网络请求（在线引擎 0.2~0.5s，LLM 0.5~2s，慢时可达 10s），
若同步调用会卡住整条流水线：音频块堆积 → `stream_queue`（256 块）塞满 → 丢音频 → 识别变差。

| 规则 | 说明 |
|---|---|
| 预览阶段只滚原文，不翻译 | 边说边出时只更新原文（含未定稿尾巴），译文留空等回填 |
| 一句 = 一次翻译请求 | 只在 VAD 判定「这句话说完了」时入队；旧版 committed 每增长一次就翻一次，一条长句会打 5~10 次 API |
| 投递非阻塞 | ASR 线程只 `put_nowait`，队列满则丢最旧的一条，绝不等待 |
| 结果带序号 | 慢翻译回来时若已有更新的句子上屏，过期结果直接丢弃，不会覆盖新字幕 |

状态栏会实时显示「已识别 N 句 / 已翻译 M 句（待译 K）」，可以直观看出翻译是否跟得上。

### 已移除的旧方案

早期版本用的是「能量阈值 VAD + Whisper 整段识别」，现已在代码与界面上**完全移除**，不再保留回退：

| 旧方案的问题 | 现在的做法 |
|---|---|
| 能量 VAD 只看音量，有 BGM 时永远判断不出静音，只能靠自适应底噪猜 | Silero VAD 神经网络判人声，BGM 下依然准确 |
| Whisper 是整段模型，必须攒 1.5 秒再整段重跑，延迟降不下去、算力 O(n²) | 流式 Paraformer 增量出字，首字约 0.6~1 秒 |
| 每次预览都整段重译、整行覆盖，字幕反复回改 | 预览只滚原文；一句话说完才翻译一次，译文一次到位 |
| 翻译同步调用会卡住整条流水线（音频堆积、丢帧） | 翻译独立线程 + 有界队列，ASR 永不等待 |

> 注：流式引擎跑在 CPU（int8 量化已足够快），因此**不再需要 CUDA 运行库**，
> 也省掉了 2GB 的 `nvidia-*` / `torch` 依赖。

---

## 目录结构

```
.
├─ run.bat                 启动（GUI，无控制台）
├─ run_debug.bat           启动（带控制台，排错用）
├─ install.bat             一键重建环境 + 装依赖 + 下载流式模型
├─ build_exe.spec          PyInstaller 打包配置
├─ models\                 流式识别模型（首次运行自动下载）
└─ realtime_subtitle/
   ├─ main.py              控制面板 + 固定尺寸悬浮字幕窗
   ├─ capture.py           系统声音采集 + Silero 切句
   ├─ vad.py               Silero VAD（ONNX）
   ├─ asr_stream.py        流式 Paraformer 识别
   ├─ streaming.py         LocalAgreement 增量定稿
   ├─ transworker.py       翻译工作线程（与 ASR 解耦，不阻塞采集）
   ├─ textutil.py          字幕分块 / 文本工具
   ├─ translate.py         多引擎翻译回退链
   ├─ assets\              silero_vad.onnx
   ├─ requirements.txt     依赖清单
   └─ test_*.py            测试脚本
```

### 测试脚本

| 脚本 | 用途 |
|---|---|
| `test_vad.py` | 离线切句单测（不依赖声卡）：BGM / 小声场景下的段数与段长 |
| `test_streaming.py` | 离线流式管道：音频 → 切句 → 流式识别 → 定稿，打印字幕时间线与回改次数 |
| `test_pipeline.py` | P0 回归：投递非阻塞 / 原文译文配对 / 背压丢弃 / 一句一次翻译 |
| `test_gui.py` | GUI 自测：字幕窗固定尺寸 + 端到端出字幕（并统计一句是否只翻译一次） |
| `make_test_wav.py` | 用 TTS 生成多句测试音频 |

---

## 自行打包 exe

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python -m PyInstaller build_exe.spec --noconfirm --clean
```

产物在 `dist\SubtitleTranslator\`。

打包策略（见 `build_exe.spec`）：

| 处理 | 说明 |
|---|---|
| 只收集 `soundcard` / `sherpa_onnx` / `onnxruntime` | 其余重型库全部 excluded，控制体积 |
| 打包 `assets/silero_vad.onnx` | VAD 模型只有 2MB，随包带上 |
| 不打包流式模型（240MB） | 首次运行按需下载到 `models\` |
| `console=False` | GUI 程序不弹控制台，运行日志写 `subtitle.log` |
| 冻结后路径修正 | `main.py` 检测 `sys.frozen`，把 `config.json` / `subtitle.log` 落在 exe 同级目录 |

---

## 说明

- 需要 **Windows 10/11**（依赖 WASAPI loopback 采集系统声音）。
- 识别完全在本地完成，不上传音频；只有**翻译**环节会把识别出的文字发给在线翻译服务。
- 腾讯 Transmart / MyMemory / Google 均为免费接口，请合理使用、避免高频调用。
- 本项目仅供个人学习与辅助使用，观看受版权保护的内容请遵守相关规定。
