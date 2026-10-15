#!/usr/bin/env python3.14
"""`--verify-refs` 云端参数升级的测试：LAST_USAGE / request_params_from_env / 默认参数口径。

五部分（全部只打 127.0.0.1 上的本地 mock 端点，**不联网**、不碰 Ollama、不跑 corpus.py）：

  ① `LAST_USAGE`：成功时从响应的 `usage` 归一（三个标准字段缺的补 0、端点多给的整数字段保留、
     `true` 之类不算数、`completion_tokens_details.reasoning_tokens` 单列），失败时清空成 `{}`，
     且**不影响 `LAST_ERROR` 的既有契约**；
  ② `request_params_from_env()`：所有文档化的环境变量组合（含未设 / 空 / 非整数的手滑值）
     ——`SLCN_LLM_JSON` **默认关**、`SLCN_LLM_MAX_TOKENS` **默认 2048**；
     （`SLCN_LLM_THINKING` 的矩阵在 tests/test_llm_thinking_and_reasoning.py 里，
     这里只确认返回值的**元组形状**是 `(response_format, max_tokens, thinking)`）
  ③ 请求体：显式要求才多出 `response_format` / `max_tokens`，默认一个都不带（老调用方零影响）；
  ④ 降级判据 `llm.json_mode_rejected()`：只认 HTTP 400 且文案提到 `response_format` / `json`
     （判据本身留着；**调用方 `convert.py` ⑪ 已不再用它**——那条通道要的是整份 bibtex 文本，
     本来就不该带 `response_format`，详见 ⑤）；
  ⑤ **端到端**：真跑一次 `convert.py --verify-refs`（源稿与产物都在临时目录里），核对
     「请求体**没有** `response_format`、`max_tokens` 走默认 2048」、逐条 tokens 与收尾汇总、
     「端点拿 response_format 当借口 400 也**不重试**」、以及
     「连续 3 条失败提前停止」**没有被这次改动改坏**。

⚠️ ⑤ 需要仓库里的模板（`<repo>/编辑笔记/模板文件`）与可用的 `convert.py`；
   只写临时目录，绝不写 `out/` 或 `dist/`。

用法：cd Engine && /opt/homebrew/bin/python3.14 tests/test_llm_usage_and_jsonmode.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
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
SCRIPT = []          # 逐次响应脚本：("200", usage|None, content) / ("400"|"500", message)


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        BODIES.append(json.loads(self.rfile.read(n).decode("utf-8")))
        i = len(BODIES) - 1
        step = SCRIPT[i] if i < len(SCRIPT) else SCRIPT[-1]   # 脚本用完后一直重复最后一步
        if step[0] == "400":
            status, body = 400, {"error": {"message": step[1], "type": "invalid_request_error"}}
        elif step[0] == "500":
            status, body = 500, {"error": {"message": step[1]}}
        else:
            status, body = 200, {"choices": [{"message": {"content": step[2]}}]}
            if step[1] is not None:
                body["usage"] = step[1]
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


srv = HTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
BASE = f"http://127.0.0.1:{PORT}/v1"
threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg = llm.LLMConfig(base=BASE, key="mock", model="mock-model")


def env_params(**kw):
    """在干净的基础上临时设置这几个环境变量，调用后原样还原。"""
    keys = ("SLCN_LLM_JSON", "SLCN_LLM_MAX_TOKENS", "SLCN_LLM_THINKING")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        for k, v in kw.items():
            os.environ[k] = v
        return llm.request_params_from_env()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── ① LAST_USAGE：成功归一、失败清空 ─────────────────────────────────────
SCRIPT.clear()
BODIES.clear()
SCRIPT.append(("200", {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168},
               "OK-1"))
out = llm.chat(cfg, "SYS", "USER", timeout=10)
check("① 成功 → LAST_USAGE 三个标准字段原样（外加 reasoning_tokens=0）",
      llm.LAST_USAGE == {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168,
                         "reasoning_tokens": 0},
      repr(llm.LAST_USAGE))
check("① 成功 → 返回值与 LAST_ERROR 契约不变",
      out == "OK-1" and llm.LAST_ERROR == "", f"{out!r} / {llm.LAST_ERROR!r}")

SCRIPT.clear()
SCRIPT.append(("200", {"prompt_tokens": 11, "completion_tokens": 40, "total_tokens": 51,
                       "completion_tokens_details": {"reasoning_tokens": 30}}, "OK-1R"))
llm.chat(cfg, "SYS", "USER", timeout=10)
check("① 推理 token 在嵌套字段里（官方文档 2026-10：completion_tokens_details."
      "reasoning_tokens）→ 单列出来，completion_tokens 保持原值",
      llm.LAST_USAGE == {"prompt_tokens": 11, "completion_tokens": 40, "total_tokens": 51,
                         "reasoning_tokens": 30},
      repr(llm.LAST_USAGE))

SCRIPT.clear()
SCRIPT.append(("200", {"prompt_tokens": 11, "completion_tokens": 40, "total_tokens": 51,
                       "completion_tokens_details": {"reasoning_tokens": "30"}}, "OK-1R"))
llm.chat(cfg, "SYS", "USER", timeout=10)
check("① reasoning_tokens 非整数（字符串）→ 记 0（只认整数字段，与 cached_tokens 同一纪律）",
      llm.LAST_USAGE.get("reasoning_tokens") == 0, repr(llm.LAST_USAGE))

SCRIPT.clear()
SCRIPT.append(("200", {"prompt_tokens": 11, "completion_tokens": 7, "cached_tokens": 3,
                       "cache_hit": True}, "OK-2"))
llm.chat(cfg, "SYS", "USER", timeout=10)
check("① 缺 total_tokens → 补 0；端点多给的**整数字段**保留",
      llm.LAST_USAGE == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 0,
                         "cached_tokens": 3, "reasoning_tokens": 0}
      and llm.LAST_USAGE.get("cache_hit") is None,
      repr(llm.LAST_USAGE))

SCRIPT.clear()
SCRIPT.append(("200", None, "OK-3"))                       # 响应里压根没有 usage
llm.chat(cfg, "SYS", "USER", timeout=10)
check("① 响应无 usage → 标准字段全 0（不是空 dict：那表示失败）",
      llm.LAST_USAGE == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                         "reasoning_tokens": 0},
      repr(llm.LAST_USAGE))

SCRIPT.clear()
SCRIPT.append(("400", "nope"))
llm.chat(cfg, "SYS", "USER", timeout=10)
check("① 失败 → LAST_USAGE 清空成 {}，LAST_ERROR 仍有原因",
      llm.LAST_USAGE == {} and "HTTP 400" in llm.LAST_ERROR,
      f"{llm.LAST_USAGE!r} / {llm.LAST_ERROR[:60]!r}")

llm.chat(llm.LLMConfig(base="", key="", model=""), "SYS", "USER")   # 未配置也是失败路径
check("① 未配置 → LAST_USAGE 也是 {}（失败路径不残留上次的用量）",
      llm.LAST_USAGE == {} and "未配置" in llm.LAST_ERROR, repr(llm.LAST_USAGE))

# ── ② request_params_from_env：所有文档化的组合 ───────────────────────────
# 口径（2026 修订）：JSON 模式**默认关**（⑪ 要的是整份 bibtex 文本，不是 JSON），
# 输出封顶**默认 2048**（1024 装不下整份 bibtex，会把后面的条目截半句）。
JSON_ON = {"type": "json_object"}
DEFAULT_MAX = 2048

rf, mt, th = env_params()
check("② 三个都没设 → JSON 模式**关**（None）+ 默认封顶 2048 + 不发 thinking",
      (rf, mt, th) == (None, DEFAULT_MAX, None), repr((rf, mt, th)))
check("② 返回值的元组形状是 (response_format, max_tokens, thinking)（三值解包）",
      isinstance(env_params(), tuple) and len(env_params()) == 3, repr(env_params()))
for v in ("0", "false", "no", "FALSE", "No", ""):
    rf, _, _ = env_params(SLCN_LLM_JSON=v)
    check(f"② SLCN_LLM_JSON={v!r} → None（不带该字段）", rf is None, repr(rf))
for v in ("1", "true", "yes", "TRUE", "Yes"):
    rf, _, _ = env_params(SLCN_LLM_JSON=v)
    check(f"② SLCN_LLM_JSON={v!r}（显式开）→ JSON 模式开", rf == JSON_ON, repr(rf))

for v, want in (("4096", 4096), ("1", 1)):
    _, mt, _ = env_params(SLCN_LLM_MAX_TOKENS=v)
    check(f"② SLCN_LLM_MAX_TOKENS={v!r} → {want}", mt == want, repr(mt))
for v in ("0", "-3", ""):
    _, mt, _ = env_params(SLCN_LLM_MAX_TOKENS=v)
    check(f"② SLCN_LLM_MAX_TOKENS={v!r} → None（不封顶）", mt is None, repr(mt))

err = io.StringIO()
outbuf = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(outbuf):
    rf, mt, _ = env_params(SLCN_LLM_MAX_TOKENS="abc")
lines = [x for x in err.getvalue().splitlines() if x.strip()]
check("② 非整数 → 退回默认 2048（不抛异常：一个手滑的配置不该让 --verify-refs 挂掉）",
      (rf, mt) == (None, DEFAULT_MAX), repr((rf, mt)))
check("② 非整数 → stderr 恰好一条说明，且带上那个手滑的值",
      len(lines) == 1 and "'abc'" in lines[0], repr(lines))
check("② 非整数 → 说明只走 stderr，不污染 stdout（stdout 是给编辑看的产物日志）",
      outbuf.getvalue() == "", repr(outbuf.getvalue()))

rf, mt, _ = env_params(SLCN_LLM_JSON="1", SLCN_LLM_MAX_TOKENS="256")
check("② JSON 显式开 + 封顶 256 可以同时成立", (rf, mt) == (JSON_ON, 256), repr((rf, mt)))

# ── ③ 请求体：显式才带，默认一个都不带 ───────────────────────────────────
SCRIPT.clear()
BODIES.clear()
SCRIPT.append(("200", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, "OK"))
llm.chat(cfg, "SYS", "USER", timeout=10)
b = BODIES[-1]
check("③ 默认调用 → 请求体仍只有 model/messages/temperature",
      set(b) == {"model", "messages", "temperature"}, repr(sorted(b)))

llm.chat(cfg, "SYS", "USER", timeout=10,
         response_format={"type": "json_object"}, max_tokens=256)
b = BODIES[-1]
check("③ 显式要求 → response_format / max_tokens 按给定值出现",
      b.get("response_format") == JSON_ON and b.get("max_tokens") == 256, repr(b))

# ── ④ json_mode_rejected：只认 HTTP 400 + 文案提到 response_format/json ──
# 判据本身留着（`chat` 的契约的一部分），但 ⑪ 已不再调用它——见 ⑤C。
SCRIPT.clear()
BODIES.clear()
SCRIPT.append(("400", "response_format is not supported by this endpoint"))
llm.chat(cfg, "SYS", "USER", timeout=10, response_format=JSON_ON, max_tokens=64)
check("④ 400 + 提到 response_format → 判据为真", llm.json_mode_rejected() is True,
      llm.LAST_ERROR[:80])

SCRIPT.clear()
SCRIPT.append(("400", "invalid JSON mode request"))
llm.chat(cfg, "SYS", "USER", timeout=10, response_format=JSON_ON, max_tokens=64)
check("④ 400 + 只提到 json（大小写不一）→ 判据为真", llm.json_mode_rejected() is True,
      llm.LAST_ERROR[:80])

SCRIPT.clear()
SCRIPT.append(("400", "exceeds the available context size"))
llm.chat(cfg, "SYS", "USER", timeout=10, response_format=JSON_ON, max_tokens=64)
check("④ 400 与 JSON 无关（上下文超限）→ 判据为假（不能瞎重试）",
      llm.json_mode_rejected() is False, llm.LAST_ERROR[:80])

SCRIPT.clear()
SCRIPT.append(("500", "response_format exploded"))
llm.chat(cfg, "SYS", "USER", timeout=10, response_format=JSON_ON, max_tokens=64)
check("④ 非 400（500）→ 判据为假（网络/服务故障不该当成参数问题）",
      llm.json_mode_rejected() is False, llm.LAST_ERROR[:80])

# ④b 调用方若要降级重试，**发出去的请求**长这样：第二次没有 response_format，max_tokens 照旧 ──
SCRIPT.clear()
BODIES.clear()
SCRIPT.append(("400", "response_format json_object is not supported"))
SCRIPT.append(("200", {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}, "OK-R"))
assert llm.chat(cfg, "SYS", "USER", timeout=10,
                response_format=JSON_ON, max_tokens=256) is None
assert llm.json_mode_rejected()
got = llm.chat(cfg, "SYS", "USER", timeout=10, response_format=None, max_tokens=256)
check("④b 降级重试（调用方自己发起）→ 第二次请求**没有** response_format 但**保留** max_tokens",
      got == "OK-R" and "response_format" not in BODIES[-1] and BODIES[-1].get("max_tokens") == 256,
      repr(BODIES[-1]))
check("④b 降级重试成功 → 用量照常写进 LAST_USAGE",
      llm.LAST_USAGE.get("completion_tokens") == 4, repr(llm.LAST_USAGE))


# ── ⑤ 端到端：真跑 convert.py --verify-refs ──────────────────────────────
# 源码固定：与 tests/test_llm_request_params.py 同一思路（本地 mock、只看真实请求/输出）。
_TEX = r"""\documentclass{article}
\title{测试题名}
\author{张三}
\begin{document}
\maketitle
正文测试。

\begin{thebibliography}{9}
\bibitem{k1} 张三. 测试文献一[J]. 逻辑学研究, 2020, 13(2): 1--10.
\end{thebibliography}
\end{document}
"""
_BIB_TPL = """@article{{k{i},
  author = {{作者{i}}},
  title = {{测试文献{i}}},
  journal = {{逻辑学研究}},
  year = {{20{yy}}},
}}
"""


def run_convert(n_entries, script, **env_extra):
    """在临时目录里跑一次真实 convert.py --verify-refs，返回 (proc, 产物目录, tmp)。

    `env_extra` 里的 `SLCN_LLM_*` 按场景给；**默认两个都不设**——外层 shell 里若恰好
    export 过它们，也先摘掉，否则测的就不是「默认口径」了。
    """
    tmp = tempfile.mkdtemp(prefix="slcn_llmtest_")
    tex = os.path.join(tmp, "main.tex")
    bib = os.path.join(tmp, "refs.bib")
    with open(tex, "w", encoding="utf-8") as f:
        f.write(_TEX)
    with open(bib, "w", encoding="utf-8") as f:
        f.write("\n".join(_BIB_TPL.format(i=i, yy=10 + i) for i in range(1, n_entries + 1)))
    BODIES.clear()
    SCRIPT.clear()
    SCRIPT.extend(script)
    env = {k: v for k, v in os.environ.items()
           if k not in ("SLCN_LLM_JSON", "SLCN_LLM_MAX_TOKENS", "SLCN_LLM_THINKING")}
    env.update(env_extra)
    env["PYTHONPATH"] = os.pathsep.join([_ENGINE, os.path.join(_ENGINE, "vendor")])
    proc = subprocess.run(
        [sys.executable, os.path.join(_ENGINE, "convert.py"),
         "--source", tex, "--out", os.path.join(tmp, "outroot"), "--bib", bib,
         "--verify-refs", "--no-original", "--author", "张三",
         # ⚠️ 2026-10-15：本套件量的是**建议通道**（任务书里的 AI 建议 + 控制台的按条告警
         #    / tokens 行）。默认路径现在还会把 AI 结果并进 reference.bib 并往任务书追加
         #    「六、合并记录」（那会改动 md 里 AI 文本的出现次数与 stdout 的行数）。
         #    所以这里显式 `--no-ai-merge`：**建议通道与从前逐字节相同**正是本套件要钉的东西
         #    （并入通道有自己的套件 tests/test_aibib.py）。
         "--no-ai-merge",
         "--llm-base", BASE, "--llm-key", "mock", "--llm-model", "mock-model"],
        capture_output=True, text=True, timeout=180, env=env, cwd=_ENGINE,
    )
    out_dir = os.path.join(tmp, "outroot", "San_Zhang")
    return proc, out_dir, tmp


USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

# 场景 A：默认口径（两个环境变量都不设）→ 请求体没有 response_format、max_tokens=2048
# 返回内容用**只可能来自 mock** 的标记串，好数「追加了几条」（任务书自身也含 @article{k1,）。
_ADVICE = "@article{k1,\n  title = {AI-ADVICE-MARKER},\n}"
proc, out_dir, tmp = run_convert(2, [("200", USAGE, _ADVICE)])
check("⑤A convert.py 正常结束", proc.returncode == 0,
      (proc.stderr or proc.stdout)[-200:])
check("⑤A 每条条目各发一次请求（2 条 → 2 次，无重试）",
      len(BODIES) == 2, f"实际 {len(BODIES)} 次")
check("⑤A 请求体**没有** response_format（⑪ 要的是整份 bibtex 文本，不是 JSON）",
      all("response_format" not in b for b in BODIES),
      repr([sorted(b) for b in BODIES]))
check("⑤A max_tokens 走默认 2048（SLCN_LLM_MAX_TOKENS 未设）",
      all(b.get("max_tokens") == DEFAULT_MAX for b in BODIES),
      repr([b.get("max_tokens") for b in BODIES]))
check("⑤A 日志里没有「JSON 模式」这类话（这条通道压根不带那个字段）",
      "JSON 模式" not in proc.stdout, repr([l for l in proc.stdout.splitlines() if "JSON" in l]))
check("⑤A 逐条打印用量（2 条成功各一行 tokens 11+7）",
      proc.stdout.count("tokens 11+7") == 2, repr(proc.stdout.count("tokens 11+7")))
check("⑤A 收尾汇总按**真实请求数**统计（2 次调用，只累加成功的用量）",
      "共 2 次调用，22 prompt + 14 completion tokens" in proc.stdout, "")
md = open(os.path.join(out_dir, "San_Zhang_文献核实.md"), encoding="utf-8").read()
check("⑤A 两条建议都照旧追加进任务书",
      "## `k1`" in md and "## `k2`" in md
      and md.count("AI-ADVICE-MARKER") == 2 and "AI 建议" in md and "须人工核实" in md, "")

# 场景 B：外层环境把 SLCN_LLM_JSON 开着 → ⑪ **仍然不读它**；
#         但 SLCN_LLM_MAX_TOKENS 是这条通道唯一该读的旋钮，必须生效。
proc, out_dir, tmp = run_convert(1, [("200", USAGE, "@article{k1,\n}")],
                                 SLCN_LLM_JSON="1", SLCN_LLM_MAX_TOKENS="512")
check("⑤B convert.py 正常结束", proc.returncode == 0, (proc.stderr or proc.stdout)[-200:])
check("⑤B SLCN_LLM_JSON=1 也不影响 ⑪：请求体仍然没有 response_format",
      len(BODIES) == 1 and "response_format" not in BODIES[0], repr([sorted(b) for b in BODIES]))
check("⑤B SLCN_LLM_MAX_TOKENS=512 照常生效（⑪ 只读这一个旋钮）",
      BODIES[0].get("max_tokens") == 512, repr(BODIES[0].get("max_tokens")))

# 场景 C：端点拿 response_format 当借口 400（旧代码会「降级重试」）→
#         现在每次失败只发一个请求、不重试，也不再把原因误标成「端点不支持 JSON 模式」
proc, out_dir, tmp = run_convert(6, [("400", "response_format json_object is not supported")])
check("⑤C convert.py 仍正常结束（AI 失败不影响出稿）", proc.returncode == 0,
      (proc.stderr or proc.stdout)[-200:])
check("⑤C 6 条只尝试 3 条就提前停止，且每次失败**只发一个请求**（无降级重试）",
      len(BODIES) == 3, f"实际 {len(BODIES)} 次")
check("⑤C 不打印「端点不支持 JSON 模式」的降级说明（这段逻辑已随降级一起删除）",
      "端点不支持 JSON 模式" not in proc.stdout and "改用普通模式" not in proc.stdout, "")
check("⑤C 用户可见的提前停止说明照旧",
      "连续 3 条失败后已提前停止，共尝试 3/6 条" in proc.stdout
      and "本次**只出任务书**" in proc.stdout, "")
check("⑤C 没有成功调用也要报汇总（调用发生过 → 3 次调用、0 tokens）",
      "共 3 次调用，0 prompt + 0 completion tokens" in proc.stdout, "")
md = open(os.path.join(out_dir, "San_Zhang_文献核实.md"), encoding="utf-8").read()
check("⑤C 一条建议都没追加（失败不写盘，与从前一致）",
      "AI 建议" not in md and "须人工核实" not in md, "")

# 场景 D：持续 500（与 JSON 无关的服务故障）→ 行为与场景 C 一致，提前停止不被改坏
proc, out_dir, tmp = run_convert(6, [("500", "internal boom")])
check("⑤D convert.py 仍正常结束（AI 失败不影响出稿）", proc.returncode == 0,
      (proc.stderr or proc.stdout)[-200:])
check("⑤D 6 条只尝试 3 条就提前停止（旧行为一行未改）", len(BODIES) == 3,
      f"实际 {len(BODIES)} 次")
check("⑤D 每次失败只有一个请求、都不带 response_format",
      all("response_format" not in b for b in BODIES), repr([sorted(b) for b in BODIES]))
check("⑤D 没有成功调用也要报汇总（3 次调用、0 tokens）",
      "共 3 次调用，0 prompt + 0 completion tokens" in proc.stdout, "")

# 场景 E：bib 里没有条目 → 一次调用都没发生，就不该刷一行「0 次调用」
proc, out_dir, tmp = run_convert(0, [("200", USAGE, "unused")])
check("⑤E 0 条条目 → 正常结束、一次也不调用",
      proc.returncode == 0 and len(BODIES) == 0, f"rc={proc.returncode} 请求 {len(BODIES)} 次")
check("⑤E 没调用过就不打印用量汇总（日志不留一行无意义的 0）",
      "次调用" not in proc.stdout and "prompt +" not in proc.stdout, "")

srv.shutdown()
print()
print("SUMMARY: %d FAIL" % len(FAILS) + ("  -> " + ", ".join(FAILS) if FAILS else "  (all PASS)"))
sys.exit(1 if FAILS else 0)
