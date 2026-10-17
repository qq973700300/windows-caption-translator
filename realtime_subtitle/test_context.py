# -*- coding: utf-8 -*-
"""P1 回归：严格有界上下文

验证：
1) 没有历史时退回简版 prompt（省 token）
2) 历史严格有界：max_turns=N 就只保留 N 句，不会随播放时长无限增长
3) prompt 里同时带原文和已上屏译文（这样新译文的术语才跟屏幕上的一致）
4) 场景重置：换集/长静音后清空，不把上一部的人名带过来
5) 与翻译线程集成：每翻完一句自动入历史
6) 关闭上下文（0 句）时行为等价旧版
"""
import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from context import TranslationContext    # noqa: E402
from translate import Translator          # noqa: E402
from transworker import TranslationWorker  # noqa: E402


class FastTranslator:
    def __init__(self):
        self.calls = []

    def translate(self, text, src_lang=None, context=None, on_delta=None):
        self.calls.append((text, context is not None))
        return "译" + text[-3:]


class MockLLM(BaseHTTPRequestHandler):
    """本地 OpenAI 兼容接口：记下每次收到的 prompt，返回固定译文"""

    prompts = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        MockLLM.prompts.append(body["messages"][0]["content"])
        data = json.dumps(
            {"choices": [{"message": {"content": "他说他会把东西带过来。"}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start_mock():
    from http.server import HTTPServer
    MockLLM.prompts = []
    srv = HTTPServer(("127.0.0.1", 0), MockLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def case_empty_history():
    c = TranslationContext(max_turns=3)
    p = c.build_prompt("Where are you going?")
    ok = p is None          # 第一句没历史，不该浪费 token
    print(f"  首句 prompt: {'None（退回简版）' if p is None else '有'}")
    return ok


def case_bounded():
    c = TranslationContext(max_turns=2)
    for i in range(6):
        c.add(f"src {i}", f"译 {i}")
    ok = len(c) == 2
    hist, _ = c.snapshot()
    ok = ok and hist[0][0] == "src 4" and hist[1][0] == "src 5"
    print(f"  塞入 6 句，max_turns=2 -> 实际保留 {len(c)} 句: {[h[0] for h in hist]}")
    return ok


def case_prompt_has_both():
    c = TranslationContext(max_turns=3)
    c.add("I need to talk to John.", "我需要和约翰谈谈。")
    c.add("Where are you going?", "你要去哪儿？")
    p = c.build_prompt("He told me he would bring it.")
    ok = ("EN: I need to talk to John." in p
          and "ZH: 我需要和约翰谈谈。" in p
          and "上一句" in p and "上二句" in p
          and "只输出当前句的译文" in p
          and "He told me he would bring it." in p)
    print(f"  含原文+译文: {'EN: I need' in p and 'ZH: 我需要' in p} | "
          f"含只翻当前句约束: {'只输出当前句的译文' in p}")
    return ok


def case_reset():
    c = TranslationContext(max_turns=3)
    c.add("a", "甲")
    c.add("b", "乙")
    sid_before = c.scene_id
    sid = c.reset()
    ok = len(c) == 0 and sid == sid_before + 1
    ok = ok and c.build_prompt("x") is None
    print(f"  重置后历史 {len(c)} 句，scene_id {sid_before} -> {sid}")
    return ok


def case_zero_turns():
    c = TranslationContext(max_turns=0)
    for i in range(3):
        c.add(f"s{i}", f"译{i}")
    ok = len(c) == 0 and c.build_prompt("x") is None
    print(f"  关闭上下文：历史 {len(c)} 句，prompt 始终简版")
    return ok


def case_glossary():
    """P2：术语表注入（完整用例见 test_glossary.py）"""
    c = TranslationContext(max_turns=0,
                           glossary={"Stark Industries": "斯塔克工业"})
    p = c.build_prompt("He works at Stark Industries.")
    ok = p is not None and "Stark Industries = 斯塔克工业" in p
    print(f"  术语表注入: {ok}（无历史也能注入，靠 is not None 判空）")
    return ok


def case_worker_integration():
    """翻译线程每翻完一句自动入历史，且受 max_turns 约束"""
    out = queue.Queue()
    tr = FastTranslator()
    ctx = TranslationContext(max_turns=2)
    w = TranslationWorker(out, tr, min_gap=0.0, context=ctx)
    w.start()
    for i in range(4):
        w.submit(i + 1, f"sentence {i}")
    deadline = time.time() + 5
    while w.done < 4 and time.time() < deadline:
        time.sleep(0.05)
    w.stop()
    ok = w.done == 4 and len(ctx) == 2 and all(c[1] for c in tr.calls)
    print(f"  翻完 {w.done} 句 -> 历史 {len(ctx)} 句（上限 2）| "
          f"每次调用都带 context: {all(c[1] for c in tr.calls)}")
    return ok


def case_real_llm_path():
    """走真实 translate._llm 代码路径：起一个本地 mock 的 OpenAI 兼容接口，
    验证发出去的 prompt 里确实带上了历史（不需要真实 API Key）"""
    srv = start_mock()
    port = srv.server_address[1]

    from translate import Translator
    tr = Translator(engine="llm", target_lang="zh-CN",
                    llm_config={"base_url": f"http://127.0.0.1:{port}/v1",
                                "api_key": "test", "model": "mock"})
    ctx = TranslationContext(max_turns=3)
    ctx.add("I need to talk to John.", "我需要和约翰谈谈。")
    out = tr.translate("He told me he would bring it.",
                       src_lang="en", context=ctx)
    srv.shutdown()

    sent = MockLLM.prompts[-1] if MockLLM.prompts else ""
    ok = (out == "他说他会把东西带过来。"
          and "我需要和约翰谈谈。" in sent
          and "He told me he would bring it." in sent)
    print(f"  收到译文: {out}")
    print(f"  prompt 含历史译文: {'我需要和约翰谈谈。' in sent} | "
          f"含当前句: {'He told me he would bring it.' in sent}")
    return ok


def case_llm_local_options():
    """本地模型（Ollama / LM Studio）适配：<arg_key:6124c78e> 开关 + 可调超时"""
    srv = start_mock()
    tr_on = Translator(engine="llm", target_lang="zh-CN",
                       llm_config={"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1",
                                   "api_key": "test", "model": "mock"},
                       timeout=45, no_think=True)
    tr_on._llm("Hello there.", "en")
    p_on = MockLLM.prompts[-1]

    tr_off = Translator(engine="llm", target_lang="zh-CN",
                        llm_config={"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1",
                                    "api_key": "test", "model": "mock"},
                        timeout=45, no_think=False)
    tr_off._llm("Hello there.", "en")
    p_off = MockLLM.prompts[-1]
    srv.shutdown()
    ok = (p_on.endswith("\n<arg_key:6124c78e>") and "<arg_key:6124c78e>" not in p_off
          and tr_on.timeout == 45)
    print(f"  开: 末尾带 <arg_key:6124c78e> = {p_on.endswith(chr(10) + '<arg_key:6124c78e>')} | "
          f"关: 不含 = {'<arg_key:6124c78e>' not in p_off} | 超时可配 = {tr_on.timeout}")
    return ok


def start_mock():
    from http.server import HTTPServer
    srv = HTTPServer(("127.0.0.1", 0), MockLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    results = []
    print("P1 回归：严格有界翻译上下文")
    print("\n[1/8] 首句无历史 -> 简版 prompt")
    results.append(case_empty_history())
    print("\n[2/8] 历史严格有界")
    results.append(case_bounded())
    print("\n[3/8] prompt 同时含原文与已上屏译文")
    results.append(case_prompt_has_both())
    print("\n[4/8] 场景重置")
    results.append(case_reset())
    print("\n[5/8] 关闭上下文（0 句）")
    results.append(case_zero_turns())
    print("\n[6/8] 术语表注入")
    results.append(case_glossary())
    print("\n[7/8] 与翻译线程集成")
    results.append(case_worker_integration())
    print("\n[8/9] 真实 LLM 代码路径（本地 mock 接口）")
    results.append(case_real_llm_path())
    print("\n[9/9] 本地模型适配（<arg_key:6124c78e> 开关 + 可调超时）")
    results.append(case_llm_local_options())
    print("\n" + "=" * 52)
    print("CONTEXT TEST:", "PASS" if all(results) else f"FAIL {results}")
