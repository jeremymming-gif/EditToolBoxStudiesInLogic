#!/usr/bin/env python3.14
"""`thinking` 请求字段 + 推理 token 可见性的测试（本地 mock 端点，不联网、不碰 Ollama）。

判据来自官方文档（2026-10）：
  - https://api-docs.deepseek.com/zh-cn/api/create-chat-completion
    `thinking`: `{"type": "enabled"|"disabled"}`，**默认 enabled**；
    `finish_reason`: stop / length / content_filter / tool_calls /
    insufficient_system_resource / aborted；思考模式下 `temperature` **没有效果**；
  - https://api-docs.deepseek.com/zh-cn/quick_start/pricing
    `usage.completion_tokens_details.reasoning_tokens`（思维链）**按输出计费**。

五部分：
  ① `SLCN_LLM_THINKING` 取值矩阵（含无法识别的值 → None + **恰好一条** stderr 说明，不抛异常）；
  ② 请求体：默认调用**没有** thinking（老调用方逐字节兼容），显式要求才出现；
  ③ 降级判据 `llm.thinking_rejected()`：只认 HTTP 400 且文案提到 thinking；
  ④ `LAST_USAGE["reasoning_tokens"]`：从嵌套字段取，缺失 / 非整数 → 0；
  ⑤ **端到端**（真跑 `convert.py --verify-refs`）：
     A 端点 400 拒收 thinking → 该条带它失败、去掉它重试成功，其余条目都不再带它，
       且只打印**一条**中文降级说明；
     B 与 thinking 无关的 400（上下文超限）→ **不重试**（不能瞎重试，白跑一倍请求）；
     C 重试也失败 → 失败处理照旧（真失败不被降级路径吞掉）；
     D 响应带 reasoning_tokens → 逐条 `（含推理 r）` + 收尾 `（含推理 R）`；
       为 0 时一个字都不多写（同时证明不设 `SLCN_LLM_THINKING` 时请求体不带该字段）。

⚠️ ⑤ 需要仓库里的模板（`<repo>/编辑笔记/模板文件`）与可用的 `convert.py`；
   只写临时目录，绝不写 `out/` 或 `dist/`。

用法：cd Engine && /opt/homebrew/bin/python3.14 tests/test_llm_thinking_and_reasoning.py
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
RAWS = []            # 同上，保留原始字节（默认请求体要与从前逐字节一致）
SCRIPT = []          # 逐步响应脚本：
#   200 → {"status": 200, "content": str, "finish": str|省略, "usage": dict|None}
#   非 200 → {"status": 400|500, "message": str}
OFF = {"type": "disabled"}
ON = {"type": "enabled"}


def _read_artifact(out_dir: str, name: str, proc=None) -> str:
    r"""读引擎产物；**读不到就把诊断打出来**，而不是抛 FileNotFoundError。

    为什么要有它：2026-10-16 windows-latest 上 `test_llm_*` 只报了一句
    `FileNotFoundError: ...\outroot\San_Zhang\San_Zhang_文献核实.md` ——
    看不出 convert.py 是崩了、还是把产物写到了别处。这里把退出码、stdout/stderr 末段
    与产物目录树都打出来，一眼能判断。返回空串，让后续断言以"内容缺失"的方式失败。
    """
    path = os.path.join(out_dir, name)
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()

    print(f"  ⚠️ 没找到产物：{path}")
    if proc is not None:
        print(f"     convert.py 退出码：{getattr(proc, 'returncode', '?')}")
        for label in ("stdout", "stderr"):
            text = getattr(proc, label, "") or ""
            lines = text.strip().split("\n")
            print(f"     convert.py {label}（末 {min(20, len(lines))} 行）：")
            for line in lines[-20:]:
                print("       " + line)
    print("     产物目录树（前 25 个文件）：")
    shown = 0
    for root, _dirs, files in os.walk(out_dir):
        for f in sorted(files):
            if shown >= 25:
                break
            print("       " + os.path.relpath(os.path.join(root, f), out_dir))
            shown += 1
        if shown >= 25:
            break
    return ""


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8")
        RAWS.append(raw)
        BODIES.append(json.loads(raw))
        i = len(BODIES) - 1
        step = SCRIPT[i] if i < len(SCRIPT) else SCRIPT[-1]   # 脚本用完后一直重复最后一步
        if step["status"] != 200:
            status, body = step["status"], {"error": {"message": step.get("message", "boom")}}
        else:
            choice = {"message": {"content": step["content"]}}
            if "finish" in step:
                choice["finish_reason"] = step["finish"]
            status, body = 200, {"choices": [choice]}
            if step.get("usage") is not None:
                body["usage"] = step["usage"]
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = HTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
BASE = f"http://127.0.0.1:{PORT}/v1"
threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg = llm.LLMConfig(base=BASE, key="mock", model="mock-model")
USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


def call(**step):
    """只放一步脚本，跑一次 `chat()`，返回内容。"""
    SCRIPT.clear()
    SCRIPT.append(step)
    return llm.chat(cfg, "SYS", "USER", timeout=10)


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


# ── ① SLCN_LLM_THINKING 取值矩阵 ─────────────────────────────────────────
# 默认**不发这个字段**：官方文档写明 thinking 默认就是 enabled，而本项目的通道
# （⑪ 纯抽取）不需要思维链——显式发 enabled 只会让请求体不再与从前逐字节相同。
_, _, th = env_params()
check("① 未设 → None（请求体不带 thinking，与从前逐字节相同）", th is None, repr(th))
_, _, th = env_params(SLCN_LLM_THINKING="")
check("① 空串（export SLCN_LLM_THINKING=）→ None（视同未设）", th is None, repr(th))
for v in ("0", "false", "no", "off", "none", "FALSE", "No", "Off", " none "):
    _, _, th = env_params(SLCN_LLM_THINKING=v)
    check(f"① SLCN_LLM_THINKING={v!r} → {{'type': 'disabled'}}", th == OFF, repr(th))
for v in ("1", "true", "yes", "on", "TRUE", "Yes", " on "):
    _, _, th = env_params(SLCN_LLM_THINKING=v)
    check(f"① SLCN_LLM_THINKING={v!r} → {{'type': 'enabled'}}", th == ON, repr(th))

err = io.StringIO()
outbuf = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(outbuf):
    rf, mt, th = env_params(SLCN_LLM_THINKING="Maybe")
lines = [x for x in err.getvalue().splitlines() if x.strip()]
check("① 无法识别的值 → None（不抛异常：手滑的配置不该让 --verify-refs 挂掉）",
      th is None and (rf, mt) == (None, 2048), repr((rf, mt, th)))
check("① 无法识别的值 → stderr 恰好一条说明，且**原样**带上那个手滑的值",
      len(lines) == 1 and "'Maybe'" in lines[0], repr(lines))
check("① 无法识别的值 → 说明只走 stderr，不污染 stdout（stdout 是给编辑看的产物日志）",
      outbuf.getvalue() == "", repr(outbuf.getvalue()))

# ── ② 请求体：默认不带，显式才带 ─────────────────────────────────────────
BODIES.clear()
RAWS.clear()
call(status=200, content="OK", finish="stop", usage=USAGE)
check("② 默认调用 → 请求体仍只有 model/messages/temperature（没有 thinking）",
      set(BODIES[-1]) == {"model", "messages", "temperature"}, repr(sorted(BODIES[-1])))
check("② 默认调用 → 请求体**逐字节**等于旧格式（老调用方零影响）",
      RAWS[-1] == '{"model": "mock-model", "messages": [{"role": "system", "content": "SYS"}, '
                  '{"role": "user", "content": "USER"}], "temperature": 0}',
      RAWS[-1])

SCRIPT.clear()
SCRIPT.append({"status": 200, "content": "OK", "finish": "stop", "usage": USAGE})
llm.chat(cfg, "SYS", "USER", timeout=10, thinking=OFF)
check("② thinking 显式给出 → 原样出现在请求体",
      BODIES[-1].get("thinking") == OFF, repr(BODIES[-1].get("thinking")))
llm.chat(cfg, "SYS", "USER", timeout=10, thinking=ON, max_tokens=256)
check("② thinking + max_tokens 可以同时成立（temperature 仍照发）",
      BODIES[-1].get("thinking") == ON and BODIES[-1].get("max_tokens") == 256
      and BODIES[-1].get("temperature") == 0, repr(BODIES[-1]))

# ── ③ thinking_rejected：只认 HTTP 400 + 文案提到 thinking ───────────────
call(status=400, message="Unknown parameter: 'thinking'")
check("③ 400 + Unknown parameter: 'thinking' → 判据为真", llm.thinking_rejected() is True,
      llm.LAST_ERROR[:90])
call(status=400, message="thinking is not supported by this endpoint")
check("③ 400 + thinking is not supported → 判据为真", llm.thinking_rejected() is True,
      llm.LAST_ERROR[:90])
call(status=400, message="exceeds the available context size")
check("③ 400 与 thinking 无关（上下文超限）→ 判据为假（不能瞎重试）",
      llm.thinking_rejected() is False, llm.LAST_ERROR[:90])
call(status=500, message="thinking exploded")
check("③ 非 400（500）→ 判据为假（服务故障不该当成参数问题）",
      llm.thinking_rejected() is False, llm.LAST_ERROR[:90])
call(status=200, content="OK", finish="stop", usage=USAGE)
check("③ 成功调用后 LAST_ERROR 清空 → 判据为假（不残留上一次的 400）",
      llm.thinking_rejected() is False, repr(llm.LAST_ERROR))

# ── ④ reasoning_tokens 解析：嵌套字段、缺失 / 非整数 → 0 ─────────────────
call(status=200, content="OK", finish="stop",
     usage={"prompt_tokens": 11, "completion_tokens": 40, "total_tokens": 51,
            "completion_tokens_details": {"reasoning_tokens": 30}})
check("④ completion_tokens_details.reasoning_tokens=30 → LAST_USAGE 里记 30（其余字段不动）",
      llm.LAST_USAGE == {"prompt_tokens": 11, "completion_tokens": 40, "total_tokens": 51,
                         "reasoning_tokens": 30}, repr(llm.LAST_USAGE))
for label, usage in (
    ("没有 completion_tokens_details", {"prompt_tokens": 1, "completion_tokens": 2}),
    ("completion_tokens_details 不是 dict", {"prompt_tokens": 1, "completion_tokens": 2,
                                             "completion_tokens_details": 7}),
    ("reasoning_tokens='30'（字符串）", {"prompt_tokens": 1, "completion_tokens": 2,
                                         "completion_tokens_details": {"reasoning_tokens": "30"}}),
    ("reasoning_tokens=true（bool）", {"prompt_tokens": 1, "completion_tokens": 2,
                                       "completion_tokens_details": {"reasoning_tokens": True}}),
):
    call(status=200, content="OK", finish="stop", usage=usage)
    check(f"④ {label} → reasoning_tokens=0（缺/非整数都不算 token 数）",
          llm.LAST_USAGE.get("reasoning_tokens") == 0, repr(llm.LAST_USAGE))


# ── ⑤ 端到端：真跑 convert.py --verify-refs ──────────────────────────────
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
_ADVICE = "@article{k1,\n  title = {AI-OK-MARKER},\n}"


def run_convert(n_entries, script, **env_extra):
    """在临时目录里跑一次真实 convert.py --verify-refs，返回 (proc, 任务书正文)。"""
    tmp = tempfile.mkdtemp(prefix="slcn_thinktest_")
    tex = os.path.join(tmp, "main.tex")
    bib = os.path.join(tmp, "refs.bib")
    with open(tex, "w", encoding="utf-8") as f:
        f.write(_TEX)
    with open(bib, "w", encoding="utf-8") as f:
        f.write("\n".join(_BIB_TPL.format(i=i, yy=10 + i) for i in range(1, n_entries + 1)))
    BODIES.clear()
    RAWS.clear()
    SCRIPT.clear()
    SCRIPT.extend(script)
    # 三个 SLCN_LLM_* 一律先摘掉：外层 shell 里 export 过什么，都不该改变本测试的口径
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
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, env=env, cwd=_ENGINE,
    )
    out_dir = os.path.join(tmp, "outroot", "San_Zhang")
    md = _read_artifact(out_dir, "San_Zhang_文献核实.md", proc)
    return proc, md


_OK = {"status": 200, "content": _ADVICE, "finish": "stop", "usage": USAGE}

# A：端点 400 拒收 thinking（SLCN_LLM_THINKING=0）→ 该条去掉字段重试成功，
#    后续条目直接不带它（不该每条都白撞一次 400）。
proc, md = run_convert(3, [{"status": 400, "message": "Unknown parameter: 'thinking'"}, _OK],
                       SLCN_LLM_THINKING="0")
check("⑤A convert.py 正常结束（降级路径不改退出码）", proc.returncode == 0,
      (proc.stderr or proc.stdout)[-200:])
check("⑤A 3 条条目共 4 次请求：第 1 条带 thinking 失败 + 去掉它重试，后两条各 1 次",
      len(BODIES) == 4, f"实际 {len(BODIES)} 次")
check("⑤A 第一次请求**带着** thinking（字段确实发出去了，不是没生效）",
      BODIES[0].get("thinking") == OFF, repr(BODIES[0].get("thinking")))
check("⑤A 重试请求**没有** thinking",
      "thinking" not in BODIES[1], repr(sorted(BODIES[1])))
check("⑤A 其余条目也不再发 thinking（不重复撞 400）",
      all("thinking" not in b for b in BODIES[2:]), repr([sorted(b) for b in BODIES[2:]]))
check("⑤A 只打印一条中文降级说明（说明端点不支持 + 后续都不再发）",
      proc.stdout.count("端点不支持 thinking 字段") == 1
      and "其余条目也不再发送它" in proc.stdout,
      repr([l for l in proc.stdout.splitlines() if "thinking" in l]))
check("⑤A max_tokens 仍照发（降级只去掉 thinking，别的参数不动）",
      all(b.get("max_tokens") == 2048 for b in BODIES), repr([b.get("max_tokens") for b in BODIES]))
check("⑤A 3 条建议都追加成功、汇总按真实请求数（4 次）",
      md.count("AI-OK-MARKER") == 3 and "已追加 3 条 AI 建议" in proc.stdout
      and "共 4 次调用，33 prompt + 21 completion tokens" in proc.stdout, "")
check("⑤A reasoning_tokens 为 0 → 日志里一个字都不多写",
      "含推理" not in proc.stdout, repr([l for l in proc.stdout.splitlines() if "tokens" in l]))

# B：与 thinking 无关的 400（上下文超限）→ 不许重试（重试只会每条白跑一次）
proc, md = run_convert(3, [{"status": 400, "message": "exceeds the available context size"}],
                       SLCN_LLM_THINKING="0")
check("⑤B 3 条只尝试 3 条（连续失败提前停止），每次失败**只发一个请求**",
      proc.returncode == 0 and len(BODIES) == 3, f"rc={proc.returncode} 请求 {len(BODIES)} 次")
check("⑤B 每条都带着 thinking（没有降级），也不打印降级说明",
      all(b.get("thinking") == OFF for b in BODIES)
      and "端点不支持 thinking 字段" not in proc.stdout, repr([sorted(b) for b in BODIES]))
check("⑤B 真失败照旧如实报告（只出任务书）",
      "本次**只出任务书**" in proc.stdout
      and "共 3 次调用，0 prompt + 0 completion tokens" in proc.stdout, "")

# C：重试**也**失败 → 走正常失败处理（降级路径不许把真失败藏起来）
proc, md = run_convert(3, [{"status": 400, "message": "thinking is not supported"},
                           {"status": 500, "message": "internal boom"}],
                       SLCN_LLM_THINKING="1")
check("⑤C 端点先拒收 thinking、重试又 500 → 仍然只出任务书、退出码 0",
      proc.returncode == 0 and "本次**只出任务书**" in proc.stdout,
      (proc.stderr or proc.stdout)[-200:])
check("⑤C 请求数如实统计：4 次（1 带字段 + 1 重试 + 后两条各 1 次）",
      len(BODIES) == 4 and "共 4 次调用，0 prompt + 0 completion tokens" in proc.stdout,
      f"实际 {len(BODIES)} 次")
check("⑤C 第一条带 thinking、重试与后续条目都不带（记忆生效）",
      BODIES[0].get("thinking") == ON and all("thinking" not in b for b in BODIES[1:]),
      repr([sorted(b) for b in BODIES]))
check("⑤C 一条建议都没追加（真失败没有被降级路径吞掉）",
      "AI-OK-MARKER" not in md and "须人工核实" not in md, "")

# D：推理 token 显示（官方文档：reasoning_tokens 按**输出**计费）
_USAGE_R = {"prompt_tokens": 11, "completion_tokens": 40, "total_tokens": 51,
            "completion_tokens_details": {"reasoning_tokens": 30}}
proc, md = run_convert(2, [{"status": 200, "content": _ADVICE, "finish": "stop",
                            "usage": _USAGE_R}])
check("⑤D convert.py 正常结束", proc.returncode == 0, (proc.stderr or proc.stdout)[-200:])
check("⑤D 逐条用量写出推理 token：tokens 11+40（含推理 30）× 2 条",
      proc.stdout.count("tokens 11+40（含推理 30）") == 2,
      repr([l for l in proc.stdout.splitlines() if "tokens" in l]))
check("⑤D 收尾汇总也带上推理合计：completion 80 里含推理 60",
      "共 2 次调用，22 prompt + 80 completion tokens（含推理 60）" in proc.stdout,
      repr([l for l in proc.stdout.splitlines() if "次调用" in l]))
check("⑤D finish_reason=stop → 不误报（告警与任务书标记都不出现）",
      "未正常结束" not in proc.stdout and "截断" not in proc.stdout
      and "未正常结束" not in md and md.count("AI-OK-MARKER") == 2, "")
check("⑤D 不设 SLCN_LLM_THINKING → 所有请求都不带 thinking",
      all("thinking" not in b for b in BODIES),
      repr([sorted(b) for b in BODIES]))

srv.shutdown()
print()
print("SUMMARY: %d FAIL" % len(FAILS) + ("  -> " + ", ".join(FAILS) if FAILS else "  (all PASS)"))
sys.exit(1 if FAILS else 0)
