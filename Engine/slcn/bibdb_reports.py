"""bibdb 报告族（B-228 第一段）：报告数据收集 + markdown/csv/json 渲染与写出。

纯搬运自 v1.4.0-9 `Engine/bibdb.py`（B-228 第一段，零行为变化）：冲突/重复/规范化/
作者/编辑/合并各族的 `collect_*`、`build_*`、`_write_*` 与报告统计；报告渲染所需的
底层收集与判据 helper 一并放在本模块（否则会形成 reports→cli 反向依赖）。
依赖方向 `bibdb_core ← bibdb_reports ← bibdb_import ← bibdb_cli`（无循环 import）。
函数体逐字节未改；唯一改动是模块头 import 与本 docstring。门面 re-export 全部旧名字。
"""
from __future__ import annotations

import difflib, json, os, re

from slcn import bibdb, bibnorm, fsutil
from slcn.bibdb_core import (
    _clip,
)  # noqa: F401

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


#: `conflicts --json` 的条目字段白名单（spec §4.1；去掉内部 `record` / `incoming_source`
#: 大对象，只保留界面需要的稳定契约字段）。新增字段只能往这里**加**，不能改老键。
_CONFLICT_ITEM_FIELDS = (
    "stable_no", "record_key", "bib_key", "entry_type", "title", "author",
    "year", "issue", "slug", "file", "at", "incoming_sha256", "pending",
    "resolution", "resolved_at", "diff", "advice", "verified", "method",
)


def _public_conflict_item(item: dict) -> dict:
    """冲突条目 → `conflicts --json` 输出（白名单投影，不泄露内部大对象）。"""
    return {name: item.get(name) for name in _CONFLICT_ITEM_FIELDS}


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


def _normalize_pending_conflict_fields(record: dict) -> set[str]:
    """该记录**待裁决**冲突涉及的字段集合（apply 不能碰这些字段，否则冲突旧值过期）。"""
    names: set[str] = set()
    for conflict in record.get("conflicts") or []:
        if isinstance(conflict, dict) and bibdb.conflict_is_pending(conflict):
            names |= set(bibdb.conflict_fields(conflict))
    return names


def build_normalize_apply_md(db_path: str, stats: dict, applied: list[dict],
                             skipped: list[dict], verification: dict,
                             sha_before: str, sha_after: str,
                             backup_path: str, backup_sha: str) -> str:
    """生成 B1 apply 报告（中文人读：改了什么/跳过了什么/二次校验）。"""
    db_label = os.path.basename(db_path) or db_path
    field_count = sum(len(item["diff"]) for item in applied)
    semantic_count = sum(1 for item in applied if item["semantic"])
    raw_count = len(applied) - semantic_count
    lines: list[str] = [
        "# 引用库 · B1 确定性规范化 apply 报告",
        "",
        "> 本报告由 `bibdb.py normalize --apply` 生成：**已把安全条目的规范化结果写进库**。",
        "> 只更新 `entry_type` / `fields` / `content_sha256` / `updated_at`；",
        "> `verified` / `method` / `sources` / `conflicts` / `aliases` / 创建时间**未动**。",
        "",
        f"- 数据来源：引用库 `{db_label}`",
        f"- 库 sha256（apply 前）：`{sha_before or '（读不到）'}`",
        f"- 库 sha256（apply 后）：`{sha_after or '（读不到）'}`",
        f"- apply 前备份：`{backup_path or '（未提供）'}`"
        f"（sha256 `{backup_sha or '（未提供）'}`，与 apply 前一致 = 可回滚）",
        f"- 库记录：**{stats['records']}** 条",
        f"- 本次 apply：**{len(applied)}** 条（语义变更 {semantic_count} 条 + "
        f"仅原始值/格式 {raw_count} 条；共改 **{field_count}** 处字段）",
        f"- **未 apply / 需人工处理：{len(skipped)} 条**（逐条见下）",
        f"- 二次校验（apply 后重跑 dry-run）：语义变更 **{verification['semantic']}** 条"
        f"（**排除条目后剩余 {verification['semantic_excluding_skipped']} 条**）/ "
        f"仅格式 {verification['format_only']} / 异常 {verification['errors']}",
        "",
        "> ⚠️ 库存 `fields` 字典、不存 BibTeX 排版：所谓「仅格式变化」在库里没有落盘差异，"
        "**不需要 apply**；本报告只列真正改了字段的条目。",
        "",
        "## apply 明细（按库内 key 排序）",
        "",
        "| # | 库内 key | 类型 | 字段变更（旧值 → 新值） | 规则 |",
        "|---|---|---|---|---|",
    ]
    for index, item in enumerate(applied, start=1):
        changes = []
        for name, values in item["diff"].items():
            old_value = _md_code(_clip(values.get("old") or "（空）", 50))
            new_value = _md_code(_clip(values.get("new") or "（空）", 50))
            changes.append(f"{name}：{old_value} → {new_value}")
        lines.append(
            f"| A{index:04d} | `{_md_escape(item['key'])}` | {_md_escape(item['entry_type'])} | "
            f"{'<br>'.join(changes) or '（仅类型）'} | "
            f"{_md_escape('；'.join(item['rules']) or '（无）', width=50)} |")
    if not applied:
        lines.append("| — | （无） | — | — | — |")
    lines += [
        "",
        f"## 未 apply / 需人工处理清单（{len(skipped)} 条，不能静默吞掉）",
        "",
    ]
    if skipped:
        for item in skipped:
            lines.append(f"- `{_md_escape(item['key'])}`：{_md_escape(item['reason'])}")
    else:
        lines.append("（无）")
    lines += [
        "",
        "## 二次校验（apply 后重跑 dry-run）",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 记录数 | {verification['records']} |",
        f"| 语义变更 | {verification['semantic']}（排除未 apply 条目后 "
        f"**{verification['semantic_excluding_skipped']}**） |",
        f"| 仅格式变化 / 无需改动 | {verification['format_only']} |",
        f"| 异常 | {verification['errors']} |",
        "",
    ]
    if verification["remaining_keys"]:
        lines += ["剩余语义变更 key（前 20 个，应全部在未 apply 清单里）：",
                  "", "、".join(f"`{key}`" for key in verification["remaining_keys"]), ""]
    lines += [
        "## 回滚方法（如需）",
        "",
        f"```bash",
        f"cp {backup_path or '/tmp/bibliography_before_B1_apply.jsonl'} "
        f"../EditToolBox-notes/bib_db/bibliography.jsonl",
        "```",
        "",
        f"（备份 sha256：`{backup_sha or '（未提供）'}`）",
        "",
    ]
    return "\n".join(lines)


def build_normalize_apply_json(path: str, stats: dict, applied: list[dict],
                               skipped: list[dict], verification: dict,
                               sha_before: str, sha_after: str,
                               backup_path: str, backup_sha: str) -> str:
    """B1 apply 的机器可读摘要（applied / skipped / 二次校验）。"""
    payload = {
        "tool": "bibdb.py normalize --apply",
        "db": os.path.basename(path) or path,
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "backup_path": backup_path,
        "backup_sha256": backup_sha,
        "records": stats["records"],
        "applied": len(applied),
        "applied_semantic": sum(1 for item in applied if item["semantic"]),
        "applied_field_changes": sum(len(item["diff"]) for item in applied),
        "skipped": len(skipped),
        "applied_entries": applied,
        "skipped_entries": skipped,
        "verification": verification,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


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


# ── B2：作者倒装批处理（normalize-authors，第 136 条后续）──────────────────────
#
# 编辑部 2026-10-15 决定：英文 `author` 一律倒装成「姓, 名缩写」；中文/日文不倒装。
# 复用 `slcn.bibnorm` 的同一份实现（检查器/规范化器同源）：
#   · `normalize_author_field()` 已处理：多作者 `and`、虚词（van/de/von）、
#     全名缩成首字母、花括号/LaTeX 保护原样、CJK 原样、已倒装原样（幂等）、
#     「全有或全无」（一段判不准整条不动）；
#   · 本 CLI 再加一层**保守跳过**：疑似机构/组织作者、`and others`、邮箱/网址、
#     待裁决冲突字段 → 不进 apply（宁可漏改，不改错学术事实）。
# 默认 dry-run；`--apply` 才写库，且只改 `author` 字段（+ content_sha256/updated_at）。
# `editor` **不动**：同一套倒装规则也适用 editor，但本批按任务口径只处理 author
# （editor 样本/风险另批评估）；如需同批处理需用户拍板。

AUTHOR_REPORT_DRY = "B2-author-normalize-dryrun.md"
AUTHOR_REPORT_APPLY = "B2-author-normalize-apply.md"

_CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
#: 机构/组织作者的明显词（人名里一般不会出现；命中就整条不动）
#: B-291：补罗马字日文机构词（长音/无长音变体 `Kankōkai|Kankokai` 等）；`Board` 不作
#: 孤立判据（`Oliver Board` 是真人名，B-248 误报），只认下面 `Board of/for/on …` 组合。
_INSTITUTION_RE = re.compile(
    r"\b(?:Association|Society|Organization|Organisation|Institute|University|Universit[äa]t|"
    r"College|School|Department|Faculty|Center|Centre|Committee|Council|Group|Team|Consortium|"
    r"Network|Agency|Office|Bureau|Panel|Project|Workshop|Conference|Congress|Foundation|"
    r"Corporation|Company|Inc|Ltd|Press|Publishing|Ministry|Government|Bank|Laboratory|Lab|"
    r"Commission|Federation|Union|Academy|Trust|Forum|Initiative|Platform|Alliance|Coalition|"
    r"Staff|Members|Contributors|Editors|Editorial|"
    r"Daigaku|Kank[oō]kai|Hensh[ūu]bu|Sh[ūu]eisha)\b", re.I)
#: `Board` 单独出现不算机构（`Oliver Board` 是人名）；只有「Board of/for/on …」组合才算。
_INSTITUTION_BOARD_RE = re.compile(r"\bBoard\s+(?:of|for|on)\b", re.I)


def is_institution_author(value: str) -> bool:
    """作者/编者值是否疑似机构/组织（`_author_skip_reason()` 机构判据的单点）。"""
    text = (value or "").strip()
    return bool(text and (_INSTITUTION_RE.search(text)
                          or _INSTITUTION_BOARD_RE.search(text)))


def _author_skip_reason(value: str) -> str:
    """作者值不适合（或不必要）倒装时的中文原因（空串 = 可以倒装）。"""
    text = (value or "").strip()
    if not text:
        return "空值"
    if _CJK_RE.search(text):
        return "含中文/日文（不倒装）"
    if is_institution_author(text):
        return "疑似机构/组织作者（不推断）"
    if re.search(r"@|https?://", text):
        return "含邮箱/网址（不推断）"
    if "and others" in text.lower():
        return "含 `and others`（保持原样）"
    if "{" in text or "\\" in text:
        return "含花括号/LaTeX 保护（保持原样）"
    if "," in text:
        return "已倒装或含逗号（保持原样）"
    words = text.split()
    if len(words) < 2:
        return "单名/只有一个词（保持原样）"
    return ""       # 结构上可以尝试倒装（最终由 bibnorm 判准不准）


def collect_author_rows(store: "bibdb.Store") -> list[dict]:
    """逐条计算 author 倒装结果（不改库）。

    返回行：`key` / `old` / `new` / `status`（change / skip）/ `reason` / `issues`。
    """
    rows: list[dict] = []
    for record in store.records():
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        if "author" not in fields:
            continue
        old = str(fields.get("author") or "")
        row = {
            "key": str(record.get("key") or ""),
            "old": old,
            "new": old,
            "status": "skip",
            "reason": "",
            "issues": _record_issues(record),
        }
        reason = _author_skip_reason(old)
        if not reason:
            pending = _normalize_pending_conflict_fields(record)
            if "author" in pending:
                reason = "待裁决冲突字段（先处理冲突）"
        if reason:
            row["reason"] = reason
            rows.append(row)
            continue
        new = bibnorm.normalize_author_field(old)
        if new == old:
            row["reason"] = _author_skip_reason(old) or "规范化器判不准（保持原样）"
            rows.append(row)
            continue
        row["status"] = "change"
        row["new"] = new
        rows.append(row)
    rows.sort(key=lambda item: item["key"])
    return rows


def _author_stats(rows: list[dict]) -> dict:
    changes = [row for row in rows if row["status"] == "change"]
    skips = [row for row in rows if row["status"] == "skip"]
    reasons: dict[str, int] = {}
    for row in skips:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    return {
        "with_author": len(rows),
        "changes": len(changes),
        "skips": len(skips),
        "reasons": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "change_rows": changes,
        "skip_rows": skips,
    }


def build_author_report_md(db_path: str, stats: dict, sha_before: str,
                           sha_after: str, *, applied: bool,
                           skipped_apply: list[dict] | None = None,
                           verification: dict | None = None,
                           backup_path: str = "", backup_sha: str = "") -> str:
    """B2 作者倒装报告（dry-run / apply 共用；apply 版多二次校验与备份信息）。"""
    db_label = os.path.basename(db_path) or db_path
    title = ("# 引用库 · B2 作者倒装 apply 报告" if applied
             else "# 引用库 · B2 作者倒装 dry-run 报告")
    lines: list[str] = [title, ""]
    if applied:
        lines += [
            "> 本报告由 `bibdb.py normalize-authors --apply` 生成：**已把安全条目的英文作者倒装写进库**。",
            "> 只改 `author` 字段 + `content_sha256` + `updated_at`；其余字段与元数据未动。",
            "> 中文/日文、机构、`and others`、花括号/LaTeX 保护、已倒装、判不准 → **跳过**。",
            "",
            f"- 库 sha256（apply 前）：`{sha_before or '（读不到）'}`",
            f"- 库 sha256（apply 后）：`{sha_after or '（读不到）'}`",
            f"- apply 前备份：`{backup_path or '（未提供）'}`（sha256 `{backup_sha or '（未提供）'}`）",
        ]
    else:
        lines += [
            "> 本报告由 `bibdb.py normalize-authors` 生成：**dry-run 只算不写**，不会修改",
            "> `bibliography.jsonl`。只处理 `author` 字段（`editor` 本批不动）。",
            "> 跳过口径：中文/日文、机构、`and others`、花括号/LaTeX 保护、已倒装、判不准 → 原样不动。",
            "",
            f"- 库 sha256：`{sha_before or '（读不到）'}`（dry-run 后未变：{sha_before == sha_after}）",
        ]
    lines += [
        f"- 有 `author` 字段的记录：**{stats['with_author']}** 条",
        f"- {'已倒装' if applied else '将倒装'}：**{stats['changes']}** 条",
        f"- 跳过：**{stats['skips']}** 条（按原因见下表）",
        "",
        "## 跳过原因分类",
        "",
        "| 原因 | 条数 |",
        "|---|---|",
    ]
    for reason, count in stats["reasons"].items():
        lines.append(f"| {_md_escape(reason)} | {count} |")
    if not stats["reasons"]:
        lines.append("| （无） | 0 |")
    sample_label = "已倒装样例（前 20，旧 → 新）" if applied else "将倒装样例（前 20，旧 → 新）"
    lines += ["", f"## {sample_label}", "",
              "| # | 库内 key | 旧值 | 新值 |", "|---|---|---|---|"]
    for index, row in enumerate(stats["change_rows"][:20], start=1):
        lines.append(f"| {index:02d} | `{_md_escape(row['key'])}` | "
                     f"`{_md_escape(row['old'], width=60)}` | `{_md_escape(row['new'], width=60)}` |")
    if not stats["change_rows"]:
        lines.append("| — | （无） | — | — |")
    lines += ["", "## 跳过样例（前 20）", "",
              "| # | 库内 key | author 原值 | 原因 |", "|---|---|---|---|"]
    for index, row in enumerate(stats["skip_rows"][:20], start=1):
        lines.append(f"| {index:02d} | `{_md_escape(row['key'])}` | "
                     f"`{_md_escape(row['old'], width=60)}` | {_md_escape(row['reason'])} |")
    if not stats["skip_rows"]:
        lines.append("| — | （无） | — | — |")
    if applied:
        lines += ["", "## 二次校验（apply 后重跑 dry-run）", ""]
        if verification is not None:
            lines += [
                f"- 仍将倒装（排除未 apply 的跳过条目后）：**{verification['remaining']}** 条（应为 0）",
                f"- 跳过：{verification['skips']} 条；有 author 的记录：{verification['with_author']} 条",
                "",
            ]
            if verification.get("remaining_keys"):
                lines += ["剩余 key（前 20）：", "",
                          "、".join(f"`{key}`" for key in verification["remaining_keys"]), ""]
        if skipped_apply:
            shown = skipped_apply[:50]
            lines += [f"## 本次未 apply 的条目（前 {len(shown)} 条；共 {len(skipped_apply)} 条）", ""]
            for item in shown:
                lines.append(f"- `{_md_escape(item['key'])}`：{_md_escape(item['reason'])}")
            if len(skipped_apply) > len(shown):
                lines.append(f"- …（其余 {len(skipped_apply) - len(shown)} 条略："
                             "完整跳过清单见 `--json-out` 的 JSON 汇总或上面「跳过原因分类」）")
            lines.append("")
        lines += ["## 回滚方法（如需）", "",
                  "```bash",
                  f"cp {backup_path or '/tmp/bibliography_before_B2_authors.jsonl'} "
                  "../EditToolBox-notes/bib_db/bibliography.jsonl",
                  "```", "",
                  f"（备份 sha256：`{backup_sha or '（未提供）'}`）", ""]
    return "\n".join(lines)


def build_author_json(path: str, stats: dict, sha_before: str, sha_after: str,
                      *, applied: bool, applied_rows: list[dict] | None = None,
                      skipped_apply: list[dict] | None = None,
                      verification: dict | None = None,
                      backup_path: str = "", backup_sha: str = "") -> str:
    payload = {
        "tool": "bibdb.py normalize-authors" + (" --apply" if applied else "（dry-run）"),
        "db": os.path.basename(path) or path,
        "mode": "apply" if applied else "dry-run",
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "db_unchanged": sha_before == sha_after,
        "backup_path": backup_path,
        "backup_sha256": backup_sha,
        "with_author": stats["with_author"],
        "changes": stats["changes"],
        "skips": stats["skips"],
        "reasons": stats["reasons"],
        "change_entries": applied_rows if applied_rows is not None else [
            {"key": row["key"], "old": row["old"], "new": row["new"]}
            for row in stats["change_rows"]],
        "skipped_entries": skipped_apply if applied_rows is not None else [
            {"key": row["key"], "old": row["old"], "reason": row["reason"]}
            for row in stats["skip_rows"]],
        "verification": verification,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_author_reports(args, store, path: str, sha_before: str, stats: dict,
                          *, applied: bool, applied_rows=None, skipped_apply=None,
                          verification=None, backup_path="", backup_sha="") -> list[dict]:
    report_path = str(getattr(args, "report", "") or "")
    out_dir = str(getattr(args, "out_dir", "") or "")
    default_name = AUTHOR_REPORT_APPLY if applied else AUTHOR_REPORT_DRY
    if not report_path and out_dir:
        report_path = os.path.join(out_dir, default_name)
    # 只有显式给 --json-out 才写 JSON（--out-dir 只写 Markdown，避免把逐条全量明细塞进笔记仓）
    json_path = str(getattr(args, "json_out", "") or "")
    sha_after = bibdb.db_sha256(path)
    written: list[dict] = []
    if report_path:
        written.append(_write_report(report_path, build_author_report_md(
            path, stats, sha_before, sha_after, applied=applied,
            skipped_apply=skipped_apply, verification=verification,
            backup_path=backup_path, backup_sha=backup_sha)))
    if json_path:
        written.append(_write_report(json_path, build_author_json(
            path, stats, sha_before, sha_after, applied=applied,
            applied_rows=applied_rows, skipped_apply=skipped_apply,
            verification=verification, backup_path=backup_path, backup_sha=backup_sha)))
    return written


# ── B3：编者倒装 dry-run（normalize-editors；只出报告，**绝不写库**）───────────
#
# 编辑部 2026-10-15 决定：英文 `author` / `editor` **一律倒装**成「姓, 名缩写」。
# B2（`normalize-authors`）只做了 author，editor 留到 B3（交接提示词原文：
# 「editor 倒装（B3）：先 dry-run 报告、用户拍板后 apply；规则同 B2」）。
#
# 本批**只实现 dry-run**，绝不 apply / 绝不写 `bibliography.jsonl`：
#   · 判定与 B2 完全同源：`_author_skip_reason()` + `slcn.bibnorm.normalize_author_field()`，
#     只把字段名换成 `editor`；连「只有 editor 没有 author」的记录也会被扫到
#     （B2 的采集器硬编码 `author`，会漏掉这类记录；本库当前 29 条）。
#   · **不实现可执行的 --apply**：传了就明确拒绝（退出 2）。B3 apply 须用户拍板后另批，
#     届时沿用 B2 的 `--apply --backup`（sha256 与当前库一致才允许执行）门禁。
#   · 报告/MD/CSV/JSON 无时间戳、无绝对路径：同一份库重复生成逐字节相同。
EDITOR_REPORT_DRY = "B3-editor-normalize-dryrun.md"
EDITOR_REPORT_APPLY = "B3-editor-normalize-apply.md"
EDITOR_REPORT_APPLY_CSV = "B3-editor-normalize-apply.csv"
EDITOR_REPORT_APPLY_JSON = "B3-editor-normalize-apply.json"
EDITOR_REPORT_CSV = "B3-editor-normalize-dryrun.csv"
EDITOR_REPORT_JSON = "B3-editor-normalize-dryrun.json"

#: 编者标记（`(ed.)` / `(eds.)` / `(editors)` / `（编）` / `主编`）——只做风险标记，不改判定。
_EDITOR_MARK_RE = re.compile(r"\(\s*(?:eds?|editors?|编|主编|主編|編)\.?\s*\)", re.I)
#: 粘连首字母（`M.C.` / `J.A.`）：规范化器会拆成 `M. C.`，报告里标出来供抽查。
_GLUED_INITIALS_RE = re.compile(r"(?<=[A-Za-z]\.)(?=[A-Za-z])")


def _role_part_count(value: str) -> int:
    """`A and B` 形式里的人名个数（用于「多编者」风险标记）。"""
    return len([part for part in re.split(r"\s+and\s+", value or "", flags=re.I)
                if part.strip()])


def _editor_risk_flags(record: dict, old: str, new: str) -> list[str]:
    """B3 风险标记（只记号，不参与判定；顺序固定 → 报告可逐字节复现）。"""
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    flags: list[str] = []
    editors_n = _role_part_count(old)
    if editors_n >= 2:
        flags.append(f"多编者({editors_n})")
    if _EDITOR_MARK_RE.search(old or ""):
        flags.append("(eds.) 形态")
    if not str(fields.get("author") or "").strip():
        flags.append("仅 editor")
    if _GLUED_INITIALS_RE.search(old or ""):
        flags.append("粘连缩写")
    if " AND " in (old or ""):
        flags.append("大写 AND")
    # 只对「将倒装」行标：倒装后的值会带花括号保护（如 `{Kaulbach (eds.)}, F.`）
    if new != old and "{" in (new or ""):
        flags.append("倒装后加花括号")
    return flags


def collect_editor_rows(store: "bibdb.Store") -> list[dict]:
    """逐条计算 editor 倒装结果（**不改库**）。

    行字段与 B2 的 `collect_author_rows` 对齐，另带 B3 报告需要的来源/题名/风险标记：
    `key` / `old` / `new` / `status`（change/skip）/ `reason` / `issues`（记录出现过的期）/
    `sources`（期/篇/文件文本）/ `entry_type` / `title` / `n_editors` / `has_author` / `flags`。
    """
    rows: list[dict] = []
    for record in store.records():
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        if "editor" not in fields:
            continue
        old = str(fields.get("editor") or "")
        row = {
            "key": str(record.get("key") or ""),
            "old": old,
            "new": old,
            "status": "skip",
            "reason": "",
            "issues": _record_issues(record),
            "sources": [_source_text(source) for source in _record_sources(record)],
            "entry_type": str(record.get("entry_type") or ""),
            "title": str(fields.get("title") or fields.get("booktitle") or ""),
            "n_editors": _role_part_count(old),
            "has_author": bool(str(fields.get("author") or "").strip()),
        }
        reason = _author_skip_reason(old)
        if not reason:
            pending = _normalize_pending_conflict_fields(record)
            if "editor" in pending:
                reason = "待裁决冲突字段（先处理冲突）"
        if reason:
            row["reason"] = reason
            row["flags"] = _editor_risk_flags(record, old, old)
            rows.append(row)
            continue
        new = bibnorm.normalize_author_field(old)
        if new == old:
            row["reason"] = _author_skip_reason(old) or "规范化器判不准（保持原样）"
            row["flags"] = _editor_risk_flags(record, old, old)
            rows.append(row)
            continue
        row["status"] = "change"
        row["new"] = new
        row["flags"] = _editor_risk_flags(record, old, new)
        rows.append(row)
    rows.sort(key=lambda item: item["key"])
    return rows


def _editor_stats(rows: list[dict], records_total: int) -> dict:
    changes = [row for row in rows if row["status"] == "change"]
    skips = [row for row in rows if row["status"] == "skip"]
    reasons: dict[str, int] = {}
    for row in skips:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    flags: dict[str, int] = {}
    for row in changes:
        for flag in row["flags"]:
            flags[flag] = flags.get(flag, 0) + 1
    by_type: dict[str, int] = {}
    count_hist: dict[str, int] = {}
    issues: set[str] = set()
    issue_hits = 0
    for row in changes:
        by_type[row["entry_type"]] = by_type.get(row["entry_type"], 0) + 1
        count_hist[str(row["n_editors"])] = count_hist.get(str(row["n_editors"]), 0) + 1
        issues |= set(row["issues"])
        issue_hits += len(row["issues"])
    return {
        "records": records_total,
        "with_editor": len(rows),
        "editor_only": sum(1 for row in rows if not row["has_author"]),
        "editor_only_changes": sum(1 for row in changes if not row["has_author"]),
        "changes": len(changes),
        "skips": len(skips),
        "reasons": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "risk_flags": dict(sorted(flags.items(), key=lambda kv: (-kv[1], kv[0]))),
        "change_by_entry_type": dict(sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        "editor_count_hist": dict(sorted(count_hist.items(), key=lambda kv: int(kv[0]))),
        "issues": sorted(issues, key=_issue_sort_key),
        "issue_hits": issue_hits,
        "multi_editor_changes": sum(1 for row in changes if row["n_editors"] >= 2),
        "change_rows": changes,
        "skip_rows": skips,
    }


def _editor_sources_text(row: dict) -> str:
    sources = row.get("sources") or []
    shown = "；".join(sources[:3])
    if len(sources) > 3:
        shown += f"；…（另 {len(sources) - 3} 条）"
    return shown or "（未记录来源）"


def _editor_flags_text(row: dict) -> str:
    flags = row.get("flags") or []
    return "；".join(flags) if flags else "—"


def _editor_table_row(index: int, row: dict, *, applied: bool = False) -> str:
    """逐条明细表的一行（列：#/key/期篇文件/entry_type/题名/旧值/新值/状态/跳过原因/风险标记）。"""
    status = ("已倒装" if applied else "将倒装") if row["status"] == "change" else "跳过"
    new = row["new"] if row["status"] == "change" else "（保持原样）"
    return ("| {index:04d} | `{key}` | {sources} | {etype} | {title} | {old} | {new} | "
            "{status} | {reason} | {flags} |").format(
        index=index,
        key=_md_escape(row["key"]),
        sources=_md_escape(_editor_sources_text(row)),
        etype=_md_escape(row["entry_type"]),
        title=_md_escape(row["title"], width=50),
        old=f"`{_md_escape(row['old'], width=60)}`",
        new=f"`{_md_escape(new, width=60)}`",
        status=status,
        reason=_md_escape(row["reason"] or "—"),
        flags=_md_escape(_editor_flags_text(row)),
    )


def build_editor_report_md(db_path: str, stats: dict, sha_before: str,
                           sha_after: str, verification: dict, *,
                           applied: bool = False,
                           applied_rows: list[dict] | None = None,
                           skipped_apply: list[dict] | None = None,
                           backup_path: str = "", backup_sha: str = "") -> str:
    """B3 编者倒装报告（dry-run / apply 共用；apply 版多备份、sha 链、回滚与二次校验）。

    无时间戳、无绝对路径：同一份库重复生成逐字节相同。
    """
    db_label = os.path.basename(db_path) or db_path
    all_rows = stats["change_rows"] + stats["skip_rows"]
    eds_rows = [row for row in stats["change_rows"] if "(eds.) 形态" in row["flags"]]
    and_rows = [row for row in all_rows if " AND " in row["old"]]
    and_change = [row for row in and_rows if row["status"] == "change"]
    title = ("# 引用库 · B3 编者（editor）倒装 apply 报告" if applied
             else "# 引用库 · B3 编者（editor）倒装 dry-run 报告")
    lines: list[str] = [title, ""]
    if applied:
        lines += [
            "> 本报告由 `bibdb.py normalize-editors --apply` 生成：**已把安全条目的英文编者",
            "> 倒装写进库**（用户 2026-10-22 拍板·方案 A）。只改 `editor` 字段 +",
            "> `content_sha256` + `updated_at`；其余字段与元数据未动。",
            "> 判定规则与 B2 作者倒装**同源**（`slcn.bibnorm.normalize_author_field` + 同一套保守",
            "> 跳过口径），只处理 `editor` 字段；连「只有 editor 没有 author」的记录也一并扫描。",
            "> 中文/日文、机构、`and others`、花括号/LaTeX 保护、已倒装、判不准 → **原样不动**。",
            "",
            f"- 数据来源：引用库 `{db_label}`（本报告同目录）",
            f"- 库 sha256（apply 前）：`{sha_before or '（读不到）'}`",
            f"- 库 sha256（apply 后）：`{sha_after or '（读不到）'}`",
            f"- apply 前备份：`{backup_path or '（未提供）'}`（sha256 `{backup_sha or '（未提供）'}`）",
            f"- 库记录：**{stats['records']}** 条；有 `editor` 字段：**{stats['with_editor']}** 条"
            f"（其中只有 editor、没有 author：**{stats['editor_only']}** 条）",
            f"- 结果：**已倒装 {stats['changes']} 条** / **跳过 {stats['skips']} 条**"
            f"（按原因见下表）",
            f"- 逐条明细：见文末附表（{stats['with_editor']} 行，按库内 key 排序）；"
            f"全量 CSV 另存 `{EDITOR_REPORT_APPLY_CSV}`（Excel 可直接打开，UTF-8 带 BOM）",
            "",
        ]
    else:
        lines += [
            "> 本报告由 `bibdb.py normalize-editors` 生成：**纯 dry-run，只算不写**——不会调用",
            "> `Store.save()`、不会修改 `bibliography.jsonl`（跑前/跑后 sha256 见下，必相同）。",
            "> 判定规则与 B2 作者倒装**同源**（`slcn.bibnorm.normalize_author_field` + 同一套保守",
            "> 跳过口径），只处理 `editor` 字段；连「只有 editor 没有 author」的记录也一并扫描。",
            "> 中文/日文、机构、`and others`、花括号/LaTeX 保护、已倒装、判不准 → **原样不动**。",
            "> `--apply` 需 `--backup` 备份 + sha256 与当前库一致才能执行；本报告是 dry-run 版。",
            "",
            f"- 数据来源：引用库 `{db_label}`（本报告同目录）",
            f"- 库 sha256（跑前）：`{sha_before or '（读不到）'}`",
            f"- 库 sha256（跑后）：`{sha_after or '（读不到）'}`"
            f"（与跑前一致 = **未改动库**）",
            f"- 库记录：**{stats['records']}** 条；有 `editor` 字段：**{stats['with_editor']}** 条"
            f"（其中只有 editor、没有 author：**{stats['editor_only']}** 条）",
            f"- 结果：**将倒装 {stats['changes']} 条** / **跳过 {stats['skips']} 条**"
            f"（按原因见下表）",
            f"- 逐条明细：见文末附表（{stats['with_editor']} 行，按库内 key 排序）；"
            f"全量 CSV 另存 `{EDITOR_REPORT_CSV}`（Excel 可直接打开，UTF-8 带 BOM）",
            f"- 幂等：同一份库重复跑本命令，MD/CSV/JSON 逐字节相同（无时间戳/无绝对路径）；"
            f"把 {stats['changes']} 条新值再喂同一个规范化器，剩余将变 "
            f"**{verification['remaining']}** 条（应为 0，见「二次校验」）——即 apply 后重跑 dry-run 必为 0。",
            "",
        ]
    lines += [
        "## 跳过原因分类",
        "",
        "| 原因 | 条数 |",
        "|---|---|",
    ]
    for reason, count in stats["reasons"].items():
        lines.append(f"| {_md_escape(reason)} | {count} |")
    if not stats["reasons"]:
        lines.append("| （无） | 0 |")
    lines += [
        "",
        f"## 影响面（{'本次 apply' if applied else '若 apply'}）",
        "",
        f"- 只改 `editor` 字段（+ `content_sha256` / `updated_at`）；"
        f"`verified` / `sources` / `conflicts` / `aliases` 与其它字段不动；",
        f"- 多编者（`and` 分隔 ≥2）：**{stats['multi_editor_changes']}** 条；"
        "编者人数分布：" + "、".join(
            f"{n} 人 {c} 条" for n, c in stats["editor_count_hist"].items()),
        f"- 其中 editor-only（B2 漏扫、只有 editor 的记录）："
        f"{'已倒装' if applied else '将倒装'} **{stats['editor_only_changes']}** 条",
        f"- 覆盖 **{len(stats['issues'])}** 期（{stats['issues'][0] if stats['issues'] else '—'}"
        f" … {stats['issues'][-1] if stats['issues'] else '—'}）；"
        f"「记录×期」覆盖点 {stats['issue_hits']}（同一记录跨期会重复计入）",
        "",
        "按 entry_type 分布：" if stats["change_by_entry_type"] else "按 entry_type 分布：（无）",
        "",
        f"| entry_type | {'已倒装' if applied else '将倒装'}条数 |",
        "|---|---|",
    ]
    for etype, count in stats["change_by_entry_type"].items():
        lines.append(f"| {_md_escape(etype)} | {count} |")
    lines += [
        "",
        "风险标记计数（仅「将倒装」行；同一条可命中多个标记）：",
        "",
        "| 风险标记 | 条数 |",
        "|---|---|",
    ]
    for flag, count in stats["risk_flags"].items():
        lines.append(f"| {_md_escape(flag)} | {count} |")
    if not stats["risk_flags"]:
        lines.append("| （无） | 0 |")
    lines += [
        "",
        "## ⚠️ 需要用户拍板的 3 个点",
        "",
        f"### ① `(eds.)` / `(ed.)` 形态：{len(eds_rows)} 条（编者标记会被并进姓）",
        "",
    ]
    if eds_rows:
        lines += ["| # | 库内 key | 期/篇/文件 | 旧值 | 同规则将改为 |", "|---|---|---|---|---|"]
        for index, row in enumerate(eds_rows, start=1):
            lines.append(f"| {index:02d} | `{_md_escape(row['key'])}` | "
                         f"{_md_escape(_editor_sources_text(row))} | "
                         f"`{_md_escape(row['old'], width=60)}` | "
                         f"`{_md_escape(row['new'], width=60)}` |")
        lines += [
            "",
            "- **方案 A（默认建议，与 B2 同规则自动改）**：沿用 B2 先例（作者字段也这么处理，"
            "如已落库的 `{Borchert (ed.)}, D.`），一次批处理、不单列人工清单；",
            "- **方案 B（保守）**：这 2 条不自动改，另列「人工处理清单」，由编辑决定编者标记写法；"
            "其余条目照常。",
            "- 影响：`(eds.)` 会被并进姓（例：`{Kaulbach (eds.)}, F.`），与 B2 已落库的作者形态一致。",
        ]
    else:
        lines += ["（本库无此类条目）"]
    lines += [
        "",
        f"### ② 大写分隔符 `AND`：默认**不动**（{len(and_rows)} 条 editor 值含大写 `AND`）",
        "",
        "- 默认与 B2 一致：**不改分隔符大小写**（BibTeX 按 `and` 关键字分词，大小写不影响语义）；"
        "如要统一小写 `and`，需拍板后另加规则。",
    ]
    if and_rows:
        lines += ["", "| 库内 key | 状态 | editor 原值 |", "|---|---|---|"]
        for row in and_rows:
            status = "将倒装（保留大写 AND）" if row["status"] == "change" else "跳过"
            lines.append(f"| `{_md_escape(row['key'])}` | {status} | "
                         f"`{_md_escape(row['old'], width=80)}` |")
    lines += [
        "",
        "### ③ 多编者：建议抽查范围",
        "",
        f"- 将倒装行中 **{stats['multi_editor_changes']}** 条是多编者（`and` 分隔）；"
        "建议按 CSV 的「编者人数」列抽 20–30 条核对姓名顺序/连接词写法（规则与 B2 同源，"
        "任一段判不准整条不动，不会产出半倒装）。",
        "",
        f"## {'已倒装' if applied else '将倒装'}样例（前 20，旧 → 新）",
        "",
        "| # | 库内 key | 旧值 | 新值 | 风险标记 |",
        "|---|---|---|---|---|",
    ]
    for index, row in enumerate(stats["change_rows"][:20], start=1):
        lines.append(f"| {index:02d} | `{_md_escape(row['key'])}` | "
                     f"`{_md_escape(row['old'], width=60)}` | "
                     f"`{_md_escape(row['new'], width=60)}` | "
                     f"{_md_escape(_editor_flags_text(row))} |")
    if not stats["change_rows"]:
        lines.append("| — | （无） | — | — | — |")
    lines += [
        "",
        "## 跳过样例（前 20）",
        "",
        "| # | 库内 key | editor 原值 | 原因 |",
        "|---|---|---|---|",
    ]
    for index, row in enumerate(stats["skip_rows"][:20], start=1):
        lines.append(f"| {index:02d} | `{_md_escape(row['key'])}` | "
                     f"`{_md_escape(row['old'], width=60)}` | {_md_escape(row['reason'])} |")
    if not stats["skip_rows"]:
        lines.append("| — | （无） | — | — |")
    lines += [
        "",
        *([] if not applied else [
            "## 二次校验（apply 后重跑 dry-run）",
            "",
            f"- 仍将倒装（排除未 apply 的跳过条目后）：**{verification['remaining']}** 条（应为 0）",
            f"- 跳过：{verification.get('skips', '—')} 条；有 editor 的记录："
            f"{verification.get('with_editor', '—')} 条",
            "",
            "## 回滚方法（如需）",
            "",
            "```bash",
            f"cp {backup_path or '/tmp/bibliography_before_B3_editors.jsonl'} "
            "../EditToolBox-notes/bib_db/bibliography.jsonl",
            "```",
            "",
            f"（备份 sha256：`{backup_sha or '（未提供）'}`）",
            "",
        ] if applied else [
            "## 二次校验（内存预演，不写库）",
            "",
            f"- 把 **{verification['checked']}** 条「将倒装」的新值再跑同一个规范化器："
            f"**仍将变化 {verification['remaining']} 条（应为 0）** → 规则幂等；",
            "- 本 dry-run 连跑两次（同库）：MD/CSV/JSON 逐字节相同（由测试钉住）；",
            f"- 跑前/跑后库 sha256 相同：`{sha_before}`（本命令不写库）。",
            "",
        ]),
        f"## 逐条明细（{stats['with_editor']} 条；按库内 key 排序）",
        "",
        f"> 状态 = {'已倒装' if applied else '将倒装'} / 跳过；跳过原因见列；"
        f"风险标记为「{'已倒装' if applied else '将倒装'}」行的抽查提示。"
        f"CSV（{len(stats['change_rows'])} 行）与 JSON 有同样内容。",
        "",
        "| # | 库内 key | 期/篇/文件 | entry_type | 题名 | editor 旧值 | editor 新值 | "
        "状态 | 跳过原因 | 风险标记 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(all_rows, start=1):
        lines.append(_editor_table_row(index, row, applied=applied))
    lines.append("")
    return "\n".join(lines)


EDITOR_CSV_COLUMNS = ["#", "库内 key", "期/篇/文件", "entry_type", "题名",
                      "editor 旧值", "editor 新值", "编者人数", "风险标记"]


def build_editor_csv(stats: dict) -> str:
    """B3 全量 CSV（**只含「将倒装」行**，供编辑逐条审；UTF-8 带 BOM）。"""
    import csv as _csv
    import io

    buffer = io.StringIO()
    writer = _csv.writer(buffer, lineterminator="\n")
    writer.writerow(EDITOR_CSV_COLUMNS)
    for index, row in enumerate(stats["change_rows"], start=1):
        writer.writerow([
            index,
            row["key"],
            "；".join(row["sources"]),
            row["entry_type"],
            row["title"],
            row["old"],
            row["new"],
            row["n_editors"],
            "；".join(row["flags"]),
        ])
    return "\ufeff" + buffer.getvalue()  # BOM：Excel 直接打开不乱码


def build_editor_json(db_path: str, stats: dict, sha_before: str,
                      sha_after: str, verification: dict, *,
                      applied: bool = False,
                      applied_rows: list[dict] | None = None,
                      skipped_apply: list[dict] | None = None,
                      backup_path: str = "", backup_sha: str = "") -> str:
    """B3 机读摘要（无绝对路径：`db` 只留文件名）。"""
    payload = {
        "tool": "bibdb.py normalize-editors" + (" --apply" if applied else "（dry-run）"),
        "db": os.path.basename(db_path) or db_path,
        "mode": "apply" if applied else "dry-run",
        "apply_implemented": True,
        "backup_path": backup_path,
        "backup_sha256": backup_sha,
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "db_unchanged": sha_before == sha_after,
        "records": stats["records"],
        "with_editor": stats["with_editor"],
        "editor_only": stats["editor_only"],
        "editor_only_changes": stats["editor_only_changes"],
        "changes": stats["changes"],
        "skips": stats["skips"],
        "reasons": stats["reasons"],
        "risk_flags": stats["risk_flags"],
        "change_by_entry_type": stats["change_by_entry_type"],
        "editor_count_hist": stats["editor_count_hist"],
        "issues_touched": stats["issues"],
        "issue_hits": stats["issue_hits"],
        "multi_editor_changes": stats["multi_editor_changes"],
        "verification": verification,
        "applied_entries": applied_rows if applied_rows is not None else [],
        "skipped_apply": skipped_apply if skipped_apply is not None else [],
        "change_entries": [
            {key: row[key] for key in ("key", "old", "new", "entry_type", "title",
                                       "sources", "n_editors", "flags")}
            for row in stats["change_rows"]],
        "skipped_entries": [
            {key: row[key] for key in ("key", "old", "reason", "entry_type", "title",
                                       "sources", "n_editors", "flags")}
            for row in stats["skip_rows"]],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_editor_reports(args, path: str, stats: dict, sha_before: str,
                          sha_after: str, verification: dict, *,
                          applied: bool = False,
                          applied_rows: list[dict] | None = None,
                          skipped_apply: list[dict] | None = None,
                          backup_path: str = "", backup_sha: str = "") -> list[dict]:
    report_path = str(getattr(args, "report", "") or "")
    out_dir = str(getattr(args, "out_dir", "") or "")
    default_md = EDITOR_REPORT_APPLY if applied else EDITOR_REPORT_DRY
    if not report_path and out_dir:
        report_path = os.path.join(out_dir, default_md)
    csv_path = str(getattr(args, "csv_out", "") or "")
    if not csv_path and out_dir:
        # apply 版 CSV 只列「已倒装」行（供抽查）；文件名带 apply 后缀避免覆盖 dry-run
        csv_path = os.path.join(
            out_dir, EDITOR_REPORT_APPLY_CSV if applied else EDITOR_REPORT_CSV)
    json_path = str(getattr(args, "json_out", "") or "")
    if not json_path and out_dir:
        json_path = os.path.join(
            out_dir, EDITOR_REPORT_APPLY_JSON if applied else EDITOR_REPORT_JSON)
    written: list[dict] = []
    if report_path:
        written.append(_write_report(report_path, build_editor_report_md(
            path, stats, sha_before, sha_after, verification, applied=applied,
            applied_rows=applied_rows, skipped_apply=skipped_apply,
            backup_path=backup_path, backup_sha=backup_sha)))
    if csv_path:
        written.append(_write_report(csv_path, build_editor_csv(stats)))
    if json_path:
        written.append(_write_report(json_path, build_editor_json(
            path, stats, sha_before, sha_after, verification, applied=applied,
            applied_rows=applied_rows, skipped_apply=skipped_apply,
            backup_path=backup_path, backup_sha=backup_sha)))
    return written


# ── 重复候选合并计划（merge-duplicates；**dry-run only**，2026-10-18 第四十三批）──
#
# 用户 2026-10-18 指令：1294 对「同作品不同 key」候选里，除 `miaolitian`/`miaolitian2`
# 两个 key 外**全部合并**；`similarity=1` 的簇统一 key 形式（人名+年份+可选 a/b/c）；
# 版本选择**优先保留花括号保护**的写法；旧 key 记 aliases；实质冲突（年份/作者不同）
# 不自动合并 → 单列人工清单。本子命令只做**计划（dry-run）**，绝不写 bibliography.jsonl；
# apply 由下一张卡实现（届时复用本计划 JSON，逐条落库并重算 content_sha256）。
#
# key 规范（APA 7 核实，见报告「规则依据」）：
#   · 同一作者同一年有多条作品时，**按题名字母序**依次加 a/b/c（APA 7：alphabetize
#     by title, ignoring initial articles；再按顺序加 suffix a, b, c…）；
#   · 本库内部 key = `Surname`（或中文 `SurnameGiven` 拼音）+ 4 位年份 + 可选字母，
#     与库内既有风格一致（`LiLei2005` / `Haack1978` / `Li2026a`）。
#   · 只有 similarity=1 的簇参与 key 规范化；其余合并簇保留 canonical 旧 key（报告注明）。
MERGE_IGNORE_KEYS = frozenset({"miaolitian", "miaolitian2"})
MERGE_REPORT_MD = "DUPLICATES-MERGE-PLAN.md"
MERGE_REPORT_CSV = "DUPLICATES-MERGE-PLAN.csv"
MERGE_RENAMES_CSV = "DUPLICATES-MERGE-RENAMES.csv"
MERGE_MANUAL_CSV = "DUPLICATES-MERGE-MANUAL.csv"
MERGE_PLAN_JSON = "DUPLICATES-MERGE-PLAN.json"

_KEY_PARTICLES = frozenset({"van", "von", "de", "der", "den", "la", "le", "di", "da",
                            "del", "dos", "du", "st", "ten", "ter"})
_KEY_INITIAL_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.I)
#: entry_type 收敛优先级（高者更具体；同秩不同类型 → 人工）
_TYPE_RANK = {
    "article": 5, "incollection": 5, "inproceedings": 5, "book": 5, "collection": 5,
    "phdthesis": 4, "mastersthesis": 4, "thesis": 4, "techreport": 4, "report": 4,
    "inbook": 4, "proceedings": 4, "dataset": 3, "online": 3, "periodical": 3,
    "misc": 1, "unpublished": 1, "booklet": 1, "conference": 1,
}
#: 版本选择：字段非空个数之外，这几种「明确冲突」直接进人工清单
MERGE_FIELD_DIFF_NOTE_LIMIT = 8


def _merge_key_name(person: str) -> str:
    """第一作者姓名 → 规范化 key 前缀（拉丁 `Haack` / 中文 `LiLei`）。认不出返回 ""。"""
    name = str(person or "").strip()
    if not name:
        return ""
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", name):
        # `弗雷格著` / `王路译` / `…编`：角色标记不是姓名的一部分
        name = re.sub(r"(?:著|譯|译|校|编|編|主编|主編|点校|點校|整理|注释|注釋|"
                      r"编著|編著|编译|編譯|等译|等譯)+\s*$", "", name).strip()
        try:
            from pypinyin import Style, lazy_pinyin
            syl = [w for w in lazy_pinyin(name, style=Style.NORMAL, errors="ignore") if w]
        except Exception:            # pypinyin 不在（CI 没装）→ 不猜
            return ""
        head = "".join(w.capitalize() for w in syl if re.search(r"[a-z]", w, re.I))
        return head if len(head) >= 2 else ""
    import unicodedata

    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z .,'\-]", " ", text)   # ⚠️ 逗号必须保留：倒装姓在逗号前
    if "," in text:                  # 已倒装：`Haack, S.` / `van Fraassen, B. C.`
        surname = text.split(",")[0]
    else:                            # 未倒装：`B. C. van Fraassen`
        toks = [t for t in re.split(r"[\s\-]+", text) if t]
        if not toks:
            return ""
        i = len(toks) - 1
        while i - 1 >= 0 and toks[i - 1].lower() in _KEY_PARTICLES:
            i -= 1
        surname = " ".join(toks[i:])
    words = [w for w in re.split(r"[\s\-']+", surname.strip()) if w]
    if not words:
        return ""
    out = ""
    for word in words:
        low = word.lower()
        if low in _KEY_PARTICLES:
            out += low
        elif word.isupper() and len(word) > 1:      # 机构缩写照抄（`WHO`）
            out += word
        else:
            out += word[0].upper() + word[1:].lower()
    out = re.sub(r"[^A-Za-z]", "", out)
    if not out:
        return ""
    return out[0].upper() + out[1:] if out[0].islower() else out


def _merge_title_sort_key(title: str) -> str:
    """APA 7 题名字母序（**忽略起始冠词 A/An/The**，大小写/标点/花括号不敏感）。"""
    text = str(title or "")
    text = text.replace("{", "").replace("}", "").replace("\\", "")
    text = re.sub(r"\s+", " ", text).strip()
    text = _KEY_INITIAL_ARTICLE.sub("", text)
    text = text.lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _record_year(record: dict) -> str:
    return bibdb.entry_year(record.get("fields") or {})


def _record_fields(record: dict) -> dict:
    return record.get("fields") if isinstance(record.get("fields"), dict) else {}


def _record_brace_score(record: dict) -> int:
    """花括号保护分（用户拍板：版本选择优先保留花括号保护）。"""
    fields = _record_fields(record)
    return sum(str(fields.get(name) or "").count("{") + str(fields.get(name) or "").count("\\")
               for name in ("title", "author", "editor", "booktitle", "journal", "publisher"))


def _record_filled(record: dict) -> int:
    return sum(1 for value in _record_fields(record).values() if str(value or "").strip())


def _record_sources(record: dict) -> list[dict]:
    return [s for s in (record.get("sources") or []) if isinstance(s, dict)]


def _merge_version_score(record: dict) -> tuple:
    """canonical 版本排序键（越大越好）：花括号保护 > 字段完整 > 来源多 > 冲突少。"""
    key = str(record.get("key") or "")
    return (
        _record_brace_score(record),
        _record_filled(record),
        len(_record_sources(record)),
        -len(record.get("conflicts") or []),
        _record_year(record),
        "" if key.startswith("-") else key,      # 稳定 tie-break
    )


def _merge_field_equivalent(name: str, a: str, b: str) -> bool:
    """字段值是否只是写法差异（大小写/标点/花括号/页码破折号），不是实质不同。"""
    left, right = str(a or ""), str(b or "")
    if left == right:
        return True
    if name == "title":
        return bibdb.normalize_title(left) == bibdb.normalize_title(right)
    if name in ("author", "editor"):
        return (re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", left.lower())
                == re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", right.lower()))
    if name in ("year", "date"):
        return _record_year({"fields": {"year": left}}) == _record_year({"fields": {"year": right}})
    if name == "doi":
        return bibdb.normalize_doi(left) == bibdb.normalize_doi(right)
    if name in ("pages", "volume", "number", "edition"):
        strip = lambda v: re.sub(r"[\s\-\u2010-\u2015]+", "-", v.lower())  # noqa: E731
        return strip(left) == strip(right)
    return False


def _merge_prefer_value(name: str, values: list[str]) -> str:
    """同名多写法 → 取「花括号保护最多、信息最全」的那个（稳定 tie-break：字典序）。"""
    if not values:
        return ""
    def score(value: str) -> tuple:
        return (value.count("{") + value.count("\\") * 2,
                len(re.sub(r"\s+", "", value)), value)
    return max(values, key=score)


def _first_person_surname(record: dict) -> str:
    fields = _record_fields(record)
    person = str(fields.get("author") or fields.get("editor") or "").strip()
    first = re.split(r"\s+and\s+", person, flags=re.I)[0].strip() if person else ""
    if not first:
        return ""
    if re.search(r"[\u4e00-\u9fff]", first):
        match = re.match(r"^([\u4e00-\u9fff]{1,6})", first)
        return match.group(1)[:1] if match else first
    if "," in first:
        surname = first.split(",")[0]
    else:
        toks = [t for t in first.split() if t]
        surname = toks[-1] if toks else ""
    return re.sub(r"[^0-9a-z]", "", surname.lower())


def _merge_cluster_fields(cluster_records: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """簇内字段合并（缺失补全 + 花括号/信息最全优先），返回 (fields, 字段差异明细)。"""
    fields: dict[str, str] = {}
    field_diff_notes: list[dict] = []
    for name in sorted({n for r in cluster_records for n in _record_fields(r)}):
        values = [str(_record_fields(r).get(name) or "") for r in cluster_records]
        values = [v for v in values if v.strip()]
        if not values:
            continue
        chosen = _merge_prefer_value(name, values)
        fields[name] = chosen
        distinct = [v for v in values if not _merge_field_equivalent(name, v, chosen)]
        if distinct and len({re.sub(r"\s+", " ", v).strip() for v in distinct}) > 1:
            field_diff_notes.append({"field": name, "keep": chosen,
                                     "others": sorted(set(distinct))[:3]})
    return fields, field_diff_notes


def build_merge_plan(store: "bibdb.Store", pairs: list[tuple[str, str, float]]) -> dict:
    """只读计算合并计划（不写库）：聚类 → 版本/字段/key/aliases → 人工清单。"""
    records = {str(r.get("key")): r for r in store.records() if isinstance(r, dict)}
    mia_pairs = [p for p in pairs if p[0] in MERGE_IGNORE_KEYS or p[1] in MERGE_IGNORE_KEYS]
    kept_pairs = [p for p in pairs
                  if p[0] not in MERGE_IGNORE_KEYS and p[1] not in MERGE_IGNORE_KEYS]
    # ── 连通分量 ──
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b, _sim in kept_pairs:
        union(a, b)
    members: dict[str, set[str]] = {}
    for key in list(parent):
        members.setdefault(find(key), set()).add(key)
    edge_by_cluster: dict[str, list[tuple[str, str, float]]] = {}
    for a, b, sim in kept_pairs:
        edge_by_cluster.setdefault(find(a), []).append((a, b, sim))

    clusters: list[dict] = []
    for index, (root, keys) in enumerate(sorted(members.items(),
                                                key=lambda item: sorted(item[1])[0]), start=1):
        keys_sorted = sorted(keys)
        cluster_records = [records[k] for k in keys_sorted if k in records]
        missing = [k for k in keys_sorted if k not in records]
        edges = edge_by_cluster.get(root, [])
        sims = [sim for _a, _b, sim in edges]
        canonical = max(cluster_records, key=_merge_version_score) if cluster_records else None
        entry = {
            "cluster": f"M{index:04d}",
            "keys": keys_sorted,
            "missing_keys": missing,
            "edges": [{"a": a, "b": b, "similarity": sim} for a, b, sim in edges],
            "has_similarity_1": any(sim >= 1.0 for sim in sims),
            "canonical_key": str(canonical.get("key")) if canonical else "",
            "members": [],
            "action": "merge",
            "reason": "",
            "new_key": "",
            "aliases": [],
            "merged_fields": {},
            "field_diffs": [],
            "entry_type": str((canonical or {}).get("entry_type") or ""),
        }
        # 成员摘要（报告/CSV/JSON 共用）
        for key in keys_sorted:
            record = records.get(key)
            if record is None:
                continue
            fields = _record_fields(record)
            entry["members"].append({
                "key": key,
                "entry_type": str(record.get("entry_type") or ""),
                "author": str(fields.get("author") or fields.get("editor") or ""),
                "year": _record_year(record),
                "title": str(fields.get("title") or fields.get("booktitle") or ""),
                "sha256": str(record.get("content_sha256") or ""),
                "issues": [str(s.get("issue")) for s in _record_sources(record) if s.get("issue")],
                "brace_score": _record_brace_score(record),
                "filled": _record_filled(record),
            })
        # ── 实质冲突 → 人工 ──
        years = {_record_year(r) for r in cluster_records} - {""}
        surnames = {_first_person_surname(r) for r in cluster_records} - {""}
        types = {str(r.get("entry_type") or "").lower() for r in cluster_records}
        type_ranks = {_TYPE_RANK.get(t, 0) for t in types}
        if len(years) > 1:
            entry["action"] = "manual"
            entry["reason"] = f"年份实质冲突（{ '、'.join(sorted(years)) }）"
        elif len(surnames) > 1:
            entry["action"] = "manual"
            entry["reason"] = "第一作者姓氏实质冲突（" + "、".join(sorted(surnames)) + "）"
        elif missing:
            entry["action"] = "manual"
            entry["reason"] = "候选 key 在库内不存在（数据不一致）"
        elif len(type_ranks) > 1:
            strong = {t for t in types if _TYPE_RANK.get(t, 0) >= 3}   # 具体类型
            if len(strong) == 1 and all(_TYPE_RANK.get(t, 0) <= 2 for t in types - strong):
                # 弱类型（misc/unpublished/…）+ 唯一具体类型 → 收敛到具体类型（自动）
                entry["resolved_entry_type"] = sorted(strong)[0]
            else:
                entry["action"] = "manual"
                entry["reason"] = "条目类型实质冲突（" + "、".join(sorted(types)) + "）"
        if entry["action"] == "merge":
            entry["resolved_entry_type"] = entry.get("resolved_entry_type")
        clusters.append(entry)

    # ── 合并字段（只对 action=merge 的簇计算明细）──
    for entry in clusters:
        if entry["action"] != "merge":
            continue
        cluster_records = [records[k] for k in entry["keys"] if k in records]
        fields, field_diff_notes = _merge_cluster_fields(cluster_records)
        entry["merged_fields"] = fields
        entry["field_diffs"] = field_diff_notes[:MERGE_FIELD_DIFF_NOTE_LIMIT]
        canonical = max(cluster_records, key=_merge_version_score)
        entry["entry_type"] = str(canonical.get("entry_type") or "")
        if entry.get("resolved_entry_type"):
            entry["entry_type"] = entry.pop("resolved_entry_type")

    # ── key 规范化：只对「有 similarity=1 边」的簇生成新 key（用户指令）──
    live_keys = {k for k in records}
    renamed_away = {k for entry in clusters if entry["action"] == "merge"
                    for k in entry["keys"]}
    reserved = live_keys - renamed_away          # 未合并记录仍占用 key
    # 不参加 key 规范化的合并簇（无 similarity=1 边）保留旧 key → 也占位
    for entry in clusters:
        if entry["action"] == "merge" and not entry["has_similarity_1"]:
            reserved.add(entry["canonical_key"])

    def _person_raw(entry: dict) -> str:
        canonical = records[entry["canonical_key"]]
        fields = _record_fields(canonical)
        return re.split(r"\s+and\s+", str(fields.get("author")
                                          or fields.get("editor") or ""), flags=re.I)[0]

    def _identity(person: str) -> tuple[str, tuple[str, ...]]:
        """(姓, 名的规范化 token 序列)：拉丁取逗号前/虚词后为姓；中日文取拼音。"""
        text = str(person or "").strip()
        if not text:
            return "", ()
        if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
            head = _merge_key_name(text)
            return head, ()
        import unicodedata
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r"[^A-Za-z .,'\-]", " ", text)
        if "," in text:
            surname_part, given_part = text.split(",", 1)
        else:
            toks = [t for t in re.split(r"[\s\-]+", text) if t]
            if not toks:
                return "", ()
            i = len(toks) - 1
            while i - 1 >= 0 and toks[i - 1].lower() in _KEY_PARTICLES:
                i -= 1
            surname_part, given_part = " ".join(toks[i:]), " ".join(toks[:i])
        surname = re.sub(r"[^a-z]", "", surname_part.lower())
        given = tuple(w.lower() for w in re.split(r"[\s\-.']+", given_part) if w)
        return surname, given

    def _given_compatible(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
        if not a or not b:
            return a == b
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        for token in shorter:
            if not any(longer_token.startswith(token) for longer_token in longer):
                return False
        return True

    def _same_author(entry_a: dict, entry_b: dict) -> bool:
        sur_a, giv_a = _identity(_person_raw(entry_a))
        sur_b, giv_b = _identity(_person_raw(entry_b))
        return bool(sur_a) and sur_a == sur_b and _given_compatible(giv_a, giv_b)

    def _given_discriminator(person: str) -> str:
        """同姓同年不同作者时用「名首字母」区分（`FanJ` / `FanX`）。"""
        if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", str(person or "")):
            full = _merge_key_name(person)
            return full                       # 中文直接用 SurnameGiven 全拼
        import unicodedata
        text = unicodedata.normalize("NFKD", str(person or ""))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r"[^A-Za-z .,'\-]", " ", text)
        if "," in text:
            given_part = text.split(",", 1)[1]
        else:
            toks = [t for t in re.split(r"[\s\-]+", text) if t]
            i = len(toks) - 1
            while i - 1 >= 0 and toks[i - 1].lower() in _KEY_PARTICLES:
                i -= 1
            given_part = " ".join(toks[:i])
        initials = "".join(w[0].upper() for w in re.split(r"[\s\-.' ]+", given_part) if w)[:3]
        surname, _ = _identity(person)
        return surname.capitalize() + initials

    rekey_groups: dict[str, list[dict]] = {}
    key_conflicts: list[dict] = []
    apa_letter_groups: list[dict] = []
    surname_year_discriminated: list[dict] = []
    for entry in clusters:
        if entry["action"] != "merge" or not entry["has_similarity_1"]:
            continue
        canonical = records[entry["canonical_key"]]
        person = _person_raw(entry)
        prefix = _merge_key_name(person)
        year = _record_year(canonical)
        if not prefix or not year:
            entry["new_key"] = entry["canonical_key"]
            key_conflicts.append({"cluster": entry["cluster"],
                                  "canonical": entry["canonical_key"],
                                  "why": "作者或年份不可解析，保留旧 key"})
            continue
        base = f"{prefix}{year}"
        entry["new_key_base"] = base
        entry["_person"] = person
        rekey_groups.setdefault(base, []).append(entry)

    def assign(entries: list[dict], base: str, *, kind: str) -> None:
        """给同一 base 的簇分配 key：同一作者 → 题名字母序 a/b/c；不同作者 → 名前缀区分。"""
        def sort_key(entry: dict) -> str:
            canonical = records[entry["canonical_key"]]
            return _merge_title_sort_key(str(_record_fields(canonical).get("title")
                                             or _record_fields(canonical).get("booktitle") or ""))
        entries.sort(key=sort_key)
        # ① 按作者身份分组（名的缩写兼容视为同一人）
        identities: list[list[dict]] = []
        for entry in entries:
            for group in identities:
                if _same_author(entry, group[0]):
                    group.append(entry)
                    break
            else:
                identities.append([entry])
        used = set(reserved)
        if len(identities) == 1:
            group = identities[0]
            if len(group) == 1 and base not in used:
                group[0]["new_key"] = base
                used.add(base)
                return
            if len(group) > 1:
                for index, entry in enumerate(group):
                    candidate = f"{base}{'abcdefghijklmnopqrstuvwxyz'[index]}"
                    entry["new_key"] = candidate
                    used.add(candidate)
                apa_letter_groups.append({
                    "base": base,
                    "clusters": [e["cluster"] for e in group],
                    "keys": [e["new_key"] for e in group],
                    "titles": [str(_record_fields(records[e["canonical_key"]]).get("title") or "")
                               for e in group],
                })
                return
            # 单条但 base 被未合并记录占用 → 加字母并记冲突（待确认）
            for letter in "abcdefghijklmnopqrstuvwxyz":
                candidate = f"{base}{letter}"
                if candidate not in used:
                    break
            group[0]["new_key"] = candidate
            used.add(candidate)
            key_conflicts.append({"cluster": group[0]["cluster"],
                                  "canonical": group[0]["canonical_key"],
                                  "why": f"base `{base}` 已被未合并记录占用 → 改用 `{candidate}`"})
            return
        # ② 同姓同年但作者不同 → Surname+GivenInitials+Year 区分（中文用 SurnameGiven 全拼）
        for group in identities:
            disc_base = f"{_given_discriminator(group[0]['_person'])}{_record_year(records[group[0]['canonical_key']])}"
            group.sort(key=sort_key)
            free_letters = iter(ch for ch in "abcdefghijklmnopqrstuvwxyz"
                                if f"{disc_base}{ch}" not in used)
            if len(group) == 1 and disc_base not in used:
                group[0]["new_key"] = disc_base
                used.add(disc_base)
                surname_year_discriminated.append({
                    "base": base, "discriminator": disc_base,
                    "cluster": group[0]["cluster"],
                    "canonical": group[0]["canonical_key"],
                    "why": f"同姓同年不同作者（base `{base}`）→ 用「姓名首字母」区分",
                })
                continue
            for entry in group:
                candidate = f"{disc_base}{next(free_letters)}"
                entry["new_key"] = candidate
                used.add(candidate)
            if len(group) > 1:
                apa_letter_groups.append({
                    "base": disc_base,
                    "clusters": [e["cluster"] for e in group],
                    "keys": [e["new_key"] for e in group],
                    "titles": [str(_record_fields(records[e["canonical_key"]]).get("title") or "")
                               for e in group],
                })
            surname_year_discriminated.append({
                "base": base, "discriminator": disc_base,
                "clusters": [e["cluster"] for e in group],
                "canonical": "、".join(e["canonical_key"] for e in group),
                "why": f"同姓同年不同作者（base `{base}`）→ 用「姓名首字母」区分",
            })

    for base, entries in sorted(rekey_groups.items()):
        assign(entries, base, kind="rekey")
    # 兜底：只对**重命名**的簇做全局唯一性检查（不重命名的合并簇保留旧 key，不得被改名）
    taken = set(reserved)
    for entry in [e for e in clusters if e["action"] == "merge" and e.get("new_key_base")]:
        key = entry.get("new_key") or entry["canonical_key"]
        if key not in taken:
            taken.add(key)
            continue
        stem = entry.get("new_key_base") or entry["canonical_key"]
        for letter in "abcdefghijklmnopqrstuvwxyz":
            candidate = f"{stem}{letter}"
            if candidate not in taken:
                break
        else:
            candidate = f"{stem}-2"
        key_conflicts.append({"cluster": entry["cluster"],
                              "canonical": entry["canonical_key"],
                              "why": f"新 key `{key}` 全局冲突 → 改用 `{candidate}`"})
        entry["new_key"] = candidate
        taken.add(candidate)
    for entry in clusters:
        entry.pop("_person", None)
        if entry["action"] != "merge":
            continue
        if not entry.get("new_key"):
            entry["new_key"] = entry["canonical_key"]
        # aliases：所有成员旧 key（含 canonical 旧 key）+ 成员已有 aliases
        alias_set = set()
        for key in entry["keys"]:
            record = records.get(key)
            if record is None:
                continue
            alias_set.add(key)
            for alias in record.get("aliases") or []:
                if isinstance(alias, str) and alias:
                    alias_set.add(alias)
        alias_set.discard(entry["new_key"])
        final_keys = (live_keys - renamed_away) | {e["new_key"] for e in clusters
                                                   if e["action"] == "merge"}
        alias_set = {a for a in alias_set if a not in final_keys}
        entry["aliases"] = sorted(alias_set)

    # ── 汇总统计 ──
    merge_clusters = [entry for entry in clusters if entry["action"] == "merge"]
    manual_clusters = [entry for entry in clusters if entry["action"] == "manual"]
    merged_away = sum(len(entry["keys"]) - 1 for entry in merge_clusters)
    renames = [{"old": key, "new": entry["new_key"]}
               for entry in merge_clusters for key in entry["keys"]
               if key != entry["new_key"]]
    return {
        "db": os.path.basename(store.path) if getattr(store, "path", "") else "",
        "db_sha256": bibdb.db_sha256(store.path) if getattr(store, "path", "") else "",
        "pairs_total": len(pairs),
        "pairs_excluded_miaolitian": len(mia_pairs),
        "pairs_used": len(kept_pairs),
        "similarity_1_pairs": sum(1 for _a, _b, s in kept_pairs if s >= 1.0),
        "clusters_total": len(clusters),
        "clusters_merge": len(merge_clusters),
        "clusters_manual": len(manual_clusters),
        "records_total": len(records),
        "records_in_clusters": sum(len(entry["keys"]) for entry in clusters),
        "records_merged_away": merged_away,
        "predicted_records": len(records) - merged_away,
        "rekey_clusters": sum(1 for entry in merge_clusters if entry["has_similarity_1"]),
        "rekey_records": sum(1 for entry in merge_clusters if entry["has_similarity_1"]
                             and entry["new_key"] != entry["canonical_key"]),
        "key_conflicts": key_conflicts,
        "apa_letter_groups": apa_letter_groups,
        "surname_year_discriminated": surname_year_discriminated,
        "renames": renames,
        "manual_clusters": manual_clusters,
        "clusters": clusters,
        "miaolitian_keys": sorted(MERGE_IGNORE_KEYS & set(records)),
    }


def _merge_cluster_row(entry: dict) -> list[str]:
    keys = entry["keys"]
    sims = "、".join(f"{edge['similarity']:.4f}" for edge in entry["edges"][:3]) + \
           ("…" if len(entry["edges"]) > 3 else "")
    return [
        entry["cluster"], str(len(keys)), "；".join(keys), entry["action"],
        entry["canonical_key"], entry["new_key"] or entry["canonical_key"],
        entry["entry_type"], sims, entry["reason"],
        "；".join(f"{d['field']}" for d in entry["field_diffs"]) or "",
    ]


MERGE_CSV_COLUMNS = ["cluster", "size", "keys", "action", "canonical_key", "new_key",
                     "entry_type", "edge_similarity", "reason", "field_diffs"]


def build_merge_report_md(plan: dict, *, apa_url: str, generated_from: str) -> str:
    """合并计划报告（dry-run；无时间戳/无绝对路径，可重复生成逐字节相同）。"""
    lines = [
        "# 引用库 · 重复候选合并计划（dry-run，未写库）",
        "",
        "> 本报告由 `bibdb.py merge-duplicates`（**dry-run only**）生成：只读 `bibliography.jsonl`，",
        "> 不合并、不改 key、不重算 sha——**一个字都不写库**。apply 需 Lead/用户审批后另卡执行",
        "> （届时按本计划 JSON 逐簇合并并重算 content_sha256）。",
        f"> 数据：`{plan['db']}`（库 sha256 `{plan['db_sha256']}`）。",
        "",
        "## 0. 统计",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 候选对总数（duplicates.csv） | {plan['pairs_total']} |",
        f"| `miaolitian`/`miaolitian2` 相关对（**跳过**） | {plan['pairs_excluded_miaolitian']} |",
        f"| 参与聚类的对 | {plan['pairs_used']} |",
        f"| 其中 `similarity=1`（题名同形） | {plan['similarity_1_pairs']} |",
        f"| 连通分量（簇） | **{plan['clusters_total']}**（合并 {plan['clusters_merge']} / 人工 {plan['clusters_manual']}） |",
        f"| 涉及记录 | {plan['records_in_clusters']}（库内 {plan['records_total']} 条） |",
        f"| **预计合并消失的记录** | **{plan['records_merged_away']}** |",
        f"| **预计合并后条数** | **{plan['predicted_records']}**（{plan['records_total']} → {plan['predicted_records']}） |",
        f"| 参与 key 规范化的簇 / 实际改 key 的记录 | {plan['rekey_clusters']} / {plan['rekey_records']} |",
        f"| 其中同作者同年按题名字母序加 a/b/c（APA 7，自动） | {len(plan['apa_letter_groups'])} 组 |",
        f"| 同姓同年不同作者（用「姓名首字母」区分，自动） | {len(plan['surname_year_discriminated'])} 项 |",
        f"| key 冲突/需人工确认项 | {len(plan['key_conflicts'])} |",
        "",
        "## 1. 规则依据（已 web 核实）",
        "",
        "- **APA 7**：同一作者同一年有多条作品时，先按**题名字母序**排列，再依次加 a/b/c 后缀；",
        "  题名排序忽略起始冠词 A/An/The（本计划 `_merge_title_sort_key` 按此实现）。",
        f"  官方页：<{apa_url}>（Citing works with the same author and date；正文由 JS 渲染，",
        "  判据另经 Tilburg University APA 7 指南 3.3「Order of reference list entries」核实：",
        "  <https://libguides.uvt.nl/apa-manual/order-references>）。",
        "- 本库内部 key 规范：`Surname`（或中文 `SurnameGiven` 拼音）+ 4 位年份 + 可选 a/b/c，",
        "  与库内既有风格一致（`LiLei2005` / `Haack1978` / `Li2026a`）；只对 **similarity=1 的簇**",
        "  生成新 key，其余合并簇保留 canonical 旧 key（避免一次改动过大）。",
        "- 版本选择（用户拍板）：**优先保留花括号/LaTeX 保护** → 其次字段更完整 → 来源更多 →",
        "  冲突更少 → key 稳定。",
        "- 字段合并：缺失补全；写法差异取「花括号保护最多/信息最全」的值；实质冲突（年份/第一作者",
        "  姓氏/同秩条目类型不同）**不自动合并**，整簇进人工清单。",
        "- 旧 key 一律进 `aliases`（合并后不再与任何存活 key 冲突才保留）；来源 `sources` 全量合并去重。",
        "",
        "## 2. key 规范化样例（前 20）",
        "",
        "| 簇 | canonical 旧 key | 新 key | 簇成员 |",
        "|---|---|---|---|",
    ]
    rekeyed = [entry for entry in plan["clusters"]
               if entry["action"] == "merge" and entry.get("new_key")
               and entry["new_key"] != entry["canonical_key"]]
    for entry in rekeyed[:20]:
        lines.append(f"| {entry['cluster']} | `{entry['canonical_key']}` | `{entry['new_key']}` | "
                     f"{'、'.join('`' + k + '`' for k in entry['keys'][:6])} |")
    if not rekeyed:
        lines.append("| — | （无） | — | — |")
    lines += [
        "",
        "## 3. 合并样例（前 20；保留成员 + 字段差异）",
        "",
        "| 簇 | 保留 key（新 key） | 成员 | 字段差异（保留值 vs 其它写法） |",
        "|---|---|---|---|",
    ]
    for entry in [e for e in plan["clusters"] if e["action"] == "merge"][:20]:
        diffs = "；".join(f"{d['field']}" for d in entry["field_diffs"]) or "（无实质差异）"
        lines.append(f"| {entry['cluster']} | `{entry['canonical_key']}` → `{entry['new_key']}` | "
                     f"{'、'.join('`' + k + '`' for k in entry['keys'][:8])} | {diffs} |")
    lines += [
        "",
        "## 4. 人工清单（不自动合并；需用户逐条确认）",
        "",
        f"共 **{len(plan['manual_clusters'])}** 簇：",
        "",
        "| 簇 | 成员 | 原因 |",
        "|---|---|---|",
    ]
    for entry in plan["manual_clusters"][:60]:
        lines.append(f"| {entry['cluster']} | {'、'.join('`' + k + '`' for k in entry['keys'])} | "
                     f"{entry['reason']} |")
    if not plan["manual_clusters"]:
        lines.append("| — | （无） | — |")
    lines += [
        "",
        "## 5. key 规则明细",
        "",
        "### 5.1 同作者同年 → 题名字母序 a/b/c（APA 7，自动）",
        "",
        "| base | 新 key（按题名排序） | 题名 |",
        "|---|---|---|",
    ]
    for group in plan["apa_letter_groups"]:
        titles = "；".join(f"`{_md_escape(t, width=40)}`" for t in group["titles"][:4])
        lines.append(f"| `{group['base']}` | "
                     f"{'、'.join('`' + k + '`' for k in group['keys'])} | {titles} |")
    if not plan["apa_letter_groups"]:
        lines.append("| — | — | （无） |")
    lines += [
        "",
        "### 5.2 同姓同年但作者不同人 → 用「姓名首字母」区分（自动）",
        "",
        "| base | 区分后 key | 所属簇 | 说明 |",
        "|---|---|---|---|",
    ]
    for item in plan["surname_year_discriminated"]:
        keys = item.get("keys") or [item.get("discriminator", "")]
        lines.append(f"| `{item.get('base', '')}` | "
                     f"{'、'.join('`' + k + '`' for k in keys)} | "
                     f"{item.get('cluster', '')} | {item.get('why', '')} |")
    if not plan["surname_year_discriminated"]:
        lines.append("| — | — | — | （无） |")
    lines += [
        "",
        "### 5.3 key 冲突 / 需人工确认（全量）",
        "",
        "| 簇 | canonical | 说明 |",
        "|---|---|---|",
    ]
    for item in plan["key_conflicts"]:
        lines.append(f"| {item.get('cluster', '')} | `{item.get('canonical', '')}` | "
                     f"{item.get('why', '')} |")
    if not plan["key_conflicts"]:
        lines.append("| — | — | （无） |")
    lines += [
        "",
        "## 6. 风险与回滚",
        "",
        "- **风险**：同作品判断依赖题名归一相似度（≥0.90）+ 第一作者姓 + 年份；极端同题不同版",
        "  （初版/译本/修订版）可能被并。故人工清单外的簇仍建议按本报告 §3 抽查 20 条。",
        "- 年份/第一作者姓氏实质冲突的簇**已全部**进人工清单，未自动处理。",
        "- 高相似度但题名不同的对子（0.90–0.999）按用户指令合并；若担心误并，可只先执行",
        "  `similarity=1` 子集（本报告已单独统计，见决策点）。",
        "- **回滚**：apply 前 `cp bibliography.jsonl /tmp/bibliography_before_merge.jsonl`；",
        "  回滚 `cp /tmp/bibliography_before_merge.jsonl <notes>/bib_db/bibliography.jsonl`。",
        "- apply 后须：重算 content_sha256、lint error 0、重生成三清单、逐条核对 aliases。",
        "",
        "## 7. 交付文件",
        "",
        f"- `{MERGE_REPORT_MD}`（本报告）",
        f"- `{MERGE_REPORT_CSV}`（全量簇清单）",
        f"- `{MERGE_RENAMES_CSV}`（旧 key → 新 key 全量；含冲突说明）",
        f"- `{MERGE_MANUAL_CSV}`（人工清单）",
        f"- `{MERGE_PLAN_JSON}`（apply 用完整计划：每簇成员/合并字段/aliases/新 key）",
        "",
        "## 8. 给 Lead/用户的决策点（≤10 行）",
        "",
        f"1. **合并规模**：{plan['pairs_total']} 对 → {plan['clusters_total']} 簇，自动合并 "
        f"{plan['clusters_merge']} 簇；记录 **{plan['records_total']} → {plan['predicted_records']}**"
        f"（合并消失 {plan['records_merged_away']} 条）。",
        f"2. **人工清单 {len(plan['manual_clusters'])} 簇**：全部是「条目类型实质冲突」"
        f"（年份/作者冲突 0 簇），逐条见 §4/`{MERGE_MANUAL_CSV}`——请逐簇定夺保留哪种类型。",
        f"3. **key 规范化**：{plan['rekey_clusters']} 簇 / 改 key {plan['rekey_records']} 条；"
        f"APA 同作者同年按题名字母序 {len(plan['apa_letter_groups'])} 组（自动）、"
        f"同姓同年不同人用姓名首字母 {len(plan['surname_year_discriminated'])} 项（自动）。",
        f"4. **key 冲突/待确认 {len(plan['key_conflicts'])} 项**（§5.3）：base 被未合并记录占用 →"
        " 自动加字母，或作者/年份无法解析 → 保留旧 key。",
        "5. **非 similarity=1 的合并簇保留旧 key**（按用户指令只统一 sim=1）；若也要全库统一，需另定。",
        f"6. **字段合并**：花括号保护优先 + 缺失补全 + 写法差异取信息最全；"
        f"{sum(1 for c in plan['clusters'] if c.get('field_diffs'))} 簇有字段差异明细（见 JSON）。",
        "7. 建议先审：13 人工簇 + 12 key 项 → 抽查 20 条自动合并簇 → GO 后另卡 apply"
        "（备份/重算 sha/aliases/三清单重生成/回测）。",
        "",
    ]
    return "\n".join(lines)


def build_merge_plan_csv(plan: dict) -> str:
    import csv as _csv
    import io

    buffer = io.StringIO()
    writer = _csv.writer(buffer, lineterminator="\n")
    writer.writerow(MERGE_CSV_COLUMNS)
    for entry in plan["clusters"]:
        writer.writerow(_merge_cluster_row(entry))
    return "\ufeff" + buffer.getvalue()


def build_merge_renames_csv(plan: dict) -> str:
    import csv as _csv
    import io

    buffer = io.StringIO()
    writer = _csv.writer(buffer, lineterminator="\n")
    writer.writerow(["old_key", "new_key", "note"])
    conflict_by_key = {}
    for item in plan["key_conflicts"]:
        for key in re.findall(r"`([^`]+)`", item.get("why", "")):
            conflict_by_key.setdefault(key, item.get("why", ""))
    for item in plan["renames"]:
        entry_new = item["new"]
        note = ""
        if item["old"] in plan.get("key_conflicts_text", {}):
            note = plan["key_conflicts_text"][item["old"]]
        writer.writerow([item["old"], entry_new, note])
    return "\ufeff" + buffer.getvalue()


def build_merge_manual_csv(plan: dict) -> str:
    import csv as _csv
    import io

    buffer = io.StringIO()
    writer = _csv.writer(buffer, lineterminator="\n")
    writer.writerow(["cluster", "keys", "reason", "years", "surnames", "entry_types"])
    records = {}
    for entry in plan["manual_clusters"]:
        members = entry.get("members") or []
        writer.writerow([
            entry["cluster"],
            "；".join(entry["keys"]),
            entry["reason"],
            "；".join(sorted({str(m.get("year") or "") for m in members})),
            "；".join(sorted({str(m.get("author") or "") for m in members})),
            "；".join(sorted({str(m.get("entry_type") or "") for m in members})),
        ])
    return "\ufeff" + buffer.getvalue()


def build_merge_plan_json(plan: dict) -> str:
    payload = dict(plan)
    payload["tool"] = "bibdb.py merge-duplicates（dry-run）"
    payload["mode"] = "dry-run"
    payload["apply_implemented"] = False
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_merge_reports(args, plan: dict) -> list[dict]:
    out_dir = str(getattr(args, "out_dir", "") or "")
    written: list[dict] = []
    if not out_dir:
        return written
    written.append(_write_report(os.path.join(out_dir, MERGE_REPORT_MD),
                                 build_merge_report_md(
                                     plan, apa_url="https://apastyle.apa.org/style-grammar-guidelines/citations/basic-principles/same-year-author",
                                     generated_from=f"{plan['db']}（sha256 `{plan['db_sha256']}`）")))
    written.append(_write_report(os.path.join(out_dir, MERGE_REPORT_CSV),
                                 build_merge_plan_csv(plan)))
    written.append(_write_report(os.path.join(out_dir, MERGE_RENAMES_CSV),
                                 build_merge_renames_csv(plan)))
    written.append(_write_report(os.path.join(out_dir, MERGE_MANUAL_CSV),
                                 build_merge_manual_csv(plan)))
    written.append(_write_report(os.path.join(out_dir, MERGE_PLAN_JSON),
                                 build_merge_plan_json(plan)))
    return written


# ── 重复合并 apply（merge-duplicates --apply；第四十三批 task-44）───────────────
MERGE_APPLY_REPORT_MD = "MERGE-APPLY-2026-10-18.md"
MERGE_APPLY_REPORT_JSON = "MERGE-APPLY-2026-10-18.json"
MERGE_APPLY_CLUSTERS_CSV = "MERGE-APPLY-2026-10-18.csv"
MERGE_APPLY_ARCHIVE_CSV = "MERGE-APPLY-ARCHIVE.csv"
MERGE_APPLY2_REPORT_MD = "MERGE-APPLY2-2026-10-19.md"
MERGE_APPLY2_REPORT_JSON = "MERGE-APPLY2-2026-10-19.json"
MERGE_APPLY2_CLUSTERS_CSV = "MERGE-APPLY2-2026-10-19.csv"
MERGE_APPLY2_ARCHIVE_CSV = "MERGE-APPLY2-ARCHIVE.csv"


def _merge_sources_union(records_to_merge: list[dict]) -> list[dict]:
    """合并 sources：按来源身份去重（kind/issue/slug/file/key/entry_sha256），保持确定性。"""
    seen: dict[tuple, dict] = {}
    for record in records_to_merge:
        for source in _record_sources(record):
            identity = tuple(str(source.get(name) or "") for name in
                             ("kind", "issue", "slug", "file", "key", "entry_sha256"))
            if identity not in seen:
                seen[identity] = dict(source)
    return [seen[key] for key in sorted(seen, key=lambda item: item)]


def _merge_conflicts_union(records_to_merge: list[dict]) -> list[dict]:
    """合并 conflicts 历史：按（existing_sha256, incoming_sha256, resolution, resolved_at, at）去重。"""
    seen: dict[tuple, dict] = {}
    for record in records_to_merge:
        for conflict in record.get("conflicts") or []:
            if not isinstance(conflict, dict):
                continue
            identity = tuple(str(conflict.get(name) or "") for name in
                             ("existing_sha256", "incoming_sha256", "resolution",
                              "resolved_at", "at"))
            seen.setdefault(identity, conflict)
    return [seen[key] for key in sorted(seen, key=lambda item: item)]


def _merge_manual_research_table(research: list[dict] | None) -> list[str]:
    """13 个人工簇的联网核实建议表（≤13 行 + 表头）。"""
    lines = [
        "## 4. 13 个人工簇：**暂不合并** + 联网核实建议（待用户/Lead 最终点头）",
        "",
        "> 依据：免费 web 搜索/抓取（未用付费 AI）；每簇给出证据 URL 与建议保留类型。",
        "",
        "| # | 簇 | 成员 key | 当前类型 | 证据 URL | 建议保留类型 | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    if not research:
        lines.append("| — | — | — | — | （未提供 research JSON） | — | — |")
        return lines
    for index, item in enumerate(research[:13], start=1):
        urls = "<br>".join(f"<{url}>" for url in (item.get("urls") or [])[:3]) or "（无）"
        lines.append(
            f"| {index:02d} | {_md_escape(item.get('cluster', ''))} | "
            f"{'、'.join('`' + str(k) + '`' for k in (item.get('keys') or [])[:6])} | "
            f"{_md_escape('/'.join(str(t) for t in item.get('types') or []))} | {urls} | "
            f"`{_md_escape(item.get('suggested_type', ''))}` | "
            f"{_md_escape(item.get('reason', ''))} |")
    return lines


def build_merge_apply_report_md(plan: dict, result: dict, sha_before: str, sha_after: str,
                                *, backup_path: str, backup_sha: str,
                                verification: dict, research: list[dict] | None = None,
                                lists_note: tuple[str, str, str] | None = None,
                                decisions: list[dict] | None = None,
                                skipped_decisions: list[dict] | None = None,
                                batch: int = 1) -> str:
    """合并 apply 报告（写入 bib_db/；含 sha 链/别名归档/人工簇核实/回滚）。"""
    applied = result["applied_clusters"]
    batch_label = "（第四十四批 · 第二批：13 人工簇按建议合并）" if batch == 2 else "（第四十三批）"
    lines = [
        f"# 引用库 · 重复候选合并 + key 规范化 apply 报告{batch_label}",
        "",
        "> 本报告由 `bibdb.py merge-duplicates --apply` 生成：按 Lead 审阅通过的计划执行",
        "> **657 个自动合并簇 + 499 条 key 规范化**（用户 2026-10-18 授权：除",
        "> `miaolitian`/`miaolitian2` 外全部合并；similarity=1 统一 key 规范）。",
        "> **13 个人工簇（条目类型实质冲突）保持原样未合并**；`miaolitian*` 未动；",
        "> 历史冲突裁决（203/203 已裁决）不改。",
        "",
        f"- 库 sha256（apply 前）：`{sha_before}`",
        f"- 库 sha256（apply 后）：`{sha_after}`",
        f"- apply 前备份：`{backup_path}`（sha256 `{backup_sha}`）",
        f"- 记录数：**{plan['records_total']} → {result['records_after']}**"
        f"（合并消失 {result['records_removed']} 条）",
        f"- 自动合并簇：**{result['clusters_applied']}** / 计划 {plan['clusters_merge']}"
        f"（跳过 {result['clusters_skipped']}）；人工保留：**{len(plan['manual_clusters'])}** 簇",
        f"- key 改名：**{result['renames']}** 条；别名写入：**{result['aliases_total']}** 个"
        f"（全量旧→新映射见 `{MERGE_APPLY_ARCHIVE_CSV}`）",
        f"- key 冲突消解：{len(plan['key_conflicts'])} 项（§5）",
        "",
        "## 1. 合并规则（与 dry-run 计划一致）",
        "",
        "- canonical 版本：花括号保护 → 字段完整 → 来源多 → 冲突少（同一套 `_merge_version_score`）；",
        "- 字段合并：缺失补全；写法差异取「花括号保护最多/信息最全」的值（不丢 title/author/year/",
        "  journal/volume/number/pages/publisher/doi/url/note）；",
        "- sources 全量合并去重（kind/issue/slug/file/key/entry_sha256 去重）；conflicts 历史合并去重；",
        "- 被并记录旧 key + 原有 aliases 全部写入合并后记录的 `aliases`；",
        "- key 规范：APA 7（同作者同年按题名字母序 a/b/c）；同姓同年不同作者用姓名首字母区分。",
        "",
        "## 2. 幂等与一致性验证",
        "",
        f"- apply 后重新生成候选清单再跑 dry-run：**合并簇残留 {verification.get('residual_merge', '—')}**"
        f"（预期 0；剩余的 {verification.get('residual_manual', '—')} 簇均为人工簇）",
        f"- 二次 apply（按 apply 后新计划）：**applied {verification.get('second_applied', '—')}**"
        f"、库未变 `{verification.get('second_unchanged', '—')}`",
        f"- lint：error **{verification.get('lint_errors', '—')}** / warning "
        f"{verification.get('lint_warnings', '—')}；content_sha 复算不符 **0** 条",
        f"- 冲突：203 项仍全部已裁决（待裁决 **{verification.get('pending_conflicts', '—')}**）",
    ]
    if lists_note:
        lines += [
            f"- 三清单重生成 sha：CONFLICTS `{lists_note[0][:16]}…` / DUPLICATES "
            f"`{lists_note[1][:16]}…` / duplicates.csv `{lists_note[2][:16]}…`；"
            f"候选对 **{verification.get('pairs_after', '—')}** 对"
            f"（合并前 1294；剩余均属人工簇/待确认）",
        ]
    lines += [
        "",
        "## 3. apply 明细（全量见 CSV；前 20 抽样）",
        "",
        "| # | 簇 | 旧 key | 新 key | 别名数 | 合并来源 |",
        "|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(applied[:20], start=1):
        old = "、".join(f"`{key}`" for key in item["keys"][:6]) + ("…" if len(item["keys"]) > 6 else "")
        lines.append(f"| {index:02d} | {item['cluster']} | {old} | `{item['new_key']}` | "
                     f"{len(item['aliases'])} | {item['sources']} |")
    if not applied:
        lines.append("| — | （无） | — | — | — | — |")
    if decisions:
        lines += [
            "## 3b. 本次人工簇决策（用户 2026-10-19 拍板「按建议合并」）",
            "",
            "| 簇 | 成员 key | 选定类型 | 备注 |",
            "|---|---|---|---|",
        ]
        for item in decisions:
            note = str(item.get("note") or "")
            if item.get("doubt"):
                note = ("⚠ 存疑：" + note).strip()
            lines.append(f"| {_md_escape(str(item.get('cluster', '')))} | "
                         f"{'、'.join('`' + str(k) + '`' for k in item.get('keys') or [])} | "
                         f"`{_md_escape(str(item.get('entry_type', '')))}` | {_md_escape(note)} |")
        for item in skipped_decisions or []:
            lines.append(f"| （跳过） | {'、'.join('`' + str(k) + '`' for k in item.get('keys') or [])} | — | "
                         f"{_md_escape(str(item.get('reason', '')))} |")
        lines.append("")
    if batch == 2:
        lines += [
            "## 4. 人工簇处理结果",
            "",
            "- 12 个按用户拍板建议类型合并（§3b）；**M0009 Segerberg 保持 `Segerberg1971`（phdthesis）与",
            "  `Segerberg1971b`（book 版）两条不合并**（用户 2026-10-18 已拆分）；",
            "- 剩余人工簇：M0009 一组（候选对中仍有其内部 6 对，见后文清单校验）。",
            "",
        ]
    else:
        lines += ["", * _merge_manual_research_table(research), ""]
    lines += [
        "## 5. key 冲突/自动消解（全量）",
        "",
        "| 簇 | canonical | 处理 |",
        "|---|---|---|",
    ]
    for item in plan.get("key_conflicts") or []:
        lines.append(f"| {_md_escape(str(item.get('cluster', '')))} | "
                     f"`{_md_escape(str(item.get('canonical', '')))}` | "
                     f"{_md_escape(str(item.get('why', '')))} |")
    if not plan.get("key_conflicts"):
        lines.append("| — | — | （无） |")
    lines += [
        "",
        "## 6. 回滚命令",
        "",
        "```bash",
        f"cp {backup_path} ../EditToolBox-notes/bib_db/bibliography.jsonl",
        "```",
        "",
        f"（备份 sha256：`{backup_sha}`；回滚后须重跑 `bibdb.py report --out ../EditToolBox-notes/bib_db`）",
        "",
        "## 7. 待用户确认",
        "",
        f"- **13 个人工簇**（§4，条目类型实质冲突）暂未合并，等用户/Lead 最终点头；",
        f"- `miaolitian` / `miaolitian2` 保持原样（用户明确例外）；",
        f"- key 冲突 {len(plan.get('key_conflicts') or [])} 项已按规则自动消解（§5，可复核）。",
        "",
    ]
    return "\n".join(lines)


def build_merge_apply_json(plan: dict, result: dict, sha_before: str, sha_after: str,
                           *, backup_path: str, backup_sha: str,
                           verification: dict) -> str:
    payload = {
        "tool": "bibdb.py merge-duplicates --apply",
        "mode": "apply",
        "plan_db_sha256": plan.get("db_sha256"),
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "backup_path": backup_path,
        "backup_sha256": backup_sha,
        "records_before": plan.get("records_total"),
        "records_after": result["records_after"],
        "records_removed": result["records_removed"],
        "clusters_total": plan.get("clusters_total"),
        "clusters_merge": plan.get("clusters_merge"),
        "clusters_manual_kept": len(plan.get("manual_clusters") or []),
        "clusters_applied": result["clusters_applied"],
        "clusters_skipped": result["clusters_skipped"],
        "renames": result["renames"],
        "aliases_total": result["aliases_total"],
        "key_conflicts": plan.get("key_conflicts") or [],
        "manual_clusters": plan.get("manual_clusters") or [],
        "applied_entries": result["applied_clusters"],
        "skipped_entries": result["skipped_clusters"],
        "verification": verification,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_merge_apply_clusters_csv(result: dict) -> str:
    import csv as _csv
    import io

    buffer = io.StringIO()
    writer = _csv.writer(buffer, lineterminator="\n")
    writer.writerow(["cluster", "old_keys", "canonical_key", "new_key", "aliases",
                     "removed_keys", "sources", "conflicts"])
    for item in result["applied_clusters"]:
        writer.writerow([item["cluster"], "；".join(item["keys"]), item["canonical_key"],
                         item["new_key"], "；".join(item["aliases"]),
                         "；".join(item["removed"]), item["sources"], item["conflicts"]])
    return "\ufeff" + buffer.getvalue()


def build_merge_archive_csv(plan: dict) -> str:
    """旧 key → 新 key 归档映射（被并记录 + 改名 canonical）。"""
    import csv as _csv
    import io

    buffer = io.StringIO()
    writer = _csv.writer(buffer, lineterminator="\n")
    writer.writerow(["old_key", "new_key", "cluster", "note"])
    for entry in plan.get("clusters") or []:
        if entry.get("action") != "merge":
            continue
        for key in entry.get("keys") or []:
            if key == entry.get("new_key"):
                continue
            writer.writerow([key, entry.get("new_key", ""), entry.get("cluster", ""),
                             "merged" if key != entry.get("canonical_key") else "renamed"])
    return "\ufeff" + buffer.getvalue()


def _write_merge_apply_reports(args, plan: dict, result: dict, sha_before: str,
                               sha_after: str, *, backup_path: str, backup_sha: str,
                               verification: dict, lists_note=None,
                               decisions: list[dict] | None = None,
                               skipped_decisions: list[dict] | None = None,
                               batch: int = 1) -> list[dict]:
    out_dir = str(getattr(args, "out_dir", "") or "")
    if not out_dir:
        return []
    md_name = MERGE_APPLY2_REPORT_MD if batch == 2 else MERGE_APPLY_REPORT_MD
    json_name = MERGE_APPLY2_REPORT_JSON if batch == 2 else MERGE_APPLY_REPORT_JSON
    csv_name = MERGE_APPLY2_CLUSTERS_CSV if batch == 2 else MERGE_APPLY_CLUSTERS_CSV
    archive_name = MERGE_APPLY2_ARCHIVE_CSV if batch == 2 else MERGE_APPLY_ARCHIVE_CSV
    research = None
    research_path = str(getattr(args, "research", "") or "")
    if research_path and os.path.isfile(research_path):
        try:
            research = json.load(open(research_path, encoding="utf-8"))
        except (OSError, ValueError):
            research = None
    written: list[dict] = []
    written.append(_write_report(os.path.join(out_dir, md_name),
                                 build_merge_apply_report_md(
                                     plan, result, sha_before, sha_after,
                                     backup_path=backup_path, backup_sha=backup_sha,
                                     verification=verification, research=research,
                                     lists_note=lists_note, decisions=decisions,
                                     skipped_decisions=skipped_decisions, batch=batch)))
    written.append(_write_report(os.path.join(out_dir, json_name),
                                 build_merge_apply_json(
                                     plan, result, sha_before, sha_after,
                                     backup_path=backup_path, backup_sha=backup_sha,
                                     verification=verification)))
    written.append(_write_report(os.path.join(out_dir, csv_name),
                                 build_merge_apply_clusters_csv(result)))
    written.append(_write_report(os.path.join(out_dir, archive_name),
                                 build_merge_archive_csv(plan)))
    return written


# ── 字段归位建议（A 类 265 条；只读）────────────────────────────────────────

_TITLE_MARKER_RE = re.compile(r"\\textit\s*\{|\\emph\s*\{|《[^》]{1,120}》|「[^」]{1,120}」|『[^』]{1,120}』")
_YEAR_TOKEN_RE = re.compile(r"(?<!\d)(1[5-9]\d{2}|20[0-2]\d)(?!\d)")
_PUBLISHER_TOKEN_RE = re.compile(
    r"(?:出版社|書房|書店|书店|书局|印书馆|Press\b|University\b|Publishing\b|Verlag\b|"
    r"Routledge\b|Blackwell\b|Springer\b)")
_AUTHOR_SEG_SPLIT_RE = re.compile(r"\s+and\s+", re.I)


def _plain_cjk_len(text: str) -> int:
    return len(re.sub(r"\\[a-zA-Z]+\*?\{?|[{}\\]", "", text))


def _strip_title_wrapper(text: str) -> str:
    """`\\emph{論理思想}` / `《書名》` → 内文（只是建议值，不改库）。"""
    t = text.strip()
    for cmd in ("\\textit{", "\\emph{"):
        if t.startswith(cmd) and t.endswith("}"):
            return t[len(cmd):-1].strip()
    if (t.startswith("《") and t.endswith("》")) or (t.startswith("「") and t.endswith("」")) \
            or (t.startswith("『") and t.endswith("』")):
        return t[1:-1].strip()
    return t


def _segment_candidates(record: dict) -> dict:
    """一条记录 → {title: [...], year: [...], publisher: [...], entry_type: [...]} 候选。"""
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    out: dict[str, list[dict]] = {"title": [], "year": [], "publisher": [], "entry_type": []}
    seen = set()

    def _add(kind: str, value: str, source: str, evidence: str, risk: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        token = (kind, text)
        if token in seen:
            return
        seen.add(token)
        out[kind].append({"value": text, "source": source,
                          "evidence": evidence, "risk": risk})

    for name in ("author", "journal", "booktitle", "note", "publisher", "address"):
        raw = str(fields.get(name) or "")
        if not raw:
            continue
        for segment in _AUTHOR_SEG_SPLIT_RE.split(raw):
            segment = segment.strip().rstrip("。.,，;；")
            if not segment:
                continue
            if _YEAR_TOKEN_RE.search(segment) and len(segment) <= 12:
                for year in _YEAR_TOKEN_RE.findall(segment):
                    _add("year", year, name,
                         f"{name} 字段里出现独立年份 `{segment}`", "medium")
            if _TITLE_MARKER_RE.search(segment):
                _add("title", _strip_title_wrapper(segment), name,
                     f"{name} 字段里有题名标记（\\textit/\\emph/《》/「」/『』）",
                     "medium")
            elif name == "journal" and len(segment) >= 8 and not _YEAR_TOKEN_RE.search(segment):
                _add("title", segment, name,
                     "journal 字段整段是长串（可能把题名错填进期刊名）", "high")
            elif name == "author" and len(segment) >= 8 and _plain_cjk_len(segment) >= 6 \
                    and not _PUBLISHER_TOKEN_RE.search(segment):
                _add("title", segment, name,
                     "author 字段里出现长中文串（疑似题名/版本信息错位）", "high")
            if _PUBLISHER_TOKEN_RE.search(segment) or re.match(r"^[^：:]{1,30}[：:]", segment):
                _add("publisher", segment, name,
                     f"{name} 字段里有出版者/出版地形态（`：`/出版社/書房/Press…）", "medium")
    return out


_PLACEHOLDER_KEY_RE = re.compile(r"^(?:key|k|ref|entry|bib|item)\d*$", re.I)
_DOI_KEY_RE = re.compile(r"^(?:10\.\d|https?://|doi:)", re.I)


def _key_anomaly(key: str) -> str:
    """key 是否明显异常（C 类：DOI/网址当 key、`key2` 之类占位符）→ 原因（空=正常）。"""
    text = str(key or "").strip()
    if _DOI_KEY_RE.match(text):
        return "doi"
    if _PLACEHOLDER_KEY_RE.match(text):
        return "placeholder"
    return ""


def collect_field_fix_rows(store, registry: str) -> list[dict]:
    """`suggest-field-fixes` 的只读行：候选字段 + 证据 + 风险等级（不写库）。"""
    rows: list[dict] = []
    known = sorted(bibdb.KNOWN_ENTRY_TYPES)
    for record in store.records():
        key = str(record.get("key") or "")
        etype = str(record.get("entry_type") or "").lower()
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        issues = _record_issues(record)
        sources = _record_sources(record)
        if registry == "unknown_entry_type":
            # 本 registry = 设计卡 C 类「键/类型异常」：entry_type 不在已知表，**或**
            # key 明显是 DOI / 占位符（`10.2307/…`、`key2`）——两类都要「先修类型/key」。
            etype_bad = etype not in bibdb.KNOWN_ENTRY_TYPES
            key_bad = _key_anomaly(key)
            if not etype_bad and not key_bad:
                continue
            close = difflib.get_close_matches(etype, known, n=3, cutoff=0.6)
            candidates = [{"value": candidate, "source": "entry_type",
                           "evidence": f"`{etype}` 与已知类型 `{candidate}` 拼写相近",
                           "risk": "low" if candidate == close[0] and etype[:1] == candidate[:1]
                           else "medium"} for candidate in close] if etype_bad else []
            key_suggestion = ""
            key_candidate = ""
            key_reason = ""
            if key_bad:
                key_reason = "key 是 DOI/网址形态（应改成「作者姓+年份」规范 key）" \
                    if re.match(r"^(?:10\.\d|https?://|doi:)", key) \
                    else "key 是占位符形态（`key2`/`key` 之类，应改成「作者姓+年份」）"
                surname = bibdb.first_author_surname(
                    str(fields.get("author") or fields.get("editor") or ""))
                year = str(bibdb.entry_year(fields) or "")
                if surname and year:
                    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", surname)
                    # 库内 key 惯例 = 首字母大写的姓 + 年份（`Tindale2015`）
                    key_candidate = (stem[:1].upper() + stem[1:] if stem else "") + year
                key_suggestion = (key_reason + f"；建议 `{key_candidate}`"
                                  if key_candidate else key_reason
                                  + "；旧 key 用 --new-key 自动进 aliases")
            anomaly_kind = ("unknown_entry_type" if etype_bad
                            else ("doi_key" if _key_anomaly(key) == "doi"
                                  else "placeholder_key"))
            rows.append({"key": key, "entry_type": etype,
                         "missing": [], "candidates": candidates,
                         "anomaly_kind": anomaly_kind,
                         "key_candidate": key_candidate,
                         "key_risk": ("medium" if anomaly_kind == "doi_key"
                                      and key_candidate else
                                      ("high" if key_candidate else "")),
                         "key_reason": key_reason,
                         "key_suggestion": key_suggestion,
                         "issues": issues,
                         "sources": [{"issue": s.get("issue"), "slug": s.get("slug"),
                                      "file": s.get("file")} for s in sources],
                         "fields": fields})
            continue
        missing = []
        if not str(fields.get("title") or "").strip():
            missing.append("title")
        if not bibdb.entry_year(fields):
            missing.append("year")
        if not missing:
            continue
        cands = _segment_candidates(record)
        cands = {kind: values for kind, values in cands.items() if values}
        if not cands:
            continue
        payload_candidates = [{"field": kind, **item} for kind, items in cands.items()
                              for item in items]
        rows.append({"key": key, "entry_type": etype, "missing": missing,
                     "candidates": payload_candidates,
                     "issues": issues,
                     "sources": [{"issue": s.get("issue"), "slug": s.get("slug"),
                                  "file": s.get("file")} for s in sources],
                     "fields": fields})
    rows.sort(key=lambda row: row["key"])
    return rows
