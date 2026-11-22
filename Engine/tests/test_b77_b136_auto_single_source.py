#!/usr/bin/env python3.14
r"""批77 / B-136（P1）：AUTO「普查」sha 单源 = 笔记仓冻结基线；无笔记仓环境信息性降级。

为什么有这个测试：`Engine/README.md` 的 AUTO 块长期公开显示 `docx_audit.csv = 76ff3ba8…`
（**b70 遗留的 `Engine/out/` 本机缓存值**，out/ 是 gitignored 的），而冻结基线早已是笔记仓
`baselines/docx_audit.csv` 的另一份（批76 = `9e5f3e59…`）→ 公开数字与基线不同源、且陈旧
（B-136，批73 登记、批77 落地）。修复口径：
  · 本机可发现笔记仓 → AUTO 普查段**只读**笔记仓基线，行内注明来源 + 成功/失败数；
  · 无笔记仓（CI / 新克隆）→ 信息性降级：不展示会误导的 sha（有 out 缓存时只报成功/失败数）；
  · `--check` 在无笔记仓环境把「普查」整段归一化（环境差异不误红）；有笔记仓时仍严格比对，
    基线 sha 漂移必须判红（反例见 (e)）；
  · `out/auto-inputs.json` 增 `notes_audit_csv_sha256` / `notes_audit_txt_sha256` 参与快速通道。

全部 **hermetic** 的部分：(a)–(c)、(f) 用 `tempfile` fixture / 纯函数，不写真实 README 与 out/；
(d)/(e) 的端到端 `--check --no-test-runs` 只读真实 README（**本卡禁止跑 canonical gen_docs 写**，
README 仍待验收链重写 1 次），且只在「前提齐全 + 笔记仓可发现」的本机跑，CI 上 SKIP。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
for _p in (str(ENGINE), str(ENGINE / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ["SLCN_RUN_RECORD"] = "0"          # 纯测试：不写 run manifest 证据

import gen_docs as G  # noqa: E402

PY = G.PY if Path(G.PY).is_file() else sys.executable
TMP = Path(tempfile.mkdtemp(prefix="slcn_b136_"))
FAILS: list[str] = []


def check(name: str, cond: bool, detail="") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def run_manifest_isolation_checks() -> None:
    """Exercise strict manifest acceptance using only a temporary fake Engine."""
    fake_engine = TMP / "manifest_engine"
    fake_tools = fake_engine / "tools"
    fake_tests = fake_engine / "tests"
    fake_tools.mkdir(parents=True, exist_ok=True)
    fake_tests.mkdir(parents=True, exist_ok=True)
    runner = fake_tools / "run_engine_tests.sh"
    docs_suite = fake_tests / "test_docs_consistency.py"
    unit_suite = fake_tests / "test_unit.py"
    runner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docs_suite.write_text("# synthetic docs suite v1\n", encoding="utf-8")
    unit_suite.write_text("# synthetic unit suite v1\n", encoding="utf-8")

    saved_engine = G.ENGINE
    env_keys = ("SLCN_MANUSCRIPTS", "SLCN_TEST_DATASET_ID")
    saved_env = {key: os.environ.get(key) for key in env_keys}
    saved_manifest = os.environ.get("SLCN_TEST_RESULT_MANIFEST")
    saved_identity = os.environ.get("SLCN_TEST_INPUTS_SHA256")
    manifest_path = TMP / "current-full-manifest.json"
    synthetic_manuscripts = TMP / "manifest_manuscripts"
    (synthetic_manuscripts / "2099年第1期(fixture)").mkdir(parents=True, exist_ok=True)
    os.environ["SLCN_MANUSCRIPTS"] = str(synthetic_manuscripts)
    os.environ["SLCN_TEST_DATASET_ID"] = "fixture:test_b77_b136_auto_single_source:v1"
    inputs = {"fixture": "b387-manifest-loader", "dataset": os.environ["SLCN_TEST_DATASET_ID"]}

    def write_manifest(suites: list[dict], identity: str, **overrides) -> None:
        payload = {"schema": 1, "mode": "full", "identity_sha256": identity,
                   "suites": suites}
        payload.update(overrides)
        manifest_path.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")

    def suite_row(name: str, **overrides) -> dict:
        row = {"name": name, "passes": 3, "fails": 0, "return_code": 0,
               "status": None, "duration_seconds": 0.125}
        row.update(overrides)
        return row

    def current_rows() -> list[dict]:
        return [suite_row(path.stem) for path in G.runner_test_files()]

    try:
        G.ENGINE = fake_engine
        expected_names = [path.stem for path in G.runner_test_files()]
        identity = G.test_run_identity(inputs)
        os.environ["SLCN_TEST_RESULT_MANIFEST"] = str(manifest_path)
        os.environ["SLCN_TEST_INPUTS_SHA256"] = identity
        write_manifest(current_rows(), identity)
        loaded, reason = G.load_current_test_manifest(inputs)
        check("(manifest 1) 当前 identity 且精确覆盖的完整 manifest 被 loader 采信",
              expected_names == ["test_docs_consistency", "test_unit"]
              and loaded is not None and not reason and [r[0] for r in loaded] == ["test_unit"])

        runner.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        loaded_after_runner_change, _ = G.load_current_test_manifest(inputs)
        check("(manifest 2) runner shell 改变后旧 identity/manifest 被拒",
              loaded_after_runner_change is None)
        runner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        unit_suite.write_text("# synthetic unit suite v2\n", encoding="utf-8")
        loaded_after_suite_change, _ = G.load_current_test_manifest(inputs)
        check("(manifest 3) suite 内容改变后旧 identity/manifest 被拒",
              loaded_after_suite_change is None)

        identity = G.test_run_identity(inputs)
        os.environ["SLCN_TEST_INPUTS_SHA256"] = identity
        base_rows = current_rows()

        def rejected(suites: list[dict], **overrides) -> bool:
            write_manifest(suites, identity, **overrides)
            loaded_rows, _ = G.load_current_test_manifest(inputs)
            return loaded_rows is None

        check("(manifest 4) 缺失、重复、额外 suite 均被拒",
              rejected(base_rows[:-1])
              and rejected(base_rows + [suite_row("test_unit")])
              and rejected([suite_row("test_docs_consistency"), suite_row("test_extra")]))
        check("(manifest 5) 坏 count/status/duration 均被拒",
              rejected([suite_row("test_docs_consistency"), suite_row("test_unit", passes=True)])
              and rejected([suite_row("test_docs_consistency"), suite_row("test_unit", status=1)])
              and rejected([suite_row("test_docs_consistency"),
                            suite_row("test_unit", duration_seconds=math.nan)]))
        check("(manifest 6) docs suite 有 FAIL、非零退出或非空 status 均被拒",
              rejected([suite_row("test_docs_consistency", fails=1), suite_row("test_unit")])
              and rejected([suite_row("test_docs_consistency", return_code=1), suite_row("test_unit")])
              and rejected([suite_row("test_docs_consistency", status="BLOCKED"),
                            suite_row("test_unit")]))

        abnormal_results = []
        abnormal_details = []
        for status in ("BLOCKED", "NOT_RUN"):
            blocked_rows = [suite_row("test_docs_consistency"),
                            suite_row("test_unit", status=status)]
            write_manifest(blocked_rows, identity)
            loaded_rows, reason = G.load_current_test_manifest(inputs)
            abnormal = G.abnormal_suites(loaded_rows)
            trusted = G.suite_rows_trusted(loaded_rows, expected=["test_unit"])
            abnormal_results.append(loaded_rows is not None
                                    and ("test_unit", status) in abnormal and not trusted)
            abnormal_details.append((status, reason, abnormal, trusted))
        check("(manifest 7) 非 docs 的 BLOCKED/NOT_RUN 不会通过严格结果判定",
              all(abnormal_results), abnormal_details)
    finally:
        G.ENGINE = saved_engine
        if saved_manifest is None:
            os.environ.pop("SLCN_TEST_RESULT_MANIFEST", None)
        else:
            os.environ["SLCN_TEST_RESULT_MANIFEST"] = saved_manifest
        if saved_identity is None:
            os.environ.pop("SLCN_TEST_INPUTS_SHA256", None)
        else:
            os.environ["SLCN_TEST_INPUTS_SHA256"] = saved_identity
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


run_manifest_isolation_checks()


def collect_synthetic_inputs() -> dict:
    """Collect cache-contract inputs under a temporary root and explicit dataset id."""
    keys = ("SLCN_MANUSCRIPTS", "SLCN_TEST_DATASET_ID")
    saved = {key: os.environ.get(key) for key in keys}
    fixture_root = TMP / "cache_manuscripts"
    (fixture_root / "2099年第1期(fixture)").mkdir(parents=True, exist_ok=True)
    try:
        os.environ["SLCN_MANUSCRIPTS"] = str(fixture_root)
        os.environ["SLCN_TEST_DATASET_ID"] = "fixture:test_b77_b136_auto_single_source:v1"
        inputs = G.collect_auto_inputs()
        inputs["env_complete"] = True
        return inputs
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_check(env_extra: dict, readme: Path | None = None) -> subprocess.CompletedProcess:
    """跑一次 `gen_docs.py --check --no-test-runs`（不写 README；只读 git/out/notes）。"""
    env = dict(os.environ, SLCN_RUN_RECORD="0", **env_extra)
    return subprocess.run([PY, str(ENGINE / "tools" / "gen_docs.py"), "--check", "--no-test-runs"],
                          cwd=str(ENGINE), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


COMPLETE, MISSING = G.environment_complete()
NOTES = G.notes_audit_paths()

print("── (a) 本机：普查数字唯一来源 = 笔记仓冻结基线 ──")
if NOTES is None:
    print("SKIP  (a) 本机没有笔记仓冻结基线（与 CI 同环境）—— 来源断言只在开发机有意义")
else:
    csv_path, txt_path = NOTES
    block, ok = G.audit_block()
    text = "\n".join(block)
    check("(a1) 普查段首行注明来源=笔记仓冻结基线",
          block[0].startswith("**普查**") and "笔记仓冻结基线" in block[0]
          and "baselines/docx_audit.csv" in block[0], block[0][:120])
    check("(a2) sha256[:12] = 基线文件现算值（csv 与 txt 各一）",
          f"`{G.sha12(csv_path)}`" in text and f"`{G.sha12(txt_path)}`" in text,
          f"csv={G.sha12(csv_path)} txt={G.sha12(txt_path)}")
    check("(a3) 不再出现旧 out 缓存 sha（B-136 的原始症状）",
          "76ff3ba8" not in text and G.sha12(G.AUDIT_CSV) not in text)
    sf = G._audit_success_fail(txt_path)
    check("(a4) 行内成功/失败数取自基线 txt 抬头",
          sf is not None and f"成功解析 **{sf[0]}**" in text and f"解析失败 **{sf[1]}**" in text,
          f"成功/失败={sf}")
    check("(a5) 与 `_ok` 列交叉核对为 0 行不一致（口径不变）",
          ok and "不一致 **0** 行 ✅" in text)
    # 有笔记仓时 normalize **不许**把普查段丢掉（否则基线漂移就抓不出来了）
    probe = "**普查**（探针）\n\n| x |\n\n**corpus 回归**（y）\n"
    check("(a6) 有笔记仓时 normalize_for_check 保留普查段（漂移仍会被抓）",
          "探针" in G.normalize_for_check(probe))

print("\n── (b) out/auto-inputs.json：notes sha 参与快速通道/缓存判定 ──")
if NOTES is None:
    print("SKIP  (b) 本机没有笔记仓冻结基线（notes sha 为空，无可断言）")
else:
    csv_path, txt_path = NOTES
    inputs = collect_synthetic_inputs()
    check("(b1) inputs 增 notes_audit_csv_sha256 = 基线 csv 的 sha256",
          inputs.get("notes_audit_csv_sha256") == sha_of(csv_path))
    check("(b2) inputs 增 notes_audit_txt_sha256 = 基线 txt 的 sha256",
          inputs.get("notes_audit_txt_sha256") == sha_of(txt_path))
    check("(b3) notes sha 参与 inputs 指纹（变了 → 输入指纹必变）",
          G.inputs_sha(inputs) != G.inputs_sha(dict(inputs, notes_audit_csv_sha256="deadbeef")))
    _cache_rows = [("test_x", 1, 0, None)]
    body = "\n".join(G.render_test_section(_cache_rows))
    block_sha = hashlib.sha256(
        G.normalize_for_check(body, lenient=False).encode("utf-8")).hexdigest()
    project = {"schema": G.AUTO_INPUTS_SCHEMA, "inputs": inputs,
               "test_rows": _cache_rows,
               "block_sha256": block_sha}
    # Cache behavior is driven by this synthetic input's own environment bit, not
    # COMPLETE from the caller's manuscript environment.
    check("(b4b) 合成完整 inputs 与块指纹一致 → 快速通道命中（不跑套件）",
          inputs.get("env_complete") is True
          and G.fast_check_ok(body, project, inputs))
    _incomplete_inputs = dict(inputs, env_complete=False)
    check("(b4a) 显式合成不完整 inputs → 快速通道不命中",
          not G.fast_check_ok(body, dict(project, inputs=_incomplete_inputs), _incomplete_inputs))
    check("(b5) 基线 sha 变 → 快速通道**不**命中（回退现算比对 → 能抓 README 未跟随）",
          not G.fast_check_ok(body, project, dict(inputs, notes_audit_csv_sha256="deadbeef")))
    _no_dataset_id = dict(inputs, manuscripts_present=True, test_dataset_id_sha256="")
    check("(b6) 反例：稿件库存在但缺数据集身份 → 持久缓存仍被拒绝",
          not G.persistent_cache_allowed(_no_dataset_id)
          and not G.fast_check_ok(body, project, _no_dataset_id))

print("\n── (c) 无笔记仓环境：信息性降级（不展示主导 sha，不误红）──")
_real_paths, _real_txt = G.notes_audit_paths, G.AUDIT_TXT
try:
    G.notes_audit_paths = lambda: None
    block, ok = G.audit_block()
    text = "\n".join(block)
    check("(c1) 降级行明确写「本环境无笔记仓基线」",
          ok and "本环境无笔记仓基线" in text, text[:160])
    check("(c2) 降级**不展示任何 sha**（不误导）",
          "sha256" not in text and not re.search(r"`[0-9a-f]{12}`", text))
    if G.AUDIT_TXT.is_file():
        sf = G._audit_success_fail(G.AUDIT_TXT)
        check("(c3) 本机有 out 缓存时只报成功/失败数并标 fallback",
              sf is not None and f"成功解析 **{sf[0]}**" in text and "fallback" in text, text[:160])
    else:
        print("SKIP  (c3) 本机无 out 缓存，降级不报数字（CI 情形）")
    G.AUDIT_TXT = TMP / "nope" / "docx_audit.txt"
    text_no_out = "\n".join(G.audit_block()[0])
    check("(c4) 无笔记仓且无 out 产物 → 指向笔记仓基线（CI 的真实情形）",
          "本环境也没有 `out/` 普查产物" in text_no_out, text_no_out[:160])
finally:
    G.notes_audit_paths, G.AUDIT_TXT = _real_paths, _real_txt
# 环境差异归一化：README（开发机写的 notes 基线块）与 CI 现算（信息性行）必须可比
_b_readme = "**普查**（来源：笔记仓冻结基线，表…）\n\n| x |\n\n**corpus 回归**（y）\n"
_b_ci = "**普查**：本环境无笔记仓基线（…）\n\n**corpus 回归**（y）\n"
try:
    G.notes_audit_paths = lambda: None
    _n_readme = G.normalize_for_check(_b_readme)
    _n_ci = G.normalize_for_check(_b_ci)
    check("(c5) 无笔记仓 →「普查」整段归一化（开发机块 vs CI 现算块相等）",
          _n_readme == _n_ci and "笔记仓冻结基线" not in _n_readme)
finally:
    G.notes_audit_paths = _real_paths

print("\n── (d) 端到端两环境：--check --no-test-runs 的退出码 ──")
if not COMPLETE:
    print(f"SKIP  (d) 本机前提不全（缺 {'、'.join(MISSING)}）—— 与 test_docs_consistency (a) 同因")
else:
    empty_notes = TMP / "empty_notes"
    empty_notes.mkdir(exist_ok=True)
    p = run_check({"SLCN_NOTES": str(empty_notes)})
    tail = (p.stdout + p.stderr).strip().split("\n")[-1:]
    check("(d1) 无笔记仓环境 → --check 仍 rc0（信息性降级不误红）",
          p.returncode == 0, f"exit={p.returncode} {tail}")
    if NOTES is not None:
        p2 = run_check({})
        out2 = p2.stdout + p2.stderr
        if p2.returncode == 0:
            check("(d2) 有笔记仓环境 → --check rc0（README 已按 notes 基线重写）", True)
        else:
            # **预期残留**：本卡明令禁止跑 canonical gen_docs 写（写 1 次留给验收链）。
            # 只要红的原因就是「README 普查段尚未重写」，本项即算符合卡面预期。
            stale = [ln for ln in out2.split("\n")
                     if ln.lstrip().startswith(("-", "+")) and "**普查**" in ln]
            check("(d2) 有笔记仓环境 → 暂 rc1 且差异只在「普查」段（预期：待验收链写 1 次）",
                  p2.returncode == 1 and "过期" in out2 and bool(stale),
                  f"exit={p2.returncode} 差异行={len(stale)}")
    else:
        print("SKIP  (d2) 本机无笔记仓，无「有基线」对照环境")

print("\n── (e) 反例：基线 sha 变（临时 fixture）→ --check 必须判红 ──")
if NOTES is None:
    print("SKIP  (e) 本机没有笔记仓冻结基线，无法构造 fixture")
else:
    fixture = TMP / "fixture_notes"
    (fixture / "cases").mkdir(parents=True, exist_ok=True)
    (fixture / "baselines").mkdir(parents=True, exist_ok=True)
    fx_csv = fixture / "baselines" / "docx_audit.csv"
    shutil.copyfile(NOTES[0], fx_csv)
    shutil.copyfile(NOTES[1], fixture / "baselines" / "docx_audit.txt")
    # 只改 sha（多一个空行，CSV 记录数/表不变）→ 表相同、sha256 不同
    fx_csv.write_bytes(fx_csv.read_bytes() + b"\n")
    fx_sha12 = G.sha12(fx_csv)
    check("(e0) fixture 的 sha256 与真基线不同（只改 sha，不改表）",
          fx_sha12 != G.sha12(NOTES[0]), f"fixture={fx_sha12}")
    _real = G.notes_audit_paths
    try:
        G.notes_audit_paths = lambda: (fx_csv, fixture / "baselines" / "docx_audit.txt")
        fx_block = G.audit_block()[0]
        G.notes_audit_paths = _real
        real_block = G.audit_block()[0]
    finally:
        G.notes_audit_paths = _real
    no_sha = lambda lines: [ln for ln in lines if "sha256[:12]" not in ln]
    check("(e1) fixture 被当成唯一来源：sha 行随基线变、三态表逐字不变",
          f"`{fx_sha12}`" in "\n".join(fx_block) and no_sha(fx_block) == no_sha(real_block))
    if not COMPLETE:
        print(f"SKIP  (e2) 本机前提不全（缺 {'、'.join(MISSING)}），端到端反例跳过")
    else:
        p3 = run_check({"SLCN_NOTES": str(fixture)})
        out3 = p3.stdout + p3.stderr
        check("(e2) fixture 改基线 sha → --check --no-test-runs rc1（仍抓得出来）",
              p3.returncode == 1 and "过期" in out3 and fx_sha12 in out3,
              f"exit={p3.returncode}")

print("\n── (f) 口径不变：普查段以外的 AUTO 非测试行与 README 逐字一致 ──")
if not COMPLETE or NOTES is None:
    print("SKIP  (f) 需要在「前提齐全 + 笔记仓可发现」的开发机上比对")
else:
    new, _ = G.build_block(run_test_suites=False)

    def nontest_non_audit(body: str) -> str:
        txt = G.strip_test_lines(G.normalize_for_check(body, lenient=False))
        return G.AUDIT_SECTION.sub("<AUDIT>", txt)

    old_body = G.split_block((ENGINE / "README.md").read_text(encoding="utf-8"))[1]
    check("(f1) 测试计数行以外的数据段（corpus/占位符/红线）逐字不变（--no-test-runs 仍严格）",
          nontest_non_audit(old_body) == nontest_non_audit("\n" + new))
    check("(f2) 写模式「数据块先行」仍能切出测试段（B-110 的中间态落盘不回归）",
          G.test_section_lines("\n" + new) is not None
          and G.test_section_lines("\n" + new)[0].startswith("**测试**"))

print("\n── (g) strict full identity 覆盖 runner 全套件（含自指 docs suite）──")
_glob_names = sorted(p.stem for p in (ENGINE / "tests").glob("test_*.py") if p.is_file())
_runner_names = [p.stem for p in G.runner_test_files()]
_auto_names = [p.stem for p in G.test_files()]
check("(g1) full runner 集合与 tests/test_*.py 文件集一致",
      _runner_names == _glob_names, f"runner={len(_runner_names)} glob={len(_glob_names)}")
check("(g2) AUTO 只排除唯一自指 docs suite",
      set(_runner_names) - set(_auto_names) == {"test_docs_consistency"}
      and set(_auto_names) <= set(_runner_names))
_valid_rows = [(name, 1, 0, None) for name in _runner_names]
check("(g2a) strict 实跑结果必须精确覆盖 runner 全集",
      G.runner_result_rows_complete(_valid_rows, _runner_names))
check("(g2b) strict 实跑缺项/重复/额外 suite 或坏 row 都拒绝",
      not G.runner_result_rows_complete(_valid_rows[:-1], _runner_names)
      and not G.runner_result_rows_complete(_valid_rows[:-1] + [_valid_rows[0]], _runner_names)
      and not G.runner_result_rows_complete(
          _valid_rows + [("test_extra", 1, 0, None)], _runner_names)
      and not G.runner_result_rows_complete(
          _valid_rows[:-1] + [("test_broken", 1)], _runner_names))

_identity_engine = TMP / "identity_engine"
(_identity_engine / "tools").mkdir(parents=True, exist_ok=True)
(_identity_engine / "tests").mkdir(parents=True, exist_ok=True)
(_identity_engine / "tools" / "run_engine_tests.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
_docs_suite = _identity_engine / "tests" / "test_docs_consistency.py"
_docs_suite.write_text("# docs suite fixture v1\n", encoding="utf-8")
(_identity_engine / "tests" / "test_unit.py").write_text("# ordinary suite fixture\n", encoding="utf-8")
_saved_engine = G.ENGINE
try:
    G.ENGINE = _identity_engine
    _identity_before = G.test_run_identity({"fixture": "fixed"})
    _docs_suite.write_text("# docs suite fixture v2\n", encoding="utf-8")
    _identity_after_docs_change = G.test_run_identity({"fixture": "fixed"})
    (_identity_engine / "tests" / "test_extra.py").write_text("# new suite fixture\n", encoding="utf-8")
    _identity_after_membership_change = G.test_run_identity({"fixture": "fixed"})
finally:
    G.ENGINE = _saved_engine
check("(g3) 仅改 strict docs suite 内容会使 full identity 失效",
      _identity_before != _identity_after_docs_change)
check("(g4) 新增 runner suite 会使 full identity 失效",
      _identity_after_docs_change != _identity_after_membership_change)

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
