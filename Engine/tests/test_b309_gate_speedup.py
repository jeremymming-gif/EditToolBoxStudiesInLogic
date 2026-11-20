#!/usr/bin/env python3
"""B-309 门禁/CI/测试执行提速：契约 + 反例（全部 hermetic）。

钉住（不读真实稿件库、不写仓库 out/、不开真窗口；跑一次约 15 秒）：
  (a) `corpus.py --out DIR`：输出根可换、`_worker_init` 把 OUT 带进子进程（macOS 3.14
      spawn 下父进程改全局无效）、两遍 CSV 逐字节一致；不传 --out 时默认仍是
      `Engine/out/corpus`，仓库里的 canonical `out/corpus/` 一个字节都不许动。
  (b) `run_engine_tests.sh` 的 `SLCN_TEST_JOBS` 与单套超时：并行红套件**串行复核**，
      抖动（并行红/串行绿）不计红；超时清理整棵进程树并继续后续套件；退出码 =
      复核后的红套数；manifest 全覆盖；串行 allowlist 照跑照计；未设 jobs 时走旧串行。
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
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


def native(path_str: str) -> Path:
    """把 Git Bash 打印的 MSYS 路径转成当前平台的原生路径。

    `run_engine_tests.sh` 用 `${TMPDIR:-/tmp}` 建日志目录，Git Bash 打印的是
    `/tmp/slcn_engine_tests.XXXX`；Windows 上 `Path("/tmp/...")` 会变成「当前盘根下的
    \\tmp\\...」判非目录。用 bash 自带的 `cygpath -w` 转换（POSIX 上原样返回）。
    见 B-304/T5、B300-09。
    """
    if os.name != "nt":
        return Path(path_str)
    r = run(["bash", "-c", 'cygpath -w "$1"', "_", path_str])
    out = (r.stdout or "").strip()
    return Path(out) if out else Path(path_str)


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
                   env=clean_env(PYTHONPATH=str(ENGINE / "vendor"),
                                 PYTHONUTF8="1"))

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
        "test_ok": (
            'print("\\tPASS counted")\n'
            'print("PASSAGE not counted")\n'
            'print("prefix FAIL not counted")\n'
            'print("FAILURE not counted")\n'),
        "test_red": (
            'print(" PASS reached")\n'
            'print("  FAIL intended")\n'
            'print("prefix PASS not counted")\n'
            'print("FAILURE not counted")\n'
            'raise SystemExit(1)\n'),
        # 只在并行档红：用来验证「并行红 → 串行复核绿 = 抖动，不计红」
        "test_flaky": (
            'import os\n'
            'p = os.environ.get("SLCN_TEST_PHASE", "")\n'
            'print("phase=%s" % p)\n'
            'print(" PASS parallel" if p == "parallel" else " PASS serial-final")\n'
            'if p == "parallel":\n'
            '    print(" FAIL transient")\n'
            '    raise SystemExit(1)\n'),
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
          r.returncode == 1, f"rc={r.returncode} {out[-800:]}")
    check("(b) 汇总行格式不变（`汇总：N 套件，红 R ｜ full 档耗时 S 秒`）",
          re.search(r"汇总：4 套件，红 1 ｜ full 档耗时 [\d.]+ 秒", out) is not None,
          f"{[ln for ln in out.splitlines() if ln.startswith('汇总')]}\n{out[-800:]}")
    check("(b) 并行红/串行复核红/抖动分别报告",
          "并行红 2 套 → 串行复核红 1 套（抖动 1 套）" in out, out[-500:])
    check("(b) 四套都跑了、没有静默跳过（PASS/FAIL/⚠️ 各一行）",
          len(re.findall(r"^  (?:PASS|FAIL|⚠️) ", out, re.M)) == 4
          and "PASS test_ok" in out and "FAIL test_red" in out
          and "⚠️ test_flaky" in out and "PASS test_shared_paths" in out,
          out[-800:])
    _kept = re.search(r"完整日志：([^\s）]+)", out)
    _logs = native(_kept.group(1)) if _kept else None
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
        shutil.rmtree(native(_kept2.group(1)), ignore_errors=True)
    r2 = run(["bash", "tools/run_engine_tests.sh", "full"], cwd=str(_eng),
             env=_suite_env(SLCN_TEST_JOBS="0"))
    check("(b) 非法 SLCN_TEST_JOBS=0 → 退出 2 且说明原因",
          r2.returncode == 2 and "SLCN_TEST_JOBS 只能是 ≥1 的整数" in (r2.stdout + r2.stderr),
          f"rc={r2.returncode}")
    r_timeout_invalid = run(
        ["bash", "tools/run_engine_tests.sh", "full"], cwd=str(_eng),
        env=_suite_env(SLCN_TEST_TIMEOUT_SECONDS="0"))
    check("(b) 默认单套超时为 720 秒且 0 被拒绝",
          'SLCN_TEST_TIMEOUT_SECONDS:-720' in text_of(_eng / "tools" / "run_engine_tests.sh")
          and r_timeout_invalid.returncode == 2
          and "SLCN_TEST_TIMEOUT_SECONDS 只能是 1 到 4000000 的整数"
          in (r_timeout_invalid.stdout + r_timeout_invalid.stderr),
          f"rc={r_timeout_invalid.returncode} {r_timeout_invalid.stdout + r_timeout_invalid.stderr}")

    _manifest_path = _tmp / "full-result.json"
    r_manifest = run(["bash", "tools/run_engine_tests.sh", "full"], cwd=str(_eng),
                     env=_suite_env(SLCN_TEST_JOBS="2",
                                    SLCN_TEST_SERIAL_ALLOWLIST="test_shared_paths",
                                    SLCN_TEST_RESULT_MANIFEST=str(_manifest_path),
                                    SLCN_TEST_INPUTS_SHA256="b" * 64))
    try:
        _manifest = json.loads(_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as _e:
        _manifest = {}
        _manifest_error = str(_e)
    else:
        _manifest_error = ""
    _rows = _manifest.get("suites", [])
    _by_suite = {row.get("name"): row for row in _rows if isinstance(row, dict)}
    check("(b) manifest schema/full/identity 正确",
          _manifest.get("schema") == 1 and _manifest.get("mode") == "full"
          and _manifest.get("identity_sha256") == "b" * 64,
          _manifest_error or str(_manifest))
    check("(b) manifest 对当前 full suites 恰好覆盖一次",
          len(_rows) == len(_suites)
          and {row.get("name") for row in _rows if isinstance(row, dict)} == set(_suites)
          and len(_by_suite) == len(_suites),
          str([row.get("name") for row in _rows if isinstance(row, dict)]))
    _ok_row = _by_suite.get("test_ok", {})
    _red_row = _by_suite.get("test_red", {})
    _flaky_row = _by_suite.get("test_flaky", {})
    check("(b) manifest 使用锚定 PASS/FAIL 计数并忽略近似行",
          (_ok_row.get("passes"), _ok_row.get("fails")) == (1, 0)
          and (_red_row.get("passes"), _red_row.get("fails")) == (1, 1),
          f"ok={_ok_row} red={_red_row}")
    check("(b) manifest 记录退出码、状态和有限的非负耗时",
          _ok_row.get("return_code") == 0 and _ok_row.get("status") is None
          and _red_row.get("return_code") == 1 and _red_row.get("status") == "rc=1 FAIL 1"
          and all(isinstance(row.get("duration_seconds"), (int, float))
                  and math.isfinite(row["duration_seconds"])
                  and row["duration_seconds"] >= 0 for row in _rows if isinstance(row, dict)),
          f"ok={_ok_row} red={_red_row}")
    check("(b) 并行红/串行绿的 manifest 采用最终串行结果",
          _flaky_row.get("passes") == 1 and _flaky_row.get("fails") == 0
          and _flaky_row.get("return_code") == 0 and _flaky_row.get("status") is None,
          str(_flaky_row))
    _manifest_kept = re.search(r"完整日志：([^\s）]+)", r_manifest.stdout + r_manifest.stderr)
    if _manifest_kept:
        shutil.rmtree(native(_manifest_kept.group(1)), ignore_errors=True)
    _quick_match = re.search(r"^QUICK=\(([\s\S]*?)^\)",
                             text_of(_eng / "tools" / "run_engine_tests.sh"), re.M)
    _quick_names = (re.findall(r"^\s+(test_[A-Za-z0-9_]+)\s+#", _quick_match.group(1), re.M)
                    if _quick_match else [])
    for _name in _quick_names:
        (_eng / "tests" / f"{_name}.py").write_text('print(" PASS quick fixture")\n',
                                                      encoding="utf-8")
    _quick_manifest = _tmp / "quick-result.json"
    r_quick_manifest = run(["bash", "tools/run_engine_tests.sh", "quick"], cwd=str(_eng),
                           env=_suite_env(SLCN_TEST_RESULT_MANIFEST=str(_quick_manifest)))
    check("(b) quick manifest 请求会警告但不写清单",
          bool(_quick_names) and r_quick_manifest.returncode == 0
          and "仅支持 full 档" in (r_quick_manifest.stdout + r_quick_manifest.stderr)
          and not _quick_manifest.exists(),
          f"quick suites={len(_quick_names)} rc={r_quick_manifest.returncode} "
          f"output={(r_quick_manifest.stdout + r_quick_manifest.stderr)[-300:]}")

    print("\n── (b) 单套超时：串行 / allowlist / 并行 / 串行复核清理进程树 ──")
    _timeout_eng = _tmp / "TimeoutEngine"
    (_timeout_eng / "tools").mkdir(parents=True)
    _timeout_tests = _timeout_eng / "tests"
    _timeout_tests.mkdir()
    shutil.copy(ENGINE / "tools" / "run_engine_tests.sh",
                _timeout_eng / "tools" / "run_engine_tests.sh")

    def _install_timeout_suites(tag: str):
        _pid_file = _tmp / f"{tag}.child.pid"
        _parent_file = _tmp / f"{tag}.suite.pid"
        _pulse_file = _tmp / f"{tag}.pulse"
        _child = (
            "import os, signal, time\n"
            "from pathlib import Path\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"pid_path = Path({str(_pid_file)!r})\n"
            f"pulse_path = Path({str(_pulse_file)!r})\n"
            "pid_path.write_text(str(os.getpid()), encoding='ascii')\n"
            "i = 0\n"
            "while True:\n"
            "    pulse_path.write_text(str(i), encoding='ascii')\n"
            "    i += 1\n"
            "    time.sleep(0.03)\n"
        )
        _hanging_suite = (
            "import os, subprocess, sys, time\n"
            "from pathlib import Path\n"
            f"Path({str(_parent_file)!r}).write_text(str(os.getpid()), encoding='ascii')\n"
            f"child = {_child!r}\n"
            "subprocess.Popen([sys.executable, '-u', '-c', child])\n"
            f"pid_path = Path({str(_pid_file)!r})\n"
            "deadline = time.monotonic() + 10\n"
            "while not pid_path.exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.01)\n"
            "print('CHILD_PID=' + (pid_path.read_text(encoding='ascii') if pid_path.exists() else 'missing'), flush=True)\n"
            "while True:\n"
            "    time.sleep(1)\n"
        )
        (_timeout_tests / "test_a_timeout.py").write_text(_hanging_suite, encoding="utf-8")
        (_timeout_tests / "test_b_after_timeout.py").write_text(
            f"from pathlib import Path\nPath({str(_tmp / (tag + '.continued'))!r}).write_text('ran', encoding='ascii')\n"
            "import os\nprint('phase=' + os.environ.get('SLCN_TEST_PHASE', 'serial'))\n"
            "print(' PASS after timeout')\n",
            encoding="utf-8")
        return _pid_file, _parent_file, _pulse_file, _tmp / f"{tag}.continued"

    def _active_process(pid: int) -> bool:
        if os.name == "nt":
            result = run(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
            return re.search(rf"\b{pid}\b", result.stdout + result.stderr) is not None
        result = run(["ps", "-o", "stat=", "-p", str(pid)])
        state = result.stdout.strip()
        return bool(state) and not state.startswith("Z")

    def _tree_is_stopped(pid_file: Path, parent_file: Path, pulse_file: Path) -> bool:
        if not pid_file.is_file() or not parent_file.is_file() or not pulse_file.is_file():
            return False
        try:
            child_pid = int(pid_file.read_text(encoding="ascii"))
            parent_pid = int(parent_file.read_text(encoding="ascii"))
        except (OSError, ValueError):
            return False
        for _ in range(20):
            first = text_of(pulse_file)
            time.sleep(0.1)
            second = text_of(pulse_file)
            if first == second:
                return not _active_process(child_pid) and not _active_process(parent_pid)
        return False

    _serial_pid, _serial_parent, _serial_pulse, _serial_continued = _install_timeout_suites("serial-timeout")
    _serial_manifest_path = _tmp / "serial-timeout-result.json"
    r_serial_timeout = run(
        ["bash", "tools/run_engine_tests.sh", "full"], cwd=str(_timeout_eng),
        env=_suite_env(SLCN_TEST_TIMEOUT_SECONDS="2",
                       SLCN_TEST_RESULT_MANIFEST=str(_serial_manifest_path),
                       SLCN_TEST_INPUTS_SHA256="c" * 64), timeout=12)
    _serial_out = r_serial_timeout.stdout + r_serial_timeout.stderr
    check("(b) 默认串行超时计 1 红、退出 1，并继续运行后续套件",
          r_serial_timeout.returncode == 1
          and "FAIL test_a_timeout" in _serial_out and "PASS test_b_after_timeout" in _serial_out
          and _serial_continued.is_file()
          and re.search(r"汇总：2 套件，红 1 ｜ full 档耗时 [\d.]+ 秒", _serial_out) is not None,
          f"rc={r_serial_timeout.returncode} {_serial_out[-500:]}")
    _serial_log_match = re.search(r"完整日志：([^\s）]+)", _serial_out)
    _serial_logs = native(_serial_log_match.group(1)) if _serial_log_match else None
    _serial_timeout_log = (_serial_logs / "test_a_timeout.log") if _serial_logs else Path("/nonexistent")
    _serial_timeout_rc = (_serial_logs / "test_a_timeout.log.rc") if _serial_logs else Path("/nonexistent")
    _serial_timeout_duration = (_serial_logs / "test_a_timeout.log.duration") if _serial_logs else Path("/nonexistent")
    check("(b) 串行超时日志标记 TIMEOUT，退出码 124 和耗时落盘",
          "TIMEOUT:" in text_of(_serial_timeout_log)
          and text_of(_serial_timeout_rc).strip() == "124"
          and _serial_timeout_duration.is_file(),
          str(_serial_timeout_log))
    try:
        _serial_timeout_manifest = json.loads(_serial_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as _e:
        _serial_timeout_manifest = {}
        _serial_timeout_manifest_error = str(_e)
    else:
        _serial_timeout_manifest_error = ""
    _serial_timeout_rows = {row.get("name"): row for row in _serial_timeout_manifest.get("suites", [])}
    check("(b) 串行超时 manifest 覆盖两套且 timeout 状态/rc 明确",
          set(_serial_timeout_rows) == {"test_a_timeout", "test_b_after_timeout"}
          and _serial_timeout_rows.get("test_a_timeout", {}).get("return_code") == 124
          and _serial_timeout_rows.get("test_a_timeout", {}).get("status") == "timeout"
          and _serial_timeout_rows.get("test_b_after_timeout", {}).get("return_code") == 0,
          _serial_timeout_manifest_error or str(_serial_timeout_manifest))
    check("(b) 默认串行超时后没有存活的 suite/child 进程",
          _tree_is_stopped(_serial_pid, _serial_parent, _serial_pulse))
    if _serial_logs is not None:
        shutil.rmtree(_serial_logs, ignore_errors=True)

    _allow_pid, _allow_parent, _allow_pulse, _allow_continued = _install_timeout_suites("allow-timeout")
    r_allow_timeout = run(
        ["bash", "tools/run_engine_tests.sh", "full"], cwd=str(_timeout_eng),
        env=_suite_env(SLCN_TEST_TIMEOUT_SECONDS="2", SLCN_TEST_JOBS="2",
                       SLCN_TEST_SERIAL_ALLOWLIST="test_a_timeout"), timeout=12)
    _allow_out = r_allow_timeout.stdout + r_allow_timeout.stderr
    _allow_log_match = re.search(r"完整日志：([^\s）]+)", _allow_out)
    _allow_logs = native(_allow_log_match.group(1)) if _allow_log_match else None
    _allow_timeout_log = (_allow_logs / "test_a_timeout.allow.log") if _allow_logs else Path("/nonexistent")
    check("(b) serial allowlist 超时计红且并行中的后续套件照跑",
          r_allow_timeout.returncode == 1 and "FAIL test_a_timeout" in _allow_out
          and "PASS test_b_after_timeout" in _allow_out and _allow_continued.is_file()
          and "TIMEOUT:" in text_of(_allow_timeout_log),
          f"rc={r_allow_timeout.returncode} {_allow_out[-500:]}")
    check("(b) serial allowlist 超时后没有存活的 suite/child 进程",
          _tree_is_stopped(_allow_pid, _allow_parent, _allow_pulse))
    if _allow_logs is not None:
        shutil.rmtree(_allow_logs, ignore_errors=True)

    _par_pid, _par_parent, _par_pulse, _par_continued = _install_timeout_suites("parallel-timeout")
    _parallel_manifest_path = _tmp / "parallel-timeout-result.json"
    _parallel_started = time.monotonic()
    r_parallel_timeout = run(
        ["bash", "tools/run_engine_tests.sh", "full"], cwd=str(_timeout_eng),
        env=_suite_env(SLCN_TEST_TIMEOUT_SECONDS="2", SLCN_TEST_JOBS="2",
                       SLCN_TEST_RESULT_MANIFEST=str(_parallel_manifest_path),
                       SLCN_TEST_INPUTS_SHA256="d" * 64), timeout=12)
    _parallel_elapsed = time.monotonic() - _parallel_started
    _parallel_out = r_parallel_timeout.stdout + r_parallel_timeout.stderr
    _parallel_log_match = re.search(r"完整日志：([^\s）]+)", _parallel_out)
    _parallel_logs = native(_parallel_log_match.group(1)) if _parallel_log_match else None
    _parallel_log = (_parallel_logs / "test_a_timeout.par.log") if _parallel_logs else Path("/nonexistent")
    _recheck_log = (_parallel_logs / "test_a_timeout.serial.log") if _parallel_logs else Path("/nonexistent")
    check("(b) 并行超时被串行复核；复核超时也返回红并继续完成汇总",
          r_parallel_timeout.returncode == 1 and _parallel_elapsed < 8
          and "↺ 串行复核 test_a_timeout（并行红）" in _parallel_out
          and "并行红 1 套 → 串行复核红 1 套（抖动 0 套）" in _parallel_out
          and "PASS test_b_after_timeout" in _parallel_out and _par_continued.is_file()
          and re.search(r"汇总：2 套件，红 1 ｜ full 档耗时 [\d.]+ 秒", _parallel_out) is not None,
          f"rc={r_parallel_timeout.returncode} elapsed={_parallel_elapsed:.2f}s {_parallel_out[-700:]}")
    check("(b) 并行和串行复核日志都记录 TIMEOUT/124",
          "TIMEOUT:" in text_of(_parallel_log) and "TIMEOUT:" in text_of(_recheck_log)
          and text_of(Path(str(_parallel_log) + ".rc")).strip() == "124"
          and text_of(Path(str(_recheck_log) + ".rc")).strip() == "124")
    try:
        _parallel_timeout_manifest = json.loads(_parallel_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as _e:
        _parallel_timeout_manifest = {}
        _parallel_timeout_manifest_error = str(_e)
    else:
        _parallel_timeout_manifest_error = ""
    _parallel_timeout_rows = {row.get("name"): row for row in _parallel_timeout_manifest.get("suites", [])}
    check("(b) 并行超时 manifest 仍覆盖每套一次并采用超时复核结果",
          set(_parallel_timeout_rows) == {"test_a_timeout", "test_b_after_timeout"}
          and _parallel_timeout_rows.get("test_a_timeout", {}).get("return_code") == 124
          and _parallel_timeout_rows.get("test_a_timeout", {}).get("status") == "timeout"
          and _parallel_timeout_rows.get("test_b_after_timeout", {}).get("return_code") == 0,
          _parallel_timeout_manifest_error or str(_parallel_timeout_manifest))
    check("(b) 并行 timeout + timed-out recheck 后没有存活的 suite/child 进程",
          _tree_is_stopped(_par_pid, _par_parent, _par_pulse))
    if _parallel_logs is not None:
        shutil.rmtree(_parallel_logs, ignore_errors=True)
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) Flet 桥契约：engine_bridge.ENTRIES 与 engine_main 逐项一致 ──")
# 旧 (c) 真跑 sources-win/tests/test_bridge.py（Tk 外壳的桥）；批108 删旧壳后那条路没了。
# 等价的「分发器不漂」契约改钉静态事实源：Flet 桥的 ENTRIES 必须与 engine_main.ENTRIES
# **逐项同序**（桥靠它决定把哪个入口转给 sidecar），且桥里仍有 sidecar 定位/启动逻辑。
# 不 import flet（Engine 套件不依赖 GUI 运行时），只读源码 —— 漏一个入口的后果与旧壳同型：
# 那个页在用户机器上「❌ 不认识的入口」退出 2。
_bridge = REPO / "sources-flet" / "app" / "services" / "engine_bridge.py"
check("(c) Flet 桥源文件在仓内", _bridge.is_file(), str(_bridge))
_em_entries: tuple[str, ...] = ()
try:
    _spec_em = importlib.util.spec_from_file_location("_engine_main_c", ENGINE / "engine_main.py")
    _em_mod = importlib.util.module_from_spec(_spec_em)
    _spec_em.loader.exec_module(_em_mod)
    _em_entries = tuple(_em_mod.ENTRIES)
except Exception as _e:  # noqa: BLE001
    print(f"FAIL  (c) 读 engine_main.ENTRIES 失败：{_e}")
if _bridge.is_file():
    _src = _bridge.read_text(encoding="utf-8")
    _bm = re.search(r"ENTRIES\s*=\s*\((.*?)\)", _src, re.S)
    _bridge_entries = tuple(re.findall(r'"([^"]+\.py)"', _bm.group(1) if _bm else ""))
    check("(c) Flet 桥 ENTRIES 与 engine_main.ENTRIES 逐项同序"
          "（桥/分发器两份表漂一个 = 那个页在用户机器上「不认识的入口」退出 2）",
          bool(_bridge_entries) and _bridge_entries == _em_entries,
          f"bridge={_bridge_entries} ／ engine_main={_em_entries}")
    check("(c) 桥仍负责 sidecar 定位/启动（冻结 exe 是用户机器上唯一的引擎）",
          re.search(r"def [a-z_]*(?:sidecar|engine_exe)[a-z_]*\(", _src) is not None
          and "SLCNEngine" in _src,
          "未找到 sidecar 定位函数或 SLCNEngine 字样")

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) ci.yml：3 路并行 / AUTO 守卫显式 SKIP / 旧壳下线 / Windows 轻量覆盖 / build-flet 手动 ──")
_ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
check("(d) engine 全部套件步骤带 SLCN_TEST_JOBS: \"3\"",
      re.search(r'SLCN_TEST_JOBS: "3"[\s\S]{0,400}?run_engine_tests\.sh full', _ci)
      is not None)
check("(d) AUTO 守卫缺本机解释器时显式 SKIP 并退出 0",
      "if [ ! -x /opt/homebrew/bin/python3.14 ]" in _ci
      and "exit 0" in _ci.split("if [ ! -x /opt/homebrew/bin/python3.14 ]", 1)[1][:400]
      and "SKIP：没有 /opt/homebrew/bin/python3.14" in _ci)
# 2026-11-01 用户拍板 CI 去冗余：SwiftUI/Tk 旧壳冻结 → 旧壳 job 下线；
# Windows 侧改为 Flet 壳测试 + 引擎稳定子集（不依赖 TeX/LibreOffice/稿件库）。
check("(d) 旧壳 job 已下线（ci.yml 不再跑 Swift 测试台 / Windows 外壳桥梁）",
      "run_swift_tests.sh" not in _ci and "test_bridge.py" not in _ci)
check("(d) Windows 轻量 job 存在：Flet 壳测试 + 引擎稳定子集",
      "Windows：Flet 壳测试 + 引擎稳定子集" in _ci
      and "runs-on: windows-latest" in _ci
      and "python -m pytest sources-flet/tests -q" in _ci
      and "tests/test_paths.py" in _ci and "tests/test_proofcheck.py" in _ci
      and "tests/test_b285_diagnose.py" not in _ci)  # 环境假设红：不入干净 runner
check("(d) build-flet 只在手动触发（发版前验证），不随 push 打包",
      "workflow_dispatch:" in (REPO / ".github/workflows/build-flet.yml").read_text(encoding="utf-8")
      and "push:" not in (REPO / ".github/workflows/build-flet.yml").read_text(encoding="utf-8").split("permissions:", 1)[0])

# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) 笔记仓 tools/run_gates.py（S1）：--help / lint 解析 / 依赖留痕 ──")
# 笔记仓定位走房规 `slcn.paths.notes_root()`（判据：目录里有 `cases/`；`SLCN_NOTES` 优先）。
# 旧代码写的是 `REPO.parent / "EditToolBox-notes"` —— 那是**主检出**的兄弟目录；在 worktree
# 里 `REPO` 是 `<…>/worktrees/<名字>/EditToolBoxStudiesInLogic`，`REPO.parent` 变成
# `<…>/worktrees/<名字>`，于是明明存在的 `tools/run_gates.py` 被判成「没有」，静默 SKIP。
# 房规内其他套件（如 `tests/test_batch59_coop_fulltext.py:238`）都走 `paths.notes_root()`。
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
try:
    from slcn import paths as _paths
    _notes_dir = _paths.notes_root()
    _notes_why = _paths.describe_notes()
except Exception as _e:  # noqa: BLE001  定位器本身不可用也不能假装绿
    _notes_dir, _notes_why = None, f"slcn.paths 不可用：{_e}"
_rg = (Path(_notes_dir) / "tools" / "run_gates.py") if _notes_dir else None
if _rg is None or not _rg.is_file():
    # 两种情形要分开说：笔记仓找不到（合理 SKIP）vs 笔记仓在、工具不在（那是笔记仓的问题）。
    print(f"SKIP  (e) 笔记仓 tools/run_gates.py（{_notes_why}；门禁机上必须全跑）")
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
