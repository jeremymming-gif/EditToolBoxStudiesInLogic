#!/usr/bin/env python3
r"""目录摘要推送（B-225）：期目录 → 公众号推送文案 docx。

用法（对外契约见 `Engine/docs/ENGINE_API.md`）
---------------------------------------------
    python3 Engine/dirpush.py --issue <期目录> [--out <输出根>] [--with-en-toc] [--json] [--dry-run]
    python3 Engine/dirpush.py --issue <任一期目录> --annual [--year <yyyy>] \
        [--include-issues 1,2,3] [--out <根>] [--json] [--dry-run]

  · `--issue` 必填：当期模式 = 具体期目录；年度模式 = 该年**任一期**目录（扫其父目录）
  · `--out` 省略 = 本机默认 `~/Documents/sysu/编辑部/目录摘要推送`
  · `--dry-run` 只采集+校验，**不写任何文件**，stdout 给 JSON 预览（GUI 用）
  · `--json`  stdout **只有一个 JSON 对象**，人类日志全走 stderr
  · 退出码：0 成功（含 warning）/ 2 输入或用法错误 / 1 内部异常

设计约束（与 spec B-225 一致）
------------------------------
  · 输入期目录**只读**；不写 `Engine/out/`；不产 PDF；
  · 同名 docx **直接覆盖**、不留 `.bak`（临时文件写完 `os.replace`）；
  · 同输入两遍产物**逐字节相同**（固定 zip 时间戳、固定 docProps、无当前时间）；
  · 外壳只拼参数：所有判断都在这里与 `slcn/pushcopy/`（渲染逻辑不落外壳）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

from slcn import platform as _plat
from slcn import paths as _paths
from slcn.pushcopy import collect as _collect

#: `--json` 契约版本（Lead 冻结；改字段名 = 破坏 Phase B 外壳）
JSON_SCHEMA = 1


def _out_root(args) -> str:
    root = args.out or _collect.default_out_root()
    return os.path.abspath(os.path.expanduser(root))


def _protected_roots() -> list[tuple[str, str]]:
    """输出根落在这些目录内要 stderr WARNING（不阻断，spec §2.5）。"""
    roots: list[tuple[str, str]] = []
    here = os.path.dirname(os.path.abspath(__file__))
    roots.append((os.path.dirname(here), "公开仓"))
    try:
        notes = _paths.notes_root()
        if notes:
            roots.append((notes, "笔记仓"))
    except Exception:  # noqa: BLE001  路径锚点找不到就跳过
        pass
    try:
        manus = _paths.manuscripts_root()
        if manus:
            roots.append((manus, "稿件仓"))
    except Exception:  # noqa: BLE001
        pass
    return roots


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:      # 跨盘符
        return False


def _check_out_root(out_root: str) -> list[str]:
    warnings: list[str] = []
    for root, label in _protected_roots():
        if _inside(out_root, root):
            warnings.append(
                f"输出根位于{label}内（{out_root}），生成物会成为未跟踪文件；建议改到仓库外")
    return warnings


def _issue_json(issue: _collect.IssueData) -> dict:
    return {
        "year": issue.year,
        "number": issue.number,
        "vol": issue.vol,
        "total": issue.total,
        "header_lines": list(issue.header_lines),
        "with_en_toc": bool(issue.with_en_toc),
        "column": None if issue.column is None else {
            "label": issue.column.label,
            "name": issue.column.name,
            "host": issue.column.host,
        },
    }


def _counts(issues: list[_collect.IssueData]) -> dict:
    zh = sum(len(i.zh_entries) for i in issues)
    en = sum(len(i.en_entries) for i in issues if i.with_en_toc)
    articles = sum(len(i.articles) for i in issues)
    if len(issues) == 1:
        return {
            "zh_entries": zh,
            "en_entries": en,
            "articles": articles,
            "abstracts_ok": issues[0].abstracts_ok,
            "abstracts_missing": articles - issues[0].abstracts_ok,
        }
    # 年度模式不生成摘要段（spec：年度只含目录）
    return {
        "zh_entries": zh,
        "en_entries": en,
        "articles": articles,
        "abstracts_ok": 0,
        "abstracts_missing": 0,
    }


def _empty_result(mode: str, dry_run: bool, message: str) -> dict:
    return {
        "schema": JSON_SCHEMA,
        "ok": False,
        "mode": mode,
        "dry_run": bool(dry_run),
        "outputs": [],
        "issue": None,
        "counts": {"zh_entries": 0, "en_entries": 0, "articles": 0,
                   "abstracts_ok": 0, "abstracts_missing": 0},
        "warnings": [message],
        "missing": [],
        "annual": None,
    }


def _emit(obj: dict, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
        sys.stdout.flush()
        return
    if not obj["ok"]:
        return
    issue = obj.get("issue") or {}
    print(f"期次：{issue.get('year')} 年第 {issue.get('number')} 期"
          f"（第 {issue.get('vol')} 卷，总第 {issue.get('total')} 期）"
          if issue.get("number") else "期次：-")
    for line in issue.get("header_lines") or []:
        print(f"  {line}")
    if obj.get("annual"):
        a = obj["annual"]
        print(f"年度：{a['year']} 年，纳入 {len(a['included'])} 期"
              f"（{','.join(str(n) for n in a['included'])}）")
        for rec in a.get("issues") or []:
            print(f"  {rec['number']} 期：{rec['status']}"
                  f"，体例={'中英文对照' if rec['with_en_toc'] else '仅中文'}")
        if a.get("missing_issues"):
            print(f"  缺期：{','.join(str(n) for n in a['missing_issues'])}")
    c = obj["counts"]
    print(f"目录条目：中文 {c['zh_entries']} / 英文 {c['en_entries']}；"
          f"文章 {c['articles']}（摘要取到 {c['abstracts_ok']}）")
    for line in obj.get("warnings") or []:
        print(f"WARNING: {line}", file=sys.stderr)
    for path in obj.get("outputs") or []:
        print(f"{'计划输出' if obj['dry_run'] else '输出'}：{path}")
    if obj["dry_run"]:
        print("（dry-run：未写任何文件）")


def _settings_write(path: str, year: int, number: int, with_en_toc: bool) -> None:
    """体例设置：确定性字段、无时间戳；先写临时文件再 rename（不产生 .bak）。"""
    data = {"schema": _collect.SETTINGS_SCHEMA, "year": year,
            "issue": number, "with_en_toc": bool(with_en_toc)}
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _run_issue(args, out_root: str, as_json: bool) -> int:
    issue_dir = os.path.abspath(os.path.expanduser(args.issue))
    with_en = True if args.with_en_toc else None
    issue = _collect.collect_issue(issue_dir, with_en_toc=with_en, out_root=out_root)
    out_dir, docx_path, settings_path = _collect.issue_output_paths(
        out_root, issue.year, issue.number)
    result = {
        "schema": JSON_SCHEMA,
        "ok": True,
        "mode": "issue",
        "dry_run": bool(args.dry_run),
        "outputs": [docx_path],
        "issue": _issue_json(issue),
        "counts": _counts([issue]),
        "warnings": _check_out_root(out_root) + list(issue.warnings),
        "missing": list(issue.missing),
        "annual": None,
    }
    if not args.dry_run:
        from slcn.pushcopy import render_docx as _render   # 延迟导入：--dry-run 不加载渲染层
        os.makedirs(out_dir, exist_ok=True)
        _render.render_issue_docx(issue, docx_path)
        _settings_write(settings_path, issue.year, issue.number, issue.with_en_toc)
    _emit(result, as_json)
    return 0


def _annual_issue_json(args, out_root: str, year: int) -> dict:
    """年度模式的 `issue` 字段：`--issue` 那一期（缺 TOC 时给能拿到的部分）。"""
    issue_dir = os.path.abspath(os.path.expanduser(args.issue))
    zh_tex = _collect.toc_files(issue_dir)[0]
    parts = _collect.issue_name_parts(issue_dir)
    number = parts[1] if parts else 0
    try:
        with open(zh_tex, encoding="utf-8", errors="replace") as fh:
            info = _collect.parse_issue_header(fh.read())
        return {
            "year": info["year"], "number": info["number"], "vol": info["vol"],
            "total": info["total"], "header_lines": _collect._header_lines(info),
            "with_en_toc": _collect.infer_with_en_toc(out_root, info["year"], info["number"], issue_dir),
            "column": None,
        }
    except (OSError, _collect.CollectError):
        return {
            "year": year, "number": number, "vol": 0, "total": 0,
            "header_lines": [], "with_en_toc": False, "column": None,
        }


def _run_annual(args, out_root: str, as_json: bool) -> int:
    issue_dir = os.path.abspath(os.path.expanduser(args.issue))
    year = args.year
    include: list[int] | None = None
    if args.include_issues:
        try:
            include = [int(x) for x in args.include_issues.split(",") if x.strip()]
        except ValueError:
            raise _collect.CollectError(
                f"--include-issues 只接受逗号分隔的期号：{args.include_issues}") from None
    plan = _collect.collect_annual(issue_dir, year=year, include_issues=include,
                                   out_root=out_root)
    year = plan.year
    docx_path = _collect.annual_output_path(out_root, year)
    warnings: list[str] = _check_out_root(out_root)
    for data in plan.data:
        warnings.extend(f"{data.number} 期：{w}" for w in data.warnings)
    if include is not None:
        found = {n for n, _ in plan.included} | set(plan.excluded)
        for n in include:
            if n not in found:
                warnings.append(f"--include-issues 指定了不存在的期号：{n}")
    with_en_flags = {rec["number"]: rec["with_en_toc"] for rec in plan.issues}
    for n, _d in plan.included:
        if not with_en_flags.get(n):
            warnings.append(f"{n} 期：未找到《目录摘要设置.json》，年度还原为仅中文体例")
    if plan.missing_issues:
        warnings.append("缺期：" + ",".join(str(n) for n in plan.missing_issues))
    result = {
        "schema": JSON_SCHEMA,
        "ok": True,
        "mode": "annual",
        "dry_run": bool(args.dry_run),
        "outputs": [docx_path],
        "issue": _annual_issue_json(args, out_root, year),
        "counts": _counts(plan.data),
        "warnings": warnings,
        "missing": [],
        "annual": {
            "year": year,
            "included": [n for n, _d in plan.included],
            "excluded": list(plan.excluded),
            "missing_issues": list(plan.missing_issues),
            "issues": [
                {"number": rec["number"], "with_en_toc": bool(rec["with_en_toc"]),
                 "status": rec["status"]}
                for rec in plan.issues
            ],
        },
    }
    if not args.dry_run:
        from slcn.pushcopy import render_docx as _render   # 延迟导入
        os.makedirs(os.path.dirname(docx_path), exist_ok=True)
        _render.render_annual_docx(plan.data, year, docx_path)
    _emit(result, as_json)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    parser = argparse.ArgumentParser(
        prog="dirpush.py",
        description="目录摘要推送：期目录 → 公众号推送文案 docx（只读输入；不产 PDF）")
    parser.add_argument("--issue", required=True, help="期目录（当期）；年度模式给该年任一期目录")
    parser.add_argument("--out", default=None, help="输出根（缺省 ~/Documents/sysu/编辑部/目录摘要推送）")
    parser.add_argument("--with-en-toc", action="store_true", help="当期模式：生成中英文对照目录")
    parser.add_argument("--annual", action="store_true", help="年度模式：逐期拼接目录")
    parser.add_argument("--year", type=int, default=None, help="年度模式：年份")
    parser.add_argument("--include-issues", default=None, help="年度模式：只纳入这些期号，逗号分隔")
    parser.add_argument("--json", action="store_true", help="stdout 只输出一个 JSON 对象")
    parser.add_argument("--dry-run", action="store_true", help="只采集+校验，不写任何文件")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if as_json and exc.code != 0:
            _emit(_empty_result("issue", "--dry-run" in argv, "参数错误（见 stderr）"), True)
        raise

    mode = "annual" if args.annual else "issue"
    out_root = _out_root(args)
    try:
        if args.annual:
            if args.with_en_toc:
                raise _collect.CollectError("参数冲突：--with-en-toc 仅用于当期模式，年度体例按期还原")
            return _run_annual(args, out_root, args.json)
        if args.year is not None or args.include_issues:
            raise _collect.CollectError("参数冲突：--year/--include-issues 仅用于年度模式（--annual）")
        return _run_issue(args, out_root, args.json)
    except _collect.CollectError as exc:
        if args.json:
            _emit(_empty_result(mode, args.dry_run, str(exc)), True)
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001  内部异常：traceback 走 stderr，退出码 1
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    _plat.utf8_stdio()
    sys.exit(main())
