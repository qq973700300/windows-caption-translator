import sys
import time
from collections import deque
from typing import Optional

from PyQt6.QtCore import QObject, QPoint, QRect, Qt, QThread, pyqtSignal, pyqtSlot
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

# 字幕轮询：正常间隔与控件失效后的短暂退避，避免 tight loop
_POLL_INTERVAL_SEC = 0.5
_STALE_BACKOFF_SEC = 0.15


class SubtitleWorker(QObject):
    """在 QThread 中轮询字幕并翻译；仅通过信号向主线程投递 UI 更新。"""

    # 译文；第二个参数为 True 时表示与上一条同属「当前句」变长，UI 应替换最后一行而非追加
    translation_ready = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        # 原文 -> 译文，仅在工作线程内访问
        self._translation_cache: dict[str, str] = {}
        # pywinauto Static 包装器列表，仅在工作线程内访问；失效后清空并重建
        self._static_cache: list = []
        # 已按「最后一句」处理并刷新过 UI 的原文尾行（避免重复翻译与重复刷新）
        self._last_emitted_tail: str = ""
        self._last_emitted_zh: Optional[str] = None

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

    @staticmethod
    def _is_same_utterance_extension(prev_tail: str, new_tail: str) -> bool:
        """当前英文尾行是否由上一尾行「变长」而来（同一句延续），用于 UI 只更新一行。"""
        if not prev_tail or not new_tail:
            return False
        a = prev_tail.strip().lower()
        b = new_tail.strip().lower()
        if a == b:
            return False
        return b.startswith(a)

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
                        prev_tail = self._last_emitted_tail
                        translated = self._translate_cached(tail)
                        self._last_emitted_tail = tail
                        if (
                            self._last_emitted_zh is not None
                            and translated == self._last_emitted_zh
                        ):
                            continue
                        self._last_emitted_zh = translated
                        replace_last = self._is_same_utterance_extension(
                            prev_tail, tail
                        )
                        print(f"{tail}\n{translated}")
                        self.translation_ready.emit(translated, replace_last)

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

        self._subtitle_history: deque[str] = deque(maxlen=_SUBTITLE_HISTORY_MAX)

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

        self.show()

    def _sync_subtitle_geometry(self):
        self.subtitle_view.setGeometry(self.rect())

    @pyqtSlot(str, bool)
    def append_subtitle(self, zh: str, replace_last: bool = False):
        """
        追加或更新中文译文。
        replace_last 为 True 时（同一句英文变长）：用最新整段译文替换历史里最后一行，不重复堆叠。
        """
        line = " ".join(s for s in (ln.strip() for ln in zh.splitlines()) if s)
        if not line:
            return
        if replace_last and self._subtitle_history:
            self._subtitle_history[-1] = line
        else:
            self._subtitle_history.append(line)
        self.subtitle_view.setPlainText("\n".join(self._subtitle_history))
        bar = self.subtitle_view.verticalScrollBar()
        bar.setValue(bar.maximum())
        cursor = self.subtitle_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.subtitle_view.setTextCursor(cursor)
        self.subtitle_view.ensureCursorVisible()

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
