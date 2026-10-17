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
    """只识别模式：窗口里每条只显示原文（没有译文的第二行）"""
    root = tk.Tk()
    root.withdraw()
    ov = M.SubtitleOverlay(root, -1, -1)

    ov.set_subtitle("Hello there, this is a test.", "", mono=True)
    t1 = ov.txt.get("1.0", "end").strip()
    ok1 = "Hello there" in t1 and "\n" not in t1

    ov.set_subtitle("Hello there.", "你好。", mono=False)
    t2 = ov.txt.get("1.0", "end").strip()
    ok2 = "Hello there." in t2 and "你好。" in t2

    # 正在说的那句用淡色挂在最下面，定稿后合并成一条
    ov.set_partial("Next sentence is being spoken")
    t3 = ov.txt.get("1.0", "end").strip()
    ok3 = "Next sentence" in t3 and t3.count("Next sentence") == 1

    h1 = ov.winfo_height()
    ov.set_subtitle("x" * 200, "", mono=True)
    ov.update_idletasks()
    h2 = ov.winfo_height()
    ok4 = abs(h1 - h2) <= 1
    root.destroy()
    print(f"  mono 只显示原文: {ok1}")
    print(f"  双语模式仍可用: {ok2}")
    print(f"  进行中那句挂最后: {ok3}")
    print(f"  窗口高度不变: {ok4} ({h1} -> {h2})")
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
