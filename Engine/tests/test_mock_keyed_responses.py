#!/usr/bin/env python3
r"""mock 端点「按请求内容匹配夹具」的**独立**验证（task-3）。

被验证的对象是**仓库里那份** mock：`tests/test_llm_finish_reason.py` 里 `class H` 的
`do_POST`（⑪「AI 预跑」把它当 LLM 端点用）。CI 的 windows-latest 上三个 `test_llm_*`
套件红过，症状是任务书里**整段 AI 建议消失**；怀疑的根因是 mock 按**请求序号**取夹具
（`SCRIPT[i]`）——平台上请求的时机/条数一变索引就错位。

本套件**不另写一份 mock**：用 `ast` 把源码里的 `class H` 连同它引用到的模块级名字
（`BODIES` / `SCRIPT` / `MISSING` / import / 辅助函数，按引用关系传递闭包地捞）一起抽出来、
逐字执行，再用**真实 HTTP 请求**驱动它。另写一份实现只能证明「我写的逻辑是对的」，
证明不了「仓库里那份是对的」——这正是这次要独立验证的东西。

钉住的语义（提交信息里声明的口径）：
  · 带 `when` 的脚本项：请求体里出现该子串就用它，**与请求顺序无关**；
  · 命中 `when` **不消费** —— 重复请求同一键给同一答案；
  · 不带 `when` 的项仍按请求序号兜底；脚本用完后重复最后一步（老行为）；
  · 脚本为空 → HTTP 500（不是静默 200）。

不变量（不可放宽）：**一个请求只能拿到属于它自己键的夹具**。错配的夹具会让 ⑪ 把 A 条的
回复贴到 B 条上，正是 CI 上「整段 AI 建议消失 / 张冠李戴」的成因。所以：未匹配的请求
**不得**落到「别的键」的项上——脚本里还有不带 `when` 的项时，必须落到那些项上。
（脚本里只剩带键项时是另一回事，见 (G)：原套件 ④ 依赖它仍回 200。）

附加健壮性（`when` 是子串匹配的固有坑，键不重叠时无影响）：`when="k1"` 不许把请求体里
含 `k10` 的请求抢走。`run_convert()` 目前最多绑到 k6，所以今天不炸，但条目数 >9 就会。

不依赖 TeX / 稿件库、不联网（只打 127.0.0.1）、秒级。

用法：cd Engine && PYTHONPATH="$PWD/vendor" /opt/homebrew/bin/python3.14 tests/test_mock_keyed_responses.py
"""
from __future__ import annotations

import ast
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
# 本地依赖自给自足：不靠 PYTHONPATH（与 tests/test_llm_finish_reason.py 同一思路）
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import llm  # noqa: E402

_SRC = os.path.join(_HERE, "test_llm_finish_reason.py")

pass_n = 0
fails: list[str] = []
TRACE: list[tuple[str, object, object, object]] = []   # (标签, 期望, 实际状态, 实际内容)


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


# ── 抽取器：把源码里那份 mock 逐字拿来执行（不另写实现）──────────────────────
def _extract_mock():
    """返回 (命名空间, 错误说明, 类源码, 文件全文)。命名空间里有源码那份 `class H`。"""
    with open(_SRC, encoding="utf-8") as fh:
        text = fh.read()
    tree = ast.parse(text, filename=_SRC)
    cls = next((n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == "H"), None)
    if cls is None:
        return None, "源码顶层没有 `class H`", "", text

    # 顶层「名字 → 节点」：do_POST 用到的模块级名字（BODIES / SCRIPT / MISSING /
    # 将来可能加的辅助函数）按引用关系传递闭包地捞出来 —— 辅助函数挪到哪儿都不影响抽取。
    top: dict = {}
    imports: list = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if not (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
                imports.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top[node.name] = node
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    top[tgt.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top[node.target.id] = node

    def _loads(node: ast.AST) -> set:
        return {n.id for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}

    need, seen = _loads(cls) - {"H"}, set()
    picked: list = []
    while need:
        name = need.pop()
        if name in seen:
            continue
        seen.add(name)
        node = top.get(name)
        if node is None:               # 内建 / 由 import 提供（json、HTTPServer…）
            continue
        picked.append(node)
        need |= _loads(node) - seen

    ns: dict = {"__name__": "mock_extracted_from_source", "__file__": _SRC}
    for node in [*imports, *sorted(picked, key=lambda n: n.lineno), cls]:
        exec(compile(ast.Module(body=[node], type_ignores=[]), _SRC, "exec"), ns)
    missing = [n for n in ("H", "SCRIPT", "BODIES") if n not in ns]
    if missing:
        return None, f"抽出来的命名空间缺 {missing}", "", text
    return ns, "", ast.get_source_segment(text, cls) or "", text


NS, _ERR, _CLS_SRC, _SRC_TEXT = _extract_mock()

print("── (0) 抽取器：验证对象必须是仓库里那份 mock，不是另写的复刻 ──")
check("(0) 能从 tests/test_llm_finish_reason.py 里抽出 `class H`（do_POST 的宿主）",
      NS is not None, _ERR)
check("(0) 抽出的 `class H` 与源码逐字一致，且含 `def do_POST`（证明不是复刻）",
      NS is not None and "def do_POST" in _CLS_SRC and _CLS_SRC.strip() in _SRC_TEXT, _ERR)
if NS is None:
    print("\n" + "=" * 66)
    print(f"失败 {len(fails)} 项：{fails}")
    print("抽取器失效 = 本套件什么都没验证（不许静默当通过），请先修抽取器。")
    sys.exit(1)

srv = HTTPServer(("127.0.0.1", 0), NS["H"])
MOCK = f"http://127.0.0.1:{srv.server_address[1]}"
threading.Thread(target=srv.serve_forever, daemon=True).start()


def _request(user_text: str):
    """发一条 chat 形状的 POST（请求体照 convert.py 的形状），返回 (状态, 内容, 响应体)。"""
    body = {"model": "mock-model",
            "messages": [{"role": "system", "content": "SYS"},
                         {"role": "user", "content": user_text}]}
    req = urllib.request.Request(
        MOCK + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:      # 非 200 走这里，也要能看到响应体
        status, raw = exc.code, exc.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return status, None, None
    try:
        return status, payload["choices"][0]["message"]["content"], payload
    except Exception:
        return status, None, payload


def set_script(items) -> None:
    """换脚本（原地清空，与真套件的 `SCRIPT.clear()` 同一动作）。"""
    del NS["SCRIPT"][:]
    NS["SCRIPT"].extend(items)
    del NS["BODIES"][:]


def ask(key=None, expect=None, label="", marker="NOTHING-MATCHES"):
    """发一条请求：key 给定时请求体含 `@article{key,`；None = 无法匹配任何键的请求。"""
    user = (f"@article{{{key},\n  title = {{T}},\n}}" if key
            else f"请核实这条参考文献（无条目键：{marker}）")
    status, content, _payload = _request(user)
    TRACE.append((label or (key or "（无键）"), expect, status, content))
    return status, content


def check_answer(name: str, key, expect: str, label: str = "") -> None:
    status, content = ask(key, expect, label)
    check(name, status == 200 and content == expect,
          f"期望 {expect!r}，实际 HTTP {status} / {content!r}")


# ── (A) 脚本为空 → 500（不是静默 200）──────────────────────────────────────
print("\n── (A) 脚本为空：必须回 500，不许静默 200 ──")
set_script([])
_st, _ct, _pl = _request("@article{k1,\n  title = {T},\n}")
check("(A) 脚本为空 → HTTP 500（静默 200 会让测试自己写错时查不出来）",
      _st == 500, f"实际 HTTP {_st} / {_ct!r}")
check("(A) 脚本为空 → 响应体写明原因（error.message == 'no script'）",
      isinstance(_pl, dict) and _pl.get("error", {}).get("message") == "no script",
      repr(_pl))

# ── (B) 不带 when：仍按序号（这次改动不许把老行为改坏）──────────────────────
print("\n── (B) 不带 when 的脚本：仍按请求序号、用完后重复最后一步 ──")
set_script([{"status": 200, "content": "SEQ-1"},
            {"status": 200, "content": "SEQ-2"}])
_want = ["SEQ-1", "SEQ-2", "SEQ-2"]
_got = [ask(None, _want[i], f"序号 #{i + 1}")[1] for i in range(3)]
check("(B) 第 1/2 次请求 → SEQ-1 / SEQ-2（纯序号兜底照旧）",
      _got[:2] == ["SEQ-1", "SEQ-2"], repr(_got))
check("(B) 脚本用完后重复最后一步（第 3 次 → SEQ-2，不越界）",
      _got[2] == "SEQ-2", repr(_got))

# ── (C) 核心：按请求内容匹配 ⇒ 与请求顺序无关 ─────────────────────────────
print("\n── (C) 核心：带 when 的项按请求体子串匹配，与请求顺序无关 ──")
set_script([{"when": "k1", "status": 200, "content": "FIX-K1"},
            {"when": "k2", "status": 200, "content": "FIX-K2"}])
check_answer("(C) 乱序第 1 条：请求体含 k2 → 拿 FIX-K2（纯按序号会给 FIX-K1）",
             "k2", "FIX-K2", "先 k2")
check_answer("(C) 乱序第 2 条：请求体含 k1 → 拿 FIX-K1",
             "k1", "FIX-K1", "后 k1")

set_script([{"when": "k1", "status": 200, "content": "FIX-K1"},
            {"when": "k2", "status": 200, "content": "FIX-K2"}])
_got = [ask("k1", "FIX-K1", f"k1 第 {i} 次")[1] for i in (1, 2, 3)]
check("(C) 重复请求同一键 3 次 → 每次都拿到同一份夹具（when 命中不消费）",
      _got == ["FIX-K1"] * 3, repr(_got))

set_script([{"when": "k1", "status": 200, "content": "FIX-K1"},
            {"when": "k2", "status": 200, "content": "FIX-K2"}])
_inter = [("k2", "FIX-K2"), ("k1", "FIX-K1"), ("k2", "FIX-K2"), ("k1", "FIX-K1")]
_got = [ask(_k, _e, f"交错 {_k}")[1] for _k, _e in _inter]
check("(C) 交错 k2,k1,k2,k1 → 四条全部对号（顺序怎么变都不错配）",
      _got == [_e for _k, _e in _inter], repr(_got))

# ── (D) 无法匹配 → 序号兜底，但**不许落到别的键上** ────────────────────────
# ⚠️ `set_script()` 与真套件的 `run_convert()` 一样，换场景只 clear `SCRIPT`/`BODIES`
#    （不重建服务器、不重载模块）。所以兜底游标**必须由这两者派生**（或写在脚本项上）：
#    一个活过 `SCRIPT.clear()` 的私有计数器，会把上个场景的兜底进度带进下个场景——
#    那是另一种「行为取决于请求历史、而不是请求内容」，与要修的 bug 同类。
print("\n── (D) 无法匹配的请求：走序号兜底，不许拿到别人键的夹具 ──")
set_script([{"when": "k1", "status": 200, "content": "FIX-K1"},
            {"status": 200, "content": "BARE-1"},
            {"status": 200, "content": "BARE-2"}])
_seq = [("未匹配 #1", None, "BARE-1"), ("k1 命中", "k1", "FIX-K1"),
        ("未匹配 #2", None, "BARE-2"), ("未匹配 #3", None, "BARE-2")]
_got = [ask(_k, _exp, _lab)[1] for _lab, _k, _exp in _seq]
check("(D) 未匹配请求落到**不带 when** 的项上：第 1 条 → BARE-1（不是 FIX-K1）"
      "（换脚本后兜底必须从第一条重新开始：游标不许活过 SCRIPT.clear()）",
      _got[0] == "BARE-1", f"实际 {_got[0]!r}（拿到别人键的夹具 = 错配）")
check("(D) 键命中不推进兜底游标：k1 之后未匹配 → BARE-2（不是 BARE-1、更不是 FIX-K1）",
      _got[1] == "FIX-K1" and _got[2] == "BARE-2", repr(_got))
check("(D) 兜底项用完后重复最后一条（第 3 条未匹配 → BARE-2）",
      _got[3] == "BARE-2", repr(_got))

# ── (E) 附加健壮性：键前缀重叠（子串匹配的固有坑）──────────────────────────
print("\n── (E) 附加健壮性：when='k1' 不许抢走请求体含 'k10' 的请求（键不重叠时无影响）──")
set_script([{"when": "k1", "status": 200, "content": "FIX-K1"},
            {"when": "k10", "status": 200, "content": "FIX-K10"}])
check_answer("(E) 请求体含 k10 → 拿 FIX-K10（子串匹配要取最长/精确绑定）",
             "k10", "FIX-K10", "k10")
check_answer("(E) 请求体含 k1 → 仍拿 FIX-K1", "k1", "FIX-K1", "k1")

# ── (F) 真实调用方（slcn.llm.chat）也走同一条路 ────────────────────────────
print("\n── (F) 真实调用方：slcn.llm.chat（convert.py 用的就是它）乱序也对号 ──")
set_script([{"when": "k1", "status": 200, "content": "FIX-K1"},
            {"when": "k2", "status": 200, "content": "FIX-K2"}])
_cfg = llm.LLMConfig(base=MOCK + "/v1", key="mock", model="mock-model")
_out2 = llm.chat(_cfg, "SYS", "@article{k2,\n  title = {T},\n}", timeout=10)
_out1 = llm.chat(_cfg, "SYS", "@article{k1,\n  title = {T},\n}", timeout=10)
check("(F) llm.chat 先问 k2 再问 k1 → 两次内容对号（None = 调用失败）",
      _out2 == "FIX-K2" and _out1 == "FIX-K1", f"k2→{_out2!r} / k1→{_out1!r}")

# ── (G) 兼容性：脚本里只剩带键项时的兜底（原套件 ④ 依赖它必须回 200）────────
print("\n── (G) 兼容性：只剩带键项时的兜底必须仍回 200（原套件 ④ 的场景）──")
set_script([{"when": "k1", "status": 200, "content": "ONLY"}])
check_answer("(G) 脚本只有 k1 一项、请求 k2 无法匹配 → 仍回 200（重复最后一步），不许 500",
             "k2", "ONLY", "k2（无兜底项）")
check_answer("(G) 同一脚本里请求 k1 → 键命中照常拿 ONLY", "k1", "ONLY", "k1")

srv.shutdown()

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print("请求轨迹（标签 / 期望 / 实际 HTTP / 实际内容）——每一条 FAIL 的复现都在里面：")
    for _lab, _exp, _st, _ct in TRACE:
        print(f"  {str(_lab):<12} 期望 {str(_exp):<10} 实际 HTTP {_st} / {_ct!r}")
    print()
    print(f"失败 {len(fails)} 项：{fails}")
    print("说明：本套件从 tests/test_llm_finish_reason.py **抽出真实 do_POST** 执行，"
          "以上 FAIL = mock 仍按请求序号取夹具（`SCRIPT[i]`）的复现。")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
