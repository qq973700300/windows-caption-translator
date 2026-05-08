# Live Caption Translator Overlay

**English** · [中文](#实时辅助字幕翻译浮窗)

A small **Windows** desktop tool: it reads text from **Windows Live Captions** (实时辅助字幕), translates it to **Simplified Chinese** with Google Translate (via `deep-translator`), and shows the result in a **draggable, resizable, always-on-top** semi-transparent overlay.

---

## Features

- Polls the Live Captions window (title matches `.*实时辅助字幕.*`) using **UI Automation** (`pywinauto`).
- **Auto-detect** source language → **zh-CN** translation, with in-memory caching to reduce repeated API calls.
- When the same caption line **grows** (streaming recognition), the overlay **updates the last line** instead of stacking duplicates.
- Keeps a short history (up to **5** Chinese lines) in the overlay.
- **Frameless** window: drag anywhere (except edges) to move; drag **edges** to resize.

---

## Requirements

- **Windows 10/11** with **Live captions** (Settings → Accessibility → Captions, or equivalent path) turned on and showing the floating caption UI titled like **「实时辅助字幕」**.
- **Python 3.10+** (recommended; adjust if your environment differs).
- Internet access for **Google Translate** (used by `deep-translator`).

Install dependencies:

```bash
pip install PyQt6 pywinauto deep-translator
```

---

## How to use

1. Enable **Windows Live Captions** and make sure the caption window is visible (Chinese UI title typically contains **实时辅助字幕**).
2. Run:

   ```bash
   python translator_overlay.py
   ```

3. A dark semi-transparent bar appears; **drag** it to a comfortable position, **resize** from borders/corners if needed.
4. Spoken/audio captions should appear in English (or other languages) in the system caption window; this app shows **Chinese** in the overlay.

If nothing shows, confirm the caption window title still matches the pattern (you may need to adjust `title_re` in code if Microsoft changes the window title).

---

## Technical notes

- UI thread: **PyQt6** overlay; worker: **QThread** polling Static controls under the caption window.
- Translation: `GoogleTranslator(source="auto", target="zh-CN")` from **deep-translator**.
- Polling interval and history limits are constants at the top of `translator_overlay.py` (`_POLL_INTERVAL_SEC`, `_SUBTITLE_HISTORY_MAX`, etc.).

---

## Disclaimer

This tool is for personal accessibility / learning use. Respect **Google Translate** terms of use and rate limits. Not affiliated with Microsoft or Google.

---

# 实时辅助字幕翻译浮窗

一款 **Windows** 小工具：从 **Windows 实时辅助字幕**（Live Captions）窗口读取字幕文本，通过 **Google 翻译**（`deep-translator` 库）翻译成 **简体中文**，并在 **可拖动、可缩放、置顶** 的半透明浮窗里显示。

---

## 功能概览

- 用 **UI 自动化**（`pywinauto` + UIA）定位标题符合 `.*实时辅助字幕.*` 的字幕窗口。
- **自动识别**原文语言 → 译为 **简体中文**；带内存缓存，减少重复请求。
- 同一句字幕 **边听边变长** 时，浮窗 **只更新最后一行**，避免刷屏重复。
- 浮窗内保留最近 **5 条**中文记录。
- **无边框**窗口：中间区域 **拖动** 移动；**边缘** **拖动** 调整大小。

---

## 环境要求

- **Windows 10/11**，并已打开 **实时辅助字幕**（设置 → 辅助功能 → 字幕等，以系统实际菜单为准），字幕悬浮窗标题需能被程序匹配（默认包含 **「实时辅助字幕」**）。
- **Python 3.10+**（建议版本，可按本机情况调整）。
- 可访问互联网的 **Google 翻译**（由 `deep-translator` 调用）。

安装依赖：

```bash
pip install PyQt6 pywinauto deep-translator
```

---

## 使用步骤

1. 打开 **Windows 实时辅助字幕**，确保带 **「实时辅助字幕」** 字样的字幕窗口已显示。
2. 执行：

   ```bash
   python translator_overlay.py
   ```

3. 会出现深色半透明字幕条；可 **拖到** 合适位置，在 **边框/四角** **拖动** 改变大小。
4. 系统字幕窗口里一般是英文（或其它语言）实时字幕；本程序在浮窗中显示对应 **中文** 译文。

若无输出，请确认字幕窗口标题是否仍符合代码中的正则；若微软改名，需在 `translator_overlay.py` 里自行修改 `title_re`。

---

## 技术说明

- 界面线程：**PyQt6** 浮窗；后台 **QThread** 轮询字幕窗口下的 **Static** 控件文案。
- 翻译：`deep-translator` 的 `GoogleTranslator(source="auto", target="zh-CN")`。
- 轮询间隔、历史条数等在 `translator_overlay.py` 文件顶部常量中（如 `_POLL_INTERVAL_SEC`、`_SUBTITLE_HISTORY_MAX`）。

---

## 声明

仅供个人辅助阅读、学习使用；请遵守 **Google 翻译** 相关服务条款与频率限制。与微软、谷歌无关联。
