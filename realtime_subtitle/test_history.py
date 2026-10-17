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
from capture import BLOCK_FRAMES  # noqa: E402


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


def case_pending_src_keeps_translation():
    """原文先上屏、译文还在翻的时候，不能把上一条译文清空

    字幕流程是：说完 -> 原文立刻上屏（译文空）-> 几百毫秒后译文回填。
    这中间如果清空译文行，屏幕上就是一条没译文的空字幕，正好把还没看清的
    上一条顶掉。
    """
    app = _app()
    try:
        app._enqueue_final("done sentence", "上一句译文")
        before = app.overlay.dst_label.cget("text")
        app._show_subtitle("next sentence just finished", "")
        mid = app.overlay.dst_label.cget("text")
        src = app.overlay.src_label.cget("text")
        # 译文回填后才替换
        app._show_subtitle("next sentence just finished", "下一句译文")
        done = app.overlay.dst_label.cget("text")
        ok = ("上一句译文" in before and "上一句译文" in mid
              and "next sentence" in src and "下一句译文" in done)
        print(f"  原文先上屏时保留上一条译文: {'上一句译文' in mid} | "
              f"回填后替换: {'下一句译文' in done}")
    finally:
        _close(app)
    return ok


def case_history_records():
    """只记真正上屏（带译文）的成句；只上原文不记"""
    M.MIN_SUB_HOLD = 0.0          # 关掉停留等待，两条都能立刻上屏
    app = _app()
    try:
        app._enqueue_final("a", "甲")
        app._show_subtitle("a just finished", "")   # 只上原文，不该进历史
        app._enqueue_final("b", "乙")
        ok = len(app.history) == 2 and app.history[0][2] == "甲" and app.history[1][2] == "乙"
        print(f"  成句 2 条 + 只上原文 1 次 -> 历史 {len(app.history)} 条 "
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


def case_hard_cap():
    """超长句兜底：一直没人停顿时，到 hard_cap 必须收句（延迟上限）

    现在的切句由 recognizer 的端点规则负责（rule1/rule2），
    我们的 hard_cap 只是保险：防止一句话无限长，模型越拖越糊。
    """
    from asr_stream import StreamingASR
    eng = StreamingASR(num_threads=2, hard_cap=2.0)
    n, hit = 0, None
    for i in range(30):                      # 30 块 = 3s
        eng.accept(np.zeros(BLOCK_FRAMES, dtype=np.float32), 48000)
        n += 1
        if eng.endpoint():
            hit = i * 0.1 + 0.1
            break
    ok = hit is not None and abs(hit - 2.0) <= 0.3
    print(f"  硬上限 2.0s -> 实际收句在 {hit}")
    return ok


if __name__ == "__main__":
    results = []
    print("回看能力回归：历史记录 + 停留保护 + 出字时机")
    print("\n[1/6] 最短停留（上一条没看够不换）")
    results.append(case_min_hold())
    print("\n[2/6] 排队不丢字幕")
    results.append(case_queue_no_loss())
    print("\n[3/6] 译文未回填时不清空上一条译文")
    results.append(case_pending_src_keeps_translation())
    print("\n[4/6] 历史只记成句")
    results.append(case_history_records())
    print("\n[5/6] 历史窗口（内容/复制/清空）")
    results.append(case_history_window())
    print("\n[6/6] 超长句硬上限（防止一句无限长）")
    results.append(case_hard_cap())
    print("\n" + "=" * 52)
    print("HISTORY TEST:", "PASS" if all(results) else f"FAIL {results}")
