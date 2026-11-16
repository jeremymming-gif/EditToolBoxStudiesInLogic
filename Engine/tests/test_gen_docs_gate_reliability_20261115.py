#!/usr/bin/env python3.14
r"""G1（2026-11-15）：gen_docs 门禁可信化 —— 套件子进程状态必须进入结论。

为什么有这个测试（根因，可在基点上复现）：
`run_tests()` 从前**只数 `PASS` / `FAIL` 文本行**、把 `subprocess.run` 的 `returncode`
整个丢掉。于是 5 类"套件其实没跑成功"的形态全被当成 PASS：

  · rc=3 但一行都没打        → PASS=0 FAIL=0（"零断言"被当成通过）
  · 打了 5 行 PASS 后 exit 7 → PASS=5 FAIL=0
  · import 期就崩 rc=1       → PASS=0 FAIL=0（**最危险**：崩溃看起来比失败还"干净"）
  · 只在 stderr 抛错 rc=1    → 只数到它已经打出的 PASS 行
  · 合法 rc=0 但零断言       → PASS=0 FAIL=0

只有"文本 FAIL 但 rc=0"这一类会被抓住。后果：一个套件崩了/超时了，AUTO 块照样写 ✅。

本测试把这些反例一次性钉住，并钉住"修好之后不许把原本正确的渲染形态改坏"：
  A. run_tests 的 status 语义（5 类反例的 rc / status 逐条断言，**几秒内跑完**）
  B. build_block 的渲染契约：全绿时**逐字节**等于基点渲染（把基点源码取出来现算对比），
     异常时摘要行带异常套件数与状态、明细多一行「异常套件：」，且 all_ok=False
  C. 缓存契约：schema 不匹配拒用、带 FAIL 拒用、带 status 拒用、3 元素旧行仍可复用
  D. fast path：缓存行有非 None status 时不得宣布 PASS
  E. `--check` 语义分离：真跑了套件且有异常 → 必须非 PASS 且 rc≠0；`--no-test-runs` 不表态
  F. `--strict-tests`：缺前提 → BLOCKED + rc=2；不允许 lenient；不得走"带异常状态"的 fast path

**全部 hermetic**：假套件写在 tempfile 里、`G.test_files` 指过去；AUTO 输入清单指临时文件；
不跑真实套件、不写真实 README。(E)/(F) 也在**进程内**把 `environment_complete` /
`run_tests` / `README` 换成 fixture —— 绝不能在这里再起一次真实的 `--check`
（那等于把这 173 个套件又跑一遍，而本套件自己就在里面：门禁会自我拖垮、超时）。
"""
from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
sys.path.insert(0, str(ENGINE / "tools"))

FAILS: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="slcn_g1_"))
# 必须在 import gen_docs **之前**设好：AUTO_INPUTS 是模块导入期算出来的常量。
os.environ["SLCN_AUTO_INPUTS"] = str(TMP / "out" / "auto-inputs.json")
os.environ["SLCN_RUN_RECORD"] = "0"

import gen_docs as G  # noqa: E402

# ⚠️ 真的 `gen_docs.py --check` 跑本套件时，环境里带着 SLCN_GEN_DOCS_NESTED=1（它给每个子进程
#    都置了它，防止套件再回头调 gen_docs → 无限递归）。本套件的 A 段要**直接**调
#    `G.run_tests(files=假套件)` —— 那是"跑几个临时脚本"，不是递归调用 gen_docs。
#    不摘掉这个变量，A 段第一句就会撞上递归护栏 sys.exit → 整个门禁里本套件"startup failed rc=1"。
os.environ.pop("SLCN_GEN_DOCS_NESTED", None)


def check(name: str, cond: bool, detail="") -> None:
    # ⚠️ detail 必须压成**一行**：被捕获的 gen_docs 输出里本来就有 `PASS` / `FAIL` 开头的行，
    #    原样拼进本套件的输出会被 `run_tests` 的 `^\\s*FAIL\\b` 当成"本套件的断言行"，
    #    于是 AUTO 块里凭空多出 FAIL 计数（test_b74 踩过的同一个坑）。
    tail = ""
    if detail:
        tail = "  " + " ⏎ ".join(str(detail).splitlines())[:400]
    print(("PASS  " if cond else "FAIL  ") + name + tail)
    if not cond:
        FAILS.append(name)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@contextlib.contextmanager
def quiet():
    """吞掉 gen_docs 自己的 PASS/FAIL 输出。

    本套件的断言计数靠 `^\\s*PASS` 行数，被调函数打印的同形行会被外层 gen_docs 误当断言行
    （这正是 test_b74 踩过的坑）。凡是**进程内**调用 gen_docs 的地方一律套它。
    """
    buf_o, buf_e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_o), contextlib.redirect_stderr(buf_e):
        yield


# ── 假套件（写在临时目录，绝不往 Engine/tests 里塞）──────────────────────────
SUITE_DIR = TMP / "fake_suites"


def suite(name: str, body: str) -> Path:
    p = SUITE_DIR / f"{name}.py"
    write(p, body)
    return p


# ① 一行不打，直接 exit 3
S_CRASH_SILENT = suite("fake_crash_silent", "import sys\nsys.exit(3)\n")
# ② 打 5 行 PASS 后 exit 7
S_PASS_THEN_EXIT = suite("fake_pass_then_exit", "\n".join(
    [f'print("PASS  项 {i}")' for i in range(1, 6)] + ["import sys", "sys.exit(7)", ""]))
# ③ import 期就崩（rc=1，零输出）—— 最危险的一类
S_IMPORT_ERROR = suite("fake_import_error", "import definitely_not_a_real_module_xyz\n")
# ④ 只在 stderr 抛错（打了 2 行 PASS，rc=1）
S_STDERR_ONLY = suite("fake_stderr_only", "\n".join([
    'import sys',
    'print("PASS  a")',
    'print("PASS  b")',
    'print("Traceback (most recent call last): boom", file=sys.stderr)',
    "sys.exit(1)", ""]))
# ⑤ 合法 rc=0 但零断言
S_EMPTY_OK = suite("fake_empty_ok", "print('nothing to assert')\n")
# ⑥ 合法全绿（对照组）
S_ALL_GREEN = suite("fake_all_green", "\n".join(
    [f'print("PASS  项 {i}")' for i in range(1, 5)] + []))
# ⑦ 文本 FAIL 但 rc=0（这一类旧实现本来就能抓，别改坏）
S_TEXT_FAIL = suite("fake_text_fail", "\n".join([
    'print("PASS  ok")', 'print("FAIL  反例")', "raise SystemExit(0)", ""]))
# ⑧ 超时：先打一行 PASS 再睡死（配 0.5s 的 timeout，几秒内结束）
S_HANG = suite("fake_hang", "import time\nprint('PASS  before sleep')\ntime.sleep(30)\n")

REAL_TEST_FILES = G.test_files


def fake_files(*paths: Path):
    """让 run_tests 只跑给定假套件（不动真实 Engine/tests）。"""
    return list(paths)


# ═══════════════════════════════════════════════════════════════════════════
print("== A. run_tests：子进程 returncode / timeout 必须成为显式失败状态 ==")
# ═══════════════════════════════════════════════════════════════════════════
for label, path, exp_pass, exp_fail, exp_status_part in [
    ("① rc=3 零输出", S_CRASH_SILENT, 0, 0, "rc=3"),
    ("② 5 PASS 后 exit 7", S_PASS_THEN_EXIT, 5, 0, "rc=7"),
    ("③ import 期崩溃 rc=1", S_IMPORT_ERROR, 0, 0, "startup failed rc=1"),
    ("④ 仅 stderr 抛错 rc=1", S_STDERR_ONLY, 2, 0, "rc=1"),
    ("⑤ 合法 rc=0 零断言", S_EMPTY_OK, 0, 0, None),
    ("⑥ 合法全绿", S_ALL_GREEN, 4, 0, None),
    ("⑦ 文本 FAIL 但 rc=0", S_TEXT_FAIL, 1, 1, "FAIL 1"),
]:
    rows, total = G.run_tests(timeout=30, files=fake_files(path))
    check(f"A {label}：跑出 1 行", len(rows) == 1, rows)
    if len(rows) != 1:
        continue
    name, passes, fails, status = rows[0]
    check(f"A {label}：PASS 行数 = {exp_pass}", passes == exp_pass, f"实得 {passes}")
    check(f"A {label}：FAIL 行数 = {exp_fail}", fails == exp_fail, f"实得 {fails}")
    if exp_status_part is None:
        check(f"A {label}：status 必须是 None（真全绿）", status is None, f"实得 {status!r}")
    else:
        check(f"A {label}：status 含 {exp_status_part!r}",
              bool(status) and exp_status_part in status, f"实得 {status!r}")
    check(f"A {label}：返回行是 4 元组", len(rows[0]) == 4, rows[0])

# 成功判据：(rc==0 and fails==0) —— 只有 ⑥ 与 ⑦(有 FAIL) 之外的组合
rows_ok, _ = G.run_tests(timeout=30, files=fake_files(S_ALL_GREEN))
check("A 成功判据：rc0 且 0 FAIL → 行 status is None",
      rows_ok[0][3] is None and rows_ok[0][2] == 0, rows_ok)
check("A abnormal_suites 只挑非 None",
      [n for n, _ in G.abnormal_suites(rows_ok)] == [], G.abnormal_suites(rows_ok))
check("A suite_rows_trusted：全绿行可信", G.suite_rows_trusted(rows_ok) is True)
check("A suite_rows_trusted：3 元素旧行不可信（形状不完整）",
      G.suite_rows_trusted([("n", 1, 0)]) is False)

# 超时反例：必须几秒内返回，且 status 明确是 timeout（不是"没看见 FAIL 就当过"）
import time as _time  # noqa: E402
_t0 = _time.time()
rows_to, _ = G.run_tests(timeout=0.5, files=fake_files(S_HANG))
_elapsed = _time.time() - _t0
check("A ⑧ 超时：子进程被真的掐断（<15s）", _elapsed < 15, f"{_elapsed:.2f}s")
check("A ⑧ 超时：status 明确是 timeout",
      bool(rows_to[0][3]) and "timeout" in rows_to[0][3], rows_to[0][3])
check("A ⑧ 超时：睡死前打出的 PASS 仍被计入（便于定位卡在哪）",
      rows_to[0][1] == 1, rows_to[0])
check("A ⑧ 超时：绝不因为 0 FAIL 就当全绿",
      G.abnormal_suites(rows_to) != [], G.abnormal_suites(rows_to))
check("A 默认超时常量为正数", G.TEST_TIMEOUT_SECONDS > 0, G.TEST_TIMEOUT_SECONDS)


# ═══════════════════════════════════════════════════════════════════════════
print()
print("== B. build_block：全绿渲染逐字节不变 + 异常必须显式化 ==")
# ═══════════════════════════════════════════════════════════════════════════
def pin_fixture():
    """把 AUTO 块里除「测试」以外的段落钉成确定性文本（不依赖本机 out/ 产物）。"""
    G.notes_audit_paths = lambda: None
    G.AUDIT_CSV = TMP / "no_such_audit.csv"
    G.AUDIT_TXT = TMP / "no_such_audit.txt"
    G.CORPUS_CSV = TMP / "no_such_corpus.csv"
    G.CORPUS_DIR = TMP / "no_such_corpus_dir"


pin_fixture()

green3 = [(f.stem, 1, 0) for f in G.test_files()[:3]]
green4 = [(n, p, f, None) for n, p, f in green3]
body_ok, ok_ok = G.build_block(run_test_suites=False, test_rows_in=green4)
test_line_ok = next(ln for ln in body_ok.split("\n") if ln.startswith("**测试**"))
check("B 全绿：all_ok 为 True", ok_ok is True)
check("B 全绿：摘要行以 ✅ 结尾（异常标记不出现）",
      test_line_ok.endswith("属自指）") and " ✅（不含" in test_line_ok, test_line_ok)
check("B 全绿：摘要行含「N 套件 / M 项断言 / FAIL 0 行」",
      f"**{len(green4)} 套件 / {sum(p for _, p, _, _ in green4)} 项断言 / FAIL 0 行**" in test_line_ok)
check("B 全绿：明细行格式与历史一致",
      "套件明细：" in body_ok and f"`{green4[0][0]}` 1" in body_ok)
check("B 全绿：不出现「异常套件：」行", "异常套件：" not in body_ok)

# —— 逐字节对比基点渲染 ——
# 契约 2 要求「全绿时 AUTO 测试行逐字不变」。这里把基点（f0551230）的 gen_docs.py 取出来
# 现算一遍，与本实现逐字比 —— 不靠肉眼、不靠记下来的字符串。
BASE_PY = TMP / "gen_docs_base.py"
_base_src = subprocess.run(
    ["git", "show", "f0551230106ec0b49944b85d0ab3366323570627:Engine/tools/gen_docs.py"],
    cwd=str(ENGINE), capture_output=True, text=True, encoding="utf-8")
if _base_src.returncode != 0 or not _base_src.stdout:
    print(f"SKIP  B 逐字节对比：取不到基点源码（{_base_src.returncode}：{_base_src.stderr.strip()[:120]}）")
else:
    write(BASE_PY, _base_src.stdout)
    import importlib.util as _ilu  # noqa: E402
    _spec = _ilu.spec_from_file_location("gen_docs_baseline_g1", BASE_PY)
    _BASE = _ilu.module_from_spec(_spec)
    sys.modules["gen_docs_baseline_g1"] = _BASE
    _spec.loader.exec_module(_BASE)
    # 用同一组 fixture 把基点也钉住（否则基线会去读真实的 out/ 产物）
    for _m in (_BASE,):
        _m.notes_audit_paths = lambda: None
        _m.AUDIT_CSV = G.AUDIT_CSV
        _m.AUDIT_TXT = G.AUDIT_TXT
        _m.CORPUS_CSV = G.CORPUS_CSV
        _m.CORPUS_DIR = G.CORPUS_DIR
    _base_body, _base_ok = _BASE.build_block(run_test_suites=False, test_rows_in=green3)
    _new_body, _new_ok = G.build_block(run_test_suites=False, test_rows_in=green4)
    check("B 逐字节：全绿时新渲染 == 基点渲染（整块）",
          _base_body == _new_body,
          "" if _base_body == _new_body else "（见下 diff）")
    if _base_body != _new_body:
        import difflib
        for ln in difflib.unified_diff(_base_body.split("\n"), _new_body.split("\n"),
                                       "基点", "现在", lineterm="", n=2):
            print("      " + ln)
    check("B 逐字节：all_ok 也一致", _base_ok == _new_ok)

# —— 异常渲染 ——
bad4 = [("fake_ok", 3, 0, None), ("fake_crash", 0, 0, "startup failed rc=1"),
        ("fake_timeout", 1, 0, "timeout 0.5s"), ("fake_red", 2, 1, "rc=0 FAIL 1")]
body_bad, ok_bad = G.build_block(run_test_suites=False, test_rows_in=bad4)
line_bad = next(ln for ln in body_bad.split("\n") if ln.startswith("**测试**"))
check("B 异常：all_ok 必须 False（旧实现只看 FAIL 计数会漏掉崩溃/超时）", ok_bad is False)
check("B 异常：摘要行带 ❌", " ❌ " in line_bad, line_bad)
check("B 异常：摘要行标出**异常套件数 / 总数**", "异常套件 3/4" in line_bad, line_bad)
check("B 异常：摘要行里崩溃串可见", "fake_crash(startup failed rc=1)" in line_bad, line_bad)
check("B 异常：摘要行里超时串可见", "fake_timeout(timeout 0.5s)" in line_bad, line_bad)
# 「纯 FAIL（rc=0 但有 FAIL 断言）」是另一个分支：状态串里已经带 FAIL 数，这里单独验一次。
_pure_fail = [("fake_green", 2, 0, None), ("fake_red", 2, 1, "rc=0 FAIL 1")]
_body_pf, _ok_pf = G.build_block(run_test_suites=False, test_rows_in=_pure_fail)
_line_pf = next(ln for ln in _body_pf.split("\n") if ln.startswith("**测试**"))
check("B 异常：纯 FAIL 行仍按 `名字(status)` 表达（历史 `名字(N FAIL)` 信息不丢）",
      "fake_red(rc=0 FAIL 1)" in _line_pf and _ok_pf is False, _line_pf)
check("B 异常：明细多一行「异常套件：」", "异常套件：" in body_bad)
_abn_line = next((ln for ln in body_bad.split("\n") if ln.startswith("异常套件：")), "")
check("B 异常：明细行逐条列状态",
      all(s in _abn_line for s in ("fake_crash", "startup failed rc=1", "fake_timeout", "timeout 0.5s")),
      _abn_line)
check("B 异常：全绿的套件不进异常明细", "fake_ok" not in _abn_line, _abn_line)
# 「FAIL 断言行数」与「套件是否成功」是两件事：零 FAIL 但崩溃时，摘要仍要写 FAIL 0 行。
_no_fail_bad = [("fake_ok", 3, 0, None), ("fake_crash", 0, 0, "startup failed rc=1")]
_body_nf, _ok_nf = G.build_block(run_test_suites=False, test_rows_in=_no_fail_bad)
_line_nf = next(ln for ln in _body_nf.split("\n") if ln.startswith("**测试**"))
check("B 异常：FAIL 0 行但套件崩溃时摘要仍报 FAIL 0 行（两种事实分开表达）",
      "FAIL 0 行**" in _line_nf and " ✅" not in _line_nf and _ok_nf is False, _line_nf)
# 3 元素旧行渲染：status 视为 None → 与历史一致
body_old3, ok_old3 = G.build_block(run_test_suites=False, test_rows_in=green3)
check("B 兼容：3 元素旧行渲染 == 4 元素全绿行渲染", body_old3 == body_ok and ok_old3 is True)


# ═══════════════════════════════════════════════════════════════════════════
print()
print("== C. 缓存契约：schema 升 2 + 拒收带状态的快照 ==")
# ═══════════════════════════════════════════════════════════════════════════
check("C schema 已升到 2", G.AUTO_INPUTS_SCHEMA == 2, G.AUTO_INPUTS_SCHEMA)
_inputs = {"engine_py_sha256": "e" * 64, "tests_manifest_sha256": "t" * 64,
           "env_complete": True, "manuscripts_fingerprint": "m" * 16}


def proj(**kw):
    base = {"schema": G.AUTO_INPUTS_SCHEMA, "inputs": dict(_inputs),
            "test_rows": [["test_x", 1, 0, None]], "block_sha256": "0" * 64}
    base.update(kw)
    return base


check("C 命中：全绿 4 元素行可复用", G.cached_test_rows(_inputs, proj()) == [["test_x", 1, 0, None]])
check("C 拒用：schema=1 的旧缓存",
      G.cached_test_rows(_inputs, proj(schema=1)) is None)
check("C 拒用：schema=3 的未来缓存",
      G.cached_test_rows(_inputs, proj(schema=3)) is None)
check("C 拒用：缓存行带非 None status（0 FAIL 但崩溃/超时）",
      G.cached_test_rows(_inputs, proj(test_rows=[["test_x", 1, 0, "startup failed rc=1"]])) is None)
check("C 拒用：缓存行带 FAIL 计数（B-110 语义保留）",
      G.cached_test_rows(_inputs, proj(test_rows=[["test_x", 1, 1, None]])) is None)
check("C 兼容：(e3) 手写的 3 元素行仍可复用（test_docs_consistency 的硬断言）",
      G.cached_test_rows(_inputs, {"schema": G.AUTO_INPUTS_SCHEMA, "inputs": dict(_inputs),
                                   "test_rows": [["test_x", 1, 0]], "block_sha256": "0" * 64})
      == [["test_x", 1, 0]])
check("C 拒用：inputs 指纹变了（engine）",
      G.cached_test_rows(dict(_inputs, engine_py_sha256="deadbeef"), proj()) is None)
check("C 拒用：inputs 指纹变了（tests）",
      G.cached_test_rows(dict(_inputs, tests_manifest_sha256="cafe"), proj()) is None)
check("C 拒用：空 test_rows", G.cached_test_rows(_inputs, proj(test_rows=[])) is None)

# save_auto_inputs 必须写出 schema=2 与 4 列行
G.AUTO_INPUTS = TMP / "out" / "auto-inputs.json"
_rows_to_save = [("test_a", 3, 0, None), ("test_b", 4, 0, "rc=7")]
G.save_auto_inputs(_inputs, _rows_to_save, "\n**测试**：x\n")
_saved = json.loads(G.AUTO_INPUTS.read_text(encoding="utf-8"))
check("C 落盘：schema 写 2", _saved.get("schema") == 2, _saved.get("schema"))
check("C 落盘：test_rows 保留 status 列（4 列）",
      _saved.get("test_rows") == [["test_a", 3, 0, None], ["test_b", 4, 0, "rc=7"]],
      _saved.get("test_rows"))
check("C 落盘：读回后 cached_test_rows 拒用（因为有一行 status）",
      G.cached_test_rows(_inputs, _saved) is None)

# 正向：全绿 4 列行落盘 → 读回**可复用**（这就是写两次不重跑套件的路径）
G.save_auto_inputs(_inputs, [("test_a", 3, 0, None), ("test_b", 4, 0, None)], "\n**测试**：y\n")
_saved_ok = json.loads(G.AUTO_INPUTS.read_text(encoding="utf-8"))
check("C 落盘：全绿快照读回后可复用，且内容逐字相同",
      G.cached_test_rows(_inputs, _saved_ok) == _saved_ok.get("test_rows"),
      _saved_ok.get("test_rows"))
check("C 落盘：可复用快照的形状是 4 列",
      all(len(r) == 4 for r in (_saved_ok.get("test_rows") or [])), _saved_ok.get("test_rows"))


# ═══════════════════════════════════════════════════════════════════════════
print()
print("== D. fast path：带异常状态的缓存不得直接宣布 PASS ==")
# ═══════════════════════════════════════════════════════════════════════════
_fixture_body, _ = G.build_block(run_test_suites=False, test_rows_in=green4)
import hashlib as _hl  # noqa: E402
# ⚠️ AUTO 块的**正式形状**是 "\n" + build_block()[0]（块标记后紧跟一个换行，见 _main_impl）；
#    save_auto_inputs 存的是这个形状的 sha，所以这里也必须带上那个前导换行再算。
_blk_sha = _hl.sha256(
    G.normalize_for_check("\n" + _fixture_body, lenient=False).encode("utf-8")).hexdigest()
_ok_proj = {"schema": G.AUTO_INPUTS_SCHEMA, "inputs": dict(_inputs),
            "test_rows": [["test_x", 1, 0, None]], "block_sha256": _blk_sha}
check("D 基线：指纹都对上时 fast path 可用",
      G.fast_check_ok("\n" + _fixture_body, _ok_proj, dict(_inputs, env_complete=True)) is True)
check("D 拒用：缓存行带非 None status（块指纹对得上也不算 PASS）",
      G.fast_check_ok("\n" + _fixture_body,
                      dict(_ok_proj, test_rows=[["test_x", 1, 0, "timeout 900s"]]),
                      dict(_inputs, env_complete=True)) is False)
check("D 拒用：旧 schema", G.fast_check_ok("\n" + _fixture_body,
                                           dict(_ok_proj, schema=1),
                                           dict(_inputs, env_complete=True)) is False)
check("D 拒用：块指纹不符",
      G.fast_check_ok("\n" + _fixture_body, dict(_ok_proj, block_sha256="0" * 64),
                      dict(_inputs, env_complete=True)) is False)
check("D 拒用：输入指纹变了",
      G.fast_check_ok("\n" + _fixture_body, _ok_proj,
                      dict(_inputs, engine_py_sha256="deadbeef", env_complete=True)) is False)


# ═══════════════════════════════════════════════════════════════════════════
print()
print("== E/F. --check 语义分离 与 --strict-tests（进程内 fixture，hermetic）==")
# ═══════════════════════════════════════════════════════════════════════════
# 纪律：这里**不许**再起一次真实的 `tools/gen_docs.py --check` —— 那会把 173 个真实套件
# 再跑一遍（本套件自己就在里面），门禁会自我拖垮 + 超时。所以把 README /
# environment_complete / run_tests / AUTO 输入清单全部换成临时 fixture，直接调
# `G._main_impl()`。只有 argparse 层的互斥是安全的真子进程（parse 完就退出，不跑套件）。
E_FIX = TMP / "ef_engine"
E_README = E_FIX / "README.md"
REAL_AUTO = ENGINE / "out" / "auto-inputs.json"
_REAL_AUTO_BYTES = REAL_AUTO.read_bytes() if REAL_AUTO.is_file() else None


def ef_readme(body: str) -> None:
    write(E_README, "前文逐字节不动\n" + G.BEGIN + body + G.END + "后文逐字节不动\n")


def ef_pin(*, complete: bool, missing: list[str] | None = None, rows: list | None = None) -> None:
    """把 _main_impl 依赖的外部世界整体钉住（不碰真实 README / out / 套件）。"""
    G.README = E_README
    G.AUTO_INPUTS = TMP / "out" / "auto-inputs.json"
    G.load_auto_inputs = lambda: None            # 默认关掉快速通道：E/F 只验「现算」路径
    G.notes_audit_paths = lambda: None
    if complete:
        G.environment_complete = lambda: (True, [])
    else:
        G.environment_complete = lambda: (False, list(missing or ["稿件库"]))
    if rows is not None:
        G.run_tests = lambda *a, **k: (list(rows), sum(int(r[1]) for r in rows))


EF_ROWS_OK = [(f.stem, 2, 0, None) for f in G.test_files()[:4]]
EF_ROWS_BAD = [("fake_ok", 3, 0, None), ("fake_crash", 0, 0, "startup failed rc=1")]


def ef_check(*args: str) -> tuple[int, str]:
    """在进程内跑一次 _main_impl，返回 (rc, 合并输出)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            rc = G._main_impl(list(args))
        except SystemExit as e:                  # argparse 的 ap.error 走 SystemExit(2)
            rc = int(e.code or 0)
    return rc, buf.getvalue()


# —— E1：新鲜 → 非严格 --check 仍然 PASS（别把原语义改坏）——
ef_pin(complete=True, rows=EF_ROWS_OK)
_ef_body, _ = G.build_block(run_test_suites=False, test_rows_in=EF_ROWS_OK)
ef_readme("\n" + _ef_body)
_rc_e1, _out_e1 = ef_check("--check")
check("E1 新鲜块：非严格 --check rc==0", _rc_e1 == 0, f"rc={_rc_e1}｜{_out_e1[:200]}")
check("E1 新鲜块：结论行是 PASS", "PASS  AUTO 块" in _out_e1, _out_e1[:200])
check("E1 新鲜块：不出现「套件通过未达成」", "套件通过" not in _out_e1, _out_e1[:200])

# —— E2：块新鲜但套件真有异常 → 文档新鲜度 PASS 之外必须显式非 PASS 且 rc≠0 ——
ef_pin(complete=True, rows=EF_ROWS_BAD)
_ef_bad_body, _ = G.build_block(run_test_suites=False, test_rows_in=EF_ROWS_BAD)
ef_readme("\n" + _ef_bad_body)
_rc_e2, _out_e2 = ef_check("--check")
check("E2 套件异常：文档新鲜度仍报 PASS（两件事分开）", "PASS  AUTO 块" in _out_e2, _out_e2[:400])
check("E2 套件异常：必须打印明确的非 PASS 行", "FAIL  套件异常" in _out_e2, _out_e2[:600])
check("E2 套件异常：rc 必须非零（旧实现这里是 0）", _rc_e2 != 0, f"rc={_rc_e2}")
check("E2 套件异常：逐条列出异常套件与状态",
      "fake_crash" in _out_e2 and "startup failed rc=1" in _out_e2, _out_e2[:600])
check("E2 套件异常：不再用「不影响 --check 的结论」把异常咽掉",
      "不影响 --check 的结论" not in _out_e2, _out_e2[:600])

# —— E3：--no-test-runs 不声称任何套件状态（连跑都不该跑）——
def _explode(*a, **k):
    raise AssertionError("--no-test-runs 不许调 run_tests()")


ef_pin(complete=True, rows=EF_ROWS_OK)
G.run_tests = _explode
ef_readme("\n" + _ef_body)
_rc_e3, _out_e3 = ef_check("--check", "--no-test-runs")
check("E3 --no-test-runs：rc==0（块非测试行一致）", _rc_e3 == 0, f"rc={_rc_e3}｜{_out_e3[:200]}")
check("E3 --no-test-runs：明确说「本次未跑套件」", "本次未跑套件" in _out_e3, _out_e3[:300])
check("E3 --no-test-runs：绝不声称「套件通过」", "套件通过" not in _out_e3, _out_e3[:300])

# —— F1：严格入口缺前提 → BLOCKED + 原因清单 + rc==2（且不许退化成宽松）——
ef_pin(complete=False, missing=["稿件库", "LibreOffice"], rows=EF_ROWS_OK)
ef_readme("\n" + _ef_body)
_rc_f1, _out_f1 = ef_check("--check", "--strict-tests")
check("F1 严格缺前提：rc==2（专用退出码）", _rc_f1 == 2, f"rc={_rc_f1}｜{_out_f1[:200]}")
check("F1 严格缺前提：打印 BLOCKED", "BLOCKED" in _out_f1, _out_f1[:200])
check("F1 严格缺前提：原因清单逐条列出",
      "稿件库" in _out_f1 and "LibreOffice" in _out_f1, _out_f1[:400])
check("F1 严格缺前提：绝不退化成宽松归一化", "宽松校验" not in _out_f1, _out_f1[:400])
check("F1 严格缺前提：不打印任何 PASS 结论", "PASS  AUTO" not in _out_f1, _out_f1[:400])

# —— F2：严格入口前提齐全 → 真的现算并逐字比对；套件异常必须 rc≠0 ——
ef_pin(complete=True, rows=EF_ROWS_OK)
ef_readme("\n" + _ef_body)
_rc_f2, _out_f2 = ef_check("--check", "--strict-tests")
check("F2 严格齐全：rc==0 且声明「严格测试校验（前提齐全）」",
      _rc_f2 == 0 and "严格测试校验" in _out_f2, f"rc={_rc_f2}｜{_out_f2[:300]}")
ef_pin(complete=True, rows=EF_ROWS_BAD)
ef_readme("\n" + _ef_bad_body)
_rc_f3, _out_f3 = ef_check("--check", "--strict-tests")
check("F2 严格齐全 + 套件异常：rc≠0", _rc_f3 != 0, f"rc={_rc_f3}")
check("F2 严格齐全 + 套件异常：报出异常套件", "FAIL  套件异常" in _out_f3, _out_f3[:600])

# —— F3：严格模式不许借「3 元素旧缓存行」走快速通道 ——
# 非严格：块指纹与输入指纹都对得上 → 走快速通道（历史行为，保留）。
# 严格：缓存行是 3 列（没有 status 证据）→ 必须回退到现算，不许宣布 PASS。
import hashlib as _hl2  # noqa: E402
_ef_inputs = G.collect_auto_inputs()
_cached_body, _ = G.build_block(run_test_suites=False, test_rows_in=EF_ROWS_OK)
ef_readme("\n" + _cached_body)
_blk_sha2 = _hl2.sha256(
    G.normalize_for_check("\n" + _cached_body, lenient=False).encode("utf-8")).hexdigest()
_legacy_proj = {"schema": G.AUTO_INPUTS_SCHEMA, "inputs": dict(_ef_inputs, env_complete=True),
                "test_rows": [["test_x", 1, 0]], "block_sha256": _blk_sha2}
ef_pin(complete=True, rows=EF_ROWS_OK)
G.load_auto_inputs = lambda: dict(_legacy_proj)
_rc_nf, _out_nf = ef_check("--check")
check("F3 非严格：缓存指纹对上时走快速通道（历史行为保留）",
      _rc_nf == 0 and "快速通道" in _out_nf, f"rc={_rc_nf}｜{_out_nf[:300]}")
_RAN = {"n": 0}


def _counting_run(*a, **k):
    _RAN["n"] += 1
    return list(EF_ROWS_OK), sum(int(r[1]) for r in EF_ROWS_OK)


ef_pin(complete=True)
G.run_tests = _counting_run
G.load_auto_inputs = lambda: dict(_legacy_proj)
_rc_sf, _out_sf = ef_check("--check", "--strict-tests")
check("F3 严格：3 列缓存行不足以走快速通道 → 必须现算",
      "快速通道" not in _out_sf and _RAN["n"] >= 1, f"rc={_rc_sf}｜ran={_RAN['n']}｜{_out_sf[:300]}")

# —— F5：缓存能不能当「套件通过」的证据 ——
# 反例（lead 实测）：只篡改 out/auto-inputs.json 的 test_rows ——
#   ① 真实 174 个套件，缓存只留 1 行；② 单套断言数改成 99999。
# 旧实现的 `suite_rows_trusted` 只看「非空 + ≥4 列 + status 全 None」，从不核对**覆盖面**，
# 于是截断/篡改过的缓存也能当"全部套件都绿过"的证据，严格档直接走快速通道 rc=0 PASS。
# 修法：`suite_rows_trusted(rows, expected=[...])` —— 给了期望名单就必须**恰好**覆盖。
_ALL_STEMS = [f.stem for f in G.test_files()]
check("F5 前提：真实套件数 > 1（否则下面的截断反例无意义）", len(_ALL_STEMS) > 1, len(_ALL_STEMS))

# 两种调用形态：不带 expected（历史语义，只看形状与 status）、带 expected（校验覆盖面）。
_ROWS_TRUNC = [[_ALL_STEMS[0], 45, 0, None]]
_ROWS_FULL = [[s, 1, 0, None] for s in _ALL_STEMS]
check("F5 形态① `suite_rows_trusted(rows)`（不给 expected）：截断的缓存行仍只看形状 → True",
      G.suite_rows_trusted(_ROWS_TRUNC) is True, _ROWS_TRUNC)
check("F5 形态② `suite_rows_trusted(rows, expected=...)`：同样的截断行被覆盖面校验拒掉",
      G.suite_rows_trusted(_ROWS_TRUNC, expected=_ALL_STEMS) is False, len(_ALL_STEMS))
check("F5 形态② 正向：174 行且套件名逐一匹配 → True",
      G.suite_rows_trusted(_ROWS_FULL, expected=_ALL_STEMS) is True, len(_ROWS_FULL))

# F5c 的正向缓存：与真实 test_files() 完全匹配、status 全 None。
_F5_BODY, _ = G.build_block(run_test_suites=False, test_rows_in=[(s, 1, 0, None) for s in _ALL_STEMS])
ef_readme("\n" + _F5_BODY)
_F5_INPUTS = G.collect_auto_inputs()
_F5_SHA = _hl2.sha256(
    G.normalize_for_check("\n" + _F5_BODY, lenient=False).encode("utf-8")).hexdigest()


def _f5_proj(rows: list) -> dict:
    """按真实快照的形状造一份缓存（唯一变量是 test_rows）。"""
    return {"schema": G.AUTO_INPUTS_SCHEMA, "inputs": dict(_F5_INPUTS, env_complete=True),
            "test_rows": rows, "block_sha256": _F5_SHA}


def _f5_run(*args: str, rows: list, spy_rows: list | None = None) -> tuple[int, str, int]:
    """跑一次 --check，返回 (rc, 输出, run_tests 被调次数)。

    `spy_rows` 是「回退现算」时替身 run_tests 回报的行。默认退回全绿且与 README 一致；
    反例场景传一份 PASS 数不同的行，这样一旦真的回退现算，文档就必然对不上 → rc≠0，
    于是「缓存被拒」在 rc 上就能看出来，而不是被一次巧合的现算 PASS 掩盖。
    """
    ran = {"n": 0}
    _rows = list(spy_rows) if spy_rows is not None else [(s, 1, 0, None) for s in _ALL_STEMS]

    def _spy(*a, **k):
        ran["n"] += 1
        return list(_rows), sum(int(r[1]) for r in _rows)

    ef_pin(complete=True)
    G.run_tests = _spy
    G.load_auto_inputs = lambda: _f5_proj(rows)
    rc, out = ef_check(*args)
    return rc, out, ran["n"]


# 反例里让现算结果与 README 不同（PASS 1 → 2），使「回退现算」必然以 rc≠0 收场。
_SPY_DRIFT = [(s, 2, 0, None) for s in _ALL_STEMS]

# F5a：严格 + 缓存只留 1 行（谎称覆盖 1/174）→ 不许走快速通道、不许 rc=0 PASS。
_rc_f5a, _out_f5a, _ran_f5a = _f5_run("--check", "--strict-tests",
                                      rows=_ROWS_TRUNC, spy_rows=_SPY_DRIFT)
check("F5a 严格+截断缓存：不得走快速通道", "快速通道" not in _out_f5a, _out_f5a[:300])
check("F5a 严格+截断缓存：必须回退现算（run_tests 被调 ≥1 次）", _ran_f5a >= 1, _ran_f5a)
check("F5a 严格+截断缓存：不得 rc=0（缓存不足采信，回退后如实报 FAIL）",
      _rc_f5a != 0, f"rc={_rc_f5a}｜ran={_ran_f5a}｜{_out_f5a[:400]}")
check("F5a 严格+截断缓存：绝不出现「缓存 1 行 / 期望 174 个套件」那种放行说明",
      "证据来源：本次未跑套件" not in _out_f5a, _out_f5a[:400])

# F5b：严格 + 套件名集合不符（少一个 / 多一个 / 换一个）→ 同样拒。
_ROWS_MISSING = [[s, 1, 0, None] for s in _ALL_STEMS[:-1]]
_ROWS_EXTRA = [[s, 1, 0, None] for s in _ALL_STEMS] + [["fake_extra_suite", 1, 0, None]]
_ROWS_RENAMED = [[s if i else "fake_renamed_suite", 1, 0, None]
                 for i, s in enumerate(_ALL_STEMS)]
check("F5b 少一个套件 → suite_rows_trusted 拒",
      G.suite_rows_trusted(_ROWS_MISSING, expected=_ALL_STEMS) is False, len(_ROWS_MISSING))
check("F5b 多一个套件 → suite_rows_trusted 拒",
      G.suite_rows_trusted(_ROWS_EXTRA, expected=_ALL_STEMS) is False, len(_ROWS_EXTRA))
check("F5b 换一个套件名（行数不变）→ suite_rows_trusted 拒",
      G.suite_rows_trusted(_ROWS_RENAMED, expected=_ALL_STEMS) is False, _ROWS_RENAMED[0])
for _lbl, _rows_b, _name in (("少一个", _ROWS_MISSING, "F5b 少一个套件"),
                             ("多一个", _ROWS_EXTRA, "F5b 多一个套件"),
                             ("换名", _ROWS_RENAMED, "F5b 换名")):
    _rc_b, _out_b, _ran_b = _f5_run("--check", "--strict-tests",
                                    rows=_rows_b, spy_rows=_SPY_DRIFT)
    check(f"{_name}：严格档不得走快速通道", "快速通道" not in _out_b, _out_b[:300])
    check(f"{_name}：严格档必须回退现算", _ran_b >= 1, _ran_b)
    check(f"{_name}：不得 rc=0（缓存覆盖面对不上 → 回退后如实报 FAIL）", _rc_b != 0,
          f"rc={_rc_b}｜{_out_b[:300]}")
    check(f"{_name}：不得打印严格缓存放行说明",
          "证据来源：本次未跑套件" not in _out_b, _out_b[:300])

# F5c：严格 + 缓存 174 行且集合完全匹配、status 全 None → **允许**走快速通道（正向兼容）。
_rc_f5c, _out_f5c, _ran_f5c = _f5_run("--check", "--strict-tests", rows=_ROWS_FULL)
check("F5c 严格+完整缓存：允许走快速通道（别把功能做成永不命中）",
      _rc_f5c == 0 and "快速通道" in _out_f5c, f"rc={_rc_f5c}｜{_out_f5c[:400]}")
check("F5c 严格+完整缓存：本次确实没跑套件", _ran_f5c == 0, _ran_f5c)
check("F5c 严格+完整缓存：证据来源可读（缓存行数 / 期望套件数 / status 全 None）",
      "证据来源：本次未跑套件" in _out_f5c
      and f"缓存 {len(_ROWS_FULL)} 行" in _out_f5c
      and f"期望 {len(_ALL_STEMS)} 个套件" in _out_f5c
      and "status 全 None" in _out_f5c, _out_f5c[:600])

# F5d：非严格 + 同样被截断的缓存 → 历史行为保持（仍可走快速通道），证明没顺手改掉非严格语义。
_rc_f5d, _out_f5d, _ran_f5d = _f5_run("--check", rows=_ROWS_TRUNC)
check("F5d 非严格+截断缓存：仍走快速通道（历史语义一字不动）",
      _rc_f5d == 0 and "快速通道" in _out_f5d, f"rc={_rc_f5d}｜{_out_f5d[:300]}")
check("F5d 非严格：不打印严格档才有的证据来源行",
      "证据来源：本次未跑套件" not in _out_f5d, _out_f5d[:300])

# —— F5e：**集合全对、数字被改**的缓存 ——
# `suite_rows_trusted(expected=...)` 只看「套件名是否恰好覆盖」，它**读不懂数字**：
# 保留全部 174 行、只把某个套件的断言数从 1 改成 99999，覆盖面依旧完美匹配。
# 所以严格档还有第二道闸 `cached_rows_match_block()`：拿缓存里的计数重渲染一遍，
# 与 README 里已经写着的测试段逐行对比 —— 缓存是"自己说自己对"，不能自证。
_ROWS_NUMDRIFT = [[s, (99999 if i == 0 else 1), 0, None] for i, s in enumerate(_ALL_STEMS)]
check("F5e 前提：数字篡改后套件名覆盖面依然完全匹配（说明只靠 expected 挡不住）",
      G.suite_rows_trusted(_ROWS_NUMDRIFT, expected=_ALL_STEMS) is True, len(_ROWS_NUMDRIFT))
check("F5e 第二道闸：缓存计数与块内测试段逐行一致时才为 True（正向）",
      G.cached_rows_match_block("\n" + _F5_BODY, _ROWS_FULL) is True, "F5c 的正向缓存")
check("F5e 第二道闸：数字被改 → 重渲染与块对不上 → False",
      G.cached_rows_match_block("\n" + _F5_BODY, _ROWS_NUMDRIFT) is False, _ROWS_NUMDRIFT[0])
check("F5e 第二道闸：没有缓存行 → False（不许把空缓存当证据）",
      G.cached_rows_match_block("\n" + _F5_BODY, None) is False
      and G.cached_rows_match_block("\n" + _F5_BODY, []) is False, "None / []")
check("F5e 第二道闸：块里找不到测试段（不是本脚本写的块）→ False",
      G.cached_rows_match_block("\n**普查**：没有测试段的块\n", _ROWS_FULL) is False, "缺测试段")
_rc_f5e, _out_f5e, _ran_f5e = _f5_run("--check", "--strict-tests",
                                      rows=_ROWS_NUMDRIFT, spy_rows=_SPY_DRIFT)
check("F5e 严格+数字篡改缓存：不得走快速通道", "快速通道" not in _out_f5e, _out_f5e[:400])
check("F5e 严格+数字篡改缓存：必须回退现算", _ran_f5e >= 1, _ran_f5e)
check("F5e 严格+数字篡改缓存：不得 rc=0", _rc_f5e != 0, f"rc={_rc_f5e}｜{_out_f5e[:400]}")
check("F5e 严格+数字篡改缓存：不得打印严格缓存放行说明",
      "证据来源：本次未跑套件" not in _out_f5e, _out_f5e[:400])

# ═══════════════════════════════════════════════════════════════════════════
print()
print("== G. 写模式缓存复用（卡 §7 第 1 项：不可信缓存不得升级成「套件通过」）==")
# ═══════════════════════════════════════════════════════════════════════════
# qoder §18 / Codex lead §19 的缺口：写模式当时直接调 `cached_test_rows()`，那个函数的判据
# 只是「不含失败证据」，于是**缺 status 的三列缓存**也能过；写模式随后 `_norm_test_rows()`
# 把 status 补成 None、渲染成 ✅ 并 `save_auto_inputs()` 落盘 —— 无状态证据被升级成成功证据。
# 现在写模式走 `reusable_test_rows()`：`cached_test_rows()` + 4 列 status + 覆盖面 + 与块自证。
_W_BODY, _ = G.build_block(run_test_suites=False, test_rows_in=[(s, 1, 0, None) for s in _ALL_STEMS])
_ROWS_3COL_REAL = [[s, 1, 0] for s in _ALL_STEMS]   # 三列、名字全真：cached_test_rows 会放行

check("G 前提：三列真实名单的缓存能过旧的宽口径 `cached_test_rows`（缺口本体）",
      G.cached_test_rows(_F5_INPUTS, _f5_proj(_ROWS_3COL_REAL)) is not None,
      G.cached_test_rows(_F5_INPUTS, _f5_proj(_ROWS_3COL_REAL)))
check("G 缺口已闭合：同一份三列缓存被 `reusable_test_rows` 拒（缺 status 不算通过证据）",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_ROWS_3COL_REAL), "\n" + _W_BODY) is None)
check("G 拒用：截断缓存（174 个套件只剩 1 行）",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_ROWS_TRUNC), "\n" + _W_BODY) is None)
check("G 拒用：少一个套件",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_ROWS_MISSING), "\n" + _W_BODY) is None)
check("G 拒用：多一个套件",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_ROWS_EXTRA), "\n" + _W_BODY) is None)
check("G 拒用：换掉一个套件名（行数不变）",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_ROWS_RENAMED), "\n" + _W_BODY) is None)
check("G 拒用：数字被篡改（套件名全覆盖、计数与块对不上）",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_ROWS_NUMDRIFT), "\n" + _W_BODY) is None)
check("G 拒用：带 FAIL 计数的缓存（B-110 语义在写模式也生效）",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj([[s, 1, 1, None] for s in _ALL_STEMS]),
                           "\n" + _W_BODY) is None)
check("G 拒用：带非 None status 的缓存",
      G.reusable_test_rows(_F5_INPUTS,
                           _f5_proj([[s, 1, 0, "startup failed rc=1"] for s in _ALL_STEMS]),
                           "\n" + _W_BODY) is None)
check("G 拒用：块里没有测试段（缓存无法与块互证）",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_ROWS_FULL), "\n**普查**：没有测试段\n") is None)
check("G 拒用：schema 不是 2",
      G.reusable_test_rows(_F5_INPUTS, dict(_f5_proj(_ROWS_FULL), schema=1), "\n" + _W_BODY) is None)
check("G 正向：真实的 4 列全绿缓存 + 与块一致 → 仍可复用（没有靠关掉缓存修绿）",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_ROWS_FULL), "\n" + _W_BODY) == _ROWS_FULL)


def _g_write(rows: list, body: str | None = None) -> tuple[int, str, int]:
    """跑一次**写模式**（无参 _main_impl），返回 (rc, 输出, run_tests 被调次数)。

    `body` 给了就用它当 README 的 AUTO 块正文（卡 §9 的 all-fake 自洽场景要用全假名块），
    缺省用 `_W_BODY`（真实 174 个套件的块）。
    """
    ran = {"n": 0}

    def _spy(*a, **k):
        ran["n"] += 1
        return list(_SPY_DRIFT), sum(int(r[1]) for r in _SPY_DRIFT)

    ef_pin(complete=True)
    G.run_tests = _spy
    G.load_auto_inputs = lambda: _f5_proj(rows)
    ef_readme("\n" + (body if body is not None else _W_BODY))   # 每次从干净块起步
    rc, out = ef_check()
    return rc, out, ran["n"]


# G1：写模式遇到三列缓存 → 拒，回退到现跑（§7 要求的「转向实际执行分支」）
_rc_g1, _out_g1, _ran_g1 = _g_write(_ROWS_3COL_REAL)
check("G1 写模式+三列缓存：必须回退现算（run_tests 被调 ≥1 次）", _ran_g1 >= 1, _ran_g1)
check("G1 写模式+三列缓存：输入清单行报「现跑」而不是「复用缓存」",
      "测试计数现跑" in _out_g1 and "测试计数复用缓存" not in _out_g1, _out_g1[-400:])

# G2：写模式遇到截断/篡改缓存 → 同样回退
for _lbl, _rows_g in (("截断", _ROWS_TRUNC), ("数字篡改", _ROWS_NUMDRIFT),
                      ("换名", _ROWS_RENAMED), ("少一个", _ROWS_MISSING)):
    _rc_g, _out_g, _ran_g = _g_write(_rows_g)
    check(f"G2 写模式+{_lbl}缓存：回退现算", _ran_g >= 1, _ran_g)
    check(f"G2 写模式+{_lbl}缓存：不声称复用缓存",
          "测试计数复用缓存" not in _out_g, _out_g[-300:])
    check(f"G2 写模式+{_lbl}缓存：落盘的是现算行（4 列、status 全 None）",
          (json.loads(G.AUTO_INPUTS.read_text(encoding="utf-8")).get("test_rows")
           == [list(r) for r in _SPY_DRIFT]), "落盘行见 out/auto-inputs.json")

# G3：写模式遇到**可信**缓存 → 仍然复用（不许把功能做成永不命中）
_rc_g3, _out_g3, _ran_g3 = _g_write(_ROWS_FULL)
check("G3 写模式+可信缓存：复用缓存、不跑套件", _ran_g3 == 0, _ran_g3)
check("G3 写模式+可信缓存：输入清单行报「复用缓存」", "测试计数复用缓存" in _out_g3,
      _out_g3[-400:])
check("G3 写模式+可信缓存：README 块逐字节不变（幂等）", "\n" + _W_BODY in (
    G.README.read_text(encoding="utf-8")), "块被改写了")

# ═══════════════════════════════════════════════════════════════════════════
print()
print("== H. 卡 §9 第 1 项：全假名缓存 + 全假名块「自洽」也必须拒 ==")
# ═══════════════════════════════════════════════════════════════════════════
# qoder §22.2.1 的残余：写模式当时有一段 `if covered and (...)` —— 缓存行**一行都对不上**
# 当前套件表时（covered == 0）整段跳过覆盖面校验，于是 all-fake 缓存 + all-fake README 段
# 只要互相自洽就能复用并落盘。卡 §9 第 1 项要求删掉这个特例：名单必须**无条件恰好**覆盖。
_ROWS_ALLFAKE = [["test_a", 3, 0, None], ["test_b", 4, 0, None]]
_ALLFAKE_BODY, _ = G.build_block(run_test_suites=False,
                                 test_rows_in=[("test_a", 3, 0, None), ("test_b", 4, 0, None)])
check("H0 前提：全假名缓存能过旧的宽口径 cached_test_rows（缺口本体）",
      G.cached_test_rows(_F5_INPUTS, _f5_proj(_ROWS_ALLFAKE)) is not None,
      G.cached_test_rows(_F5_INPUTS, _f5_proj(_ROWS_ALLFAKE)))
check("H0 前提：全假名缓存与全假名块**互为自证**（只靠块比对挡不住）",
      G.cached_rows_match_block("\n" + _ALLFAKE_BODY, _ROWS_ALLFAKE) is True,
      "块与缓存同源 → 逐行一致")
check("H1 卡 §9 第 1 项：全假名缓存 + 自洽块 → reusable_test_rows 必须拒（无 covered==0 豁免）",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_ROWS_ALLFAKE), "\n" + _ALLFAKE_BODY) is None)
check("H2 覆盖面判据是无条件的：expected 缺一行/多一行/全不符都 False",
      G.suite_rows_trusted(_ROWS_ALLFAKE) is True            # 只给形状时 True（历史语义保留）
      and G.suite_rows_trusted(_ROWS_ALLFAKE, expected=_ALL_STEMS) is False)
_rc_h3, _out_h3, _ran_h3 = _g_write(_ROWS_ALLFAKE, body=_ALLFAKE_BODY)
check("H3 写模式+全假名缓存：必须回退现算（run_tests 被调 ≥1 次）", _ran_h3 >= 1, _ran_h3)
check("H3 写模式+全假名缓存：不声称复用缓存",
      "测试计数复用缓存" not in _out_h3, _out_h3[-300:])
check("H3 写模式+全假名缓存：落盘的不是假名（现算行）",
      (json.loads(G.AUTO_INPUTS.read_text(encoding="utf-8")).get("test_rows")
       == [list(r) for r in _SPY_DRIFT]), "落盘行见 out/auto-inputs.json")

# ═══════════════════════════════════════════════════════════════════════════
print()
print("== I. 卡 §9 第 2 项：status 必须**严格 None**，不按 truthiness ==")
# ═══════════════════════════════════════════════════════════════════════════
# qoder §22.2.2 的残余：`suite_rows_trusted()` 曾写 `if r[3]: return False`、`abnormal_suites()`
# 同样按 truthiness、`_norm_test_rows()` 又 `str(st) if st else None` —— 三层叠加让 `""` / False / 0
# 一路被当成「没有状态」，于是写模式把这种缓存洗成 None 落盘成「全绿证据」、严格快路也 rc0。
_FALSY_ROWS = [[s, 1, 0, _v] for s, _v in zip(_ALL_STEMS, itertools.cycle(["", False, 0]))]
check("I0 前提：这份缓存确实全部是 falsy 状态（空串/False/0）",
      all(not r[3] for r in _FALSY_ROWS) and all(r[3] is not None for r in _FALSY_ROWS),
      [r[3] for r in _FALSY_ROWS[:3]])
check("I1 suite_rows_trusted：falsy 状态不算 status 全 None → False",
      G.suite_rows_trusted(_FALSY_ROWS) is False)
check("I2 abnormal_suites：falsy 状态要被看见（不是空列表）",
      len(G.abnormal_suites(_FALSY_ROWS)) == len(_ALL_STEMS), G.abnormal_suites(_FALSY_ROWS)[:2])
check("I3 cached_test_rows：带 falsy 状态的缓存在第一道闸就被拒",
      G.cached_test_rows(_F5_INPUTS, _f5_proj(_FALSY_ROWS)) is None)
check("I4 reusable_test_rows：写模式拒用带 falsy 状态的缓存",
      G.reusable_test_rows(_F5_INPUTS, _f5_proj(_FALSY_ROWS), "\n" + _W_BODY) is None)
check("I5 _norm_test_rows：falsy 状态**原样保留**，不许洗成 None（否则落盘＝伪造全绿）",
      [r[3] for r in G._norm_test_rows(_FALSY_ROWS)] == [str(r[3]) for r in _FALSY_ROWS],
      [r[3] for r in G._norm_test_rows(_FALSY_ROWS)][:3])
check("I6 渲染：带 falsy 状态的行走 ❌ 分支，不许渲染出 ✅",
      "✅" not in "".join(G.render_test_section(G._norm_test_rows(_FALSY_ROWS))),
      G.render_test_section(G._norm_test_rows(_FALSY_ROWS))[0][:160])
_rc_i7, _out_i7, _ran_i7 = _g_write(_FALSY_ROWS)
check("I7 写模式+falsy 状态缓存：必须回退现算（run_tests 被调 ≥1 次）", _ran_i7 >= 1, _ran_i7)
check("I7 写模式+falsy 状态缓存：不声称复用缓存",
      "测试计数复用缓存" not in _out_i7, _out_i7[-300:])
check("I8 写模式+falsy 状态缓存：落盘不变形（现算 4 列、status 全为 None）",
      (json.loads(G.AUTO_INPUTS.read_text(encoding="utf-8")).get("test_rows")
       == [list(r) for r in _SPY_DRIFT]), "落盘行见 out/auto-inputs.json")
_rc_i9, _out_i9, _ran_i9 = _f5_run("--check", "--strict-tests",
                                   rows=_FALSY_ROWS, spy_rows=_SPY_DRIFT)
check("I9 严格快路+falsy 状态缓存：不得走快速通道", "快速通道" not in _out_i9, _out_i9[:400])
# 「不采信」的正确表现是**回退到真实执行**，不是把 rc 强行做成非零：回退后现跑的 174 个套件
# 若全绿，rc=0 是诚实的结论（证明的是"本次真跑了"，而不是"缓存说它绿"）。判据落在
# 「有没有真跑」与「有没有拿缓存当证据」上，别用 rc 代替。
check("I9 严格快路+falsy 状态缓存：必须回退现算（真跑了套件）",
      _ran_i9 >= 1 and "本次实跑" in _out_i9, f"ran={_ran_i9}｜{_out_i9[:400]}")
check("I9 严格快路+falsy 状态缓存：不得把缓存当作「套件通过」的证据",
      "证据来源：本次未跑套件" not in _out_i9, _out_i9[:400])
check("I9 严格快路+falsy 状态缓存：不得打印「status 全 None」的放行说明",
      "status 全 None" not in _out_i9, _out_i9[:400])

# —— F4：argparse 层两条互斥/依赖约束（真子进程；parse 完即退出，不跑套件）——
PY = G.PY if Path(G.PY).is_file() else sys.executable
GEN = ENGINE / "tools" / "gen_docs.py"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ENGINE / "vendor"), SLCN_RUN_RECORD="0")
    env.pop("SLCN_GEN_DOCS_NESTED", None)
    return subprocess.run([PY, "-u", str(GEN), *args], cwd=str(ENGINE), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


_p_both = run_cli("--check", "--no-test-runs", "--strict-tests")
check("F4 --no-test-runs + --strict-tests 互斥：rc!=0（argparse）", _p_both.returncode != 0,
      _p_both.returncode)
check("F4 互斥：报错文案写明「互斥」", "互斥" in (_p_both.stdout + _p_both.stderr),
      (_p_both.stdout + _p_both.stderr)[-300:])
_p_write = run_cli("--strict-tests")
check("F4 --strict-tests 不带 --check：rc!=0（写模式不许假装严格）",
      _p_write.returncode != 0, _p_write.returncode)

# —— 隔离：本套件绝不写真实 out/auto-inputs.json ——
_now_bytes = REAL_AUTO.read_bytes() if REAL_AUTO.is_file() else None
check("E0 隔离：本套件没有改动真实的 out/auto-inputs.json", _now_bytes == _REAL_AUTO_BYTES,
      "size %s → %s" % (len(_REAL_AUTO_BYTES or b""), len(_now_bytes or b"")))

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
