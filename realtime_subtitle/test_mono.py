# -*- coding: utf-8 -*-
"""「只识别不翻译」模式回归

字幕窗行为：原文放大显示在主行（小字行留空），窗口尺寸不变；
应用行为：引擎选「不翻译」时不启动翻译线程，成句直接上屏。
"""
import os
import sys
import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import main as M  # noqa: E402


def check_overlay():
    root = tk.Tk()
    root.withdraw()
    ov = M.SubtitleOverlay(root, -1, -1)
    ov.set_subtitle("Hello there, this is a test.", "", mono=True)
    src1 = ov.src_label.cget("text").strip()
    dst1 = ov.dst_label.cget("text").strip()
    ok1 = src1 == "" and "Hello there" in dst1

    ov.set_subtitle("Hello there.", "你好。", mono=False)
    src2 = ov.src_label.cget("text").strip()
    dst2 = ov.dst_label.cget("text").strip()
    ok2 = "Hello there" in src2 and "你好" in dst2

    # 实时增量：只写小字行，不动大字行
    ov.set_subtitle("Final sentence.", "", mono=True)
    ov.set_partial("Next sentence is being spoken")
    src3 = ov.src_label.cget("text").strip()
    dst3 = ov.dst_label.cget("text").strip()
    ok4 = "Next sentence" in src3 and "Final sentence" in dst3
    print(f"  增量只写小字行: {ok4} (src={src3[:24]!r} dst={dst3!r})")

    h1 = ov.winfo_height()
    ov.set_subtitle("x" * 200, "", mono=True)
    ov.update_idletasks()
    h2 = ov.winfo_height()
    ok3 = abs(h1 - h2) <= 1
    root.destroy()
    print(f"  mono 只显示原文: {ok1} (src={src1!r} dst={dst1!r})")
    print(f"  双语模式仍可用: {ok2}")
    print(f"  窗口高度不变: {ok3} ({h1} -> {h2})")
    return ok1 and ok2 and ok3 and ok4


def check_app_path():
    app = M.App()
    app.cfg["translate_engine"] = "none"
    app.mono = True
    app._start_translator()
    no_trans = app.trans_q is None
    app.destroy()
    print(f"  不翻译时不启动翻译线程: {no_trans}")
    return no_trans


def main():
    print("只识别模式")
    a = check_overlay()
    b = check_app_path()
    print("\n" + "=" * 50)
    ok = a and b
    print("MONO TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
