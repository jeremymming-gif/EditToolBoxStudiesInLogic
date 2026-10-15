#!/usr/bin/env python3.14
"""llm.chat 失败原因可见性测试（本地临时端点，不联网）。
用法：cd Engine && PYTHONPATH="$PWD/vendor" python3.14 /tmp/test_llm_error.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.getcwd())
from slcn import llm  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


class H(BaseHTTPRequestHandler):
    mode = "400"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        if H.mode == "400":
            body = json.dumps({"error": {"message":
                    "exceeds the available context size, try increasing it",
                    "type": "exceed_context_size_error"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif H.mode == "500":
            body = b"internal boom"
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
base = f"http://127.0.0.1:{port}/v1"

# ── 1) HTTP 400（上下文超限）：返回 None，但原因必须可见 ──────────────────
H.mode = "400"
cfg = llm.LLMConfig(base=base, key="ollama", model="m")
out = llm.chat(cfg, "sys", "user", timeout=10)
check("400 → 返回值仍是 None（契约不变）", out is None, repr(out))
check("400 → LAST_ERROR 有状态码", "HTTP 400" in llm.LAST_ERROR, llm.LAST_ERROR[:120])
check("400 → LAST_ERROR 带响应体（能看出 exceed_context_size_error）",
      "exceed_context_size_error" in llm.LAST_ERROR, "")
check("400 → LAST_ERROR 带端点与模型名", base in llm.LAST_ERROR and "model=m" in llm.LAST_ERROR, "")

# ── 2) HTTP 500 且响应体不是 JSON ────────────────────────────────────────
H.mode = "500"
llm.chat(cfg, "sys", "user", timeout=10)
check("500 → 仍是 None 且原因可见", llm.LAST_ERROR.startswith("HTTP 500"), llm.LAST_ERROR[:100])
check("500 → 非 JSON 响应体也带出来", "internal boom" in llm.LAST_ERROR, "")

# ── 3) 成功：返回内容，并清掉上次的错误 ──────────────────────────────────
H.mode = "200"
out = llm.chat(cfg, "sys", "user", timeout=10)
check("200 → 正常返回内容", out == "OK-CONTENT", repr(out))
check("200 → LAST_ERROR 被清空", llm.LAST_ERROR == "", repr(llm.LAST_ERROR))

# ── 4) 端点不可达（连接被拒）────────────────────────────────────────────
cfg2 = llm.LLMConfig(base="http://127.0.0.1:9/v1", key="x", model="m")
out = llm.chat(cfg2, "sys", "user", timeout=5)
check("不可达 → None 且原因是异常名（不是 HTTP）",
      out is None and "HTTP" not in llm.LAST_ERROR and "Error" in llm.LAST_ERROR,
      llm.LAST_ERROR[:100])

# ── 5) 未配置（base/model 空）────────────────────────────────────────────
out = llm.chat(llm.LLMConfig(base="", key="", model=""), "sys", "user")
check("未配置 → None 且原因写明未配置",
      out is None and "未配置" in llm.LAST_ERROR, llm.LAST_ERROR[:80])

srv.shutdown()
print()
print("SUMMARY: %d FAIL" % len(FAILS) + ("  -> " + ", ".join(FAILS) if FAILS else "  (all PASS)"))
sys.exit(1 if FAILS else 0)
