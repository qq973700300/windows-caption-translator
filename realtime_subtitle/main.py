# -*- coding: utf-8 -*-
"""
实时视频翻译字幕
- 捕获系统播放的声音（loopback）
- faster-whisper 本地识别
- 翻译成中文
- 置顶悬浮字幕窗显示
"""
import json
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "subtitle.log")

sys.path.insert(0, BASE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
log = logging.getLogger("subtitle")

# ---------------------------------------------------------------------------
# 配置
DEFAULT_CONFIG = {
    "model_size": "small",            # tiny / base / small / medium
    "source_lang": "auto",            # auto / en / ja / ko / zh / ru ...
    "translate_engine": "auto",       # auto / google / mymemory / llm
    "sensitivity": 1.0,               # 0.5 - 3.0，越大越灵敏
    "device_id": "",                  # 音频输出设备（空 = 系统默认）
    "compute_device": "auto",         # 识别运算设备：auto / cuda / cpu
    "preview": True,                  # 实时预览：边说边出字幕（低延迟）
    "subtitle_x": -1,                 # 字幕窗位置（-1 居中）
    "subtitle_y": -1,
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "",
}


SENTENCE_GAP = 0.6      # 同一段里多句话时的逐句显示间隔（秒）


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
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
MODELS = {"tiny (最快/精度低)": "tiny", "base (快, 延迟约0.6s)": "base",
          "small (推荐, 延迟约1.6s)": "small", "medium (慢/精度高)": "medium"}
ENGINES = {"自动（免费在线回退链）": "auto", "Google 翻译": "google",
           "MyMemory": "mymemory", "LLM API (OpenAI兼容)": "llm"}
DEVICES = {"自动（优先GPU）": "auto", "GPU (CUDA)": "cuda", "CPU": "cpu"}
DEVICES_INV = {v: k for k, v in DEVICES.items()}
MODELS_INV = {v: k for k, v in MODELS.items()}

SUB_DURATION = 9.0  # 字幕停留秒数


# ---------------------------------------------------------------------------
class SubtitleOverlay(tk.Toplevel):
    """置顶半透明悬浮字幕窗（无边框、可拖动）"""

    def __init__(self, master, x, y):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.82)
        self.configure(bg="#101014")

        w = 760
        self.src_label = tk.Label(
            self, text="", font=("Microsoft YaHei UI", 12),
            fg="#c8c8c8", bg="#101014", wraplength=w - 40, justify="left")
        self.src_label.pack(padx=20, pady=(10, 0), anchor="w")
        self.dst_label = tk.Label(
            self, text="", font=("Microsoft YaHei UI", 17, "bold"),
            fg="#ffd54a", bg="#101014", wraplength=w - 40, justify="left")
        self.dst_label.pack(padx=20, pady=(2, 10), anchor="w")

        # 关闭/拖动小提示条（右下角）
        tip = tk.Label(self, text="按住拖动 | 双击关闭", font=("Microsoft YaHei UI", 8),
                       fg="#666670", bg="#101014")
        tip.pack(side="bottom", anchor="e", padx=8, pady=2)

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
            self.geometry(f"+{(sw - w) // 2}+{sh - 180}")

    def _on_press(self, e):
        self._dx = e.x
        self._dy = e.y

    def _on_move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def set_subtitle(self, src, dst, partial=False):
        # partial：边说边出的实时预览，末尾加 … 且颜色略淡，成句后会被完整版替换
        self.src_label.config(text=src + ("  …" if partial else ""),
                              fg="#a8a8b0" if partial else "#c8c8c8")
        self.dst_label.config(text=dst if dst else "",
                              fg="#ffe9a8" if partial else "#ffd54a")
        self.deiconify()

    def clear(self):
        self.src_label.config(text="")
        self.dst_label.config(text="")


# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("实时视频翻译字幕")
        self.geometry("480x640")
        self.resizable(False, False)
        self.configure(bg="#f5f5f7")
        self._load_font_fallback()

        self.cfg = load_config()
        self.overlay = None
        self.segmenter = None
        self.worker = None
        self.seg_queue = queue.Queue(maxsize=8)
        self.disp_queue = queue.Queue()
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
            frm, text="实时预览（边说边出字幕，关闭则整句识别后再显示）",
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
            ("运行设备", ttk.Combobox(gridf, state="readonly",
                          values=list(DEVICES.keys()))),
            ("识别模型", ttk.Combobox(gridf, state="readonly",
                          values=list(MODELS.keys()))),
            ("视频语言", ttk.Combobox(gridf, state="readonly",
                          values=list(LANGS.keys()))),
            ("翻译引擎", ttk.Combobox(gridf, state="readonly",
                          values=list(ENGINES.keys()))),
        ]
        self.dev_cb = rows[1][1]
        self.model_cb, self.lang_cb, self.eng_cb = rows[2][1], rows[3][1], rows[4][1]
        for i, (label, cb) in enumerate(rows):
            ttk.Label(gridf, text=label).grid(row=i, column=0, sticky="e", padx=6, pady=4)
            cb.grid(row=i, column=1, sticky="we", padx=6, pady=4)
            cb.bind("<<ComboboxSelected>>", lambda e: self._save_now())
        gridf.columnconfigure(1, weight=1)

        self.model_cb.set(self._key_of(MODELS, self.cfg["model_size"]))
        self.lang_cb.set(self._key_of(LANGS, self.cfg["source_lang"]))
        self.eng_cb.set(self._key_of(ENGINES, self.cfg["translate_engine"]))
        self.dev_cb.set(self._key_of(DEVICES, self.cfg.get("compute_device", "auto")))
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
        self.cfg["model_size"] = MODELS.get(self.model_cb.get(), "small")
        self.cfg["source_lang"] = LANGS.get(self.lang_cb.get(), "auto")
        self.cfg["translate_engine"] = ENGINES.get(self.eng_cb.get(), "auto")
        self.cfg["sensitivity"] = round(float(self.sens_var.get()), 2)
        self.cfg["compute_device"] = DEVICES.get(self.dev_cb.get(), "auto")
        self.cfg["preview"] = bool(self.preview_var.get())
        if hasattr(self, "_devices"):
            self.cfg["device_id"] = self._devices.get(self.device_cb.get(), "")
        self.cfg["llm_base_url"] = self.llm_url.get().strip()
        self.cfg["llm_api_key"] = self.llm_key.get().strip()
        self.cfg["llm_model"] = self.llm_model.get().strip()

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

        self.seg_queue = queue.Queue(maxsize=8)
        self.disp_queue = queue.Queue()
        self._last_partial = ""
        self._last_final = ""       # 上一次成句文本（用于去重）
        self._main_lang = None      # 本次运行识别到的主语言（用于过滤跨语言噪声）
        self.segmenter = AudioSegmenter(
            self.seg_queue, sensitivity=self.cfg["sensitivity"],
            device_id=self.cfg.get("device_id", "") or None,
            preview=self.cfg.get("preview", True))
        self.segmenter.start()
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

        self.running = True
        self._asr_start_ts = time.time()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("运行中：正在加载识别模型（GPU 首次初始化需数秒）……")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self._asr_start_ts = None
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
        from transcribe import (transcribe_pieces, detect_device, actual_device,
                                split_sentences)   # split_sentences 重导出，供测试使用
        from translate import Translator

        # 预加载模型：不等第一段语音到达才加载，显著降低首个字幕的延迟
        try:
            from transcribe import get_model, actual_device
            get_model(self.cfg["model_size"], self.cfg.get("compute_device", "auto"))
            dev = actual_device()
            self.disp_queue.put(
                ("status", f"识别模型已就绪（{'GPU' if dev == 'cuda' else 'CPU'}），"
                           f"正在监听视频声音……"))
            self._asr_start_ts = None
            self._compute_dev = dev
        except Exception as e:
            log.exception("模型加载失败: %s", e)
            self.disp_queue.put(("status", f"模型加载失败: {e}"))
            return

        llm_cfg = None
        if self.cfg["translate_engine"] == "llm" or self.cfg["llm_api_key"]:
            llm_cfg = {"base_url": self.cfg["llm_base_url"],
                       "api_key": self.cfg["llm_api_key"],
                       "model": self.cfg["llm_model"]}
        tr = Translator(engine=self.cfg["translate_engine"],
                        target_lang="zh-CN", llm_config=llm_cfg)

        while self.running:
            try:
                pcm, partial = self.seg_queue.get(timeout=0.3)
            except queue.Empty:
                continue
            # 防止积压：只保留最新片段（避免延迟越滚越大）
            # 但优先保留"成句"结果，否则整句会被后来的预览片段覆盖掉
            pending = [(pcm, partial)]
            try:
                while True:
                    pending.append(self.seg_queue.get_nowait())
            except queue.Empty:
                pass
            finals = [x for x in pending if not x[1]]
            pcm, partial = finals[-1] if finals else pending[-1]
            try:
                if not self._compute_dev:
                    self._compute_dev = actual_device()
                t0 = time.time()
                pieces, src_lang, lang_prob = transcribe_pieces(
                    pcm, model_size=self.cfg["model_size"],
                    language=self.cfg["source_lang"],
                    device=self.cfg.get("compute_device", "auto"))
                asr_ms = (time.time() - t0) * 1000
                if not pieces:
                    continue
                text = "".join(pieces)
                # 语言一致性：与已确定的主语言不符且置信度不高 -> 判为噪声幻觉
                if src_lang:
                    if self._main_lang is None:
                        self._main_lang = src_lang
                    elif src_lang != self._main_lang and lang_prob < 0.95:
                        log.info("跳过跨语言噪声: %s (%s, p=%.2f)",
                                 text[:30], src_lang, lang_prob)
                        continue
                if partial:
                    # 部分结果：与上一次相同则不必重复翻译/刷新
                    if text == self._last_partial:
                        continue
                    self._last_partial = text
                    # 预览只显示"当前正在说的这一两句"，避免长段越滚越成一串
                    sentences = pieces[-2:]
                else:
                    # 与上一次成句完全相同（预览已完整显示过）-> 不重复刷新
                    if text == self._last_final:
                        continue
                    self._last_final = text
                    self._stats["sentences"] += 1
                    self._last_partial = ""
                    # 成句：已切好的字幕块，逐条显示
                    sentences = pieces
                for i, sent in enumerate(sentences):
                    translated = None
                    if not (src_lang and src_lang.startswith("zh")):
                        translated = tr.translate(sent, src_lang=src_lang)
                    else:
                        translated = sent  # 本身就是中文
                    self.disp_queue.put(("subtitle", sent, translated, asr_ms,
                                         src_lang, partial))
                    # 逐条间隔刷新，否则多条会在一次轮询里被最后一条盖掉
                    if i < len(sentences) - 1 and self.running:
                        time.sleep(SENTENCE_GAP)
            except Exception as e:
                log.exception("处理片段出错: %s", e)
                self.disp_queue.put(("status", f"处理出错: {e}"))

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
                    _, src, dst, asr_ms, lang, partial = item
                    self._last_sub_time = time.time()
                    self._show_subtitle(src, dst, partial)
                    engine_name = "LLM" if self.cfg["translate_engine"] == "llm" else "在线"
                    tag = "实时" if partial else "成句"
                    self.status_var.set(
                        f"运行中 | 已识别 {self._stats['sentences']} 句 | {tag} "
                        f"识别耗时 {asr_ms:.0f}ms | 语言 {lang or '?'} | 翻译: {engine_name}")
                    tail = (dst or src)[:40]
                    self.last_var.set(f"最新: {tail}")
                elif item[0] == "status":
                    if not self.running or time.time() - self._last_sub_time > 2:
                        self.status_var.set(item[1])
        except queue.Empty:
            pass
        # GPU 迟迟没出结果（显卡被其他程序占满时会卡住）-> 提示
        if getattr(self, "_asr_start_ts", None) and time.time() - self._asr_start_ts > 60:
            self.status_var.set(
                "首次识别超过 60 秒未完成：显卡可能正被其他程序占满。"
                "可关闭占用程序，或在「运行设备」切换为 CPU 后重新开始。")
            self._asr_start_ts = None
        # 字幕淡出
        if self.overlay and self._last_sub_time and time.time() - self._last_sub_time > SUB_DURATION:
            self.overlay.clear()
            self._last_sub_time = 0
        self.after(120, self._poll)

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
