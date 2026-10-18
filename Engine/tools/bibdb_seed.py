#!/usr/bin/env python3.14
r"""把稿件仓「往期成品 bib」灌进笔记仓引用库（**只读扫描，可重复运行幂等**）。

要解决的问题
------------
编辑部要把**已出版期的成品 bib**（人工核对过）沉淀成自己的引用库，以后核实文献先查库。
手工一份份导入既慢又漏，所以用这个工具把稿件仓全部期目录扫一遍：

  · **只读**：稿件仓（`Studies_in_Logic_Database`）里一个字节都不写；
  · **成品 bib 判定**：取各篇稿子终稿 `.tex` 实际引用的那份 `.bib`
    （`\addbibresource{…}` / `\bibliography{…}`），不导草稿/副本（如 `xxx - 副本.bib`）；
  · **成品期判定**：期目录里有 `000 …`（中英文目录）→ 该期已定稿 → bib 入 `verified=true`；
    否则只入未核实；
  · **幂等**：库内容没变化时**不重写文件**（git 零 diff）；重复运行不重复追加来源；
  · **不覆盖已核实条目**：同一 key 但字段不同 → 记进 `conflicts` 留人工裁决，
    原字段一个不动（这是「AI/新导入不得静默改学术事实」的落点）。

输出（人看）
------------
逐期：`成品期/未定稿 + 份数 + 条目（新入库/合并或已存在/更新/冲突/跳过及原因）`；
汇总：期数、文件数、条目、新入库、合并、冲突、跳过原因；
库概况：记录数（已核实/未核实）、大小、sha256。

用法
----
    cd Engine
    export PYTHONPATH="$PWD:$PWD/vendor"
    /opt/homebrew/bin/python3.14 tools/bibdb_seed.py                # 默认：自动找稿件仓 + 笔记仓
    /opt/homebrew/bin/python3.14 tools/bibdb_seed.py --dry-run      # 只算不写
    /opt/homebrew/bin/python3.14 tools/bibdb_seed.py --through-year 2025
    /opt/homebrew/bin/python3.14 tools/bibdb_seed.py --json > /tmp/seed.json

退出码
------
`0` = 成功（有冲突也是 0，冲突是给人看的报告，不是失败）；`2` = 用法 / IO 错误。
**本机没有稿件仓**时按项目惯例优雅跳过（退出码 0，一行说明）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)

from slcn import bibdb, paths  # noqa: E402


def _skipped_total(report: dict) -> int:
    return sum((report.get("skipped") or {}).values())


def _reasons(report: dict) -> str:
    skipped = report.get("skipped") or {}
    if not skipped:
        return ""
    return "、".join(f"{name}×{count}" for name, count in sorted(skipped.items()))


def _issue_line(issue: dict) -> str:
    report = issue["report"]
    flag = "成品期" if issue["finished"] else "未定稿"
    line = (f"[{flag}] {issue['issue']}：{len(issue['files'])} 份 bib，"
            f"条目 {report['entries']}（新入库 {report['new']}"
            f" / 合并或已存在 {report['merged']} / 更新 {report['updated']}"
            f" / 冲突 {report['conflicts']} / 跳过 {_skipped_total(report)}）")
    if report.get("disambiguated"):
        line += f"，key 撞车加后缀 {report['disambiguated']} 条"
    reasons = _reasons(report)
    if reasons:
        line += f"；跳过原因：{reasons}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bibdb_seed.py",
        description="只读扫描稿件仓往期成品 bib，汇总进笔记仓引用库（幂等，可重复运行）。",
        epilog="默认库：<笔记仓>/bib_db/bibliography.jsonl（SLCN_NOTES 解析笔记仓）。",
    )
    parser.add_argument("--root", default="", metavar="PATH",
                        help="稿件库根（默认自动探测）")
    parser.add_argument("--db", default="", metavar="PATH",
                        help="引用库文件（默认 笔记仓/bib_db/bibliography.jsonl）")
    parser.add_argument("--through-year", type=int, default=0, metavar="YYYY",
                        help="只导入 ≤ 该年份的期（0 = 不限；往期口径可写 --through-year 2025）")
    parser.add_argument("--finished-only", action="store_true",
                        help="只导入有成品期证据（`000 …` 目录）的期")
    parser.add_argument("--force", action="store_true",
                        help="允许覆盖已核实条目（旧字段全量进 conflicts；人工裁决用）")
    parser.add_argument("--dry-run", action="store_true", help="只算统计，不写库")
    parser.add_argument("--json", action="store_true", help="机器可读输出（不打印人读报告）")
    args = parser.parse_args(argv)

    root = args.root or paths.manuscripts_root()
    if not root:
        print(f"≡ bibdb_seed：{paths.describe()}")
        return 0
    try:
        db_path = args.db or bibdb.default_db_path()
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    try:
        store = bibdb.Store.load(db_path, create=True)
        result = bibdb.import_manuscripts(
            store, root, force=args.force,
            through_year=args.through_year or None,
            finished_only=args.finished_only,
        )
        written = False if args.dry_run else store.save()
        stats = store.stats()
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    total = result["total"]
    finished_issues = sum(1 for issue in result["issues"] if issue["finished"])
    payload = {
        "manuscripts_root": os.path.abspath(root),
        "db": db_path,
        "dry_run": args.dry_run,
        "written": written,
        "issues": len(result["issues"]),
        "finished_issues": finished_issues,
        "files": sum(len(issue["files"]) for issue in result["issues"]),
        "result": result,
        "stats": stats,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(f"稿件库：{os.path.abspath(root)}")
    print(f"引用库：{db_path}")
    print("─" * 78)
    for issue in result["issues"]:
        print(_issue_line(issue))
        for skipped in issue["skipped"]:
            print(f"    · 跳过作者目录 {skipped['slug']}：{skipped['reason']}")
    print("─" * 78)
    print(f"扫描：{len(result['issues'])} 期（成品期 {finished_issues} / "
          f"未定稿 {len(result['issues']) - finished_issues}），"
          f"{payload['files']} 份 bib")
    print(f"合计：条目 {total['entries']}（新入库 {total['new']} / "
          f"合并或已存在 {total['merged']} / 更新 {total['updated']} / "
          f"冲突 {total['conflicts']} / 跳过 {_skipped_total(total)}）")
    if total.get("disambiguated"):
        print(f"      key 撞车加后缀 {total['disambiguated']} 条"
              "（不同作品用了同一个 BibTeX key，原 key 已进 aliases）")
    reasons = _reasons(total)
    if reasons:
        print(f"      跳过原因：{reasons}")
    if total.get("findings"):
        print(f"      bibparse 语法发现 {total['findings']} 处（未阻断导入）")
    for warning in result["warnings"]:
        print(f"⚠️ {warning}")
    if total["conflicts"]:
        print(f"⚠️ 有 {total['conflicts']} 条已核实条目出现字段差异：**保留原字段**并记进 "
              "conflicts（人工裁决入口：`bibdb.py query --key …` 看详情）")
    print("─" * 78)
    print(f"库：{stats['records']} 条（已核实 {stats['verified']} / 未核实 {stats['unverified']}）"
          f"，{stats['files']} 份 bib / {stats['issues']} 期")
    print(f"别名 {stats['aliases']['total']} 个；冲突 {stats['conflicts']['records']} 条记录"
          f"（{stats['conflicts']['total']} 处差异）；内容摘要重复 {stats['duplicate_content']}")
    if args.dry_run:
        print("（--dry-run：未写盘）")
    elif written:
        print(f"已写回：{stats['path']}（{stats['bytes']:,} 字节，sha256: {stats['sha256']}）")
    else:
        print(f"库无变化（幂等，未重写）：{stats['path']}（sha256: {stats['sha256']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
