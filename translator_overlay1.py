import re
import sys
import time
from collections import deque
from typing import Optional

from PyQt6.QtCore import QObject, QPoint, QRect, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication, QTextEdit, QWidget

from pywinauto import Desktop
from deep_translator import GoogleTranslator


# 边缘命中与缩放方向（位标志）
_RESIZE_LEFT = 1
_RESIZE_RIGHT = 2
_RESIZE_TOP = 4
_RESIZE_BOTTOM = 8

_EDGE_MARGIN = 8

# 字幕历史条数上限（仅中文，每条一行展示）
_SUBTITLE_HISTORY_MAX = 5

# 新句切换去抖：上一句尾行有句末标点时用较短稳定窗，否则用较长窗（毫秒）
_STABLE_MS_WITH_PUNCT = 140
_STABLE_MS_NO_PUNCT = 420

# 句末标点 + 草稿久无更新：将当前句单独写入历史（毫秒）
_SILENCE_COMMIT_CHECK_MS = 200
_SILENCE_COMMIT_AFTER_MS = 700

# 时间维度的分工（避免两套定时器在概念上「抢收尾权」）：
# - pending 单次定时器：只解决「疑似新句在缓冲区里是否稳定」，到时再交 _commit_draft_to_history("debounce")。
# - silence 周期定时器：只解决「尾句有句末标点后长时间无更新 = 本句已说完」，交 _commit_draft_to_history("idle_end")。

# 句末标点（英文 / 中文常见句终）
_TAIL_SENTENCE_END_RE = re.compile(
    r"""[\.\!\?\…。！？]["'\)\]]*\s*$""",
    re.UNICODE,
)

# 字幕轮询：正常间隔与控件失效后的短暂退避，避免 tight loop
_POLL_INTERVAL_SEC = 0.5
_STALE_BACKOFF_SEC = 0.15


class SubtitleWorker(QObject):
    """在 QThread 中轮询字幕并翻译；仅通过信号向主线程投递 UI 更新。"""

    # 英文尾句（用于区分「同一句增长」与「新的一句」）、中文译文
    translation_ready = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        # 原文 -> 译文，仅在工作线程内访问
        self._translation_cache: dict[str, str] = {}
        # pywinauto Static 包装器列表，仅在工作线程内访问；失效后清空并重建
        self._static_cache: list = []
        # 已按「最后一句」处理并刷新过 UI 的原文尾行（避免重复翻译与重复刷新）
        self._last_emitted_tail: str = ""

    def stop(self):
        self._running = False

    def _translate_cached(self, text: str) -> str:
        if text in self._translation_cache:
            return self._translation_cache[text]
        translated = GoogleTranslator(
            source="auto",
            target="zh-CN",
        ).translate(text)
        self._translation_cache[text] = translated
        return translated

    @staticmethod
    def _last_non_empty_line(blob: str) -> str:
        """整块字幕只取最后一行非空文本，作为当前句。"""
        if not blob or not blob.strip():
            return ""
        norm = blob.replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln.strip() for ln in norm.split("\n")]
        nonempty = [ln for ln in lines if ln]
        return nonempty[-1] if nonempty else ""

    def _rebuild_static_cache(self, caption_window) -> None:
        """仅在缓存缺失或失效时调用，内部会执行一次 descendants()。"""
        self._static_cache = [
            child
            for child in caption_window.descendants()
            if child.friendly_class_name() == "Static"
        ]

    def _read_static_text(self, ctrl) -> Optional[str]:
        """
        读取单个 Static 文本。
        返回 None 表示控件已失效（包装器不可用），应重建缓存。
        """
        try:
            return ctrl.window_text().strip()
        except Exception:
            return None

    @pyqtSlot()
    def run_forever(self):
        try:
            caption_window = Desktop(backend="uia").window(
                title_re=".*实时辅助字幕.*"
            )
            while self._running:
                try:
                    if not self._static_cache:
                        self._rebuild_static_cache(caption_window)

                    cache_stale = False
                    for ctrl in self._static_cache:
                        raw = self._read_static_text(ctrl)
                        if raw is None:
                            cache_stale = True
                            break
                        tail = self._last_non_empty_line(raw)
                        if not tail:
                            continue
                        if tail == self._last_emitted_tail:
                            continue
                        translated = self._translate_cached(tail)
                        self._last_emitted_tail = tail
                        print(f"{tail}\n{translated}")
                        self.translation_ready.emit(tail, translated)

                    if cache_stale:
                        self._static_cache = []
                        time.sleep(_STALE_BACKOFF_SEC)
                        continue

                    time.sleep(_POLL_INTERVAL_SEC)
                except Exception as e:
                    print("错误:", e)
                    self._static_cache = []
                    time.sleep(_POLL_INTERVAL_SEC)
        finally:
            th = self.thread()
            if th is not None:
                th.quit()


class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setMinimumSize(200, 80)
        self.setMouseTracking(True)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 已说完的句子（仅中文），最多保留最近 N 条
        self._subtitle_history: deque[str] = deque(maxlen=_SUBTITLE_HISTORY_MAX)
        # 当前正在说的「尾句」及其译文：同一句增长时只替换底部，不写入历史
        self._draft_tail: str = ""
        self._draft_zh: str = ""
        # 最近一次「草稿入库」原因，仅调试用（debounce / idle_end）
        self._last_draft_commit_reason: Optional[str] = None
        # 调试/理解用：idle=无草稿；drafting=当前句；pending=缓冲区里有一句待稳定
        self._draft_state: str = "idle"
        # 仅暂存「疑似下一句」的尾句与译文；不承载分句语义，延伸判断用 _streaming_extension
        self._pending_new_tail: Optional[str] = None
        self._pending_new_zh: Optional[str] = None
        self._pending_stability_timer = QTimer(self)
        self._pending_stability_timer.setSingleShot(True)
        self._pending_stability_timer.timeout.connect(
            self._on_pending_stability_timeout
        )
        self._last_draft_activity_mono: float = time.monotonic()
        self._silence_commit_timer = QTimer(self)
        self._silence_commit_timer.setInterval(_SILENCE_COMMIT_CHECK_MS)
        self._silence_commit_timer.timeout.connect(self._maybe_silence_commit_draft)

        self.subtitle_view = QTextEdit(self)
        self.subtitle_view.setReadOnly(True)
        self.subtitle_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.subtitle_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.NoContextMenu
        )
        self.subtitle_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.subtitle_view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.subtitle_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.subtitle_view.setPlaceholderText("等待字幕中...")
        self.subtitle_view.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.subtitle_view.setStyleSheet("""
            QTextEdit {
                color: white;
                font-size: 28px;
                background-color: rgba(0,0,0,180);
                padding: 15px;
                border-radius: 10px;
                border: none;
            }
        """)

        self.resize(1200, 200)
        self.move(300, 800)

        self._sync_subtitle_geometry()

        # 交互状态：None | "drag" | "resize"
        self._mode = None
        self._resize_edges = 0
        self._press_global = QPoint()
        self._start_geometry = QRect()
        self._drag_offset = QPoint()

        self._silence_commit_timer.start()
        self._sync_draft_state()

        self.show()

    def _sync_subtitle_geometry(self):
        self.subtitle_view.setGeometry(self.rect())

    @staticmethod
    def _tail_ends_sentence_punct(tail: str) -> bool:
        s = tail.rstrip()
        if not s:
            return False
        return bool(_TAIL_SENTENCE_END_RE.search(s))

    @staticmethod
    def _streaming_extension(prev_tail: str, new_tail: str) -> bool:
        """ASR 流式：同一条文本的增长或小幅回退修正（仅比较两段字符串）。"""
        if not prev_tail:
            return True
        if new_tail == prev_tail:
            return True
        return new_tail.startswith(prev_tail) or prev_tail.startswith(new_tail)

    @classmethod
    def _same_utterance_in_progress(cls, prev_tail: str, new_tail: str) -> bool:
        """
        草稿尾句 vs 新来的尾句：是否仍像「同一句在识别中」。
        （pending 缓冲区请用 _streaming_extension(anchor, tail)，避免与草稿语义混读。）
        """
        return cls._streaming_extension(prev_tail, new_tail)

    def _render_subtitles(self) -> None:
        lines = list(self._subtitle_history)
        draft = self._draft_zh.strip()
        if draft:
            lines.append(draft)
        self.subtitle_view.setPlainText("\n".join(lines))
        bar = self.subtitle_view.verticalScrollBar()
        bar.setValue(bar.maximum())
        cursor = self.subtitle_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.subtitle_view.setTextCursor(cursor)
        self.subtitle_view.ensureCursorVisible()

    def _touch_draft_activity(self) -> None:
        self._last_draft_activity_mono = time.monotonic()

    def _sync_draft_state(self) -> None:
        if self._pending_new_tail is not None:
            self._draft_state = "pending"
        elif self._draft_tail.strip() or self._draft_zh.strip():
            self._draft_state = "drafting"
        else:
            self._draft_state = "idle"

    def _commit_draft_to_history(self, reason: str) -> None:
        """
        草稿 → 历史的唯一入口（启发式分句的「收尾」动作集中在这里）。

        reason 约定：
        - debounce：pending 稳定窗到期，确认换句；
        - idle_end：句末标点 + 静音，本句已说完且无 pending。
        （后续若增加「无去抖的语义断句」可再扩展，如 semantic_break。）
        """
        self._last_draft_commit_reason = reason
        if self._draft_zh.strip():
            self._subtitle_history.append(self._draft_zh.strip())
        self._draft_tail = ""
        self._draft_zh = ""
        self._sync_draft_state()

    def _cancel_pending_break(self) -> None:
        self._pending_new_tail = None
        self._pending_new_zh = None
        self._pending_stability_timer.stop()
        self._sync_draft_state()

    def _arm_pending_stability_timer(self) -> None:
        """pending 缓冲区有内容时（re）启动稳定窗；窗长仅看当前草稿尾行是否像已说完。"""
        if self._pending_new_tail is None:
            return
        ms = (
            _STABLE_MS_WITH_PUNCT
            if self._tail_ends_sentence_punct(self._draft_tail)
            else _STABLE_MS_NO_PUNCT
        )
        self._pending_stability_timer.stop()
        self._pending_stability_timer.start(ms)

    @pyqtSlot()
    def _on_pending_stability_timeout(self) -> None:
        """pending 单次定时器：仅在此处把旧草稿入库并切换到缓冲区内容。"""
        if self._pending_new_tail is None:
            return
        self._apply_pending_when_stable()

    def _apply_pending_when_stable(self) -> None:
        if self._pending_new_tail is None or self._pending_new_zh is None:
            return
        nt = self._pending_new_tail
        nz = self._pending_new_zh
        self._cancel_pending_break()
        self._commit_draft_to_history("debounce")
        self._draft_tail = nt
        self._draft_zh = nz
        self._touch_draft_activity()
        self._sync_draft_state()
        self._render_subtitles()

    @pyqtSlot()
    def _maybe_silence_commit_draft(self) -> None:
        """句末标点 + 一段时间无草稿更新：将当前句单独入库（不依赖是否已出现下一句）。"""
        if self._pending_new_tail is not None:
            return
        if not self._draft_tail.strip() or not self._draft_zh.strip():
            return
        if not self._tail_ends_sentence_punct(self._draft_tail):
            return
        idle_ms = (time.monotonic() - self._last_draft_activity_mono) * 1000.0
        if idle_ms < _SILENCE_COMMIT_AFTER_MS:
            return
        self._commit_draft_to_history("idle_end")
        self._sync_draft_state()
        self._render_subtitles()

    @pyqtSlot(str, str)
    def append_subtitle(self, tail_en: str, zh: str):
        """
        入口只做两件事：更新当前草稿，或把疑似新句写入 pending 并交给稳定定时器。
        入库（commit）只发生在 _apply_pending_when_stable / _maybe_silence_commit_draft 内，
        且一律经 _commit_draft_to_history。
        """
        tail = tail_en.strip()
        zh_line = " ".join(
            s for s in (ln.strip() for ln in zh.splitlines()) if s
        )
        if not tail or not zh_line:
            return

        if self._pending_new_tail is not None:
            if self._same_utterance_in_progress(self._draft_tail, tail):
                self._cancel_pending_break()
            elif self._streaming_extension(self._pending_new_tail, tail):
                self._pending_new_tail = tail
                self._pending_new_zh = zh_line
                self._arm_pending_stability_timer()
                self._sync_draft_state()
                return
            else:
                self._pending_new_tail = tail
                self._pending_new_zh = zh_line
                self._arm_pending_stability_timer()
                self._sync_draft_state()
                return

        if not self._draft_tail:
            self._draft_tail = tail
            self._draft_zh = zh_line
            self._touch_draft_activity()
            self._sync_draft_state()
            self._render_subtitles()
            return

        if self._same_utterance_in_progress(self._draft_tail, tail):
            self._draft_tail = tail
            self._draft_zh = zh_line
            self._touch_draft_activity()
            self._sync_draft_state()
            self._render_subtitles()
            return

        self._pending_new_tail = tail
        self._pending_new_zh = zh_line
        self._arm_pending_stability_timer()
        self._sync_draft_state()

    def resizeEvent(self, event):
        self._sync_subtitle_geometry()
        super().resizeEvent(event)

    def _hit_test_edges(self, pos: QPoint) -> int:
        r = self.rect()
        edges = 0
        if pos.x() <= _EDGE_MARGIN:
            edges |= _RESIZE_LEFT
        elif pos.x() >= r.width() - _EDGE_MARGIN:
            edges |= _RESIZE_RIGHT
        if pos.y() <= _EDGE_MARGIN:
            edges |= _RESIZE_TOP
        elif pos.y() >= r.height() - _EDGE_MARGIN:
            edges |= _RESIZE_BOTTOM
        return edges

    def _cursor_shape_for_edges(self, edges: int):
        if edges == 0:
            return Qt.CursorShape.ArrowCursor

        h = edges & (_RESIZE_LEFT | _RESIZE_RIGHT)
        v = edges & (_RESIZE_TOP | _RESIZE_BOTTOM)

        if h and v:
            if (edges & _RESIZE_LEFT and edges & _RESIZE_TOP) or (
                edges & _RESIZE_RIGHT and edges & _RESIZE_BOTTOM
            ):
                return Qt.CursorShape.SizeFDiagCursor
            return Qt.CursorShape.SizeBDiagCursor
        if h:
            return Qt.CursorShape.SizeHorCursor
        if v:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _update_cursor(self, local_pos: QPoint):
        if self._mode == "resize" or self._mode == "drag":
            return
        edges = self._hit_test_edges(local_pos)
        self.setCursor(self._cursor_shape_for_edges(edges))

    def _apply_resize(self, global_pos: QPoint):
        delta = global_pos - self._press_global
        g = QRect(self._start_geometry)
        e = self._resize_edges

        if e & _RESIZE_LEFT:
            g.setLeft(g.left() + delta.x())
        if e & _RESIZE_TOP:
            g.setTop(g.top() + delta.y())
        if e & _RESIZE_RIGHT:
            g.setRight(g.right() + delta.x())
        if e & _RESIZE_BOTTOM:
            g.setBottom(g.bottom() + delta.y())

        min_w = self.minimumWidth()
        min_h = self.minimumHeight()
        if g.width() < min_w:
            if e & _RESIZE_LEFT:
                g.setLeft(g.right() - min_w + 1)
            else:
                g.setRight(g.left() + min_w - 1)
        if g.height() < min_h:
            if e & _RESIZE_TOP:
                g.setTop(g.bottom() - min_h + 1)
            else:
                g.setBottom(g.top() + min_h - 1)

        self.setGeometry(g)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            local = event.position().toPoint()
            self._press_global = event.globalPosition().toPoint()
            self._resize_edges = self._hit_test_edges(local)

            if self._resize_edges:
                self._mode = "resize"
                self._start_geometry = self.geometry()
            else:
                self._mode = "drag"
                self._drag_offset = self._press_global - self.pos()

            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        gp = event.globalPosition().toPoint()
        local = event.position().toPoint()

        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._mode == "drag":
                self.move(gp - self._drag_offset)
            elif self._mode == "resize":
                self._apply_resize(gp)
            event.accept()
            return

        self._update_cursor(local)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._mode = None
            self._resize_edges = 0
            self._update_cursor(event.position().toPoint())
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._mode is None:
            self.unsetCursor()
        super().leaveEvent(event)


def main():
    app = QApplication(sys.argv)

    window = OverlayWindow()

    worker_thread = QThread()
    worker = SubtitleWorker()
    worker.moveToThread(worker_thread)

    worker_thread.started.connect(worker.run_forever)
    worker.translation_ready.connect(window.append_subtitle)

    app.aboutToQuit.connect(worker.stop)
    worker_thread.finished.connect(worker.deleteLater)

    worker_thread.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
