#!/usr/bin/env python3
"""CI 结果摘要器（B-145 波1 / 批78）——只读、离线、确定性、**不调用 gh、不联网**

读 `gh run view <id> --json name,status,conclusion,jobs` 的**落盘 JSON**（可选再给一份
job 日志目录）→ 生成 `ci_summary.md`（job 表 / 失败 step / **归因类别**）+ `ci_summary.json`。
摘要可直接贴进 EVIDENCE 的「CI」行；本工具**不**代替人判断「可接受的红」。

归因类别（按优先级判定，避免"测试红背锅"）
------------------------------------------
1. **取消**：run / job / step 任一 conclusion = `cancelled`。
2. **超时**：step conclusion = `timed_out`，或 job 日志出现 `timed out` /
   `exceeded the maximum execution time`。
3. **依赖安装失败**：失败 step 名含 `装依赖` / `pip` / `install` / `requirements`。
4. **文档 AUTO 过期**：失败 step 名含 `AUTO` / `文档看门狗` / `gen_docs` / `docs_consistency`。
5. **测试红**：失败 step 名含 `套件` / `测试` / `测试台` / `test` / `swift` / `桥梁`。
6. **其它**：兜底（含「job 红但找不到失败 step」——绝不静默当绿）。

只依赖 `name` / `status` / `conclusion` / `jobs[].steps[]`（`databaseId` / `startedAt` /
`completedAt` 可选）；字段缺失 → `UNKNOWN` + WARN，不猜。

用法
----
  python3 tools/ci_summary.py --input <run.json> --out <输出目录> [--log <job 日志目录>] [--self-test]

  --log 目录里按 `<job databaseId>.log` 或 `<job 名>.log`（非法字符换成 `_`）查找 job 日志。
  退出码：0 = 全绿（无红、无归因命中）；1 = 有红/归因命中；2 = 输入不可用。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

TOOL_VERSION = "1.0.0"
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

CATEGORIES = ("测试红", "依赖安装失败", "文档 AUTO 过期", "超时", "取消", "其它")
FAIL_CONCLUSIONS = {"failure", "timed_out", "cancelled", "startup_failure", "action_required"}

_DEPS_HINTS = ("装依赖", "pip", "install", "requirements", "setup-python", "npm", "cargo")
_DOCS_HINTS = ("auto", "文档看门狗", "gen_docs", "docs_consistency", "文档一致性")
_TESTS_HINTS = ("套件", "测试", "测试台", "test", "swift", "桥梁", "pytest", "unittest")
_TIMEOUT_HINTS = ("timed out", "timeout", "exceeded the maximum execution time",
                  "the job has exceeded")


def _hits(name: str, hints) -> bool:
    low = (name or "").lower()
    return any(h in low for h in hints)


def classify(step_name: str, job_name: str, conclusions, log_text: str = "") -> str:
    """conclusions = 该 job/step 的 conclusion 集合（run/job/step）。"""
    if "cancelled" in conclusions:
        return "取消"
    low_log = (log_text or "").lower()
    if "timed_out" in conclusions or any(h in low_log for h in _TIMEOUT_HINTS):
        return "超时"
    # 失败 step 名优先（step 名有值就不看 job 名，避免「引擎（套件…）」把一切归成测试红）
    if (step_name or "").strip():
        if _hits(step_name, _DEPS_HINTS):
            return "依赖安装失败"
        if _hits(step_name, _DOCS_HINTS):
            return "文档 AUTO 过期"
        if _hits(step_name, _TESTS_HINTS):
            return "测试红"
        return "其它"
    # 只有 job 红、定位不到失败 step 时，才用 job 名兜底
    if _hits(job_name, _DEPS_HINTS):
        return "依赖安装失败"
    if _hits(job_name, _DOCS_HINTS):
        return "文档 AUTO 过期"
    if _hits(job_name, _TESTS_HINTS):
        return "测试红"
    return "其它"


def _seconds(start, end):
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    from datetime import datetime
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        return int((datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds())
    except ValueError:
        return None


def _sanitize(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", name or "") or "job"


def _load_job_log(log_dir: str, job: dict, index: int) -> str:
    if not log_dir or not os.path.isdir(log_dir):
        return ""
    candidates = []
    if job.get("databaseId") is not None:
        candidates.append(f"{job['databaseId']}.log")
    candidates.append(f"{_sanitize(job.get('name'))}.log")
    candidates.append(f"{index}.log")
    for name in candidates:
        path = os.path.join(log_dir, name)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
    return ""


def summarize_run(run: dict, log_dir: str = "") -> dict:
    jobs_in = run.get("jobs") or []
    jobs, failures, warns = [], [], []
    counts = {c: 0 for c in CATEGORIES}
    red_jobs = 0
    for index, job in enumerate(jobs_in, start=1):
        if not isinstance(job, dict):
            warns.append(f"jobs[{index}] 不是对象 → 跳过（UNKNOWN）")
            continue
        conclusion = job.get("conclusion")
        status = job.get("status")
        if conclusion is None:
            warns.append(f"job「{job.get('name')}」缺 conclusion → UNKNOWN")
        steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
        failing = [s for s in steps if (s.get("conclusion") in FAIL_CONCLUSIONS)]
        log_text = _load_job_log(log_dir, job, index)
        job_failed = conclusion in FAIL_CONCLUSIONS or bool(failing)
        if job_failed:
            red_jobs += 1
            conclusions = {conclusion} | {s.get("conclusion") for s in failing}
            conclusions.discard(None)
            if failing:
                for step in failing:
                    cat = classify(step.get("name") or "", job.get("name") or "",
                                   conclusions | {step.get("conclusion")}, log_text)
                    counts[cat] += 1
                    failures.append({"job": job.get("name"), "job_index": index,
                                     "step": step.get("name"),
                                     "step_conclusion": step.get("conclusion"),
                                     "category": cat})
            else:
                cat = classify("", job.get("name") or "", conclusions, log_text)
                counts[cat] += 1
                failures.append({"job": job.get("name"), "job_index": index, "step": None,
                                 "step_conclusion": None, "category": cat})
        jobs.append({
            "index": index,
            "name": job.get("name"),
            "status": status,
            "conclusion": conclusion,
            "steps": len(steps),
            "failed_steps": [s.get("name") for s in failing],
            "duration_s": _seconds(job.get("startedAt"), job.get("completedAt")),
        })
    run_conclusion = run.get("conclusion")
    if run_conclusion == "success" and red_jobs == 0 and not failures:
        verdict = "PASS"
    elif run_conclusion is None:
        verdict = "UNKNOWN"
    else:
        verdict = "FAIL"
    return {
        "id": run.get("databaseId"),
        "name": run.get("name"),
        "workflow": run.get("workflowName"),
        "head_sha": run.get("headSha"),
        "event": run.get("event"),
        "status": run.get("status"),
        "conclusion": run_conclusion,
        "verdict": verdict,
        "jobs_total": len(jobs),
        "jobs": jobs,
        "failures": failures,
        "red_jobs": red_jobs,
        "attribution_counts": counts,
        "attribution_hits": sum(counts.values()),
        "warns": warns,
    }


def render_md(doc: dict) -> str:
    runs = doc["runs"]
    lines = [f"# CI 摘要（ci_summary v{TOOL_VERSION}；只读/离线；不调用 gh）"]
    for run in runs:
        ident = f"{run.get('name') or 'CI'} #{run.get('id')}"
        lines.append("")
        lines.append(f"## {ident}")
        sha = (run.get("head_sha") or "")[:12]
        lines.append(f"workflow `{run.get('workflow') or '?'}`｜head `{sha or '?'}`｜"
                     f"status {run.get('status') or '?'} / conclusion "
                     f"{run.get('conclusion') or '?'}｜事件 {run.get('event') or '?'}")
        lines.append(f"结论：**{run['verdict']}**（{run['jobs_total']} job；红 job "
                     f"{run['red_jobs']}；归因命中 {run['attribution_hits']}）")
        lines.append("| job | 结论 | 步骤数 | 失败 step | 时长 |")
        lines.append("|---|---|---|---|---|")
        for job in run["jobs"]:
            failed = "、".join(job["failed_steps"]) or "—"
            dur = f"{job['duration_s']}s" if job["duration_s"] is not None else "—"
            lines.append(f"| {job.get('name') or '?'} | {job.get('conclusion') or '?'} | "
                         f"{job['steps']} | {failed} | {dur} |")
        lines.append("")
        lines.append("### 失败 step 与归因")
        if run["failures"]:
            for f in run["failures"]:
                lines.append(f"- **{f['category']}**｜job「{f['job']}」｜step「{f['step'] or '（未定位）'}」"
                             f"｜{f['step_conclusion'] or '—'}")
        else:
            lines.append("（无失败 step、无归因命中）")
        counts = "；".join(f"{c} {run['attribution_counts'][c]}" for c in CATEGORIES)
        lines.append(f"归因统计：{counts}")
        if run["warns"]:
            for w in run["warns"]:
                lines.append(f"- WARN {w}")
    lines.append("")
    lines.append("口径：取消 > 超时 > 依赖安装失败 > 文档 AUTO 过期 > 测试红 > 其它；"
                 "字段缺失降级 UNKNOWN+WARN，绝不静默判绿")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# --self-test（hermetic：夹具生成假 run JSON / 假 job 日志）
# ---------------------------------------------------------------------------

def _load_fixtures():
    path = os.path.join(TOOLS_DIR, "tool_fixtures.py")
    if not os.path.isfile(path):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ci_summary_fixtures", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001
        return None
    return mod


def _quiet_main(argv: list) -> int:
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return main(argv)


def run_self_test() -> int:
    fixtures = _load_fixtures()
    if fixtures is None:
        print("[ci_summary self-test] FAIL：缺 tools/tool_fixtures.py 或导入失败")
        return 1
    results = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, detail))
        print(("[ci_summary self-test] %-22s %s %s" % (name, "PASS" if ok else "FAIL", detail)).rstrip())

    def run(td, tag, run_doc, log_text=None):
        root = os.path.join(td, tag)
        os.makedirs(root, exist_ok=True)
        inp = os.path.join(root, "run.json")
        fixtures.write_json(inp, run_doc)
        argv = ["--input", inp, "--out", os.path.join(root, "out")]
        if log_text is not None:
            logdir = os.path.join(root, "logs")
            job_id = None
            for job in run_doc.get("jobs") or []:
                if job.get("conclusion") in FAIL_CONCLUSIONS or any(
                        s.get("conclusion") in FAIL_CONCLUSIONS for s in job.get("steps") or []):
                    job_id = job.get("databaseId")
                    break
            fixtures.write_text(os.path.join(logdir, f"{job_id}.log"), log_text)
            argv += ["--log", logdir]
        rc = _quiet_main(argv)
        with open(os.path.join(root, "out", "ci_summary.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        with open(os.path.join(root, "out", "ci_summary.md"), encoding="utf-8") as fh:
            md = fh.read()
        return rc, doc, md

    def t_green(td):
        rc, doc, md = run(td, "green", fixtures.ci_green())
        r = doc["runs"][0]
        if rc != 0 or r["verdict"] != "PASS":
            return False, f"exit {rc} verdict {r['verdict']}"
        if r["jobs_total"] != 2 or r["red_jobs"] != 0 or r["attribution_hits"] != 0:
            return False, f"jobs {r['jobs_total']} red {r['red_jobs']} hits {r['attribution_hits']}"
        if "| job | 结论 |" not in md:
            return False, "md 缺 job 表"
        return True, f"2 job 全 success、红 0、归因命中 0；md {len(md.splitlines())} 行"

    def case_check(case, expected):
        def fn(td):
            rc, doc, _ = run(td, f"red-{case}", fixtures.ci_red(case),
                             fixtures.failing_job_log(case))
            r = doc["runs"][0]
            if rc != 1 or r["verdict"] != "FAIL":
                return False, f"exit {rc} verdict {r['verdict']}"
            got = [f["category"] for f in r["failures"]]
            if expected not in got:
                return False, f"归因 {got} 未含 {expected}"
            return True, f"红 job → {got}"
        return fn

    def t_timeout_without_log(td):
        # 无日志时靠 step conclusion=timed_out 判超时
        rc, doc, _ = run(td, "red-timeout-nolog", fixtures.ci_red("timeout"))
        if rc != 1:
            return False, f"exit {rc}"
        got = [f["category"] for f in doc["runs"][0]["failures"]]
        if "超时" not in got:
            return False, f"归因 {got}"
        return True, "仅用 timed_out conclusion 也能判超时"

    def t_degrade_unknown(td):
        broken = fixtures.ci_green()
        for job in broken["jobs"]:
            job.pop("conclusion", None)
            for step in job.get("steps") or []:
                step.pop("conclusion", None)
        broken.pop("conclusion", None)
        rc, doc, _ = run(td, "degraded", broken)
        r = doc["runs"][0]
        if r["verdict"] != "UNKNOWN":
            return False, f"缺 conclusion 未降级 UNKNOWN（{r['verdict']}）"
        if not r["warns"]:
            return False, "未产生 WARN"
        return True, f"缺 conclusion → UNKNOWN + {len(r['warns'])} 条 WARN"

    def t_determinism(td):
        _, d1, m1 = run(td, "det1", fixtures.ci_red("tests"), fixtures.failing_job_log("tests"))
        _, d2, m2 = run(td, "det2", fixtures.ci_red("tests"), fixtures.failing_job_log("tests"))
        if m1 != m2:
            return False, "ci_summary.md 两次不同"
        a = {k: v for k, v in d1.items() if k != "generated_at"}
        b = {k: v for k, v in d2.items() if k != "generated_at"}
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            return False, "ci_summary.json 除时间戳外有差异"
        return True, "两遍逐字节相同（除 generated_at）"

    print("[ci_summary self-test] hermetic 夹具（tempfile；不联网、不调用 gh）")
    with tempfile_dir() as td:
        def mk(case, expected):
            return lambda: case_check(case, expected)(td)

        check("green_run", lambda: t_green(td))
        check("red_tests", mk("tests", "测试红"))
        check("red_deps", mk("deps", "依赖安装失败"))
        check("red_docs_auto", mk("docs_auto", "文档 AUTO 过期"))
        check("red_timeout", mk("timeout", "超时"))
        check("red_cancelled", mk("cancelled", "取消"))
        check("red_other", mk("other", "其它"))
        check("timeout_without_log", lambda: t_timeout_without_log(td))
        check("degrade_unknown", lambda: t_degrade_unknown(td))
        check("determinism", lambda: t_determinism(td))

    ok = sum(1 for _, o, _ in results if o)
    print(f"[ci_summary self-test] {ok}/{len(results)} PASS"
          + ("" if ok == len(results) else " ← 有失败"))
    return 0 if ok == len(results) else 1


def tempfile_dir():
    import tempfile
    return tempfile.TemporaryDirectory(prefix="ci-summary-selftest-")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        description="CI 结果摘要器（B-145 波1）：gh run view 落盘 JSON → ci_summary.md/json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python3 tools/ci_summary.py --input <run.json> --out <outdir> [--log <logsdir>]")
    ap.add_argument("--input", help="gh run view --json 落盘 JSON（单个 run 或 run 列表）")
    ap.add_argument("--out", help="输出目录（ci_summary.md + ci_summary.json）")
    ap.add_argument("--log", help="可选：job 日志目录（<databaseId>.log 或 <job 名>.log）")
    ap.add_argument("--self-test", action="store_true", help="跑 hermetic 自检（≥5 用例）")
    ap.add_argument("--version", action="version", version=f"ci_summary {TOOL_VERSION}")
    args = ap.parse_args(argv)

    if args.self_test:
        sys.dont_write_bytecode = True
        return run_self_test()
    if not args.input or not args.out:
        ap.error("--input 与 --out 为必填（或使用 --self-test）")

    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[ci_summary] 输入不可用：{args.input}（{type(exc).__name__}）", file=sys.stderr)
        return 2
    runs_in = raw if isinstance(raw, list) else [raw]
    runs = [summarize_run(r, args.log or "") for r in runs_in if isinstance(r, dict)]
    if not runs:
        print("[ci_summary] 输入里没有 run 对象", file=sys.stderr)
        return 2
    doc = {
        "tool": "ci_summary",
        "version": TOOL_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "input": os.path.basename(args.input),
        "runs": runs,
        "summary": {
            "runs": len(runs),
            "failed_runs": sum(1 for r in runs if r["verdict"] == "FAIL"),
            "unknown_runs": sum(1 for r in runs if r["verdict"] == "UNKNOWN"),
            "red_jobs": sum(r["red_jobs"] for r in runs),
            "attribution_counts": {c: sum(r["attribution_counts"][c] for r in runs)
                                   for c in CATEGORIES},
        },
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "ci_summary.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_md(doc))
    with open(os.path.join(args.out, "ci_summary.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    s = doc["summary"]
    print(f"[ci_summary] {len(runs)} run → {args.out}/ci_summary.md + ci_summary.json；"
          f"红 run {s['failed_runs']} / 红 job {s['red_jobs']} / 归因命中 "
          f"{sum(s['attribution_counts'].values())}")
    if s["failed_runs"]:
        return 1
    if s["unknown_runs"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
