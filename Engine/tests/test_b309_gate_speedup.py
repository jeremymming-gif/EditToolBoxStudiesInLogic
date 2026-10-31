#!/usr/bin/env python3
"""B-309 门禁/CI/测试执行提速：契约 + 反例（全部 hermetic）。

钉住（不读真实稿件库、不写仓库 out/、不开真窗口；跑一次约 5 秒）：
  (a) `corpus.py --out DIR`：输出根可换、`_worker_init` 把 OUT 带进子进程（macOS 3.14
      spawn 下父进程改全局无效）、两遍 CSV 逐字节一致；不传 --out 时默认仍是
      `Engine/out/corpus`，仓库里的 canonical `out/corpus/` 一个字节都不许动。
  (b) `run_engine_tests.sh` 的 `SLCN_TEST_JOBS`：并行跑 → 红套件**串行复核** →
      抖动（并行红/串行绿）不计红；退出码 = 复核后的红套数；汇总行格式不变；
      串行 allowlist 照跑照计数；非法值退出 2；`SLCN_TEST_JOBS` 不设时走旧串行。
  (c) `test_bridge.py` 的 `SLCN_BRIDGE_SKIP_GUI=1`：只跳 (g)/(h) 真窗口与 (o) 的
      tkinter import，纯逻辑断言照跑、退出 0；默认分支仍在（静态守卫）。
  (d) `ci.yml`：engine 套件 3 路并行、AUTO 守卫显式 SKIP（缺本机解释器时退出 0）、
      swift 的桥梁步骤**不设** SKIP_GUI（spec S5：该 env 只由本地编排器设置，CI 走默认分支）。
  (e) 笔记仓 `tools/run_gates.py`（S1）：`--help` 可用；lint errors 解析失败不得 PASS；
      依赖被 `--skip` 时依赖方标 dep_skipped（不静默绿）、依赖未被 `--only` 选中时才 WARN
      standalone（没有笔记仓时 SKIP）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
REPO = ENGINE.parent
PY = sys.executable

_passed = 0
_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}  {detail}")


def run(args, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def clean_env(**over) -> dict:
    """去掉外层档位变量，避免 `SLCN_TEST_JOBS=4` 跑 full 时污染被测脚本。"""
    e = {k: v for k, v in os.environ.items() if not k.startswith("SLCN_TEST_")}
    e.pop("SLCN_BRIDGE_SKIP_GUI", None)
    e.update({k: str(v) for k, v in over.items()})
    return e


def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest() if path.is_file() else ""


def text_of(path: Path) -> str:
    """缺文件时返回空串：断言失败要给行号，不能让测试自己抛异常。"""
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# ─────────────────────────────────────────────────────────────────────────
print("\n── (a) corpus.py --out：输出根可换 / worker 带 OUT / CSV 可复现 ──")
_tmp = Path(tempfile.mkdtemp(prefix="b309_corpus_"))
try:
    _canon = ENGINE / "out" / "corpus" / "corpus.csv"
    _canon_before = sha1(_canon)
    # 用 --limit 0 的等价物：直接替掉 collect/_require_manuscripts，只测写路径。
    # 必须 --jobs 1：并行路径在 spawn 子进程里重新 import corpus，patch 不会带过去。
    _script = """
import os, sys
sys.path.insert(0, %r)
import corpus
corpus._require_manuscripts = lambda: True
corpus.collect = lambda: [
    ("2026年第5期(90)", "01 A_Author", "/nowhere/a.tex"),
    ("2026年第5期(90)", "02 B_Author", "/nowhere/b.tex"),
]
corpus._worker_run = lambda it: corpus.Row(it[0], it[1], "LaTeX", "zh", True,
                                          raw=0.9, content=0.8)
sys.argv = ["corpus.py", "--out", sys.argv[1], "--jobs", "1"]
rc = corpus.main()
assert rc == 0, rc
assert corpus.OUT == os.path.abspath(sys.argv[2]), corpus.OUT
# spawn 安全：initializer 的第 4 个参数必须落到子进程的全局 OUT
corpus._worker_init(None, None, True, os.path.join(os.path.abspath(sys.argv[2]), "w"))
assert corpus.OUT == os.path.join(os.path.abspath(sys.argv[2]), "w"), corpus.OUT
print("WORKER_OUT_OK")
""" % (str(ENGINE),)

    def _corpus_run(out_dir: Path) -> subprocess.CompletedProcess:
        return run([PY, "-c", _script, str(out_dir)], cwd=str(ENGINE),
                   env=clean_env(PYTHONPATH=str(ENGINE / "vendor")))

    d1, d2 = _tmp / "p1", _tmp / "p2"
    r1, r2 = _corpus_run(d1), _corpus_run(d2)
    check("(a) 两次 --out 运行都退出 0", r1.returncode == 0 and r2.returncode == 0,
          f"rc={r1.returncode}/{r2.returncode} err={(r1.stderr + r2.stderr)[:300]}")
    check("(a) `_worker_init(..., OUT)` 把输出根带进子进程（spawn 安全）",
          "WORKER_OUT_OK" in (r1.stdout + r2.stdout), r1.stdout[-300:])
    c1, c2 = d1 / "corpus.csv", d2 / "corpus.csv"
    check("(a) corpus.csv 落在 --out 目录（默认 out/corpus 不被写）",
          c1.is_file() and c2.is_file() and not (d1 / "out").exists(),
          f"{c1.is_file()}/{c2.is_file()}")
    check("(a) 两遍 CSV 逐字节一致", c1.is_file() and c2.is_file()
          and c1.read_bytes() == c2.read_bytes())
    check("(a) 两行都按 `_worker_run` 的返回落盘（raw=0.900000）",
          c1.is_file() and c1.read_text(encoding="utf-8").count("0.900000") == 2,
          c1.read_text(encoding="utf-8")[:300] if c1.is_file() else "缺文件")
    check("(a) 仓库 canonical out/corpus/corpus.csv 未被改写",
          sha1(_canon) == _canon_before, f"{_canon_before[:12]}→{sha1(_canon)[:12]}")
    r3 = run([PY, "-c", "import corpus; print(corpus.OUT)"], cwd=str(ENGINE),
             env=clean_env(PYTHONPATH=str(ENGINE / "vendor")))
    check("(a) 不传 --out 时默认输出根仍是 Engine/out/corpus",
          r3.returncode == 0 and Path(r3.stdout.strip()) == ENGINE / "out" / "corpus",
          r3.stdout.strip() + r3.stderr[:200])
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) run_engine_tests.sh 并行档：串行复核 / 抖动 / 汇总行 / 退出码 ──")
_tmp = Path(tempfile.mkdtemp(prefix="b309_engine_"))
try:
    _eng = _tmp / "Engine"
    (_eng / "tools").mkdir(parents=True)
    (_eng / "tests").mkdir()
    shutil.copy(ENGINE / "tools" / "run_engine_tests.sh", _eng / "tools" / "run_engine_tests.sh")
    _suites = {
        "test_ok": 'print("ok")\n',
        "test_red": 'print("FAIL 故意红")\nraise SystemExit(1)\n',
        # 只在并行档红：用来验证「并行红 → 串行复核绿 = 抖动，不计红」
        "test_flaky": (
            'import os\n'
            'p = os.environ.get("SLCN_TEST_PHASE", "")\n'
            'print("phase=%s" % p)\n'
            'raise SystemExit(1 if p == "parallel" else 0)\n'),
        # allowlist 套件：必须串行跑、且照常计数
        "test_shared_paths": (
            'import os\n'
            'print("phase=%s" % os.environ.get("SLCN_TEST_PHASE", ""))\n'),
    }
    for _name, _body in _suites.items():
        (_eng / "tests" / f"{_name}.py").write_text(_body, encoding="utf-8")

    def _suite_env(**over) -> dict:
        return clean_env(SLCN_TEST_PYTHON=PY, **over)

    r = run(["bash", "tools/run_engine_tests.sh", "full"], cwd=str(_eng),
            env=_suite_env(SLCN_TEST_JOBS="2",
                           SLCN_TEST_SERIAL_ALLOWLIST="test_shared_paths"))
    out = r.stdout + r.stderr
    check("(b) 并行档退出码 = 复核后的红套数（1，不是并行红 2）",
          r.returncode == 1, f"rc={r.returncode}")
    check("(b) 汇总行格式不变（`汇总：N 套件，红 R ｜ full 档耗时 S 秒`）",
          re.search(r"汇总：4 套件，红 1 ｜ full 档耗时 [\d.]+ 秒", out) is not None,
          [ln for ln in out.splitlines() if ln.startswith("汇总")])
    check("(b) 并行红/串行复核红/抖动分别报告",
          "并行红 2 套 → 串行复核红 1 套（抖动 1 套）" in out, out[-500:])
    check("(b) 四套都跑了、没有静默跳过（PASS/FAIL/⚠️ 各一行）",
          len(re.findall(r"^  (?:PASS|FAIL|⚠️) ", out, re.M)) == 4
          and "PASS test_ok" in out and "FAIL test_red" in out
          and "⚠️ test_flaky" in out and "PASS test_shared_paths" in out)
    _kept = re.search(r"完整日志：([^\s）]+)", out)
    _logs = Path(_kept.group(1)) if _kept else None
    check("(b) 有红时保留日志目录并打印路径", _logs is not None and _logs.is_dir(), str(_logs))
    _allow_log = (_logs / "test_shared_paths.allow.log") if _logs else Path("/nonexistent")
    _par_log = (_logs / "test_flaky.par.log") if _logs else Path("/nonexistent")
    _ser_log = (_logs / "test_flaky.serial.log") if _logs else Path("/nonexistent")
    check("(b) allowlist 套件确实串行跑（phase=serial 落盘）",
          "phase=serial" in text_of(_allow_log))
    check("(b) 抖动套件：并行日志 phase=parallel、复核 phase=serial",
          "phase=parallel" in text_of(_par_log) and "phase=serial" in text_of(_ser_log))
    if _logs is not None:
        shutil.rmtree(_logs, ignore_errors=True)

    r1 = run(["bash", "tools/run_engine_tests.sh", "full"], cwd=str(_eng), env=_suite_env())
    check("(b) SLCN_TEST_JOBS 不设 → 旧串行档（无并行行、红 1、退出码 1）",
          r1.returncode == 1 and "并行：" not in (r1.stdout + r1.stderr)
          and re.search(r"汇总：4 套件，红 1 ｜ full 档耗时 [\d.]+ 秒",
                        r1.stdout + r1.stderr) is not None,
          f"rc={r1.returncode}")
    _kept2 = re.search(r"完整日志：([^\s）]+)", r1.stdout + r1.stderr)
    if _kept2:
        shutil.rmtree(_kept2.group(1), ignore_errors=True)
    r2 = run(["bash", "tools/run_engine_tests.sh", "full"], cwd=str(_eng),
             env=_suite_env(SLCN_TEST_JOBS="0"))
    check("(b) 非法 SLCN_TEST_JOBS=0 → 退出 2 且说明原因",
          r2.returncode == 2 and "SLCN_TEST_JOBS 只能是 ≥1 的整数" in (r2.stdout + r2.stderr),
          f"rc={r2.returncode}")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) test_bridge.py：SLCN_BRIDGE_SKIP_GUI=1 只跳 GUI 探针 ──")
_bridge = REPO / "sources-win" / "tests" / "test_bridge.py"
r = run([PY, str(_bridge)], cwd=str(REPO), timeout=300,
        env=clean_env(SLCN_BRIDGE_SKIP_GUI="1", PYTHONPATH=str(ENGINE / "vendor")))
_bout = r.stdout + r.stderr
check("(c) 退出码 0、纯逻辑断言全过（失败 0 项）",
      r.returncode == 0 and "失败 0 项（全部通过）" in _bout, f"rc={r.returncode}")
_m = re.search(r"全部 (\d+) 项断言通过", _bout)
check("(c) 断言数没塌（>300 项；只少了 GUI 那三条）",
      _m is not None and int(_m.group(1)) > 300, _m.group(0) if _m else _bout[-300:])
check("(c) (g) 显式 SKIP 且点名变量",
      "SKIP  (g) SLCN_BRIDGE_SKIP_GUI=1" in _bout)
check("(c) (h) 真窗口那半组显式 SKIP（跨平台夹具仍跑）",
      "SKIP  (h) 真窗口那一步没跑（SLCN_BRIDGE_SKIP_GUI=1）" in _bout
      and "(h) 跨平台夹具" in _bout)
check("(c) (o) 需要 tkinter 的 import 那组显式 SKIP（静态守卫仍跑）",
      "SKIP  (o) dirpush 纯函数断言（SLCN_BRIDGE_SKIP_GUI=1" in _bout
      and "(o) 页面静态守卫" in _bout)
check("(c) 真窗口专属断言一条都没跑",
      "(g) 真开一次窗口：控件全部建成" not in _bout
      and "(h) 默认是**摘要**" not in _bout and "(o) 当期 argv" not in _bout)
_src = _bridge.read_text(encoding="utf-8")
check("(c) 默认分支仍在：只认 `=1`、不设变量时照旧走 `_tk_python()`",
      '_SKIP_GUI = os.environ.get("SLCN_BRIDGE_SKIP_GUI") == "1"' in _src
      and "tkpy = _tk_python()" in _src)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) ci.yml：3 路并行 / AUTO 守卫显式 SKIP / 桥梁不设 SKIP_GUI ──")
_ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
check("(d) engine 全部套件步骤带 SLCN_TEST_JOBS: \"3\"",
      re.search(r'SLCN_TEST_JOBS: "3"[\s\S]{0,400}?run_engine_tests\.sh full', _ci)
      is not None)
check("(d) AUTO 守卫缺本机解释器时显式 SKIP 并退出 0",
      "if [ ! -x /opt/homebrew/bin/python3.14 ]" in _ci
      and "exit 0" in _ci.split("if [ ! -x /opt/homebrew/bin/python3.14 ]", 1)[1][:400]
      and "SKIP：没有 /opt/homebrew/bin/python3.14" in _ci)
check("(d) swift 桥梁步骤不设 SLCN_BRIDGE_SKIP_GUI（spec S5：CI 不设，走默认分支）",
      "SLCN_BRIDGE_SKIP_GUI" not in _ci)
check("(d) 桥梁步骤仍按默认行为真跑 `python tests/test_bridge.py`",
      re.search(r"Windows 外壳桥梁测试[\s\S]{0,300}?run: python tests/test_bridge\.py", _ci)
      is not None)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) 笔记仓 tools/run_gates.py（S1）：--help / lint 解析 / 依赖留痕 ──")
_notes = REPO.parent / "EditToolBox-notes"
_rg = _notes / "tools" / "run_gates.py"
if not _rg.is_file():
    print(f"SKIP  (e) 没有 {_rg}（笔记仓未检出本工具；门禁机上必须全跑）")
else:
    r = run([PY, str(_rg), "--help"], cwd=str(REPO),
            env=clean_env(PYTHONPATH=str(ENGINE / "vendor")))
    check("(e) run_gates.py --help 退出 0 且打印用法",
          r.returncode == 0 and "run_gates" in (r.stdout + r.stderr),
          f"rc={r.returncode} {(r.stdout + r.stderr)[-300:]}")
    _rgsrc = _rg.read_text(encoding="utf-8")
    check("(e) 默认并行 4 门、--reuse-full-post 默认关",
          '"--jobs", type=int, default=4' in _rgsrc
          and '"--reuse-full-post", action="store_true"' in _rgsrc)

    # P2 修复回归：直接 import 被编排器使用的小函数，钉住「解析失败不得 PASS」与依赖留痕。
    _spec = importlib.util.spec_from_file_location("b309_run_gates", _rg)
    _rgmod = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _rgmod   # dataclass 解析注解时需要模块已注册
    _spec.loader.exec_module(_rgmod)
    _m_empty = _rgmod.parse_lint_metrics("")
    check("(e) lint 解析：缺失文本时 errors=-1（不会被当成 0）",
          _m_empty["errors"] == -1, str(_m_empty))
    _exit_unknown, _note_unknown = _rgmod.lint_verdict(0, _m_empty)
    check("(e) lint errors=-1 → required 门 FAIL 且带 UNKNOWN note（不静默 PASS）",
          _exit_unknown == 1 and "UNKNOWN" in _note_unknown,
          f"exit={_exit_unknown} note={_note_unknown}")
    check("(e) lint errors=0 且命令 rc=0 → 不误杀",
          _rgmod.lint_verdict(0, {"errors": 0})[0] == 0)
    check("(e) 依赖被 --skip：依赖方标 dep_skipped（不是静默 SKIP/exit0）",
          _rgmod.dep_warnings(["full_pre", "full_post"], {"full_pre": "--skip"})
          == {"full_post": ("dep_skipped", "full_pre")})
    check("(e) 依赖未被 --only 选中：只 WARN standalone，允许独立真跑",
          _rgmod.dep_warnings(["full_post"], {})
          == {"full_post": ("standalone", "full_pre")})

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if _fails:
    print(f"失败 {len(_fails)} 项：{_fails}")
    sys.exit(1)
print(f"全部 {_passed} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
