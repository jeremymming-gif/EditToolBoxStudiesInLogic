#!/usr/bin/env python3.14
"""「截断不得被静默接受」的测试：`llm.LAST_FINISH` + ⑪ 的截断标注。

四部分（全部只打 127.0.0.1 上的本地 mock 端点，**不联网**、不碰 Ollama、不跑 corpus.py）：

  ① `LAST_FINISH` 的取值口径：成功时取自 `choices[0].finish_reason`（trim；
     `stop` / `length` 原样，不做大小写折叠——调用方按规范小写比对 `== "length"`）；
     字段缺失 / `None` / 非字符串（`5`、`true`、list、dict）一律当「端点没给」= `""`，
     **且不影响成功返回与 `LAST_ERROR` 契约**；
  ② 失败路径绝不残留：先做一次 `finish_reason=length` 的成功调用「下毒」，再分别走
     HTTP 400 / 500 / 端点不可达 / 未配置 / 200 但结构残缺 —— `LAST_FINISH` 必须回到 `""`
     （残留上次的 `length` 会让 ⑪ 给**这一条**贴错截断标记，比不标更糟）；
  ③ **端到端**：真跑一次 `convert.py --verify-refs`，mock 对 k1 回 `finish_reason=length`
     → stdout 有一句中文告警，且任务书里 k1 的建议块**正文之前**带截断标记；
     同一次运行里 k2 回 `stop` → **不许**被标注（判据是按条读的，不是整期一把）；
  ④ **端到端（正常）**：全部回 `stop` → 告警与标记一个都不出现，建议照旧追加；
     再跑一次「老端点」（响应里没有 finish_reason、以及非字符串值）→ 同样不误报，
     且 `convert.py` 不因这个字段缺失而失败。
  ⑤ **端到端（其余不干净收尾）**：官方文档（2026-10）的取值不止 `length`——
     `content_filter` / `insufficient_system_resource` / `aborted` / `tool_calls`
     以及「不认识的取值」都要按条告警 + 任务书里按条标记（从前只盯 `length`，
     这几种会**静默**当成完整答案追加进任务书）。

⚠️ ③④ 需要仓库里的模板（`<repo>/编辑笔记/模板文件`）与可用的 `convert.py`；
   只写临时目录，绝不写 `out/` 或 `dist/`。

用法：cd Engine && /opt/homebrew/bin/python3.14 tests/test_llm_finish_reason.py
"""
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
SCRIPT = []          # 逐步响应脚本（dict）：
#   200 → {"status": 200, "content": str, "finish": <任意值>, "usage": dict|None}
#         省略 "finish" 键 = 响应里**压根没有** finish_reason（老端点行为）
#   非 200 → {"status": 400|500, "message": str}
#   任意 → {"status": ..., "json": {...}} 直接给整段响应体（测畸形结构）
MISSING = object()   # 「这个键不存在」的哨兵（`None` 是「键存在但值为 null」，两者都要测）


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
        if not SCRIPT:                       # 脚本空 = 测试自己写错了，别静默回 200
            step = {"status": 500, "message": "no script"}
        else:
            step = SCRIPT[i] if i < len(SCRIPT) else SCRIPT[-1]   # 脚本用完后重复最后一步
        if "json" in step:                   # 整段响应体原样发（畸形结构场景）
            status, body = step["status"], step["json"]
        elif step["status"] != 200:
            status, body = step["status"], {"error": {"message": step.get("message", "boom")}}
        else:
            choice = {"message": {"content": step["content"]}}
            if step.get("finish", MISSING) is not MISSING:
                choice["finish_reason"] = step["finish"]
            status, body = 200, {"choices": [choice]}
            if step.get("usage") is not None:
                body["usage"] = step["usage"]
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
USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


def call(**step):
    """只放一步脚本，跑一次 `chat()`，返回内容。"""
    SCRIPT.clear()
    BODIES.clear()
    SCRIPT.append(step)
    return llm.chat(cfg, "SYS", "USER", timeout=10)


def poison():
    """把 LAST_FINISH 置成非空（先做一次 `finish_reason=length` 的成功调用）。

    为什么要「下毒」：只有在一个**已知非空**的状态上再失败，才能证明失败路径真的清过它；
    否则 `""` 可能只是因为它本来就是空的（假通过）。
    """
    SCRIPT.clear()
    BODIES.clear()
    SCRIPT.append({"status": 200, "content": "CUT", "finish": "length", "usage": USAGE})
    assert llm.chat(cfg, "SYS", "USER", timeout=10) == "CUT"
    assert llm.LAST_FINISH == "length", repr(llm.LAST_FINISH)


# ── ① LAST_FINISH 取值口径：成功路径 ─────────────────────────────────────
for fr, want in (("stop", "stop"), ("length", "length"), ("tool_calls", "tool_calls"),
                 ("content_filter", "content_filter"),
                 ("  length  ", "length"),                  # trim（有的端点带空白）
                 ("STOP", "STOP"),                          # **不做**大小写折叠
                 ("", "")):
    out = call(status=200, content="OK", finish=fr, usage=USAGE)
    check(f"① finish_reason={fr!r} → LAST_FINISH={want!r}",
          llm.LAST_FINISH == want, repr(llm.LAST_FINISH))
    check(f"① finish_reason={fr!r} → 成功返回与 LAST_ERROR 契约不受影响",
          out == "OK" and llm.LAST_ERROR == "", f"{out!r} / {llm.LAST_ERROR!r}")

# 缺字段 / None / 非字符串：一律当「端点没给」，**不能**让这次成功调用变成失败
for label, step in (
    ("响应里没有 finish_reason（老端点）", {"status": 200, "content": "OK", "usage": USAGE}),
    ("finish_reason=None", {"status": 200, "content": "OK", "finish": None, "usage": USAGE}),
    ("finish_reason=5（数字）", {"status": 200, "content": "OK", "finish": 5, "usage": USAGE}),
    ("finish_reason=true（bool）", {"status": 200, "content": "OK", "finish": True, "usage": USAGE}),
    ("finish_reason=['length']（list）",
     {"status": 200, "content": "OK", "finish": ["length"], "usage": USAGE}),
    ("finish_reason={'reason': 'length'}（dict）",
     {"status": 200, "content": "OK", "finish": {"reason": "length"}, "usage": USAGE}),
):
    out = call(**step)
    check(f"① {label} → LAST_FINISH=''（当作端点没给，不污染 ==\"length\" 判据）",
          llm.LAST_FINISH == "", repr(llm.LAST_FINISH))
    check(f"① {label} → 仍算成功（返回内容、LAST_ERROR 清空）",
          out == "OK" and llm.LAST_ERROR == "", f"{out!r} / {llm.LAST_ERROR!r}")

# ── ② 失败路径绝不残留上一次的 finish_reason ─────────────────────────────
def fail_case(label, step, do_call, expect):
    """poison() → 制造失败 → 断言 LAST_FINISH 回到 ''。（每次失败前重新下毒。）"""
    poison()
    out = do_call(step)
    check(f"② {label} → 返回 None（契约不变）、LAST_ERROR 有原因、LAST_FINISH 清空",
          out is None and llm.LAST_FINISH == "" and expect in llm.LAST_ERROR,
          f"{out!r} / {llm.LAST_FINISH!r} / {llm.LAST_ERROR[:60]!r}")


def _run(step, config=None):
    SCRIPT.clear()
    BODIES.clear()
    SCRIPT.append(step)
    return llm.chat(config or cfg, "SYS", "USER", timeout=5)


fail_case("HTTP 400", {"status": 400, "message": "nope"}, _run, "HTTP 400")
fail_case("HTTP 500", {"status": 500, "message": "internal boom"}, _run, "HTTP 500")
# 端点不可达（连接被拒）：非 HTTP 的异常路径
fail_case("端点不可达",
          None,
          lambda _s: _run({"status": 200, "content": "unused"},
                          config=llm.LLMConfig(base="http://127.0.0.1:9/v1", key="x", model="m")),
          "Error")
# 未配置（base/model 空）：连请求都不发
fail_case("未配置",
          None,
          lambda _s: _run({"status": 200, "content": "unused"},
                          config=llm.LLMConfig(base="", key="", model="")),
          "未配置")
# 200 但结构残缺：先取「内容」就抛，因此**不许**把 finish_reason 写进 LAST_FINISH
fail_case("200 缺 choices", {"status": 200, "json": {}}, _run, "KeyError")
fail_case("200 choices 为空表", {"status": 200, "json": {"choices": []}}, _run, "IndexError")
fail_case("200 只有 finish_reason、没有 message",
          {"status": 200, "json": {"choices": [{"finish_reason": "length"}]}}, _run, "KeyError")


# ── ③④ 端到端：真跑 convert.py --verify-refs ──────────────────────────────
# 源稿与产物都在临时目录里（与 tests/test_llm_usage_and_jsonmode.py 同一思路）。
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

# 告警行与任务书里的标记行：与 convert.py ⑪ 的文案逐字对应（改了文案就必须改这里）
WARN_CORE = "⚠ k1：返回被 max_tokens 截断（finish_reason=length），本条**不完整**，建议调大 SLCN_LLM_MAX_TOKENS 或人工核实"
MARK = "> ⚠️ 本条 AI 返回被截断（finish_reason=length），内容不完整。"


def run_convert(n_entries, script):
    """在临时目录里跑一次真实 convert.py --verify-refs，返回 (proc, 产物目录)。"""
    tmp = tempfile.mkdtemp(prefix="slcn_finishtest_")
    tex = os.path.join(tmp, "main.tex")
    bib = os.path.join(tmp, "refs.bib")
    with open(tex, "w", encoding="utf-8") as f:
        f.write(_TEX)
    with open(bib, "w", encoding="utf-8") as f:
        f.write("\n".join(_BIB_TPL.format(i=i, yy=10 + i) for i in range(1, n_entries + 1)))
    BODIES.clear()
    SCRIPT.clear()
    SCRIPT.extend(script)
    # SLCN_LLM_MAX_TOKENS 明确不设 → 走默认 2048（截断场景的成因要可追溯）；
    # SLCN_LLM_THINKING 也不设 → 请求体不带 thinking（老调用方的字节级兼容由
    # tests/test_llm_thinking_and_reasoning.py 单独断言）
    env = {k: v for k, v in os.environ.items()
           if k not in ("SLCN_LLM_JSON", "SLCN_LLM_MAX_TOKENS", "SLCN_LLM_THINKING")}
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
    md = open(os.path.join(out_dir, "San_Zhang_文献核实.md"), encoding="utf-8").read()
    return proc, md


# ③ 截断场景：k1 被 max_tokens 截断（半条 bibtex），k2 正常收尾。
#    断言「按条标注」：只标 k1，不许把整期都标成截断，也不许一条都不标。
_CUT_K1 = "@article{k1,\n  title = {AI-CUT-K1},\n"          # 故意没有收尾的 }
_OK_K2 = "@article{k2,\n  title = {AI-OK-K2},\n}"
proc, md = run_convert(2, [
    {"status": 200, "content": _CUT_K1, "finish": "length", "usage": USAGE},
    {"status": 200, "content": _OK_K2, "finish": "stop", "usage": USAGE},
])
check("③A convert.py 正常结束（截断不改变退出码、不影响出稿）", proc.returncode == 0,
      (proc.stderr or proc.stdout)[-200:])
check("③A stdout 恰好一条截断告警，含条目号 / finish_reason / 调整建议",
      proc.stdout.count("返回被 max_tokens 截断") == 1 and WARN_CORE in proc.stdout,
      repr([l for l in proc.stdout.splitlines() if "截断" in l]))
check("③A 未被截断的 k2 不告警（判据按条读，不能整期一把）",
      proc.stdout.count("截断") == 1 and "⚠ k2" not in proc.stdout, repr(proc.stdout.count("截断")))
_mark_at = md.find(MARK)      # detail 里也要用，且不能因为「没找到」就抛异常（那会假崩）
# ⚠️ 顺序断言只能在**追加的建议块**里做：任务书本身（`_refmark.build` 生成的正文）里
#    也有 `## \`k1\`` / `## \`k2\`` 两节，拿全文 index 比会把建议块之前的位置也算进来。
_tail = md[md.index("AI 建议（**须人工核实**，工具不保证正确）"):]
_h1, _h2, _m = _tail.find("## `k1`"), _tail.find("## `k2`"), _tail.find(MARK)
check("③A 任务书里恰好一处截断标记，且落在 k1 一节内（不误伤 k2）",
      md.count(MARK) == 1 and -1 < _h1 < _m < _h2,
      repr([l for l in md.splitlines() if "截断" in l]))
check("③A 标记在**该条建议正文之前**（引用行 → 空行 → 建议正文）",
      MARK + "\n\n" + _CUT_K1.strip() in md,
      repr(md[_mark_at:_mark_at + 120]) if _mark_at >= 0 else "(任务书里没有标记行)")
check("③A 截断的那条仍然保留（只标注、不丢弃——编辑要能看到已给的部分）",
      "AI-CUT-K1" in md and "AI-OK-K2" in md, "")
check("③A 任务书结构与「须人工核实」标注照旧",
      "AI 建议（**须人工核实**，工具不保证正确）" in md and "## `k1`" in md, "")
check("③A 逐条 tokens 与收尾汇总未被改坏",
      proc.stdout.count("tokens 11+7") == 2
      and "共 2 次调用，22 prompt + 14 completion tokens" in proc.stdout, "")
check("③A max_tokens 仍按默认 2048 发出（截断的成因可追溯：告警里指向的就是这个旋钮）",
      len(BODIES) == 2 and all(b.get("max_tokens") == 2048 for b in BODIES),
      repr([b.get("max_tokens") for b in BODIES]))

# ④ 正常场景（finish_reason=stop）：告警与标记都不许出现，建议照旧追加
proc, md = run_convert(2, [{"status": 200, "content": _OK_K2, "finish": "stop", "usage": USAGE}])
check("④A convert.py 正常结束", proc.returncode == 0, (proc.stderr or proc.stdout)[-200:])
check("④A finish_reason=stop → stdout 一句截断告警都没有",
      "截断" not in proc.stdout and "不完整" not in proc.stdout,
      repr([l for l in proc.stdout.splitlines() if "截断" in l or "不完整" in l]))
check("④A finish_reason=stop → 任务书里没有截断标记",
      MARK not in md and "截断" not in md, repr([l for l in md.splitlines() if "截断" in l]))
check("④A 建议照旧追加、汇总照旧（正常路径零影响）",
      md.count("AI-OK-K2") == 2 and "共 2 次调用，22 prompt + 14 completion tokens" in proc.stdout, "")

# ④B 老端点：响应里没有 finish_reason / 显式空串 / 非字符串值 → 都不误报，且不失败
#     （空串是「端点没给」的归一结果，**不是** unclean：这条通道要能继续接老端点）
proc, md = run_convert(3, [
    {"status": 200, "content": _OK_K2, "usage": USAGE},                 # 无 finish_reason 键
    {"status": 200, "content": _OK_K2, "finish": "", "usage": USAGE},   # 显式空串
    {"status": 200, "content": _OK_K2, "finish": 5, "usage": USAGE},    # 非字符串
])
check("④B 缺字段 / 空串 / 非字符串 → convert.py 仍正常结束（该字段只用来判断收尾是否干净，"
      "不能让整条通道挂掉）",
      proc.returncode == 0, (proc.stderr or proc.stdout)[-200:])
check("④B 缺字段 / 空串 / 非字符串 → 不误报（告警与标记都不出现，建议照旧追加）",
      "截断" not in proc.stdout and "未正常结束" not in proc.stdout
      and MARK not in md and "未正常结束" not in md and md.count("AI-OK-K2") == 3, "")

# ⑤ 其余「不干净收尾」：每条一个取值，一次运行全测掉——既验证中文文案映射，
#    也验证标记是**按条**落在各自的块里（不是整期一把）。
_UNCLEAN = [
    ("k1", "content_filter", "被内容过滤"),
    ("k2", "insufficient_system_resource", "服务端资源不足被中断"),
    ("k3", "aborted", "生成被中断"),
    ("k4", "tool_calls", "意外返回了工具调用"),
    ("k5", "shadow_banned", "shadow_banned"),   # 不认识的取值：原样带上该值
    ("k6", "length", None),                     # length 沿用既有「截断」文案
]
_MARKS = {}   # 条目号 → 该条应有的任务书标记行
for _k, _fr, _why in _UNCLEAN:
    if _fr == "length":
        _MARKS[_k] = MARK.replace("k1", _k)     # 既有文案，只换条目号
    else:
        _MARKS[_k] = (f"> ⚠️ 本条 AI 返回未正常结束——{_why}"
                      f"（finish_reason={_fr}），内容可能不完整。")

proc, md = run_convert(6, [
    {"status": 200, "content": f"@article{{{k},\n  title = {{AI-UNCLEAN-{k}}},\n",
     "finish": fr, "usage": USAGE}
    for k, fr, _why in _UNCLEAN
])
check("⑤ convert.py 正常结束（不干净收尾不改退出码、不影响出稿）", proc.returncode == 0,
      (proc.stderr or proc.stdout)[-200:])
for _k, _fr, _why in _UNCLEAN[:-1]:
    check(f"⑤ finish_reason={_fr} → stdout 一句按条告警（中文原因 + 引用原值）",
          f"⚠ {_k}：AI 返回未正常结束——{_why}（finish_reason={_fr}），"
          "本条可能**不完整**，请人工核实" in proc.stdout,
          repr([l for l in proc.stdout.splitlines() if _k in l and "⚠ " in l]))
    check(f"⑤ finish_reason={_fr} → 任务书里该条块内一行标记",
          _MARKS[_k] in md, repr([l for l in md.splitlines() if _fr in l]))
check("⑤ length 仍走既有「截断」文案（k6；编辑已认识那句，且带可操作建议）",
      f"⚠ k6：返回被 max_tokens 截断（finish_reason=length），本条**不完整**，"
      "建议调大 SLCN_LLM_MAX_TOKENS 或人工核实" in proc.stdout
      and _MARKS["k6"] in md, "")
check("⑤ 「截断」这个词只在 length 那一条出现（别的成因不许被误说成截断）",
      proc.stdout.count("截断") == 1 and md.count("截断") == 1,
      f"stdout={proc.stdout.count('截断')} md={md.count('截断')}")
_tail5 = md[md.index("AI 建议（**须人工核实**，工具不保证正确）"):]
_order_ok, _detail = True, []
for _i, (_k, _fr, _why) in enumerate(_UNCLEAN):
    _h = _tail5.find(f"## `{_k}`")
    _m = _tail5.find(_MARKS[_k])
    _next = (_tail5.find(f"## `{_UNCLEAN[_i + 1][0]}`")
             if _i + 1 < len(_UNCLEAN) else len(_tail5))
    _order_ok = _order_ok and -1 < _h < _m < _next
    _detail.append((_k, _h, _m, _next))
check("⑤ 六条标记各自落在**本条**的块里（条目头 → 标记 → 正文 → 下一条）",
      _order_ok, repr(_detail))
check("⑤ 标记与建议正文之间的空行照旧（引用行不会被并进代码围栏）",
      _MARKS["k1"] + "\n\n" + "@article{k1,\n  title = {AI-UNCLEAN-k1}," in md, "")
check("⑤ 六条建议一条都没丢（只标注、不丢弃）",
      all(f"AI-UNCLEAN-{_k}" in md for _k, _fr, _why in _UNCLEAN), "")
check("⑤ 逐条 tokens 与收尾汇总未被改坏",
      proc.stdout.count("tokens 11+7") == 6
      and "共 6 次调用，66 prompt + 42 completion tokens" in proc.stdout, "")

srv.shutdown()
print()
print("SUMMARY: %d FAIL" % len(FAILS) + ("  -> " + ", ".join(FAILS) if FAILS else "  (all PASS)"))
sys.exit(1 if FAILS else 0)
