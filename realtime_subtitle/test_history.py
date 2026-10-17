# -*- coding: utf-8 -*-
"""回看能力回归：字幕历史 + 最短停留 + 预览不清空译文 + 强切时长

针对的反馈：「一直在说话时翻译不出来」「上一句还没看清就被下一句盖掉」
「希望能向上翻滚看之前的记录」。
"""
import os
import queue
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np                              # noqa: E402

import main as M                                # noqa: E402
from capture import AudioSegmenter, BLOCK_FRAMES  # noqa: E402


def _app():
    app = M.App()
    app.update()
    app.withdraw()
    app.overlay = M.SubtitleOverlay(app, -1, -1)
    app.overlay.update_idletasks()
    return app


def _close(app):
    """关掉轮询定时器再销毁，避免 after 回调砸到已销毁的窗口"""
    try:
        if app._poll_job:
            app.after_cancel(app._poll_job)
    except Exception:
        pass
    app.destroy()


# ------------------------------------------------------------------- 用例
def case_min_hold():
    """最短停留：上一条没看够 2.5 秒，新句子排队而不立刻覆盖"""
    M.MIN_SUB_HOLD = 0.6          # 加速测试，逻辑完全一致
    app = _app()
    try:
        app._enqueue_final("first sentence", "第一句")
        shown1 = app.overlay.dst_label.cget("text")
        app._enqueue_final("second sentence", "第二句")     # 紧接着来
        shown2 = app.overlay.dst_label.cget("text")         # 应仍是第一句
        time.sleep(0.7)
        app._flush_pending()
        shown3 = app.overlay.dst_label.cget("text")
        ok = ("第一句" in shown1 and "第一句" in shown2 and "第二句" in shown3)
        print(f"  连续两句: 立刻显示={shown1.strip()[:6]} | "
              f"未到停留时间仍显示={shown2.strip()[:6]} | 到点后={shown3.strip()[:6]}")
    finally:
        _close(app)
    return ok


def case_queue_no_loss():
    """积压时排队不丢字幕：连来 3 条，逐条按停留时间放出来"""
    M.MIN_SUB_HOLD = 0.25
    app = _app()
    try:
        for i in range(3):
            app._enqueue_final(f"s{i}", f"句{i}")
        queued = len(app._pending)
        got = [app.overlay.dst_label.cget("text").strip()]   # 第 0 条已立即上屏
        for _ in range(6):
            time.sleep(0.3)
            app._flush_pending()
            t = app.overlay.dst_label.cget("text").strip()
            if t and (not got or got[-1] != t):
                got.append(t)
        ok = queued >= 1 and got == ["句0", "句1", "句2"]
        print(f"  入队 3 条 -> 排队 {queued} 条 -> 依次上屏 {got}")
    finally:
        _close(app)
    return ok


def case_preview_keeps_translation():
    """预览（下一句刚开口）不能把上一条还没看清的译文清空"""
    app = _app()
    try:
        app._enqueue_final("done sentence", "上一句译文")
        before = app.overlay.dst_label.cget("text")
        app._show_subtitle("next sentence is being spoken", None, partial=True)
        after = app.overlay.dst_label.cget("text")
        src = app.overlay.src_label.cget("text")
        ok_keep = ("上一句译文" in before and "上一句译文" in after
                   and "next sentence" in src)
        # 关掉开关后恢复旧行为（立即清空译文）
        app.cfg["keep_prev_dst"] = False
        app._show_subtitle("another", None, partial=True)
        off = app.overlay.dst_label.cget("text")
        ok = ok_keep and off.strip() == ""
        print(f"  预览时保留译文: {'上一句译文' in after} | "
              f"关掉开关后清空: {off.strip() == ''}")
    finally:
        _close(app)
    return ok


def case_history_records():
    """只有成句进历史；预览不进（否则一句话会被拆成十几条）"""
    M.MIN_SUB_HOLD = 0.0          # 关掉停留等待，两条都能立刻上屏
    app = _app()
    try:
        app._enqueue_final("a", "甲")
        app._show_subtitle("a is being spoken now", None, partial=True)
        app._enqueue_final("b", "乙")
        ok = len(app.history) == 2 and app.history[0][2] == "甲" and app.history[1][2] == "乙"
        print(f"  成句 2 条 + 预览 1 次 -> 历史 {len(app.history)} 条 "
              f"{[h[2] for h in app.history]}")
    finally:
        _close(app)
    return ok


def case_history_window():
    """历史窗口：能打开、内容正确、可清空、可复制"""
    app = _app()
    try:
        app._record_history("Where are you going?", "你要去哪儿？")
        app._record_history("I need to talk to John.", "我需要和约翰谈谈。")
        app._open_history()
        app.update()
        txt = app._hist_text
        ok = txt is not None
        body = txt.get("1.0", "end") if ok else ""
        ok = ok and "你要去哪儿？" in body and "我需要和约翰谈谈。" in body
        ok = ok and "Where are you going?" in body
        ok = ok and app._hist_count.get() == "共 2 条"
        # 复制全部
        app._copy_history()
        app.update()
        clip = ""
        try:
            clip = app.clipboard_get()
        except Exception:
            clip = "我要去哪儿？|fallback"
        ok = ok and ("你要去哪儿？" in clip)
        # 清空
        app._clear_history()
        ok = ok and len(app.history) == 0 and txt.get("1.0", "end").strip() == ""
        print(f"  窗口内容含两条译文: {'你要去哪儿？' in body and '我需要和约翰谈谈。' in body} | "
              f"复制全部: {'你要去哪儿？' in clip} | 清空后 {len(app.history)} 条")
        app._close_history()
        ok = ok and app._hist_win is None
    finally:
        _close(app)
    return ok


class ScriptedVAD:
    """假 VAD：前 n 帧判有人声，之后判无人声。

    用它测切句状态机本身，不依赖 Silero 对某段音频的判定 —— 结果才确定。
    """

    def __init__(self, speech_frames):
        self.n = speech_frames
        self.i = 0

    def is_speech(self, block):
        self.i += 1
        on = self.i <= self.n
        return on, (0.99 if on else 0.01)

    def reset(self):
        self.i = 0


def _feed(vad, n_blocks):
    q = queue.Queue(maxsize=4096)
    seg = AudioSegmenter(q, sensitivity=1.0)
    seg._silero = vad
    marks = []
    state = {"i": 0}
    seg.on_event = lambda k: marks.append((k, state["i"] * 0.1))
    for i in range(n_blocks):
        state["i"] = i
        seg.feed_block(np.zeros(BLOCK_FRAMES, dtype=np.float32))
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
    return [round(t, 1) for k, t in marks if k == "end"]


def case_force_split_grace():
    """超时后不是立刻硬切，而是先等一个自然停顿（等太久才硬切）

    曾经是"到点且有声音就立刻切"，实测把长句从中间劈开：
    "...and explain" + "why this technology matters ..." 变成两半，
    半句去翻译译文必然不通顺，识别质量也跟着下降（matters -> matter）。
    """
    from capture import MAX_SEGMENT_FRAMES, GRACE_FRAMES

    # A：一直说不停 -> 到 (MAX + GRACE) 才硬切，这是延迟上限
    ends_a = _feed(ScriptedVAD(10 ** 6), MAX_SEGMENT_FRAMES + GRACE_FRAMES + 10)
    hard_at = (MAX_SEGMENT_FRAMES + GRACE_FRAMES) * 0.1

    # B：说过上限后停顿 -> 应当在**停顿处**收句，比硬切早，句子才是完整的
    stop_at = MAX_SEGMENT_FRAMES + 8                  # 超过上限 0.8s 后停下
    ends_b = _feed(ScriptedVAD(stop_at), MAX_SEGMENT_FRAMES + GRACE_FRAMES + 10)
    pause_at = (stop_at + 4) * 0.1                    # 再过 4 帧静音判定收句

    ok_a = bool(ends_a) and abs(ends_a[0] - hard_at) <= 0.4
    ok_b = bool(ends_b) and abs(ends_b[0] - pause_at) <= 0.4 and ends_b[0] < hard_at
    print(f"  一直不停: end 在 {ends_a[:1]}（硬切上限 {hard_at:.1f}s）")
    print(f"  超时后停顿: end 在 {ends_b[:1]}（停顿 {pause_at:.1f}s，早于硬切 -> 句子完整）")
    return ok_a and ok_b


if __name__ == "__main__":
    results = []
    print("回看能力回归：历史记录 + 停留保护 + 出字时机")
    print("\n[1/6] 最短停留（上一条没看够不换）")
    results.append(case_min_hold())
    print("\n[2/6] 排队不丢字幕")
    results.append(case_queue_no_loss())
    print("\n[3/6] 预览不清空上一条译文")
    results.append(case_preview_keeps_translation())
    print("\n[4/6] 历史只记成句")
    results.append(case_history_records())
    print("\n[5/6] 历史窗口（内容/复制/清空）")
    results.append(case_history_window())
    print("\n[6/6] 超长句：先等停顿，实在没有才硬切")
    results.append(case_force_split_grace())
    print("\n" + "=" * 52)
    print("HISTORY TEST:", "PASS" if all(results) else f"FAIL {results}")
