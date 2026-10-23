#!/usr/bin/env python3
r"""case_compare.py —— 批次运行对比 + corpus 回归（B-206，case-lab；owner A）

判据全在本模块（LEAD R1：batch_case_test.py 只接线，不复制判据）。纯 stdlib、离线、确定性；
import 无副作用；不读真实稿/网络/仓库，只写显式 `--out`。
允许：cite↑；parencite↑；bib↑ 且新增 bib 键都被当前正文引用（closure.cited，缺则
l1.cite_keys；否则 bib_up_uncited）；findings 减少且无新 code、同 code 级别不升；
l1_cite_missing↓；l1_ok false→true。违规：cite↓；bib↓；新增 finding code；同 code 级别升
（P2<P1<P0）；pages 变；l1_ok true→false；sha_ok true→false；案例增/删；允许清单之外的任何
变化 → unlisted_change。缺失：基线有/当前无 → missing_in_current；基线无/当前有 →
missing_in_baseline（保守）。
CLI 退出码：compare-run 0/1/2（无违规/有违规/用法或读写错误）；corpus-diff 0/1/2；
self-test 0/1。
"""
import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

VERSION = "1.0.0"
LEVEL_RANK = {"P2": 0, "P1": 1, "P0": 2}
METRIC_ORDER = {n: i for i, n in enumerate(("case", "cite", "parencite", "footnote", "bib", "bib_keys", "pages", "sha_ok", "findings", "l1_ok", "l1_cite_missing", "l1_bib_missing"))}
CSV_HEADER = ["issue", "slug", "channel", "lang", "ok", "raw_ratio", "content_acc", "l2", "l3", "ref_chars", "src_tbl", "out_tbl", "src_img", "out_img", "src_shape", "out_shape", "error"]
# 简单 metric 统一规则：(增→allowed 类别, 减→violation 类别, 允许方向 +1/-1/0)
SIMPLE_RULES = {"cite": ("cite_up", "cite_decrease", 1), "parencite": ("parencite_up", "unlisted_change", 1), "footnote": (None, "unlisted_change", 0), "pages": (None, "pages_changed", 0), "l1_cite_missing": ("l1_cite_missing_down", "unlisted_change", -1), "l1_bib_missing": (None, "unlisted_change", 0)}


def _dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict) or key not in obj: return None, True
        obj = obj[key]
    return obj, False


def _as_int(value, missing):
    if missing or value is None or isinstance(value, bool): return None, True
    try: return int(value), False
    except (TypeError, ValueError): return None, True


def _findings(case):
    raw, missing = _dig(case, "findings")
    if missing or not isinstance(raw, list): return None, True
    out = []
    for item in raw:
        if not isinstance(item, dict) or item.get("level") not in LEVEL_RANK or not isinstance(item.get("code"), str) or not item["code"]: return None, True
        out.append((item["level"], item["code"]))
    return out, False


def _l1(case):
    l1, missing = _dig(case, "l1")
    if missing or not isinstance(l1, dict): return (None, True), (None, True), (None, True)
    ok, cm, rc = l1.get("ok"), l1.get("cite_missing"), l1.get("ref_coverage")
    bm = _as_int(rc["missing_n"], rc["missing_n"] is None) if isinstance(rc, dict) and "missing_n" in rc else (None, True)
    return ((ok, False) if isinstance(ok, bool) else (None, True)), ((len(cm), False) if isinstance(cm, list) else (None, True)), bm


def _cited(case):
    for path in (("closure", "cited"), ("l1", "cite_keys")):
        value, missing = _dig(case, *path)
        if not missing and isinstance(value, list): return [str(x) for x in value], False
    return None, True


def _extract(case):
    case = case if isinstance(case, dict) else {}
    out = {name: _as_int(*_dig(case, *path)) for name, path in (("cite", ("tex", "cite")), ("parencite", ("tex", "parencite")), ("footnote", ("tex", "footnote")), ("pages", ("pdf_pages",)))}
    out["bib"] = _as_int(*_dig(case, "bib", "entries"))
    keys, missing = _dig(case, "closure", "bib_keys")
    out["bib_keys"] = (None, True) if missing or not isinstance(keys, list) else ([str(x) for x in keys], False)
    sha, absent = (case["sha_ok"], False) if "sha_ok" in case else (None, True)
    out["sha_ok"] = (sha, False) if not absent and (sha is None or isinstance(sha, bool)) else (None, True)
    out["findings"] = _findings(case); out["l1_ok"], out["l1_cite_missing"], out["l1_bib_missing"] = _l1(case); out["cited"] = _cited(case)
    return out


def _compare_case(key, base_case, cur_case):
    b, c = _extract(base_case), _extract(cur_case)
    violations, allowed, metrics, missing = [], [], {}, []

    def add(bucket, metric, category, before, after, detail):
        bucket.append({"case": key, "metric": metric, "category": category, "before": before, "after": after, "detail": detail}); metrics.setdefault(metric, {"before": before, "after": after})

    def viol(metric, category, before, after, detail): add(violations, metric, category, before, after, detail)

    def ok_change(metric, category, before, after, detail): add(allowed, metric, category, before, after, detail)

    def missing_checked(metric):
        bv, bm = b[metric]; cv, cm = c[metric]
        if bm and cm: missing.append(metric); return True
        if bm or cm:
            missing.append(metric); viol(metric, "missing_in_baseline" if bm else "missing_in_current", bv, cv, "基线无而当前有（保守）" if bm else "基线有而当前无值"); return True
        return False

    for metric, (up_cat, down_cat, allow_dir) in SIMPLE_RULES.items():
        if missing_checked(metric): continue
        bv, cv = b[metric][0], c[metric][0]
        if bv == cv: continue
        if allow_dir and (cv - bv) * allow_dir > 0: ok_change(metric, up_cat, bv, cv, f"{metric} {bv} → {cv}")
        else: viol(metric, down_cat, bv, cv, f"{metric} {'增' if cv > bv else '减'} {bv} → {cv}")

    bib_action = ""
    if not missing_checked("bib"):
        bv, cv = b["bib"][0], c["bib"][0]
        if cv > bv:
            bib_action = "up"; bk, bkm = b["bib_keys"]; ck, ckm = c["bib_keys"]
            if bkm or ckm:
                missing.append("bib_keys"); metrics.setdefault("bib_keys", {"before": bk, "after": ck}); viol("bib", "bib_up_uncited", bv, cv, "bib 增加但 bib_keys 缺失，无法校验新键（保守）")
            elif c["cited"][1]: viol("bib", "bib_up_uncited", bv, cv, "bib 增加但无当前引用键（保守）")
            else:
                added = [k for k in ck if k not in set(bk)]; uncited = [k for k in added if k not in set(c["cited"][0])]
                if uncited: viol("bib", "bib_up_uncited", bv, cv, "新增 bib 键未被正文引用：" + ", ".join(uncited))
                else:
                    ok_change("bib", "bib_up_cited", bv, cv, "bib 增加且新键均被引用：" + (", ".join(added) or "—"))
                    lost = [k for k in bk if k not in set(ck)]
                    if lost: viol("bib_keys", "unlisted_change", bk, ck, "bib 增加但丢失基线键（允许清单之外）：" + ", ".join(lost))
        elif cv < bv: bib_action = "down"; viol("bib", "bib_decrease", bv, cv, f"文献数减少 {bv} → {cv}")
        else: bib_action = "same"
    if bib_action != "up" and not missing_checked("bib_keys"):
        bk, ck = b["bib_keys"][0], c["bib_keys"][0]
        if set(bk) != set(ck) and bib_action != "down": viol("bib_keys", "unlisted_change", bk, ck, "bib 键集合变化但 bib 数未增加（允许清单之外）")

    if not missing_checked("sha_ok"):
        bv, cv = b["sha_ok"][0], c["sha_ok"][0]
        if bv is True and cv is False: viol("sha_ok", "sha_ok_regressed", bv, cv, "确定性 true → false")
        elif bv != cv: viol("sha_ok", "unlisted_change", bv, cv, "sha_ok 变化不在允许清单内（None=未核验，视作值参与比较）")

    findings_added, findings_removed = [], []
    if not missing_checked("findings"):
        bl, cl = b["findings"][0], c["findings"][0]
        bb = {code: level for level, code in bl}; cb = {code: level for level, code in cl}
        new = [code for code in cb if code not in bb]; removed = [code for code in bb if code not in cb]
        up = [(code, bb[code], cb[code]) for code in bb if code in cb and LEVEL_RANK[cb[code]] > LEVEL_RANK[bb[code]]]
        for code in new: viol("findings", "new_finding_code", None, cb[code], f"新增 finding code {code}（基线无；当前级别 {cb[code]}）")
        for code, before_level, after_level in up: viol("findings", "finding_level_up", before_level, after_level, f"同 code {code} 级别升高（P2<P1<P0）：{before_level} → {after_level}")
        if len(cl) < len(bl) and not new and not up: ok_change("findings", "findings_down", len(bl), len(cl), "findings 减少且无新 code、无级别升高")
        elif not new and not up and bl != cl: viol("findings", "unlisted_change", len(bl), len(cl), "findings 变化不在允许清单内（数量未减少或仅级别降低）")
        findings_added = [{"level": cb[code], "code": code} for code in new]; findings_removed = [{"level": bb[code], "code": code} for code in removed]

    if not missing_checked("l1_ok"):
        bv, cv = b["l1_ok"][0], c["l1_ok"][0]
        if bv is False and cv is True: ok_change("l1_ok", "l1_ok_recovered", bv, cv, "L1 判定 false → true")
        elif bv is True and cv is False: viol("l1_ok", "l1_ok_regressed", bv, cv, "L1 判定 true → false")
        elif bv != cv: viol("l1_ok", "unlisted_change", bv, cv, "l1.ok 变化不在允许清单内")

    detail = {"changed": bool(metrics) or bool(findings_added) or bool(findings_removed), "metrics": metrics, "findings_added": findings_added, "findings_removed": findings_removed}
    if missing: detail["missing_metrics"] = missing
    return detail, violations, allowed


def _load_json(path, what):
    try: data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc: raise ValueError(f"无法读取{what}：{path}（{exc}）") from exc
    except json.JSONDecodeError as exc: raise ValueError(f"{what}不是合法 JSON：{path}（{exc}）") from exc
    if not isinstance(data, dict): raise ValueError(f"{what}顶层必须是 JSON 对象：{path}")
    return data


def _cases_of(data, what):
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, dict) or not cases: raise ValueError(f"{what} 的 cases 必须是非空 dict")
    return cases


def _totals(cases):
    out = {"cases": len(cases), "p0": 0, "p1": 0, "p2": 0, "sha_ok": 0, "sha_checked": 0, "cite": 0, "bib": 0, "pages": 0, "l1_ok": 0, "l1_cite_missing": 0, "l1_bib_missing": 0}
    for case in cases.values():
        ex = _extract(case)
        for level, _code in (ex["findings"][0] or []): out[level.lower()] += 1
        out["sha_checked"] += ex["sha_ok"][0] is not None; out["sha_ok"] += ex["sha_ok"][0] is True
        for name in ("cite", "bib", "pages", "l1_cite_missing", "l1_bib_missing"): out[name] += ex[name][0] or 0
        out["l1_ok"] += ex["l1_ok"][0] is True
    return out


def compare_run(current_run: dict, baseline_path, out_dir=None, label="") -> dict:
    """逐篇对比 current_run 与基线 summary.json；`out_dir` 非空时落盘 compare.md/json。
    本函数不 print；stdout 末行由调用方打印 `result["summary_line"]`。"""
    cur_cases = _cases_of(current_run, "current_run"); base_cases = _cases_of(_load_json(baseline_path, "基线 summary.json"), "baseline")
    base_keys, cur_keys = set(base_cases), set(cur_cases)
    violations, allowed, details = [], [], {}

    def case_violation(key, category, before, after, detail):
        violations.append({"case": key, "metric": "case", "category": category, "before": before, "after": after, "detail": detail})
        details[key] = {"changed": True, "metrics": {}, "findings_added": [], "findings_removed": []}

    for key in sorted(base_keys & cur_keys):
        details[key], case_violations, case_allowed = _compare_case(key, base_cases[key], cur_cases[key]); violations += case_violations; allowed += case_allowed
    for key in sorted(base_keys - cur_keys): case_violation(key, "case_removed", "present", None, "基线有该案例、当前没有（案例消失）")
    for key in sorted(cur_keys - base_keys): case_violation(key, "case_added", None, "present", "当前有该案例、基线没有（案例新增）")
    sort_key = lambda x: (x["case"], METRIC_ORDER.get(x["metric"], 99), x["category"])  # noqa: E731
    violations.sort(key=sort_key); allowed.sort(key=sort_key)
    result = {"label": label or "", "baseline": str(baseline_path), "current_cases": len(cur_cases), "baseline_cases": len(base_cases), "violations": violations, "allowed_changes": allowed, "cases": details, "totals_before": _totals(base_cases), "totals_after": _totals(cur_cases), "summary_line": f"COMPARE violations={len(violations)} allowed_changes={len(allowed)}"}
    if out_dir:
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        (out / "compare.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "compare.md").write_text(_render_md(result), encoding="utf-8")
    return result


def _cell(value):
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else ("—" if value is None else str(value))
    return text.replace("|", "\\|").replace("\n", " ")[:200]


def _table(rows, columns):
    return ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)] + ["| " + " | ".join(_cell(row.get(col)) for col in columns) + " |" for row in rows]


def _render_md(result):
    violations, allowed = result["violations"], result["allowed_changes"]; unchanged = sum(1 for item in result["cases"].values() if not item["changed"])
    lines = ["# 运行对比（case_compare）", "", f"- 基线：`{result['baseline']}`", f"- 当前：`{result['label'] or '—'}`", f"- 案例：当前 {result['current_cases']} / 基线 {result['baseline_cases']}；未变化 {unchanged}", "", f"## 违规（{len(violations)}）", ""]
    lines += _table(violations, ["case", "metric", "category", "before", "after", "detail"]) if violations else ["（无）"]
    lines += ["", f"## 允许变化（{len(allowed)}）", ""]
    lines += _table(allowed, ["case", "metric", "before", "after", "detail"]) if allowed else ["（无）"]
    lines += ["", f"未变化案例：{unchanged}", "", result["summary_line"], ""]
    return "\n".join(lines)


def _read_corpus_csv(path):
    p = Path(path)
    try:
        with p.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if [str(f).strip() for f in (reader.fieldnames or [])] != CSV_HEADER: raise ValueError(f"CSV 表头不符（需要 {','.join(CSV_HEADER)}）：{p}")
            rows = {}
            for lineno, raw in enumerate(reader, 2):
                issue = str(raw.get("issue") or "").strip(); slug = str(raw.get("slug") or "").strip()
                if not issue or not slug: raise ValueError(f"{p}:{lineno} 缺 issue/slug")
                if (issue, slug) in rows: raise ValueError(f"{p}:{lineno} 重复行 {issue}/{slug}")
                try: ok, l2, l3, src_tbl = (int(str(raw[col]).strip()) for col in ("ok", "l2", "l3", "src_tbl"))
                except (KeyError, TypeError, ValueError) as exc: raise ValueError(f"{p}:{lineno} 数值列非法：{exc}") from exc
                if ok not in (0, 1): raise ValueError(f"{p}:{lineno} ok 只能是 0/1：{ok}")
                rows[(issue, slug)] = {"issue": issue, "slug": slug, "ok": ok, "l2": l2, "l3": l3, "src_tbl": src_tbl, "error": str(raw.get("error") or "").strip()}
    except OSError as exc: raise ValueError(f"无法读取 CSV：{p}（{exc}）") from exc
    return rows


def corpus_diff(baseline_path, new_path, out_dir=None) -> dict:
    """按 (issue, slug) 对齐 corpus.csv；变坏 = ok 1→0 / error 新增 / l2·l3 下降 /
    src_tbl 增加 / 基线行消失。新增行只记 info（不算变坏）。"""
    base_rows, new_rows = _read_corpus_csv(baseline_path), _read_corpus_csv(new_path)
    worsened, info = [], []
    for key in sorted(set(base_rows) | set(new_rows)):
        label = f"{key[0]}/{key[1]}"; base, new = base_rows.get(key), new_rows.get(key)
        if base is None: info.append({"case": label, "kind": "row_added", "detail": "基线无此行（新增，不算变坏）"}); continue
        if new is None: worsened.append({"case": label, "category": "row_missing", "column": "row", "before": "present", "after": None, "detail": "基线行在新表中缺失（案例消失）"}); continue
        conditions = []
        if base["ok"] == 1 and new["ok"] == 0: conditions.append(("ok_regressed", "ok", 1, 0, "ok 1→0"))
        if not base["error"] and new["error"]: conditions.append(("error_new", "error", base["error"], new["error"], "error 新增"))
        for column in ("l2", "l3"):
            if new[column] < base[column]: conditions.append((column + "_down", column, base[column], new[column], f"{column} 下降 {base[column]} → {new[column]}"))
        if new["src_tbl"] > base["src_tbl"]: conditions.append(("src_tbl_up", "src_tbl", base["src_tbl"], new["src_tbl"], "src_tbl 增加"))
        for category, column, before, after, detail in conditions: worsened.append({"case": label, "category": category, "column": column, "before": before, "after": after, "detail": detail})
        if not conditions:
            better = [text for text, hit in (("ok 0→1", base["ok"] == 0 and new["ok"] == 1), ("error 消失", bool(base["error"]) and not new["error"]), ("l2 上升", new["l2"] > base["l2"]), ("l3 上升", new["l3"] > base["l3"])) if hit]
            if better: info.append({"case": label, "kind": "improved", "detail": "；".join(better)})
    worsened_cases = len({item["case"] for item in worsened})
    result = {"baseline": str(baseline_path), "new": str(new_path), "worsened": worsened_cases, "worsened_conditions": len(worsened), "worsened_rows": worsened, "info": info, "summary_line": f"CORPUS_DIFF worsened={worsened_cases}"}
    if out_dir:
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        (out / "corpus_diff.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        md = ["# corpus 回归 diff（case_compare --corpus-diff）", "", f"- 基线：`{result['baseline']}`", f"- 新表：`{result['new']}`", "", f"## 变坏（{worsened_cases} 个案例 / {len(worsened)} 条条件）", ""]
        md += _table(worsened, ["case", "category", "column", "before", "after", "detail"]) if worsened else ["（无）"]
        md += ["", f"## info（{len(info)}）", ""] + ([f"- {item['case']}：{item['detail']}" for item in info] or ["（无）"]) + ["", result["summary_line"], ""]
        (out / "corpus_diff.md").write_text("\n".join(md), encoding="utf-8")
    return result


def _mk_case(*, cite=10, parencite=10, footnote=1, bib=5, bib_keys="ABCDE", cited="ABCDEF", pages=10, sha=True, findings=(("P1", "X"),), l1_ok=True, cite_missing=(), bib_missing=0):
    return {"tex": {"cite": cite, "parencite": parencite, "footnote": footnote}, "bib": {"entries": bib}, "closure": {"bib_keys": list(bib_keys), "cited": list(cited)}, "pdf_pages": pages, "sha_ok": sha, "findings": [{"level": level, "code": code} for level, code in findings], "l1": {"ok": l1_ok, "cite_missing": list(cite_missing), "ref_coverage": {"missing_n": bib_missing}}}


def _corpus_row(issue, slug, ok=1, l2=10, l3=5, src_tbl=1, error=""):
    return ",".join([issue, slug, "Word", "zh", str(ok), "0.900000", "0.800000", str(l2), str(l3), "100", str(src_tbl), "1", "0", "0", "0", "0", f'"{error}"'])


def run_self_test() -> int:
    import contextlib
    import io
    print("case_compare --self-test")
    checks = []

    def check(name, cond):
        checks.append(bool(cond)); print(f"  [{'OK' if cond else 'FAIL'}] {name}")

    def raises(func, *args):
        try: func(*args)
        except ValueError: return True
        return False

    with tempfile.TemporaryDirectory(prefix="case_compare_self_") as tmpdir:
        tmp = Path(tmpdir)
        good_base = dict(findings=(("P1", "X"), ("P2", "Y")), l1_ok=False, cite_missing="ABC")
        good_cur = dict(cite=12, parencite=12, bib=6, bib_keys="ABCDEF", findings=(("P2", "Y"),), l1_ok=True, cite_missing=())
        bad_base = dict(bib_missing=2)
        bad_cur = dict(cite=9, parencite=9, footnote=2, bib=4, pages=11, sha=False, l1_ok=False, bib_missing=1)
        base = {"good": _mk_case(**good_base), "bad": _mk_case(**bad_base), "findings": _mk_case(findings=(("P2", "X"),)), "bib-keys": _mk_case(), "sha-none": _mk_case(), "uncited": _mk_case(), "no-tex": _mk_case(), "same": _mk_case(), "missing": _mk_case(), "removed": _mk_case()}
        cur = {"good": _mk_case(**good_cur), "bad": _mk_case(**bad_cur), "findings": _mk_case(findings=(("P1", "X"), ("P2", "Z"))), "bib-keys": _mk_case(bib_keys="ABCDF"), "sha-none": _mk_case(sha=None), "uncited": _mk_case(bib=6, bib_keys="ABCDEF", cited="ABCDE"), "missing": _mk_case(), "same": _mk_case(), "no-tex": _mk_case(), "added": _mk_case()}
        del base["no-tex"]["tex"]; del cur["missing"]["tex"]
        baseline = tmp / "baseline.json"; baseline.write_text(json.dumps({"cases": base}, ensure_ascii=False), encoding="utf-8")
        result = compare_run({"cases": cur}, baseline, tmp / "out", label="self-test")
        allowed = {(v["case"], v["category"]) for v in result["allowed_changes"]}
        hits = {(v["case"], v["category"]) for v in result["violations"]}
        check("允许白名单 6 类且无多报", allowed == {("good", "cite_up"), ("good", "parencite_up"), ("good", "bib_up_cited"), ("good", "findings_down"), ("good", "l1_cite_missing_down"), ("good", "l1_ok_recovered")})
        check("违规白名单全中（cite↓/pages/新code/升格/sha/l1/bib↓/键集合/bib↑未引/增删）", hits >= {("bad", "cite_decrease"), ("bad", "pages_changed"), ("bad", "sha_ok_regressed"), ("bad", "l1_ok_regressed"), ("bad", "bib_decrease"), ("bad", "unlisted_change"), ("findings", "new_finding_code"), ("findings", "finding_level_up"), ("bib-keys", "unlisted_change"), ("uncited", "bib_up_uncited"), ("removed", "case_removed"), ("added", "case_added")})
        check("缺失双向各 3 + 未变化案例 + summary_line/计数/label", sum(1 for v in result["violations"] if v["category"] == "missing_in_current") == 3 and sum(1 for v in result["violations"] if v["category"] == "missing_in_baseline") == 3 and result["cases"]["same"]["changed"] is False and result["summary_line"] == "COMPARE violations=21 allowed_changes=6" and result["current_cases"] == 10 and result["baseline_cases"] == 10 and result["label"] == "self-test" and result["baseline"] == str(baseline))
        md_path = tmp / "out" / "compare.md"; saved = json.loads((tmp / "out" / "compare.json").read_text(encoding="utf-8"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): rc = main(["--compare-run", str(baseline), str(baseline), "--label", "cli"])
        check("round-trip md/json + CLI 零差异 exit 0、stdout 末行 summary_line", result["summary_line"] in md_path.read_text(encoding="utf-8") and saved["summary_line"] == result["summary_line"] and saved["cases"] == result["cases"] and rc == 0 and buf.getvalue().strip().splitlines()[-1] == "COMPARE violations=0 allowed_changes=0")
        bad_json = tmp / "bad.json"; bad_json.write_text('{"cases": {}}', encoding="utf-8")
        check("非法输入（空 cases/非 dict/文件不存在）→ ValueError", all(raises(compare_run, current, path) for current, path in (({"cases": cur}, bad_json), ({"cases": []}, baseline), ({"cases": cur}, tmp / "missing.json"))))
        csv_base = [",".join(CSV_HEADER), _corpus_row("90", "01"), _corpus_row("90", "02"), _corpus_row("90", "03"), _corpus_row("90", "04"), _corpus_row("90", "05"), _corpus_row("90", "06"), _corpus_row("90", "08", ok=0)]
        csv_new = [",".join(CSV_HEADER), _corpus_row("90", "01"), _corpus_row("90", "02", ok=0), _corpus_row("90", "03", error="boom"), _corpus_row("90", "04", l2=9), _corpus_row("90", "05", src_tbl=2), _corpus_row("90", "07"), _corpus_row("90", "08", ok=1)]
        corpus_base, corpus_new = tmp / "corpus_base.csv", tmp / "corpus_new.csv"
        corpus_base.write_text("\n".join(csv_base) + "\n", encoding="utf-8"); corpus_new.write_text("\n".join(csv_new) + "\n", encoding="utf-8")
        diff = corpus_diff(corpus_base, corpus_new, tmp / "corpus_out")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): crc = main(["--corpus-diff", str(corpus_base), str(corpus_new)])
        check("corpus：5 类变坏/未变化不误报/info/落盘/CLI exit 1 + 末行", diff["worsened"] == 5 and {v["category"] for v in diff["worsened_rows"]} == {"ok_regressed", "error_new", "l2_down", "src_tbl_up", "row_missing"} and not any(v["case"] == "90/01" for v in diff["worsened_rows"]) and {v["case"] for v in diff["info"]} >= {"90/07", "90/08"} and "CORPUS_DIFF worsened=5" in (tmp / "corpus_out" / "corpus_diff.md").read_text(encoding="utf-8") and json.loads((tmp / "corpus_out" / "corpus_diff.json").read_text(encoding="utf-8"))["worsened"] == 5 and crc == 1 and buf.getvalue().strip().splitlines()[-1] == "CORPUS_DIFF worsened=5")
        bad_csv = tmp / "bad.csv"; bad_csv.write_text("issue,slug\n90,01\n", encoding="utf-8")
        check("corpus：表头不符 → ValueError", raises(corpus_diff, bad_csv, corpus_new))

    failed = checks.count(False)
    print()
    print(f"SELF-TEST {'FAIL' if failed else 'PASS'} n={len(checks)}" + (f" failed={failed}" if failed else ""))
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="case_compare.py", description="批次运行对比（--compare-run）与 corpus 回归 diff（--corpus-diff）；纯 stdlib、离线、只写 --out")
    ap.add_argument("--self-test", action="store_true", help="跑内置自检（hermetic，秒级）")
    ap.add_argument("--compare-run", nargs=2, metavar=("BASELINE_JSON", "CURRENT_JSON"), help="比较基线 summary.json 与当前 run/summary JSON")
    ap.add_argument("--corpus-diff", nargs=2, metavar=("BASELINE_CSV", "NEW_CSV"), help="比较两份 corpus.csv（按 issue+slug 对齐）")
    ap.add_argument("--out", default="", help="输出目录（compare.md/json 或 corpus_diff.*）")
    ap.add_argument("--label", default="", help="当前 run 的标签（写进 md/json）")
    ap.add_argument("--version", action="version", version=f"case_compare.py {VERSION}")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test: return run_self_test()
    try:
        if args.compare_run:
            result = compare_run(_load_json(args.compare_run[1], "当前 run"), args.compare_run[0], args.out or None, args.label)
            print(result["summary_line"]); return 1 if result["violations"] else 0
        if args.corpus_diff:
            result = corpus_diff(args.corpus_diff[0], args.corpus_diff[1], args.out or None)
            print(result["summary_line"]); return 1 if result["worsened"] else 0
    except ValueError as exc: print(f"[用法] {exc}", file=sys.stderr); return 2
    except OSError as exc: print(f"[IO] {exc}", file=sys.stderr); return 2
    build_parser().print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
