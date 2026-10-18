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
                [--dry-run]              导入一份 .bib；给了期/篇默认按「往期成品 bib」
                                           入 verified（AI 结果不要走这个口子）
    import-dir  [--root PATH] [--through-year YYYY] [--finished-only] [--force]
                [--dry-run]              只读扫描稿件仓各期成品 bib 并入库；
                                           `--root` 也可给**一期目录**（只导入这一期）
    query       (--key K | --doi D | --title T [--author A] [--year Y])
                [--limit N] [--threshold X] [--min-score X] [--bibtex] [--json]
    stats       [--json]
    lint        [--json]                    重复 key / 字段冲突 / 缺来源 / verified 无记录 …
    report      [--out DIR]                 一次生成全部待裁决清单（推荐）
    report-conflicts  [--out DIR]           字段冲突待裁决清单 CONFLICTS.md（中文、可勾选）
    report-duplicates [--out DIR]           同作品不同 key 候选对 DUPLICATES.md + duplicates.csv

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
    /opt/homebrew/bin/python3.14 bibdb.py report --out ../EditToolBox-notes/bib_db
    # ↑ 生成 CONFLICTS.md / DUPLICATES.md / duplicates.csv（同一份库重复生成逐字节相同）

退出码
------
`0` = 成功（查询 0 命中、lint 只有 warning 也算成功）；`1` = `lint` 有 error；
`2` = 用法 / IO / 库结构错误（消息在 stderr）。库文件不存在时 `query/stats/lint` /
`report*` 退出 2（先 init/import）；`import-bib` / `import-dir` 会自动建库。
`report*` 只写清单文件（可重复生成、内容幂等），不改库本体。
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

from slcn import bibdb, fsutil, paths  # noqa: E402
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
    return items


def _count_by(values) -> dict[str, int]:
    counted: dict[str, int] = {}
    for value in values:
        counted[value] = counted.get(value, 0) + 1
    return counted


def build_conflicts_md(store: "bibdb.Store", db_path: str) -> str:
    """生成 `CONFLICTS.md`：中文、按期→篇→条目、逐条可勾选的冲突裁决清单。"""
    stats = store.stats()
    items = collect_conflict_items(store)
    records_with = {item["record_key"] for item in items}
    issues = _count_by(item["issue"] or "（无期）" for item in items)
    slugs = {(item["issue"], item["slug"]) for item in items}
    field_counts = _count_by(name for item in items for name in item["diff"])
    kinds = {"both": 0, "old_empty": 0, "new_empty": 0}
    for item in items:
        for values in item["diff"].values():
            if values["old"] and values["new"]:
                kinds["both"] += 1
            elif not values["old"] and values["new"]:
                kinds["old_empty"] += 1
            else:
                kinds["new_empty"] += 1
    advice_counts = _count_by(item["advice"] for item in items)
    n_default = sum(1 for item in items if item["advice"].startswith("保留旧值"))
    n_can_adopt = len(items) - n_default
    total_fields = sum(len(item["diff"]) for item in items)
    db_label = os.path.basename(db_path) or db_path
    lines: list[str] = [
        "# 参考文献库 · 字段冲突待裁决清单（CONFLICTS.md）",
        "",
        "> **这份清单是给人看的**：编辑部引用库（`bibliography.jsonl`）里**已核实条目**",
        "> 遇到「同一作品、不同期成品 bib 字段写法不同」时，库**不会静默改掉已核实的旧值**，",
        "> 只把差异留档进 `conflicts`。本文件把那些差异逐条翻成中文，供编辑裁决。",
        "",
        f"- 数据来源：引用库 `{db_label}`（本清单同目录；sha256 见下）",
        f"- 库内容 sha256：`{stats.get('sha256') or '（读不到）'}`",
        f"- 库记录：**{stats.get('records', 0)}** 条（已核实 {stats.get('verified', 0)} / "
        f"未核实 {stats.get('unverified', 0)}）",
        f"- 待裁决冲突项：**{len(items)}** 条（共 **{total_fields}** 处字段差异），涉及 "
        f"**{len(records_with)}** 条库记录、**{len(slugs)}** 篇稿子、**{len(issues)}** 期",
        f"- 差异字段分布：" + "、".join(
            f"{_FIELD_LABELS.get(name, name)}{count}"
            for name, count in sorted(field_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12]),
        f"- 差异类型：两版都有值 **{kinds['both']}** 处；旧有新无 **{kinds['new_empty']}** 处；"
        f"旧无新有 **{kinds['old_empty']}** 处",
        f"- 建议分布：默认保留旧值 **{n_default}** 条；旧值为空、新稿补全可考虑采用 "
        f"**{n_can_adopt}** 条（逐条看下列「建议」）",
        "",
        "> ⚠️ **这里只留档、不自动改库**：勾选/裁决后由维护者用命令改库（见文末「裁决方法」）。",
        "> 同一份库重复生成必须**逐字节相同**（本文件不含生成时间，翻 diff 不会因重跑而变化）。",
        "",
        "## 怎么读这份清单",
        "",
        "每条待裁决项包含：",
        "",
        "1. **库内记录**：库里已核实的版本（题名/作者/年份）；",
        "2. **冲突来源（新稿）**：这次想让库改字段的那份稿子 bib（期 / 篇 / 文件）；",
        "3. **库内已核实来源**：旧值当初来自哪几期成品 bib；",
        "4. **逐字段差异**：`已核实旧值（库）` vs `新稿值`；",
        "5. **建议**与**勾选框**：一般默认「保留旧值」，除非新值明显更完整；",
        "6. 每条的 bib key：在库里可能带 `-2` 后缀（跨期撞车时加过），用 `库内记录` 那个键查。",
        "",
        "**裁决总则**：已核实旧值是**已出版成品**里的人工核对结果，默认保留；只有新稿值"
        "明显更完整/更准确（旧值为空、旧值是笔误、旧值来自转引来源）时才采用新值。"
        "拿不准就勾「仍需人工查证」，不要为了消差异而改学术事实。",
        "",
        "## 统计（按期）",
        "",
        "| 期 | 待裁决条数 |",
        "|---|---|",
    ]
    for issue in sorted(issues, key=_issue_sort_key):
        lines.append(f"| {_md_escape(issue)} | {issues[issue]} |")
    lines += ["", f"## 待裁决明细（{len(items)} 条）", ""]
    if not items:
        lines += ["（无：引用库当前没有字段冲突。）", ""]
    current_issue = None
    current_slug = None
    index = 0
    for item in items:
        index += 1
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
        lines.append(f"##### C{index:03d} · `{_md_escape(item['record_key'])}`"
                     f"（{_md_escape(item['entry_type'] or '未知类型')}）")
        lines.append("")
        lines.append(f"- **库内记录**：{title} · {author}{year}")
        incoming = " / ".join(part for part in (issue_name, item["slug"], item["file"]) if part)
        original_key = item["source_key"]
        key_note = (f"（原 bib key `{_md_escape(original_key)}`）"
                    if original_key and original_key != item["record_key"] else "")
        lines.append(f"- **冲突来源（新稿）**：{_md_escape(incoming)}{key_note}")
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
        lines.append("- **裁决（勾选一项）**：")
        lines.append("  - [ ] 保留旧值（建议默认）")
        lines.append("  - [ ] 采用新稿值（确认新值更完整/更准确）")
        lines.append("  - [ ] 仍需人工查证（先不改库）")
        lines.append("")
    lines += [
        "",
        "---",
        "",
        "## 裁决方法（维护者用；编辑只需在上面的勾选框里记录决定）",
        "",
        "1. **先看差异**：",
        "   `python3 Engine/bibdb.py query --key <库内记录 key> --json`（`conflicts` 里有完整留档）。",
        "2. **决定「采用新稿值」时**（默认保留旧值，除非新值明显更完整）：",
        "   把该条从冲突来源 bib 里**单独摘出来**存成 `/tmp/one.bib`（只留这一条），然后",
        "   `python3 Engine/bibdb.py import-bib --bib /tmp/one.bib \\",
        "       --issue \"<期>\" --slug \"<篇>\" --force`",
        "   —— `--force` 会把旧字段全量写进 `conflicts`（resolution=`forced-replaced`）留档。",
        "   ⚠️ 不要直接对整份 BibTeX 用 `--force`：那会把该 bib 里**所有**与库不一致的条目一起改掉。",
        "3. **决定「保留旧值」时**：什么都不用做；本条继续留在 `conflicts` 里（不影响查询/命中）。",
        "4. **改完**：重跑 `python3 Engine/bibdb.py lint`（error 必须 0）并**重新生成本清单**；",
        "   本文件里已裁决的条目会消失（或变为 `forced-replaced` 留档，按实现而定）。",
        "5. 不推荐手改 `bibliography.jsonl`：记录里有 `content_sha256`，手改字段会让 `lint` 报 error。",
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


def cmd_report_conflicts(args) -> int:
    try:
        path, store = _open_store(args)
        out_dir = _resolve_report_out(args)
        written, _rows = _generate_reports(store, path, out_dir, duplicates=False)
        stats = store.stats()
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json({"db": path, "reports": written,
                     "conflicts": stats["conflicts"]})
        return 0
    print(f"库：{path}")
    print(f"冲突：{stats['conflicts']['records']} 条记录 / "
          f"{stats['conflicts']['total']} 条待裁决项（已核实旧值不会被静默覆盖）")
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
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if args.json:
        _print_json({"db": path, "reports": written,
                     "conflicts": stats["conflicts"], "pairs": len(rows)})
        return 0
    print(f"库：{path}")
    print(f"字段冲突：{stats['conflicts']['records']} 条记录 / "
          f"{stats['conflicts']['total']} 条待裁决项（清单：CONFLICTS.md）")
    print(f"同作品不同 key 候选对：{len(rows)} 对（清单：DUPLICATES.md + duplicates.csv；"
          "仅供参考，不自动合并）")
    _print_written(written)
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
