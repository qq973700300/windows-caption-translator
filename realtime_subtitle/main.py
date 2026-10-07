# -*- coding: utf-8 -*-
"""
实时视频翻译字幕（流式方案）

链路：系统声音 loopback 采集 -> Silero VAD 切句 -> 流式 Paraformer 增量识别
      -> LocalAgreement 定稿 -> 翻译（独立线程）-> 固定尺寸悬浮字幕窗

已移除的旧方案（不再保留回退）：
  - 能量阈值 VAD（对背景音乐敏感）-> 统一 Silero VAD
  - Whisper 整段识别（必须攒够音频再整段重跑，延迟高）-> 统一流式 Paraformer

架构底线：**ASR 永远不等翻译**。
  翻译是耗时不可控的网络请求，若同步调用会卡住整条流水线（音频块堆积、
  stream_queue 塞满后丢音频、识别质量下降）。因此：
  - 预览阶段（边说边出）只显示原文，不翻译
  - 一句话说完（VAD end）才入翻译队列，一条成句 = 一次翻译请求
  - 翻译在自己的线程里串行执行，结果回 UI；队列满则丢最旧的
"""
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox

# 模块目录：始终指向代码所在位置（打包后为 _MEIPASS 内部）
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
# 数据目录：源码运行=代码目录；打包后=exe 同级目录（保证配置/日志可持久保存）
BASE_DIR = (os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False) else _MODULE_DIR)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "subtitle.log")

sys.path.insert(0, _MODULE_DIR)

from textutil import group_pieces, looks_chinese, is_garbage  # noqa: E402

# 日志落盘；若目录不可写（如装在 Program Files）则退到临时目录，不阻断启动
try:
    _log_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
except Exception:
    import tempfile
    LOG_PATH = os.path.join(tempfile.gettempdir(), "subtitle.log")
    _log_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[_log_handler],
)
log = logging.getLogger("subtitle")

# ---------------------------------------------------------------------------
# 配置
DEFAULT_CONFIG = {
    "source_lang": "auto",            # auto / en / ja / ko / zh / ru ...
    "translate_engine": "auto",       # auto / google / mymemory / llm
    "sensitivity": 1.0,               # 0.5 - 3.0，越大越灵敏
    "device_id": "",                  # 音频输出设备（空 = 系统默认）
    "preview": True,                  # 边说边出字幕（关闭则整句说完才显示）
    "subtitle_x": -1,                 # 字幕窗位置（-1 居中）
    "subtitle_y": -1,
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "",
    "context_turns": 3,           # 给 LLM 参考的历史句数（0 = 关闭，仅 LLM 引擎有效）
}


SENTENCE_GAP = 0.6      # 同一段里多句话时的逐句显示间隔（秒）
SCENE_RESET_SEC = 60    # 连续静音超过这么久 -> 判定换片/换集，清空翻译上下文


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    # 清理已废弃的旧方案配置项
    for dead in ("model_size", "compute_device", "vad_engine", "asr_engine"):
        cfg.pop(dead, None)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("保存配置失败: %s", e)


LANGS = {
    "自动检测": "auto",
    "英语": "en",
    "日语": "ja",
    "韩语": "ko",
    "中文": "zh",
    "俄语": "ru",
    "法语": "fr",
    "德语": "de",
    "西班牙语": "es",
}
ENGINES = {"自动（免费在线回退链）": "auto", "Google 翻译": "google",
           "MyMemory": "mymemory", "LLM API (OpenAI兼容)": "llm"}
# 给 LLM 参考的历史句数（仅 LLM 引擎有效；免费引擎是单句接口，拿不到上下文）
CTX_TURNS = {"关闭（每句独立）": 0, "1 句": 1, "2 句": 2, "3 句": 3, "5 句": 5}

SUB_DURATION = 9.0  # 字幕停留秒数

# ---------------------------------------------------------------------------
# 字幕窗尺寸（固定，不随文本长短变化）
SUB_WIDTH = 760
SRC_LINES = 1        # 原文固定 1 行
DST_LINES = 2        # 译文固定 2 行
SRC_FONT = ("Microsoft YaHei UI", 12)
DST_FONT = ("Microsoft YaHei UI", 17, "bold")

_CJK_RE = re.compile(
    r"[\u3000-\u303f\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\uff00-\uffef]")


def _tokenize(s):
    """切成折行用的最小单位：CJK 按字，西文按词"""
    tokens, buf = [], ""
    for ch in s:
        if _CJK_RE.match(ch):
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch.isspace():
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(" ")
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


def fit_lines(text, font, max_px, n_lines):
    """把文本折行并规整成恰好 n_lines 行

    - 放不下时保留**最后** n_lines 行（字幕要留最新内容），首行加 …
    - 不足 n_lines 行时在前面补空行（顶部对齐），保证框体高度恒定
    """
    lines, cur = [], ""
    for tok in _tokenize(text or ""):
        if tok == " " and not cur:
            continue
        probe = cur + tok
        if cur and font.measure(probe.rstrip()) > max_px:
            lines.append(cur.rstrip())
            cur = "" if tok == " " else tok
        else:
            cur = probe
    if cur.strip():
        lines.append(cur.rstrip())
    if len(lines) > n_lines:                 # 超长：保留最新的几行
        lines = lines[-n_lines:]
        lines[0] = "…" + lines[0].lstrip()
    elif len(lines) < n_lines:               # 不足：在后面补空行，内容始终顶在第一行
        lines = lines + [""] * (n_lines - len(lines))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
class SubtitleOverlay(tk.Toplevel):
    """置顶半透明悬浮字幕窗（无边框、可拖动、尺寸固定）"""

    def __init__(self, master, x, y):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.82)
        self.configure(bg="#101014")
        self.pack_propagate(False)      # 内容再多也不撑大窗口

        self._src_f = tkfont.Font(family=SRC_FONT[0], size=SRC_FONT[1])
        self._dst_f = tkfont.Font(family=DST_FONT[0], size=DST_FONT[1],
                                  weight="bold")
        wrap = SUB_WIDTH - 40

        self.src_label = tk.Label(
            self, text="", font=self._src_f, fg="#c8c8c8", bg="#101014",
            wraplength=wrap, justify="left", anchor="nw", height=SRC_LINES)
        self.src_label.pack(padx=20, pady=(10, 0), fill="x")
        self.dst_label = tk.Label(
            self, text="", font=self._dst_f, fg="#ffd54a", bg="#101014",
            wraplength=wrap, justify="left", anchor="nw", height=DST_LINES)
        self.dst_label.pack(padx=20, pady=(2, 10), fill="x")

        # 关闭/拖动小提示条（右下角）
        tip = tk.Label(self, text="按住拖动 | 双击关闭", font=("Microsoft YaHei UI", 8),
                       fg="#666670", bg="#101014")
        tip.pack(side="bottom", anchor="e", padx=8, pady=2)

        # 高度按固定行数算死：内容长短变化时窗口不跳动
        padding = 10 + 2 + 10 + 4          # 各行 pack 的上下留白
        tip_line = self._src_f.metrics("linespace")   # 提示条约一行高
        h = (padding + SRC_LINES * self._src_f.metrics("linespace")
             + DST_LINES * self._dst_f.metrics("linespace") + tip_line + 6)
        self._h = int(h)
        self.geometry(f"{SUB_WIDTH}x{self._h}")

        self._dx = self._dy = 0
        for widget in (self, self.src_label, self.dst_label):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_move)
            widget.bind("<Double-Button-1>", lambda e: self.master.event_generate("<<close-overlay>>"))

        if x >= 0 and y >= 0:
            self.geometry(f"+{x}+{y}")
        else:
            # 默认：屏幕底部居中
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            self.update_idletasks()
            self.geometry(f"+{(sw - SUB_WIDTH) // 2}+{sh - self._h - 60}")

    def _on_press(self, e):
        self._dx = e.x
        self._dy = e.y

    def _on_move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def set_subtitle(self, src, dst, partial=False):
        # partial：边说边出的实时预览，末尾加 … 且颜色略淡，成句后会被完整版替换
        self.src_label.config(
            text=fit_lines(src + ("  …" if partial else ""), self._src_f,
                           SUB_WIDTH - 40, SRC_LINES),
            fg="#a8a8b0" if partial else "#c8c8c8")
        self.dst_label.config(
            text=fit_lines(dst if dst else "", self._dst_f,
                           SUB_WIDTH - 40, DST_LINES),
            fg="#ffe9a8" if partial else "#ffd54a")
        self.deiconify()

    def clear(self):
        # 清空也保持同样的行数，框体尺寸不变
        self.src_label.config(text="\n" * (SRC_LINES - 1), fg="#c8c8c8")
        self.dst_label.config(text="\n" * (DST_LINES - 1), fg="#ffd54a")


# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("实时视频翻译字幕")
        self.geometry("480x560")
        self.resizable(False, False)
        self.configure(bg="#f5f5f7")
        self._load_font_fallback()

        self.cfg = load_config()
        self.overlay = None
        self.segmenter = None
        self.worker = None
        self.stream_queue = queue.Queue(maxsize=256)
        self.disp_queue = queue.Queue()
        self.trans_q = None          # 翻译工作线程（P0：与 ASR 解耦）
        self.ctx = None              # 翻译上下文（P1：最近几句原文+译文）
        self._seq = 0                # 成句序号，防止慢翻译回来覆盖新句子
        self._last_final_seq = 0
        self.running = False
        self._last_sub_time = 0
        self._stats = {"sentences": 0}

        self._build_ui()
        self.after(120, self._poll)

    def _load_font_fallback(self):
        try:
            self.call("tk", "scaling", 1.15)
        except Exception:
            pass

    # ----------------------------------------------------------------- UI
    def _build_ui(self):
        pad = {"padx": 14, "pady": 4}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=4, pady=6)

        ttk.Label(frm, text="实时视频翻译字幕", font=("Microsoft YaHei UI", 15, "bold")).pack(pady=(8, 2))
        ttk.Label(frm, text="播放视频（任意播放器/网页）即可在屏幕下方出现中文翻译字幕",
                  foreground="#666").pack()

        btnf = ttk.Frame(frm)
        btnf.pack(pady=8)
        self.start_btn = ttk.Button(btnf, text="▶ 开始翻译", width=14, command=self.start)
        self.start_btn.grid(row=0, column=0, padx=6)
        self.stop_btn = ttk.Button(btnf, text="■ 停止", width=14, command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=6)

        # 音量指示
        volf = ttk.LabelFrame(frm, text=" 系统声音音量（播放视频时应跳动） ")
        volf.pack(fill="x", **pad)
        self.level_canvas = tk.Canvas(volf, height=14, bg="#e8e8ec", highlightthickness=0)
        self.level_canvas.pack(fill="x", padx=8, pady=6)

        self.preview_var = tk.BooleanVar(value=bool(self.cfg.get("preview", True)))
        ttk.Checkbutton(
            frm, text="实时预览（边说边出字幕，关闭则整句说完再显示）",
            variable=self.preview_var,
            command=self._save_now).pack(anchor="w", **pad)

        ttk.Label(frm, text="识别灵敏度（背景音大则调低）").pack(anchor="w", **pad)
        self.sens_var = tk.DoubleVar(value=float(self.cfg["sensitivity"]))
        ttk.Scale(frm, from_=0.5, to=3.0, variable=self.sens_var,
                  command=lambda v: None).pack(fill="x", **pad)

        gridf = ttk.Frame(frm)
        gridf.pack(fill="x", **pad)
        self.device_cb = ttk.Combobox(gridf, state="readonly", values=["加载中..."])
        rows = [
            ("音频来源", self.device_cb),
            ("视频语言", ttk.Combobox(gridf, state="readonly",
                          values=list(LANGS.keys()))),
            ("翻译引擎", ttk.Combobox(gridf, state="readonly",
                          values=list(ENGINES.keys()))),
        ]
        self.lang_cb, self.eng_cb = rows[1][1], rows[2][1]
        for i, (label, cb) in enumerate(rows):
            ttk.Label(gridf, text=label).grid(row=i, column=0, sticky="e", padx=6, pady=4)
            cb.grid(row=i, column=1, sticky="we", padx=6, pady=4)
            cb.bind("<<ComboboxSelected>>", lambda e: self._save_now())
        gridf.columnconfigure(1, weight=1)

        self.lang_cb.set(self._key_of(LANGS, self.cfg["source_lang"]))
        self.eng_cb.set(self._key_of(ENGINES, self.cfg["translate_engine"]))

        # LLM 配置
        llmf = ttk.LabelFrame(frm, text=" LLM 翻译（可选，OpenAI 兼容接口，填好后选择对应引擎） ")
        llmf.pack(fill="x", **pad)
        self.llm_url = ttk.Entry(llmf)
        self.llm_key = ttk.Entry(llmf, show="*")
        self.llm_model = ttk.Entry(llmf)
        for i, (label, entry, ph) in enumerate([
                ("接口地址", self.llm_url, "如 https://api.deepseek.com/v1"),
                ("API Key", self.llm_key, "sk-..."),
                ("模型名", self.llm_model, "如 deepseek-chat")]):
            ttk.Label(llmf, text=label).grid(row=i, column=0, sticky="e", padx=6, pady=3)
            entry.grid(row=i, column=1, sticky="we", padx=6, pady=3)
            entry.insert(0, self.cfg.get(
                {"接口地址": "llm_base_url", "API Key": "llm_api_key", "模型名": "llm_model"}[label], ""))
            entry.bind("<FocusOut>", lambda e: self._save_now())
        # 上下文句数：给 LLM 参考最近几句（0 = 关闭），只对 LLM 引擎有效
        self.ctx_cb = ttk.Combobox(llmf, state="readonly",
                                   values=list(CTX_TURNS.keys()), width=16)
        ttk.Label(llmf, text="上下文句数").grid(row=3, column=0, sticky="e", padx=6, pady=3)
        self.ctx_cb.grid(row=3, column=1, sticky="w", padx=6, pady=3)
        self.ctx_cb.set(self._key_of(CTX_TURNS, int(self.cfg.get("context_turns", 3) or 0)))
        self.ctx_cb.bind("<<ComboboxSelected>>", lambda e: self._save_now())
        ttk.Label(llmf, text="把最近几句原文+译文一起给模型参考，代词/人名更连贯",
                  foreground="#666", font=("Microsoft YaHei UI", 8)).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=6)
        llmf.columnconfigure(1, weight=1)

        # 状态
        stf = ttk.LabelFrame(frm, text=" 状态 ")
        stf.pack(fill="both", expand=True, **pad)
        self.status_var = tk.StringVar(value="待机。点击「开始翻译」后播放视频即可。")
        ttk.Label(stf, textvariable=self.status_var, wraplength=430,
                  justify="left").pack(anchor="w", padx=8, pady=6)
        self.last_var = tk.StringVar(value="")
        ttk.Label(stf, textvariable=self.last_var, wraplength=430, foreground="#0a7",
                  justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        self.bind("<<close-overlay>>", lambda e: self.stop())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_devices()

    def _refresh_devices(self):
        """填充音频来源下拉框（音频输出设备列表）"""
        self._devices = {"系统默认输出设备": ""}
        try:
            import soundcard as sc
            for s in sc.all_speakers():
                self._devices[s.name] = s.id
        except Exception as e:
            log.warning("声卡枚举失败: %s", e)
        self.device_cb["values"] = list(self._devices.keys())
        saved = self.cfg.get("device_id", "")
        cur = "系统默认输出设备"
        for name, did in self._devices.items():
            if did == saved:
                cur = name
                break
        self.device_cb.set(cur)
        self.device_cb.bind("<<ComboboxSelected>>", lambda e: self._save_now())

    @staticmethod
    def _key_of(mapping, value):
        for k, v in mapping.items():
            if v == value:
                return k
        return list(mapping.keys())[0]

    def _collect_cfg(self):
        self.cfg["source_lang"] = LANGS.get(self.lang_cb.get(), "auto")
        self.cfg["translate_engine"] = ENGINES.get(self.eng_cb.get(), "auto")
        self.cfg["sensitivity"] = round(float(self.sens_var.get()), 2)
        self.cfg["preview"] = bool(self.preview_var.get())
        if hasattr(self, "_devices"):
            self.cfg["device_id"] = self._devices.get(self.device_cb.get(), "")
        self.cfg["llm_base_url"] = self.llm_url.get().strip()
        self.cfg["llm_api_key"] = self.llm_key.get().strip()
        self.cfg["llm_model"] = self.llm_model.get().strip()
        if hasattr(self, "ctx_cb"):
            self.cfg["context_turns"] = CTX_TURNS.get(self.ctx_cb.get(), 3)

    def _save_now(self):
        self._collect_cfg()
        save_config(self.cfg)

    # ------------------------------------------------------------ 启停
    def start(self):
        if self.running:
            return
        self._save_now()
        try:
            from capture import AudioSegmenter
        except Exception as e:
            messagebox.showerror("错误", f"加载音频模块失败: {e}")
            return
        try:
            self.overlay = SubtitleOverlay(self, int(self.cfg["subtitle_x"]), int(self.cfg["subtitle_y"]))
            self.overlay.attributes("-alpha", 0.82)
        except Exception as e:
            log.warning("字幕窗创建失败: %s", e)
            self.overlay = None

        self.stream_queue = queue.Queue(maxsize=256)   # 流式 ASR：按 100ms 块送音频
        self.disp_queue = queue.Queue()
        self._seq = 0
        self._last_final_seq = 0
        self._start_translator()
        try:
            self.segmenter = AudioSegmenter(
                self.stream_queue, sensitivity=self.cfg["sensitivity"],
                device_id=self.cfg.get("device_id", "") or None)
        except Exception as e:
            log.exception("采集器启动失败: %s", e)
            messagebox.showerror("错误", f"音频采集启动失败: {e}")
            return
        self.segmenter.start()
        log.info("切句引擎: %s", self.segmenter.vad_mode)
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

        self.running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("运行中：正在准备流式识别模型……")

    def _start_translator(self):
        """启动独立翻译线程（ASR 线程只投递请求，不等结果）"""
        try:
            from translate import Translator
            from transworker import TranslationWorker
            from context import TranslationContext
        except Exception as e:
            log.exception("翻译模块加载失败: %s", e)
            messagebox.showerror("错误", f"翻译模块加载失败: {e}")
            return
        llm_cfg = None
        if self.cfg["translate_engine"] == "llm" or self.cfg["llm_api_key"]:
            llm_cfg = {"base_url": self.cfg["llm_base_url"],
                       "api_key": self.cfg["llm_api_key"],
                       "model": self.cfg["llm_model"]}
        lang = self.cfg.get("source_lang", "auto")
        tr = Translator(engine=self.cfg["translate_engine"],
                        target_lang="zh-CN", llm_config=llm_cfg)
        turns = int(self.cfg.get("context_turns", 3) or 0)
        self.ctx = TranslationContext(max_turns=turns)
        self.trans_q = TranslationWorker(
            self.disp_queue, tr,
            src_lang=None if lang == "auto" else lang,
            min_gap=SENTENCE_GAP, is_zh=looks_chinese, context=self.ctx)
        self.trans_q.start()
        log.info("翻译线程已启动（引擎=%s，上下文 %d 句）",
                 self.cfg["translate_engine"], turns)

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.trans_q:
            self.trans_q.stop()
            self.trans_q = None
        if self.segmenter:
            self.segmenter.stop()
        if self.overlay:
            x = self.overlay.winfo_x()
            y = self.overlay.winfo_y()
            self.cfg["subtitle_x"], self.cfg["subtitle_y"] = x, y
            self.overlay.destroy()
            self.overlay = None
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("已停止。")
        self._save_now()

    def _on_close(self):
        self.stop()
        self.destroy()

    # ---------------------------------------------------------- 工作线程
    def _worker_loop(self):
        """ASR 线程：只做采集->VAD->识别，翻译一律投递出去，绝不在这里等"""
        self._loop_streaming()

    # ------------------------------------------------------------ 流式循环
    def _loop_streaming(self):
        """真流式：每 100ms 一块持续喂给流式引擎，边说边出字幕"""
        import asr_stream
        from streaming import LocalAgreement

        if not asr_stream.available():
            self.disp_queue.put(
                ("status", "缺少 sherpa-onnx：请运行 install.bat 安装依赖"))
            return
        if asr_stream.find_model_dir() is None:
            self.disp_queue.put(("status", "首次使用：正在下载流式模型（约 240MB）…"))
            try:
                asr_stream.download_model()
            except Exception as e:
                log.exception("流式模型下载失败: %s", e)
                self.disp_queue.put(("status", f"流式模型下载失败: {e}"))
                return
        try:
            engine = asr_stream.StreamingASR(num_threads=4)
        except Exception as e:
            log.exception("流式引擎启动失败: %s", e)
            self.disp_queue.put(("status", f"流式引擎启动失败: {e}"))
            return

        self.disp_queue.put(("status", "流式引擎已就绪（延迟约 1 秒），正在监听视频声音……"))
        la = LocalAgreement()
        last_committed = ""
        last_preview = ""

        while self.running:
            try:
                kind, payload = self.stream_queue.get(timeout=0.3)
            except queue.Empty:
                self._maybe_reset_scene()
                continue
            try:
                if kind == "start":
                    engine.start(payload)      # 新的一句话，带上预卷音频
                    la.reset()
                    last_committed = ""
                    continue
                if kind == "cancel":
                    engine.reset()             # 太短的段，判定为误触发，丢弃
                    la.reset()
                    last_committed = ""
                    continue
                if kind == "end":
                    text = engine.finish().strip()
                    la.reset()
                    last_committed = ""
                    if self._bad(text):
                        continue
                    self._stats["sentences"] += 1
                    # 先立刻把完整的原文顶上去（不带 …），译文留空等翻译线程回填
                    self.disp_queue.put(("subtitle", text, "", 0.0, False, -1))
                    for sent in (group_pieces([text]) or [text]):
                        self._seq += 1
                        if self.trans_q:
                            self.trans_q.submit(self._seq, sent)
                        else:                       # 翻译线程没起来：至少显示原文
                            self.disp_queue.put(
                                ("subtitle", sent, "", 0.0, True, self._seq))
                    continue
                # ---- audio：边说边出（只滚原文，不翻译） ----
                # 注意：必须先喂音频给流式引擎，再决定要不要显示。
                # 关掉预览只是"不显示"，不能连音频都不喂，否则引擎收不到数据。
                t0 = time.time()
                text = engine.feed(payload).strip()
                asr_ms = (time.time() - t0) * 1000
                if self._bad(text):
                    continue
                committed, _pending = la.update(text)
                last_committed = committed
                if not self.cfg.get("preview", True):
                    continue
                # LocalAgreement 仍维护定稿状态（P1 的暂译会用到），
                # 但不再拿它触发翻译 —— 否则一句长句会打 5~10 次 API
                committed, _pending = la.update(text)
                last_committed = committed
                if text == last_preview:
                    continue
                last_preview = text
                self.disp_queue.put(("subtitle", text, "", asr_ms, False, -1))
            except Exception as e:
                log.exception("流式处理出错: %s", e)

    def _maybe_reset_scene(self):
        """连续静音过久 -> 判定换片/换集，清空翻译上下文

        不清的话，上一集的人物和译法会一路带进下一集（第一集的 John
        可能和第二集的 John 不是一个人，术语也会串）。
        """
        if not (self.ctx and self.segmenter):
            return
        try:
            if self.segmenter.silent_seconds >= SCENE_RESET_SEC and len(self.ctx):
                sid = self.ctx.reset()
                log.info("静音 %d 秒，翻译上下文已重置（场景 #%d）",
                         SCENE_RESET_SEC, sid)
        except Exception:
            pass

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _bad(text):
        """噪声/幻觉过滤"""
        if not text or len(text.strip()) < 2:
            return True
        return is_garbage(text)

    # -------------------------------------------------------------- 轮询
    def _poll(self):
        # 音量条
        if self.segmenter is not None and self.running:
            rms = self.segmenter.level_rms
            w = self.level_canvas.winfo_width() or 400
            self.level_canvas.delete("all")
            level = min(rms * 8.0, 1.0)
            color = "#0a84ff" if level < 0.6 else "#ff9f0a"
            self.level_canvas.create_rectangle(0, 0, max(2, level * (w - 4)), 14,
                                               fill=color, width=0)
        # 字幕队列
        try:
            while True:
                item = self.disp_queue.get_nowait()
                if item[0] == "subtitle":
                    _, src, dst, asr_ms, final = item[:5]
                    seq = item[5] if len(item) > 5 else -1
                    # 慢翻译回来时可能已经有更新的句子上屏了，过期结果直接丢弃
                    if final and seq >= 0 and seq < self._last_final_seq:
                        continue
                    if final and seq >= 0:
                        self._last_final_seq = seq
                    self._last_sub_time = time.time()
                    self._show_subtitle(src, dst, partial=not final)
                    engine_name = "LLM" if self.cfg["translate_engine"] == "llm" else "在线"
                    tag = "成句" if final else "实时"
                    done = self.trans_q.done if self.trans_q else 0
                    pend = (self.trans_q.q.qsize() if self.trans_q else 0)
                    self.status_var.set(
                        f"运行中 | 已识别 {self._stats['sentences']} 句 | "
                        f"已翻译 {done} 句（待译 {pend}） | {tag} "
                        f"识别耗时 {asr_ms:.0f}ms | 翻译: {engine_name}"
                        + self._silent_hint())
                    tail = (dst or src)[:40]
                    self.last_var.set(f"最新: {tail}")
                elif item[0] == "status":
                    if not self.running or time.time() - self._last_sub_time > 2:
                        self.status_var.set(item[1] + self._silent_hint())
        except queue.Empty:
            pass
        # 字幕淡出
        if self.overlay and self._last_sub_time and time.time() - self._last_sub_time > SUB_DURATION:
            self.overlay.clear()
            self._last_sub_time = 0
        self.after(120, self._poll)

    def _silent_hint(self):
        """长时间没采到声音 -> 很可能是「音频来源」选错了设备

        这是最容易让人以为"软件坏了"的坑：设备列表里确实有那个设备，
        但视频的声音其实从另一路出去，loopback 抓到的就是纯零。
        """
        if not (self.running and self.segmenter):
            return ""
        try:
            if self.segmenter.silent_seconds >= 5:
                return " | ⚠ 5 秒没采到声音：请确认「音频来源」选的是正在出声的那个设备"
        except Exception:
            pass
        return ""

    def _show_subtitle(self, src, dst, partial=False):
        if self.overlay:
            try:
                self.overlay.set_subtitle(src, dst or "", partial)
            except Exception:
                pass


def main():
    # pythonw 下无 stdout/stderr，避免 print 崩溃
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
