# -*- coding: utf-8 -*-
"""字幕窗（历史式）回归

新形态：一句话结束就追加一条（原文 + 译文），最新的在最下面，
往前能看到刚才说了什么。不再有"单条字幕 + 2.5 秒停留"那套。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import main as M  # noqa: E402


def _app():
    """新建 App 并挂一个字幕窗（overlay 平时由 start() 创建）"""
    app = M.App()
    app.overlay = M.SubtitleOverlay(app, -1, -1)
    return app


def case_append_order():
    """连续三句都追加，最新的在最下面"""
    app = _app()
    for i in range(1, 4):
        app.overlay.set_subtitle(f"Sentence {i}.", f"第{i}句。")
    text = app.overlay.txt.get("1.0", "end").strip()
    lines = [l for l in text.splitlines() if l.strip()]
    ok = lines[-2:] == ["Sentence 3.", "第3句。"] and "Sentence 1." in lines
    print(f"  三句追加: {ok} | 末两行 {lines[-2:]}")
    app.destroy()
    return ok


def case_only_recent():
    """窗口只保留最近 MAX_ITEMS 条，旧的往上顶掉"""
    app = _app()
    for i in range(1, 6):
        app.overlay.set_subtitle(f"S{i}.", f"译{i}。")
    text = app.overlay.txt.get("1.0", "end").strip()
    keep = app.overlay.MAX_ITEMS
    ok = (f"S{5}." in text) and (f"S{5 - keep}." not in text)
    print(f"  只留最近 {keep} 条: {ok} | {text.replace(chr(10), ' / ')}")
    app.destroy()
    return ok


def case_pending_src():
    """译文还没回来时，原文以淡色挂在最下面；定稿后变成正式条目"""
    app = _app()
    app.overlay.set_partial("He is coming.")
    t1 = app.overlay.txt.get("1.0", "end").strip()
    app.overlay.set_subtitle("He is coming.", "他来了。")
    t2 = app.overlay.txt.get("1.0", "end").strip()
    ok = ("He is coming." in t1) and ("他来了。" in t2) and t2.count("He is coming.") == 1
    print(f"  原文先挂、定稿替换: {ok}")
    app.destroy()
    return ok


def case_history_records():
    """内部历史仍记录成句（双击看全部历史用）"""
    app = _app()
    app._record_history("Hello.", "你好。")
    ok = len(app.history) == 1 and app.history[0][2] == "你好。"
    print(f"  历史记录: {ok} | {app.history[:1]}")
    app.destroy()
    return ok


def case_clear():
    """清空后窗口为空"""
    app = _app()
    app.overlay.set_subtitle("A.", "甲。")
    app.overlay.clear()
    ok = not app.overlay.txt.get("1.0", "end").strip()
    print(f"  清空: {ok}")
    app.destroy()
    return ok


def case_hard_cap():
    """超长句兜底：一直没人停顿时，到 hard_cap 必须收句（延迟上限）"""
    import numpy as np
    from capture import BLOCK_FRAMES
    from asr_stream import StreamingASR
    eng = StreamingASR(num_threads=2, hard_cap=2.0)
    hit = None
    for i in range(30):                      # 30 块 = 3s
        eng.accept(np.zeros(BLOCK_FRAMES, dtype=np.float32), 48000)
        if eng.endpoint():
            hit = i * 0.1 + 0.1
            break
    ok = hit is not None and abs(hit - 2.0) <= 0.3
    print(f"  硬上限 2.0s -> 收句在 {hit}")
    return ok


def main():
    results = []
    print("字幕窗（历史式）回归")
    print("\n[1/6] 追加顺序")
    results.append(case_append_order())
    print("\n[2/6] 只保留最近几条")
    results.append(case_only_recent())
    print("\n[3/6] 原文先挂、定稿替换")
    results.append(case_pending_src())
    print("\n[4/6] 历史记录")
    results.append(case_history_records())
    print("\n[5/6] 清空")
    results.append(case_clear())
    print("\n[6/6] 超长句硬上限")
    results.append(case_hard_cap())
    print("\n" + "=" * 52)
    print("HISTORY TEST:", "PASS" if all(results) else f"FAIL {results}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
