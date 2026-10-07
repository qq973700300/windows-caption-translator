# -*- coding: utf-8 -*-
"""P2 回归：术语表（Glossary）+ 语境缓存（scene-aware cache）

没有 API Key 也能验证的部分：
  - 词典有没有被正确、且**有界地**注入 prompt
  - 词典改动有没有让翻译缓存失效
  - 同一句话在不同上下文 / 不同场景（换集）下会不会误命中旧译文

验证不了的部分：模型翻得好不好（那需要真实 LLM）。
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from context import TranslationContext   # noqa: E402
from glossary import Glossary            # noqa: E402
from translate import Translator         # noqa: E402


# ---------------------------------------------------------------- mock LLM
class MockLLM(BaseHTTPRequestHandler):
    """本地 OpenAI 兼容接口：每次请求返回不同的译文，便于观察是否命中缓存"""
    prompts = []
    n = 0

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        MockLLM.prompts.append(body["messages"][0]["content"])
        MockLLM.n += 1
        data = json.dumps(
            {"choices": [{"message": {"content": f"译文{MockLLM.n}"}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start_mock():
    MockLLM.prompts, MockLLM.n = [], 0
    srv = HTTPServer(("127.0.0.1", 0), MockLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def llm_translator(srv):
    port = srv.server_address[1]
    return Translator(engine="llm", target_lang="zh-CN",
                      llm_config={"base_url": f"http://127.0.0.1:{port}/v1",
                                  "api_key": "test", "model": "mock"})


# -------------------------------------------------------------------- 用例
def case_bounded_injection():
    """词典很大也不要全塞进 prompt：只注入当前句/历史里出现过的"""
    g = Glossary(data={
        "John": "约翰",
        "Stark Industries": "斯塔克工业",
        "Westeros": "维斯特洛",
        "Iron Man": "钢铁侠",
    })
    ctx = TranslationContext(max_turns=0, glossary=g)
    p = ctx.build_prompt("I need to talk to John.", "简体中文")
    ok = ("John = 约翰" in p
          and "Westeros" not in p
          and "Stark Industries = 斯塔克工业" not in p
          and "Iron Man" not in p)
    print(f"  4 条词典，当前句只提到 John -> prompt 里术语数: "
          f"{p.count(' = ')}")
    return ok


def case_word_boundary():
    """西文术语要按词边界匹配，避免 Stark 命中 Starkville、I 命中 Iceland"""
    g = Glossary(data={"Stark": "史塔克", "I": "我"})
    ctx = TranslationContext(max_turns=0, glossary=g)
    hit = ctx.build_prompt("Starkville is a city in Iceland.")
    ok = hit is None
    p2 = ctx.build_prompt("John Stark will come.")
    ok = ok and p2 is not None and "Stark = 史塔克" in p2
    print(f"  Starkville/Iceland 不误命中: {hit is None} | "
          f"John Stark 正常命中: {p2 is not None and 'Stark = 史塔克' in p2}")
    return ok


def case_cjk_substring():
    """中文/日文术语没有词边界，按子串匹配"""
    g = Glossary(data={"史塔克": "Stark"})
    ctx = TranslationContext(max_turns=0, glossary=g)
    p = ctx.build_prompt("史塔克家族")
    ok = p is not None and "史塔克 = Stark" in p
    print(f"  中文子串命中: {ok}")
    return ok


def case_history_terms():
    """历史里出现过的术语也要注入（当前句可能只用代词 he 指代）"""
    g = Glossary(data={"John": "约翰"})
    ctx = TranslationContext(max_turns=3, glossary=g)
    ctx.add("I need to talk to John.", "我需要和约翰谈谈。")
    p = ctx.build_prompt("He told me he would bring it.")
    ok = p is not None and "John = 约翰" in p
    print(f"  当前句无 John，但历史里有 -> 仍注入: {ok}")
    return ok


def case_persist_roundtrip():
    """glossary.json 存读往返"""
    path = os.path.join(tempfile.mkdtemp(), "glossary.json")
    g1 = Glossary(path=path)
    g1.set("John", "约翰")
    g1.set("Westeros", "维斯特洛")
    ok = g1.save()
    g2 = Glossary(path=path)
    g2.load()
    ok = ok and g2.items() == [("John", "约翰"), ("Westeros", "维斯特洛")]
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    ok = ok and raw == {"John": "约翰", "Westeros": "维斯特洛"}
    print(f"  存读往返: {g2.items()} | 文件是纯 JSON 可直接手改: {isinstance(raw, dict)}")
    return ok


def case_version():
    """版本号：只有内容真的变了才 +1（用于让缓存失效）"""
    g = Glossary()
    v0 = g.version
    g.set("John", "约翰")
    v1 = g.version
    g.set("John", "约翰")        # 重复设置同样内容
    v2 = g.version
    g.set("John", "乔恩")        # 改译法
    v3 = g.version
    g.remove("John")
    v4 = g.version
    ok = (v1 == v0 + 1) and (v2 == v1) and (v3 == v2 + 1) and (v4 == v3 + 1)
    print(f"  版本: {v0} ->新增 {v1} ->重设 {v2} ->改值 {v3} ->删除 {v4}")
    return ok


def case_real_prompt():
    """真实 _llm 代码路径：prompt 里确实有【术语表】段"""
    srv = start_mock()
    tr = llm_translator(srv)
    g = Glossary(data={"Stark Industries": "斯塔克工业", "John": "约翰"})
    ctx = TranslationContext(max_turns=0, glossary=g)
    out = tr.translate("He works at Stark Industries.", src_lang="en", context=ctx)
    srv.shutdown()
    sent = MockLLM.prompts[-1] if MockLLM.prompts else ""
    ok = (out == "译文1" and "【术语表】" in sent
          and "Stark Industries = 斯塔克工业" in sent
          and "John" not in sent.split("【当前句】")[0].split("【术语表】")[-1])
    print(f"  收到译文: {out} | prompt 含术语表段: {'【术语表】' in sent}")
    return ok


def case_context_cache():
    """语境缓存：同上下文命中，换上下文/换集/改词典都要失效"""
    srv = start_mock()
    tr = llm_translator(srv)
    ctx = TranslationContext(max_turns=3)
    text = "He told me he would bring it."

    a1 = tr.translate(text, src_lang="en", context=ctx)      # 第 1 次请求
    a2 = tr.translate(text, src_lang="en", context=ctx)      # 缓存命中
    ctx.add("Where are you going?", "你要去哪儿？")            # 上下文变了
    a3 = tr.translate(text, src_lang="en", context=ctx)      # 应重新请求
    ctx.reset()                                             # 换集
    a4 = tr.translate(text, src_lang="en", context=ctx)      # 应重新请求
    ctx.glossary.set("John", "约翰")                          # 改词典
    a5 = tr.translate(text, src_lang="en", context=ctx)      # 应重新请求
    n = MockLLM.n
    srv.shutdown()

    ok = (a1 == a2 and a3 != a1 and a4 != a3 and a5 != a4 and n == 4)
    print(f"  相同上下文复用: {a1 == a2} | 换上下文重译: {a3 != a1} | "
          f"换集重译: {a4 != a3} | 改词典重译: {a5 != a4}")
    print(f"  共发出请求 {n} 次（应为 4）")
    return ok


def case_plain_cache_for_api_engines():
    """单句引擎不受上下文影响：仍是纯文本 key，缓存命中率不下降"""
    tr = Translator(engine="mymemory")
    ctx_a = TranslationContext(max_turns=3)
    ctx_a.add("a", "甲")
    ctx_b = TranslationContext(max_turns=3)
    ctx_b.add("b", "乙")
    k1 = tr._cache_key("Hello", ctx_a)
    k2 = tr._cache_key("Hello", ctx_b)
    ok = k1 == k2 == "Hello"
    print(f"  单句引擎缓存键: {k1!r}（上下文不影响）")
    return ok


def case_editor_window():
    """术语表编辑窗口：能打开、能添加、写进 glossary.json"""
    import tkinter as tk
    from tkinter import ttk
    import main as M

    app = M.App()
    app.update()
    app.withdraw()
    tmp = os.path.join(tempfile.mkdtemp(), "glossary.json")
    app.glossary.path = tmp
    app.glossary.data.clear()

    def find_all(w, cls):
        out = []
        for c in w.winfo_children():
            if isinstance(c, cls):
                out.append(c)
            out += find_all(c, cls)
        return out

    try:
        app._open_glossary()
        app.update()
        tops = [c for c in app.winfo_children() if isinstance(c, tk.Toplevel)]
        ok = len(tops) == 1
        win = tops[0] if tops else None
        entries = find_all(win, ttk.Entry)
        btns = {b.cget("text"): b for b in find_all(win, ttk.Button)}
        ok = ok and len(entries) == 2 and "添加 / 更新" in btns
        entries[0].insert(0, "Stark Industries")
        entries[1].insert(0, "斯塔克工业")
        btns["添加 / 更新"].invoke()
        app.update()
        ok = ok and app.glossary.items() == [("Stark Industries", "斯塔克工业")]
        with open(tmp, encoding="utf-8") as f:
            ok = ok and json.load(f) == {"Stark Industries": "斯塔克工业"}
        rows = find_all(win, ttk.Treeview)
        ok = ok and len(rows[0].get_children()) == 1
        print(f"  窗口打开: {len(tops) == 1} | 添加后落盘: {app.glossary.items()}")
    finally:
        app.destroy()
    return ok


if __name__ == "__main__":
    results = []
    print("P2 回归：术语表 + 语境缓存")
    print("\n[1/10] 有界注入（词典不全塞进 prompt）")
    results.append(case_bounded_injection())
    print("\n[2/10] 西文词边界（Stark 不误命中 Starkville）")
    results.append(case_word_boundary())
    print("\n[3/10] 中文子串匹配")
    results.append(case_cjk_substring())
    print("\n[4/10] 历史里出现过的术语也要注入")
    results.append(case_history_terms())
    print("\n[5/10] glossary.json 存读往返")
    results.append(case_persist_roundtrip())
    print("\n[6/10] 版本号只在内容变化时递增")
    results.append(case_version())
    print("\n[7/10] 真实 _llm 路径（本地 mock 接口）")
    results.append(case_real_prompt())
    print("\n[8/10] 语境缓存命中/失效")
    results.append(case_context_cache())
    print("\n[9/10] 单句引擎仍是纯文本缓存键")
    results.append(case_plain_cache_for_api_engines())
    print("\n[10/10] 术语表编辑窗口（真 UI）")
    results.append(case_editor_window())
    print("\n" + "=" * 52)
    print("GLOSSARY TEST:", "PASS" if all(results) else f"FAIL {results}")
