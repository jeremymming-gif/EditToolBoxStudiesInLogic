#!/usr/bin/env python3.14
"""`llm.chat` 可选请求参数测试（response_format / max_tokens）—— 本地 mock 端点，不联网、不碰 Ollama。

断言的是**真正发出去的请求体**：默认调用必须与从前逐字节等价（老调用方 convert.py
的参考文献通道依赖这条），显式要求时才多出字段。

用法：cd Engine && /opt/homebrew/bin/python3.14 tests/test_llm_request_params.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
# 本地依赖自给自足：不靠 PYTHONPATH（vendor 里有 lxml 等）
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import llm  # noqa: E402

FAILS = []
BODIES = []          # mock 端点收到的请求体（解析后）
RAWS = []            # 同上，但保留**原始字节**（逐字节比对旧格式）


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


class H(BaseHTTPRequestHandler):
    mode = "200"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8")
        RAWS.append(raw)
        BODIES.append(json.loads(raw))
        if H.mode == "400":
            body = json.dumps({"error": {"message": "nope", "type": "invalid_request_error"}}).encode()
            self.send_response(400)
        else:
            body = json.dumps({"choices": [{"message": {"content": "OK-CONTENT"}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = HTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg = llm.LLMConfig(base=f"http://127.0.0.1:{port}/v1", key="mock", model="mock-model")

# ── 1) 默认调用：请求体里**没有**这两个字段（向后兼容的硬要求）─────────────
H.mode = "200"
out = llm.chat(cfg, "SYS", "USER", timeout=10)
b = BODIES[-1]
check("默认 → 正常返回内容", out == "OK-CONTENT", repr(out))
check("默认 → 请求体字段集合与从前完全一致",
      set(b) == {"model", "messages", "temperature"}, repr(sorted(b)))
check("默认 → 无 response_format / max_tokens",
      "response_format" not in b and "max_tokens" not in b)
check("默认 → 请求体**逐字节**等于旧格式（convert.py 的通道不可能被影响）",
      RAWS[-1] == '{"model": "mock-model", "messages": [{"role": "system", "content": "SYS"}, '
                  '{"role": "user", "content": "USER"}], "temperature": 0}',
      RAWS[-1])
check("默认 → temperature 仍是 0、model 与 messages 原样",
      b["temperature"] == 0 and b["model"] == "mock-model"
      and [m["role"] for m in b["messages"]] == ["system", "user"]
      and [m["content"] for m in b["messages"]] == ["SYS", "USER"], "")

# ── 2) 显式要求：两个字段按**给定值**出现 ─────────────────────────────────
out = llm.chat(cfg, "SYS", "USER", timeout=10,
               response_format={"type": "json_object"}, max_tokens=123)
b = BODIES[-1]
check("显式 → response_format 原样出现在请求体",
      b.get("response_format") == {"type": "json_object"}, repr(b.get("response_format")))
check("显式 → max_tokens 原样出现在请求体（整数）",
      b.get("max_tokens") == 123 and isinstance(b.get("max_tokens"), int), repr(b.get("max_tokens")))
check("显式 → 返回值与 temperature 不受影响", out == "OK-CONTENT" and b["temperature"] == 0, "")

# ── 3) 空 dict / 0 也要**如实**带上（「0=不封顶」是调用方的口径 —— 当年试点脚本
#       `aistruct` 如此约定，该脚本已退役删除（README 第 50 条）；chat 只照传，不解释）──
llm.chat(cfg, "SYS", "USER", timeout=10, response_format={}, max_tokens=0)
b = BODIES[-1]
check("response_format={} → 如实带上（None 才是「不带」）",
      "response_format" in b and b["response_format"] == {}, repr(b))
check("max_tokens=0 → 如实带上（judgement 留给调用方）",
      b.get("max_tokens") == 0, repr(b.get("max_tokens")))

# ── 4) 加了参数也不改失败契约：仍返回 None，原因仍可见 ────────────────────
H.mode = "400"
out = llm.chat(cfg, "SYS", "USER", timeout=10,
               response_format={"type": "json_object"}, max_tokens=8)
check("400 → 仍是 None（契约不变）", out is None, repr(out))
check("400 → LAST_ERROR 仍带状态码与端点", "HTTP 400" in llm.LAST_ERROR and cfg.base in llm.LAST_ERROR,
      llm.LAST_ERROR[:100])

srv.shutdown()
print()
print("SUMMARY: %d FAIL" % len(FAILS) + ("  -> " + ", ".join(FAILS) if FAILS else "  (all PASS)"))
sys.exit(1 if FAILS else 0)
