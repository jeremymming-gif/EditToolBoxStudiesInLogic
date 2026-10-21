#!/usr/bin/env python3
"""验收证据聚合器（B-145 波1 / 批78）——只读、离线、确定性、无第三方依赖

读 **门禁计划**（`gate_plan.py` 产出的 `gate_plan.json`）+ **证据目录**（各门禁的
日志/JSON 落盘）→ 校验每个**必跑**门禁的证据是否**存在**、关键字段能否**解析出数字**
→ 生成 `accept.md`（≤40 行，可直接进 ACCEPT/EVIDENCE）与 `accept.json`（机读）。

判据（绝不静默 PASS）
--------------------
- 必跑门禁**没有**证据文件 → `FAIL`（并在「缺证据」节逐条列出）。
- 有证据但**关键字段解析不出** → `UNKNOWN`（不是 PASS）。
- 证据表明失败（写后仍有红套件 / lint 有 error / docs 有失败项 / Swift 有 FAIL /
  gen_docs 写失败）→ `FAIL`。
- 写前（`*_pre`）门禁的红是「既有基线」：记 `WARN` 并在摘要里点名（不吞掉、不算通过）。
- 可跳门禁一律列出命中规则 id；延后门禁单列（`ci_watch` 待 push 后必跑）。
- 退出码字段兼容三种等价写法（各取**最后一个**匹配）：`EXIT=<n>` > `rc=<n>` >
  `exit code:<n>`；**三种都没有** → 该门禁仍判 `UNKNOWN`（不降低严格性）。
- `seconds`（`elapsed=`）是**可空**字段：拿不到就记 null + WARN，**不判 UNKNOWN**。
- **cwd 只呈现、不参与判据**（B-149）：按 gate 名从 `gate_plan.json` 读 `cwd`
  （`public_root`=公开仓根 / `engine`=公开仓 `Engine/`）并写进 `accept.md` 表；
  老计划缺 `cwd` → 该列留空（`—`）+ 汇总一条 WARN，**绝不**据此判 PASS/FAIL/UNKNOWN。
- 退出码：`PASS`/`PASS+WARN` = 0；`FAIL` = 1；`UNKNOWN` = 2（便于 CI/脚本区分）。

用法
----
  python3 tools/batch_accept.py --batch 77 --gates <gate_plan.json> --evidence <证据目录> \
      --out <输出目录> [--kpi <batch-kpi.json>] [--map <映射.json>] [--self-test]

证据映射
--------
默认映射（`DEFAULT_GATE_SPECS`）按门禁 id 给「文件名模式 + 解析器种类」；文件名模式
用 `fnmatch` 在证据目录**顶层**匹配，先按模式顺序、再按名字排序取**第一个**命中。
格式漂移时用 `--map` 覆盖/追加，不必改代码：

  {"gates": {"lint": {"patterns": ["my-lint-*.log"], "kind": "lint"}}}

kind 取值：`full` / `gen_docs` / `lint` / `docs` / `swift` / `corpus` / `audit` /
`build` / `ci` / `generic`。

证据格式约定（B-152；**门禁跑完按此落盘，`batch_accept` 才能解析**）
---------------------------------------------------------------
1. **每条命令日志的最后一行 `EXIT=<rc>`**（`elapsed=<秒>` 可同行或另起）。
2. **通用键值行**：证据日志里写单行 `key: value` 或 `key=value` 即可被通用解析器取值，
   `key` 用下方各门禁的字段名（如 `diff_rows: 0`）。数字/浮点/布尔按字段类型转换；
   取不到就是 `None` → 该门禁判 `UNKNOWN`（不是 PASS）。
3. `gen_docs`：AUTO 计数写在 `*auto-numbers*.txt`，正文含
   `AUTO测试行: N 套件 / M 项断言 / FAIL K 行` + `inputs_sha256: <hex>`；
   README 是否改动由 `*-readme-diff*.log` 判（含 `@@` 即改动，空 diff = 未改动）。
4. `lint`：日志内必须有 `db_sha_before: <64 hex>` 与 `db_sha_after: <64 hex>`
   （判库未被改写；真实 bibdb lint 默认不打印这两行 → **由验收人用 `printf` 追加到日志末尾**）。
5. `corpus_two`：两条 `shasum` 输出行（`<64 hex>  out/corpus/corpus.csv`，按出现顺序 = pass1/pass2），
   或键值行 `pass1_sha:` / `pass2_sha:` / `diff_rows:`。
6. `build`：`cmp=28/28`、`o5=1`、`app_version=…`、`artifact_sha=…`、`engine_py_sha=…`、
   `manifest=…`、`elapsed=Ns`。
7. `ci_watch`：`gh run view … --json …` 的落盘 JSON（顶层 run 或 `{"runs": [...]}`；
   `docs` 工作流按 `workflowName`/`name` 含 `docs`/`文档` 识别），或键值行
   `ci_run_id:` / `ci_conclusion:` / `docs_run_id:` / `docs_conclusion:`。
   **`docs` 工作流有 `paths` 过滤（只在 `docs/**` 改动时跑）→ 无 docs run 属正常态**：
   `docs_run_id`/`docs_conclusion` 记 null + WARN，**不判 UNKNOWN**（`ci_conclusion` 仍硬判）。
8. **同批两轮证据**（如 `*-dev.log` 与提交后 `*-post.log`）会因「最短名优先」互相遮蔽
   → 用 `--map` 显式声明权威轮的文件名模式（例：
   `{"gates": {"lint": {"patterns": ["07-lint-post*.log", "07-lint*.log"], "kind": "lint"}}}`）。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import tempfile
import time

TOOL_VERSION = "1.2.0"
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
MD_LINE_LIMIT = 40

PASS, WARN, FAIL, UNKNOWN = "PASS", "WARN", "FAIL", "UNKNOWN"
# cwd 元数据的合法取值（B-149；仅呈现，不参与判据）
CWD_VALUES = ("public_root", "engine")

# 门禁 id -> 证据发现方式 + 解析器种类（可被 --map 覆盖）
DEFAULT_GATE_SPECS = {
    "full_pre": {"kind": "full",
                 "patterns": ["*full-pre*.log", "*full_pre*.log", "*fullpre*.log"]},
    "full_post": {"kind": "full",
                  "patterns": ["*full-post*.log", "*full_post*.log", "*fullpost*.log"]},
    "gen_docs_write": {"kind": "gen_docs",
                       "patterns": ["*gen-docs-write*.log", "*gen_docs_write*.log",
                                    "*gen-docs*.log"],
                       "aux": {"auto": ["*auto-numbers*.txt", "*auto_numbers*.txt"],
                               "readme_diff": ["*readme-diff*.log", "*readme_diff*.log"],
                               "final_state": ["*final-state*.log", "*final_state*.log"]}},
    "lint": {"kind": "lint", "patterns": ["*lint*.log"]},
    "docs": {"kind": "docs", "patterns": ["*docs.log", "*docs-consistency*.log"]},
    "swift": {"kind": "swift", "patterns": ["*swift*.log"]},
    "corpus_two": {"kind": "corpus", "patterns": ["*corpus*.log", "*corpus*.txt"]},
    "docx_audit": {"kind": "audit", "patterns": ["*audit*.log", "*audit*.txt"]},
    "build": {"kind": "build", "patterns": ["*build*.log"]},
    "ci_watch": {"kind": "ci", "patterns": ["*ci*.json"]},
}
# 写前基线门禁：红不判 FAIL（记 WARN）；写后门禁红 = FAIL
PRE_GATE_IDS = {"full_pre"}


# ---------------------------------------------------------------------------
# 读取 / 发现 / 解析
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _load_json(path: str, warnings: list, what: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{what} 读取失败（{type(exc).__name__}）")
        return None


def _find_evidence(ev_dir: str, patterns) -> str:
    """按模式顺序找证据；同一模式多命中时取『最短名』（如 `x-write.log` 优先于
    `x-write-prestate.log`），再按名字排序保证确定性。"""
    if not patterns or not os.path.isdir(ev_dir):
        return None
    names = sorted(n for n in os.listdir(ev_dir) if os.path.isfile(os.path.join(ev_dir, n)))
    for pat in patterns:
        hits = [n for n in names if fnmatch.fnmatch(n, pat)]
        if hits:
            return os.path.join(ev_dir, min(hits, key=lambda n: (len(n), n)))
    return None


# 退出码的三种等价写法（优先级从高到低；同一写法内取最后一个匹配）
RC_PATTERNS = (r"^EXIT=(\d+)", r"^rc=(\d+)", r"^exit code:\s*(\d+)")
# 可缺字段：缺失时记 null + WARN，不降级 UNKNOWN（seconds 可从别处/--map 取）
SOFT_FIELDS = {"seconds"}
# 按门禁的可缺字段（B-152）：`docs` 工作流有 paths 过滤（只在 docs/ 改动时跑）→
# 无 docs run 属正常态，不是解析失败（ci_conclusion 仍硬判）
SOFT_FIELDS_BY_GATE = {"ci_watch": {"docs_run_id", "docs_conclusion"}}


def _rc(text: str):
    """按 EXIT= > rc= > exit code: 的优先级取退出码；同一写法取最后一个匹配。"""
    for pattern in RC_PATTERNS:
        value = _num(pattern, text, last=True)
        if value is not None:
            return value
    return None


def _num(pattern: str, text: str, group: int = 1, last: bool = False):
    matches = re.findall(pattern, text, re.M)
    if not matches:
        return None
    val = matches[-1] if last else matches[0]
    if isinstance(val, tuple):
        val = val[group - 1]
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _float(pattern: str, text: str, group: int = 1):
    m = re.search(pattern, text, re.M)
    if not m:
        return None
    try:
        return float(m.group(group))
    except (TypeError, ValueError):
        return None


# 通用键值行（B-152 证据格式约定 §2）：`key: value` / `key=value`，值不跨行
def _kv_value(text: str, key: str, conv: str = "str"):
    m = re.search(r"^\s*" + re.escape(key) + r"\s*[:=]\s*(\S+)\s*$", text, re.M)
    return _convert(m.group(1), conv) if m else None


def _convert(value, conv: str):
    """按字段类型转换；转不出来返回 None（→ 该字段缺失，仍判 UNKNOWN）。"""
    if value is None:
        return None
    try:
        if conv == "int":
            return int(str(value).replace(",", ""))
        if conv == "float":
            return float(str(value))
    except (TypeError, ValueError):
        return None
    if conv == "bool":
        return str(value).strip().lower() in ("1", "true", "yes", "y", "是", "一致", "ok")
    return str(value)


def _field_value(text: str, field: str, patterns, conv: str = "str"):
    """先按字段专用正则、再按通用键值行约定取值。"""
    for pat in patterns or ():
        m = re.search(pat, text, re.M)
        if m:
            got = _convert(m.group(1) if m.groups() else m.group(0), conv)
            if got is not None:
                return got
    return _kv_value(text, field, conv)


SHA64 = r"([0-9a-f]{64})"

# generic 门禁的最小可判字段（B-152）：字段名 = 门禁计划 evidence 里的名字
GENERIC_FIELD_SPECS = {
    "corpus_two": {
        "sha_pass1": {"conv": "str", "patterns": [r"^pass1_sha\s*[:=]\s*" + SHA64]},
        "sha_pass2": {"conv": "str", "patterns": [r"^pass2_sha\s*[:=]\s*" + SHA64]},
        "diff_vs_baseline": {"conv": "str",
                             "patterns": [r"^diff_vs_baseline\s*[:=]\s*(\S+)"]},
        "diff_rows": {"conv": "int", "patterns": [r"^diff_rows\s*[:=]\s*(\d+)"]},
    },
    "docx_audit": {
        "success": {"conv": "int", "patterns": [r"^success\s*[:=]\s*(\d+)"]},
        "fail": {"conv": "int", "patterns": [r"^fail\s*[:=]\s*(\d+)"]},
        "csv_sha": {"conv": "str", "patterns": [r"^csv_sha\s*[:=]\s*" + SHA64]},
        "diff_vs_notes_baseline": {"conv": "str",
                                   "patterns": [r"^diff_vs_notes_baseline\s*[:=]\s*(\S+)"]},
    },
    "build": {
        "cmp_28": {"conv": "str", "patterns": [r"^cmp(?:_28)?\s*[:=]\s*(\d+/\d+)"]},
        "o5": {"conv": "int", "patterns": [r"^o5\s*[:=]\s*(\d+)"]},
        "app_version": {"conv": "str", "patterns": [r"^app_version\s*[:=]\s*(\S+)"]},
        "artifact_sha": {"conv": "str", "patterns": [r"^artifact_sha\s*[:=]\s*" + SHA64]},
        "engine_py_sha": {"conv": "str", "patterns": [r"^engine_py_sha\s*[:=]\s*" + SHA64]},
        "manifest": {"conv": "str", "patterns": [r"^manifest\s*[:=]\s*(\S+)"]},
    },
    "ci_watch": {
        "ci_run_id": {"conv": "int", "patterns": [r"^ci_run_id\s*[:=]\s*(\d+)"]},
        "ci_conclusion": {"conv": "str", "patterns": [r"^ci_conclusion\s*[:=]\s*(\S+)"]},
        "docs_run_id": {"conv": "int", "patterns": [r"^docs_run_id\s*[:=]\s*(\d+)"]},
        "docs_conclusion": {"conv": "str",
                            "patterns": [r"^docs_conclusion\s*[:=]\s*(\S+)"]},
    },
}


def parse_full(text: str, path: str) -> dict:
    seconds = _float(r"汇总：\d+ 套件，红 \d+[^\n]*?耗时 ([\d.]+) 秒", text)
    return {
        "suites": _num(r"汇总：(\d+) 套件", text),
        "red": _num(r"汇总：\d+ 套件，红 (\d+)", text),
        "seconds": seconds,
        "log": os.path.basename(path),
        "rc": _rc(text),
    }


def parse_gen_docs(text: str, path: str, aux: dict) -> dict:
    out = {"rc": _rc(text),
           "log": os.path.basename(path),
           "seconds": _float(r"elapsed=([\d.]+)s", text)}
    auto_path = aux.get("auto")
    if auto_path:
        auto = _read_text(auto_path)
        out["auto_suites"] = _num(r"AUTO测试行:\s*(\d+) 套件", auto)
        out["auto_assertions"] = _num(r"AUTO测试行:\s*\d+ 套件 / (\d+) 项断言", auto)
        out["auto_fail"] = _num(r"AUTO测试行:[^\n]*FAIL (\d+) 行", auto)
        m = re.search(r"inputs_sha256:\s*([0-9a-f]{6,64})", auto)
        out["inputs_sha"] = m.group(1)[:12] if m else None
        out["auto_numbers_file"] = os.path.basename(auto_path)
    if out.get("inputs_sha") is None:
        # 真实 gen_docs 写模式日志自带「inputs sha <12 hex>」（B-152 约定 §3）
        m = re.search(r"inputs sha\s*([0-9a-f]{6,64})", text)
        if m:
            out["inputs_sha"] = m.group(1)[:12]
    diff_path = aux.get("readme_diff")
    # 幂等写（B-159）：clean 树写模式输出「AUTO 块已是最新，未改动 README.md」→ 0 diff 是**正确**结果
    out["readme_noop"] = bool(re.search(r"AUTO 块已是最新|未改动 README", text))
    if diff_path:
        diff = _read_text(diff_path)
        # 有 hunk（`@@`）即说明 README 真被改；空 diff 文件 = 未改动
        out["readme_changed"] = "@@" in diff
        out["readme_diff_file"] = os.path.basename(diff_path)
        stat = None
        final_path = aux.get("final_state")
        for text_src in ([_read_text(final_path)] if final_path else []) + [diff]:
            stat = re.search(r"1 file changed, (\d+) insertions?\(\+\), (\d+) deletions?\(-\)",
                             text_src)
            if stat:
                break
        if stat:
            out["readme_insertions"] = int(stat.group(1))
            out["readme_deletions"] = int(stat.group(2))
        hunks = re.search(r"hunks=(\d+)", _read_text(final_path) if final_path else diff)
        if hunks:
            out["readme_hunks"] = int(hunks.group(1))
    return out


def parse_lint(text: str, path: str) -> dict:
    before = re.search(r"^db_sha_before:\s*([0-9a-f]{64})", text, re.M)
    after = re.search(r"^db_sha_after:\s*([0-9a-f]{64})", text, re.M)
    return {
        "rc": _rc(text),
        "errors": _num(r"error (\d+) 项，warning \d+ 项", text),
        "warnings": _num(r"error \d+ 项，warning (\d+) 项", text),
        "records": _num(r"条目 (\d+)（", text),
        "db_sha_before": before.group(1) if before else None,
        "db_sha_after": after.group(1) if after else None,
        "log": os.path.basename(path),
    }


def parse_docs(text: str, path: str) -> dict:
    return {
        "rc": _rc(text),
        "failures": _num(r"失败 (\d+) 项", text, last=True),
        "log": os.path.basename(path),
    }


def parse_swift(text: str, path: str) -> dict:
    pairs = re.findall(r"SUMMARY:\s*(\d+) PASS / (\d+) FAIL", text)
    if not pairs:
        return {"beds": None, "fail": None, "pass_total": None, "rc": _rc(text),
                "log": os.path.basename(path)}
    return {
        "beds": len(pairs),
        "pass_total": sum(int(p) for p, _ in pairs),
        "fail": sum(int(f) for _, f in pairs),
        "rc": _rc(text),
        "log": os.path.basename(path),
    }


def parse_corpus(text: str, path: str, aux: dict) -> dict:
    """corpus 两遍确定性（B-152）：位置式 `shasum` 行 + 键值行 + `diff_rows`。"""
    spec = GENERIC_FIELD_SPECS["corpus_two"]
    out = {"log": os.path.basename(path), "rc": _rc(text)}
    shas = re.findall(r"^([0-9a-f]{64})\s+\S*corpus(?:\.csv)?", text, re.M)
    for field in ("sha_pass1", "sha_pass2"):
        out[field] = _field_value(text, field, spec[field]["patterns"], spec[field]["conv"])
    if out["sha_pass1"] is None and len(shas) >= 1:
        out["sha_pass1"] = shas[0]
    if out["sha_pass2"] is None and len(shas) >= 2:
        out["sha_pass2"] = shas[1]
    for field in ("diff_vs_baseline", "diff_rows"):
        out[field] = _field_value(text, field, spec[field]["patterns"], spec[field]["conv"])
    if out["diff_rows"] is None and out["diff_vs_baseline"]:
        m = re.search(r"(\d+)\s*行|(\d+)", str(out["diff_vs_baseline"]))
        if m:
            out["diff_rows"] = int(m.group(1) or m.group(2))
    out["equal"] = (out["sha_pass1"] is not None and out["sha_pass1"] == out["sha_pass2"])
    return out


def parse_docx_audit(text: str, path: str, aux: dict) -> dict:
    """全库普查（B-152）：`success`/`fail`/`csv_sha`/与笔记仓基线对照。"""
    spec = GENERIC_FIELD_SPECS["docx_audit"]
    out = {"log": os.path.basename(path), "rc": _rc(text)}
    for field, meta in spec.items():
        out[field] = _field_value(text, field, meta["patterns"], meta["conv"])
    if out["success"] is None:      # 真实日志形态：`成功 132 篇 / 失败 0 篇`
        m = re.search(r"成功\s*(\d+)\s*篇", text)
        if m:
            out["success"] = int(m.group(1))
    if out["fail"] is None:
        m = re.search(r"失败\s*(\d+)\s*篇", text)
        if m:
            out["fail"] = int(m.group(1))
    return out


def parse_build(text: str, path: str, aux: dict) -> dict:
    """打包（B-152）：`cmp=28/28`、`o5=1`、`app_version`、产物 sha、`elapsed`。"""
    spec = GENERIC_FIELD_SPECS["build"]
    out = {"log": os.path.basename(path), "rc": _rc(text),
           "seconds": _float(r"elapsed=([\d.]+)s", text)}
    for field, meta in spec.items():
        out[field] = _field_value(text, field, meta["patterns"], meta["conv"])
    return out


def parse_ci(text: str, path: str, aux: dict) -> dict:
    """CI 结论（B-152）：`gh run view --json` 落盘 JSON，或 `ci_run_id:`/`ci_conclusion:` 键值行。"""
    spec = GENERIC_FIELD_SPECS["ci_watch"]
    out = {"log": os.path.basename(path), "rc": _rc(text)}
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        doc = None
    runs = doc if isinstance(doc, list) else (doc.get("runs") if isinstance(doc, dict)
                                              and isinstance(doc.get("runs"), list) else [doc])
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        name = str(run.get("workflowName") or run.get("name") or "").lower()
        rid = run.get("databaseId", run.get("id"))
        rid = int(rid) if str(rid).isdigit() else rid
        conclusion = run.get("conclusion")
        if "docs" in name or "文档" in name:
            out.setdefault("docs_run_id", rid)
            out.setdefault("docs_conclusion", conclusion)
        elif out.get("ci_run_id") is None and rid is not None:
            out["ci_run_id"] = rid
            out["ci_conclusion"] = conclusion
    for field, meta in spec.items():
        if out.get(field) is None:
            out[field] = _field_value(text, field, meta["patterns"], meta["conv"])
    return out


PARSERS = {
    "full": lambda text, path, aux: parse_full(text, path),
    "gen_docs": parse_gen_docs,
    "lint": lambda text, path, aux: parse_lint(text, path),
    "docs": lambda text, path, aux: parse_docs(text, path),
    "swift": lambda text, path, aux: parse_swift(text, path),
    "corpus": parse_corpus,
    "audit": parse_docx_audit,
    "build": parse_build,
    "ci": parse_ci,
    "generic": lambda text, path, aux: {"log": os.path.basename(path), "rc": _rc(text)},
}

# 「这条证据字段是否解析出数字」时用的字段名归一（evidence 声明名 -> 解析键）
FIELD_ALIASES = {
    "log": "log",
    "rc": "rc",
    "suites": "suites",
    "red": "red",
    "seconds": "seconds",
    "inputs_sha": "inputs_sha",
    "auto_suites": "auto_suites",
    "auto_assertions": "auto_assertions",
    "auto_fail": "auto_fail",
    "readme_changed": "readme_changed",
    "errors": "errors",
    "warnings": "warnings",
    "records": "records",
    "db_sha_before": "db_sha_before",
    "db_sha_after": "db_sha_after",
    "failures": "failures",
    "beds": "beds",
    "fail": "fail",
    "pass_total": "pass_total",
    # generic 门禁（B-152）：同名直通 + gates.json/夹具里的历史别名
    "sha_pass1": "sha_pass1",
    "sha_pass2": "sha_pass2",
    "diff_vs_baseline": "diff_vs_baseline",
    "diff_rows": "diff_rows",
    "vs_baseline": "diff_vs_baseline",
    "diff_vs_notes_baseline": "diff_vs_notes_baseline",
    "success": "success",
    "csv_sha": "csv_sha",
    "cmp_28": "cmp_28",
    "cmp": "cmp_28",
    "o5": "o5",
    "app_version": "app_version",
    "artifact_sha": "artifact_sha",
    "engine_py_sha": "engine_py_sha",
    "manifest": "manifest",
    "ci_run_id": "ci_run_id",
    "ci_conclusion": "ci_conclusion",
    "docs_run_id": "docs_run_id",
    "docs_conclusion": "docs_conclusion",
}


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def judge_gate(gate: dict, fields: dict, missing: list) -> dict:
    gid = gate["id"]
    gid_status = gate["status"]
    verdict, notes = PASS, []
    red = fields.get("red")
    rc = fields.get("rc")

    if gid in PRE_GATE_IDS and red:
        verdict = WARN
        notes.append(f"写前基线红 {red} 套（现状记录；验收判据=写后 0 红）")
    if gid == "full_post":
        if red:
            verdict = FAIL
            notes.append(f"写后仍有 {red} 套红")
        elif rc is not None and rc != 0:
            verdict = FAIL
            notes.append(f"写后退出码 {rc}")
    if gid == "gen_docs_write":
        if rc is not None and rc != 0:
            verdict, _ = FAIL, notes.append(f"gen_docs 写失败（exit {rc}）")
        if fields.get("auto_fail"):
            verdict = FAIL
            notes.append(f"AUTO 测试行 FAIL {fields['auto_fail']} 行")
        if (fields.get("readme_changed") is False and fields.get("auto_numbers_file")
                and not fields.get("readme_noop")):
            verdict = FAIL
            notes.append("gen_docs 写完 README 却未见 diff（写失败）")
        if fields.get("readme_noop"):
            notes.append("幂等写：AUTO 块已是最新 → 0 diff 属预期（B-159）")
    if gid == "lint":
        if fields.get("errors"):
            verdict = FAIL
            notes.append(f"lint error {fields['errors']} 项")
        if rc is not None and rc != 0:
            verdict = FAIL
            notes.append(f"lint 退出码 {rc}")
    if gid == "docs":
        if fields.get("failures"):
            verdict = FAIL
            notes.append(f"文档看门狗失败 {fields['failures']} 项")
        if rc is not None and rc != 0:
            verdict = FAIL
            notes.append(f"文档看门狗退出码 {rc}")
    if gid == "swift":
        if fields.get("fail"):
            verdict = FAIL
            notes.append(f"Swift FAIL {fields['fail']}")
        if rc is not None and rc != 0:
            verdict = FAIL
            notes.append(f"Swift 退出码 {rc}")
    # ---- generic 门禁（B-152）：最小可判证据，仍「绝不静默 PASS」 ----
    if gid == "corpus_two":
        p1, p2 = fields.get("sha_pass1"), fields.get("sha_pass2")
        if p1 is not None and p2 is not None and p1 != p2:
            verdict = FAIL
            notes.append(f"两遍 corpus sha 不一致（{p1[:12]} ≠ {p2[:12]}）→ 非确定性回归")
        rows = fields.get("diff_rows")
        if isinstance(rows, int) and rows > 0:
            verdict = FAIL
            notes.append(f"与冻结基线 diff {rows} 行（预期 0；变坏迁移=0 纪律）")
        if rc is not None and rc != 0:
            verdict = FAIL
            notes.append(f"corpus 退出码 {rc}")
    if gid == "docx_audit":
        if fields.get("fail"):
            verdict = FAIL
            notes.append(f"docx_audit 失败 {fields['fail']} 篇")
        if rc is not None and rc != 0:
            verdict = FAIL
            notes.append(f"docx_audit 退出码 {rc}")
    if gid == "build":
        cmp_val = fields.get("cmp_28")
        if cmp_val is not None and str(cmp_val) != "28/28":
            verdict = FAIL
            notes.append(f"包内 cmp {cmp_val} ≠ 28/28")
        o5 = fields.get("o5")
        if o5 is not None and o5 != 1:
            verdict = FAIL
            notes.append(f"o5={o5} ≠ 1")
        if rc is not None and rc != 0:
            verdict = FAIL
            notes.append(f"build 退出码 {rc}")
    if gid == "ci_watch":
        conclusion = fields.get("ci_conclusion")
        if conclusion is not None and str(conclusion) != "success":
            verdict = FAIL
            notes.append(f"CI 结论 {conclusion} ≠ success")
        docs_conclusion = fields.get("docs_conclusion")
        if docs_conclusion is not None and str(docs_conclusion) != "success":
            verdict = FAIL
            notes.append(f"docs 工作流结论 {docs_conclusion} ≠ success")

    if verdict != FAIL and missing:
        verdict = UNKNOWN
        notes.append("字段缺失/解析不出：" + ", ".join(missing))
    if verdict == PASS and gid_status != "required":
        notes.append("（非必跑）")
    return {"verdict": verdict, "notes": notes}


def _clean_sha(value):
    return value if isinstance(value, str) else None


def build_accept(plan: dict, ev_dir: str, specs: dict, warnings: list) -> dict:
    gates = [g for g in (plan.get("gates") or []) if isinstance(g, dict)]
    rows, skipped, deferred = [], [], []
    no_cwd, bad_cwd = [], []
    for gate in gates:
        gid = str(gate.get("id") or "")
        status = str(gate.get("status") or "")
        spec = dict(specs.get(gid) or {"kind": "generic", "patterns": []})
        cwd = gate.get("cwd")
        entry = {"id": gid, "status": status, "declared_evidence": list(gate.get("evidence") or []),
                 "required_when": list(gate.get("required_when") or []),
                 "skip_when": list(gate.get("skip_when") or []),
                 "rules": [r.get("rule") for r in (gate.get("reasons") or []) if isinstance(r, dict)],
                 "last": gate.get("last") or {},
                 "cmd": gate.get("cmd") or "",
                 # cwd 仅呈现（B-149）：缺失=空白，绝不参与 PASS/FAIL/UNKNOWN 判定
                 "cwd": cwd if isinstance(cwd, str) else ""}
        if not entry["cwd"]:
            no_cwd.append(gid)
        elif entry["cwd"] not in CWD_VALUES:
            bad_cwd.append(f"{gid}={entry['cwd']}")
        if status == "skipped":
            entry.update({"verdict": "SKIP", "evidence": None, "fields": {}, "missing": [],
                          "notes": [f"规则 id：{r.get('rule')}" for r in (gate.get("reasons") or [])
                                    if isinstance(r, dict)]})
            skipped.append(entry)
            continue
        if status == "deferred":
            entry.update({"verdict": "DEFER", "evidence": None, "fields": {}, "missing": [],
                          "notes": [f"规则 id：{r.get('rule')}" for r in (gate.get("reasons") or [])
                                    if isinstance(r, dict)]})
            deferred.append(entry)
            continue

        path = _find_evidence(ev_dir, spec.get("patterns"))
        aux = {}
        if path and spec.get("kind") == "gen_docs":
            for key, pats in (spec.get("aux") or {}).items():
                aux[key] = _find_evidence(ev_dir, pats)
        if not path:
            entry.update({"verdict": FAIL, "evidence": None, "fields": {}, "missing": [],
                          "notes": [f"缺证据文件（模式 {spec.get('patterns') or '（无映射）'}；"
                                    f"必跑门禁无证据 → FAIL）"]})
            rows.append(entry)
            continue

        text = _read_text(path)
        try:
            fields = PARSERS[spec.get("kind", "generic")](text, path, aux)
        except Exception as exc:  # noqa: BLE001 —— 解析异常一律降级 UNKNOWN，绝不当 PASS
            fields = {"log": os.path.basename(path)}
            warnings.append(f"{gid} 解析异常（{type(exc).__name__}）")
        declared = [FIELD_ALIASES.get(f, f) for f in entry["declared_evidence"]]
        soft_ok = SOFT_FIELDS | SOFT_FIELDS_BY_GATE.get(gid, set())
        missing = [f for f in declared if fields.get(f) is None]
        soft = [f for f in missing if f in soft_ok]
        hard = [f for f in missing if f not in soft_ok]
        judged = judge_gate(gate, fields, hard)
        if soft and judged["verdict"] != FAIL:
            judged["notes"].append("可缺字段记 null（不判 UNKNOWN）：" + ", ".join(soft))
            warnings.append(f"{gid}：{'、'.join(soft)} 缺失 → 记 null + WARN"
                            "（rc 等关键字段缺失仍判 UNKNOWN，严格性不变）")
        entry.update({"verdict": judged["verdict"], "evidence": os.path.basename(path),
                      "aux": {k: (os.path.basename(v) if v else None) for k, v in aux.items()},
                      "fields": {k: v for k, v in fields.items() if _clean_sha(v) is not None
                                 or isinstance(v, (int, float, bool))},
                      "missing": hard, "soft_missing": soft, "notes": judged["notes"]})
        rows.append(entry)

    # cwd 只呈现：缺失/非法都只汇总一条 WARN，绝不改 PASS/FAIL/UNKNOWN
    if no_cwd:
        warnings.append("未声明 cwd 的门禁：" + ", ".join(no_cwd)
                        + "（老 gate_plan.json → accept.md 的 cwd 列留空；仅呈现，不影响判定）")
    if bad_cwd:
        warnings.append("cwd 取值不在 public_root/engine：" + ", ".join(bad_cwd)
                        + "（照原样呈现，不参与判定）")

    counts = {PASS: 0, WARN: 0, FAIL: 0, UNKNOWN: 0}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    if counts[FAIL]:
        overall = FAIL
    elif counts[UNKNOWN]:
        overall = UNKNOWN
    elif counts[WARN]:
        overall = f"{PASS}（含 {counts[WARN]} 项 WARN）"
    else:
        overall = PASS
    return {"gates": rows, "skipped": skipped, "deferred": deferred,
            "counts": counts, "verdict": overall,
            "cwd_missing": no_cwd,
            "plan_summary": plan.get("summary") or {}}


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _fmt(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, str) and len(value) > 12:
        return value[:12]
    return str(value)


def _delta(cur, last):
    if cur is None or not isinstance(last, (int, float)) or isinstance(last, bool):
        return ""
    if isinstance(cur, bool):
        return ""
    diff = cur - last
    sign = "+" if diff > 0 else ""
    return f"（上批 {last}；{sign}{diff:g}）"


def render_md(doc: dict, batch: int, plan_path: str, ev_dir: str, kpi: dict) -> str:
    lines = [f"# 批{batch} 验收证据摘要（batch_accept v{TOOL_VERSION}；只读/确定性）"]
    ps = doc.get("plan_summary") or {}
    pc = ps.get("counts") or {}
    lines.append(f"门禁计划：{pc.get('required', '?')} 必跑 / {pc.get('skipped', '?')} 可跳 / "
                 f"{pc.get('deferred', '?')} 延后（`{os.path.basename(plan_path)}`；"
                 f"证据目录 `{os.path.basename(os.path.normpath(ev_dir))}/`）")
    lines.append(f"结论：**{doc['verdict']}**（PASS {doc['counts'][PASS]} / WARN {doc['counts'][WARN]} / "
                 f"FAIL {doc['counts'][FAIL]} / UNKNOWN {doc['counts'][UNKNOWN]}）")
    lines.append("")
    lines.append("## 门禁结论表（cwd 列=门禁计划 cwd 字段，仅呈现；`—`=老计划未声明）")
    lines.append("| 门禁 | cwd | 结论 | 关键字段 | 证据 |")
    lines.append("|---|---|---|---|---|")
    for row in doc["gates"]:
        f = row["fields"]
        gid = row["id"]
        if gid in ("full_pre", "full_post"):
            secs = f"{f['seconds']:.1f}" if isinstance(f.get("seconds"), (int, float)) else "—"
            key = f"{_fmt(f.get('suites'))} 套件 / 红 {_fmt(f.get('red'))} / {secs}s"
        elif gid == "gen_docs_write":
            key = (f"rc {_fmt(f.get('rc'))} / AUTO {_fmt(f.get('auto_suites'))} 套件 / "
                   f"{_fmt(f.get('auto_assertions'))} 断言 / FAIL {_fmt(f.get('auto_fail'))}")
        elif gid == "lint":
            key = (f"error {_fmt(f.get('errors'))} / warning {_fmt(f.get('warnings'))} / "
                   f"{_fmt(f.get('records'))} 条")
        elif gid == "docs":
            key = f"失败 {_fmt(f.get('failures'))} 项 / rc {_fmt(f.get('rc'))}"
        elif gid == "swift":
            key = (f"{_fmt(f.get('beds'))} 台 / {_fmt(f.get('pass_total'))} PASS / "
                   f"{_fmt(f.get('fail'))} FAIL")
        elif gid == "corpus_two":
            same = f.get("sha_pass1") is not None and f.get("sha_pass1") == f.get("sha_pass2")
            key = (f"两遍 sha {'一致' if same else '不一致'}（{_fmt(f.get('sha_pass1'))}） / "
                   f"基线 diff {_fmt(f.get('diff_rows'))} 行")
        elif gid == "docx_audit":
            key = (f"成功 {_fmt(f.get('success'))} / 失败 {_fmt(f.get('fail'))} / "
                   f"csv {_fmt(f.get('csv_sha'))}")
        elif gid == "build":
            key = (f"cmp {_fmt(f.get('cmp_28'))} / o5 {_fmt(f.get('o5'))} / "
                   f"{_fmt(f.get('app_version'))}")
        elif gid == "ci_watch":
            key = f"run {_fmt(f.get('ci_run_id'))} / {_fmt(f.get('ci_conclusion'))}"
        else:
            key = "（无解析器）"
        if row["verdict"] == FAIL and not row["evidence"]:
            key = "—（缺证据）"
        lines.append(f"| {gid} | {row.get('cwd') or '—'} | {row['verdict']} | {key} | "
                     f"{row['evidence'] or '—'} |")
    lines.append("")
    lines.append("## 必跑缺证据 / 解析失败")
    bad = [r for r in doc["gates"] if r["verdict"] in (FAIL, UNKNOWN)]
    if bad:
        for r in bad:
            detail = "；".join(r["notes"]) or "—"
            lines.append(f"- {r['id']}（{r['verdict']}）：{detail}")
    else:
        lines.append("（无；必跑门禁证据齐全且关键字段可解析）")
    lines.append("")
    lines.append(f"## 跳过项（{len(doc['skipped'])}；须 Lead 在 ACCEPT 确认）")
    if doc["skipped"]:
        lines.append("| 门禁 | cwd | 规则 id |")
        lines.append("|---|---|---|")
        for r in doc["skipped"]:
            rules = ", ".join(str(x) for x in r["rules"] if x) or "（无理由 → 应改判必跑）"
            lines.append(f"| {r['id']} | {r.get('cwd') or '—'} | {rules} |")
    else:
        lines.append("（无）")
    if doc["deferred"]:
        names = "、".join(f"{r['id']}（{'/'.join(str(x) for x in r['rules'] if x) or '-'}）"
                         for r in doc["deferred"])
        lines.append(f"延后：{names}（提交后必跑）")
    lines.append("")
    lines.append("## 关键数字汇总（括号内=门禁计划 last 基线）")
    by_id = {r["id"]: r for r in doc["gates"]}
    for gid, fields in (("full_post", ("suites", "red")), ("gen_docs_write",
                                                           ("auto_suites", "auto_assertions")),
                        ("lint", ("errors", "warnings")), ("swift", ("beds", "pass_total"))):
        row = by_id.get(gid)
        if not row:
            continue
        f = row["fields"]
        last = row["last"] or {}
        parts = []
        for key in fields:
            if f.get(key) is None:
                continue
            last_key = {"suites": "suites", "red": "red", "auto_suites": "auto_suites",
                        "auto_assertions": "auto_assertions", "errors": "errors",
                        "warnings": "warnings", "beds": "beds", "pass_total": "pass_total"}[key]
            parts.append(f"{key} {_fmt(f[key])}{_delta(f[key], last.get(last_key))}")
        if parts:
            extra = ""
            if gid == "gen_docs_write" and f.get("readme_insertions") is not None:
                extra = (f"；README diff {_fmt(f['readme_insertions'])}+/"
                         f"{_fmt(f['readme_deletions'])}−/{_fmt(f.get('readme_hunks'))} hunk")
            if gid == "lint" and f.get("db_sha_before") and f.get("db_sha_before") == f.get("db_sha_after"):
                extra = "；库 sha 前后不变"
            lines.append(f"- {gid}：" + "；".join(parts) + extra + f"（证据 {row['evidence']}）")
    docs_row = by_id.get("docs")
    if docs_row:
        lines.append(f"- docs：失败 {_fmt(docs_row['fields'].get('failures'))} 项"
                     f"（证据 {docs_row['evidence']}）")
    lines.append("")
    lines.append("## 与 KPI 对照")
    if not kpi:
        lines.append("- 未提供 `--kpi` → 跳过 KPI 对照（仅用门禁计划 last 基线）")
    else:
        lines.append(f"- KPI 行 batch {kpi.get('batch')}：" + "；".join(kpi.get("checks") or [])
                     + f"（{kpi.get('status')}）")
    lines.append("- 口径：缺证据 → FAIL；字段解析不出 → UNKNOWN；绝不静默 PASS")
    if len(lines) > MD_LINE_LIMIT:
        # 硬约束 ≤40 行：先收掉「关键数字汇总」的逐条明细（数字在结论表里仍在）
        start = next((i for i, s in enumerate(lines) if s.startswith("## 关键数字汇总")), None)
        end = next((i for i, s in enumerate(lines) if s.startswith("## 与 KPI 对照")), None)
        if start is not None and end is not None and end - start > 2:
            lines[start + 1:end - 1] = ["- （明细见门禁结论表；行数上限 40 时压缩）"]
    return "\n".join(lines) + "\n"


def kpi_cross_check(kpi_doc, batch: int, doc: dict) -> dict:
    if not isinstance(kpi_doc, dict):
        return {}
    row = (kpi_doc.get("batches") or {}).get(str(batch))
    if not isinstance(row, dict):
        return {"batch": batch, "status": "info", "checks": [f"batch {batch} 无 KPI 行 → 跳过"]}
    by_id = {r["id"]: r for r in doc["gates"]}
    checks, status = [], "pass"

    def cmp(name, kpi_val, got):
        nonlocal status
        if kpi_val is None:
            checks.append(f"{name} KPI=null（未测，不估算）")
            return
        if got is None:
            checks.append(f"{name} 证据缺 → 无法对照")
            status = "warn"
            return
        if kpi_val == got:
            checks.append(f"{name} {got} ✓")
        else:
            checks.append(f"{name} KPI {kpi_val} ≠ 证据 {got}")
            status = "warn"

    post = by_id.get("full_post") or {}
    cmp("full_suites", row.get("full_suites"), (post.get("fields") or {}).get("suites"))
    gen = by_id.get("gen_docs_write") or {}
    gf = gen.get("fields") or {}
    cmp("auto_suites", row.get("auto_suites"), gf.get("auto_suites"))
    cmp("auto_assertions", row.get("auto_assertions"), gf.get("auto_assertions"))
    # generic 门禁跑了才对照（B-152；跳过时 KPI 里的旧 sha 不代表本批）
    corpus_row = by_id.get("corpus_two")
    if corpus_row:
        cmp("corpus_sha", row.get("corpus_sha"), (corpus_row.get("fields") or {}).get("sha_pass1"))
    audit_row = by_id.get("docx_audit")
    if audit_row:
        cmp("audit_csv_sha", row.get("audit_csv_sha"),
            (audit_row.get("fields") or {}).get("csv_sha"))
    skipped_ids = [r["id"] for r in doc["skipped"]]
    build_kpi = row.get("build_count")
    if isinstance(build_kpi, int):
        if build_kpi == 0 and "build" in skipped_ids:
            checks.append("build_count 0（build 跳过）✓")
        elif build_kpi == 1 and "build" not in skipped_ids:
            checks.append("build_count 1（build 已跑）✓")
        else:
            checks.append(f"build_count {build_kpi} 与 build 状态（{'跳过' if 'build' in skipped_ids else '必跑'}）不一致")
            status = "warn"
    return {"batch": batch, "status": status, "checks": checks,
            "source": os.path.basename(str(kpi_doc.get("__path__") or "batch-kpi.json"))}


# ---------------------------------------------------------------------------
# --self-test（hermetic：tempfile 假证据；不碰真实仓/真实库）
# ---------------------------------------------------------------------------

def _load_fixtures():
    path = os.path.join(TOOLS_DIR, "tool_fixtures.py")
    if not os.path.isfile(path):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("_batch_accept_fixtures", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001
        return None
    return mod


def _quiet_main(argv: list) -> int:
    """self-test 里静默调用 main（只留用例行，输出/告警噪音不外溢）。"""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return main(argv)


def run_self_test() -> int:
    fixtures = _load_fixtures()
    if fixtures is None:
        print("[batch_accept self-test] FAIL：缺 tools/tool_fixtures.py 或导入失败")
        return 1
    results = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, detail))
        print(("[batch_accept self-test] %-24s %s %s" % (name, "PASS" if ok else "FAIL", detail)).rstrip())

    # cwd 夹具（B-149）：与真实 gate_plan 同口径，仅呈现、不参与判据
    PLAN_CWD = {"full_pre": "engine", "full_post": "engine", "gen_docs_write": "engine",
                "lint": "public_root", "docs": "engine", "swift": "public_root",
                "corpus_two": "engine", "docx_audit": "engine", "build": "public_root",
                "ci_watch": "public_root"}

    def run(td, tag, *, omit=(), post=(107, 0, 128.0, 0), pre=(106, 2, 131.5, 2),
            lint=(0, 18, 7051, 0), swift=(fixtures.DEFAULT_SWIFT_BEDS, 0, 0),
            plan_rows=None, rc_style="exit", with_cwd=False, generic=False,
            overrides=None, gen_docs_noop=False):
        root = os.path.join(td, tag)
        ev = os.path.join(root, "evidence")
        fixtures.build_evidence_dir(ev, omit=omit, pre=pre, post=post, lint=lint, swift=swift,
                                    rc_style=rc_style, generic=generic,
                                    gen_docs_noop=gen_docs_noop)
        for gid, val in (overrides or {}).items():
            spec = fixtures.EVIDENCE_FILENAMES.get(gid) or (None,)
            if not spec[0]:
                continue
            if isinstance(val, dict):
                fixtures.write_json(os.path.join(ev, spec[0]), val)
            else:
                fixtures.write_text(os.path.join(ev, spec[0]), val)
        plan_path = os.path.join(root, "gate_plan.json")
        plan = fixtures.gate_plan_doc()
        if plan_rows:
            for g in plan["gates"]:
                if g["id"] in plan_rows:
                    g["status"] = plan_rows[g["id"]]
        if with_cwd:
            for g in plan["gates"]:
                g["cwd"] = PLAN_CWD.get(g["id"], "public_root")
        fixtures.write_json(plan_path, plan)
        out = os.path.join(root, "out")
        rc = _quiet_main(["--batch", str(fixtures.THIS_BATCH), "--gates", plan_path,
                          "--evidence", ev, "--out", out])
        with open(os.path.join(out, "accept.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        with open(os.path.join(out, "accept.md"), encoding="utf-8") as fh:
            md = fh.read()
        return rc, doc, md, out

    def t_all_pass(td):
        rc, doc, md, _ = run(td, "green")
        if doc["counts"][FAIL] or doc["counts"][UNKNOWN]:
            return False, f"counts {doc['counts']}"
        post = next(r for r in doc["gates"] if r["id"] == "full_post")["fields"]
        got = (post["suites"], post["red"], post["seconds"])
        if got != (107, 0, 128.0):
            return False, f"full_post 解析 {got}"
        if not doc["verdict"].startswith(PASS):
            return False, f"verdict {doc['verdict']}"
        if len(md.splitlines()) > MD_LINE_LIMIT:
            return False, f"accept.md {len(md.splitlines())} 行 > {MD_LINE_LIMIT}"
        return True, f"PASS/{doc['verdict']}；full_post {got}；md {len(md.splitlines())} 行"

    def t_pre_red_warn(td):
        _, doc, md, _ = run(td, "pre-red")
        pre = next(r for r in doc["gates"] if r["id"] == "full_pre")
        if pre["verdict"] != WARN:
            return False, f"写前红未记 WARN（{pre['verdict']}）"
        if "WARN" not in md:
            return False, "accept.md 未点名 WARN"
        return True, "写前基线红 2 → WARN 且点名（非静默 PASS）"

    def t_post_red_fail(td):
        rc, doc, _, _ = run(td, "post-red", post=(107, 2, 128.0, 2))
        post = next(r for r in doc["gates"] if r["id"] == "full_post")
        if post["verdict"] != FAIL or rc != 1:
            return False, f"写后红未判 FAIL（verdict={post['verdict']}, exit={rc}）"
        return True, "写后红 2 → FAIL 且 exit 1"

    def t_missing_evidence(td):
        rc, doc, md, _ = run(td, "missing", omit=("lint",))
        lint = next(r for r in doc["gates"] if r["id"] == "lint")
        if lint["verdict"] != FAIL or rc != 1:
            return False, f"缺证据未判 FAIL（{lint['verdict']}, exit={rc}）"
        if "缺证据文件" not in md:
            return False, "accept.md 未列缺证据"
        return True, "缺 lint 证据 → FAIL / exit 1 / md 列缺证据"

    def t_missing_field_unknown(td):
        root = os.path.join(td, "nofield")
        ev = os.path.join(root, "evidence")
        fixtures.build_evidence_dir(ev)
        fixtures.write_text(os.path.join(ev, "04-lint.log"),
                            "库：fixture\nerror 0 项，warning 18 项——库可用。\nEXIT=0\n")
        plan_path = os.path.join(root, "gate_plan.json")
        fixtures.write_gate_plan(plan_path)
        out = os.path.join(root, "out")
        rc = _quiet_main(["--batch", str(fixtures.THIS_BATCH), "--gates", plan_path,
                          "--evidence", ev, "--out", out])
        with open(os.path.join(out, "accept.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        lint = next(r for r in doc["gates"] if r["id"] == "lint")
        if lint["verdict"] != UNKNOWN or rc != 2:
            return False, f"字段缺失未判 UNKNOWN（{lint['verdict']}, exit={rc}）"
        if not lint["missing"]:
            return False, "missing 列表为空"
        return True, f"字段缺失 → UNKNOWN / exit 2（missing={lint['missing']}）"

    def t_skip_rules(td):
        _, doc, md, _ = run(td, "skip")
        ids = [r["id"] for r in doc["skipped"]]
        if ids != ["corpus_two", "docx_audit", "build"]:
            return False, f"跳过项 {ids}"
        if "only_tools_docs_gui_changed" not in md or "no_parse_change" not in md:
            return False, "accept.md 未列规则 id"
        return True, f"跳过 {len(ids)} 项且规则 id 齐全；延后 {[r['id'] for r in doc['deferred']]}"

    def t_determinism(td):
        _, doc1, md1, out1 = run(td, "det1")
        _, doc2, md2, _ = run(td, "det2")
        if md1 != md2:
            return False, "accept.md 两次不同"
        a = {k: v for k, v in doc1.items() if k != "generated_at"}
        b = {k: v for k, v in doc2.items() if k != "generated_at"}
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            return False, "accept.json 除时间戳外有差异"
        return True, "两遍逐字节相同（除 generated_at）"

    def t_map_override(td):
        root = os.path.join(td, "map")
        ev = os.path.join(root, "evidence")
        fixtures.build_evidence_dir(ev, omit=("lint",))
        fixtures.write_text(os.path.join(ev, "my-lint-77.log"),
                            fixtures.lint_log(0, 18, 7051))
        plan_path = os.path.join(root, "gate_plan.json")
        fixtures.write_gate_plan(plan_path)
        map_path = os.path.join(root, "map.json")
        fixtures.write_json(map_path, {"gates": {"lint": {"patterns": ["my-lint-*.log"],
                                                         "kind": "lint"}}})
        out = os.path.join(root, "out")
        rc = _quiet_main(["--batch", str(fixtures.THIS_BATCH), "--gates", plan_path,
                          "--evidence", ev, "--out", out, "--map", map_path])
        with open(os.path.join(out, "accept.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        lint = next(r for r in doc["gates"] if r["id"] == "lint")
        if rc != 0 or lint["verdict"] != PASS or lint["evidence"] != "my-lint-77.log":
            return False, f"--map 未生效（exit {rc}, {lint['verdict']}, {lint['evidence']}）"
        return True, "--map 覆盖文件名模式生效"

    def t_rc_forms(td):
        """批78 真实链形态：rc=0 / EXIT= 与 rc= 混用 → 仍判 PASS，rc 取最后一个。"""
        rc_a, doc_a, _, _ = run(td, "rc-form", rc_style="rc")
        docs = next(r for r in doc_a["gates"] if r["id"] == "docs")
        gen = next(r for r in doc_a["gates"] if r["id"] == "gen_docs_write")
        if (docs["fields"].get("rc"), gen["fields"].get("rc")) != (0, 0):
            return False, f"rc= 形态未解析出 rc0（docs={docs['fields'].get('rc')}, " \
                          f"gen_docs={gen['fields'].get('rc')}）"
        if rc_a != 0 or doc_a["counts"][UNKNOWN] or docs["verdict"] != PASS \
                or gen["verdict"] != PASS:
            return False, (f"rc= 形态判 {doc_a['verdict']}（UNKNOWN {doc_a['counts'][UNKNOWN]}）"
                           f"docs={docs['verdict']}/gen_docs={gen['verdict']}")
        rc_b, doc_b, _, _ = run(td, "rc-mixed", rc_style="mixed")
        gen_b = next(r for r in doc_b["gates"] if r["id"] == "gen_docs_write")
        if gen_b["fields"].get("rc") != 0 or gen_b["verdict"] != PASS:
            return False, f"混用未取最后一个（rc={gen_b['fields'].get('rc')}, {gen_b['verdict']}）"
        return True, "rc= 形态 → PASS；EXIT=2/rc=0/EXIT=0 混用 → 取最后 EXIT=0"

    def t_rc_missing_unknown(td):
        rc, doc, md, _ = run(td, "rc-none", rc_style="none")
        docs = next(r for r in doc["gates"] if r["id"] == "docs")
        if docs["verdict"] != UNKNOWN or rc != 2:
            return False, f"三种写法全无仍判 {docs['verdict']} / exit {rc}（严格性被降低）"
        if "rc" not in md:
            return False, "accept.md 未点名缺 rc"
        return True, "EXIT=/rc=/exit code: 全无 → UNKNOWN / exit 2（严格性不变）"

    def t_cwd_presented(td):
        """cwd 进 accept.md/json，且与「无 cwd 的计划」判定逐项一致（仅呈现）。"""
        rc_on, doc_on, md_on, _ = run(td, "cwd-on", with_cwd=True)
        _, doc_off, _, _ = run(td, "cwd-off")
        cwds = {r["id"]: r.get("cwd") for r in doc_on["gates"]}
        want = {gid: PLAN_CWD[gid] for gid in cwds}
        if cwds != want:
            return False, f"accept.json cwd {cwds} != {want}"
        if "| 门禁 | cwd |" not in md_on or "public_root" not in md_on or "engine" not in md_on:
            return False, "accept.md 未显示 cwd 列/取值"
        if (rc_on != 0 or doc_on["counts"][FAIL] or doc_on["counts"][UNKNOWN]
                or doc_on["verdict"] != doc_off["verdict"]
                or doc_on["counts"] != doc_off["counts"]):
            return False, (f"cwd 影响判定：on={doc_on['verdict']}{doc_on['counts']} "
                           f"off={doc_off['verdict']}{doc_off['counts']} rc={rc_on}")
        if any("cwd" in str(w) for w in doc_on.get("warnings") or []):
            return False, f"带 cwd 却报 cwd WARN：{doc_on.get('warnings')}"
        return True, (f"{len(cwds)} 门 cwd 进 md/json；判定与无 cwd 计划一致"
                      f"（{doc_on['verdict']}）")

    def t_cwd_missing(td):
        """老 gate_plan.json 无 cwd → 空白 + WARN，绝不据此判 FAIL/UNKNOWN。"""
        rc, doc, md, _ = run(td, "cwd-absent")
        if any(r.get("cwd") != "" for r in doc["gates"]):
            return False, f"缺 cwd 却呈现了值：{[r.get('cwd') for r in doc['gates']]}"
        if rc != 0 or doc["counts"][FAIL] or doc["counts"][UNKNOWN] or doc["cwd_missing"] == []:
            return False, f"缺 cwd 被当成判据（{doc['verdict']} / rc {rc} / {doc['counts']}）"
        if not any("cwd" in str(w) for w in doc.get("warnings") or []):
            return False, f"缺 cwd 未降级 WARN：{doc.get('warnings')}"
        if not any(f"| {gid} | — |" in md for gid in ("full_pre", "lint", "docs")):
            return False, "accept.md 的 cwd 列未留空（—）"
        return True, f"缺 cwd → cwd 列 — + WARN；判定仍 {doc['verdict']}（rc 0）"

    # ---- generic 门禁解析器（B-152） ----
    GENERIC_IDS = ("corpus_two", "docx_audit", "build", "ci_watch")
    GENERIC_REQUIRED = {gid: "required" for gid in GENERIC_IDS}

    def t_generic_parsed(td):
        """generic 门禁有字段解析器：不再「恒 UNKNOWN /（无解析器）」。"""
        rc, doc, md, _ = run(td, "generic-ok", generic=True, plan_rows=GENERIC_REQUIRED)
        rows = {r["id"]: r for r in doc["gates"] if r["id"] in GENERIC_IDS}
        want = {"corpus_two": ("sha_pass1", "sha_pass2", "diff_rows"),
                "docx_audit": ("success", "fail", "csv_sha"),
                "build": ("cmp_28", "o5", "app_version"),
                "ci_watch": ("ci_run_id", "ci_conclusion")}
        for gid, keys in want.items():
            f = rows[gid]["fields"]
            missing = [k for k in keys if f.get(k) is None]
            if missing:
                return False, f"{gid} 未解析出 {missing}（fields={sorted(f)}）"
        if rc != 0 or doc["counts"][FAIL] or doc["counts"][UNKNOWN]:
            return False, f"generic 门禁未判绿（{doc['verdict']} / rc {rc} / {doc['counts']}）"
        if "（无解析器）" in md:
            return False, "accept.md 仍显示「（无解析器）」"
        if rows["corpus_two"]["fields"].get("equal") is not True:
            return False, "corpus 的 equal 字段未判 True"
        return True, ("4 门 generic 均解析且 PASS（corpus "
                      f"{rows['corpus_two']['fields']['sha_pass1'][:8]}… / build cmp "
                      f"{rows['build']['fields']['cmp_28']} / ci run "
                      f"{rows['ci_watch']['fields']['ci_run_id']}）")

    def t_corpus_sha_mismatch_fail(td):
        """两遍 sha 不一致 = 非确定性回归 → FAIL（不静默 PASS）。"""
        rc, doc, _, _ = run(td, "corpus-mismatch", generic=True, plan_rows=GENERIC_REQUIRED,
                            overrides={"corpus_two":
                                       fixtures.corpus_log(pass2_sha="f" * 64)})
        row = next(r for r in doc["gates"] if r["id"] == "corpus_two")
        if row["verdict"] != FAIL or rc != 1:
            return False, f"两遍 sha 不一致未判 FAIL（{row['verdict']} / rc {rc}）"
        return True, "两遍 sha 不一致 → FAIL（exit 1）"

    def t_generic_thresholds_fail(td):
        """docx fail>0 / build cmp≠28/28、o5≠1 / ci 结论非 success → FAIL。"""
        rc, doc, md, _ = run(td, "generic-red", generic=True, plan_rows=GENERIC_REQUIRED,
                             overrides={"docx_audit": fixtures.audit_log(fail=2),
                                        "build": fixtures.build_log(cmp="27/28", o5=0),
                                        "ci_watch": fixtures.ci_watch_json(
                                            conclusion="failure")})
        verdicts = {r["id"]: r["verdict"] for r in doc["gates"] if r["id"] in GENERIC_IDS}
        bad = [gid for gid in ("docx_audit", "build", "ci_watch")
               if verdicts.get(gid) != FAIL]
        if rc != 1 or bad:
            return False, f"阈值未判 FAIL：{verdicts} / rc {rc}"
        if "cmp 27/28" not in md:
            return False, "accept.md 未点名 build 的 cmp 字段"
        return True, "docx fail / build cmp+o5 / ci 非 success → 全 FAIL（exit 1）"

    def t_generic_missing_unknown(td):
        """有证据文件但字段取不到 → 仍判 UNKNOWN（不因加了解析器就放宽）。"""
        rc, doc, _, _ = run(td, "generic-nofield", generic=True,
                            plan_rows=GENERIC_REQUIRED,
                            overrides={"corpus_two": "▶ corpus 跑完了（夹具无字段）\nEXIT=0\n"})
        row = next(r for r in doc["gates"] if r["id"] == "corpus_two")
        if row["verdict"] != UNKNOWN or rc != 2:
            return False, f"字段缺失未判 UNKNOWN（{row['verdict']} / rc {rc}）"
        if not row["missing"]:
            return False, "missing 列表为空"
        return True, f"字段取不到 → UNKNOWN（missing={row['missing']}）"

    def t_corpus_kv_form(td):
        """证据格式约定 §2：键值行 `pass1_sha:`/`pass2_sha:` 也能解析。"""
        rc, doc, _, _ = run(td, "corpus-kv", generic=True, plan_rows=GENERIC_REQUIRED,
                            overrides={"corpus_two": fixtures.corpus_log(form="kv")})
        row = next(r for r in doc["gates"] if r["id"] == "corpus_two")
        f = row["fields"]
        if row["verdict"] != PASS or rc != 0 or f.get("equal") is not True:
            return False, f"键值形态未解析（{row['verdict']} / rc {rc} / {f}）"
        return True, "键值行形态（pass1_sha:/pass2_sha:）→ PASS"

    def t_gen_docs_noop_ok(td):
        """B-159 幂等写：clean 树「AUTO 块已是最新」→ 0 diff 属预期，不得判 FAIL。"""
        rc, doc, _, _ = run(td, "gen-docs-noop", gen_docs_noop=True)
        gen = next(r for r in doc["gates"] if r["id"] == "gen_docs_write")
        if gen["verdict"] != PASS or rc != 0:
            return False, f"幂等写未判 PASS（{gen['verdict']} / rc {rc} / {gen['notes']}）"
        if gen["fields"].get("readme_changed") is not False:
            return False, "readme_changed 应为 False（空 diff）"
        if gen["fields"].get("readme_noop") is not True:
            return False, "readme_noop 未识别"
        return True, "幂等写（AUTO 块已是最新，0 diff）→ PASS（非写失败）"

    def t_ci_no_docs_run(td):
        """docs 工作流有 paths 过滤（只在 docs/ 改动时跑）→ 无 docs run 属正常态（记 null+WARN，不判 UNKNOWN）。"""
        rc, doc, md, _ = run(td, "ci-no-docs", generic=True, plan_rows=GENERIC_REQUIRED,
                             overrides={"ci_watch": {"databaseId": 40000000003,
                                                     "workflowName": "CI",
                                                     "status": "completed",
                                                     "conclusion": "success"}})
        row = next(r for r in doc["gates"] if r["id"] == "ci_watch")
        if row["verdict"] != PASS or rc != 0:
            return False, f"无 docs run 时判 {row['verdict']} / rc {rc}（应 PASS+WARN）"
        if sorted(row.get("soft_missing") or []) != ["docs_conclusion", "docs_run_id"]:
            return False, f"soft_missing 应为 docs_*（实际 {row.get('soft_missing')}）"
        if doc["counts"][UNKNOWN]:
            return False, f"无 docs run 被判 UNKNOWN（{doc['counts']}）"
        return True, "无 docs run → ci_run_id/成功结论仍判 PASS（docs_* 记 null + WARN）"

    print("[batch_accept self-test] hermetic 夹具（tempfile；不读真实仓/真实库）")
    with tempfile.TemporaryDirectory(prefix="batch-accept-selftest-") as td:
        check("all_green", lambda: t_all_pass(td))
        check("pre_red_is_warn", lambda: t_pre_red_warn(td))
        check("post_red_is_fail", lambda: t_post_red_fail(td))
        check("missing_evidence_fail", lambda: t_missing_evidence(td))
        check("missing_field_unknown", lambda: t_missing_field_unknown(td))
        check("skipped_rules", lambda: t_skip_rules(td))
        check("map_override", lambda: t_map_override(td))
        check("rc_forms", lambda: t_rc_forms(td))
        check("rc_missing_unknown", lambda: t_rc_missing_unknown(td))
        check("cwd_presented_only", lambda: t_cwd_presented(td))
        check("cwd_missing_present_only", lambda: t_cwd_missing(td))
        check("generic_parsed", lambda: t_generic_parsed(td))
        check("corpus_sha_mismatch_fail", lambda: t_corpus_sha_mismatch_fail(td))
        check("generic_thresholds_fail", lambda: t_generic_thresholds_fail(td))
        check("generic_missing_unknown", lambda: t_generic_missing_unknown(td))
        check("corpus_kv_form", lambda: t_corpus_kv_form(td))
        check("gen_docs_noop_ok", lambda: t_gen_docs_noop_ok(td))
        check("ci_no_docs_run", lambda: t_ci_no_docs_run(td))
        check("determinism", lambda: t_determinism(td))

    ok = sum(1 for _, o, _ in results if o)
    print(f"[batch_accept self-test] {ok}/{len(results)} PASS"
          + ("" if ok == len(results) else " ← 有失败"))
    return 0 if ok == len(results) else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        description="验收证据聚合器（B-145 波1）：gate_plan + 证据目录 → accept.md / accept.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python3 tools/batch_accept.py --batch 77 --gates <dir>/gate_plan.json "
               "--evidence <dir> --out <outdir>")
    ap.add_argument("--batch", type=int, help="批次号（用于标题与 KPI 行匹配）")
    ap.add_argument("--gates", help="门禁计划 JSON（gate_plan.py 的输出）")
    ap.add_argument("--evidence", help="证据目录（只读；顶层文件按模式匹配）")
    ap.add_argument("--out", help="输出目录（accept.md + accept.json）")
    ap.add_argument("--kpi", help="可选：batch-kpi.json（提供则做 KPI 对照）")
    ap.add_argument("--map", help="可选：证据映射 JSON（覆盖 DEFAULT_GATE_SPECS）")
    ap.add_argument("--self-test", action="store_true", help="跑 hermetic 自检（≥6 用例）")
    ap.add_argument("--version", action="version", version=f"batch_accept {TOOL_VERSION}")
    args = ap.parse_args(argv)

    if args.self_test:
        sys.dont_write_bytecode = True
        return run_self_test()
    if not args.gates or not args.evidence or not args.out:
        ap.error("--gates / --evidence / --out 为必填（或使用 --self-test）")

    warnings: list = []
    plan = _load_json(args.gates, warnings, "--gates")
    if not isinstance(plan, dict) or not isinstance(plan.get("gates"), list):
        print(f"[batch_accept] 门禁计划不可用：{args.gates}（{'; '.join(warnings)}）", file=sys.stderr)
        return 2
    if plan.get("batch") is not None and args.batch is None:
        args.batch = int(plan["batch"])
    if args.batch is None:
        ap.error("--batch 必填（门禁计划里也没有 batch）")
    if not os.path.isdir(args.evidence):
        print(f"[batch_accept] 证据目录不存在：{args.evidence}", file=sys.stderr)
        return 2

    specs = {k: dict(v) for k, v in DEFAULT_GATE_SPECS.items()}
    if args.map:
        override = _load_json(args.map, warnings, "--map")
        if isinstance(override, dict):
            for gid, spec in (override.get("gates") or {}).items():
                merged = dict(specs.get(gid) or {})
                merged.update({k: v for k, v in spec.items() if v is not None})
                specs[gid] = merged
        else:
            warnings.append("--map 非法（应为 {\"gates\": {...}}）")

    doc = build_accept(plan, args.evidence, specs, warnings)
    kpi = {}
    if args.kpi:
        kpi_doc = _load_json(args.kpi, warnings, "--kpi")
        if isinstance(kpi_doc, dict):
            kpi_doc["__path__"] = args.kpi
            kpi = kpi_cross_check(kpi_doc, args.batch, doc)

    payload = {"tool": "batch_accept", "version": TOOL_VERSION, "generated_at":
               time.strftime("%Y-%m-%dT%H:%M:%S%z"), "batch": args.batch,
               "gates_source": os.path.basename(args.gates),
               "evidence_dir": os.path.basename(os.path.normpath(args.evidence)),
               **doc, "kpi": kpi, "warnings": warnings}
    os.makedirs(args.out, exist_ok=True)
    md = render_md(doc, args.batch, args.gates, args.evidence, kpi)
    with open(os.path.join(args.out, "accept.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    with open(os.path.join(args.out, "accept.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    verdict = doc["verdict"]
    print(f"[batch_accept] batch {args.batch} → {args.out}/accept.md + accept.json；"
          f"结论 {verdict}（PASS {doc['counts'][PASS]} / WARN {doc['counts'][WARN]} / "
          f"FAIL {doc['counts'][FAIL]} / UNKNOWN {doc['counts'][UNKNOWN]}）")
    for w in warnings:
        print(f"[batch_accept] WARN {w}", file=sys.stderr)
    if doc["counts"][FAIL]:
        return 1
    if doc["counts"][UNKNOWN]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
