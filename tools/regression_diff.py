#!/usr/bin/env python3
"""基线 vs 本批 回归差（B-145 波1 / 批78）——只读、离线、确定性、无第三方依赖

读两份 JSON（基线行 / 本批行：`baselines/batch-kpi.json` 的 `batches.<n>` 单行，
或整份 batch-kpi.json + `--baseline-batch/--current-batch` 选行）→ 字段级变化表 +
分类 + 环比百分比 → `regression_diff.md` / `regression_diff.json`。

分类（判据可覆盖：`--policy`）
------------------------------
- `improved`：越好越好的字段（面值/等价/steps/红数/返工数…）**下降**。
- `regression`：同类字段**上升**；或"内容量"字段（套件/断言）**下降**。
- `expected`：内容量上升、中性计数变化、sha 不变、列表项变化（逐项列出）、相等。
- `skip`：任一侧 `null`（KPI 纪律：**未测就是未测，禁止估算**）。
- `unknown`：类型不一致、sha 变了但无法判定是否变坏、未分类字段（**交人工**，不当绿）。

环比：`pct = (本批−基线)/基线×100`，同时给**四舍五入到整百分点**的 `pct_points`
（与 EVIDENCE「vs 上一批 −60%」口径对齐）；基线为 0 时不给百分比。

用法
----
  python3 tools/regression_diff.py --baseline b76.json --current b77.json --out <目录> \
      [--baseline-batch 76] [--current-batch 77] [--policy p.json] [--fail-on-regression] \
      [--self-test]

退出码：0 = 无 regression（或未加 `--fail-on-regression`）；1 = 有 regression 且加了
`--fail-on-regression`；2 = 输入不可用。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

TOOL_VERSION = "1.0.0"
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

IMPROVED, EXPECTED, REGRESSION, SKIP, UNKNOWN = ("improved", "expected", "regression",
                                                 "skip", "unknown")

DEFAULT_POLICY = {
    # 越低越好（成本/红数/返工）
    "lower_is_better": ["face_M", "eq_M", "steps", "ci_red", "amend_count", "rework_count",
                        "review_findings", "lead_closure_steps"],
    # 越高越好的「内容量」（数量掉了 = 回归信号）
    "higher_is_expected": ["full_suites", "auto_suites", "auto_assertions"],
    # 中性计数：变化只记录，不判好坏
    "neutral_counters": ["build_count", "o5", "sessions", "checkpoint_snapshot"],
    # sha 类：相等=expected；变了=unknown（是否变坏要人工）
    "sha_fields": ["corpus_sha", "audit_csv_sha"],
    # 列表类：逐项列出变化
    "list_fields": ["gates_required", "gates_skipped", "tools_used"],
    # 元信息：不参与比较（只计数）
    "meta_fields": ["batch", "status", "source", "sessions_note", "updated_at", "schema"],
}

FIELD_ZH = {
    "face_M": "面值(M)", "eq_M": "成本等价(M)", "steps": "steps", "ci_red": "CI 红",
    "amend_count": "补记数", "rework_count": "返工数", "review_findings": "监理发现",
    "lead_closure_steps": "Lead 收口 steps", "full_suites": "full 套件",
    "auto_suites": "AUTO 套件", "auto_assertions": "AUTO 断言", "build_count": "build 次数",
    "o5": "O5 产物", "sessions": "会话数", "checkpoint_snapshot": "checkpoint 快照",
    "corpus_sha": "corpus sha", "audit_csv_sha": "audit csv sha",
    "gates_required": "必跑门禁", "gates_skipped": "跳过门禁", "tools_used": "用到的工具",
}


def _round_half_away(x: float) -> int:
    return int(math.floor(x + 0.5)) if x >= 0 else int(math.ceil(x - 0.5))


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _label(name: str) -> str:
    zh = FIELD_ZH.get(name)
    return f"{name}（{zh}）" if zh else name


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, list):
        return f"{len(v)} 项"
    if isinstance(v, dict):
        return f"{len(v)} 键"
    text = str(v)
    return text[:12] + "…" if len(text) > 16 else text


# ---------------------------------------------------------------------------
# 输入
# ---------------------------------------------------------------------------

def load_row(path: str, batch_key, what: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{what} 不可用：{path}（{type(exc).__name__}）") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{what} 不是 JSON 对象：{path}")
    batches = doc.get("batches")
    if isinstance(batches, dict):
        key = str(batch_key) if batch_key is not None else None
        if key is None:
            raise ValueError(f"{what} 是 batch-kpi 容器，必须给 --{what}-batch")
        row = batches.get(key)
        if not isinstance(row, dict):
            raise ValueError(f"{what} 容器里没有 batch {key}")
        return dict(row), f"{os.path.basename(path)}#batches.{key}"
    if batch_key is not None and str(doc.get("batch")) not in (str(batch_key),):
        raise ValueError(f"{what} 行的 batch={doc.get('batch')!r} 与 --{what}-batch={batch_key} 不一致")
    return dict(doc), os.path.basename(path)


# ---------------------------------------------------------------------------
# 比较
# ---------------------------------------------------------------------------

def compare_field(name: str, bv, cv, policy: dict) -> dict:
    row = {"field": name, "baseline": bv, "current": cv, "change": None,
           "pct": None, "pct_points": None, "category": EXPECTED, "note": ""}
    lists = {k: set(policy.get(k) or []) for k in
             ("lower_is_better", "higher_is_expected", "neutral_counters", "sha_fields",
              "list_fields", "meta_fields")}

    if bv is None or cv is None:
        row["category"] = SKIP
        row["note"] = "任一侧未测（null）→ 不估算"
        return row
    if isinstance(bv, bool) or isinstance(cv, bool):
        if bv == cv:
            row["note"] = "未变"
        else:
            row["category"] = UNKNOWN
            row["note"] = "布尔状态变化 → 人工确认"
        return row
    if isinstance(bv, list) or isinstance(cv, list):
        bl = bv if isinstance(bv, list) else [bv]
        cl = cv if isinstance(cv, list) else [cv]
        added = [x for x in cl if x not in bl]
        removed = [x for x in bl if x not in cl]
        row["change"] = f"{len(bl)}→{len(cl)} 项"
        if not added and not removed:
            row["note"] = "逐项一致"
        else:
            row["category"] = EXPECTED
            bits = []
            if added:
                bits.append("新增 " + "、".join(_list_name(x) for x in added[:4]))
            if removed:
                bits.append("移除 " + "、".join(_list_name(x) for x in removed[:4]))
            row["note"] = "；".join(bits)
        return row
    if isinstance(bv, str) or isinstance(cv, str):
        if bv == cv:
            row["note"] = "未变"
            return row
        if name in lists["sha_fields"]:
            row["category"] = UNKNOWN
            row["note"] = "sha 变化 → 需人工确认未变坏"
            return row
        row["category"] = UNKNOWN
        row["note"] = "文本变化 → 人工确认"
        return row

    # 数值
    if not (_is_num(bv) and _is_num(cv)):
        row["category"] = UNKNOWN
        row["note"] = "类型不一致 → 人工确认"
        return row
    diff = cv - bv
    row["change"] = f"{diff:+g}" if isinstance(diff, int) else f"{diff:+.2f}"
    if bv != 0:
        pct = (cv - bv) / bv * 100.0
        row["pct"] = round(pct, 1)
        row["pct_points"] = _round_half_away(pct)
    else:
        row["note"] = "基线 0 → 百分比不适用"

    if name in lists["lower_is_better"]:
        if diff < 0:
            row["category"] = IMPROVED
        elif diff > 0:
            row["category"] = REGRESSION
        else:
            row["note"] = (row["note"] + "；" if row["note"] else "") + "未变"
    elif name in lists["higher_is_expected"]:
        if diff < 0:
            row["category"] = REGRESSION
            row["note"] = "内容量下降 → 回归信号"
        else:
            row["category"] = EXPECTED
    elif name in lists["neutral_counters"]:
        row["category"] = EXPECTED
        row["note"] = (row["note"] + "；" if row["note"] else "") + "中性计数，仅记录"
    else:
        row["category"] = UNKNOWN
        row["note"] = "未分类字段 → 人工确认（可用 --policy 归类）"
    return row


def _list_name(item) -> str:
    if isinstance(item, dict):
        return str(item.get("gate") or item.get("id") or "dict")
    return str(item)


def build_diff(base: dict, cur: dict, policy: dict) -> dict:
    policy = {**DEFAULT_POLICY, **(policy or {})}
    meta = set(policy.get("meta_fields") or [])
    present = set(base) | set(cur)
    order = []
    for key in ("lower_is_better", "higher_is_expected", "neutral_counters", "sha_fields",
                "list_fields"):
        for name in policy.get(key) or []:
            if name in present and name not in order:
                order.append(name)
    for name in sorted(set(base) | set(cur)):
        if name in meta or name in order:
            continue
        order.append(name)
    rows = [compare_field(name, base.get(name), cur.get(name), policy) for name in order]
    counts = {IMPROVED: 0, EXPECTED: 0, REGRESSION: 0, SKIP: 0, UNKNOWN: 0}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    return {"rows": rows, "counts": counts,
            "skipped_meta": sorted(meta & (set(base) | set(cur))),
            "regressions": [r["field"] for r in rows if r["category"] == REGRESSION],
            "unknown": [r["field"] for r in rows if r["category"] == UNKNOWN]}


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render_md(doc: dict) -> str:
    base, cur = doc["baseline"], doc["current"]
    lines = [f"# 回归差（regression_diff v{TOOL_VERSION}；只读/确定性）"]
    lines.append(f"基线 batch {base.get('label')}（`{base.get('source')}`）→ 本批 batch "
                 f"{cur.get('label')}（`{cur.get('source')}`）")
    counts = doc["counts"]
    lines.append(f"分类：improved {counts[IMPROVED]} / expected {counts[EXPECTED]} / "
                 f"regression {counts[REGRESSION]} / skip {counts[SKIP]} / unknown {counts[UNKNOWN]}")
    lines.append("")
    lines.append("| 字段 | 基线 | 本批 | 变化 | 变化率 | 分类 |")
    lines.append("|---|---|---|---|---|---|")
    for row in doc["rows"]:
        if row["category"] == SKIP and row["baseline"] is None and row["current"] is None:
            continue  # 两侧都没测：不占表格行（在「未比较/未测」里列出）
        pct = "—"
        if row["pct"] is not None:
            pct = f"{row['pct']:+.1f}%（{row['pct_points']:+d} pp）"
        change = row["change"] if row["change"] is not None else "—"
        lines.append(f"| {_label(row['field'])} | {_fmt(row['baseline'])} | {_fmt(row['current'])} | "
                     f"{change} | {pct} | {row['category']} |")
    lines.append("")
    lines.append("## 回归项（regression）")
    if doc["regressions"]:
        for row in doc["rows"]:
            if row["category"] == REGRESSION:
                lines.append(f"- **{_label(row['field'])}**：{_fmt(row['baseline'])} → "
                             f"{_fmt(row['current'])}（{row['pct_points']:+d} pp）{row['note']}")
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 需人工判定（unknown）")
    if doc["unknown"]:
        for row in doc["rows"]:
            if row["category"] == UNKNOWN:
                lines.append(f"- {_label(row['field'])}：{_fmt(row['baseline'])} → "
                             f"{_fmt(row['current'])}；{row['note']}")
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 未测 / 未比较")
    skipped = [r for r in doc["rows"] if r["category"] == SKIP]
    if skipped:
        names = "、".join(f"{r['field']}（{'—' if r['baseline'] is None else _fmt(r['baseline'])}"
                          f"→{'—' if r['current'] is None else _fmt(r['current'])}）" for r in skipped)
        lines.append(f"- skip：{names}（KPI 纪律：未测不估算）")
    if doc["skipped_meta"]:
        lines.append(f"- 元信息字段不比较：{'、'.join(doc['skipped_meta'])}")
    lines.append("")
    lines.append("## 环比（EVIDENCE 口径：四舍五入到整百分点）")
    bullets = []
    for row in doc["rows"]:
        if row["field"] in ("face_M", "eq_M", "steps") and row["pct"] is not None:
            bullets.append(f"{row['field']} {row['pct']:+.1f}%（{row['pct_points']:+d} pp）")
    lines.append("- " + ("；".join(bullets) if bullets else "（缺 face_M/eq_M/steps）"))
    lines.append("- 判据：`--fail-on-regression` 时只要有 regression 即 exit 1；"
                 "unknown 一律交人工，不静默当绿")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# --self-test（hermetic：夹具生成假 KPI 行）
# ---------------------------------------------------------------------------

def _load_fixtures():
    path = os.path.join(TOOLS_DIR, "tool_fixtures.py")
    if not os.path.isfile(path):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("_regression_diff_fixtures", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001
        return None
    return mod


def _quiet_main(argv: list):
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return main(argv)


def run_self_test() -> int:
    fixtures = _load_fixtures()
    if fixtures is None:
        print("[regression_diff self-test] FAIL：缺 tools/tool_fixtures.py 或导入失败")
        return 1
    results = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, detail))
        print(("[regression_diff self-test] %-22s %s %s"
               % (name, "PASS" if ok else "FAIL", detail)).rstrip())

    def run(td, tag, base, cur, extra=()):
        root = os.path.join(td, tag)
        os.makedirs(root, exist_ok=True)
        bp, cp = os.path.join(root, "base.json"), os.path.join(root, "cur.json")
        fixtures.write_json(bp, base)
        fixtures.write_json(cp, cur)
        out = os.path.join(root, "out")
        rc = _quiet_main(["--baseline", bp, "--current", cp, "--out", out, *extra])
        with open(os.path.join(out, "regression_diff.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        with open(os.path.join(out, "regression_diff.md"), encoding="utf-8") as fh:
            md = fh.read()
        return rc, doc, md

    def rows_by_field(doc):
        return {r["field"]: r for r in doc["rows"]}

    base, cur = fixtures.kpi_pair_regression()

    def t_improved(td):
        _, doc, _ = run(td, "improved", base, cur)
        r = rows_by_field(doc)["face_M"]
        if r["category"] != IMPROVED or r["pct_points"] != -40:
            return False, f"face_M {r['category']} / {r['pct_points']} pp"
        return True, f"face_M 40→24 improved（{r['pct']:+.1f}% / {r['pct_points']:+d} pp）"

    def t_regression(td):
        rc, doc, md = run(td, "regression", base, cur)
        r = rows_by_field(doc)["steps"]
        if r["category"] != REGRESSION:
            return False, f"steps {r['category']}"
        if rc != 0:
            return False, f"未加 --fail-on-regression 却 exit {rc}"
        if "## 回归项" not in md or "steps" not in md:
            return False, "md 未列回归项"
        return True, f"steps 300→360 regression（+20 pp）；不加旗标 exit 0"

    def t_fail_on_regression(td):
        rc, _, _ = run(td, "fail-flag", base, cur, extra=("--fail-on-regression",))
        if rc != 1:
            return False, f"--fail-on-regression 未 exit 1（{rc}）"
        return True, "--fail-on-regression → exit 1"

    def t_skip_null(td):
        _, doc, md = run(td, "skip", base, cur)
        r = rows_by_field(doc)["corpus_sha"]
        if r["category"] != SKIP:
            return False, f"corpus_sha {r['category']}"
        if "未测" not in md:
            return False, "md 未标未测"
        return True, "corpus_sha null → skip（不估算）"

    def t_expected_suites(td):
        _, doc, _ = run(td, "expected", base, cur)
        r = rows_by_field(doc)["full_suites"]
        if r["category"] != EXPECTED or r["change"] != "+1":
            return False, f"full_suites {r['category']} / {r['change']}"
        return True, "full_suites 106→107 expected"

    def t_unknown_sha(td):
        b = fixtures.kpi_row(fixtures.THIS_BATCH - 1, audit_csv_sha="a" * 64)
        c = fixtures.kpi_row(fixtures.THIS_BATCH, audit_csv_sha="b" * 64)
        _, doc, md = run(td, "unknown", b, c)
        r = rows_by_field(doc)["audit_csv_sha"]
        if r["category"] != UNKNOWN:
            return False, f"audit_csv_sha {r['category']}"
        if "需人工" not in md:
            return False, "md 未标需人工"
        return True, "audit_csv_sha 变 → unknown（交人工）"

    def t_container_batches(td):
        root = os.path.join(td, "container")
        os.makedirs(root, exist_ok=True)
        kpi = {"schema": 1, "batches": {str(fixtures.THIS_BATCH - 1): base,
                                        str(fixtures.THIS_BATCH): cur}}
        path = fixtures.write_json(os.path.join(root, "kpi.json"), kpi)
        out = os.path.join(root, "out")
        rc = _quiet_main(["--baseline", path, "--current", path,
                          "--baseline-batch", str(fixtures.THIS_BATCH - 1),
                          "--current-batch", str(fixtures.THIS_BATCH), "--out", out])
        with open(os.path.join(out, "regression_diff.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        if rc != 0 or rows_by_field(doc)["face_M"]["category"] != IMPROVED:
            return False, f"容器选行失败（exit {rc}）"
        return True, "整份 batch-kpi.json + --*-batch 选行可用"

    def t_determinism(td):
        _, d1, m1 = run(td, "det1", base, cur)
        _, d2, m2 = run(td, "det2", base, cur)
        if m1 != m2:
            return False, "md 两次不同"
        a = {k: v for k, v in d1.items() if k != "generated_at"}
        b = {k: v for k, v in d2.items() if k != "generated_at"}
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            return False, "json 除时间戳外有差异"
        return True, "两遍逐字节相同（除 generated_at）"

    print("[regression_diff self-test] hermetic 夹具（tempfile；不读真实 KPI/仓）")
    with _tmpdir() as td:
        check("improved_cost", lambda: t_improved(td))
        check("regression_steps", lambda: t_regression(td))
        check("fail_on_regression", lambda: t_fail_on_regression(td))
        check("skip_null", lambda: t_skip_null(td))
        check("expected_suites", lambda: t_expected_suites(td))
        check("unknown_sha", lambda: t_unknown_sha(td))
        check("kpi_container", lambda: t_container_batches(td))
        check("determinism", lambda: t_determinism(td))

    ok = sum(1 for _, o, _ in results if o)
    print(f"[regression_diff self-test] {ok}/{len(results)} PASS"
          + ("" if ok == len(results) else " ← 有失败"))
    return 0 if ok == len(results) else 1


def _tmpdir():
    import tempfile
    return tempfile.TemporaryDirectory(prefix="regression-diff-selftest-")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        description="基线 vs 本批 回归差（B-145 波1）：两份 JSON → 字段级变化表 + 分类",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python3 tools/regression_diff.py --baseline b76.json --current b77.json "
               "--out <outdir> [--fail-on-regression]")
    ap.add_argument("--baseline", help="基线行 JSON（或整份 batch-kpi.json）")
    ap.add_argument("--current", help="本批行 JSON（或整份 batch-kpi.json）")
    ap.add_argument("--baseline-batch", help="基线是 batch-kpi 容器时用它选行（如 76）")
    ap.add_argument("--current-batch", help="本批是 batch-kpi 容器时用它选行（如 77）")
    ap.add_argument("--out", help="输出目录（regression_diff.md + regression_diff.json）")
    ap.add_argument("--baseline-label", help="可选：基线显示标签（行 JSON 无 batch 字段时用）")
    ap.add_argument("--current-label", help="可选：本批显示标签")
    ap.add_argument("--policy", help="可选：分类策略 JSON（覆盖 DEFAULT_POLICY 的键）")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="任一项 regression 时 exit 1")
    ap.add_argument("--self-test", action="store_true", help="跑 hermetic 自检（≥5 用例）")
    ap.add_argument("--version", action="version", version=f"regression_diff {TOOL_VERSION}")
    args = ap.parse_args(argv)

    if args.self_test:
        sys.dont_write_bytecode = True
        return run_self_test()
    if not args.baseline or not args.current or not args.out:
        ap.error("--baseline / --current / --out 为必填（或使用 --self-test）")

    try:
        base, base_src = load_row(args.baseline, args.baseline_batch, "baseline")
        cur, cur_src = load_row(args.current, args.current_batch, "current")
    except ValueError as exc:
        print(f"[regression_diff] {exc}", file=sys.stderr)
        return 2
    policy = {}
    if args.policy:
        try:
            with open(args.policy, "r", encoding="utf-8") as fh:
                policy = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[regression_diff] --policy 不可用（{type(exc).__name__}）", file=sys.stderr)
            return 2
        if not isinstance(policy, dict):
            print("[regression_diff] --policy 必须是 JSON 对象", file=sys.stderr)
            return 2

    diff = build_diff(base, cur, policy)
    doc = {
        "tool": "regression_diff",
        "version": TOOL_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "baseline": {"label": base.get("batch", args.baseline_batch or args.baseline_label or "（未标）"),
                     "source": base_src},
        "current": {"label": cur.get("batch", args.current_batch or args.current_label or "（未标）"),
                    "source": cur_src},
        **diff,
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "regression_diff.md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(render_md(doc))
    with open(os.path.join(args.out, "regression_diff.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    c = doc["counts"]
    print(f"[regression_diff] baseline {doc['baseline']['label']} → current "
          f"{doc['current']['label']}：improved {c[IMPROVED]} / expected {c[EXPECTED]} / "
          f"regression {c[REGRESSION]} / skip {c[SKIP]} / unknown {c[UNKNOWN]}；"
          f"→ {args.out}/regression_diff.md + .json")
    if c[REGRESSION] and args.fail_on_regression:
        print(f"[regression_diff] 有 {c[REGRESSION]} 项 regression → exit 1", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
