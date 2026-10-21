#!/usr/bin/env python3
"""hermetic 夹具（批78 / B-145 波1）——纯标准库、无网络、只写调用方给的目录

供 `tools/batch_accept.py` / `tools/ci_summary.py` / `tools/regression_diff.py`
的 `--self-test` 共用：在 `tempfile.TemporaryDirectory()` 里生成

- 假 `gate_plan.json`（必跑 / 可跳 / 延后 + 规则 id + last 基线）
- 假证据目录（full 写前写后、gen_docs 写一次 + AUTO 数字、lint、docs、Swift 7 台）
- 假 `gh run view --json` 落盘产物（绿 + 5 类红：测试 / 依赖 / 文档 AUTO / 超时 / 取消）
- 假 KPI 行（基线 vs 本批，含回归）

设计约束（与三件工具同）：**不硬编码任何真实路径**（不写死任何机器上的绝对路径），
所有产物写在调用方传入的 `root` 之下；同一输入多次调用逐字节相同。

用法：由 self-test 内部 `importlib` 加载，不单独运行（`--dump` 仅用于人工看一眼）。
"""
from __future__ import annotations

import json
import os
import re

FIXTURE_VERSION = "1.0.0"

# 夹具里的「上一批」基线（对应真实 gate_plan.json 的 last 字段）
LAST_BATCH = 76
THIS_BATCH = 78


# ---------------------------------------------------------------------------
# 基础写入
# ---------------------------------------------------------------------------

def write_text(path: str, text: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def write_json(path: str, obj) -> str:
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# 假 gate plan（schema 与 notes/tools/gate_plan.py 的输出一致）
# ---------------------------------------------------------------------------

def gate_plan_doc(batch: int = THIS_BATCH) -> dict:
    gates = [
        {"id": "full_pre", "status": "required",
         "cmd": "cd Engine && PYTHONPATH=.:vendor bash tools/run_engine_tests.sh full",
         "evidence": ["suites", "red", "seconds", "log"],
         "required_when": ["any_file_changed"], "skip_when": [],
         "reasons": [{"rule": "any_file_changed", "detail": "required_when 命中"}],
         "last": {"batch": LAST_BATCH, "suites": 106, "red": 0, "seconds": 125.9}},
        {"id": "corpus_two", "status": "skipped",
         "cmd": "cd Engine && PYTHONPATH=.:vendor python3 corpus.py (x2, shasum out/corpus/corpus.csv)",
         "evidence": ["sha_pass1", "sha_pass2", "diff_vs_baseline"],
         "required_when": ["engine_product_py_changed"], "skip_when": ["only_tools_docs_gui_changed"],
         "reasons": [{"rule": "only_tools_docs_gui_changed",
                      "detail": "skip_when 命中（跳过须 Lead 在 ACCEPT 确认）"}],
         "last": {"batch": LAST_BATCH, "sha": "0" * 64, "diff_rows": 0}},
        {"id": "docx_audit", "status": "skipped",
         "cmd": "cd Engine && PYTHONPATH=.:vendor python3 docx_audit.py --out-dir <out>",
         "evidence": ["success", "fail", "csv_sha", "diff_vs_notes_baseline"],
         "required_when": ["docx_or_refs_parse_changed"], "skip_when": ["no_parse_change"],
         "reasons": [{"rule": "no_parse_change",
                      "detail": "skip_when 命中（跳过须 Lead 在 ACCEPT 确认）"}],
         "last": {"batch": LAST_BATCH, "success": 132, "fail": 0, "csv_sha": "1" * 64,
                  "vs_baseline": "equal"}},
        {"id": "gen_docs_write", "status": "required",
         "cmd": "cd Engine && python3 tools/gen_docs.py (write exactly once)",
         "evidence": ["rc", "inputs_sha", "auto_suites", "auto_assertions", "auto_fail",
                      "readme_changed"],
         "required_when": ["always"], "skip_when": [],
         "reasons": [{"rule": "always", "detail": "required_when 命中"}],
         "last": {"batch": LAST_BATCH, "rc": 0, "inputs_sha": "f25dbf2182a0",
                  "auto_suites": 105, "auto_assertions": 4221, "auto_fail": 0}},
        {"id": "full_post", "status": "required",
         "cmd": "same as full_pre",
         "evidence": ["suites", "red", "seconds", "log"],
         "required_when": ["gen_docs_changed_readme", "engine_product_py_changed"],
         "skip_when": ["gen_docs_noop_and_no_code_change"],
         "reasons": [{"rule": "gen_docs_changed_readme", "detail": "无法从变更推断 → 保守必跑"}],
         "last": {"batch": LAST_BATCH, "suites": 106, "red": 0, "seconds": 125.7}},
        {"id": "build", "status": "skipped",
         "cmd": "./build.sh (exactly once)",
         "evidence": ["seconds", "cmp_28", "manifest", "engine_py_sha", "artifact_sha"],
         "required_when": ["app_bundle_inputs_changed"],
         "skip_when": ["only_notes_or_gen_docs_or_docs_changed"],
         "reasons": [{"rule": "only_notes_or_gen_docs_or_docs_changed",
                      "detail": "skip_when 命中（跳过须 Lead 在 ACCEPT 确认）"}],
         "last": {"batch": LAST_BATCH, "seconds": 29.6, "cmp": "28/28", "o5": 1}},
        {"id": "lint", "status": "required",
         "cmd": "cd Engine && PYTHONPATH=.:vendor python3 bibdb.py lint --db <db.jsonl>",
         "evidence": ["rc", "errors", "warnings", "records", "db_sha_before", "db_sha_after"],
         "required_when": ["always"], "skip_when": [],
         "reasons": [{"rule": "always", "detail": "required_when 命中"}],
         "last": {"batch": LAST_BATCH, "rc": 0, "errors": 0, "warnings": 18, "records": 7051,
                  "db_sha": "3" * 64}},
        {"id": "docs", "status": "required",
         "cmd": "cd Engine && PYTHONPATH=.:vendor python3 tests/test_docs_consistency.py",
         "evidence": ["rc", "failures"],
         "required_when": ["always"], "skip_when": [],
         "reasons": [{"rule": "always", "detail": "required_when 命中"}],
         "last": {"batch": LAST_BATCH, "rc": 0, "failures": 0}},
        {"id": "swift", "status": "required",
         "cmd": "bash tools/run_swift_tests.sh",
         "evidence": ["beds", "fail", "pass_total"],
         "required_when": ["swift_or_sources_mac_changed", "acceptance_batch"],
         "skip_when": ["only_notes_changed"],
         "reasons": [{"rule": "acceptance_batch", "detail": "required_when 命中"}],
         "last": {"batch": LAST_BATCH, "beds": 7, "fail": 0, "pass_total": 583}},
        {"id": "ci_watch", "status": "deferred",
         "cmd": "gh run view <id> --json status,conclusion",
         "evidence": ["ci_run_id", "ci_conclusion"],
         "required_when": ["commit_pushed"], "skip_when": [],
         "reasons": [{"rule": "commit_pushed",
                      "detail": "尚未 push；提交后必跑（ACCEPT 记录 ci run id）"}],
         "last": {"batch": LAST_BATCH, "ci_run_id": 39622908989, "ci_conclusion": "success"}},
    ]
    required = [g["id"] for g in gates if g["status"] == "required"]
    skipped = [g["id"] for g in gates if g["status"] == "skipped"]
    deferred = [g["id"] for g in gates if g["status"] == "deferred"]
    return {
        "batch": batch,
        "gates": gates,
        "summary": {"required": required, "skipped": skipped, "deferred": deferred,
                    "counts": {"required": len(required), "skipped": len(skipped),
                               "deferred": len(deferred)}},
        "gates_schema": 1,
        "tool": "gate_plan",
        "version": "1.0.0",
        "generated_at": "2026-10-22T00:00:00+0800",
    }


def write_gate_plan(path: str, batch: int = THIS_BATCH) -> str:
    return write_json(path, gate_plan_doc(batch))


# ---------------------------------------------------------------------------
# 假证据日志（格式对齐批77 实测的验收日志形态）
# ---------------------------------------------------------------------------

def full_log(suites: int, red: int, seconds: float, exit_code: int,
             red_suites=("test_fixture_red_a", "test_fixture_red_b")) -> str:
    lines = [f"▶ full 档：全部 {suites} 套（夹具数据，非真实套件）",
             "  解释器 <fixture-python> ｜ PYTHONPATH=<fixture>"]
    for i in range(min(3, max(0, suites - red))):
        lines.append(f"  PASS test_fixture_{i:03d}                     失败 0 项（全部通过）")
    for name in list(red_suites)[:max(0, red)]:
        lines.append(f"  FAIL {name}                     退出码 1｜失败 1 项：['fixture 断言']")
    lines.append(f"汇总：{suites} 套件，红 {red} ｜ full 档耗时 {seconds} 秒")
    lines.append("✅ 全部通过" if red == 0 else f"❌ 有 {red} 套红（夹具）")
    lines.append(f"EXIT={exit_code} elapsed={int(round(seconds))}s")
    return "\n".join(lines) + "\n"


def gen_docs_write_log(exit_code: int = 0, inputs_sha: str = "ec1b5867be84") -> str:
    status = "PASS" if exit_code == 0 else "FAIL"
    return (
        "提示  写模式：非测试数据块已先行落盘；接着跑套件算测试计数\n"
        f"{status}  已就地更新 AUTO 块（夹具；块外内容逐字节未动）\n"
        f"      AUTO 输入清单：Engine/out/auto-inputs.json（inputs sha {inputs_sha}；测试计数现跑）\n"
        f"EXIT={exit_code} elapsed=130s\n"
    )


def auto_numbers_text(auto_suites: int = 106, auto_assertions: int = 4244,
                      auto_fail: int = 0,
                      inputs_sha: str = "ec1b5867be84ce4c566e53ce7d4449747a9b93cede1f764bdce282baecc6fa78"
                      ) -> str:
    return (
        f"AUTO测试行: {auto_suites} 套件 / {auto_assertions} 项断言 / FAIL {auto_fail} 行\n"
        f"inputs_sha256: {inputs_sha}\n"
        f"tests_files: {auto_suites}\n"
    )


def readme_diff_text(insertions: int = 31, deletions: int = 31) -> str:
    return (
        "diff --git a/Engine/README.md b/Engine/README.md\n"
        "--- a/Engine/README.md\n"
        "+++ b/Engine/README.md\n"
        "@@ -127,58 +127,58 @@\n"
        "-**测试**：**105 套件 / 4221 项断言 / FAIL 0 行**\n"
        "+**测试**：**106 套件 / 4244 项断言 / FAIL 0 行**\n"
        "--- 统计 ---\n"
        " Engine/README.md | 62 +++++++++++++----------------\n"
        f" 1 file changed, {insertions} insertions(+), {deletions} deletions(-)\n"
        "hunks=1\n"
    )


def lint_log(errors: int = 0, warnings: int = 18, records: int = 7051,
             exit_code: int = 0, db_sha: str = "3" * 64) -> str:
    return (
        f"db_sha_before: {db_sha}  ../fixture/bibliography.jsonl\n"
        "库：../fixture/bibliography.jsonl\n"
        "! 内容完全相同的多条记录：fixture-a、fixture-b\n"
        "! 字段冲突：待裁决 0 条 / 历史已裁决 181 条（审计留痕，无需处理）\n"
        f"条目 {records}（已核实 {records} / 未核实 0）；字段冲突：待裁决 0 条记录 / 0 处差异\n"
        f"error {errors} 项，warning {warnings} 项——库可用。\n"
        f"EXIT={exit_code} elapsed=0s\n"
        f"db_sha_after: {db_sha}  ../fixture/bibliography.jsonl\n"
    )


def docs_log(failures: int = 0, exit_code: int = 0) -> str:
    tail = "失败 0 项（全部通过）" if failures == 0 else f"失败 {failures} 项（有失败）"
    return (
        "── (a) AUTO 块（tools/gen_docs.py --check）──\n"
        "PASS  (a) gen_docs.py --check 通过（低成本（--no-test-runs）；夹具）\n"
        "── (b) 文档里引用的路径必须存在 ──\n"
        "PASS  (b) 1 份文档里的路径引用全部存在\n"
        f"{tail}\n"
        f"EXIT={exit_code} elapsed=0s\n"
    )


DEFAULT_SWIFT_BEDS = (("ai_settings", 411), ("ai_store", 14), ("proofcheck", 18),
                      ("toc", 16), ("infocheck", 26), ("cutover", 49), ("bibdb_ux", 49))


def swift_log(beds=DEFAULT_SWIFT_BEDS, fail: int = 0, exit_code: int = 0) -> str:
    lines = ["▶ 编译并运行 Swift 测试台（生产源码本身；夹具）"]
    total = 0
    for name, passed in beds:
        total += passed
        lines.append(f"  {name:<14} SUMMARY: {passed} PASS / 0 FAIL")
    if fail:
        # 夹具：把失败记在最后一台上，总数相应扣减
        lines[-1] = f"  {beds[-1][0]:<14} SUMMARY: {max(0, beds[-1][1] - fail)} PASS / {fail} FAIL"
        total -= fail
    lines.append("✅ 全部测试台 0 FAIL" if fail == 0 else "❌ 有测试台 FAIL（夹具）")
    lines.append(f"EXIT={exit_code} elapsed=129s")
    return "\n".join(lines) + "\n"


def rc_style_log(text: str, style: str = "exit") -> str:
    """把日志的退出码标记改写成批78 真实验收链的形态（供 rc 解析用例）。

    style = "exit"（默认，`EXIT=0`）/ "rc"（`rc=0`，批78 真日志形态）/
    "mixed"（先出现中途 `EXIT=2`，再 `rc=0`，末行 `EXIT=0` → 应取最后一个）/
    "none"（删掉所有退出码标记 → 应判 UNKNOWN）。
    """
    if style in (None, "", "exit"):
        return text
    m = re.search(r"^EXIT=(\d+)(.*)$", text, re.M)
    code = int(m.group(1)) if m else 0
    lines, tail = [], ""
    for ln in text.splitlines():
        hit = re.match(r"^EXIT=\d+(.*)$", ln)
        if hit:
            if hit.group(1).strip():        # 保留同行尾注（如 elapsed=130s）
                tail = hit.group(1).strip()
            continue
        lines.append(ln)
    if style == "rc":
        lines.append(f"rc={code}")
    elif style == "mixed":
        lines.append("EXIT=2")          # 中途某个步骤的旧码（必须被最后一个覆盖）
        lines.append(f"rc={code}")
        lines.append(f"EXIT={code}")
    # style == "none"：什么都不追加（退不出码 → 应判 UNKNOWN）
    if tail:
        lines.append(tail)
    return "\n".join(lines) + "\n"


# 门禁 id -> (主证据文件名模式, 附加证据键)
EVIDENCE_FILENAMES = {
    "full_pre": ("01-full_pre.log", None),
    "full_post": ("02-full_post.log", None),
    "gen_docs_write": ("03-gen_docs_write.log", "auto-numbers.txt"),
    "lint": ("04-lint.log", None),
    "docs": ("05-docs.log", None),
    "swift": ("06-swift.log", None),
}


def build_evidence_dir(root: str, *, omit=(), pre=(107, 2, 131.5, 2),
                       post=(107, 0, 128.0, 0), auto=(106, 4244, 0),
                       lint=(0, 18, 7051, 0), docs=(0, 0),
                       swift=(DEFAULT_SWIFT_BEDS, 0, 0), gen_docs_exit: int = 0,
                       readme_diff: bool = True, rc_style: str = "exit") -> dict:
    """在 root 下生成一套完整证据；`omit` 里的门禁**不写文件**（用于缺证据反例）。"""
    os.makedirs(root, exist_ok=True)
    files = {}

    def put(gate_id, name, text):
        if gate_id in omit:
            return
        write_text(os.path.join(root, name), rc_style_log(text, rc_style))
        files[gate_id] = name

    pre_name, _ = EVIDENCE_FILENAMES["full_pre"]
    post_name, _ = EVIDENCE_FILENAMES["full_post"]
    gw_name, aux_name = EVIDENCE_FILENAMES["gen_docs_write"]
    put("full_pre", pre_name, full_log(*pre))
    put("full_post", post_name, full_log(*post))

    if "gen_docs_write" not in omit:
        write_text(os.path.join(root, gw_name),
                   rc_style_log(gen_docs_write_log(gen_docs_exit), rc_style))
        write_text(os.path.join(root, aux_name), auto_numbers_text(*auto))
        if readme_diff:
            write_text(os.path.join(root, "03-readme-diff.log"), readme_diff_text())
        files["gen_docs_write"] = gw_name
    put("lint", EVIDENCE_FILENAMES["lint"][0], lint_log(*lint))
    put("docs", EVIDENCE_FILENAMES["docs"][0], docs_log(*docs))
    put("swift", EVIDENCE_FILENAMES["swift"][0], swift_log(*swift))
    return files


# ---------------------------------------------------------------------------
# 假 CI run（gh run view --json name,status,conclusion,jobs 的落盘形态）
# ---------------------------------------------------------------------------

def ci_step(number: int, name: str, conclusion: str = "success", status: str = "completed") -> dict:
    return {"number": number, "name": name, "status": status, "conclusion": conclusion}


def ci_job(name: str, steps, conclusion: str = "success", status: str = "completed",
           database_id: int = 1) -> dict:
    return {"databaseId": database_id, "name": name, "status": status,
            "conclusion": conclusion, "steps": steps}


def ci_run(jobs, *, conclusion: str = "success", status: str = "completed",
           run_id: int = 40000000001, name: str = "CI", workflow: str = "ci.yml",
           head_sha: str = "a" * 40) -> dict:
    return {"conclusion": conclusion, "status": status, "databaseId": run_id,
            "name": name, "workflowName": workflow, "event": "push", "headSha": head_sha,
            "createdAt": "2026-10-22T00:00:00Z", "updatedAt": "2026-10-22T00:05:00Z",
            "jobs": jobs}


def ci_green() -> dict:
    engine = ci_job("引擎（套件 + 文档看门狗）", [
        ci_step(1, "Set up job"),
        ci_step(2, "Run actions/checkout@v4"),
        ci_step(3, "Run actions/setup-python@v5"),
        ci_step(4, "装依赖"),
        ci_step(5, "AUTO 块是否过期（独立守卫；CI 无稿件库/out 产物 → 信息性）"),
        ci_step(6, "文档看门狗"),
        ci_step(7, "全部套件"),
        ci_step(8, "案例索引是否过期（没有笔记仓时自动跳过）"),
        ci_step(9, "Complete job"),
    ], database_id=119327869151)
    swift = ci_job("Swift 测试台 + Windows 外壳桥梁", [
        ci_step(1, "Set up job"),
        ci_step(2, "Run actions/checkout@v4"),
        ci_step(3, "装依赖"),
        ci_step(4, "Swift 测试台（7 个）"),
        ci_step(5, "Windows 外壳桥梁测试"),
        ci_step(6, "Complete job"),
    ], database_id=119327869659)
    return ci_run([engine, swift])


# 5 类归因 + 1 类「其它」：失败 step 名字按真实 workflow 的措辞造
RED_CASES = {
    "tests": ("全部套件", "failure", "failure"),
    "deps": ("装依赖", "failure", "failure"),
    "docs_auto": ("AUTO 块是否过期（独立守卫；CI 无稿件库/out 产物 → 信息性）", "failure", "failure"),
    "timeout": ("全部套件", "timed_out", "failure"),
    "cancelled": ("全部套件", "cancelled", "cancelled"),
    "other": ("自定义步骤", "failure", "failure"),
}


def ci_red(case: str = "tests") -> dict:
    step_name, step_conclusion, job_conclusion = RED_CASES[case]
    engine = ci_job("引擎（套件 + 文档看门狗）", [
        ci_step(1, "Set up job"),
        ci_step(2, "Run actions/checkout@v4"),
        ci_step(3, "装依赖"),
        ci_step(4, "文档看门狗"),
        ci_step(5, step_name, step_conclusion),
        ci_step(6, "Complete job", "skipped", "completed"),
    ], conclusion=job_conclusion,
        status="completed" if job_conclusion != "cancelled" else "completed",
        database_id=119327869151)
    swift = ci_job("Swift 测试台 + Windows 外壳桥梁", [ci_step(1, "Set up job")],
                   database_id=119327869659)
    run_conclusion = "cancelled" if job_conclusion == "cancelled" else "failure"
    return ci_run([engine, swift], conclusion=run_conclusion)


def failing_job_log(case: str = "tests") -> str:
    """给 --log 用的失败 job 日志（只有超时/取消识别需要看文本）。"""
    body = "some output\n"
    if case == "timeout":
        body += "The job has exceeded the maximum execution time of 30 minutes\n"
    elif case == "cancelled":
        body += "Run was cancelled\n"
    elif case == "deps":
        body += "ERROR: Could not find a version that satisfies the requirement fixture\n"
    elif case == "docs_auto":
        body += "FAIL  AUTO 块已过期（README 里的是旧数字）\n"
    elif case == "tests":
        body += "汇总：107 套件，红 1\n"
    return body


# ---------------------------------------------------------------------------
# 假 KPI 行（baselines/batch-kpi.json 的 `batches.<n>` 形态）
# ---------------------------------------------------------------------------

def kpi_row(batch: int, **over) -> dict:
    row = {
        "status": "closed",
        "face_M": 40.0,
        "eq_M": 5.0,
        "steps": 300,
        "sessions": 4,
        "full_suites": 106,
        "auto_suites": 105,
        "auto_assertions": 4221,
        "corpus_sha": "1d4bf34edab701bd09179cf597ec7f690768643cdf500d7d5b961ee92549d3f7",
        "audit_csv_sha": "9e5f3e596fcc0707d340453c85ebf994bfdd75cf6d236f23900c2d200e9ddd25",
        "build_count": 1,
        "o5": 1,
        "ci_red": 0,
        "amend_count": 0,
        "rework_count": 0,
        "lead_closure_steps": 40,
        "review_findings": 4,
        "gates_required": ["full", "gen_docs_write", "full_post", "lint", "docs", "swift"],
        "gates_skipped": [],
        "tools_used": ["fixture/tools.py"],
        "checkpoint_snapshot": True,
        "source": "fixture",
    }
    row.update(over)
    row["batch"] = batch
    return row


def kpi_pair_regression():
    """基线/本批：面值降（improved）+ steps 上升（regression）+ 未测字段（skip）。"""
    base = kpi_row(THIS_BATCH - 1, face_M=40.0, eq_M=5.0, steps=300, full_suites=106,
                   auto_suites=105, auto_assertions=4221, build_count=1, review_findings=4)
    cur = kpi_row(THIS_BATCH, face_M=24.0, eq_M=3.0, steps=360, full_suites=107,
                  auto_suites=106, auto_assertions=4244, build_count=0, review_findings=1,
                  corpus_sha=None, audit_csv_sha=None, gates_skipped=[{"gate": "corpus_two",
                                                                       "reason": "fixture"}])
    return base, cur


# ---------------------------------------------------------------------------
# 人工看一眼（不参与 self-test）
# ---------------------------------------------------------------------------

def _main(argv) -> int:  # pragma: no cover
    import sys
    import tempfile
    if len(argv) != 2 or argv[0] != "--dump":
        print("用法：python3 tools/tool_fixtures.py --dump <目录>（生成一套夹具供人工检查）")
        return 2
    root = os.path.abspath(argv[1])
    write_gate_plan(os.path.join(root, "gate_plan.json"))
    build_evidence_dir(root)
    write_json(os.path.join(root, "ci-green.json"), ci_green())
    for case in RED_CASES:
        write_json(os.path.join(root, f"ci-red-{case}.json"), ci_red(case))
    base, cur = kpi_pair_regression()
    write_json(os.path.join(root, "kpi-base.json"), base)
    write_json(os.path.join(root, "kpi-cur.json"), cur)
    print(f"[tool_fixtures] 夹具已写入 {root}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))
