#!/usr/bin/env python3.14
r"""编辑部引用库（`bib_db`）的 CLI 薄壳 —— **两个平台都随包发布**。

谁会调它
--------
引用库的读写在三个场景：
  · **编辑器/开发者离线维护**：种子导入、抽查、体检；
  · **初排转换**（`convert.py`）在核实参考文献前查库 → 那一步的接口在
    `slcn/bibdb.py`；
  · **两个平台 GUI 的「引用库」5 页**（库概况/查库/入藏/待裁决/设置，第 132 条）——
    mac 包由 `build.sh` 打包、Windows 包由分发器 `engine_main.ENTRIES` 与
    `sources-win/engine.py: ENTRIES` 收录（四处入口表必须同时改，见
    `Engine/tests/test_platform.py` 与 `sources-win/tests/test_bridge.py` 的守卫）。
所以它只做「人看/脚本跑/界面管道读 JSON」的薄壳：所有逻辑在 `slcn/bibdb.py`。

默认库位置
----------
`<私有笔记仓>/bib_db/bibliography.jsonl`（`slcn.paths.notes_root()` 解析）。
笔记仓找不到时**明确报错**（退出码 2），绝不悄悄写到别处；`--db` 可显式指定
（测试、试用库用）。

子命令
------
    init                                    建库（目录 + 空 JSONL；幂等）
    import-bib  --bib PATH [--issue … --slug …] [--verified|--unverified] [--force]
                [--dry-run]              导入一份 .bib；给了期/篇默认按「往期成品 bib」
                                           入 verified（AI 结果不要走这个口子）
    import-dir  [--root PATH] [--through-year YYYY] [--finished-only] [--force]
                [--dry-run]              只读扫描稿件仓各期成品 bib 并入库；
                                           `--root` 也可给**一期目录**（只导入这一期）
    query       (--key K | --doi D | --title T [--author A] [--year Y])
                [--limit N] [--threshold X] [--min-score X] [--bibtex] [--json]
    stats       [--json]
    lint        [--json]                    重复 key / 字段冲突 / 缺来源 / verified 无记录 …
    resolve     --key K --action keep|defer|adopt
                (--all-fields | --fields F1,F2 | --incoming-sha256 SHA)
                [--allow-overlap] [--note TEXT] [--dry-run] [--json]
                                            记录冲突裁决：keep=保留旧值 / defer=待人工 /
                                            adopt=**逐字段采用新稿值**（改 fields、重算 sha，
                                            必须用指纹；重叠冲突默认拒绝或用 --allow-overlap
                                            标 superseded）；整条替换仍走 import-bib --force
    report      [--out DIR]                 一次生成全部待裁决清单（推荐）
    report-conflicts  [--out DIR]           字段冲突待裁决清单 CONFLICTS.md（中文、可勾选）
    report-duplicates [--out DIR]           同作品不同 key 候选对 DUPLICATES.md + duplicates.csv
    normalize   [--report PATH | --out-dir DIR] [--json-out PATH] [--json]
                                            B1 确定性规范化 dry-run（只出报告，**绝不写库**）

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
    /opt/homebrew/bin/python3.14 bibdb.py resolve --key <库内 key> --action keep --all-fields
    /opt/homebrew/bin/python3.14 bibdb.py resolve --key <库内 key> --action adopt \
        --incoming-sha256 <指纹> [--allow-overlap] [--dry-run]
    /opt/homebrew/bin/python3.14 bibdb.py report --out ../EditToolBox-notes/bib_db
    /opt/homebrew/bin/python3.14 bibdb.py normalize --out-dir ../EditToolBox-notes/bib_db
    # ↑ B1 dry-run 报告（B1-normalize-dryrun.md/.json）；只读库、不写 bibliography.jsonl
    # ↑ 生成 CONFLICTS.md / DUPLICATES.md / duplicates.csv（同一份库重复生成逐字节相同）

退出码
------
`0` = 成功（查询 0 命中、lint 只有 warning 也算成功）；`1` = `lint` 有 error；
`2` = 用法 / IO / 库结构错误（消息在 stderr）。库文件不存在时 `query/stats/lint` /
`report*` 退出 2（先 init/import）；`import-bib` / `import-dir` 会自动建库。
`report*` 只写清单文件（可重复生成、内容幂等），不改库本体；`resolve` 才写库
（只改 `conflicts[]` 的裁决元数据，**不改字段值**；`--dry-run` 不写）。
`normalize` 是**纯 dry-run**：只写报告文件，绝不写 `bibliography.jsonl`。
`--dry-run`（预览）：照常解析/查重/算冲突，但**一个字节都不写**（库文件不存在也不会
新建），`--json` 里带 `dry_run=true` 与逐条 `changes` 明细；界面「入藏」页的
「预览」按钮用的就是它。`--bibtex`（query）：每条命中多带一份可直接复制的 BibTeX。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from slcn import bibdb, bibnorm, bibparse, fsutil, paths  # noqa: E402
from slcn.platform import utf8_stdio  # noqa: E402


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


# ── 预览（--dry-run）与 BibTeX 渲染（界面「引用库」页用；第 132 条）──────────
#
# 为什么预览/明细放在 CLI 层：GUI 的「入藏」页要在**写库之前**把「将新增/合并/冲突/跳过」
# 给编辑看。判据全在 `slcn/bibdb.py`，这里只做两件事：
#   ① 导入前后各拍一张快照，比对出**逐条变化**（新增/合并/更新/冲突）；
#   ② 把「命中记录」渲染成可直接粘进 `.bib` 的一段文本。
# 库本体一个字节都不写（dry-run 连 `store.save()` 都不调）。

#: BibTeX 渲染时的字段顺序（常见字段在前，其余按名字排；只为「人读/可diff」，不影响语义）
_BIBTEX_FIELD_ORDER = (
    "author", "editor", "translator", "title", "subtitle", "booktitle",
    "journal", "journaltitle", "volume", "number", "issue", "pages",
    "year", "date", "publisher", "address", "location", "edition",
    "series", "school", "institution", "organization", "type", "chapter",
    "doi", "url", "urldate", "isbn", "issn", "language", "note", "userf",
)


def render_bibtex(record: dict) -> str:
    """一条库记录 → 一段 BibTeX（GUI「复制 BibTeX」按钮的唯一实现）。

    为什么不用 `slcn/footnotes.py: entry_to_bib`：那个渲染器是**生成产物 bib** 用的，
    字段顺序按条目类型走**白名单**，会把库记录里的 `doi/url/isbn/note` 等字段丢掉；
    这里要的是「把库里那一行记录原样抄成 BibTeX」，所有字段都得在。
    """
    key = str(record.get("key") or "").strip() or "unknown"
    etype = str(record.get("entry_type") or "misc").strip().lower() or "misc"
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    order = list(_BIBTEX_FIELD_ORDER) + sorted(
        name for name in fields if name not in _BIBTEX_FIELD_ORDER)
    lines = [f"@{etype}{{{key},"]
    for name in order:
        value = fields.get(name)
        if value is None or str(value) == "":
            continue
        # 值**原样**放进花括号：库里的字段是规范化后的内容（可能自带 `{A}n` 这类
        # 保护花括号），做逐字符 LaTeX 转义反而会改坏它。花括号不平衡的条目本身
        # 已是坏数据（`lint` 会报），这里也不替它猜一个安全写法。
        lines.append(f"  {name} = {{{value}}},")
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("}")
    return "\n".join(lines) + "\n"


def _store_snapshot(store) -> dict:
    """导入前的只读快照：`key → 记录`（记录本身不复制，只用于比对引用）。"""
    return {str(record.get("key") or ""): record for record in store.records()}


def _change_rows(before: dict, store) -> list[dict]:
    """导入前后对比 → 逐条变化明细（给 `--dry-run --json` 与界面用）。

    状态口径（与 `ImportReport` 的计数一一对得上，但这里是**逐条**的）：
      new=新入库；conflict=记录了字段冲突；updated=内容/核实状态变了；merged=只并了来源。
    只是**展示层**：真实状态以 `slcn/bibdb.py` 的 upsert 结果为准，这里由前后快照反推。
    """
    rows: list[dict] = []
    after = {str(record.get("key") or ""): record for record in store.records()}
    for key in sorted(after):
        record = after[key]
        old = before.get(key)
        old_conflicts = (old or {}).get("conflicts") or []
        new_conflicts = record.get("conflicts") or []
        conflicts_added = max(0, len(new_conflicts) - len(old_conflicts))
        added = new_conflicts[len(old_conflicts):] if conflicts_added else []
        conflict_fields = sorted({
            str(name) for conflict in added if isinstance(conflict, dict)
            for name in (conflict.get("field_diff") or {})
        })
        if old is None:
            status = "new"
        elif conflicts_added:
            status = "conflict"
        elif record.get("content_sha256") != old.get("content_sha256") \
                or (record.get("verified") is True and old.get("verified") is not True):
            status = "updated"
        elif len(record.get("sources") or []) > len(old.get("sources") or []):
            status = "merged"
        else:
            continue                      # 完全没变的条目不进明细（7949 条库里绝大多数是这种）
        title, author, year = _record_brief(record)
        rows.append({
            "key": key,
            "status": status,
            "verified": record.get("verified") is True,
            "title": title,
            "author": author,
            "year": year,
            "sources_added": max(0, len(record.get("sources") or [])
                                 - len((old or {}).get("sources") or [])),
            "conflicts_added": conflicts_added,
            "conflict_fields": conflict_fields,
        })
    return rows


def _print_changes(changes: list[dict], *, limit: int = 40) -> None:
    """把逐条变化打到 stdout（人读；超长只列前 `limit` 条）。"""
    if not changes:
        print("   逐条变化：（无 —— 库与输入完全一致）")
        return
    label = {"new": "新增", "merged": "合并来源", "updated": "更新", "conflict": "记冲突"}
    print(f"   逐条变化（{len(changes)} 条" + (f"，只列前 {limit}" if len(changes) > limit else "") + "）：")
    for row in changes[:limit]:
        tail = ""
        if row["status"] == "conflict" and row["conflict_fields"]:
            tail = "（冲突字段：" + "、".join(row["conflict_fields"]) + "）"
        elif row["status"] == "merged" and row["sources_added"]:
            tail = f"（+{row['sources_added']} 份来源）"
        print(f"     · {label.get(row['status'], row['status'])} `{row['key']}`："
              f"{_clip(row['title'], 70)}"
              + (f"（{_clip(row['author'], 40)}{(' ' + row['year']) if row['year'] else ''}）"
                 if row["author"] or row["year"] else "")
              + tail)


def _issue_dir_if_any(root: str) -> str:
    """选中的目录是「一期」而不是「稿件库根」时返回它的绝对路径，否则空串。"""
    path = os.path.abspath(root)
    if os.path.isdir(path) and paths.ISSUE_RE.match(os.path.basename(path)):
        return path
    return ""


def _import_one_issue(store, issue_path: str, *, force: bool,
                      through_year: int = 0, finished_only: bool = False) -> dict:
    """只导入**一期**目录（界面「入藏」页选整期根目录时用）。

    为什么不直接调 `slcn.bibdb.import_manuscripts()`：它按「稿件库根」扫**全部**期。
    本批约束是核心只读，所以这里把 `import_manuscripts()` 的**编排**照搬一份，但
    **判据一步都不重写**：选哪份 bib（`scan_manuscripts`）、怎么读（`read_bib_text`）、
    成品期/未定稿怎么定 verified（`IssueScan.finished`）、怎么入库（`store.import_bib_text`）
    全部调核心的公开函数。返回结构与 `import_manuscripts()` 完全相同（只含这一期）。
    """
    issue = os.path.abspath(issue_path)
    parent = os.path.dirname(issue)
    scans = [scan for scan in bibdb.scan_manuscripts(parent)
             if os.path.abspath(scan.path) == issue]
    if not scans:
        raise bibdb.BibDBError(
            f"这不是一个可识别的期目录：{issue}\n"
            "  判据与 import-dir 的稿件库扫描同源：目录名要形如 `2026年第2期(87)`，"
            "并且它的上级目录里放着各期目录。")
    scan = scans[0]
    # --through-year / --finished-only 在「稿件库根」模式由核心扫描负责过滤；单期模式
    # 核心不走 scan_manuscripts(root) 的过滤参数，这里补同一口径（否则参数会被静默忽略）。
    year_match = re.match(r"^(\d{4})年第", scan.issue)
    year = int(year_match.group(1)) if year_match else 0
    if (through_year and year > through_year) or (finished_only and not scan.finished):
        return {"root": parent, "issues": [], "total": bibdb.ImportReport().summary(),
                "warnings": [f"{scan.issue} 被 --through-year / --finished-only 过滤，未导入。"]}
    total = bibdb.ImportReport()
    warnings: list[str] = []
    file_rows: list[dict] = []
    for bib_file in scan.files:
        try:
            text, text_warning = bibdb.read_bib_text(bib_file.path)
        except bibdb.BibDBError as exc:
            total.skipped["文件读不了"] += 1
            warnings.append(f"{scan.issue}/{bib_file.rel}：{exc}")
            continue
        if text_warning:
            warnings.append(f"{scan.issue}/{bib_file.rel}：{text_warning}")
        if bib_file.warning:
            warnings.append(f"{scan.issue}/{bib_file.rel}：{bib_file.warning}")
        source = {
            "kind": bibdb.KIND_PUBLISHED if scan.finished else bibdb.KIND_IMPORTED,
            "issue": scan.issue,
            "slug": bib_file.slug,
            "file": bib_file.file,
        }
        report = store.import_bib_text(text, source, verified=scan.finished, force=force)
        total.merge(report)
        file_rows.append({"slug": bib_file.slug, "file": bib_file.file,
                          "how": bib_file.how, "report": report.summary()})
    row = {
        "issue": scan.issue,
        "path": scan.path,
        "finished": scan.finished,
        "evidence": scan.evidence,
        "files": file_rows,
        "skipped": scan.skipped,
        "report": total.summary(),
    }
    return {"root": parent, "issues": [row], "total": total.summary(),
            "warnings": warnings}


# ── 待裁决清单：report-conflicts / report-duplicates（第 129 条后续）──────────
#
# 为什么单独生成：库里的 `conflicts[]` 与「同作品不同 key」都是**机器留档**（JSONL），
# 编辑读不了。这两个报告把同样的事实翻成**中文、按期→篇→条目组织、逐条可勾选**的
# 裁决清单，让非程序员编辑也能一条条看完并记录决定。
#
# 硬要求：清单**可重复生成且逐字节幂等**——报告里不写生成时间（时间戳会让每次重新生成
# 都产生 diff），只写库内容 sha256 与现算统计；同一份库重复生成必须逐字节相同。

#: 常见 BibTeX 字段 → 中文（清单里给人看；没有映射的字段原样显示）
_FIELD_LABELS = {
    "title": "题名", "subtitle": "副标题", "shorttitle": "简称",
    "author": "作者", "editor": "编者", "translator": "译者",
    "year": "年份", "date": "日期", "month": "月份",
    "journal": "期刊", "journaltitle": "期刊", "booktitle": "书名/文集名",
    "publisher": "出版商", "address": "出版地", "location": "出版地",
    "volume": "卷号", "number": "期号", "issue": "期号", "pages": "页码",
    "edition": "版次", "series": "丛书", "type": "类型",
    "school": "学校", "institution": "机构", "organization": "组织",
    "howpublished": "出版方式", "note": "备注", "language": "语言",
    "doi": "DOI", "url": "链接", "urldate": "访问日期",
    "isbn": "ISBN", "issn": "ISSN", "userf": "标点体例",
    "chapter": "章节", "keywords": "关键词", "abstract": "摘要",
    "$entry_type": "条目类型",
}


def _issue_sort_key(name: object) -> tuple:
    """期目录名 → 可比较的排序键（按年份、期号数值；不认识的排最后）。

    为什么不用字符串排序：`2026年第10期` 字符串序会排在 `第2期` 前面，编辑按时间顺序
    翻清单时就乱了。
    """
    text = str(name or "")
    match = re.match(r"^(\d{4})年第(\d+)([A-Za-z]?)期", text)
    if match:
        return (int(match.group(1)), int(match.group(2)), match.group(3), text)
    return (9999, 0, "", text)


def _md_escape(value: object, *, width: int = 0) -> str:
    """markdown 表格单元格转义（管道符/换行）；`width>0` 时截断加省略号。"""
    text = " ".join(str(value if value is not None else "").split())
    if width and len(text) > width:
        text = text[: width - 1] + "…"
    return text.replace("|", "\\|")


def _md_code(value: object) -> str:
    """行内代码：值里的反引号会被换掉（留档可读优先，不追求逐字还原）。"""
    text = " ".join(str(value if value is not None else "").split()).replace("`", "'")
    return f"`{text}`"


def _field_label(name: str) -> str:
    label = _FIELD_LABELS.get(name)
    return f"{label}（`{name}`）" if label else _md_code(name)


def _source_text(source: dict, *, with_kind: bool = False) -> str:
    """一份来源 → `期 / slug / file`（没有期时给替代说明）。"""
    pieces = [str(source.get(key)) for key in ("issue", "slug", "file")
              if source.get(key)]
    text = " / ".join(pieces) if pieces else "（未记录期/篇/文件）"
    if with_kind and source.get("kind"):
        text += f"（{source['kind']}）"
    return text


def _record_sources(record: dict) -> list[dict]:
    return [s for s in (record.get("sources") or []) if isinstance(s, dict)]


def _record_issues(record: dict) -> list[str]:
    """记录出现过的期（去重、按时间序）；没有期的排最后。"""
    issues = {str(s.get("issue")) for s in _record_sources(record) if s.get("issue")}
    return sorted(issues, key=_issue_sort_key) if issues else []


def _record_brief(record: dict) -> tuple[str, str, str]:
    """(题名, 作者, 年份)：清单里每条记录的第一行摘要。"""
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    title = str(fields.get("title") or fields.get("booktitle") or "（无题名）")
    author = str(fields.get("author") or fields.get("editor") or "（无作者）")
    year = bibdb.entry_year(fields)
    return title, author, year


def collect_conflict_items(store: "bibdb.Store") -> list[dict]:
    """把库里所有 `conflicts[]` 摊平成清单条目（一条冲突 = 一个待裁决项）。

    每条带：期/篇/文件（冲突来源）、库内记录（key/题名/作者/年份）、逐字段
    旧值 vs 新稿值、库内已核实来源、建议默认、来源 key 等；排序键 = 期→篇→条目 key。
    另带 `pending`（是否待裁决，按 `conflicts[].resolved_at` 判断）与 `stable_no`
    （**按全部冲突含已裁决分配的固定编号 Cxxx**，第 130 条）。
    """
    items: list[dict] = []
    for record in store.records():
        conflicts = record.get("conflicts")
        if not isinstance(conflicts, list):
            continue
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        title, author, year = _record_brief(record)
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                continue
            # 第 130 条：`recorded_by="force"` 是 --force 的**审计留档**（旧值证据），
            # 不是待裁决项；清单/编号都排除它，保证 force 不会挤动其它条目的 C 编号。
            if str(conflict.get("recorded_by") or "") == "force":
                continue
            source = conflict.get("source") if isinstance(conflict.get("source"), dict) else {}
            diff = conflict.get("field_diff") if isinstance(conflict.get("field_diff"), dict) else {}
            old_empty = [name for name, values in diff.items()
                         if isinstance(values, dict)
                         and not values.get("existing") and values.get("incoming")]
            new_empty = [name for name, values in diff.items()
                         if isinstance(values, dict)
                         and values.get("existing") and not values.get("incoming")]
            both = [name for name, values in diff.items()
                    if isinstance(values, dict)
                    and values.get("existing") and values.get("incoming")]
            if diff and len(old_empty) == len(diff):
                advice = "可考虑采用新稿值：旧值为空、新稿补全了该字段"
            elif old_empty:
                filled = "、".join(_field_label(name) for name in old_empty)
                advice = f"默认保留旧值；新稿补全了 {filled}（采用新值可一并生效）"
            elif new_empty and not both:
                advice = "保留旧值（新稿这些字段为空，属信息缺失，不是更新）"
            elif both:
                advice = "保留旧值（默认）——两版都有值，通常是措辞/版次/来源写法差异"
            else:
                advice = "保留旧值（默认）"
            items.append({
                "record_key": str(record.get("key") or ""),
                "bib_key": str(source.get("key") or record.get("key") or ""),
                "entry_type": str(record.get("entry_type") or ""),
                "title": title, "author": author, "year": year,
                "issue": str(source.get("issue") or ""),
                "slug": str(source.get("slug") or ""),
                "file": str(source.get("file") or ""),
                "incoming_source": source,
                "source_key": str(source.get("key") or ""),
                "at": str(conflict.get("at") or ""),
                "resolution": str(conflict.get("resolution") or ""),
                "incoming_sha256": str(conflict.get("incoming_sha256") or ""),
                # 第 130 条：以 resolved_at 判断是否已裁决（导入时默认的 kept-existing 不算）
                "pending": bibdb.conflict_is_pending(conflict),
                "resolved_at": str(conflict.get("resolved_at") or ""),
                "resolution_note": str(conflict.get("note") or ""),
                "diff": {name: {"old": (values or {}).get("existing", ""),
                                "new": (values or {}).get("incoming", "")}
                         for name, values in diff.items()},
                "advice": advice,
                "verified": record.get("verified") is True,
                "method": str(record.get("method") or ""),
                "library_sources": [
                    s for s in _record_sources(record)
                    if (s.get("issue"), s.get("slug"), s.get("file"))
                    != (source.get("issue"), source.get("slug"), source.get("file"))
                ],
                "record": record,
            })
    items.sort(key=lambda item: (_issue_sort_key(item["issue"] or "（无期）"),
                                 item["slug"], item["record_key"],
                                 item["at"], item["bib_key"]))
    # 第 130 条：**编号按全部冲突（含已裁决）固定分配**。裁决只让条目从明细消失，
    # 不会让后面的条目改号——用户按 Cxxx 记下的决定不会错位。
    for index, item in enumerate(items, start=1):
        item["stable_no"] = index
    return items


def _count_by(values) -> dict[str, int]:
    counted: dict[str, int] = {}
    for value in values:
        counted[value] = counted.get(value, 0) + 1
    return counted


def conflict_totals(store: "bibdb.Store") -> dict:
    """冲突总数 / 待裁决 / 已裁决（CLI 打印与清单头部共用的现算数字）。"""
    items = collect_conflict_items(store)
    pending = sum(1 for item in items if item["pending"])
    return {"total": len(items), "pending": pending, "resolved": len(items) - pending}


def build_conflicts_md(store: "bibdb.Store", db_path: str) -> str:
    """生成 `CONFLICTS.md`：中文、按期→篇→条目、**只显示待裁决项**的裁决清单。

    第 130 条（2026-10-18）起：
      · 裁决结果持久化在库里（`conflicts[].resolution` + `resolved_at` + `note`），
        重新生成清单**不会丢**；清单里的 `- [ ]` 只是草稿标记，要写进库请用 `resolve`；
      · 编号 `Cxxx` 按**全部冲突（含已裁决）**固定分配：已裁决项从明细消失，其余编号不变。
    """
    stats = store.stats()
    items = collect_conflict_items(store)          # 含已裁决；stable_no 已按全量分配
    pending = [item for item in items if item["pending"]]
    resolved = [item for item in items if not item["pending"]]
    records_with = {item["record_key"] for item in items}
    slugs = {(item["issue"], item["slug"]) for item in items}
    issues_all = _count_by(item["issue"] or "（无期）" for item in items)
    issues_pending = _count_by(item["issue"] or "（无期）" for item in pending)
    issues_resolved = _count_by(item["issue"] or "（无期）" for item in resolved)
    field_counts = _count_by(name for item in pending for name in item["diff"])
    resolved_actions = _count_by(item["resolution"] or "（未标记）" for item in resolved)
    kinds = {"both": 0, "old_empty": 0, "new_empty": 0}
    for item in items:
        for values in item["diff"].values():
            if values["old"] and values["new"]:
                kinds["both"] += 1
            elif not values["old"] and values["new"]:
                kinds["old_empty"] += 1
            else:
                kinds["new_empty"] += 1
    n_default = sum(1 for item in pending if item["advice"].startswith("保留旧值"))
    n_can_adopt = len(pending) - n_default
    total_fields = sum(len(item["diff"]) for item in items)
    pending_fields = sum(len(item["diff"]) for item in pending)
    db_label = os.path.basename(db_path) or db_path
    n_kept = resolved_actions.get("kept-existing", 0)
    n_deferred = resolved_actions.get("deferred", 0)
    n_forced = resolved_actions.get("forced-replaced", 0)
    n_adopted = resolved_actions.get("adopted", 0)
    n_superseded = resolved_actions.get("superseded", 0)
    n_other = (len(resolved) - n_kept - n_deferred - n_forced
               - n_adopted - n_superseded)
    lines: list[str] = [
        "# 参考文献库 · 字段冲突待裁决清单（CONFLICTS.md）",
        "",
        "> **这份清单是给人看的**：编辑部引用库（`bibliography.jsonl`）里**已核实条目**",
        "> 遇到「同一作品、不同期成品 bib 字段写法不同」时，库**不会静默改掉已核实的旧值**，",
        "> 只把差异留档进 `conflicts`。本文件把那些差异逐条翻成中文，供编辑裁决。",
        ">",
        "> **第 130 条起裁决结果会存进库**（`conflicts[].resolution` / `resolved_at` / `note`）；",
        "> 第 135 条起 `adopt` 可**逐字段**采用新稿值（`adopted`），它覆盖掉的其它冲突标",
        "> `superseded`（被更新的裁决取代）。",
        "> 重新生成清单**不会丢**；本文件只显示**待裁决**条目。清单里的 `- [ ]` 只是草稿",
        "> 标记（重新生成就没了）——要把决定写进库，见文末「裁决方法」里的 `resolve`。",
        "",
        f"- 数据来源：引用库 `{db_label}`（本清单同目录；sha256 见下）",
        f"- 库内容 sha256：`{stats.get('sha256') or '（读不到）'}`",
        f"- 库记录：**{stats.get('records', 0)}** 条（已核实 {stats.get('verified', 0)} / "
        f"未核实 {stats.get('unverified', 0)}）",
        f"- 待裁决冲突项：**{len(pending)}** 条（共 **{pending_fields}** 处字段差异），涉及 "
        f"**{len({item['record_key'] for item in pending})}** 条库记录、"
        f"**{len({(item['issue'], item['slug']) for item in pending})}** 篇稿子、"
        f"**{len(issues_pending)}** 期",
        f"- 已裁决（不在下面明细里）：**{len(resolved)}** 条"
        f"（保留旧值 {n_kept} / 采用新值·逐字段 {n_adopted} / 采用新值·整条 {n_forced} / "
        f"待人工 {n_deferred} / 被 adopt 取代 {n_superseded}"
        + (f" / 其它 {n_other}" if n_other else "") + "）",
        f"- 冲突总数：**{len(items)}** 条（共 **{total_fields}** 处字段差异），涉及 "
        f"**{len(records_with)}** 条库记录、**{len(slugs)}** 篇稿子、**{len(issues_all)}** 期",
        f"- 待裁决的差异字段分布：" + ("、".join(
            f"{_FIELD_LABELS.get(name, name)}{count}"
            for name, count in sorted(field_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12])
            or "（无）"),
        f"- 差异类型（全部冲突）：两版都有值 **{kinds['both']}** 处；旧有新无 **{kinds['new_empty']}** 处；"
        f"旧无新有 **{kinds['old_empty']}** 处",
        f"- 建议分布（仅待裁决）：默认保留旧值 **{n_default}** 条；旧值为空、新稿补全可考虑采用 "
        f"**{n_can_adopt}** 条（逐条看下列「建议」）",
        f"- 编号规则：**Cxxx 按全部 {len(items)} 条冲突固定分配**（含已裁决）——裁决一条只是"
        "让它从明细消失，**其余编号不变**（用户已记下的编号不会错位）。",
        "",
        "> ⚠️ **这里只留档、不自动改库**：裁决由维护者用 `resolve` / `import-bib --force` 写进库",
        "> （见文末「裁决方法」）。同一份库重复生成必须**逐字节相同**（本文件不含生成时间）。",
        "",
        "## 怎么读这份清单",
        "",
        "每条**待裁决**项包含：",
        "",
        "1. **库内记录**：库里已核实的版本（题名/作者/年份）；",
        "2. **冲突来源（新稿）**：这次想让库改字段的那份稿子 bib（期 / 篇 / 文件）；",
        "3. **库内已核实来源**：旧值当初来自哪几期成品 bib；",
        "4. **逐字段差异**：`已核实旧值（库）` vs `新稿值`；",
        "5. **建议**与**三选一勾选框**：一般默认「保留旧值」，除非新值明显更完整；",
        "6. 每条的 bib key：在库里可能带 `-2` 后缀（跨期撞车时加过），用 `库内记录` 那个键查；",
        "7. 编号 Cxxx 是**固定编号**（按全部冲突分配）：本项裁决后从明细消失，其它编号不变；",
        "8. **冲突指纹**（每条下方 16 位十六进制）用于把「这一条」精确交给 `resolve`——",
        "   同一记录有多条来源不同的冲突时，`--fields` 可能同时命中多条，用指纹才精确。",
        "",
        "**裁决总则**：已核实旧值是**已出版成品**里的人工核对结果，默认保留；只有新稿值"
        "明显更完整/更准确（旧值为空、旧值是笔误、旧值来自转引来源）时才采用新值。"
        "拿不准就标「待人工查证」（defer），不要为了消差异而改学术事实。",
        "",
        "## 统计（按期）",
        "",
        "| 期 | 待裁决 | 已裁决 |",
        "|---|---|---|",
    ]
    for issue in sorted(issues_all, key=_issue_sort_key):
        lines.append(f"| {_md_escape(issue)} | {issues_pending.get(issue, 0)} | "
                     f"{issues_resolved.get(issue, 0)} |")
    lines += ["", f"## 待裁决明细（{len(pending)} 条；编号按全部冲突固定）", ""]
    if not pending:
        if not items:
            lines += ["（无：引用库当前没有字段冲突。）", ""]
        else:
            lines += ["（无：所有冲突都已裁决——保留旧值的按库内旧值生效，采用新值的已并入库，"
                      "待人工的等在人工查证。）", ""]
    current_issue = None
    current_slug = None
    for item in pending:
        issue_name = item["issue"] or "（无期——手工导入/未记录来源）"
        if issue_name != current_issue:
            current_issue = issue_name
            current_slug = None
            lines += ["", f"### 期：{_md_escape(issue_name)}", ""]
        slug_name = item["slug"] or "（无篇名）"
        if slug_name != current_slug:
            current_slug = slug_name
            lines += ["", f"#### 篇：{_md_escape(slug_name)}"
                      + (f"（`{_md_escape(item['file'])}`）" if item["file"] else ""), ""]
        title = _md_escape(item["title"])
        author = _md_escape(item["author"])
        year = f" · {_md_escape(item['year'])}" if item["year"] else ""
        lines.append(f"##### C{item['stable_no']:03d} · `{_md_escape(item['record_key'])}`"
                     f"（{_md_escape(item['entry_type'] or '未知类型')}）")
        lines.append("")
        lines.append(f"- **库内记录**：{title} · {author}{year}")
        incoming = " / ".join(part for part in (issue_name, item["slug"], item["file"]) if part)
        original_key = item["source_key"]
        key_note = (f"（原 bib key `{_md_escape(original_key)}`）"
                    if original_key and original_key != item["record_key"] else "")
        lines.append(f"- **冲突来源（新稿）**：{_md_escape(incoming)}{key_note}")
        if item["incoming_sha256"]:
            lines.append(f"- **冲突指纹**：`{item['incoming_sha256'][:16]}`"
                         "（`resolve --incoming-sha256` 可精确指定本项；"
                         "同一记录有多条冲突时用它，避免 `--fields` 误伤其它条）")
        if item["library_sources"]:
            shown = item["library_sources"][:3]
            sources_text = "；".join(_source_text(source) for source in shown)
            if len(item["library_sources"]) > len(shown):
                sources_text += f" 等 {len(item['library_sources'])} 份"
            lines.append(f"- **库内已核实来源**：{_md_escape(sources_text)}")
        else:
            lines.append("- **库内已核实来源**：（本次冲突来源即库里唯一来源，见上）")
        lines.append(f"- **库内核实方式**：`{_md_escape(item['method'] or '未记录')}`"
                     f"；核实状态：{'已核实' if item['verified'] else '**未核实**'}")
        lines.append(f"- **冲突字段（{len(item['diff'])} 处）**：")
        for name, values in item["diff"].items():
            old = _md_code(values["old"]) if values["old"] else "（空）"
            new = _md_code(values["new"]) if values["new"] else "（空）"
            lines.append(f"  - {_field_label(name)}：已核实旧值 = {old}；新稿值 = {new}")
        lines.append(f"- **建议**：{_md_escape(item['advice'])}")
        lines.append("- **裁决（勾选只是草稿；写进库用下方 resolve 命令）**：")
        lines.append("  - [ ] 保留旧值（建议默认）")
        lines.append("  - [ ] 采用新稿值（确认新值更完整/更准确）")
        lines.append("  - [ ] 仍需人工查证（先不改库）")
        lines.append("")
    lines += [
        "",
        "---",
        "",
        "## 裁决方法（维护者用；编辑只需记录决定，交给维护者执行）",
        "",
        "第 130 条起，裁决结果**写进库**（`conflicts[].resolution` + `resolved_at` + `note`）：",
        "重新生成清单不会丢，已裁决项从明细消失但**编号不变**。",
        "",
        "1. **先看差异**：",
        "   `python3 Engine/bibdb.py query --key <库内记录 key> --json`（`conflicts` 里有完整留档）。",
        "2. **保留旧值**（keep）：",
        "   `python3 Engine/bibdb.py resolve --key <库内记录 key> --action keep --all-fields`",
        "   只裁决部分字段可用 `--fields title,publisher`（一条冲突跨多字段时，选中即整条标记）；",
        "   同一记录有多条冲突时用**冲突指纹精确指定**：",
        "   `python3 Engine/bibdb.py resolve --key <库内记录 key> --action keep --incoming-sha256 <指纹>`,",
        "3. **待人工查证**（defer；先从待裁决清单移走）：",
        "   `python3 Engine/bibdb.py resolve --key <库内记录 key> --action defer --all-fields`",
        "4. **采用新稿值**：把该条从冲突来源 bib 里**单独摘出来**存成 `/tmp/one.bib`，再",
        "   `python3 Engine/bibdb.py import-bib --bib /tmp/one.bib --issue \"<期>\" --slug \"<篇>\" --force`",
        "   —— `--force` 会把被替换的旧冲突标 `forced-replaced` 并记 `resolved_at`（旧字段留档）。",
        "   ⚠️ 不要对整份 bib 用 `--force`：那会把该 bib 里**所有**与库不一致的条目一起改掉。",
        "5. 可选：`--note \"…\"` 写裁决备注；`--dry-run` 只算不写；`--json` 机读；",
        "   同一裁决重复执行是**幂等**的（无变化、不报错）。",
        "6. **改完**：重跑 `python3 Engine/bibdb.py lint`（error 必须 0）并**重新生成清单**",
        "   （`report-conflicts --out ../EditToolBox-notes/bib_db`）——已裁决项消失、编号不变。",
        "7. 不推荐手改 `bibliography.jsonl`：记录里有 `content_sha256`，手改会让 `lint` 报 error。",
        "",
    ]
    return "\n".join(lines)


def collect_duplicate_rows(store: "bibdb.Store") -> list[dict]:
    """收集「同作品不同 key」候选对（全量），带相似度与两侧信息，按相似度降序。

    判定复用库里唯一那份 `Store._near_duplicates()`（题名相似度 ≥0.90 + 第一作者姓 +
    年份相容），这里只做**展示层**：补齐题名/作者/年份/来源期/相似度并排序。
    """
    records = store.records()
    by_key = {str(record.get("key") or ""): record for record in records}
    pairs, _total = store._near_duplicates(records, cap=1_000_000)
    rows: list[dict] = []
    for first, second in pairs:
        key_a, key_b = sorted((str(first), str(second)))
        record_a, record_b = by_key.get(key_a), by_key.get(key_b)
        if record_a is None or record_b is None:
            continue
        fields_a = record_a.get("fields") or {}
        fields_b = record_b.get("fields") or {}
        title_a, author_a, year_a = _record_brief(record_a)
        title_b, author_b, year_b = _record_brief(record_b)
        similarity = bibdb.title_similarity(title_a, title_b)
        rows.append({
            "similarity": round(float(similarity), 4),
            "key_a": key_a, "key_b": key_b,
            "title_a": title_a, "title_b": title_b,
            "author_a": author_a, "author_b": author_b,
            "year_a": year_a, "year_b": year_b,
            "entry_type_a": str(record_a.get("entry_type") or ""),
            "entry_type_b": str(record_b.get("entry_type") or ""),
            "issues_a": "、".join(_record_issues(record_a)),
            "issues_b": "、".join(_record_issues(record_b)),
            "verified_a": record_a.get("verified") is True,
            "verified_b": record_b.get("verified") is True,
        })
    rows.sort(key=lambda row: (-row["similarity"], row["key_a"], row["key_b"]))
    for index, row in enumerate(rows, start=1):
        row["pair_no"] = f"D{index:04d}"
    return rows


DUPLICATES_MD_LIMIT = 200


def build_duplicates_md(store: "bibdb.Store", db_path: str, rows: list[dict]) -> str:
    """生成 `DUPLICATES.md`：同作品不同 key 的候选对（前 200 对 + 说明；全量见 CSV）。"""
    stats = store.stats()
    buckets = {"=1.0": 0, "0.95–0.999": 0, "0.90–0.95": 0}
    for row in rows:
        similarity = row["similarity"]
        if similarity >= 0.999:
            buckets["=1.0"] += 1
        elif similarity >= 0.95:
            buckets["0.95–0.999"] += 1
        else:
            buckets["0.90–0.95"] += 1
    unique_keys = {row["key_a"] for row in rows} | {row["key_b"] for row in rows}
    db_label = os.path.basename(db_path) or db_path
    lines: list[str] = [
        "# 参考文献库 · 同作品不同 key 候选对（DUPLICATES.md）",
        "",
        "> **仅供参考，不自动合并。** 同一条经典文献在不同期的稿件里常被写成不同的 BibTeX key",
        "> （如 `Belnap1960` / `1-11` / `Belnap1962`），它们在库里是**多条已核实记录**。",
        "> 本清单把这些「疑似同一作品」的**成对候选**列出来，供将来做合并/别名时人工核对；",
        "> **工具不会自动合并**（误合并学术事实比重复更糟）。单条查询请用 DOI / 题名（命中多条是正常的）。",
        "",
        f"- 数据来源：引用库 `{db_label}`（本清单同目录；sha256 见下）",
        f"- 库内容 sha256：`{stats.get('sha256') or '（读不到）'}`",
        f"- 库记录：**{stats.get('records', 0)}** 条（已核实 {stats.get('verified', 0)}）",
        f"- 候选对：**{len(rows)}** 对，涉及 **{len(unique_keys)}** 条记录",
        f"- 相似度分布：题名完全同形 **{buckets['=1.0']}** 对；"
        f"0.95–0.999 **{buckets['0.95–0.999']}** 对；0.90–0.95 **{buckets['0.90–0.95']}** 对",
        f"- 判定口径：题名归一化相似度 ≥ 0.90 + 第一作者姓相容 + 年份相容"
        f"（`slcn/bibdb.py: Store._near_duplicates()`，与 `lint` 同一份实现）",
        "",
        "## 怎么用这份清单（建议，不是自动化流程）",
        "",
        "1. 本清单**只是候选**：同名不同版本（初版/修订版/译本）也可能命中，逐对看题名/作者/年份；",
        "2. 真重复多为**经典文献跨期重复引用**（正常现象），**不需要**为了消重做任何事；",
        "3. 若确认要合并，请另开批次做「合并 + 冲突裁决」（会改库、需人工过一遍），"
        "不要手改 JSONL；",
        "4. 全量数据见同目录 `duplicates.csv`（Excel 可直接打开；UTF-8 带 BOM），"
        "本文件只列**相似度最高的前 200 对**。",
        "",
        f"## 前 {min(len(rows), DUPLICATES_MD_LIMIT)} 对（按相似度降序；全量 {len(rows)} 对见 CSV）",
        "",
        "| # | 相似度 | key A | key B | 题名 A / B | 作者 A / B | 年份 | 来源期 A / B |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows[:DUPLICATES_MD_LIMIT]:
        title_a = _md_escape(row["title_a"], width=52)
        title_b = _md_escape(row["title_b"], width=52)
        title_text = title_a if title_a == title_b else f"{title_a}<br>{title_b}"
        author_a = _md_escape(row["author_a"], width=28)
        author_b = _md_escape(row["author_b"], width=28)
        author_text = author_a if author_a == author_b else f"{author_a}<br>{author_b}"
        year_text = (row["year_a"] or "—")
        if row["year_b"] and row["year_b"] != row["year_a"]:
            year_text += f"<br>{row['year_b']}"
        issue_a = _md_escape(row["issues_a"] or "—", width=30)
        issue_b = _md_escape(row["issues_b"] or "—", width=30)
        issue_text = issue_a if issue_a == issue_b else f"{issue_a}<br>{issue_b}"
        lines.append(f"| {row['pair_no']} | {row['similarity']:.4f} | "
                     f"`{_md_escape(row['key_a'])}` | `{_md_escape(row['key_b'])}` | "
                     f"{title_text} | {author_text} | {year_text} | {issue_text} |")
    if len(rows) > DUPLICATES_MD_LIMIT:
        lines += ["", f"……其余 {len(rows) - DUPLICATES_MD_LIMIT} 对见 `duplicates.csv`。"]
    lines += [
        "",
        "---",
        "",
        "## 重新生成",
        "",
        "```bash",
        "cd EditToolBoxStudiesInLogic/Engine",
        "PYTHONPATH=\"$PWD:$PWD/vendor\" /opt/homebrew/bin/python3.14 bibdb.py \\",
        "    report-duplicates --out ../EditToolBox-notes/bib_db",
        "```",
        "",
    ]
    return "\n".join(lines)


DUPLICATES_CSV_COLUMNS = [
    "pair_no", "similarity", "key_a", "key_b",
    "title_a", "title_b", "author_a", "author_b",
    "year_a", "year_b", "entry_type_a", "entry_type_b",
    "issues_a", "issues_b", "verified_a", "verified_b",
]


def build_duplicates_csv(rows: list[dict]) -> str:
    """生成 `duplicates.csv`（全量；UTF-8 带 BOM，Excel 打开中文不乱码）。"""
    import csv as _csv
    import io

    buffer = io.StringIO()
    writer = _csv.writer(buffer, lineterminator="\n")
    writer.writerow(DUPLICATES_CSV_COLUMNS)
    for row in rows:
        writer.writerow([
            row["pair_no"], f"{row['similarity']:.4f}", row["key_a"], row["key_b"],
            row["title_a"], row["title_b"], row["author_a"], row["author_b"],
            row["year_a"], row["year_b"], row["entry_type_a"], row["entry_type_b"],
            row["issues_a"], row["issues_b"],
            "是" if row["verified_a"] else "否", "是" if row["verified_b"] else "否",
        ])
    return buffer.getvalue()


def _write_report(path: str, text: str, *, encoding: str = "utf-8") -> dict:
    """原子写报告文件（不产生 `.bak` 垃圾；重复生成内容相同 → 不重写）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    existed = os.path.isfile(path)
    if existed:
        try:
            with open(path, encoding=encoding) as handle:
                if handle.read() == text:
                    return {"path": path, "written": False,
                            "bytes": os.path.getsize(path),
                            "sha256": bibdb.db_sha256(path)}
        except (OSError, UnicodeDecodeError):
            pass
    fsutil.atomic_write(path, text, encoding=encoding, keep_backups=0)
    return {"path": path, "written": True,
            "bytes": os.path.getsize(path), "sha256": bibdb.db_sha256(path)}


def _report_out_dir(args) -> str:
    return str(getattr(args, "out", "") or "")

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
        db_existed = os.path.isfile(path)
        before = _store_snapshot(store)
        report = store.import_bib_text(text, source, verified=verified, force=args.force)
        changes = _change_rows(before, store)
        # dry-run = 只算不写：晚一点才调 `store.save()`，这里直接跳过（库文件也不会被新建）
        written = False if args.dry_run else store.save()
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    payload = {"db": path, "bib": bib_path, "source": source,
               "verified": verified, "written": written,
               "dry_run": bool(args.dry_run), "db_existed": db_existed,
               "report": report.summary(), "changes": changes}
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}" + ("" if db_existed else "（不存在，确认导入时会新建）"))
    print(f"来源：{_source_note(source)}（{'已核实' if verified else '未核实'}）")
    if args.dry_run:
        print("预览（dry-run：**未写库**，库文件一个字节都没动）：")
    _print_import_report(report.summary())
    if args.dry_run:
        _print_changes(changes)
        print("（确认无误后再点「确认入藏」/ 去掉 --dry-run 重跑。）")
        return 0
    print(f"已写回（sha256: {bibdb.db_sha256(path)}）" if written else "库无变化（幂等，未重写）。")
    return 0


def cmd_import_dir(args) -> int:
    root = args.root or paths.manuscripts_root()
    if not root:
        print(f"≡ bibdb import-dir：{paths.describe()}")
        return 0
    # 界面「入藏」页可以选**一期目录**（而不只是稿件库根）：只导入这一期。
    # 判据与核心 `scan_manuscripts` 同源（`paths.ISSUE_RE`），不是另写一套。
    issue_only = _issue_dir_if_any(root)
    try:
        path = _db_path(args)
        db_existed = os.path.isfile(path)
        path, store = _open_store(args, create=True)
        before = _store_snapshot(store)
        if issue_only:
            result = _import_one_issue(store, issue_only, force=args.force,
                                       through_year=args.through_year,
                                       finished_only=args.finished_only)
        else:
            result = bibdb.import_manuscripts(store, root, force=args.force,
                                              through_year=args.through_year,
                                              finished_only=args.finished_only)
        changes = _change_rows(before, store)
        written = False if args.dry_run else store.save()
        stats = store.stats()
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    payload = {"db": path, "written": written,
               "dry_run": bool(args.dry_run), "db_existed": db_existed,
               "root": os.path.abspath(root),
               "scope": "issue" if issue_only else "manuscripts",
               "result": result, "stats": stats, "changes": changes}
    if args.json:
        _print_json(payload)
        return 0
    if issue_only:
        print(f"单期目录：{issue_only}")
    else:
        print(f"稿件库：{os.path.abspath(root)}")
    print(f"库：{path}" + ("" if db_existed else "（不存在，确认导入时会新建）"))
    if args.dry_run:
        print("预览（dry-run：**未写库**，库文件一个字节都没动）：")
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
    if args.dry_run:
        _print_changes(changes)
        print("（确认无误后再点「确认入藏」/ 去掉 --dry-run 重跑。）")
        return 0
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
    if args.bibtex:
        # 界面「查库」页的「复制 BibTeX」直接拿这段文本（不在 Swift 里再拼一遍格式）。
        for hit in payload["hits"]:
            hit["bibtex"] = render_bibtex(hit["record"])
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
        if args.bibtex:
            print("     BibTeX：")
            for line in render_bibtex(record).rstrip("\n").split("\n"):
                print(f"       {line}")
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



def _resolve_report_out(args) -> str:
    """报告输出目录：`--out`；不给就用库文件所在目录（默认 = 笔记仓 bib_db/）。"""
    out = str(getattr(args, "out", "") or "")
    if out:
        return os.path.abspath(out)
    return os.path.dirname(os.path.abspath(_db_path(args)))


def _generate_reports(store, db_path: str, out_dir: str, *,
                      conflicts: bool = True,
                      duplicates: bool = True) -> tuple[list[dict], list[dict]]:
    """生成清单文件，返回 `(写盘结果列表, 重复候选行)`（供 cmd_* 打印/JSON）。"""
    written: list[dict] = []
    rows: list[dict] = []
    if conflicts:
        written.append(_write_report(os.path.join(out_dir, "CONFLICTS.md"),
                                     build_conflicts_md(store, db_path)))
    if duplicates:
        rows = collect_duplicate_rows(store)
        written.append(_write_report(os.path.join(out_dir, "DUPLICATES.md"),
                                     build_duplicates_md(store, db_path, rows)))
        written.append(_write_report(os.path.join(out_dir, "duplicates.csv"),
                                     build_duplicates_csv(rows),
                                     encoding="utf-8-sig"))
    return written, rows


def _print_written(written: list[dict]) -> None:
    for item in written:
        state = "已写入" if item["written"] else "内容未变（未重写）"
        print(f"   {state}：{item['path']}")
        print(f"      {item['bytes']:,} 字节，sha256: {item['sha256']}")
    print("   （清单不含生成时间：同一份库重复生成逐字节相同）")


def cmd_resolve(args) -> int:
    """记录冲突裁决：keep/defer 只改 conflicts 元数据；adopt **逐字段**改 fields。"""
    fields = [part.strip() for part in (args.fields or "").split(",") if part.strip()]
    if args.action == "adopt":
        if not (args.incoming_sha256 or "").strip():
            print("✗ --action adopt 必须配合 --incoming-sha256（冲突指纹）精确定位一条冲突",
                  file=sys.stderr)
            return 2
        if args.all_fields or fields:
            print("✗ --action adopt 只接受 --incoming-sha256；不要同时给 --fields / --all-fields",
                  file=sys.stderr)
            return 2
    elif args.allow_overlap:
        print("✗ --allow-overlap 只用于 --action adopt（keep/defer 没有重叠问题）",
              file=sys.stderr)
        return 2
    try:
        path, store = _open_store(args)
        result = store.resolve_conflicts(
            args.key, args.action, fields=fields,
            all_fields=bool(args.all_fields), note=args.note or "",
            dry_run=bool(args.dry_run),
            incoming_sha256=args.incoming_sha256 or "",
            allow_overlap=bool(args.allow_overlap))
        written = False if args.dry_run else store.save()
        totals = conflict_totals(store)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    payload = {"db": path, "written": written, **result, "totals": totals}
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"记录：`{result['key']}`；动作：{result['action']} → "
          f"resolution=`{result['resolution']}`")
    if result["already"]:
        print(f"无变化：这些冲突此前已裁决过（{result.get('resolved_count', 0)} 条；"
              f"已有裁决：{'、'.join(result.get('resolved_actions') or []) or '—'}）。")
        return 0
    if result.get("incoming_sha256"):
        target = f"冲突指纹 `{result['incoming_sha256']}`（精确一条）"
    elif result["all_fields"]:
        target = "该记录**所有**待裁决冲突"
    else:
        target = f"含字段 {('、'.join(result['fields']))} 的待裁决冲突"
    print(f"命中：{target} 共 {result['count']} 条")
    for item in result["matched"]:
        fields_text = "、".join(item["fields"]) or "（内容）"
        sha_text = (f"；incoming_sha256={item['incoming_sha256'][:12]}…"
                    if item["incoming_sha256"] else "")
        print(f"   - 冲突 #{item['index']} 字段：{fields_text}{sha_text}")
    if result["action"] == "adopt":
        print("采用新稿值（**逐字段**；只改这些字段）：")
        for name, change in (result.get("adopted_fields") or {}).items():
            if change.get("remove"):
                print(f"   - {name}：`{change['old']}` → （删除：新稿无此字段）")
            else:
                print(f"   - {name}：`{change['old']}` → `{change['new']}`")
        print(f"   content_sha256：{result['content_sha256_before'][:12]}… → "
              f"{result['content_sha256_after'][:12]}…")
        if result.get("overlapping"):
            if result.get("superseded"):
                names = "、".join(f"#{item['index']}" for item in result["superseded"])
                print(f"   ⚠️ 重叠冲突已标 `superseded`（被本次 adopt 取代）：{names}")
            else:
                names = "、".join(f"#{item['index']}" for item in result["overlapping"])
                print(f"   ⚠️ 有 {len(result['overlapping'])} 条重叠冲突将被标 `superseded`"
                      f"（--dry-run 未写）：{names}")
    if args.dry_run:
        print("（--dry-run：只算不写库；去掉该开关才会写）")
    else:
        print("已写库。" if written else "库无变化（未重写）。")
    print(f"当前：冲突 {totals['total']} 条（待裁决 {totals['pending']} / "
          f"已裁决 {totals['resolved']}）")
    print("提示：清单里的勾选只是草稿；本次裁决已写进 conflicts[].resolution/resolved_at，"
          "重新生成清单不会丢。")
    return 0


def cmd_report_conflicts(args) -> int:
    try:
        path, store = _open_store(args)
        out_dir = _resolve_report_out(args)
        written, _rows = _generate_reports(store, path, out_dir, duplicates=False)
        stats = store.stats()
        totals = conflict_totals(store)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json({"db": path, "reports": written,
                     "conflicts": stats["conflicts"], "totals": totals})
        return 0
    print(f"库：{path}")
    print(f"冲突：{stats['conflicts']['records']} 条记录 / {totals['total']} 条冲突"
          f"（**待裁决 {totals['pending']}** / 已裁决 {totals['resolved']}）")
    _print_written(written)
    return 0


def cmd_report_duplicates(args) -> int:
    try:
        path, store = _open_store(args)
        out_dir = _resolve_report_out(args)
        written, rows = _generate_reports(store, path, out_dir, conflicts=False)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json({"db": path, "reports": written, "pairs": len(rows)})
        return 0
    print(f"库：{path}")
    print(f"同作品不同 key 候选对：{len(rows)} 对（**仅供参考，不自动合并**）")
    _print_written(written)
    return 0


def cmd_report(args) -> int:
    """一次生成全部待裁决清单（README 里推荐用这一条命令）。"""
    try:
        path, store = _open_store(args)
        out_dir = _resolve_report_out(args)
        written, rows = _generate_reports(store, path, out_dir)
        stats = store.stats()
        totals = conflict_totals(store)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json({"db": path, "reports": written,
                     "conflicts": stats["conflicts"], "totals": totals,
                     "pairs": len(rows)})
        return 0
    print(f"库：{path}")
    print(f"字段冲突：{stats['conflicts']['records']} 条记录 / {totals['total']} 条冲突"
          f"（待裁决 {totals['pending']} / 已裁决 {totals['resolved']}；清单：CONFLICTS.md）")
    print(f"同作品不同 key 候选对：{len(rows)} 对（清单：DUPLICATES.md + duplicates.csv；"
          "仅供参考，不自动合并）")
    _print_written(written)
    return 0



# ── B1：引用库确定性规范化 · dry-run 报告（normalize 子命令，第 131 条后续）─────
#
# 用途：回答「如果对引用库做一次确定性规范化（B1），会发生什么？」——**只出报告，不动库**。
# 做法：每条记录按库内 `fields` 重建 BibTeX → `bibnorm.normalize_bib()`（编辑部唯一的
# 规范化实现）→ 解析回字段 → 逐条比较：
#   · **语义变更**：规范化后字段值/字段集合**真变化**（大小写/空白/对齐不算）；
#   · **仅格式变化**：只有缩进 / 字段名对齐等序列化差异；
#   · **异常**：重建或解析失败（不能静默吞掉）。
# 报告**不含生成时间戳**（同一份库重复生成逐字节相同）；本命令**绝不写 bibliography.jsonl**
# （连 `Store.save()` 都不会调），`--apply` 留给用户拍板后的批次。

NORMALIZE_REPORT_NAME = "B1-normalize-dryrun.md"
NORMALIZE_JSON_NAME = "B1-normalize-dryrun.json"

#: 标准 BibTeX 字段名形状（`posted-at` / `citeulike-article-id` / `reviewed-author` 等
#: 含连字符的名字会让 `bibnorm.parse_fields`（正则 `(\w+)\s*=`）拆错/改名/丢字段）。
_BAD_FIELD_NAME_RE = re.compile(r"^\w+$")
#: 字段值里出现 `name = value` 形状（如 note 里写 `URL = \url{…}`）会被 parse_fields 当成新字段
_EMBEDDED_ASSIGN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\s*=\s*[{\"\\]")


def _build_bib_entry(record: dict) -> str:
    """按库内 entry_type/key/fields **重建**一条 BibTeX（给规范化做输入）。"""
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields.items())
    return f"@{record.get('entry_type') or 'misc'}{{{record.get('key')},\n{body}\n}}\n"


def _unescaped(value: str, char: str) -> int:
    r"""数一个字段值里**未转义**的某字符出现次数（`\{` / `\"` 不算）。"""
    return len(re.findall(r"(?<!\\)" + re.escape(char), value))


def _value_shape_risks(name: str, value: str) -> list[str]:
    """字段值形状风险（B1 apply 前必须先人工处理的那几类）。"""
    risks: list[str] = []
    text = str(value if value is not None else "")
    if not _BAD_FIELD_NAME_RE.match(name):
        risks.append(f"字段名含连字符/非 \\w+：`{name}`（会被拆分/改名）")
    if _EMBEDDED_ASSIGN_RE.search(text):
        risks.append(f"字段值内嵌 `name = …`：`{name}`（会被拆成额外字段）")
    if _unescaped(text, "{") != _unescaped(text, "}"):
        risks.append(f"字段值花括号不配平：`{name}`（源数据疑似损坏）")
    return risks


def _normalize_rule(note: str) -> str:
    """`bibnorm.normalize_bib()` 的一条改动说明 → 报告里的规则分类（中文）。"""
    message = str(note or "").split(": ", 1)[-1]
    if message.startswith("补 userf"):
        return "补 userf = {cn/jp}（中日文标点体例）"
    if "版次并入 title" in message:
        return "英文书版次并入 title"
    if message.startswith("SEP 条目"):
        return "SEP 词条修正（howpublished→url）"
    if "合并重复字段" in message:
        return "合并重复字段"
    if message.startswith("删除字段"):
        return "删除软件元数据字段"
    if "值已清理" in message:
        return "字段值清理（页码/年份/期号等）"
    if "字段 " in message and " → " in message:
        return "字段别名归一"
    return "其它规范化规则"


def _normalize_error_reason(exc: BaseException) -> str:
    """异常 → 给人看的中文原因（已知两类：字段值里未配对引号会让 parse_fields 抛 ValueError）。"""
    message = str(exc)
    if isinstance(exc, ValueError) and "substring not found" in message:
        return ("`bibnorm.parse_fields` 解析失败：字段值里有未配对的引号"
                "（源数据疑似损坏，需人工修正后才能规范化）")
    return f"{type(exc).__name__}: {message}"


def collect_normalize_rows(store: "bibdb.Store") -> list[dict]:
    """对库里每条记录做一次规范化模拟，返回逐条结果（不改库）。

    每条：`status` ∈ semantic / format / error；`diff`（语义字段旧→新）；`rules`
    （规则分类）；`risks`（高危形状风险）；`notes`（bibnorm 原始说明）；
    `issues` / `slug`（来源期/篇，给人定位）。
    """
    rows: list[dict] = []
    for record in store.records():
        key = str(record.get("key") or "")
        issues = _record_issues(record)
        sources = _record_sources(record)
        row: dict = {
            "key": key, "entry_type": str(record.get("entry_type") or ""),
            "issues": issues, "slug": str(sources[0].get("slug") or "") if sources else "",
            "status": "format", "rules": [], "risks": [], "diff": {},
            "notes": [], "error": "",
        }
        raw_fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        for name, value in raw_fields.items():
            row["risks"].extend(_value_shape_risks(str(name), str(value)))
        row["risks"] = sorted(set(row["risks"]))
        try:
            rebuilt = _build_bib_entry(record)
            before_entries, _findings = bibparse.parse_entries(rebuilt)
            if len(before_entries) != 1:
                row["status"] = "error"
                row["error"] = f"按库内字段重建后解析出 {len(before_entries)} 条（应为 1 条）"
                rows.append(row)
                continue
            before = before_entries[0]
            normalized, notes = bibnorm.normalize_bib(rebuilt)
            row["notes"] = list(notes)
            after_entries, _findings2 = bibparse.parse_entries(normalized)
            if len(after_entries) != 1:
                row["status"] = "error"
                row["error"] = f"规范化后解析出 {len(after_entries)} 条（应为 1 条）"
                rows.append(row)
                continue
            after = after_entries[0]
            if after.key != key:
                row["status"] = "error"
                row["error"] = f"规范化改变了 key：{key!r} → {after.key!r}"
                rows.append(row)
                continue
            before_fields = bibdb.canonical_fields(before.fields)
            after_fields = bibdb.canonical_fields(after.fields)
            if str(before.etype or "").lower() != str(after.etype or "").lower():
                before_fields["$entry_type"] = str(before.etype or "").lower()
                after_fields["$entry_type"] = str(after.etype or "").lower()
            diff: dict[str, dict] = {}
            for name in sorted(set(before_fields) | set(after_fields)):
                old_value = before_fields.get(name, "")
                new_value = after_fields.get(name, "")
                if old_value != new_value:
                    diff[name] = {"old": old_value, "new": new_value}
            row["diff"] = diff
            rules = sorted({_normalize_rule(note) for note in notes})
            if row["risks"] and not rules:
                rules = ["非标准字段名/字段值形状导致静默变化（建议人工核对）"]
            if not rules:
                rules = ["其它语义变化（bibnorm 无说明，需人工核对）"]
            if diff:
                row["status"] = "semantic"
                row["rules"] = rules
            else:
                row["status"] = "format"
                row["rules"] = (rules if notes
                                else ["序列化（缩进/字段名对齐，无字段变化）"])
        except Exception as exc:  # noqa: BLE001  附加报告：单条异常不许中断整份报告
            row["status"] = "error"
            row["error"] = _normalize_error_reason(exc)
        rows.append(row)
    rows.sort(key=lambda item: item["key"])
    return rows


def _normalize_stats(rows: list[dict]) -> dict:
    """汇总统计（语义/仅格式/异常 + 规则/字段/风险分布；全部现算，不硬编码）。"""
    semantic = [row for row in rows if row["status"] == "semantic"]
    format_only = [row for row in rows if row["status"] == "format"]
    errors = [row for row in rows if row["status"] == "error"]
    rule_entries: dict[str, int] = {}
    for row in semantic:
        for rule in row["rules"]:
            rule_entries[rule] = rule_entries.get(rule, 0) + 1
    format_rule_entries: dict[str, int] = {}
    for row in format_only:
        for rule in row["rules"]:
            format_rule_entries[rule] = format_rule_entries.get(rule, 0) + 1
    field_entries: dict[str, int] = {}
    for row in semantic:
        for name in row["diff"]:
            field_entries[_field_label(name)] = field_entries.get(_field_label(name), 0) + 1
    risk_entries: dict[str, int] = {}
    for row in rows:
        for risk in row["risks"]:
            risk_entries[risk] = risk_entries.get(risk, 0) + 1
    return {
        "records": len(rows),
        "semantic": len(semantic),
        "format_only": len(format_only),
        "errors": len(errors),
        "risky_records": sum(1 for row in rows if row["risks"]),
        "rules": dict(sorted(rule_entries.items(), key=lambda kv: (-kv[1], kv[0]))),
        "format_rules": dict(sorted(format_rule_entries.items(),
                                    key=lambda kv: (-kv[1], kv[0]))),
        "fields": dict(sorted(field_entries.items(), key=lambda kv: (-kv[1], kv[0]))),
        "risks": dict(sorted(risk_entries.items(), key=lambda kv: (-kv[1], kv[0]))),
        "semantic_rows": semantic,
        "error_rows": errors,
    }


def _normalize_issue_text(row: dict) -> str:
    issues = row.get("issues") or []
    slug = row.get("slug") or ""
    if issues:
        return "；".join(issues) + (f" / {slug}" if slug else "")
    return "（无来源记录）"


def build_normalize_md(store: "bibdb.Store", db_path: str, rows: list[dict],
                       stats: dict, sha_before: str, sha_after: str) -> str:
    """生成 `B1-normalize-dryrun.md`：中文人读报告（统计/规则/逐条变更/异常）。"""
    db_label = os.path.basename(db_path) or db_path
    same = sha_before == sha_after
    lines: list[str] = [
        "# 引用库 · B1 确定性规范化 dry-run 报告",
        "",
        "> 本报告由 `bibdb.py normalize` 生成：**dry-run 只算不写** —— 不会修改",
        "> `bibliography.jsonl`（连 `Store.save()` 都不会调用）；`--apply` 留给用户拍板后的批次。",
        "> 报告**不含生成时间戳**：同一份库重复生成逐字节相同。",
        "",
        f"- 数据来源：引用库 `{db_label}`（本报告同目录）",
        f"- 库 sha256（跑前）：`{sha_before or '（读不到）'}`",
        f"- 库 sha256（跑后）：`{sha_after or '（读不到）'}`"
        + ("（与跑前一致 = **未改动库**）" if same else "（⚠️ 与跑前不同！）"),
        f"- 库记录：**{stats['records']}** 条",
        f"- 结果：**语义变更 {stats['semantic']} 条** / **仅格式变化 {stats['format_only']} 条** / "
        f"**异常 {stats['errors']} 条**",
        "- 判定口径：语义变更 = 规范化后字段值/字段集合真变化（用 `canonical_fields()` 比较，"
        "大小写/空白/字段名对齐不算）；仅格式变化 = 只有序列化（缩进/字段名对齐）差异。",
        "",
    ]
    if not same:
        lines += ["> ⚠️ **本次运行前后库 sha256 不同——理论上不可能（本命令不写库）**，"
                  "先停下检查环境/并发，不要相信下面的数字。", ""]
    lines += [
        "## 规则分类汇总（按条目；一条条目可能命中多条规则）",
        "",
        "| 规则 | 语义变更条目数 |",
        "|---|---|",
    ]
    for rule, count in stats["rules"].items():
        lines.append(f"| {_md_escape(rule)} | {count} |")
    for rule, count in stats["format_rules"].items():
        lines.append(f"| {_md_escape(rule)}（仅格式） | {count} |")
    if not stats["rules"] and not stats["format_rules"]:
        lines.append("| （无） | 0 |")
    lines += [
        "",
        "## 语义变更涉及的字段分布（按条目）",
        "",
        "| 字段 | 条目数 |",
        "|---|---|",
    ]
    for name, count in stats["fields"].items():
        lines.append(f"| {_md_escape(name)} | {count} |")
    if not stats["fields"]:
        lines.append("| （无） | 0 |")
    if stats["risks"]:
        lines += [
            "",
            "## ⚠️ 高危形状风险（B1 apply 前必须先人工处理）",
            "",
            "> 这些是 `bibnorm.normalize_bib()`（`parse_fields` 只认 `\\w+` 字段名、"
            "只认顶层 `name = value`）对**非标准字段名 / 值内嵌套赋值 / 损坏值**的已知局限：",
            "> 直接 apply 会把字段改名/拆出额外字段/丢字段。下表是**涉及条目数**：",
            "",
            "| 风险 | 涉及条目数 |",
            "|---|---|",
        ]
        for risk, count in stats["risks"].items():
            lines.append(f"| {_md_escape(risk)} | {count} |")
    lines += [
        "",
        f"## 逐条语义变更（{stats['semantic']} 条；按库内 key 排序）",
        "",
        "> 仅列**字段真变化**的条目；仅格式变化的 "
        f"{stats['format_only']} 条只统计不逐条列（全是缩进/字段名对齐差异）。",
        "",
        "| # | 库内 key | 来源期 / 篇 | 字段变更（旧值 → 新值） | 规则/说明 |",
        "|---|---|---|---|---|",
    ]
    for index, row in enumerate(stats["semantic_rows"], start=1):
        changes = []
        for name, values in row["diff"].items():
            old_value = _md_code(_clip(values.get("old") or "（空）", 60))
            new_value = _md_code(_clip(values.get("new") or "（空）", 60))
            changes.append(f"{name}：{old_value} → {new_value}")
        rules_text = "；".join(row["rules"])
        if row["risks"]:
            rules_text = "⚠️ " + rules_text
        lines.append(
            f"| B{index:04d} | `{_md_escape(row['key'])}` | {_md_escape(_normalize_issue_text(row), width=40)} | "
            f"{'<br>'.join(changes)} | {_md_escape(rules_text, width=70)} |")
    if not stats["semantic_rows"]:
        lines.append("| — | （无） | — | — | — |")
    lines += [
        "",
        f"## 异常清单（{stats['errors']} 条；重建/解析失败，不能静默吞掉）",
        "",
    ]
    if stats["error_rows"]:
        for row in stats["error_rows"]:
            lines.append(f"- `{_md_escape(row['key'])}`（{_md_escape(_normalize_issue_text(row))}）："
                         f"{_md_escape(row['error'])}")
    else:
        lines.append("（无）")
    lines += [
        "",
        "---",
        "",
        "## 说明与限制",
        "",
        "1. **本报告未修改库**：跑前/跑后 sha256 相同（见头部）；`bibliography.jsonl` 一个字节未动。",
        "2. 规范化实现 = `slcn/bibnorm.py: normalize_bib()`（编辑部唯一实现，与初排转换同一份）。",
        "3. 「语义变更」≠「必须改」：补 `userf`、版次并入 title、SEP `howpublished→url` 等都是",
        "   **编辑部既定规范**；但 apply 前要先把「高危形状风险」里的非标准字段/损坏值修掉。",
        "4. **不提供 `--apply`**（本任务不做）：是否应用、按什么顺序应用由用户拍板后另开批次。",
        "5. 重新生成：`cd Engine && python3 bibdb.py normalize --out-dir ../EditToolBox-notes/bib_db`",
        "   （同一份库重复生成逐字节相同）。",
        "",
    ]
    return "\n".join(lines)


def build_normalize_json(db_path: str, rows: list[dict], stats: dict,
                         sha_before: str, sha_after: str) -> str:
    """生成 `B1-normalize-dryrun.json`：机器可读摘要 + 逐条语义变更/异常明细。"""
    payload = {
        "tool": "bibdb.py normalize（B1 dry-run，不写库）",
        "db": os.path.basename(db_path) or db_path,
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "db_unchanged": sha_before == sha_after,
        "records": stats["records"],
        "semantic": stats["semantic"],
        "format_only": stats["format_only"],
        "errors": stats["errors"],
        "risky_records": stats["risky_records"],
        "rules": stats["rules"],
        "format_rules": stats["format_rules"],
        "fields": stats["fields"],
        "risks": stats["risks"],
        "semantic_entries": [
            {
                "key": row["key"],
                "entry_type": row["entry_type"],
                "issues": row["issues"],
                "slug": row["slug"],
                "rules": row["rules"],
                "risks": row["risks"],
                "diff": row["diff"],
            }
            for row in stats["semantic_rows"]
        ],
        "error_entries": [
            {"key": row["key"], "issues": row["issues"], "slug": row["slug"],
             "error": row["error"], "risks": row["risks"]}
            for row in stats["error_rows"]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def cmd_normalize(args) -> int:
    """B1 确定性规范化 dry-run：只出报告，**绝不写 bibliography.jsonl**。"""
    try:
        path, store = _open_store(args)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    sha_before = bibdb.db_sha256(path)
    rows = collect_normalize_rows(store)
    stats = _normalize_stats(rows)
    sha_after = bibdb.db_sha256(path)
    if sha_before != sha_after:        # 防御式：本命令按设计只读，不该发生
        print("✗ 内部错误：normalize 运行期间库文件被改动（本命令不应写库）", file=sys.stderr)
        return 2
    report_path = str(args.report or "")
    out_dir = str(args.out_dir or "")
    if not report_path and out_dir:
        report_path = os.path.join(out_dir, NORMALIZE_REPORT_NAME)
    json_path = str(args.json_out or "")
    if not json_path and out_dir:
        json_path = os.path.join(out_dir, NORMALIZE_JSON_NAME)
    written: list[dict] = []
    report_md = ""
    if report_path:
        report_md = build_normalize_md(store, path, rows, stats, sha_before, sha_after)
        written.append(_write_report(report_path, report_md))
    if json_path:
        written.append(_write_report(json_path,
                                     build_normalize_json(path, rows, stats,
                                                          sha_before, sha_after)))
    payload = {
        "db": path,
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "db_unchanged": sha_before == sha_after,
        "records": stats["records"],
        "semantic": stats["semantic"],
        "format_only": stats["format_only"],
        "errors": stats["errors"],
        "risky_records": stats["risky_records"],
        "rules": stats["rules"],
        "fields": stats["fields"],
        "risks": stats["risks"],
        "written": written,
    }
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"库 sha256（跑前/跑后）：{sha_before} / {sha_after}"
          + ("（一致 = 未改动库）" if sha_before == sha_after else "（⚠️ 不一致！）"))
    print(f"记录：{stats['records']} 条 → 语义变更 {stats['semantic']} / "
          f"仅格式 {stats['format_only']} / 异常 {stats['errors']}")
    for item in written:
        state = "已写入" if item["written"] else "内容未变（未重写）"
        print(f"   {state}：{item['path']}（{item['bytes']:,} 字节，sha256: {item['sha256']}）")
    if stats["rules"]:
        top = "、".join(f"{rule} {count}" for rule, count in list(stats["rules"].items())[:6])
        print(f"   规则 Top：{top}")
    if stats["risks"]:
        print(f"   ⚠️ 高危形状风险：涉及 {stats['risky_records']} 条记录（见报告「高危形状风险」节）")
    print("   （dry-run：未修改 bibliography.jsonl；--apply 留给用户拍板后的批次）")
    return 0

# ── 入口 ────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bibdb.py",
        description="编辑部引用库（bib_db）CLI：初始化 / 导入（可预览）/ 查询（可带 BibTeX）"
                    " / 统计 / 体检 / 待裁决清单；也是 mac 界面「引用库」页的数据源。",
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
    imp.add_argument("--dry-run", action="store_true",
                     help="预览：照常算新增/合并/冲突/跳过，但**不写库**"
                          "（--json 时带逐条 changes；界面「入藏」页的预览）")
    imp.add_argument("--json", action="store_true", help="机器可读输出")
    imp.set_defaults(func=cmd_import_bib)

    ind = sub.add_parser("import-dir", parents=[common],
                         help="只读扫描稿件仓各期成品 bib 并入库（--root 也可给一期目录）")
    ind.add_argument("--root", default="", metavar="PATH",
                     help="稿件库根，或**一期目录**（如 …/2026年第2期(87)，只导入这一期）；"
                          "默认自动探测；找不到时跳过并退出 0")
    ind.add_argument("--through-year", type=int, default=0, metavar="YYYY",
                     help="只导入 ≤ 该年份的期（0 = 不限）")
    ind.add_argument("--finished-only", action="store_true",
                     help="只导入有成品期证据（`000 …` 目录）的期")
    ind.add_argument("--force", action="store_true",
                     help="允许覆盖已核实条目（旧字段全量进 conflicts 留档）")
    ind.add_argument("--dry-run", action="store_true",
                     help="预览：照常算新增/合并/冲突/跳过，但**不写库**"
                          "（--json 时带逐条 changes；界面「入藏」页的预览）")
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
    qry.add_argument("--bibtex", action="store_true",
                     help="每条命中附带可直接复制的 BibTeX（--json 里是 `bibtex` 字段）")
    qry.add_argument("--json", action="store_true", help="机器可读输出")
    qry.set_defaults(func=cmd_query)

    stats = sub.add_parser("stats", parents=[common], help="库概况")
    stats.add_argument("--json", action="store_true", help="机器可读输出")
    stats.set_defaults(func=cmd_stats)

    lint = sub.add_parser("lint", parents=[common],
                          help="库体检（重复 key / 字段冲突 / 缺来源 / verified 无记录 …）")
    lint.add_argument("--json", action="store_true", help="机器可读输出")
    lint.set_defaults(func=cmd_lint)

    # ── 冲突裁决持久化（第 130 条）：把「保留旧值 / 待人工」写进库，重新生成清单不丢 ──
    resolve = sub.add_parser(
        "resolve", parents=[common],
        help="把冲突标记为已裁决（keep=保留旧值 / defer=待人工查证；不改字段值）")
    resolve.add_argument("--key", required=True,
                         help="库内记录 key（CONFLICTS.md 里「库内记录」那个键，可能带 -2 后缀）")
    resolve.add_argument("--action", required=True, choices=["keep", "defer", "adopt"],
                         help="keep=保留库内已核实旧值；defer=待人工查证（先从清单移走）；"
                              "adopt=**逐字段采用新稿值**（只改该冲突字段，必须配 "
                              "--incoming-sha256；整条替换仍用 import-bib --force）")
    selector = resolve.add_mutually_exclusive_group(required=True)
    selector.add_argument("--all-fields", action="store_true",
                          help="该记录**所有**待裁决冲突")
    selector.add_argument("--fields", default="",
                          help="只裁决 field_diff 含这些字段的冲突（逗号分隔，如 "
                               "title,author,$entry_type；一条冲突跨多字段时整条标记）")
    selector.add_argument("--incoming-sha256", default="", metavar="SHA",
                          help="按**冲突指纹**精确标记一条冲突（CONFLICTS.md 里每条的"
                               "「冲突指纹」，或 query --key … --json 的 "
                               "conflicts[].incoming_sha256；至少 8 位十六进制前缀且须唯一）。"
                               "同一记录有多条来源不同的冲突时用它，避免 --fields 误伤")
    resolve.add_argument("--note", default="",
                         help="裁决备注（写进 conflicts[].note，便于审计）")
    resolve.add_argument("--allow-overlap", action="store_true",
                         help="仅 --action adopt：把同记录中字段重叠的其它待裁决冲突标为 "
                              "superseded（默认遇到重叠直接拒绝，先 keep/defer 逐条处理）")
    resolve.add_argument("--dry-run", action="store_true", help="只算不写库")
    resolve.add_argument("--json", action="store_true", help="机器可读输出")
    resolve.set_defaults(func=cmd_resolve)

    # ── 待裁决清单（第 129 条后续）：给人看的中文清单，可重复生成、逐字节幂等 ──
    report = sub.add_parser(
        "report", parents=[common],
        help="生成全部待裁决清单（CONFLICTS.md / DUPLICATES.md / duplicates.csv）")
    report.add_argument("--out", default="", metavar="DIR",
                        help="输出目录（默认 = 库文件所在目录，即笔记仓 bib_db/）")
    report.add_argument("--json", action="store_true", help="机器可读输出")
    report.set_defaults(func=cmd_report)

    rc = sub.add_parser("report-conflicts", parents=[common],
                        help="生成字段冲突待裁决清单 CONFLICTS.md")
    rc.add_argument("--out", default="", metavar="DIR",
                    help="输出目录（默认 = 库文件所在目录）")
    rc.add_argument("--json", action="store_true", help="机器可读输出")
    rc.set_defaults(func=cmd_report_conflicts)

    rd = sub.add_parser("report-duplicates", parents=[common],
                        help="生成同作品不同 key 候选对（DUPLICATES.md + duplicates.csv）")
    rd.add_argument("--out", default="", metavar="DIR",
                    help="输出目录（默认 = 库文件所在目录）")
    rd.add_argument("--json", action="store_true", help="机器可读输出")
    rd.set_defaults(func=cmd_report_duplicates)

    # ── B1 确定性规范化 dry-run（第 131 条后续）：只出报告、绝不写库 ──────────
    norm = sub.add_parser(
        "normalize", parents=[common],
        help="B1 确定性规范化 dry-run（逐条重建→bibnorm.normalize_bib→比较；只出报告，"
             "**绝不写 bibliography.jsonl**）")
    norm.add_argument("--dry-run", action="store_true",
                      help="显式写一次 dry-run（默认就是；本命令从不写库，此开关只为兼容口径）")
    norm.add_argument("--report", default="", metavar="PATH",
                      help="Markdown 报告输出路径（不写则只打印摘要）")
    norm.add_argument("--out-dir", default="", metavar="DIR",
                      help=f"输出目录：写 {NORMALIZE_REPORT_NAME} + {NORMALIZE_JSON_NAME}")
    norm.add_argument("--json-out", default="", metavar="PATH",
                      help="另写 JSON 摘要文件（可选；--out-dir 时默认写同名 .json）")
    norm.add_argument("--json", action="store_true", help="stdout 输出 JSON 摘要")
    norm.set_defaults(func=cmd_normalize)
    return parser


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()          # 界面通过管道读 JSON：窄编码控制台下中文不许崩（与交付入口同一纪律）
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
