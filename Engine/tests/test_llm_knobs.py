#!/usr/bin/env python3.14
"""Responses 通道的两个**未来兼容旋钮**（第 70 条）。

为什么要有这一套：第 69 条把「联网检索」做成了开关，但请求里有两处**硬编码**：
工具类型名 `web_search` 与路径 `{base}/responses`。各家实现并不一致（DeepSeek 的
flash 仍忽略内置检索工具、v4-pro 已支持），名字也未必都叫 `web_search`
（OpenAI 自己就有 `web_search_preview`，各家网关更是各不相同），路径也可能不是 `/responses`。
所以第 70 条把它们变成配置：`SLCN_LLM_SEARCH_TOOL` / `SLCN_LLM_RESPONSES_PATH`
（命令行 `--llm-search-tool` / `--llm-responses-path`）——将来只需改配置，不必改代码。

本套件钉死四件事（全部只打 127.0.0.1 上的本地假端点，**不联网**、不花额度）：

  ① **默认口径**：不设这两个旋钮时 = `web_search` / `/responses`；空值与认不出的值
     一律回落默认（与 `protocol_from_env()` 同一条纪律：不抛异常，但也不静默）。
  ② **默认逐字节不变**：不设新旋钮时，请求**路径**与请求**体字节**与第 70 条之前
     逐字节相同（用冻结字面量对照，比「与 chat 对比」更硬）。
  ③ **环境变量 / CLI 两条入口等价**，且探针 `probe_web_search()` 用的是同一份配置
     —— 否则「体检说支持」与真实转换打的可能不是同一个地址，那是假证据。
  ④ **mock 端到端**：自定义工具名（`web_search_preview`）真的打进 `tools[].type`，
     mock 的 `/__stats.n_tools_requested` 记到的就是这个自定义名；而判词**仍然**按
     **检索证据**判「支持」（证据是 `web_search_call`，不是工具名）。

用法：cd Engine && /opt/homebrew/bin/python3.14 tests/test_llm_knobs.py
"""
import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_MACTOOLBOX = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import llm  # noqa: E402

FAILS = []
SKIPS = []
RAWS = []       # 端点收到的**原始请求体字节**（逐字节比对用）
BODIES = []     # 解析后的请求体
PATHS = []      # 端点收到的路径


def check(name, cond, detail=""):
    # detail 允许直接给列表/字典（写断言时最方便），打印前统一转成字符串。
    if not isinstance(detail, str):
        detail = repr(detail)
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


# ── 本地假端点：收到 tools 就回检索证据（扮演「你发什么检索工具我就认什么」的端点）──
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):       # pragma: no cover - 只为把非 POST 打到 400 时说清楚
        self.send_response(400)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8")
        RAWS.append(raw)
        PATHS.append(self.path)
        body = json.loads(raw)
        BODIES.append(body)
        tools = body.get("tools") or []
        output = [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "% mock 回复\n",
             "annotations": [{"type": "url_citation", "url": "https://example.invalid/src"}]}]}]
        if tools:
            # 有工具就回证据（工具名叫什么都不影响——证据就是 web_search_call）。
            output.insert(0, {"type": "web_search_call", "id": "ws_1", "status": "completed",
                              "action": {"type": "search", "query": "2025 诺奖 官方"}})
        payload = {"id": "resp_1", "object": "response", "status": "completed",
                   "model": body.get("model", ""), "output": output, "error": None,
                   "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = HTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE_V1 = f"http://127.0.0.1:{PORT}/v1"
BASE_ROOT = f"http://127.0.0.1:{PORT}"

# 第 70 条之前 `responses()` 打出来的**那一个**地址与那一份请求体（冻结字面量）。
# 为什么用字面量而不是「再算一遍」：这正是「默认逐字节不变」这句话本身，
# 抄一遍才能在下一次有人动默认值时当场变红。
FROZEN_PATH = "/v1/responses"
FROZEN_BODY = ('{"model": "mock-model", "instructions": "SYS", "input": "USER", '
               '"temperature": 0, "max_output_tokens": 2048, '
               '"reasoning": {"effort": "none"}, "tools": [{"type": "web_search"}]}')

# ── (a) 默认口径：不设 = web_search / /responses；空值与坏值回落默认 ──────────
print("\n── (a) 默认口径与坏值回落 ──")
_KEYS = ("SLCN_LLM_SEARCH_TOOL", "SLCN_LLM_RESPONSES_PATH")


@contextlib.contextmanager
def env_vars(**kv):
    """临时清掉这两个旋钮的环境变量、按需设值，退出时原样恢复。"""
    old = {k: os.environ.get(k) for k in _KEYS}
    for k in _KEYS:
        os.environ.pop(k, None)
    for k, v in kv.items():
        os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _cfg():
    c = llm.config_from_env(BASE_V1, "mock", "mock-model",
                            protocol="responses", web_search=True)
    return c.search_tool, c.responses_path


with env_vars():
    _default = _cfg()
with env_vars(SLCN_LLM_SEARCH_TOOL="web_search_preview",
              SLCN_LLM_RESPONSES_PATH="/v1/responses"):
    _override = _cfg()

check("(a) 都不设 → 工具名 web_search、路径 /responses（默认就是向后兼容）",
      _default == ("web_search", "/responses"), repr(_default))
check("(a) 环境变量覆盖：SLCN_LLM_SEARCH_TOOL / SLCN_LLM_RESPONSES_PATH",
      _override == ("web_search_preview", "/v1/responses"), repr(_override))
check("(a) 空值（export X=）→ 回落默认，不是空字符串",
      llm.resolve_search_tool("") == "web_search"
      and llm.resolve_responses_path("   ") == "/responses",
      f"{llm.resolve_search_tool('')!r} / {llm.resolve_responses_path('   ')!r}")
check("(a) 认不出的工具名（含空白）→ 默认 + 出声（绝不静默、也绝不打灭整条通道）",
      llm.resolve_search_tool("web search") == "web_search"
      and llm.resolve_search_tool("a{b}") == "web_search")
check("(a) 不像路径的值（带 :// 或空白）→ 默认",
      llm.resolve_responses_path("https://x/responses") == "/responses"
      and llm.resolve_responses_path("/a b") == "/responses")
check("(a) 缺前导 / 自动补、末尾 / 去掉（手滑最常见的一刀）",
      llm.resolve_responses_path("responses") == "/responses"
      and llm.resolve_responses_path("v1/responses/") == "/v1/responses",
      repr(llm.resolve_responses_path("v1/responses/")))
check("(a) 直接构造 LLMConfig（老调用方按位置传参）拿到的就是默认值",
      llm.LLMConfig(base="b", model="m").search_tool == "web_search"
      and llm.LLMConfig(base="b", model="m").responses_path == "/responses")

# ── (b) 默认逐字节不变：路径 + 请求体字节与冻结字面量相同 ─────────────────────
print("\n── (b) 不设新旋钮 → 路径与请求体逐字节不变（冻结字面量）──")
cfg_default = llm.LLMConfig(base=BASE_V1, key="mock", model="mock-model",
                            protocol="responses", web_search=True)
llm.complete(cfg_default, "SYS", "USER", timeout=10, max_tokens=2048,
             thinking={"type": "disabled"})
check("(b) 默认路径仍是 `{base}/responses`（base 带 /v1 → 打到 /v1/responses）",
      PATHS[-1] == FROZEN_PATH, f"实际路径 {PATHS[-1]}（冻结 {FROZEN_PATH}）")
check("(b) 请求体**逐字节**等于第 70 条之前的那一份（含 tools:[{type:web_search}]）",
      RAWS[-1] == FROZEN_BODY,
      "一致" if RAWS[-1] == FROZEN_BODY else f"实际 {RAWS[-1]}\n冻结 {FROZEN_BODY}")
check("(b) 冻结字面量自洽（真的是 mock-model 那一条请求）",
      json.loads(FROZEN_BODY)["tools"] == [{"type": "web_search"}])

# 第 70 条之后：旋钮一改，URL 与 tools 就跟着变（这才叫「可配」）
cfg_custom = llm.LLMConfig(base=BASE_V1, key="mock", model="mock-model",
                           protocol="responses", web_search=True,
                           search_tool="web_search_preview",
                           responses_path="/v1/responses")
llm.complete(cfg_custom, "SYS", "USER", timeout=10)
check("(b) 自定义工具名真的进了 tools[].type",
      BODIES[-1].get("tools") == [{"type": "web_search_preview"}],
      repr(BODIES[-1].get("tools")))
cfg_path = llm.LLMConfig(base=BASE_ROOT, key="mock", model="mock-model",
                         protocol="responses", web_search=True,
                         responses_path="/v1/responses")
llm.complete(cfg_path, "SYS", "USER", timeout=10)
check("(b) 自定义路径真的拼在 base 之后（base 不带 /v1 + 路径自带 /v1）",
      PATHS[-1] == "/v1/responses", PATHS[-1])

# ── (c) 探针用的是同一份旋钮（体检必须与真实转换打同一个地址/同一种工具）──────
print("\n── (c) probe_web_search 也走这两个旋钮 ──")
r = llm.probe_web_search(BASE_ROOT, "mock", "mock-model", timeout=10, query="随便问",
                         search_tool="web_search_preview",
                         responses_path="/v1/responses")
check("(c) 探针打到配置的路径（endpoint = base + responses_path）",
      r["endpoint"] == f"{BASE_ROOT}/v1/responses", repr(r["endpoint"]))
check("(c) 探针发的是配置的工具名（接口把 search_tool 也回出来，界面/文档能对账）",
      r["search_tool"] == "web_search_preview"
      and BODIES[-1].get("tools") == [{"type": "web_search_preview"}],
      f"{r['search_tool']} / {BODIES[-1].get('tools')!r}")
check("(c) **判据仍是检索证据**：自定义工具名 + 有 web_search_call → 支持",
      r["verdict"] == "supported" and r["n_search_calls"] == 1,
      json.dumps(r, ensure_ascii=False)[:160])
check("(c) 支持时说明里点了配置的工具名（编辑能看出这次发的是哪个工具）",
      "web_search_preview" in r["detail"], r["detail"][:160])
check("(c) 不传旋钮时探针回落到默认（与从前一模一样）",
      llm.probe_web_search(BASE_V1, "mock", "mock-model", timeout=10,
                           query="随便问")["endpoint"] == f"{BASE_V1}/responses")

# 环境变量形态也要被探针认到（界面/脚本只设环境变量时同样生效）
_old = {k: os.environ.get(k) for k in _KEYS}
os.environ["SLCN_LLM_SEARCH_TOOL"] = "web_search_preview"
os.environ["SLCN_LLM_RESPONSES_PATH"] = "/v1/responses"
try:
    r_env = llm.probe_web_search(BASE_ROOT, "mock", "mock-model", timeout=10, query="随便问")
finally:
    for k, v in _old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
check("(c) 环境变量形态的旋钮也被探针认到（两条入口等价）",
      r_env["search_tool"] == "web_search_preview"
      and r_env["endpoint"] == f"{BASE_ROOT}/v1/responses"
      and r_env["verdict"] == "supported",
      json.dumps(r_env, ensure_ascii=False)[:160])

# ── (d) CLI：`python -m slcn.llm --probe` 的两个新参数（与环境变量等价）──────
print("\n── (d) 探针 CLI 的 --llm-search-tool / --llm-responses-path ──")


def probe_cli(extra_args, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SLCN_LLM_")}
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    env.update(extra_env or {})
    p = subprocess.run([sys.executable, "-m", "slcn.llm", "--probe", "--json",
                        "--base", BASE_ROOT, "--model", "mock-model",
                        *extra_args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env, cwd=_ENGINE)
    return p


p = probe_cli(["--llm-search-tool", "web_search_preview",
               "--llm-responses-path", "/v1/responses"])
try:
    _j = json.loads(p.stdout.strip().splitlines()[-1])
except Exception:  # noqa: BLE001
    _j = {}
check("(d) CLI 两个新参数 → 探针按它们发请求并判支持（退出码 0）",
      p.returncode == 0 and _j.get("verdict") == "supported"
      and _j.get("search_tool") == "web_search_preview"
      and _j.get("endpoint") == f"{BASE_ROOT}/v1/responses",
      f"exit={p.returncode} {json.dumps(_j, ensure_ascii=False)[:160]} {p.stderr.strip()[:120]}")

p2 = probe_cli([], extra_env={"SLCN_LLM_SEARCH_TOOL": "web_search_preview",
                              "SLCN_LLM_RESPONSES_PATH": "/v1/responses"})
try:
    _j2 = json.loads(p2.stdout.strip().splitlines()[-1])
except Exception:  # noqa: BLE001
    _j2 = {}
check("(d) 不传参数、只设环境变量 → 结论与传参数时一致（两条入口等价）",
      p2.returncode == 0 and _j2.get("search_tool") == "web_search_preview"
      and _j2.get("endpoint") == f"{BASE_ROOT}/v1/responses",
      f"exit={p2.returncode} {json.dumps(_j2, ensure_ascii=False)[:160]}")

p3 = probe_cli(["--llm-search-tool", "web search"])   # 结构上不成立的名字
try:
    _j3 = json.loads(p3.stdout.strip().splitlines()[-1])
except Exception:  # noqa: BLE001
    _j3 = {}
check("(d) CLI 传了认不出的工具名 → 回落到默认（请求照发、不崩），并出声",
      _j3.get("search_tool") == "web_search" and "SLCN_LLM_SEARCH_TOOL" in p3.stderr,
      f"tool={_j3.get('search_tool')!r} stderr={p3.stderr.strip()[:120]}")

# ── (e) 端到端：真 convert.py CLI + 本地 mock，自定义工具名 ───────────────────
print("\n── (e) mock 端到端：`--llm-search-tool web_search_preview` ──")
_MOCK = os.path.join(_MACTOOLBOX, "tools", "mock_llm_server.py")
MANUSCRIPT = os.path.join(_MACTOOLBOX, "..", "2026年第5期(90)", "01 Jingru_Kou", "定稿.docx")

if not os.path.isfile(_MOCK) or not os.path.isfile(MANUSCRIPT):
    skip("(e) mock 端到端", f"缺 mock 或真实稿件（{_MOCK} / {MANUSCRIPT}）")
else:
    spec = importlib.util.spec_from_file_location("mock_llm_server_knobs", _MOCK)
    mock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mock)
    mock.STATE.mode = "responses_search"
    msrv = mock.ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
    msrv.daemon_threads = True
    mport = msrv.server_address[1]
    threading.Thread(target=msrv.serve_forever, daemon=True).start()
    mbase = f"http://127.0.0.1:{mport}/v1"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.startswith("SLCN_LLM_")}
            env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
            env["SLCN_LLM_THINKING"] = "0"
            cmd = [sys.executable, os.path.join(_ENGINE, "convert.py"),
                   "--source", MANUSCRIPT, "--out", os.path.join(tmp, "out"),
                   "--verify-refs", "--no-original", "--no-ai-merge",
                   "--llm-base", mbase, "--llm-key", "mock", "--llm-model", "mock-model",
                   "--llm-protocol", "responses", "--llm-web-search",
                   "--llm-search-tool", "web_search_preview"]
            p5 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                                env=env, cwd=_ENGINE)
            with urllib.request.urlopen(f"http://127.0.0.1:{mport}/__stats", timeout=10) as r5:
                stats = json.loads(r5.read().decode("utf-8"))
        n_tool = stats["n_tools_requested"]
        print("   [e stats] n_by_path=%(n_by_path)s n_tools_requested=%(n_tools_requested)s"
              % stats)
        check("(e) convert.py 正常结束（自定义工具名不影响退出码）", p5.returncode == 0,
              f"exit={p5.returncode} stderr={p5.stderr.strip()[-200:]}")
        check("(e) mock 的 n_tools_requested 记到的是**自定义**工具名（不是 web_search）",
              n_tool == {"web_search_preview": n_tool.get("web_search_preview", 0)}
              and n_tool.get("web_search_preview", 0) > 0,
              repr(n_tool))
        check("(e) mock 侧每条请求都带着它（recent_prompts 的 tools 全是自定义名）",
              bool(stats["last_user_prompts"])
              and all(pr["tools"] == ["web_search_preview"] for pr in stats["last_user_prompts"])
              and all(pr["protocol"] == "responses" for pr in stats["last_user_prompts"]),
              repr(stats["last_user_prompts"][:1])[:160])
        check("(e) 控制台那行通道说明写的是配置的工具名（默认时才是 web_search）",
              "responses + web_search_preview（已启用联网检索）" in p5.stdout,
              [ln for ln in p5.stdout.splitlines() if "本次核实通道" in ln][:1])
        check("(e) 有检索证据 → 引擎报「✅ 检索证据」（判据是证据，不是工具名）",
              "✅ 检索证据" in p5.stdout
              and "web_search_call" in p5.stdout,
              [ln for ln in p5.stdout.splitlines() if "✅ 检索证据" in ln][:1])
        # 探针打同一个 mock：自定义工具名 + 有证据 → 支持
        r5b = llm.probe_web_search(mbase, "mock", "mock-model", timeout=10, query="随便问",
                                   search_tool="web_search_preview")
        check("(e) 探针对同一端点用同一工具名 → 支持（n_search_calls ≥ 1）",
              r5b["verdict"] == "supported" and r5b["n_search_calls"] >= 1,
              json.dumps(r5b, ensure_ascii=False)[:160])
    finally:
        msrv.shutdown()

srv.shutdown()

print()
print("失败 %d 项" % len(FAILS) + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜跳过 {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
