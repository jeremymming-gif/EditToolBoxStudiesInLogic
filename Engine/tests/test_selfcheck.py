#!/usr/bin/env python3.14
r"""第二十二批（第 122 条）工具的回归测试：案例库治理 + 引擎自查。

覆盖：
  ① `tools/gen_case_index.py`：日期列、状态分类计数、`DIGEST.md` 生成与 `--check`；
  ② `tools/case_audit.py`：编号连续 / 状态目标存在 / INDEX 过期 三类错误 + 相似度警告；
  ③ `Engine/tools/selfcheck.py`：嵌套保护、纯静态检查端到端、`case_audit` 接线；
  ④ 第四十四批③a（O9）反例：App 包内一致性已从 AUTO 移出 ——
     ③ 用 `--skip-app` 拆掉「test_selfcheck × App 新鲜度」循环依赖；
     `gen_docs.py --cmp-app`（build.sh 末尾守卫）能抓「包内过期」；AUTO 数字不变。

全部离线、不需要稿件库；平台中立（Windows/mac 的路径分隔符都在断言里归一到 `/`）。
"""
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
_REPO = os.path.dirname(_ENGINE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor"), os.path.join(_ENGINE, "tools"),
           os.path.join(_REPO, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FAILS = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="slcn_case_tools_")


def _flat(text) -> str:
    """把多行详情压成一行：子进程输出里可能有以 `FAIL`/`PASS` 开头的行，
    若原样打印会被 `gen_docs.py` / 套件统计误当成断言行（第二十二批实测）。"""
    return " ⏎ ".join(str(text).replace("\r", "").splitlines())


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + _flat(detail) if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPS.append(name)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run(cmd, env=None):
    e = dict(os.environ)
    e.update(env or {})
    e["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=e, cwd=_REPO)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


GEN = os.path.join(_REPO, "tools", "gen_case_index.py")
AUDIT = os.path.join(_REPO, "tools", "case_audit.py")
SELF = os.path.join(_ENGINE, "tools", "selfcheck.py")

CASE_A = """# 案例法（测试用）

### 1. 第一条（2026-10-17，第二十二批）

> 状态：已发布：v1.1.1

**背景**：测试条目一。这里是正文，包含「测试」关键词。

### 2. 第二条（2026-10-17，第二十二批）

**修复**：测试条目二。正文里也有解析与门禁相关的词。
"""


def new_cases_dir(name):
    d = os.path.join(TMP, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    write(os.path.join(d, "01-30.md"), CASE_A)
    return d


# ── ① 索引生成：日期 / 状态分类 / DIGEST ──────────────────────────────────
d1 = new_cases_dir("gen")
rc, out = run([sys.executable, GEN, "--cases", d1])
check("① gen_case_index 生成成功", rc == 0, out[-200:])
idx = open(os.path.join(d1, "INDEX.md"), encoding="utf-8").read()
check("① INDEX 含日期列与日期值", "2026-10-17" in idx and "| 条 | 日期 |" in idx)
check("① INDEX 状态分类计数生效（已发布 1 条）", "已发布 1 条" in idx, idx.splitlines()[3])
dig = os.path.join(d1, "DIGEST.md")
check("① DIGEST.md 已生成", os.path.isfile(dig))
if os.path.isfile(dig):
    dtext = open(dig, encoding="utf-8").read()
    check("① DIGEST 按主题分组（含「测试」组）", "## 测试" in dtext, dtext[:200])
check("① --check 在索引新鲜时通过",
      run([sys.executable, GEN, "--cases", d1, "--check"])[0] == 0)

# ── ② case_audit：好库 / 编号缺号 / 状态目标失效 / 索引过期 / 相似度警告 ──
rc, out = run([sys.executable, AUDIT, "--cases", d1, "--check"])
check("② 正常案例库审过（rc=0）", rc == 0, out[-300:])
check("② 正常库报告「无错误」", "无错误" in out)

d2 = new_cases_dir("badnum")
write(os.path.join(d2, "01-30.md"), CASE_A.replace("### 2.", "### 9."))
rc, out = run([sys.executable, AUDIT, "--cases", d2, "--check"])
check("② 编号缺号 → rc=1 且指出缺号", rc == 1 and "缺号" in out, out[-400:])

d3 = new_cases_dir("badstatus")
write(os.path.join(d3, "01-30.md"),
      CASE_A.replace("> 状态：已发布：v1.1.1", "> 状态：已被第 99 条取代"))
rc, out = run([sys.executable, AUDIT, "--cases", d3, "--check"])
check("② 状态指向不存在的条目 → rc=1", rc == 1 and "第 99 条" in out, out[-400:])

d4 = new_cases_dir("stale")
run([sys.executable, GEN, "--cases", d4])
write(os.path.join(d4, "01-30.md"),
      CASE_A.replace("第一条", "第一条（标题已改）"))
rc, out = run([sys.executable, AUDIT, "--cases", d4, "--check"])
check("② INDEX 过期 → rc=1", rc == 1 and "过期" in out, out[-300:])

d5 = new_cases_dir("sim")
dup_body = "**修复**：这是一段完全相同的正文。" * 3
write(os.path.join(d5, "01-30.md"),
      CASE_A + "\n### 3. 第三条（2026-10-17，第二十二批）\n\n" + dup_body
      + "\n### 4. 第四条（2026-10-17，第二十二批）\n\n" + dup_body)
run([sys.executable, GEN, "--cases", d5])
rc, out = run([sys.executable, AUDIT, "--cases", d5, "--check"])
check("② 相似度只警告、不判错（rc=0 且出现 WARN）", rc == 0 and "相似度" in out, out[-400:])

# ── ③ selfcheck：嵌套保护 + 纯静态端到端 ─────────────────────────────────
import selfcheck as S  # noqa: E402

nums = S._case_numbers(d1)
check("③ selfcheck._case_numbers 解析 1/2", {1, 2} <= nums, sorted(nums)[:6])
check("③ selfcheck 的案例引用正则认「第 12 条」",
      [int(m.group(1)) for m in S.CASE_REF_RE.finditer("见第 12 条与第 0 条")] == [12, 0])

env = dict(os.environ, SLCN_SELFCHECK_NESTED="1", PYTHONIOENCODING="utf-8")
# 第四十四批③a（O9）：**必须**加 `--skip-app`——本套件会在 gen_docs 算 AUTO 时被跑，
# 而「引擎改了、App 还没重建」是批内常态；不加就会把 App 瞬态 FAIL 写进 AUTO，
# 逼出「gen_docs×2 + build×2」循环。包内一致性的守卫在 build.sh 末尾（--cmp-app）。
r = subprocess.run([sys.executable, SELF, "--no-cases", "--skip-app"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
out = (r.stdout or "") + (r.stderr or "")
check("③ selfcheck 纯静态端到端 rc=0", r.returncode == 0, out[-500:])
check("③ selfcheck 输出「自查通过」", "自查通过" in out)
check("③ 嵌套调用时套件/文档被 SKIP（防递归）",
      "SKIP" in out and "防递归" in out)
check("③ App 包内检查被 --skip-app 跳过（O9）", "SKIP  App 包内一致性（--skip-app" in out)

# ── ③b G2：严格请求必须真的透传给下游（不许静默降级成宽松/SKIP）─────────────
# 反例本体（改动前）：`check_docs(strict_tests=True)` 只做 `if not strict_tests` 的**反选**，
# 从不把 `--strict-tests` 传给 test_docs_consistency（它的 `_STRICT_TESTS` 默认 False）；
# 于是 `selfcheck.py --full` 号称严格档，下游照旧非严格，本机还因缺稿件库退化成 SKIP ——
# 结论仍是「失败 0 项 / 自查通过」。这就是「显式严格请求被静默降级成通过」。
# 判据（**纯进程内**，不跑子进程、不需要稿件库、不受机器前提影响 → 零耗时且结果确定）：
#   ① CLI 真的认得 `--strict-tests`（RELEASING.md:160 / AGENTS.md:101 早就这么写了）；
#   ② 严格档拼出的命令**含** `--strict-tests`，且**不含** `--no-test-runs`（下游两者互斥）；
#   ③ 非严格档拼出的命令含 `--no-test-runs` 且不含 `--strict-tests`（低成本行为不退化）；
#   ④ 下游非零 → check_docs 必须让 FAILS 变长（rc 不许被吞）；
#   ⑤ 下游 BLOCKED（严格档缺前提）→ 记成可区分的「BLOCKED」而不是「红」。
# 用打桩替换 `S.run` 抓命令/注入 rc：这样断言的是**契约本身**，不依赖本机有没有稿件库。
_runs: list[list[str]] = []
_run_envs: list[dict | None] = []


def _fake_run(rc, out):
    def _f(cmd, cwd=None, timeout=1800, env_extra=None):
        _runs.append(list(cmd))
        _run_envs.append(dict(env_extra) if env_extra else None)
        return rc, out
    return _f


def _probe_docs(strict, rc, out, env_extra=None):
    """打桩跑一次 check_docs，返回 (拼出的命令, 新增的 FAILS 列表)。

    ⚠️ 必须**吞掉** check_docs 的 stdout：它会打印以 `PASS  `/`FAIL  ` 开头的行，
    而本文件自身也是 gen_docs 统计断言数的套件之一（见 _flat 的同类注释），
    这些行被记账后会把 AUTO 的测试计数行搅脏。
    """
    _runs.clear()
    _run_envs.clear()
    _orig_run, _orig_fails = S.run, list(S.FAILS)
    S.run = _fake_run(rc, out)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            S.check_docs(strict_tests=strict, env_extra=env_extra)
        return _runs[0], [f for f in S.FAILS if f not in _orig_fails], _run_envs[0]
    finally:
        S.run = _orig_run
        S.FAILS[:] = _orig_fails


_cmd_strict, _fails_strict_ok, _ = _probe_docs(True, 0, "失败 0 项（全部通过）")
check("③b 严格档把 --strict-tests 透传给下游（不再只做 --no-test-runs 的反选）",
      "--strict-tests" in _cmd_strict and "--no-test-runs" not in _cmd_strict,
      " ".join(_cmd_strict[-2:]))
check("③b 严格档下游 rc=0 → 不记失败（正常路径不被误伤）", _fails_strict_ok == [],
      str(_fails_strict_ok))
_cmd_lenient, _, _ = _probe_docs(False, 0, "失败 0 项（全部通过）")
check("③b 非严格档仍走 --no-test-runs（quick 的低成本行为不退化）",
      "--no-test-runs" in _cmd_lenient and "--strict-tests" not in _cmd_lenient,
      " ".join(_cmd_lenient[-2:]))
_, _fails_strict_red, _ = _probe_docs(True, 1, "FAIL  (a) AUTO 块过期")
check("③b 严格档下游非零 → check_docs 记失败（rc 不被吞）",
      len(_fails_strict_red) == 1, str(_fails_strict_red))
_, _fails_blocked, _ = _probe_docs(
    True, 2, "BLOCKED  (a) 严格测试校验无法进行：gen_docs --check --strict-tests 返回 BLOCKED")
check("③b 严格档下游 BLOCKED → 记成可区分的「BLOCKED」而不是混作「红」",
      any("BLOCKED" in f for f in _fails_blocked), str(_fails_blocked))
_evidence = {"SLCN_TEST_RESULT_MANIFEST": "/tmp/current-run.json",
             "SLCN_TEST_INPUTS_SHA256": "synthetic-input-id"}
_, _, _passed_env = _probe_docs(True, 0, "失败 0 项（全部通过）", _evidence)
check("③b 严格档把本轮 runner 清单与身份传给文档检查",
      _passed_env == _evidence, str(_passed_env))
_runs.clear()
_run_envs.clear()
_orig_run = S.run
S.run = _fake_run(0, "汇总：1 套件，红 0")
try:
    with contextlib.redirect_stdout(io.StringIO()):
        S.check_tests("full", env_extra=_evidence)
    check("③c full runner 收到本轮清单与身份",
          _run_envs == [_evidence] and _runs[0][-1] == "full", str(_run_envs))
finally:
    S.run = _orig_run
_r_help = subprocess.run([sys.executable, SELF, "--help"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env)
check("③b selfcheck CLI 有 --strict-tests（兑现 RELEASING.md/AGENTS.md 的写法）",
      "--strict-tests" in (_r_help.stdout or ""), (_r_help.stdout or "")[-200:])

import gen_docs as G  # noqa: E402

# ── ③c 严格档不能跳过套件；quick + skip-tests 保持低成本 ───────────────────
_gate_calls = []
_gate_names = ("check_env", "check_imports", "full_test_preflight", "check_tests",
               "check_docs", "scan_code_health", "check_cases", "check_app_bundle",
               "check_deep", "check_corpus")
_saved_gates = {name: getattr(S, name) for name in _gate_names}
for _name in _gate_names:
    setattr(S, _name, lambda *args, _n=_name, **kwargs: _gate_calls.append(_n))
try:
    for _argv in (["--full", "--skip-tests"], ["--strict-tests", "--skip-tests"]):
        _stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(_stderr):
                S.main(list(_argv))
            _arg_rc = None
        except SystemExit as _e:
            _arg_rc = _e.code
        check(f"③c {' '.join(_argv)} 在门禁启动前以 argparse rc=2 拒绝",
              _arg_rc == 2 and "--skip-tests 不能与 --full 或 --strict-tests" in _stderr.getvalue()
              and not _gate_calls, _stderr.getvalue()[-250:] + str(_gate_calls))
finally:
    for _name, _fn in _saved_gates.items():
        setattr(S, _name, _fn)

_saved_run = S.run
_saved_env, _saved_imports = S.check_env, S.check_imports
_saved_health, _saved_cases, _saved_app = S.scan_code_health, S.check_cases, S.check_app_bundle
_saved_state = (list(S.FAILS), list(S.WARNS), list(S.SKIPS))
_nested_value = os.environ.pop("SLCN_SELFCHECK_NESTED", None)
_runs.clear()
_run_envs.clear()
S.run = _fake_run(0, "失败 0 项（全部通过）")
S.check_env = S.check_imports = lambda: None
S.scan_code_health = S.check_cases = S.check_app_bundle = lambda: None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        _quick_skip_rc = S.main(["--quick", "--skip-tests", "--no-cases", "--skip-app"])
    check("③c quick + --skip-tests 仍通过低成本文档检查且不启动引擎 runner",
          _quick_skip_rc == 0
          and any("test_docs_consistency.py" in part and "--no-test-runs" in _runs[i]
                  for i, cmd in enumerate(_runs) for part in cmd)
          and not any("run_engine_tests.sh" in part for cmd in _runs for part in cmd),
          str(_runs))
finally:
    S.run = _saved_run
    S.check_env, S.check_imports = _saved_env, _saved_imports
    S.scan_code_health, S.check_cases, S.check_app_bundle = _saved_health, _saved_cases, _saved_app
    S.FAILS[:], S.WARNS[:], S.SKIPS[:] = _saved_state
    if _nested_value is not None:
        os.environ["SLCN_SELFCHECK_NESTED"] = _nested_value

# ── ③d full 前提预检：缺少前提时应 BLOCKED，且不启动长套件 ────────────────
_orig_preflight = S.full_test_preflight
_orig_run = S.run
_orig_health, _orig_cases, _orig_app = S.scan_code_health, S.check_cases, S.check_app_bundle
_orig_selfcheck_fails, _orig_selfcheck_skips = list(S.FAILS), list(S.SKIPS)
_runs.clear()
S.full_test_preflight = lambda: (False, ["xelatex"], {})


def _blocked_preflight_run(cmd, cwd=None, timeout=1800, env_extra=None):
    _runs.append(list(cmd))
    _run_envs.append(dict(env_extra) if env_extra else None)
    if any("test_docs_consistency.py" in part for part in cmd):
        return 0, "失败 0 项（全部通过）"
    return 99, "unexpected runner"


S.run = _blocked_preflight_run
S.scan_code_health = lambda: None
S.check_cases = lambda: None
S.check_app_bundle = lambda: None
# The suite runner marks this process nested; this case must exercise a top-level --full call.
_nested_value = os.environ.pop("SLCN_SELFCHECK_NESTED", None)
try:
    _stdout = io.StringIO()
    with contextlib.redirect_stdout(_stdout):
        _blocked_rc = S.main(["--full"])
    check("③d1 full 前提缺失时 BLOCKED、不启动 full runner，并保留低成本文档检查",
          _blocked_rc != 0 and "BLOCKED  full 门禁未启动" in _stdout.getvalue()
          and "SKIP  引擎套件（嵌套调用，防递归）" not in _stdout.getvalue()
          and any(any("test_docs_consistency.py" in part for part in cmd)
                  and "--no-test-runs" in cmd for cmd in _runs)
          and not any(any("run_engine_tests.sh" in part for part in cmd) for cmd in _runs),
          _stdout.getvalue()[-300:] + str(_runs))
finally:
    S.full_test_preflight = _orig_preflight
    S.run = _orig_run
    S.scan_code_health, S.check_cases, S.check_app_bundle = _orig_health, _orig_cases, _orig_app
    S.FAILS[:], S.SKIPS[:] = _orig_selfcheck_fails, _orig_selfcheck_skips
    if _nested_value is not None:
        os.environ["SLCN_SELFCHECK_NESTED"] = _nested_value

# ── ③e full 严格运行时前提必须在长套件前检查 ───────────────────────────────
def _full_runtime_inputs():
    return {
        "env_complete": True,
        "env_missing": [],
        "test_runtime_fingerprint": {
            "python": {
                "version": "3.14.1 synthetic",
                "version_info": [3, 14, 1],
                "dependencies": {name: "1.0" for name in G.TEST_DEPENDENCIES},
            },
            "xelatex": {"identity": "/synthetic/xelatex", "version": "XeTeX synthetic"},
            "libreoffice": {"identity": "/synthetic/soffice", "version": "LibreOffice synthetic"},
            "fonts": {"files": 1, "sha256": "a" * 64},
        },
    }


_runtime_cases = []
_missing_dependency = G.TEST_DEPENDENCIES[0]
_runtime_cases.append(("dependency", lambda x: x["test_runtime_fingerprint"]["python"]
                       ["dependencies"].__setitem__(_missing_dependency, None),
                       f"Python 依赖 {_missing_dependency}"))
_runtime_cases.append(("python-version", lambda x: x["test_runtime_fingerprint"]["python"]
                       .__setitem__("version_info", [3, 13, 9]), "Python 需要 ≥3.14"))
_runtime_cases.append(("xelatex-version", lambda x: x["test_runtime_fingerprint"]["xelatex"]
                       .__setitem__("version", None), "xelatex 版本探测"))
_runtime_cases.append(("libreoffice-version", lambda x: x["test_runtime_fingerprint"]["libreoffice"]
                       .__setitem__("version", None), "LibreOffice 版本探测"))
_runtime_cases.append(("fonts", lambda x: x["test_runtime_fingerprint"]["fonts"]
                       .update({"files": 0, "sha256": ""}), "字体清单不可用"))

_saved_run = S.run
_saved_env, _saved_imports = S.check_env, S.check_imports
_saved_health, _saved_cases, _saved_app = S.scan_code_health, S.check_cases, S.check_app_bundle
_saved_state = (list(S.FAILS), list(S.WARNS), list(S.SKIPS))
_nested_value = os.environ.pop("SLCN_SELFCHECK_NESTED", None)
_runs.clear()
_run_envs.clear()


def _blocked_runtime_run(cmd, cwd=None, timeout=1800, env_extra=None):
    _runs.append(list(cmd))
    _run_envs.append(dict(env_extra) if env_extra else None)
    if any("test_docs_consistency.py" in part for part in cmd):
        return 0, "失败 0 项（全部通过）"
    return 99, "unexpected long runner"


S.run = _blocked_runtime_run
S.check_env = S.check_imports = lambda: None
S.scan_code_health = S.check_cases = S.check_app_bundle = lambda: None
_orig_collect_inputs = G.collect_auto_inputs
_collect_calls = []
try:
    for _case_name, _invalidate, _expected in _runtime_cases:
        _inputs = _full_runtime_inputs()
        _invalidate(_inputs)
        _collect_calls.clear()
        G.collect_auto_inputs = lambda _i=_inputs: (_collect_calls.append(_i) or _i)
        _runs.clear()
        _run_envs.clear()
        S.FAILS[:], S.WARNS[:], S.SKIPS[:] = [], [], []
        _stdout = io.StringIO()
        with contextlib.redirect_stdout(_stdout):
            _runtime_rc = S.main(["--full"])
        check(f"③e {_case_name} 缺失时明确 BLOCKED、不启动 full runner，且保留低成本文档检查",
              _runtime_rc != 0 and "BLOCKED  full 门禁未启动" in _stdout.getvalue()
              and _expected in _stdout.getvalue()
              and len(_collect_calls) == 1
              and any("test_docs_consistency.py" in part and "--no-test-runs" in cmd
                      for cmd in _runs for part in cmd)
              and not any("run_engine_tests.sh" in part for cmd in _runs for part in cmd),
              _stdout.getvalue()[-300:] + str(_runs))
finally:
    G.collect_auto_inputs = _orig_collect_inputs
    S.run = _saved_run
    S.check_env, S.check_imports = _saved_env, _saved_imports
    S.scan_code_health, S.check_cases, S.check_app_bundle = _saved_health, _saved_cases, _saved_app
    S.FAILS[:], S.WARNS[:], S.SKIPS[:] = _saved_state
    if _nested_value is not None:
        os.environ["SLCN_SELFCHECK_NESTED"] = _nested_value

# ── ③e full runner 红：cheap docs 仍跑，但 strict 不得重启长套件 ────────────
_red_inputs = _full_runtime_inputs()
_red_runs = []
_red_envs = []
_saved_run = S.run
_saved_env, _saved_imports = S.check_env, S.check_imports
_saved_health, _saved_cases, _saved_app = S.scan_code_health, S.check_cases, S.check_app_bundle
_saved_state = (list(S.FAILS), list(S.WARNS), list(S.SKIPS))
_nested_value = os.environ.pop("SLCN_SELFCHECK_NESTED", None)
_orig_collect_inputs = G.collect_auto_inputs
_orig_identity = G.test_run_identity
G.collect_auto_inputs = lambda: _red_inputs
G.test_run_identity = lambda inputs: "c" * 64


def _red_full_run(cmd, cwd=None, timeout=1800, env_extra=None):
    _red_runs.append(list(cmd))
    _red_envs.append(dict(env_extra) if env_extra else None)
    if any("run_engine_tests.sh" in part for part in cmd):
        return 7, "汇总：197 套件，红 7"
    if any("test_docs_consistency.py" in part for part in cmd):
        return 0, "失败 0 项（全部通过；低成本文档检查）"
    return 99, "unexpected command"


S.run = _red_full_run
S.check_env = S.check_imports = lambda: None
S.scan_code_health = S.check_cases = S.check_app_bundle = lambda: None
try:
    S.FAILS[:], S.WARNS[:], S.SKIPS[:] = [], [], []
    _stdout = io.StringIO()
    with contextlib.redirect_stdout(_stdout):
        _red_rc = S.main(["--full", "--no-cases", "--skip-app"])
    _runner_cmds = [cmd for cmd in _red_runs if any("run_engine_tests.sh" in p for p in cmd)]
    _docs_cmds = [cmd for cmd in _red_runs if any("test_docs_consistency.py" in p for p in cmd)]
    check("③e full runner 红时不重启 strict，改跑低成本 docs 并将严格计数标为 NOT_RUN",
          _red_rc != 0 and len(_runner_cmds) == 1 and len(_docs_cmds) == 1
          and "--no-test-runs" in _docs_cmds[0] and "--strict-tests" not in _docs_cmds[0]
          and "FAIL  引擎套件（full）" in _stdout.getvalue()
          and "NOT_RUN  严格测试计数未执行（full runner 已失败" in _stdout.getvalue(),
          _stdout.getvalue()[-500:] + str(_red_runs))
finally:
    G.collect_auto_inputs = _orig_collect_inputs
    G.test_run_identity = _orig_identity
    S.run = _saved_run
    S.check_env, S.check_imports = _saved_env, _saved_imports
    S.scan_code_health, S.check_cases, S.check_app_bundle = _saved_health, _saved_cases, _saved_app
    S.FAILS[:], S.WARNS[:], S.SKIPS[:] = _saved_state
    if _nested_value is not None:
        os.environ["SLCN_SELFCHECK_NESTED"] = _nested_value

# 成功路径也必须只采集一次；manifest identity 与严格文档检查复用同一份 inputs。
_valid_inputs = _full_runtime_inputs()
_collect_calls = []
_identity_inputs = []
_saved_run = S.run
_saved_env, _saved_imports = S.check_env, S.check_imports
_saved_health, _saved_cases, _saved_app = S.scan_code_health, S.check_cases, S.check_app_bundle
_saved_state = (list(S.FAILS), list(S.WARNS), list(S.SKIPS))
_nested_value = os.environ.pop("SLCN_SELFCHECK_NESTED", None)
_runs.clear()
_run_envs.clear()


def _successful_full_run(cmd, cwd=None, timeout=1800, env_extra=None):
    _runs.append(list(cmd))
    _run_envs.append(dict(env_extra) if env_extra else None)
    if any("run_engine_tests.sh" in part for part in cmd):
        return 0, "汇总：1 套件，红 0"
    if any("test_docs_consistency.py" in part for part in cmd):
        return 0, "失败 0 项（全部通过）"
    return 99, "unexpected command"


_orig_identity = G.test_run_identity
G.collect_auto_inputs = lambda: (_collect_calls.append(_valid_inputs) or _valid_inputs)
G.test_run_identity = lambda inputs: (_identity_inputs.append(inputs) or "b" * 64)
S.run = _successful_full_run
S.check_env = S.check_imports = lambda: None
S.scan_code_health = S.check_cases = S.check_app_bundle = lambda: None
try:
    S.FAILS[:], S.WARNS[:], S.SKIPS[:] = [], [], []
    with contextlib.redirect_stdout(io.StringIO()):
        _full_rc = S.main(["--full", "--no-cases", "--skip-app"])
    _runner_index = next(i for i, cmd in enumerate(_runs)
                         if any("run_engine_tests.sh" in part for part in cmd))
    _docs_index = next(i for i, cmd in enumerate(_runs)
                       if any("test_docs_consistency.py" in part for part in cmd))
    check("③e full 通过时 inputs/runtime 只采集一次，并由 runner 与严格文档检查共同复用",
          _full_rc == 0 and len(_collect_calls) == 1 and _identity_inputs == [_valid_inputs]
          and len([cmd for cmd in _runs
                   if any("run_engine_tests.sh" in part for part in cmd)]) == 1
          and "--strict-tests" in _runs[_docs_index]
          and "--no-test-runs" not in _runs[_docs_index]
          and _run_envs[_runner_index] == _run_envs[_docs_index]
          and _run_envs[_runner_index]["SLCN_TEST_INPUTS_SHA256"] == "b" * 64,
          str(_run_envs))
finally:
    G.collect_auto_inputs = _orig_collect_inputs
    G.test_run_identity = _orig_identity
    S.run = _saved_run
    S.check_env, S.check_imports = _saved_env, _saved_imports
    S.scan_code_health, S.check_cases, S.check_app_bundle = _saved_health, _saved_cases, _saved_app
    S.FAILS[:], S.WARNS[:], S.SKIPS[:] = _saved_state
    if _nested_value is not None:
        os.environ["SLCN_SELFCHECK_NESTED"] = _nested_value

# ── ④ O9 反例（第四十四批③a）：拆掉 test_selfcheck × App 新鲜度循环依赖 ─────
# 判据（三反例）：
#   (A) App 过期时，③ 用的命令（--skip-app）仍全绿；且同一过期包**不跳过**时 selfcheck 能红
#       —— 证明「拆依赖」不是把检查削弱。
#   (B) build.sh 末尾的守卫（gen_docs.py --cmp-app）对「包内过期」报错、对干净包 PASS；
#       用假目录模拟，不需要真 build。
#   (C) AUTO 数字不变：App 行移出后，`gen_docs --check --no-test-runs` 仍 PASS（非测试行一致）；
#       且新算的 AUTO 块不再含「App 包内一致性」行。
GEN_DOCS = os.path.join(_ENGINE, "tools", "gen_docs.py")


def fake_app(name, corrupt=None):
    """在 TMP 下造一个「包内 Engine」：按 APP_FILES 从仓库拷贝，可选弄坏一个文件。"""
    root = os.path.join(TMP, name)
    shutil.rmtree(root, ignore_errors=True)
    for rel in G.APP_FILES:
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(_ENGINE, rel), dst)
    if corrupt:
        with open(os.path.join(root, corrupt), "a", encoding="utf-8") as f:
            f.write("\n# 反例探针：包内已过期\n")
    return root


good_app = fake_app("app_good")
stale_app = fake_app("app_stale", corrupt="slcn/sources.py")

# (B) build.sh 守卫：干净包 PASS（28/28），过期包报错并点名文件
rc, out = run([sys.executable, GEN_DOCS, "--cmp-app", "--app-dir", good_app])
check("④B1 包内干净 → --cmp-app PASS（28/28）", rc == 0 and "28/28" in out, out[-200:])
rc, out = run([sys.executable, GEN_DOCS, "--cmp-app", "--app-dir", stale_app])
check("④B2 包内过期 → --cmp-app 报错（exit 1 + 点名 slcn/sources.py）",
      rc == 1 and "❌" in out and "slcn/sources.py" in out, out[-300:])
rc, out = run([sys.executable, GEN_DOCS, "--cmp-app", "--app-dir", os.path.join(TMP, "no_such_app")])
check("④B3 包目录不存在 → --cmp-app 也报错（不静默通过）",
      rc == 1 and "❌" in out, out[-200:])

# (A) App 过期时：③ 的命令（--skip-app）全绿；不跳过则红（反证检查没被削弱）
env_stale = dict(os.environ, SLCN_SELFCHECK_NESTED="1", SLCN_APP_DIR=stale_app,
                 PYTHONIOENCODING="utf-8")
r = subprocess.run([sys.executable, SELF, "--no-cases", "--skip-app"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env_stale)
out = (r.stdout or "") + (r.stderr or "")
check("④A1 App 过期 + --skip-app → selfcheck rc=0（循环依赖已拆）",
      r.returncode == 0 and "自查通过" in out, out[-400:])
r = subprocess.run([sys.executable, SELF, "--no-cases"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env_stale)
out = (r.stdout or "") + (r.stderr or "")
check("④A2 反证：同一过期包、不带 --skip-app → selfcheck rc=1（检查本身没被削弱）",
      r.returncode == 1 and "App 包内一致性" in out, out[-400:])

# (C) AUTO 数字不变：App 行移出后非测试行仍一致；且新块不再含 App 行
body = G.build_block(run_test_suites=False)[0]
check("④C1 新算的 AUTO 块不再含「App 包内一致性」行", "App 包内一致性" not in body)
if G.AUDIT_CSV.is_file() and G.CORPUS_CSV.is_file():
    # 有稿件库产物时 README 的普查/corpus 行才是可比的（CI/新克隆没有这些产物，跳过）
    rc, out = run([sys.executable, GEN_DOCS, "--check", "--no-test-runs"])
    check("④C2 移出 App 行后 AUTO 非测试行仍全部一致（--check --no-test-runs PASS）",
          rc == 0, out[-400:])
else:
    skip("④C2 AUTO 非测试行 strict 比对", "本机无 out/ 产物（CI/新克隆形态），跳过")

shutil.rmtree(TMP, ignore_errors=True)
print("=" * 70)
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）")
      + (f"｜SKIP {len(SKIPS)} 项" if SKIPS else ""))
sys.exit(1 if FAILS else 0)
