#!/usr/bin/env python3.14
r"""编辑部引用库（`bib_db`）开发用 CLI —— **不进发布包**。

为什么是开发用
--------------
引用库的读写在两个场景：
  · **编辑器/开发者离线维护**（本 CLI）：种子导入、抽查、体检；
  · **将来**由初排转换（`convert.py`）在核实参考文献前查库 → 那一步的接口在
    `slcn/bibdb.py`，本文件**不参与发布**（不改 `engine_main.ENTRIES` /
    `sources-win/engine.py` / `build.sh` 白名单；没有界面入口）。
所以它只做「人看/脚本跑」的薄壳：所有逻辑在 `slcn/bibdb.py`（与将来接入点共用一份）。

默认库位置
----------
`<私有笔记仓>/bib_db/bibliography.jsonl`（`slcn.paths.notes_root()` 解析）。
笔记仓找不到时**明确报错**（退出码 2），绝不悄悄写到别处；`--db` 可显式指定
（测试、试用库用）。

子命令
------
    init                                    建库（目录 + 空 JSONL；幂等）
    import-bib  --bib PATH [--issue … --slug …] [--verified|--unverified] [--force]
                                            导入一份 .bib；给了期/篇默认按「往期成品 bib」
                                            入 verified（AI 结果不要走这个口子）
    import-dir  [--root PATH] [--through-year YYYY] [--finished-only] [--force]
                                            只读扫描稿件仓各期成品 bib 并入库
    query       (--key K | --doi D | --title T [--author A] [--year Y])
                [--limit N] [--threshold X] [--min-score X] [--json]
    stats       [--json]
    lint        [--json]                    重复 key / 字段冲突 / 缺来源 / verified 无记录 …

用法示例（在本目录下跑）
------------------------
    export PYTHONPATH="$PWD:$PWD/vendor"
    /opt/homebrew/bin/python3.14 bibdb.py init
    /opt/homebrew/bin/python3.14 bibdb.py import-bib \
        --bib "../Studies_in_Logic_Database/2025年第6期(85)/01 Xiaoyang_Wang/name.bib" \
        --issue "2025年第6期(85)" --slug "01 Xiaoyang_Wang"
    /opt/homebrew/bin/python3.14 bibdb.py query --doi 10.1007/s10992-020-09558-5
    /opt/homebrew/bin/python3.14 bibdb.py query --title "A Theory of Everything" --author Smith
    /opt/homebrew/bin/python3.14 bibdb.py stats
    /opt/homebrew/bin/python3.14 bibdb.py lint

退出码
------
`0` = 成功（查询 0 命中、lint 只有 warning 也算成功）；`1` = `lint` 有 error；
`2` = 用法 / IO / 库结构错误（消息在 stderr）。库文件不存在时 `query/stats/lint`
退出 2（先 init/import）；`import-bib` / `import-dir` 会自动建库。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from slcn import bibdb, paths  # noqa: E402


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _clip(text: object, width: int = 60) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= width else value[: width - 1] + "…"


def _field(fields: dict, *names: str) -> str:
    for name in names:
        value = fields.get(name)
        if value:
            return str(value)
    return ""


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _db_path(args) -> str:
    return args.db or bibdb.default_db_path()


def _open_store(args, *, create: bool = False, lenient: bool = False):
    path = _db_path(args)
    store = bibdb.Store.load(path, create=create, lenient=lenient)
    return path, store


def _source_note(source: dict) -> str:
    where = "/".join(part for part in (source.get("issue"), source.get("slug"),
                                       source.get("file")) if part)
    return f"{source.get('kind')}{(' ' + where) if where else ''}"


def _print_stats(stats: dict) -> None:
    print(f"库文件：{stats['path']}")
    print(f"大小：{stats['bytes']:,} 字节（sha256: {stats['sha256'] or '不存在'}）")
    print(f"条目：{stats['records']}（已核实 {stats['verified']} / 未核实 {stats['unverified']}）")
    methods = "、".join(f"{name} {count}" for name, count in stats["methods"].items())
    if methods:
        print(f"核实方式：{methods}")
    types = "、".join(f"{name} {count}" for name, count in stats["entry_types"].items())
    if types:
        print(f"类型：{types}")
    print(f"来源：{stats['sources']} 条（{stats['files']} 份 bib / {stats['issues']} 期）")
    if stats["issue_list"]:
        print(f"期范围：{stats['issue_list'][0]} … {stats['issue_list'][-1]}"
              f"（{stats['issues']} 期）")
    print(f"别名：{stats['aliases']['records']} 条记录（共 {stats['aliases']['total']} 个别名）")
    print(f"冲突：{stats['conflicts']['records']} 条记录（共 {stats['conflicts']['total']} 处差异）")
    print(f"内容摘要重复：{stats['duplicate_content']}（正常应为 0）")
    if stats["created_range"]:
        print(f"入库时间：{stats['created_range'][0]} … {stats['created_range'][1]}")
    if stats["dirty"]:
        print("⚠️ 内存里有未落盘的改动")


def _print_import_report(report: dict, *, indent: str = "") -> None:
    print(f"{indent}{_report_line(report)}")
    if report.get("findings"):
        print(f"{indent}（bibparse 语法发现 {report['findings']} 处，未阻断导入）")
    for warning in report.get("warnings") or []:
        print(f"{indent}⚠️ {warning}")


def _report_line(report: dict) -> str:
    skipped = report.get("skipped") or {}
    skipped_total = sum(skipped.values())
    text = (f"条目 {report['entries']}（新入库 {report['new']} / "
            f"合并或已存在 {report['merged']} / 更新 {report['updated']} / "
            f"冲突 {report['conflicts']} / 跳过 {skipped_total}）")
    if report.get("disambiguated"):
        text += f"，其中 key 撞车加后缀 {report['disambiguated']}"
    if skipped:
        reasons = "、".join(f"{name}×{count}" for name, count in sorted(skipped.items()))
        text += f"；跳过原因：{reasons}"
    return text


# ── 子命令 ──────────────────────────────────────────────────────────────────


def cmd_init(args) -> int:
    path = _db_path(args)
    db_path, created = bibdb.init_db(path)
    print(f"引用库：{db_path}")
    print("已新建（空库）。" if created else "已存在，未改动。")
    return 0


def cmd_import_bib(args) -> int:
    bib_path = os.path.abspath(args.bib)
    if not os.path.isfile(bib_path):
        print(f"✗ 找不到要导入的 bib：{bib_path}", file=sys.stderr)
        return 2
    verified = bool(args.verified or (args.issue and args.slug))
    if args.unverified:
        verified = False
    if verified and (args.issue or args.slug):
        kind = bibdb.KIND_PUBLISHED
    elif verified:
        kind = bibdb.KIND_VERIFIED
    else:
        kind = bibdb.KIND_IMPORTED
    source = {
        "kind": kind,
        "issue": args.issue or None,
        "slug": args.slug or None,
        "file": os.path.basename(bib_path),
    }
    try:
        text, text_warning = bibdb.read_bib_text(bib_path)
        if text_warning:
            print(f"⚠️ {text_warning}", file=sys.stderr)
        path, store = _open_store(args, create=True)
        report = store.import_bib_text(text, source, verified=verified, force=args.force)
        written = store.save()
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    payload = {"db": path, "bib": bib_path, "source": source,
               "verified": verified, "written": written, "report": report.summary()}
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"来源：{_source_note(source)}（{'已核实' if verified else '未核实'}）")
    _print_import_report(report.summary())
    print(f"已写回（sha256: {bibdb.db_sha256(path)}）" if written else "库无变化（幂等，未重写）。")
    return 0


def cmd_import_dir(args) -> int:
    root = args.root or paths.manuscripts_root()
    if not root:
        print(f"≡ bibdb import-dir：{paths.describe()}")
        return 0
    try:
        path, store = _open_store(args, create=True)
        result = bibdb.import_manuscripts(store, root, force=args.force,
                                          through_year=args.through_year,
                                          finished_only=args.finished_only)
        written = store.save()
        stats = store.stats()
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    payload = {"db": path, "written": written, "result": result, "stats": stats}
    if args.json:
        _print_json(payload)
        return 0
    print(f"稿件库：{os.path.abspath(root)}")
    print(f"库：{path}")
    unfinished = 0
    for issue in result["issues"]:
        flag = "成品期" if issue["finished"] else "未定稿"
        unfinished += 0 if issue["finished"] else 1
        print(f"[{flag}] {issue['issue']}：{len(issue['files'])} 份 bib —— "
              + _report_line(issue["report"]))
        for skipped in issue["skipped"]:
            print(f"    · 跳过 {skipped['slug']}：{skipped['reason']}")
    print(f"共 {len(result['issues'])} 期（未定稿 {unfinished} 期；未定稿期的 bib 只入未核实）")
    total = result["total"]
    print("合计：" + _report_line(total))
    for warning in result["warnings"]:
        print(f"⚠️ {warning}")
    if total["conflicts"]:
        print(f"⚠️ 有 {total['conflicts']} 条已核实条目出现字段差异（保留原字段并记进 conflicts，"
              "用 `lint` 或 `query --key` 查看）")
    print(f"库：{stats['records']} 条（已核实 {stats['verified']} / 未核实 {stats['unverified']}）")
    print(f"已写回（{stats['bytes']:,} 字节，sha256: {stats['sha256']}）"
          if written else "库无变化（幂等，未重写）。")
    return 0


def cmd_query(args) -> int:
    try:
        path, store = _open_store(args)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if args.key is not None:
        result = store.query_key(args.key)
        kind = "key"
    elif args.doi is not None:
        result = store.query_doi(args.doi)
        kind = "doi"
    else:
        result = store.query_title(args.title, author=args.author or "",
                                   year=args.year or "", limit=args.limit,
                                   threshold=args.threshold,
                                   min_score=args.min_score)
        kind = "title"
    payload = {"db": path, "kind": kind, **result}
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    hits = result["hits"]
    if not hits:
        query = result.get("query") or {}
        subject = query.get("title") or query.get("doi") or query.get("key") or ""
        print(f"{kind} 查询无命中：{_clip(subject)}")
        if result.get("error"):
            print(f"（{result['error']}）")
        return 0
    high = result.get("high")
    print(f"{kind} 查询命中 {len(hits)} 条"
          + (f"（高置信 {high}）" if high is not None else "")
          + ("（⚠️ 多条同 key/alias，需人工确认）" if result.get("ambiguous") else ""))
    for index, hit in enumerate(hits, start=1):
        record = hit["record"]
        fields = record.get("fields") or {}
        marks = []
        marks.append("已核实" if record.get("verified") is True else "未核实")
        if "hit" in hit and hit["hit"] != "key":
            marks.append(f"{hit['hit']} 命中")
        if hit.get("confidence"):
            marks.append(("高置信" if hit["confidence"] == "high" else "低置信"))
        print(f"  {index}. [{'／'.join(marks)}] {hit['key']}")
        if kind == "title":
            print(f"     score={hit['score']:.3f}（题名 {hit['title_similarity']:.3f}"
                  + (f"／作者 {'一致' if hit['author_match'] else '不一致'}"
                     if hit["author_match"] is not None else "")
                  + (f"／年份 {'一致' if hit['year_match'] else '不一致'}"
                     if hit["year_match"] is not None else "") + "）")
        title = _field(fields, "title")
        author = _field(fields, "author", "editor")
        year = bibdb.entry_year(fields)
        if title:
            print(f"     题名：{_clip(title, 90)}")
        if author or year:
            print(f"     作者：{_clip(author, 70)}" + (f"　年份：{year}" if year else ""))
        doi = _field(fields, "doi")
        if doi:
            print(f"     DOI：{doi}")
        for source in record.get("sources") or []:
            print(f"     来源：{_source_note(source)}")
    return 0


def cmd_stats(args) -> int:
    try:
        path, store = _open_store(args)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    stats = store.stats()
    if args.json:
        _print_json(stats)
        return 0
    _print_stats(stats)
    return 0


def cmd_lint(args) -> int:
    try:
        path, store = _open_store(args, lenient=True)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    result = store.lint()
    payload = {"db": path, **result}
    if args.json:
        _print_json(payload)
        return 0 if result["ok"] else 1
    print(f"库：{path}")
    for error in result["errors"]:
        print(f"✗ {error}")
    for warning in result["warnings"]:
        print(f"! {warning}")
    counts = result["counts"]
    print(f"条目 {counts['records']}（已核实 {counts['verified']} / "
          f"未核实 {counts['unverified']}）；冲突 {counts['conflicts']['records']} 条；"
          f"同一作品多条记录 {counts['near_duplicates']} 对")
    print(f"error {counts['errors']} 项，warning {counts['warnings']} 项"
          + ("——库可用。" if result["ok"] else "——**库有 error，先修**。"))
    return 0 if result["ok"] else 1


# ── 入口 ────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bibdb.py",
        description="编辑部引用库（bib_db）开发用 CLI：初始化 / 导入 / 查询 / 统计 / 体检。",
        epilog="默认库：<笔记仓>/bib_db/bibliography.jsonl（笔记仓由 SLCN_NOTES 解析）。",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default="", metavar="PATH",
                        help="库文件路径（默认 笔记仓/bib_db/bibliography.jsonl）")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<子命令>")

    sub.add_parser("init", parents=[common],
                   help="建库（目录 + 空 JSONL；重复 init 幂等）").set_defaults(func=cmd_init)

    imp = sub.add_parser("import-bib", parents=[common],
                         help="导入一份 .bib（--issue/--slug 默认按往期成品 bib 入 verified）")
    imp.add_argument("--bib", required=True, help="要导入的 .bib 路径")
    imp.add_argument("--issue", default="", help="期目录名，例 `2025年第6期(85)`")
    imp.add_argument("--slug", default="", help="作者目录名，例 `01 Xiaoyang_Wang`")
    imp.add_argument("--verified", action="store_true",
                     help="显式标记已核实（没有期/篇时用；AI 结果不要用）")
    imp.add_argument("--unverified", action="store_true",
                     help="强制按未核实入库（即使给了期/篇）")
    imp.add_argument("--force", action="store_true",
                     help="允许覆盖已核实条目（旧字段全量进 conflicts 留档）")
    imp.add_argument("--json", action="store_true", help="机器可读输出")
    imp.set_defaults(func=cmd_import_bib)

    ind = sub.add_parser("import-dir", parents=[common],
                         help="只读扫描稿件仓各期成品 bib 并入库")
    ind.add_argument("--root", default="", metavar="PATH",
                     help="稿件库根（默认自动探测；找不到时跳过并退出 0）")
    ind.add_argument("--through-year", type=int, default=0, metavar="YYYY",
                     help="只导入 ≤ 该年份的期（0 = 不限）")
    ind.add_argument("--finished-only", action="store_true",
                     help="只导入有成品期证据（`000 …` 目录）的期")
    ind.add_argument("--force", action="store_true",
                     help="允许覆盖已核实条目（旧字段全量进 conflicts 留档）")
    ind.add_argument("--json", action="store_true", help="机器可读输出")
    ind.set_defaults(func=cmd_import_dir)

    qry = sub.add_parser("query", parents=[common], help="查询：key 精确 / DOI 精确 / 题名模糊")
    group = qry.add_mutually_exclusive_group(required=True)
    group.add_argument("--key", help="BibTeX key（也认 aliases）")
    group.add_argument("--doi", help="DOI（大小写/前缀不敏感）")
    group.add_argument("--title", help="题名（模糊匹配，返回 score/confidence）")
    qry.add_argument("--author", default="", help="第一作者姓（配合 --title）")
    qry.add_argument("--year", default="", help="年份（配合 --title）")
    qry.add_argument("--limit", type=int, default=10, help="最多返回几条（默认 10）")
    qry.add_argument("--threshold", type=float, default=bibdb.FUZZY_TITLE_THRESHOLD,
                     help=f"高置信题名阈值（默认 {bibdb.FUZZY_TITLE_THRESHOLD}）")
    qry.add_argument("--min-score", type=float, default=bibdb.FUZZY_MIN_SCORE,
                     help=f"综合分低于此值不返回（默认 {bibdb.FUZZY_MIN_SCORE}）")
    qry.add_argument("--json", action="store_true", help="机器可读输出")
    qry.set_defaults(func=cmd_query)

    stats = sub.add_parser("stats", parents=[common], help="库概况")
    stats.add_argument("--json", action="store_true", help="机器可读输出")
    stats.set_defaults(func=cmd_stats)

    lint = sub.add_parser("lint", parents=[common],
                          help="库体检（重复 key / 字段冲突 / 缺来源 / verified 无记录 …）")
    lint.add_argument("--json", action="store_true", help="机器可读输出")
    lint.set_defaults(func=cmd_lint)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except bibdb.BibDBError as exc:            # 兜底：任何子命令的库错误都按用法错误收场
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"✗ IO 错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
