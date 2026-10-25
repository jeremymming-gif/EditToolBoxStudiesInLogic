"""bibdb 命令族（B-228 第一段）：`cmd_*` 子命令编排 + argparse 接线。

纯搬运自 v1.4.0-9 `Engine/bibdb.py`（B-228 第一段，零行为变化）：查询/体检/裁决/
规范化/合并/编辑/修复建议等子命令、`build_parser()` 与只被命令消费的行收集
（`collect_normalize_rows`）/ 查询 BibTeX 输出（`render_bibtex`）；`main()` 留在门面。
依赖方向 `bibdb_core ← bibdb_reports ← bibdb_import ← bibdb_cli`（无循环 import）。
函数体逐字节未改；唯一改动是模块头 import 与本 docstring。门面 re-export 全部旧名字。
"""
from __future__ import annotations

import argparse, copy, hashlib, json, os, re, sys

from slcn import bibcheck, bibdb, bibnorm, bibparse, fsutil
from slcn.bibdb_core import (
    _backup_dir_arg, _clip, _db_path, _fail, _field, _normalize_sha256_arg, _open_store,
    _print_json, _print_stats, _print_written, _resolve_report_out, _source_note,
    _write_db_backup, _write_guard,
)  # noqa: F401
from slcn.bibdb_reports import (
    EDITOR_REPORT_APPLY, EDITOR_REPORT_CSV, EDITOR_REPORT_DRY, EDITOR_REPORT_JSON,
    MERGE_FIELD_DIFF_NOTE_LIMIT, MERGE_PLAN_JSON, _BIBTEX_FIELD_ORDER, _author_stats,
    _build_bib_entry, _editor_stats, _merge_cluster_fields, _merge_conflicts_union,
    _merge_key_name, _merge_sources_union, _merge_version_score,
    _normalize_error_reason, _normalize_pending_conflict_fields, _normalize_rule,
    _normalize_stats, _public_conflict_item, _record_brief, _record_fields,
    _record_issues, _record_sources, _record_year, _value_shape_risks,
    _write_author_reports, _write_editor_reports, _write_merge_apply_reports,
    _write_merge_reports, _write_report, build_conflicts_md, build_duplicates_csv,
    build_duplicates_md, build_merge_plan, build_normalize_apply_json,
    build_normalize_apply_md, build_normalize_json, build_normalize_md,
    collect_author_rows, collect_conflict_items, collect_editor_rows,
    collect_field_fix_rows, conflict_totals,
)  # noqa: F401
from slcn.bibdb_import import (
    cmd_import_bib, cmd_import_dir, cmd_init, cmd_rehash,
)  # noqa: F401

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
    result = store.lint(with_records=bool(getattr(args, "with_records", False)))
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
    pending_conflicts = counts.get("conflicts_pending") or {}
    resolved_conflicts = counts.get("conflicts_resolved") or {}
    near_acked = counts.get("near_duplicates_acknowledged") or 0
    near_note = (f"（另有 {near_acked} 对已确认不同，见 health-ignore.json）"
                 if near_acked else "")
    print(f"条目 {counts['records']}（已核实 {counts['verified']} / "
          f"未核实 {counts['unverified']}）；"
          f"字段冲突：待裁决 {pending_conflicts.get('records', 0)} 条记录 / "
          f"{pending_conflicts.get('total', 0)} 处差异"
          f"（历史已裁决 {resolved_conflicts.get('records', 0)} 条记录 / "
          f"{resolved_conflicts.get('total', 0)} 处差异）；"
          f"同一作品多条记录 {counts['near_duplicates']} 对{near_note}")
    print(f"error {counts['errors']} 项，warning {counts['warnings']} 项"
          + ("——库可用。" if result["ok"] else "——**库有 error，先修**。"))
    return 0 if result["ok"] else 1


# ── B-205：重复/近重复告警的 GUI 裁决入口（写 sidecar，不改库）────────────────
#
# 用户诉求（`miaolitian≈miaolitian2`）：误报的「同作品多条记录」希望自己裁决「确认不同」。
# 引擎只做**落盘 + 校验**，绝不改库：写 `<库目录>/health-ignore.json`（B-204 的 sidecar），
# `lint` 下一次读取即不再提示；GUI 负责二次确认 / 自动本地 commit / 刷新
# （spec `EditToolBox-notes/drafts/specs/b205-ack-duplicate-gui.md` §2/§4）。
#
# 口径（与 GUI 契约逐条对应）：
#   · 默认 dry-run：只算 added / 条目数 / 库 sha，不建也不改 sidecar；`--apply` 才写；
#   · key 先 canonical（`_by_key`）后唯一 alias（`_by_alias`）；canonical 去重 + **排序后**落盘；
#   · sidecar 有任何问题（坏 JSON / version≠1 / 任一条目非法 / key 解析不了）→ rc2 **零写入**，
#     提示先修 sidecar（写动作绝不覆盖坏文件）；
#   · 已 ack（同 code + 同 canonical 键集）→ **先判幂等**：rc0、added=false、零写入；
#   · 存在性：near_duplicates 必须恰好 2 个不同 canonical key 且是 `Store._near_duplicates()`
#     当前未 ack 的 pair；duplicate_content 必须列全当前内容相同组（同 content_sha256）的全部 key；
#   · 库文件一个字节都不动：命令前后 sha 必相同（JSON 里带 db_sha256）。

ACK_DUPLICATES_DEFAULT_REASON = "人工确认：两条不同条目（不再提示）"


def _duplicate_content_groups(store) -> set:
    """当前「内容完全相同」的 canonical key 组（与 `Store.lint` 的 duplicate_content 同口径）。

    lint 只对「库内 content_sha256 有效且与重算值一致」的记录分组（stale sha 是 error，
    不当重复组）；ack 校验必须同一判据，否则会把 lint 根本不提示的组写进 sidecar。
    """
    sha_owner: dict[str, list[str]] = {}
    for record in store._records:
        if not isinstance(record, dict):
            continue
        key = str(record.get("key") or "")
        stored = record.get("content_sha256")
        if not key or not isinstance(stored, str) or not bibdb._HEX64_RE.match(stored):
            continue
        actual = bibdb.content_sha256(str(record.get("entry_type") or ""),
                                      record.get("fields") or {})
        if actual != stored:
            continue
        sha_owner.setdefault(actual, []).append(key)
    return {tuple(sorted(owners)) for owners in sha_owner.values() if len(owners) > 1}


def cmd_ack_duplicates(args) -> int:
    """确认「两条不同条目」→ 写入 sidecar（默认 dry-run；`--apply` 才写；**不写库**）。"""
    apply_mode = bool(getattr(args, "apply", False))
    if apply_mode and bool(getattr(args, "dry_run", False)):
        return _fail(args, "--apply 与 --dry-run 互斥：一次只能选一种模式")
    code = str(getattr(args, "code", "") or "")
    keys = [part.strip() for part in str(getattr(args, "keys", "") or "").split(",")
            if part.strip()]
    try:
        if code not in bibdb.HEALTH_IGNORE_CODES:
            raise bibdb.BibDBError(
                f"--code 只支持 {'/'.join(bibdb.HEALTH_IGNORE_CODES)}（收到 {code!r}）")
        if not keys:
            raise bibdb.BibDBError("--keys 至少要给 1 个 key（多个用逗号分隔）")
        path, store = _open_store(args)
        db_sha_before = bibdb.db_sha256(path)
        sidecar = bibdb.load_health_ignore_entries(path)
        health = bibdb.load_health_ignore(path, store._by_key, store._by_alias)
        sidecar_path = sidecar["path"] or bibdb.health_ignore_path(path)
        problems: list[str] = []
        for problem in list(sidecar["problems"]) + list(health["problems"]):
            if problem not in problems:      # 坏 JSON 两个读取口会各报一次，去重
                problems.append(problem)
        if problems:
            summary = "；".join(problems[:5]) + ("……" if len(problems) > 5 else "")
            raise bibdb.BibDBError(
                f"现有「已确认不同」清单有 {len(problems)} 处问题（{summary}）："
                f"先修 {sidecar_path} 再确认（本次未写入，绝不覆盖坏文件）")
        canonical_keys: list[str] = []
        key_problems: list[str] = []
        for raw in keys:
            canonical, problem = bibdb._resolve_health_key(
                raw, store._by_key, store._by_alias)
            if problem:
                key_problems.append(problem)
            elif canonical not in canonical_keys:
                canonical_keys.append(canonical)
        if key_problems:
            raise bibdb.BibDBError("；".join(key_problems))
        canonical_keys.sort()
        if code == "near_duplicates" and len(canonical_keys) != 2:
            raise bibdb.BibDBError(
                f"near_duplicates 必须恰好 2 个不同的 canonical key（实际 "
                f"{len(canonical_keys)} 个：{'、'.join(canonical_keys) or '（空）'}）")
        if code == "duplicate_content" and len(canonical_keys) < 2:
            raise bibdb.BibDBError(
                f"duplicate_content 至少要 2 个不同的 canonical key（实际 "
                f"{len(canonical_keys)} 个）")
        # 先判幂等：同一 code + 同一 canonical 键集已经在 sidecar 里 → 不再做存在性校验
        # （pair 可能早被 ack、后来库又变了；重复点按钮必须仍是 rc0 且零写入）。
        acked = next((entry for entry in health["acks"]
                      if entry.get("code") == code
                      and sorted(entry.get("resolved") or []) == canonical_keys), None)
        added = acked is None
        if added:
            if code == "near_duplicates":
                records = [record for record in store._records
                           if isinstance(record, dict)]
                raw_pairs, _total = store._near_duplicates(records, cap=1_000_000)
                acked_pairs = health["near_duplicates"]
                current = {frozenset((first, second)) for first, second in raw_pairs
                           if frozenset((first, second)) not in acked_pairs}
                if frozenset(canonical_keys) not in current:
                    raise bibdb.BibDBError(
                        f"`{'≈'.join(canonical_keys)}` 不是当前未确认的近重复对"
                        "（只能确认 lint 正在提示的 pair，每对恰好 2 条；"
                        "库有变化请刷新后重试）")
            elif tuple(canonical_keys) not in _duplicate_content_groups(store):
                raise bibdb.BibDBError(
                    "这些 key 与当前「内容完全相同」的一组记录不一致："
                    "duplicate_content 必须列全该组全部 canonical key"
                    "（组有变化请刷新后重试）")
        entries_before = len(sidecar["acknowledged"])
        entries_after = entries_before + (1 if added else 0)
        written = False
        if added and apply_mode:
            reason = (str(getattr(args, "reason", "") or "").strip()
                      or ACK_DUPLICATES_DEFAULT_REASON)
            entry = {"code": code, "keys": list(canonical_keys), "reason": reason,
                     "decided_at": bibdb._now_iso()}
            bibdb.write_health_ignore_entries(
                path, list(sidecar["acknowledged"]) + [entry])
            written = True
        db_sha_after = bibdb.db_sha256(path)
        if db_sha_after != db_sha_before:
            raise bibdb.BibDBError(
                "内部错误：确认不同不应改库，但库文件 sha256 变了（请检查库文件）")
    except bibdb.BibDBError as exc:
        return _fail(args, exc)
    payload = {
        "command": "ack-duplicates", "dry_run": not apply_mode, "db": path,
        "sidecar_path": sidecar_path, "code": code, "keys": keys,
        "canonical_keys": canonical_keys, "added": added, "written": written,
        "entries_before": entries_before, "entries_after": entries_after,
        "db_sha256": db_sha_after,
    }
    if args.json:
        _print_json(payload)
        return 0
    joined = "、".join(canonical_keys)
    if not added:
        print(f"已确认过（{code}）：{joined} —— 无需重复写入"
              f"（sidecar 条目 {entries_before} 条；库未改，sha256 {db_sha_after[:12]}…）")
    elif not apply_mode:
        print(f"计划确认不同（{code}）：{joined} → {sidecar_path}"
              f"（sidecar 条目 {entries_before}→{entries_after}；dry-run 未写入；"
              "确认无误加 --apply）")
    else:
        print(f"已确认不同（{code}）：{joined} → 已写入 {sidecar_path}"
              f"（sidecar 条目 {entries_before}→{entries_after}；库未改，"
              f"sha256 {db_sha_after[:12]}…）")
    return 0


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


def cmd_conflicts(args) -> int:
    """列出冲突条目（B-127，只读）：GUI「待裁决」页的数据源。

    复用 `collect_conflict_items()`（C 编号与 CONFLICTS.md 同源），`--pending-only`
    只返回待裁决；库缺失/损坏退出 2；无冲突退出 0 + 空数组。
    """
    try:
        path, store = _open_store(args)
        items = collect_conflict_items(store)
        totals = conflict_totals(store)
        db_sha = bibdb.db_sha256(path)
    except bibdb.BibDBError as exc:
        return _fail(args, exc)
    shown = ([item for item in items if item["pending"]]
             if args.pending_only else items)
    payload = {"db": path, "db_sha256": db_sha, "totals": totals,
               "items": [_public_conflict_item(item) for item in shown]}
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"库 sha256：{db_sha or '（读不到）'}")
    print(f"冲突 {totals['total']} 条（**待裁决 {totals['pending']}** / 已裁决 "
          f"{totals['resolved']}）" + ("——只列待裁决" if args.pending_only else ""))
    if not shown:
        print("（没有符合条件的条目）")
        return 0
    for item in shown:
        state = ("待裁决" if item["pending"]
                 else f"已裁决：{item['resolution'] or '—'}")
        where = "/".join(part for part in (item["issue"], item["slug"]) if part) or "（无期/篇）"
        print(f"  C{item['stable_no']:03d} · `{item['record_key']}` · {item['title']}"
              f"（{where}）· {state}")
    return 0


def _replace_from_bib(store, args) -> dict:
    """解析**恰好 1 条**人工裁决 bib → `store.resolve_replace`（B-127）。

    安全校验（任一不满足 → `BibDBError`，库一个字节都不动）：
      · bib 文件存在、可读；
      · 恰好解析出 1 条条目（0 条 / 多条都拒绝）；
      · 条目结构完好（花括号/引号配平）且无 `problem` 级语法发现；
      · **bib key 必须等于库内 record key**（裁决不改 key）。
    另算 bib 文件字节 sha256，写进冲突的 `resolved_bib_sha256`（审计证据）。
    """
    record_key = str(args.key or "").strip()
    if store.get(record_key) is None:
        raise bibdb.BibDBError(
            f"引用库里没有记录 key `{record_key}`——请用 CONFLICTS.md 里"
            "「库内记录」那个键（跨期撞车的记录可能带 -2 后缀）")
    bib_path = os.path.abspath(str(args.bib or "").strip())
    if not os.path.isfile(bib_path):
        raise bibdb.BibDBError(f"找不到裁决 bib：{bib_path}")
    text, text_warning = bibdb.read_bib_text(bib_path)
    if text_warning:
        print(f"⚠️ {text_warning}", file=sys.stderr)
    entries, findings = bibparse.parse_entries(text)
    if len(entries) != 1:
        raise bibdb.BibDBError(
            f"裁决 bib 必须**恰好 1 条**条目，实际解析出 {len(entries)} 条：{bib_path}")
    entry = entries[0]
    if entry.broken:
        raise bibdb.BibDBError(
            f"裁决 bib 的条目结构损坏（花括号/引号不配平），拒绝写入：{bib_path}")
    problems = [finding for finding in findings
                if getattr(finding, "level", "") == bibparse.LEVEL_PROBLEM]
    if problems:
        raise bibdb.BibDBError(
            f"裁决 bib 有语法问题，拒绝写入（{problems[0].detail}）：{bib_path}")
    entry_key = str(entry.key or "").strip()
    if entry_key != record_key:
        raise bibdb.BibDBError(
            f"裁决 bib 的 key `{entry_key}` 与库内记录 key `{record_key}` 不一致"
            "（裁决**不改 key**；请把 bib 条目的 key 改成库内 record key）")
    with open(bib_path, "rb") as handle:
        bib_sha = hashlib.sha256(handle.read()).hexdigest()
    result = store.resolve_replace(
        record_key, entry.etype, entry.fields,
        incoming_sha256=args.incoming_sha256 or "", bib_sha256=bib_sha,
        note=args.note or "", dry_run=bool(args.dry_run),
        allow_overlap=bool(args.allow_overlap))
    result["bib"] = bib_path
    return result


def _resolve_adopt_all(args) -> int:
    """`resolve --adopt-all-pending`（B-127）：待裁决冲突逐条按 adopt 语义一键采用。"""
    if str(args.key or "").strip() or str(args.action or "").strip():
        return _fail(args, "--adopt-all-pending 不能与 --key / --action 同时使用"
                           "（要么逐条裁决，要么一键全部采用）")
    if args.all_fields or str(args.fields or "").strip() \
            or str(args.incoming_sha256 or "").strip():
        return _fail(args, "--adopt-all-pending 不接受 --all-fields / --fields / "
                           "--incoming-sha256（它处理全部待裁决冲突）")
    if args.allow_overlap:
        return _fail(args, "--adopt-all-pending 不接受 --allow-overlap："
                           "同记录字段重叠会整体拒绝，请逐条处理")
    try:
        path, store = _open_store(args)
        before_sha, backup_dir = _write_guard(path, args)
        totals_before = conflict_totals(store)
        result = store.adopt_all_pending(note=args.note or "",
                                         dry_run=bool(args.dry_run))
        will_write = bool(result.get("changed")) and not bool(args.dry_run)
        backup_path = ""
        if will_write and backup_dir:
            backup_path = _write_db_backup(path, backup_dir, before_sha)
        written = False if args.dry_run else store.save()
        after_sha = bibdb.db_sha256(path)
        if args.dry_run:
            # dry-run 只算不写：按「将被采用的冲突数」推算总账（跳过项仍待裁决）
            applied = len(result["adopted"])
            totals_after = {
                "total": totals_before["total"],
                "pending": max(0, totals_before["pending"] - applied),
                "resolved": totals_before["resolved"] + applied,
            }
        else:
            totals_after = conflict_totals(store)
    except bibdb.BibDBError as exc:
        return _fail(args, exc)
    except OSError as exc:
        return _fail(args, exc)
    payload = {"db": path, "written": written, **result,
               "totals_before": totals_before, "totals_after": totals_after,
               "db_sha256_before": before_sha, "db_sha256_after": after_sha,
               "backup_path": backup_path}
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    if not result["adopted"] and not result["skipped"]:
        print("无变化：当前没有待裁决冲突。")
        return 0
    print(f"一键全部采用：{result['conflicts']} 条冲突（{result['records']} 条记录）")
    for row in result["adopted"]:
        print(f"  - `{row['record_key']}`（冲突 #{row['conflict_index']}，"
              f"指纹 {row['incoming_sha256'][:12]}…）：")
        for name, change in row["fields"].items():
            if change.get("remove"):
                print(f"      {name}：`{change['old']}` → （删除：新稿无此字段）")
            else:
                print(f"      {name}：`{change['old']}` → `{change['new']}`")
    for row in result["skipped"]:
        print(f"  ⚠️ 跳过 `{row['record_key']}` 冲突 #{row['conflict_index']}："
              f"{row['reason']}")
    if backup_path:
        print(f"备份：{backup_path}")
    if args.dry_run:
        print("（--dry-run：只算不写库；去掉该开关才会写）")
    else:
        print("已写库（单次原子写）。" if written else "库无变化（未重写）。")
    print(f"当前：冲突 {totals_after['total']} 条（待裁决 {totals_after['pending']} / "
          f"已裁决 {totals_after['resolved']}）")
    print("提示：本次批量裁决已写进 conflicts[].resolution/resolved_at，重新生成清单不会丢。")
    return 0


def cmd_resolve(args) -> int:
    """记录冲突裁决：keep/defer 只改 conflicts 元数据；adopt **逐字段**改 fields；
    replace 采用**人工裁决 bib**（B-127）；`--adopt-all-pending` 一键批量采用。"""
    if args.adopt_all_pending:
        return _resolve_adopt_all(args)
    fields = [part.strip() for part in (args.fields or "").split(",") if part.strip()]
    if not str(args.key or "").strip() or not str(args.action or "").strip():
        return _fail(args, "resolve 需要 --key 与 --action"
                           "（或用 --adopt-all-pending 一键全部采用）")
    if args.action == "adopt":
        if not (args.incoming_sha256 or "").strip():
            return _fail(args, "--action adopt 必须配合 --incoming-sha256"
                               "（冲突指纹）精确定位一条冲突")
        if args.all_fields or fields:
            return _fail(args, "--action adopt 只接受 --incoming-sha256；"
                               "不要同时给 --fields / --all-fields")
    elif args.action == "replace":
        if not (args.incoming_sha256 or "").strip():
            return _fail(args, "--action replace 必须配合 --incoming-sha256"
                               "（冲突指纹）精确定位一条冲突")
        if args.all_fields or fields:
            return _fail(args, "--action replace 只接受 --incoming-sha256；"
                               "不要同时给 --fields / --all-fields")
        if not str(args.bib or "").strip():
            return _fail(args, "--action replace 必须配合 --bib（人工裁决 bib 文件路径）")
    elif args.allow_overlap:
        return _fail(args, "--allow-overlap 只用于 --action adopt / replace"
                           "（keep/defer 没有重叠问题）")
    try:
        path, store = _open_store(args)
        before_sha, backup_dir = _write_guard(path, args)
        if args.action == "replace":
            result = _replace_from_bib(store, args)
        else:
            result = store.resolve_conflicts(
                args.key, args.action, fields=fields,
                all_fields=bool(args.all_fields), note=args.note or "",
                dry_run=bool(args.dry_run),
                incoming_sha256=args.incoming_sha256 or "",
                allow_overlap=bool(args.allow_overlap))
        # 备份只在**真的会产生写入**时生成（dry-run / 幂等无变化 → 不产生备份文件）
        will_write = bool(result.get("changed")) and not bool(args.dry_run)
        backup_path = ""
        if will_write and backup_dir:
            backup_path = _write_db_backup(path, backup_dir, before_sha)
        written = False if args.dry_run else store.save()
        after_sha = bibdb.db_sha256(path)
        totals = conflict_totals(store)
    except bibdb.BibDBError as exc:
        return _fail(args, exc)
    except OSError as exc:
        return _fail(args, exc)
    payload = {"db": path, "written": written, **result, "totals": totals,
               "db_sha256_before": before_sha, "db_sha256_after": after_sha,
               "backup_path": backup_path}
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"记录：`{result['key']}`；动作：{result['action']} → "
          f"resolution=`{result['resolution']}`"
          + (f"（来源 `{result['resolved_by']}`）" if result.get("resolved_by") else ""))
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
    if result["action"] in ("adopt", "replace"):
        if result["action"] == "adopt":
            print("采用新稿值（**逐字段**；只改这些字段）：")
            changes = result.get("adopted_fields") or {}
        else:
            print("采用人工裁决 bib（**整条 entry_type+fields**；将写这些内容）：")
            if result.get("bib"):
                print(f"   bib：{result['bib']}")
            changes = result.get("replaced_fields") or {}
        for name, change in changes.items():
            if change.get("remove"):
                print(f"   - {name}：`{change['old']}` → （删除：新稿无此字段）")
            else:
                print(f"   - {name}：`{change['old']}` → `{change['new']}`")
        print(f"   content_sha256：{result['content_sha256_before'][:12]}… → "
              f"{result['content_sha256_after'][:12]}…")
        if result.get("overlapping"):
            if result.get("superseded"):
                names = "、".join(f"#{item['index']}" for item in result["superseded"])
                print(f"   ⚠️ 重叠冲突已标 `superseded`（被本次裁决取代）：{names}")
            else:
                names = "、".join(f"#{item['index']}" for item in result["overlapping"])
                print(f"   ⚠️ 有 {len(result['overlapping'])} 条重叠冲突将被标 `superseded`"
                      f"（--dry-run 未写）：{names}")
    if backup_path:
        print(f"备份：{backup_path}")
    if args.dry_run:
        print("（--dry-run：只算不写库；去掉该开关才会写）")
    else:
        print("已写库。" if written else "库无变化（未重写）。")
    print(f"当前：冲突 {totals['total']} 条（待裁决 {totals['pending']} / "
          f"已裁决 {totals['resolved']}）")
    print("提示：清单里的勾选只是草稿；本次裁决已写进 conflicts[].resolution/resolved_at，"
          "重新生成清单不会丢。")
    return 0


def cmd_restore(args) -> int:
    """从备份恢复整库（B-127）：校验备份结构 + 可选 expect sha → 原子写回。

    安全语义：
      · 备份必须能被 `Store.load()` 完整读出（行级 schema 校验；失败 → 退出 2，不恢复）；
      · `--expect-db-sha256` 与当前库字节 sha256 不一致 → 退出 2、零写入；
      · `--dry-run` 只校验/预览，不写库；
      · 写回用 `fsutil.atomic_write`（同目录临时文件 + fsync + rename），写后立即校验
        库字节 sha256 与备份一致；
      · **不做 reopen**：已裁决冲突不会单独重开，整库回到备份时的状态（P4 拍板口径）。
    """
    backup_path = os.path.abspath(str(args.backup or "").strip())
    if not os.path.isfile(backup_path):
        return _fail(args, f"找不到备份文件：{backup_path}")
    try:
        db_path = _db_path(args)
        current_sha = bibdb.db_sha256(db_path)
        expect = _normalize_sha256_arg(args.expect_db_sha256, "--expect-db-sha256")
        if expect:
            if not current_sha:
                raise bibdb.BibDBError(
                    f"库文件不存在，无法校验 --expect-db-sha256：{db_path}")
            if current_sha != expect:
                raise bibdb.BibDBError(
                    f"库 sha256 与 --expect-db-sha256 不符（当前 {current_sha[:12]}…，期望 "
                    f"{expect[:12]}…）：库可能已被其它操作修改，请刷新后重试（本次未恢复）")
        try:
            backup_store = bibdb.Store.load(backup_path)
        except bibdb.BibDBError as exc:
            raise bibdb.BibDBError(f"备份文件校验失败（本次未恢复）：{exc}") from exc
        try:
            with open(backup_path, "rb") as handle:
                backup_bytes = handle.read()
            backup_sha = hashlib.sha256(backup_bytes).hexdigest()
            text = backup_bytes.decode("utf-8")
        except OSError as exc:
            raise bibdb.BibDBError(
                f"读不了备份文件：{backup_path}（{exc}）") from exc
        except UnicodeDecodeError as exc:
            raise bibdb.BibDBError(
                f"备份文件不是 UTF-8 文本，无法恢复：{exc}") from exc
        written = False
        if not args.dry_run and current_sha != backup_sha:
            fsutil.atomic_write(db_path, text, keep_backups=0)
            written = True
            if bibdb.db_sha256(db_path) != backup_sha:
                raise bibdb.BibDBError(
                    "恢复写回校验失败：库 sha256 与备份不一致（请重新选择备份恢复）")
        after_sha = bibdb.db_sha256(db_path)
    except bibdb.BibDBError as exc:
        return _fail(args, exc)
    except OSError as exc:
        return _fail(args, exc)
    payload = {"db": db_path, "backup_path": backup_path, "written": written,
               "dry_run": bool(args.dry_run), "records": len(backup_store.records()),
               "backup_sha256": backup_sha,
               "db_sha256_before": current_sha, "db_sha256_after": after_sha}
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{db_path}")
    print(f"备份：{backup_path}（{payload['records']} 条记录；sha256: {backup_sha[:12]}…）")
    if args.dry_run:
        print("（--dry-run：只校验备份、不写库；去掉该开关才会恢复）")
    elif written:
        print("已恢复（原子写回；整库回到备份状态，不做 reopen）。")
    else:
        print("库内容已与备份一致，无需重写。")
    print(f"库 sha256：{current_sha[:12]}… → {after_sha[:12]}…")
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
            "normalized_fields": None, "normalized_entry_type": "",
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
            row["normalized_fields"] = dict(sorted(after.fields.items()))
            row["normalized_entry_type"] = str(after.etype or "").strip().lower()
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


def _raw_field_diff(old_fields: dict, new_fields: dict) -> dict[str, dict]:
    """只按原始字符串比较的字段差异（用于「仅格式/原始值」的行）。"""
    diff: dict[str, dict] = {}
    for name in sorted(set(old_fields) | set(new_fields)):
        old_value = str(old_fields.get(name, ""))
        new_value = str(new_fields.get(name, ""))
        if old_value != new_value:
            diff[name] = {"old": old_value, "new": new_value}
    return diff


def apply_normalize_rows(store: "bibdb.Store", rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """把安全条目的规范化结果写进库（**只改 entry_type/fields/content_sha256/updated_at**）。

    逐条结构安全：解析失败（`status="error"`）或存在高危形状风险（`risks`）→ **不 apply**，
    返回在 skipped 里；待裁决冲突字段将被改动的也不 apply（避免冲突旧值过期）。
    返回 (applied, skipped)。
    """
    applied: list[dict] = []
    skipped: list[dict] = []
    now = bibdb._now_iso()
    for row in rows:
        key = str(row["key"])
        record = store.get(key)
        if record is None:
            skipped.append({"key": key, "reason": "库内找不到该记录（不应发生）"})
            continue
        if row["status"] == "error":
            skipped.append({"key": key,
                            "reason": row.get("error") or "重建/规范化/解析失败"})
            continue
        if row["risks"]:
            skipped.append({"key": key,
                            "reason": "高危形状风险：" + "；".join(row["risks"])})
            continue
        after_fields = dict(sorted((row.get("normalized_fields") or {}).items()))
        after_type = str(row.get("normalized_entry_type") or "").strip().lower()
        old_fields = dict(record.get("fields") or {})
        old_type = str(record.get("entry_type") or "").strip().lower()
        if after_fields == old_fields and after_type == old_type:
            continue                       # 语义与原始值都无需改动（仅有/无排版差异）
        pending = _normalize_pending_conflict_fields(record)
        diff = (row["diff"] if row["diff"]
                else _raw_field_diff(old_fields, after_fields))
        overlap = sorted(set(diff) & pending)
        if overlap:
            skipped.append({"key": key,
                            "reason": "待裁决冲突字段将被改动（先处理冲突）："
                                      + "、".join(overlap)})
            continue
        record["entry_type"] = after_type or record.get("entry_type")
        record["fields"] = after_fields
        record["content_sha256"] = bibdb.content_sha256(record["entry_type"], after_fields)
        record["updated_at"] = now
        applied.append({
            "key": key,
            "entry_type": record["entry_type"],
            "semantic": row["status"] == "semantic",
            "rules": list(row["rules"]),
            "diff": diff,
        })
    if applied:
        store.dirty = True          # 上面直接改了 record dict，save() 靠 dirty 判断
        store.save()
    return applied, skipped


def _normalize_verification(path: str, skipped_keys: set[str]) -> dict:
    """apply 后重跑 dry-run 做二次校验：剩余语义变更应为 0（被排除的条目除外）。"""
    store = bibdb.Store.load(path)
    rows = collect_normalize_rows(store)
    stats = _normalize_stats(rows)
    remaining = [row for row in stats["semantic_rows"] if row["key"] not in skipped_keys]
    return {
        "records": stats["records"],
        "semantic": stats["semantic"],
        "format_only": stats["format_only"],
        "errors": stats["errors"],
        "semantic_excluding_skipped": len(remaining),
        "remaining_keys": [row["key"] for row in remaining][:20],
        "error_keys": [row["key"] for row in stats["error_rows"]],
    }


def _cmd_normalize_apply(path: str, store: "bibdb.Store", args,
                          sha_before: str, rows: list[dict], stats: dict) -> int:
    """`normalize --apply` 的执行部分：安全条目写库 + 报告 + 二次校验。"""
    backup_path = str(getattr(args, "backup", "") or "")
    backup_sha = bibdb.db_sha256(backup_path) if backup_path else ""
    applied, skipped = apply_normalize_rows(store, rows)
    sha_after = bibdb.db_sha256(path)
    verification = _normalize_verification(path, {item["key"] for item in skipped})
    report_path = str(args.report or "")
    out_dir = str(args.out_dir or "")
    if not report_path and out_dir:
        report_path = os.path.join(out_dir, "B1-apply-report.md")
    json_path = str(args.json_out or "")
    if not json_path and out_dir:
        json_path = os.path.join(out_dir, "B1-apply-report.json")
    written: list[dict] = []
    if report_path:
        written.append(_write_report(
            report_path,
            build_normalize_apply_md(path, stats, applied, skipped, verification,
                                     sha_before, sha_after, backup_path, backup_sha)))
    if json_path:
        written.append(_write_report(
            json_path,
            build_normalize_apply_json(path, stats, applied, skipped, verification,
                                       sha_before, sha_after, backup_path, backup_sha)))
    payload = {
        "db": path,
        "mode": "apply",
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "backup_path": backup_path,
        "backup_sha256": backup_sha,
        "records": stats["records"],
        "applied": len(applied),
        "applied_semantic": sum(1 for item in applied if item["semantic"]),
        "applied_field_changes": sum(len(item["diff"]) for item in applied),
        "skipped": len(skipped),
        "skipped_entries": skipped,
        "verification": verification,
        "written": written,
    }
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"库 sha256（apply 前/后）：{sha_before} / {sha_after}")
    print(f"备份：{backup_path}（sha256: {backup_sha}，与 apply 前一致）")
    print(f"记录：{stats['records']} 条 → apply {len(applied)} 条"
          f"（语义 {payload['applied_semantic']} + 仅原始值 {len(applied)-payload['applied_semantic']}；"
          f"共改 {payload['applied_field_changes']} 处字段）；未 apply {len(skipped)} 条")
    print(f"二次校验（重跑 dry-run）：语义变更 {verification['semantic']} 条"
          f"（排除未 apply 后剩余 {verification['semantic_excluding_skipped']} 条）/ "
          f"仅格式 {verification['format_only']} / 异常 {verification['errors']}")
    for item in written:
        state = "已写入" if item["written"] else "内容未变（未重写）"
        print(f"   {state}：{item['path']}（{item['bytes']:,} 字节，sha256: {item['sha256']}）")
    if skipped:
        print("   未 apply（需人工处理）：" + "、".join(item["key"] for item in skipped[:10])
              + ("…" if len(skipped) > 10 else ""))
    print("   （只改了 entry_type/fields/content_sha256/updated_at；其余元数据未动）")
    return 0


# ── normalize-bib（B-209）：外部 .bib 只读规范化 ────────────────────────────
#
# 规范化规则**只有一份**（`slcn.bibnorm.normalize_bib()`）；这里只是外部 bib 的读写壳
# + 逐条字段 diff / 缺字段风险归组。不碰库、不落报告、不新增判据：`--bib`/`--stdin`
# 的文本原样交给同一实现，GUI 预览与初排产物因此不会出现两套口径。
# 引号未闭合（`parse_fields` 抛 `ValueError`）时逐条降级：坏条目原样保留、好条目照常
# 规范化；整段 `normalize_bib()` 会直接抛错，所以这里按 `split_entries()` 的逐条来。

_NON_BIB_ENTRY_RE = re.compile(r"@\s*(?:string|preamble|comment)\s*\{", re.I)
_QUOTE_KEPT_RISK = "字段值引号未闭合，条目原样保留（需人工核对）"


def _entry_identity(head: str) -> tuple[str, str]:
    """从条目头取 `(key, 小写 entry_type)`；解析不了时返回空串（不改原文）。"""
    m = re.match(r"@(\w+)\s*\{\s*([^,]+)\s*,", head)
    return (m.group(2).strip(), m.group(1).lower()) if m else ("", "")


def _fields_by_name(fields_text: str) -> dict[str, str]:
    """字段名小写 → 值；同名重复以**首个**为准（改名不猜映射：旧删 + 新增）。"""
    out: dict[str, str] = {}
    for name, value in bibnorm.parse_fields(fields_text):
        out.setdefault(name.lower(), value)
    return out


def _fields_diff(old: dict[str, str], new: dict[str, str]) -> list[dict]:
    """逐字段前/后 diff（只列变化；新增 old=null、删除 new=null）。"""
    changes: list[dict] = []
    for name, old_value in old.items():
        if name not in new:
            changes.append({"name": name, "old": old_value, "new": None})
        elif new[name] != old_value:
            changes.append({"name": name, "old": old_value, "new": new[name]})
    for name, new_value in new.items():
        if name not in old:
            changes.append({"name": name, "old": None, "new": new_value})
    return changes


def _normalized_entry_fields(normalized: str) -> dict[str, str]:
    """规范化后单条条目的字段表（调用方保证只含这一条）。"""
    entries = bibnorm.split_entries(normalized)
    return _fields_by_name(entries[0][1]) if entries else {}


def _build_normalize_bib_payload(entries: list[tuple[str, str, str]]) -> dict:
    """逐条调用唯一实现 `normalize_bib()`，拼回规范文本并算 diff/风险。"""
    parts: list[str] = []
    rows: list[dict] = []
    top_notes: list[str] = []
    for head, fields_text, tail in entries:
        raw = head + fields_text + tail
        key, entry_type = _entry_identity(head)
        try:
            normalized, notes = bibnorm.normalize_bib(raw)
            changes = _fields_diff(_fields_by_name(fields_text),
                                   _normalized_entry_fields(normalized))
            body = normalized[:-1] if normalized.endswith("\n") else normalized
            status = "changed" if body != raw else "unchanged"
        except ValueError:          # 引号未闭合：整条原样保留（不截断、不丢条目）
            normalized, notes, changes, status = raw, [], [], "kept"
        entry_notes: list[str] = []
        for note in notes:
            if key and note.startswith(f"{key}: "):
                entry_notes.append(note)
            else:
                top_notes.append(note)
        rows.append({"key": key, "entry_type": entry_type, "status": status,
                     "changed_fields": changes, "notes": entry_notes,
                     "risks": [], "missing_required": []})
        parts.append(normalized)
    normalized_text = "\n".join(parts)

    # 缺字段判据**复用** bibcheck.check() 的 must 级 missing/empty（不另写 REQUIRED）。
    missing: dict[str, list[str]] = {}
    empty: dict[str, list[str]] = {}
    for issue in bibcheck.check(normalized_text).issues:
        if issue.severity != "must" or issue.kind not in ("missing", "empty"):
            continue
        bucket = missing if issue.kind == "missing" else empty
        fields = bucket.setdefault(issue.key, [])
        if issue.detail not in fields:
            fields.append(issue.detail)
    for row in rows:
        absent = missing.get(row["key"], [])
        blank = empty.get(row["key"], [])
        risks = [_QUOTE_KEPT_RISK] if row["status"] == "kept" else []
        if absent:
            risks.append("缺必填字段：" + "、".join(absent))
        if blank:
            risks.append("必填字段值为空：" + "、".join(blank))
        row["risks"] = risks
        row["missing_required"] = absent + blank

    return {
        "entries_in": len(entries),
        "entries_out": len(parts),
        "changed_entries": sum(1 for row in rows if row["status"] == "changed"),
        "unchanged_entries": sum(1 for row in rows if row["status"] == "unchanged"),
        "kept_entries": sum(1 for row in rows if row["status"] == "kept"),
        "normalized_text": normalized_text,
        "entries": rows,
        "notes": top_notes,
    }


def cmd_normalize_bib(args) -> int:
    """B-209 只读：外部 .bib → 规范文本 / 逐条 diff（唯一实现 = `bibnorm.normalize_bib`）。"""
    text_warning = ""
    if args.stdin:
        try:
            text = sys.stdin.buffer.read().decode("utf-8")
        except UnicodeDecodeError:
            print("✗ stdin 不是 UTF-8 文本（本命令只收 UTF-8）", file=sys.stderr)
            return 2
    else:
        bib_path = os.path.abspath(args.bib)
        if not os.path.isfile(bib_path):
            print(f"✗ 找不到要规范化的 bib：{bib_path}", file=sys.stderr)
            return 2
        try:
            text, text_warning = bibdb.read_bib_text(bib_path)
        except bibdb.BibDBError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2
    if _NON_BIB_ENTRY_RE.search(bibnorm.strip_bib_comments(text)):
        print("✗ 含 @string/@preamble/@comment：本命令只收纯文献条目，"
              "请先移除这些非文献块", file=sys.stderr)
        return 2
    entries = bibnorm.split_entries(text)
    if not entries:
        print("✗ 没有条目（输入为空或只有整行注释）", file=sys.stderr)
        return 2
    if text_warning:        # 拒绝路径只留一行原因；告警只在真能出结果时提示
        print(f"⚠️ {text_warning}", file=sys.stderr)
    payload = _build_normalize_bib_payload(entries)
    # stderr 只放计数（不打印 bib 正文/字段值；GUI 不解析这行）。
    print(f"normalize-bib：{payload['entries_in']} 条 → changed "
          f"{payload['changed_entries']} / unchanged {payload['unchanged_entries']}"
          f" / kept {payload['kept_entries']}", file=sys.stderr)
    if args.json:
        _print_json(payload)
    else:
        # 直接写 UTF-8 字节：Windows 的文本 stdout 会把 `\n` 翻成 `\r\n`，那样 stdout
        # 就不再逐字节等于 `normalize_bib()[0].encode("utf-8")` 了（本命令的硬契约）。
        data = payload["normalized_text"].encode("utf-8")
        out = getattr(sys.stdout, "buffer", None)
        if out is None:
            sys.stdout.write(payload["normalized_text"])
        else:
            sys.stdout.flush()
            out.write(data)
            out.flush()
    return 0


def cmd_normalize(args) -> int:
    """B1 确定性规范化 dry-run：只出报告，**绝不写 bibliography.jsonl**。"""
    try:
        path, store = _open_store(args)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    apply_mode = bool(getattr(args, "apply", False))
    sha_before = bibdb.db_sha256(path)
    if apply_mode:
        if bool(getattr(args, "dry_run", False)):
            print("✗ --apply 与 --dry-run 互斥：一次只能选一种模式", file=sys.stderr)
            return 2
        backup_path = str(getattr(args, "backup", "") or "")
        if not backup_path:
            print("✗ --apply 会写真实库，必须先备份并用 --backup PATH 指定备份文件"
                  "（例：cp bibliography.jsonl /tmp/xxx.jsonl）", file=sys.stderr)
            return 2
        if not os.path.isfile(backup_path):
            print(f"✗ 备份文件不存在：{backup_path}", file=sys.stderr)
            return 2
        backup_sha = bibdb.db_sha256(backup_path)
        if backup_sha != sha_before:
            print(f"✗ 备份 sha256 与当前库不一致（备份 {backup_sha} / 当前 {sha_before}）——"
                  "请重新备份", file=sys.stderr)
            return 2
        rows = collect_normalize_rows(store)
        stats = _normalize_stats(rows)
        return _cmd_normalize_apply(path, store, args, sha_before, rows, stats)
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


def _author_verification(path: str, skip_keys: set[str]) -> dict:
    store = bibdb.Store.load(path)
    rows = collect_author_rows(store)
    stats = _author_stats(rows)
    remaining = [row for row in stats["change_rows"] if row["key"] not in skip_keys]
    return {
        "with_author": stats["with_author"],
        "changes": stats["changes"],
        "skips": stats["skips"],
        "remaining": len(remaining),
        "remaining_keys": [row["key"] for row in remaining][:20],
    }


def apply_author_rows(store: "bibdb.Store", rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """把安全的 author 倒装结果写进库（只改 author + content_sha256 + updated_at）。"""
    applied: list[dict] = []
    skipped: list[dict] = []
    now = bibdb._now_iso()
    for row in rows:
        if row["status"] != "change":
            skipped.append({"key": row["key"], "reason": row["reason"]})
            continue
        record = store.get(row["key"])
        if record is None:
            skipped.append({"key": row["key"], "reason": "库内找不到该记录（不应发生）"})
            continue
        pending = _normalize_pending_conflict_fields(record)
        if "author" in pending:
            skipped.append({"key": row["key"], "reason": "待裁决冲突字段（先处理冲突）"})
            continue
        fields = dict(record.get("fields") or {})
        fields["author"] = row["new"]
        record["fields"] = dict(sorted(fields.items()))
        record["content_sha256"] = bibdb.content_sha256(record.get("entry_type") or "",
                                                        record["fields"])
        record["updated_at"] = now
        applied.append({"key": row["key"], "old": row["old"], "new": row["new"]})
    if applied:
        store.dirty = True
        store.save()
    return applied, skipped


def cmd_normalize_authors(args) -> int:
    """B2 作者倒装：默认 dry-run；`--apply` 才写库（只改 author）。"""
    apply_mode = bool(getattr(args, "apply", False))
    if apply_mode and bool(getattr(args, "dry_run", False)):
        print("✗ --apply 与 --dry-run 互斥：一次只能选一种模式", file=sys.stderr)
        return 2
    try:
        path, store = _open_store(args)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    sha_before = bibdb.db_sha256(path)
    backup_path = str(getattr(args, "backup", "") or "")
    backup_sha = ""
    if apply_mode:
        if not backup_path:
            print("✗ --apply 会写真实库，必须先备份并用 --backup PATH 指定", file=sys.stderr)
            return 2
        if not os.path.isfile(backup_path):
            print(f"✗ 备份文件不存在：{backup_path}", file=sys.stderr)
            return 2
        backup_sha = bibdb.db_sha256(backup_path)
        if backup_sha != sha_before:
            print(f"✗ 备份 sha256 与当前库不一致（备份 {backup_sha} / 当前 {sha_before}）",
                  file=sys.stderr)
            return 2
    rows = collect_author_rows(store)
    stats = _author_stats(rows)
    if not apply_mode:
        written = _write_author_reports(args, store, path, sha_before, stats,
                                        applied=False)
        sha_after = bibdb.db_sha256(path)
        payload = {
            "db": path, "mode": "dry-run",
            "sha256_before": sha_before, "sha256_after": sha_after,
            "db_unchanged": sha_before == sha_after,
            "with_author": stats["with_author"], "changes": stats["changes"],
            "skips": stats["skips"], "reasons": stats["reasons"],
            "written": written,
        }
        if args.json:
            _print_json(payload)
            return 0
        print(f"库：{path}")
        print(f"库 sha256：{sha_before}（dry-run 未改动：{sha_before == sha_after}）")
        print(f"有 author 的记录：{stats['with_author']} 条 → 将倒装 {stats['changes']} 条 / "
              f"跳过 {stats['skips']} 条")
        for item in written:
            print(f"   已写入：{item['path']}（{item['bytes']:,} 字节）")
        print("   跳过原因 Top：" + "、".join(
            f"{reason} {count}" for reason, count in list(stats["reasons"].items())[:6]))
        print("   （dry-run：未修改 bibliography.jsonl；--apply 才写库）")
        return 0
    applied_rows, skipped_apply = apply_author_rows(store, rows)
    verification = _author_verification(path, {item["key"] for item in skipped_apply})
    written = _write_author_reports(args, store, path, sha_before, stats,
                                    applied=True, applied_rows=applied_rows,
                                    skipped_apply=skipped_apply,
                                    verification=verification,
                                    backup_path=backup_path, backup_sha=backup_sha)
    sha_after = bibdb.db_sha256(path)
    payload = {
        "db": path, "mode": "apply",
        "sha256_before": sha_before, "sha256_after": sha_after,
        "backup_path": backup_path, "backup_sha256": backup_sha,
        "with_author": stats["with_author"], "applied": len(applied_rows),
        "skipped": len(skipped_apply), "reasons": stats["reasons"],
        "applied_entries": applied_rows, "skipped_entries": skipped_apply,
        "verification": verification, "written": written,
    }
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"库 sha256（apply 前/后）：{sha_before} / {sha_after}")
    print(f"备份：{backup_path}（sha256: {backup_sha}）")
    print(f"有 author 的记录：{stats['with_author']} 条 → 已倒装 {len(applied_rows)} 条 / "
          f"跳过 {len(skipped_apply)} 条")
    print(f"二次校验（重跑 dry-run）：剩余将倒装 {verification['remaining']} 条（应为 0）")
    for item in written:
        print(f"   已写入：{item['path']}（{item['bytes']:,} 字节）")
    print("   跳过原因 Top：" + "、".join(
        f"{reason} {count}" for reason, count in list(stats["reasons"].items())[:6]))
    print("   （只改了 author / content_sha256 / updated_at；其余元数据未动）")
    return 0


def _editor_verification(rows: list[dict]) -> dict:
    """二次幂等预演（内存里做，不写库）：把「新值」再喂同一个规范化器，应 0 条再变。"""
    changes = [row for row in rows if row["status"] == "change"]
    remaining = [row for row in changes
                 if bibnorm.normalize_author_field(row["new"]) != row["new"]]
    return {
        "checked": len(changes),
        "remaining": len(remaining),
        "remaining_keys": [row["key"] for row in remaining][:20],
    }


def apply_editor_rows(store: "bibdb.Store", rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """把安全的 editor 倒装结果写进库（只改 editor + content_sha256 + updated_at）。"""
    applied: list[dict] = []
    skipped: list[dict] = []
    now = bibdb._now_iso()
    for row in rows:
        if row["status"] != "change":
            skipped.append({"key": row["key"], "reason": row["reason"]})
            continue
        record = store.get(row["key"])
        if record is None:
            skipped.append({"key": row["key"], "reason": "库内找不到该记录（不应发生）"})
            continue
        pending = _normalize_pending_conflict_fields(record)
        if "editor" in pending:
            skipped.append({"key": row["key"], "reason": "待裁决冲突字段（先处理冲突）"})
            continue
        fields = dict(record.get("fields") or {})
        fields["editor"] = row["new"]
        record["fields"] = dict(sorted(fields.items()))
        record["content_sha256"] = bibdb.content_sha256(record.get("entry_type") or "",
                                                        record["fields"])
        record["updated_at"] = now
        applied.append({"key": row["key"], "old": row["old"], "new": row["new"]})
    if applied:
        store.dirty = True
        store.save()
    return applied, skipped


def _editor_apply_verification(path: str, skip_keys: set[str]) -> dict:
    """apply 后重跑一遍（只读）：仍将倒装的条数（排除本次跳过/冲突项，应为 0）。"""
    store = bibdb.Store.load(path)
    records_total = sum(1 for record in store.records() if isinstance(record, dict))
    rows = collect_editor_rows(store)
    stats = _editor_stats(rows, records_total)
    remaining = [row for row in stats["change_rows"] if row["key"] not in skip_keys]
    return {
        "with_editor": stats["with_editor"],
        "changes": stats["changes"],
        "skips": stats["skips"],
        "remaining": len(remaining),
        "remaining_keys": [row["key"] for row in remaining][:20],
        "risk_flags": stats["risk_flags"],
    }


def cmd_normalize_editors(args) -> int:
    """B3 编者倒装：默认 dry-run；`--apply` 才写库（只改 editor + sha/updated_at）。"""
    apply_mode = bool(getattr(args, "apply", False))
    if apply_mode and bool(getattr(args, "dry_run", False)):
        print("✗ --apply 与 --dry-run 互斥：一次只能选一种模式", file=sys.stderr)
        return 2
    try:
        path, store = _open_store(args)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    sha_before = bibdb.db_sha256(path)
    backup_path = str(getattr(args, "backup", "") or "")
    backup_sha = ""
    if apply_mode:
        if not backup_path:
            print("✗ --apply 会写真实库，必须先备份并用 --backup PATH 指定", file=sys.stderr)
            return 2
        if not os.path.isfile(backup_path):
            print(f"✗ 备份文件不存在：{backup_path}", file=sys.stderr)
            return 2
        backup_sha = bibdb.db_sha256(backup_path)
        if backup_sha != sha_before:
            print(f"✗ 备份 sha256 与当前库不一致（备份 {backup_sha} / 当前 {sha_before}）",
                  file=sys.stderr)
            return 2
    records_total = sum(1 for record in store.records() if isinstance(record, dict))
    rows = collect_editor_rows(store)
    stats = _editor_stats(rows, records_total)
    if not apply_mode:
        verification = _editor_verification(rows)
        sha_after = bibdb.db_sha256(path)
        if sha_before != sha_after:    # 防御式：dry-run 按设计只读，不该发生
            print("✗ 内部错误：normalize-editors 运行期间库文件被改动（dry-run 不应写库）",
                  file=sys.stderr)
            return 2
        written = _write_editor_reports(args, path, stats, sha_before, sha_after,
                                        verification, applied=False)
        payload = {
            "db": os.path.basename(path) or path,
            "mode": "dry-run",
            "apply_implemented": True,
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
            "multi_editor_changes": stats["multi_editor_changes"],
            "verification": verification,
            "written": written,
        }
        if args.json:
            _print_json(payload)
            return 0
        print(f"库：{path}")
        print(f"库 sha256（跑前/跑后）：{sha_before} / {sha_after}"
              + ("（一致 = 未改动库）" if sha_before == sha_after else "（⚠️ 不一致！）"))
        print(f"记录：{stats['records']} 条；有 editor 字段：{stats['with_editor']} 条"
              f"（其中只有 editor、没有 author：{stats['editor_only']} 条）")
        print(f"→ 将倒装 {stats['changes']} 条 / 跳过 {stats['skips']} 条")
        print("   跳过原因 Top：" + "、".join(
            f"{reason} {count}" for reason, count in list(stats["reasons"].items())[:6]))
        if stats["risk_flags"]:
            print("   风险标记：" + "、".join(
                f"{flag} {count}" for flag, count in list(stats["risk_flags"].items())[:8]))
        print(f"   二次校验（内存预演）：把新值再跑同一个规范化器，剩余将变 "
              f"{verification['remaining']} 条（应为 0）")
        for item in written:
            state = "已写入" if item["written"] else "内容未变（未重写）"
            print(f"   {state}：{item['path']}（{item['bytes']:,} 字节，sha256: {item['sha256']}）")
        print("   （dry-run：未修改 bibliography.jsonl；--apply 才写库）")
        return 0
    # ── apply ──
    applied_rows, skipped_apply = apply_editor_rows(store, rows)
    sha_after = bibdb.db_sha256(path)          # ⚠️ 先算 apply 后 sha，报告里必须写真实值
    verification = _editor_apply_verification(path, {item["key"] for item in skipped_apply})
    written = _write_editor_reports(args, path, stats, sha_before, sha_after,
                                    verification, applied=True,
                                    applied_rows=applied_rows,
                                    skipped_apply=skipped_apply,
                                    backup_path=backup_path, backup_sha=backup_sha)
    payload = {
        "db": os.path.basename(path) or path,
        "mode": "apply",
        "apply_implemented": True,
        "sha256_before": sha_before,
        "sha256_after": sha_after,
        "db_unchanged": sha_before == sha_after,
        "backup_path": backup_path,
        "backup_sha256": backup_sha,
        "records": stats["records"],
        "with_editor": stats["with_editor"],
        "editor_only": stats["editor_only"],
        "editor_only_changes": stats["editor_only_changes"],
        "changes": stats["changes"],
        "applied": len(applied_rows),
        "skipped": len(skipped_apply),
        "reasons": stats["reasons"],
        "risk_flags": stats["risk_flags"],
        "multi_editor_changes": stats["multi_editor_changes"],
        "applied_entries": applied_rows,
        "skipped_apply": skipped_apply,
        "verification": verification,
        "written": written,
    }
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"库 sha256（apply 前/后）：{sha_before} / {sha_after}")
    print(f"备份：{backup_path}（sha256: {backup_sha}）")
    print(f"记录：{stats['records']} 条；有 editor 字段：{stats['with_editor']} 条"
          f"（其中只有 editor、没有 author：{stats['editor_only']} 条）")
    print(f"→ 已倒装 {len(applied_rows)} 条 / 跳过 {len(skipped_apply)} 条"
          f"（其中本次因待裁决冲突跳过 {len(skipped_apply)} 条）")
    print(f"二次校验（重跑 dry-run）：剩余将倒装 {verification['remaining']} 条（应为 0）")
    for item in written:
        state = "已写入" if item["written"] else "内容未变（未重写）"
        print(f"   {state}：{item['path']}（{item['bytes']:,} 字节，sha256: {item['sha256']}）")
    print("   跳过原因 Top：" + "、".join(
        f"{reason} {count}" for reason, count in list(stats["reasons"].items())[:6]))
    print("   （只改了 editor / content_sha256 / updated_at；其余元数据未动）")
    return 0


def load_duplicate_pairs(path: str) -> list[tuple[str, str, float]]:
    """读 `duplicates.csv`（UTF-8 BOM，列 key_a/key_b/similarity）。"""
    import csv as _csv

    pairs: list[tuple[str, str, float]] = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in _csv.DictReader(handle):
            try:
                similarity = float(row.get("similarity") or 0)
            except ValueError:
                similarity = 0.0
            a, b = str(row.get("key_a") or ""), str(row.get("key_b") or "")
            if a and b and a != b:
                pairs.append((a, b, similarity))
    return pairs


def apply_merge_decisions(plan: dict, decisions: list[dict], store: "bibdb.Store") -> tuple[list[dict], list[dict]]:
    """把「人工簇」按 decisions（{keys, entry_type, note?, doubt?}）改成可合并簇。

    返回 (decided_entries, skipped_decisions)。规则：
      · canonical 取**建议类型对应**的记录（没有则退回计划 canonical）；
      · 字段合并仍按统一规则（花括号保护优先/缺失补全）；类型 = 建议类型；
      · key：similarity=1 簇按规范改名，其余保留 canonical 旧 key；旧 key 全进 aliases；
      · 未给 decision 的人工簇（如 Segerberg）**保持 manual 不动**。
    """
    records = {str(r.get("key")): r for r in store.records() if isinstance(r, dict)}
    by_keys = {tuple(sorted(str(k) for k in (c.get("keys") or []))): c
               for c in plan.get("clusters") or []}
    live_keys = set(records)
    merged_entries = [c for c in plan.get("clusters") or [] if c.get("action") == "merge"]
    decided: list[dict] = []
    skipped: list[dict] = []
    for decision in decisions:
        keys = tuple(sorted(str(k) for k in (decision.get("keys") or [])))
        entry = by_keys.get(keys)
        if entry is None or entry.get("action") != "manual":
            # 允许「人工簇的子集」决策（Segerberg：按类型拆成 thesis / book 两条，只并书版 3 条）
            parent = next((c for c in plan.get("clusters") or []
                           if c.get("action") == "manual"
                           and set(keys) <= set(str(k) for k in c.get("keys") or [])), None)
            if parent is not None:
                entry = dict(parent)
                entry["keys"] = list(keys)
                entry["cluster"] = parent["cluster"]
                entry.pop("members", None)
                parent["keys"] = [k for k in parent["keys"] if k not in keys]
                plan["clusters"].append(entry)
                if not parent["keys"]:
                    plan["clusters"].remove(parent)
                    if parent in (plan.get("manual_clusters") or []):
                        plan["manual_clusters"].remove(parent)
            else:
                skipped.append({"keys": list(keys), "reason": "计划中找不到对应的人工簇"})
                continue
        cluster_records = [records[k] for k in keys if k in records]
        if len(cluster_records) != len(keys):
            skipped.append({"keys": list(keys), "reason": "成员不在库内"})
            continue
        wanted = str(decision.get("entry_type") or "").strip().lower()
        typed = [r for r in cluster_records
                 if str(r.get("entry_type") or "").strip().lower() == wanted]
        canonical = (max(typed, key=_merge_version_score) if typed
                     else max(cluster_records, key=_merge_version_score))
        fields, diff_notes = _merge_cluster_fields(cluster_records)
        entry["action"] = "merge"
        entry["entry_type"] = wanted or str(canonical.get("entry_type") or "")
        entry["canonical_key"] = str(canonical.get("key"))
        entry["merged_fields"] = fields
        entry["field_diffs"] = diff_notes[:MERGE_FIELD_DIFF_NOTE_LIMIT]
        entry["decision"] = {"entry_type": entry["entry_type"],
                             "note": str(decision.get("note") or ""),
                             "doubt": bool(decision.get("doubt")),
                             "new_key": str(decision.get("new_key") or "")}
        merged_entries.append(entry)
        decided.append(entry)
    # key / aliases（仅对本次新并入的簇；已 apply 过的批次不再处理）
    renamed_away = {k for entry in merged_entries for k in entry["keys"]}
    reserved = live_keys - renamed_away
    used = set(reserved)
    for entry in merged_entries:
        if entry.get("new_key"):
            used.add(entry["new_key"])
    for entry in decided:
        canonical = records[entry["canonical_key"]]
        person = re.split(r"\s+and\s+",
                          str(_record_fields(canonical).get("author")
                              or _record_fields(canonical).get("editor") or ""), flags=re.I)[0]
        prefix = _merge_key_name(person)
        year = _record_year(canonical)
        base = f"{prefix}{year}" if prefix and year else ""
        if entry.get("has_similarity_1") and base:
            key = base
            if key in used:
                for letter in "abcdefghijklmnopqrstuvwxyz":
                    candidate = f"{base}{letter}"
                    if candidate not in used:
                        key = candidate
                        break
        else:
            key = str(entry.get("canonical_key") or "")
        override = str(entry.get("decision", {}).get("new_key") or "")
        if override:
            key = override
        entry["new_key"] = key
        used.add(key)
    final_keys = reserved | {entry["new_key"] for entry in merged_entries}
    for entry in decided:
        aliases = set(str(k) for k in entry["keys"])
        for key in entry["keys"]:
            record = records.get(key) or {}
            for alias in record.get("aliases") or []:
                if isinstance(alias, str) and alias:
                    aliases.add(alias)
        aliases.discard(entry["new_key"])
        entry["aliases"] = sorted(a for a in aliases if a not in final_keys)
    decided_keys = {entry["cluster"] for entry in decided}
    plan["manual_clusters"] = [c for c in plan.get("manual_clusters") or []
                               if c.get("cluster") not in decided_keys]
    plan["clusters_merge"] = sum(1 for c in plan["clusters"] if c.get("action") == "merge")
    plan["records_merged_away"] = sum(len(c["keys"]) - 1 for c in plan["clusters"]
                                      if c.get("action") == "merge")
    plan["predicted_records"] = len(records) - plan["records_merged_away"]
    return decided, skipped


def apply_merge_plan(store: "bibdb.Store", plan: dict) -> dict:
    """按 dry-run 计划执行合并（**会写库**；由 cmd 在 --backup/sha 门禁通过后调用）。

    · 只处理 `action=merge` 的簇（人工簇整簇不动）；`miaolitian*` 不在任何簇里；
    · 每簇：canonical 记录 → 合并字段 + 合并 sources/conflicts + aliases + 新 key，
      删除其余成员记录；重算 content_sha256，`updated_at`=now；
    · 幂等：若某簇成员已不存在（已被合并过/计划过期）→ 跳过并计数，不报错。
    """
    records = {str(r.get("key")): r for r in store.records() if isinstance(r, dict)}
    now = bibdb._now_iso()
    applied: list[dict] = []
    skipped: list[dict] = []
    for entry in plan.get("clusters") or []:
        if entry.get("action") != "merge":
            continue
        keys = [str(k) for k in entry.get("keys") or []]
        if any(key not in records for key in keys):
            skipped.append({"cluster": entry.get("cluster", ""), "keys": keys,
                            "reason": "成员已不存在（已合并/计划过期）"})
            continue
        canonical_key = str(entry.get("canonical_key") or "")
        if canonical_key not in records:
            skipped.append({"cluster": entry.get("cluster", ""), "keys": keys,
                            "reason": "canonical 记录不存在"})
            continue
        members = [records[key] for key in keys]
        canonical = records[canonical_key]
        new_key = str(entry.get("new_key") or canonical_key)
        if new_key in records and new_key not in keys:
            skipped.append({"cluster": entry.get("cluster", ""), "keys": keys,
                            "reason": f"新 key `{new_key}` 已被其它记录占用"})
            continue
        etype = str(entry.get("entry_type") or canonical.get("entry_type") or "")
        merged_fields = {str(name): str(value) for name, value in
                         (entry.get("merged_fields") or {}).items() if str(value or "").strip()}
        if not merged_fields:                    # 计划缺字段（旧计划）→ 退回 canonical 字段
            merged_fields = {str(n): str(v) for n, v in _record_fields(canonical).items()
                             if str(v or "").strip()}
        verified_at = min([str(r.get("verified_at") or "") for r in members if r.get("verified_at")]
                          or [""])
        created_at = min([str(r.get("created_at") or "") for r in members
                          if r.get("created_at")] or [now])
        aliases = sorted({str(a) for a in (entry.get("aliases") or []) if str(a)})
        merged = {
            "version": canonical.get("version", 1),
            "key": new_key,
            "entry_type": etype,
            "fields": dict(sorted(merged_fields.items())),
            "verified": any(bool(r.get("verified")) for r in members),
            "verified_at": verified_at or None,
            "method": canonical.get("method"),
            "content_sha256": bibdb.content_sha256(etype, merged_fields),
            "aliases": aliases,
            "sources": _merge_sources_union(members),
            "conflicts": _merge_conflicts_union(members),
            "created_at": created_at,
            "updated_at": now,
        }
        if not merged["verified_at"]:
            merged.pop("verified_at", None)
        applied.append({
            "cluster": entry.get("cluster", ""),
            "keys": keys,
            "canonical_key": canonical_key,
            "new_key": new_key,
            "aliases": aliases,
            "removed": [k for k in keys if k != new_key],
            "sources": len(merged["sources"]),
            "conflicts": len(merged["conflicts"]),
        })
        for key in keys:
            records.pop(key, None)
        records[new_key] = merged
    if applied:
        # ⚠️ 不能用 store.save()：`save()` 会调 `refresh_aliases()` **按来源重算 aliases**，
        #    把本计划显式写入的「被并旧 key → 新 key」覆盖掉（合并场景来源 key 与新 key 不同）。
        #    这里按库格式直接原子写回（与 `_record_line` 同一序列化），随后由 cmd 重新 load 校验。
        text = "".join(bibdb._record_line(records[key]) + "\n" for key in sorted(records))
        fsutil.atomic_write(store.path, text, keep_backups=0)
    return {
        "applied_clusters": applied,
        "skipped_clusters": skipped,
        "clusters_applied": len(applied),
        "clusters_skipped": len(skipped),
        "records_removed": sum(len(item["keys"]) - 1 for item in applied),
        "records_after": len(records),
        "renames": sum(1 for item in applied for key in item["keys"] if key != item["new_key"]),
        "aliases_total": sum(len(item["aliases"]) for item in applied),
    }


def cmd_merge_duplicates(args) -> int:
    """重复候选合并：默认 dry-run（计划）；`--apply --backup` 才真正写库（第四十三批）。"""
    apply_mode = bool(getattr(args, "apply", False))
    if apply_mode and bool(getattr(args, "dry_run", False)):
        print("✗ --apply 与 --dry-run 互斥：一次只能选一种模式", file=sys.stderr)
        return 2
    try:
        path, store = _open_store(args)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    sha_before = bibdb.db_sha256(path)
    backup_path = str(getattr(args, "backup", "") or "")
    backup_sha = ""
    if apply_mode:
        if not backup_path:
            print("✗ --apply 会写真实库，必须先备份并用 --backup PATH 指定", file=sys.stderr)
            return 2
        if not os.path.isfile(backup_path):
            print(f"✗ 备份文件不存在：{backup_path}", file=sys.stderr)
            return 2
        backup_sha = bibdb.db_sha256(backup_path)
        if backup_sha != sha_before:
            print(f"✗ 备份 sha256 与当前库不一致（备份 {backup_sha} / 当前 {sha_before}）",
                  file=sys.stderr)
            return 2
    if apply_mode:
        plan_path = str(getattr(args, "plan", "") or "")
        if not plan_path:
            plan_path = os.path.join(os.path.dirname(path), MERGE_PLAN_JSON)
        if not os.path.isfile(plan_path):
            print(f"✗ 找不到合并计划：{plan_path}（先跑 merge-duplicates dry-run）", file=sys.stderr)
            return 2
        try:
            plan = json.load(open(plan_path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"✗ 读不了合并计划 {plan_path}：{exc}", file=sys.stderr)
            return 2
        if str(plan.get("db_sha256") or "") != sha_before:
            print("✗ 合并计划与当前库不一致（库 sha256 变了）：请先重跑 "
                  "`bibdb.py merge-duplicates --out-dir <笔记仓>/bib_db` 生成新计划",
                  file=sys.stderr)
            return 2
        decisions_path = str(getattr(args, "decisions", "") or "")
        decided_rows: list[dict] = []
        skipped_decisions: list[dict] = []
        if decisions_path:
            if not os.path.isfile(decisions_path):
                print(f"✗ 找不到决策文件：{decisions_path}", file=sys.stderr)
                return 2
            try:
                decisions_raw = json.load(open(decisions_path, encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"✗ 读不了决策文件 {decisions_path}：{exc}", file=sys.stderr)
                return 2
            if isinstance(decisions_raw, dict):
                decisions_raw = decisions_raw.get("decisions") or []
            decided, skipped_decisions = apply_merge_decisions(plan, decisions_raw, store)
            decided_rows = [{"cluster": entry.get("cluster"), "keys": entry.get("keys"),
                             "entry_type": entry.get("entry_type"),
                             "new_key": entry.get("new_key"),
                             "note": (entry.get("decision") or {}).get("note", ""),
                             "doubt": (entry.get("decision") or {}).get("doubt", False)}
                            for entry in decided]
        result = apply_merge_plan(store, plan)
        sha_after = bibdb.db_sha256(path)
        # 重新 load 校验（写回后确认 JSONL 可解析、content_sha 自洽、索引正常）
        store2 = bibdb.Store.load(path)
        content_bad = sum(
            1 for record in store2.records()
            if isinstance(record, dict)
            and bibdb.content_sha256(str(record.get("entry_type") or ""),
                                     record.get("fields") or {}) != record.get("content_sha256"))
        lint = store2.lint()
        pending = 0; conflicts_total = 0
        for record in store2.records():
            for conflict in (record.get("conflicts") or []):
                conflicts_total += 1
                if bibdb.conflict_is_pending(conflict):
                    pending += 1
        try:
            _pairs, pairs_after = store2._near_duplicates(store2.records(), cap=0)  # noqa: SLF001
        except Exception:                      # 防御：候选对统计失败不影响主流程
            pairs_after = -1
        verification = {
            "records_after": result["records_after"],
            "content_sha_mismatch": content_bad,
            "lint_errors": len(lint.get("errors") or []),
            "lint_warnings": len(lint.get("warnings") or []),
            "pending_conflicts": pending,
            "conflicts_total": conflicts_total,
            "pairs_after": pairs_after,
        }
        written = _write_merge_apply_reports(
            args, plan, result, sha_before, sha_after,
            backup_path=backup_path, backup_sha=backup_sha, verification=verification,
            decisions=decided_rows or None, skipped_decisions=skipped_decisions or None,
            batch=2 if decisions_path else 1)
        payload = {
            "db": os.path.basename(path) or path,
            "mode": "apply",
            "sha256_before": sha_before,
            "sha256_after": sha_after,
            "backup_path": backup_path,
            "backup_sha256": backup_sha,
            "records_before": plan.get("records_total"),
            "records_after": result["records_after"],
            "records_removed": result["records_removed"],
            "clusters_applied": result["clusters_applied"],
            "clusters_skipped": result["clusters_skipped"],
            "clusters_manual_kept": len(plan.get("manual_clusters") or []),
            "renames": result["renames"],
            "aliases_total": result["aliases_total"],
            "key_conflicts": plan.get("key_conflicts") or [],
            "decisions": decided_rows,
            "skipped_decisions": skipped_decisions,
            "verification": verification,
            "written": written,
        }
        if args.json:
            _print_json(payload)
            return 0
        print(f"库：{path}")
        print(f"库 sha256（apply 前/后）：{sha_before} / {sha_after}")
        print(f"备份：{backup_path}（sha256: {backup_sha}）")
        print(f"记录：{plan.get('records_total')} → {result['records_after']}"
              f"（合并消失 {result['records_removed']} 条）")
        print(f"合并：{result['clusters_applied']} 簇（跳过 {result['clusters_skipped']}）；"
              f"人工保留 {len(plan.get('manual_clusters') or [])} 簇")
        print(f"key 改名 {result['renames']} 条；别名 {result['aliases_total']} 个")
        if decided_rows:
            print(f"人工簇决策合并：{len(decided_rows)} 簇 → "
                  + "、".join(f"{item['cluster']}→`{item['new_key']}`({item['entry_type']})"
                              for item in decided_rows))
        print(f"校验：content_sha 不符 {content_bad}；lint error {verification['lint_errors']}；"
              f"待裁决冲突 {pending}；候选对 {pairs_after}")
        for item in written:
            state = "已写入" if item["written"] else "内容未变（未重写）"
            print(f"   {state}：{item['path']}（{item['bytes']:,} 字节，sha256: {item['sha256']}）")
        print("   （只合并/改 key/写别名；未改 author 语义、未碰历史冲突裁决、未写稿件仓）")
        return 0
    # ── dry-run（计划）──
    pairs_path = str(getattr(args, "pairs", "") or "")
    if not pairs_path:
        pairs_path = os.path.join(os.path.dirname(path), "duplicates.csv")
    if not os.path.isfile(pairs_path):
        print(f"✗ 找不到重复候选清单：{pairs_path}（先跑 bibdb.py report）", file=sys.stderr)
        return 2
    pairs = load_duplicate_pairs(pairs_path)
    plan = build_merge_plan(store, pairs)
    sha_after = bibdb.db_sha256(path)
    if sha_before != sha_after:
        print("✗ 内部错误：merge-duplicates dry-run 期间库文件被改动", file=sys.stderr)
        return 2
    written = _write_merge_reports(args, plan)
    payload = {
        "db": os.path.basename(path) or path,
        "sha256": sha_before,
        "db_unchanged": True,
        "apply_implemented": True,
        "pairs_total": plan["pairs_total"],
        "pairs_excluded_miaolitian": plan["pairs_excluded_miaolitian"],
        "clusters_total": plan["clusters_total"],
        "clusters_merge": plan["clusters_merge"],
        "clusters_manual": plan["clusters_manual"],
        "records_total": plan["records_total"],
        "records_merged_away": plan["records_merged_away"],
        "predicted_records": plan["predicted_records"],
        "rekey_clusters": plan["rekey_clusters"],
        "rekey_records": plan["rekey_records"],
        "key_conflicts": len(plan["key_conflicts"]),
        "apa_letter_groups": len(plan["apa_letter_groups"]),
        "surname_year_discriminated": len(plan["surname_year_discriminated"]),
        "manual_clusters": len(plan["manual_clusters"]),
        "written": written,
    }
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"库 sha256：{sha_before}（dry-run 未改动）")
    print(f"候选对：{plan['pairs_total']}（跳过 miaolitian 相关 {plan['pairs_excluded_miaolitian']}）"
          f" → 簇 {plan['clusters_total']}（合并 {plan['clusters_merge']} / 人工 {plan['clusters_manual']}）")
    print(f"记录：{plan['records_total']} → 预计 {plan['predicted_records']}"
          f"（合并消失 {plan['records_merged_away']} 条）")
    print(f"key 规范化：{plan['rekey_clusters']} 簇 / 改 key {plan['rekey_records']} 条；"
          f"APA 字母组 {len(plan['apa_letter_groups'])}、同姓不同人区分 "
          f"{len(plan['surname_year_discriminated'])}、真冲突/待确认 {len(plan['key_conflicts'])} 项")
    print(f"人工清单：{len(plan['manual_clusters'])} 簇")
    for item in written:
        state = "已写入" if item["written"] else "内容未变（未重写）"
        print(f"   {state}：{item['path']}（{item['bytes']:,} 字节，sha256: {item['sha256']}）")
    print("   （dry-run：未修改 bibliography.jsonl；--apply 才写库）")
    return 0


# ── B-188：引用库维护闭环的写库命令（GUI 唯一写入口）────────────────────────
#
# 设计卡：`EditToolBox-notes/drafts/specs/b188-bibdb-maintenance-loop.md` §3。
# 三条命令，都是「dry-run 预览 → 用户确认 → sha 闸 → 自动备份 → 原子写 → 复核」：
#   · edit             编辑一条记录（表单/原始 bib；可改 key，旧 key 自动进 aliases）
#   · merge-records    合并 2+ 条重复记录（新规范 key 可沿用被合并键之一；旧 key 全部进
#                      aliases；逐字段选源）
#   · suggest-field-fixes  只读：缺字段的归位候选（A 类）+ 异常 entry_type/key 的修法建议
# 写库痕迹：记录里追加 `edits[]`（动作/时间/备份 sha/字段级 old→new），不另开 sidecar。

def _clone_store(store):
    """深拷贝一个 Store（dry-run 预演候选写入用；不碰库文件）。"""
    return copy.deepcopy(store)


def _store_text(store) -> str:
    """按 `Store.save()` 的同一序列化口径渲染整库文本（不写盘）。"""
    store.refresh_aliases()
    return "".join(bibdb._record_line(store._by_key[key]) + "\n"
                   for key in sorted(store._by_key))


def _store_sha(store) -> str:
    return hashlib.sha256(_store_text(store).encode("utf-8")).hexdigest()


def _entry_type_or_error(value: object, *, label: str = "entry_type") -> str:
    etype = str(value or "").strip().lower()
    if not etype:
        raise bibdb.BibDBError(f"{label} 不能为空")
    if etype not in bibdb.KNOWN_ENTRY_TYPES:
        raise bibdb.BibDBError(
            f"未知的 {label} `{etype}`：必须在 bibcheck 已知类型表里"
            f"（{', '.join(sorted(bibdb.KNOWN_ENTRY_TYPES)[:12])} …）")
    return etype


_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def _canonical_edit_fields(etype: str, raw_fields: object) -> dict[str, str]:
    """编辑输入字段 → 库内规范字段（复用 `bibdb.canonical_fields()`，唯一实现）。"""
    if not isinstance(raw_fields, dict):
        raise bibdb.BibDBError("fields 必须是 JSON 对象（字段名 → 值）")
    for name, value in raw_fields.items():
        if not isinstance(value, str):
            raise bibdb.BibDBError(f"字段 `{name}` 的值必须是字符串（收到 {type(value).__name__}）")
        low = str(name).strip().lower()
        if not _FIELD_NAME_RE.match(low):
            raise bibdb.BibDBError(
                f"字段名 `{name}` 不合法：只允许小写字母开头的字母/数字/下划线/连字符")
    fields = bibdb.canonical_fields(dict(raw_fields))
    if not fields:
        raise bibdb.BibDBError("编辑后字段为空：拒绝写入一条没有字段的记录")
    return fields


def _read_json_fields_arg(text: str) -> tuple[str, dict]:
    """`--json-fields` 的三种形态：{entry_type, fields} / {fields} / 扁平字段表。"""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise bibdb.BibDBError(f"--json-fields 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise bibdb.BibDBError("--json-fields 必须是 JSON 对象")
    if isinstance(data.get("fields"), dict):
        return str(data.get("entry_type") or ""), data["fields"]
    payload = {k: v for k, v in data.items() if k != "entry_type"}
    return str(data.get("entry_type") or ""), payload


def _read_edit_input(args) -> tuple[str, dict]:
    """→ (entry_type 候选, 原始字段 dict)；--entry-type 覆盖输入里的类型。"""
    if bool(args.bib) == bool(args.json_fields):
        raise bibdb.BibDBError("--bib 与 --json-fields 必须且只能给一个")
    if args.bib:
        path = os.path.abspath(args.bib)
        if not os.path.isfile(path):
            raise bibdb.BibDBError(f"找不到 --bib 文件：{path}")
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            raise bibdb.BibDBError(f"读不了 --bib 文件：{path}（{exc}）") from exc
        entries, findings = bibparse.parse_entries(text)
        if any(f.level == bibparse.LEVEL_PROBLEM for f in findings):
            detail = "；".join(f.detail for f in findings
                              if f.level == bibparse.LEVEL_PROBLEM)[:300]
            raise bibdb.BibDBError(f"--bib 有语法问题，拒绝编辑：{detail}")
        if len(entries) != 1:
            raise bibdb.BibDBError(
                f"--bib 必须恰好 1 条 BibTeX（实际 {len(entries)} 条）：请只给要编辑的那一条")
        entry = entries[0]
        if str(entry.key or "").strip() != str(args.key).strip():
            raise bibdb.BibDBError(
                f"--bib 的 key `{entry.key}` 与 --key `{args.key}` 不一致："
                "改 key 请用 --new-key（库内 key 是定位依据，不能靠 bib 文件换）")
        return str(entry.etype or ""), dict(entry.fields)
    etype, fields = _read_json_fields_arg(args.json_fields)
    return etype, fields


def _pending_conflict_labels(store, key: str) -> list[str]:
    """该记录**待裁决**冲突的 C 编号（与 `conflicts`/CONFLICTS.md 同源）。"""
    labels: list[str] = []
    for item in collect_conflict_items(store):
        if item.get("record_key") == key and item.get("pending"):
            labels.append(f"C{int(item['stable_no']):03d}")
    return labels


def _key_collision(store, key: str, *, allow_record: str = "") -> str:
    """key 能否当 canonical key 用：撞 canonical key / 任何 alias 都算撞车（返回说明）。"""
    if not key:
        return "key 不能为空"
    if re.search(r"[\s,{}()\"]", key):
        return f"key `{key}` 含空白/逗号/花括号/引号，不是合法 BibTeX key"
    owners = store._by_key.get(key)
    if owners is not None and str(owners.get("key") or "") != allow_record:
        return f"key `{key}` 已被库内另一条记录占用"
    alias_owners = store._by_alias.get(key) or []
    for record in alias_owners:
        if str(record.get("key") or "") != allow_record:
            return f"key `{key}` 已是记录 `{record.get('key')}` 的别名（alias）"
    return ""


def _missing_required(etype: str, fields: dict) -> list[str]:
    """该类型「必填」字段里缺哪些（lint 影响预览用；只用于展示，不是新口径）。"""
    spec = bibdb.bibcheck.REQUIRED.get(etype) or {}
    return [name for name in spec.get("must") or [] if not str(fields.get(name) or "").strip()]


def _record_brief_payload(record: dict) -> dict:
    title, author, year = _record_brief(record)
    return {"key": str(record.get("key") or ""),
            "entry_type": str(record.get("entry_type") or ""),
            "title": title, "author": author, "year": year}


def _append_edit_trace(record: dict, entry: dict) -> None:
    """记录里追加一条写库痕迹（`edits[]`；不写 sidecar、不进 content_sha256）。"""
    record.setdefault("edits", []).append(entry)


def _apply_edit_plan(store, *, key: str, entry_type: str, fields: dict,
                     new_key: str, note: str, backup_sha256: str,
                     backup_path: str) -> dict:
    """在内存里执行一次编辑（dry-run 用 clone，apply 用真 store）；返回变更摘要。"""
    record = store._by_key.get(key)
    if record is None:
        raise bibdb.BibDBError(f"库里没有 key `{key}`")
    old_etype = str(record.get("entry_type") or "")
    old_fields = dict(record.get("fields") or {})
    old_key = key
    target_key = new_key or key
    changed: dict[str, dict] = {}
    for name in sorted(set(old_fields) | set(fields)):
        old_value = str(old_fields.get(name) or "")
        new_value = str(fields.get(name) or "")
        if old_value != new_value:
            changed[name] = {"old": old_value, "new": new_value}
    aliases_added: list[str] = []
    if target_key != old_key:
        aliases = {alias for alias in (record.get("aliases") or []) if isinstance(alias, str)}
        aliases.add(old_key)
        aliases.discard(target_key)
        record["aliases"] = sorted(aliases)
        aliases_added = [old_key]
        del store._by_key[old_key]
        record["key"] = target_key
        store._by_key[target_key] = record
        store.dirty = True
    record["entry_type"] = entry_type
    record["fields"] = fields
    record["content_sha256"] = bibdb.content_sha256(entry_type, fields)
    record["updated_at"] = bibdb._now_iso()
    record["version"] = int(record.get("version") or 1)
    _append_edit_trace(record, {
        "at": record["updated_at"], "action": "edit",
        "note": note, "old_key": old_key, "new_key": target_key,
        "entry_type_before": old_etype, "entry_type_after": entry_type,
        "changed_fields": changed,
        "backup_sha256": backup_sha256, "backup_path": backup_path,
    })
    store.dirty = True
    return {"key": old_key, "new_key": target_key,
            "entry_type_before": old_etype, "entry_type_after": entry_type,
            "field_changes": changed, "aliases_added": aliases_added,
            "missing_before": _missing_required(old_etype, old_fields),
            "missing_after": _missing_required(entry_type, fields)}


def _source_identity(src: dict) -> str:
    return f"{src.get('kind')}|{src.get('issue')}|{src.get('slug')}|{src.get('file')}|{src.get('key')}"


def _parse_field_sources(values, keys: list[str]) -> dict[str, str]:
    """`--field-source field=A` 列表 → {field: 记录 key}；拒绝未知字段来源。"""
    out: dict[str, str] = {}
    for item in values or []:
        text = str(item or "").strip()
        if "=" not in text:
            raise bibdb.BibDBError(f"--field-source 需要 `字段=记录key` 形态（收到 {text!r}）")
        field, source = text.split("=", 1)
        field, source = field.strip().lower(), source.strip()
        if not _FIELD_NAME_RE.match(field):
            raise bibdb.BibDBError(f"--field-source 的字段名不合法：{field!r}")
        if source not in keys:
            raise bibdb.BibDBError(
                f"--field-source 的来源 `{source}` 不在 --keys 里（{'、'.join(keys)}）")
        out[field] = source
    return out


def _merge_fields(records: list[dict], field_sources: dict[str, str]) -> dict[str, str]:
    """逐字段选源：显式 `--field-source` 优先，否则按 --keys 顺序取第一条非空值。"""
    by_key = {str(record.get("key")): record for record in records}
    merged: dict[str, str] = {}
    for record in records:
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        for name, value in fields.items():
            if not str(value or "").strip() or name in merged:
                continue
            merged[name] = str(value)
    for field, source_key in field_sources.items():
        source_fields = by_key[source_key].get("fields") or {}
        value = str(source_fields.get(field) or "").strip()
        if value:
            merged[field] = value
        else:
            merged.pop(field, None)
    return dict(sorted(merged.items()))


def _apply_merge_plan(store, *, keys: list[str], new_key: str, entry_type: str,
                      merged_fields: dict[str, str], field_sources: dict[str, str],
                      note: str, backup_sha256: str, backup_path: str) -> dict:
    """在内存里执行一次合并；返回变更摘要（dry-run 用 clone，apply 用真 store）。"""
    records = [store._by_key[key] for key in keys]
    survivor = records[0]
    old_survivor_key = str(survivor.get("key") or "")
    comparison: dict[str, dict] = {}
    for name in sorted(set().union(*(set((r.get("fields") or {}).keys()) for r in records))):
        row = {str(r.get("key")): str((r.get("fields") or {}).get(name) or "") for r in records}
        row["chosen"] = field_sources.get(name, next(
            (key for key in keys if row.get(key)), ""))
        row["value"] = merged_fields.get(name, "")
        comparison[name] = row
    aliases = set()
    for record in records:
        aliases.add(str(record.get("key")))
        aliases.update(alias for alias in (record.get("aliases") or [])
                       if isinstance(alias, str))
    aliases.discard(new_key)
    # 来源合并（按来源身份去重，保留原顺序）
    sources = list(survivor.get("sources") or [])
    seen = {_source_identity(src) for src in sources if isinstance(src, dict)}
    for record in records[1:]:
        for source in record.get("sources") or []:
            if not isinstance(source, dict):
                continue
            identity = _source_identity(source)
            if identity in seen:
                continue
            seen.add(identity)
            sources.append(source)
    survivor["sources"] = sources
    survivor["aliases"] = sorted(aliases)
    survivor["entry_type"] = entry_type
    survivor["fields"] = merged_fields
    survivor["content_sha256"] = bibdb.content_sha256(entry_type, merged_fields)
    survivor["updated_at"] = bibdb._now_iso()
    survivor["version"] = int(survivor.get("version") or 1)
    _append_edit_trace(survivor, {
        "at": survivor["updated_at"], "action": "merge",
        "note": note, "old_key": old_survivor_key, "new_key": new_key,
        "merged_keys": [str(record.get("key")) for record in records],
        "merged_entry_sha256": {str(record.get("key")): str(record.get("content_sha256") or "")
                                for record in records},
        "field_sources": dict(sorted(field_sources.items())),
        "aliases": sorted(aliases),
        "backup_sha256": backup_sha256, "backup_path": backup_path,
    })
    for record in records[1:]:
        store.remove_record(str(record.get("key")))
    if new_key != old_survivor_key:
        store._by_key.pop(old_survivor_key, None)
        survivor["key"] = new_key
    store._reindex()
    store.dirty = True
    return {"keys": keys, "new_key": new_key, "entry_type": entry_type,
            "field_comparison": comparison, "fields": merged_fields,
            "aliases": sorted(aliases),
            "records_before": len(store.records()) + len(records) - 1,
            "records_after": len(store.records())}


def _load_verify(path: str, *, expect_key: str, expect_etype: str,
                 expect_fields: dict, expect_aliases: list[str],
                 absent_keys: list[str]) -> dict:
    """apply 后复核：重新读库 → key/内容/旧 key/aliases 全对，再跑 lint。"""
    store = bibdb.Store.load(path)
    record = store._by_key.get(expect_key)
    items = collect_conflict_items(store)
    problems: list[str] = []
    if record is None:
        problems.append(f"新 key `{expect_key}` 不在库内")
    else:
        if str(record.get("entry_type")) != expect_etype:
            problems.append("entry_type 与预期不符")
        if bibdb.content_sha256(expect_etype, record.get("fields") or {}) != \
                str(record.get("content_sha256") or ""):
            problems.append("content_sha256 与字段内容不一致")
        have_aliases = set(record.get("aliases") or [])
        miss = [alias for alias in expect_aliases if alias not in have_aliases]
        if miss:
            problems.append(f"旧 key 未进 aliases：{'、'.join(miss)}")
    # B-201：new_key 可以等于被合并键之一（它就是合并后的 canonical key）；
    # `absent_keys` 里把它排除，否则会误报「被合并的旧 key 仍在库内」。
    still = [key for key in absent_keys if key != expect_key and key in store._by_key]
    if still:
        problems.append(f"被合并的旧 key 仍在库内：{'、'.join(still)}")
    lint = store.lint()
    return {"ok": not problems, "problems": problems,
            "records": len(store.records()),
            "lint_errors": len(lint.get("errors") or []),
            "lint_warnings": len(lint.get("warnings") or []),
            "pending_conflicts": sum(1 for item in items if item.get("pending"))}


def cmd_edit(args) -> int:
    """编辑一条库记录；默认 dry-run，`--apply` 才写（sha 闸 + 可选备份 + 复核）。"""
    apply_mode = bool(getattr(args, "apply", False))
    if apply_mode and bool(getattr(args, "dry_run", False)):
        return _fail(args, "--apply 与 --dry-run 互斥：一次只能选一种模式")
    try:
        input_etype, raw_fields = _read_edit_input(args)
        new_key = str(getattr(args, "new_key", "") or "").strip()
        path, store = _open_store(args)
        key = str(args.key or "").strip()
        record = store._by_key.get(key)
        if record is None:
            raise bibdb.BibDBError(f"库里没有 key `{key}`")
        # entry_type 优先级：--entry-type > 输入（bib/json-fields）> 记录现有类型
        entry_type = _entry_type_or_error(
            getattr(args, "entry_type", "") or input_etype
            or str(record.get("entry_type") or ""))
        fields = _canonical_edit_fields(entry_type, raw_fields)
        pending = _pending_conflict_labels(store, key)
        if pending:
            raise bibdb.BibDBError(
                f"记录 `{key}` 还有 {len(pending)} 条待裁决冲突（{'、'.join(pending)}）："
                "请先在「待处理」页裁决，再编辑（否则字段会被两条写路径互相覆盖）")
        if new_key:
            collision = _key_collision(store, new_key, allow_record=key)
            if collision:
                raise bibdb.BibDBError(collision)
        before_sha = bibdb.db_sha256(path)
        backup_dir = ""
        backup_path = ""
        if apply_mode:
            backup_dir = _check_edit_apply_guards(args, path, before_sha)
        # 预演（dry-run 用 clone；apply 也先 clone 算 sha 供对比）
        clone = _clone_store(store)
        summary = _apply_edit_plan(
            clone, key=key, entry_type=entry_type, fields=fields,
            new_key=new_key, note=str(args.note or ""),
            backup_sha256="", backup_path="")
        estimate_sha = _store_sha(clone)
        written = False
        after_sha = before_sha
        verification: dict = {}
        if apply_mode:
            backup_path = (_write_db_backup(path, backup_dir, before_sha)
                           if backup_dir else "")
            result = _apply_edit_plan(
                store, key=key, entry_type=entry_type, fields=fields,
                new_key=new_key, note=str(args.note or ""),
                backup_sha256=before_sha if backup_path else "",
                backup_path=backup_path)
            written = store.save()
            after_sha = bibdb.db_sha256(path)
            verification = _load_verify(
                path, expect_key=result["new_key"], expect_etype=entry_type,
                expect_fields=fields,
                expect_aliases=result["aliases_added"],
                absent_keys=[])
            if not verification.get("ok"):
                raise bibdb.BibDBError(
                    "写库后复核失败（数据未按预期落盘，请检查库文件并按需回退）："
                    + "；".join(verification.get("problems") or []))
    except bibdb.BibDBError as exc:
        return _fail(args, exc)
    payload = {
        "command": "edit", "dry_run": not apply_mode, "db": path,
        **summary,
        "requested_new_key": new_key,
        "conflicts_blocking": [],
        "db_sha256_before": before_sha,
        "db_sha256_after": after_sha if apply_mode else estimate_sha,
        "db_sha256_after_estimate": estimate_sha,
        "backup_path": backup_path, "written": written,
        "verification": verification,
    }
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"记录：`{summary['key']}`" + (f" → `{summary['new_key']}`" if summary["new_key"] != summary["key"] else ""))
    print(f"类型：{summary['entry_type_before']} → {summary['entry_type_after']}")
    if summary["field_changes"]:
        for name, change in summary["field_changes"].items():
            print(f"  字段 {name}：{change['old']!r} → {change['new']!r}")
    else:
        print("  字段无变化")
    if summary["aliases_added"]:
        print(f"  旧 key 进 aliases：{'、'.join(summary['aliases_added'])}")
    if summary["missing_before"] or summary["missing_after"]:
        print(f"  必填缺字段：{summary['missing_before'] or '无'} → {summary['missing_after'] or '无'}")
    if apply_mode:
        if backup_path:
            print(f"  备份：{backup_path}")
        print(f"  库 sha256：{before_sha[:12]}… → {after_sha[:12]}…")
        print(f"  复核：{'通过' if verification.get('ok') else '失败'}（lint error "
              f"{verification.get('lint_errors')} / warning {verification.get('lint_warnings')}）")
    else:
        print(f"  （--dry-run：未写库；预计库 sha256 {estimate_sha[:12]}…）")
        print("   确认无误后加 --apply 写入（需要额外备份时另加 --backup <目录>）")
    return 0


def _check_edit_apply_guards(args, path: str, before_sha: str) -> str:
    """apply 前置：`--backup` **可选**（B-190 起；不传 = 不写备份）、sha 闸（可选但建议）。

    返回备份目录绝对路径（`""` = 不写备份）；口径与 `_write_guard` 同源：
    `--expect-db-sha256` 不符 → `BibDBError`（退出 2 且零写入）；`--backup` 必须是目录。
    """
    backup_dir = _backup_dir_arg(args)
    wanted = _normalize_sha256_arg(getattr(args, "expect_db_sha256", ""),
                                   "--expect-db-sha256")
    if wanted:
        if not before_sha:
            raise bibdb.BibDBError(f"库文件不存在，无法校验 --expect-db-sha256：{path}")
        if before_sha != wanted:
            raise bibdb.BibDBError(
                f"库 sha256 与 --expect-db-sha256 不符（当前 {before_sha[:12]}…，期望 "
                f"{wanted[:12]}…）：库可能已被其它操作修改，请刷新后重试（本次未写库）")
    return backup_dir


def cmd_merge_records(args) -> int:
    """合并 2+ 条重复记录（默认 dry-run；`--apply` 才写）。"""
    apply_mode = bool(getattr(args, "apply", False))
    if apply_mode and bool(getattr(args, "dry_run", False)):
        return _fail(args, "--apply 与 --dry-run 互斥：一次只能选一种模式")
    try:
        keys = [part.strip() for part in str(args.keys or "").split(",") if part.strip()]
        if len(keys) < 2:
            raise bibdb.BibDBError("--keys 至少给 2 条（用逗号分隔）")
        if len(set(keys)) != len(keys):
            raise bibdb.BibDBError("--keys 里有重复 key")
        new_key = str(args.new_key or "").strip()
        if not new_key:
            raise bibdb.BibDBError("合并必须给 --new-key（新规范 key）")
        field_sources = _parse_field_sources(args.field_source, keys)
        path, store = _open_store(args)
        missing = [key for key in keys if key not in store._by_key]
        if missing:
            raise bibdb.BibDBError(f"库里没有这些 key：{'、'.join(missing)}")
        blocking: list[str] = []
        for key in keys:
            blocking.extend(f"{key}:{label}" for label in _pending_conflict_labels(store, key))
        if blocking:
            raise bibdb.BibDBError(
                f"待合并记录还有待裁决冲突（{'、'.join(blocking)}）：请先裁决再合并")
        # B-201：新 key 可以**沿用**被合并键之一（高频诉求：`15-11`+`Ripley2015`
        # 直接并成 `Ripley2015`，正文 `\cite` 不悬空）。等于 keys[1] 时也放行
        # （那条记录合并后即被删除），撞**其它**已存在 canonical/alias 仍拒绝。
        allow_record = new_key if new_key in keys else keys[0]
        collision = _key_collision(store, new_key, allow_record=allow_record)
        if collision:
            raise bibdb.BibDBError(collision)
        records = [store._by_key[key] for key in keys]
        etypes = {str(record.get("entry_type") or "") for record in records}
        requested_etype = str(getattr(args, "entry_type", "") or "").strip()
        if requested_etype:
            entry_type = _entry_type_or_error(requested_etype)
        elif len(etypes) == 1:
            entry_type = _entry_type_or_error(next(iter(etypes)))
        else:
            raise bibdb.BibDBError(
                "被合并记录的 entry_type 不一致（" + "、".join(sorted(etypes))
                + "）：请用 --entry-type 明确合并后类型")
        merged_fields = _merge_fields(records, field_sources)
        if not merged_fields:
            raise bibdb.BibDBError("合并后字段为空：拒绝写入一条没有字段的记录")
        before_sha = bibdb.db_sha256(path)
        backup_dir = ""
        if apply_mode:
            backup_dir = _check_edit_apply_guards(args, path, before_sha)
        clone = _clone_store(store)
        summary = _apply_merge_plan(
            clone, keys=keys, new_key=new_key, entry_type=entry_type,
            merged_fields=merged_fields, field_sources=field_sources,
            note=str(args.note or ""), backup_sha256="", backup_path="")
        estimate_sha = _store_sha(clone)
        written = False
        after_sha = before_sha
        verification: dict = {}
        if apply_mode:
            backup_path = (_write_db_backup(path, backup_dir, before_sha)
                           if backup_dir else "")
            _apply_merge_plan(
                store, keys=keys, new_key=new_key, entry_type=entry_type,
                merged_fields=merged_fields, field_sources=field_sources,
                note=str(args.note or ""), backup_sha256=before_sha if backup_path else "",
                backup_path=backup_path)
            written = store.save()
            after_sha = bibdb.db_sha256(path)
            verification = _load_verify(
                path, expect_key=new_key, expect_etype=entry_type,
                expect_fields=merged_fields, expect_aliases=summary["aliases"],
                absent_keys=keys)
            if not verification.get("ok"):
                raise bibdb.BibDBError(
                    "写库后复核失败（数据未按预期落盘，请检查库文件并按需回退）："
                    + "；".join(verification.get("problems") or []))
    except bibdb.BibDBError as exc:
        return _fail(args, exc)
    payload = {
        "command": "merge-records", "dry_run": not apply_mode, "db": path,
        **summary,
        "conflicts_blocking": [],
        "db_sha256_before": before_sha,
        "db_sha256_after": after_sha if apply_mode else estimate_sha,
        "db_sha256_after_estimate": estimate_sha,
        "backup_path": backup_path if apply_mode else "", "written": written,
        "verification": verification,
    }
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"合并：{'、'.join(f'`{key}`' for key in keys)} → `{new_key}`（{entry_type}）")
    for name, row in summary["field_comparison"].items():
        sources = "、".join(f"{key}={row.get(key, '')!r}" for key in keys)
        print(f"  字段 {name}：{sources} → 取 {row['chosen'] or '（空）'} = {row['value']!r}")
    print(f"aliases：{'、'.join(summary['aliases']) or '（无）'}")
    print(f"记录数：{summary['records_before']} → {summary['records_after']}")
    if apply_mode:
        if backup_path:
            print(f"  备份：{backup_path}")
        print(f"  库 sha256：{before_sha[:12]}… → {after_sha[:12]}…")
        print(f"  复核：{'通过' if verification.get('ok') else '失败'}（lint error "
              f"{verification.get('lint_errors')} / warning {verification.get('lint_warnings')}）")
    else:
        print(f"  （--dry-run：未写库；预计库 sha256 {estimate_sha[:12]}…）")
        print("   确认无误后加 --apply 写入（需要额外备份时另加 --backup <目录>）")
    return 0


def cmd_suggest_field_fixes(args) -> int:
    """只读：缺字段归位候选 / 异常 entry_type 建议（不写库、不调 AI）。"""
    registry = str(getattr(args, "registry", "") or "missing_field")
    if registry not in ("missing_field", "unknown_entry_type"):
        return _fail(args, "--registry 只支持 missing_field / unknown_entry_type")
    try:
        path, store = _open_store(args)
        rows = collect_field_fix_rows(store, registry)
        limit = int(getattr(args, "limit", 0) or 0)
        if limit > 0:
            rows = rows[:limit]
    except bibdb.BibDBError as exc:
        return _fail(args, exc)
    payload = {"db": path, "registry": registry, "count": len(rows), "rows": rows}
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"候选：{len(rows)} 条（registry={registry}；只读，不写库）")
    for row in rows[:20]:
        print(f"· `{row['key']}`（{row['entry_type']}，缺 {'、'.join(row['missing'])}）")
        for item in row["candidates"][:4]:
            print(f"    {item['field']} ← [{item['source']}] {item['value'][:60]!r}"
                  f"（{item['risk']}；{item['evidence']}）")
    if len(rows) > 20:
        print(f"……另有 {len(rows) - 20} 条（--json 看全量）")
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
    lint.add_argument("--with-records", action="store_true",
                      help="warning_items 每条另带 records（受影响 key 列表；B-188 待处理页用）")
    lint.set_defaults(func=cmd_lint)

    # ── 规范化规则升级后的库迁移（B-115）：默认 dry-run，--apply 只改 sha/updated_at ──
    rh = sub.add_parser(
        "rehash", parents=[common],
        help="规范化规则升级后重算 content_sha256（默认 dry-run；--apply 只改 "
             "content_sha256+updated_at，原子写）")
    rh_mode = rh.add_mutually_exclusive_group()
    rh_mode.add_argument("--dry-run", action="store_true",
                         help="显式 dry-run（默认就是；只报告 old/new，不写库）")
    rh_mode.add_argument("--apply", action="store_true",
                         help="**真正写库**：逐条重算并写回 content_sha256 + updated_at；"
                              "fields/sources/verified 等一字不动（原子写）")
    rh.add_argument("--json", action="store_true", help="机器可读输出")
    rh.set_defaults(func=cmd_rehash)

    # ── 冲突裁决持久化（第 130 条）：把「保留旧值 / 待人工」写进库，重新生成清单不丢 ──
    resolve = sub.add_parser(
        "resolve", parents=[common],
        help="把冲突标记为已裁决（keep=保留旧值 / defer=待人工查证 / adopt=逐字段采用新稿值 "
             "/ replace=采用人工裁决 bib）；--adopt-all-pending 一键全部采用")
    resolve.add_argument("--key", default="",
                         help="库内记录 key（CONFLICTS.md 里「库内记录」那个键，可能带 -2 后缀；"
                              "逐条裁决必给；--adopt-all-pending 时不能给）")
    resolve.add_argument("--action", default="", choices=["keep", "defer", "adopt", "replace"],
                         help="keep=保留库内已核实旧值；defer=待人工查证（先从清单移走）；"
                              "adopt=**逐字段采用新稿值**（必须配 --incoming-sha256）；"
                              "replace=采用**人工裁决 bib**（配 --incoming-sha256 + --bib；"
                              "整条写 entry_type+fields、key 不变）")
    selector = resolve.add_mutually_exclusive_group()
    selector.add_argument("--all-fields", action="store_true",
                          help="该记录**所有**待裁决冲突")
    selector.add_argument("--fields", default="",
                          help="只裁决 field_diff 含这些字段的冲突（逗号分隔，如 "
                               "title,author,$entry_type；一条冲突跨多字段时整条标记）")
    selector.add_argument("--incoming-sha256", default="", metavar="SHA",
                          help="按**冲突指纹**精确标记一条冲突（CONFLICTS.md 里每条的"
                               "「冲突指纹」，或 conflicts --json 的 items[].incoming_sha256；"
                               "至少 8 位十六进制前缀且须唯一；adopt / replace 必给）")
    resolve.add_argument("--bib", default="", metavar="PATH",
                         help="仅 --action replace：人工裁决 bib 文件（**恰好 1 条**条目，"
                              "key 必须等于库内记录 key；裁决不改 key）")
    resolve.add_argument("--adopt-all-pending", action="store_true",
                         help="一键全部采用：对全部待裁决冲突逐条按 adopt 语义应用 incoming 值"
                              "（不给 --key/--action；同记录待裁决冲突字段重叠 → 整体拒绝并"
                              "列出冲突 #，请逐条处理；--dry-run 出逐条预览）")
    resolve.add_argument("--note", default="",
                         help="裁决备注（写进 conflicts[].note，便于审计）")
    resolve.add_argument("--allow-overlap", action="store_true",
                         help="仅 --action adopt / replace：把同记录中字段重叠的其它待裁决冲突"
                              "标为 superseded（默认遇到重叠直接拒绝，先 keep/defer 逐条处理）")
    resolve.add_argument("--expect-db-sha256", default="", metavar="SHA",
                         help="写前安全闸：校验库当前字节 sha256（完整 64 位十六进制）；"
                              "不符 → 退出 2 且零写入（防界面列表过期后误写）")
    resolve.add_argument("--backup", default="", metavar="DIR",
                         help="写前自动备份目录（不存在则创建）：真的会写入时先生成 "
                              "`bibliography-<UTCts>-<库sha前12位>.jsonl`；目录位置已存在"
                              "同名文件 → 退出 2")
    resolve.add_argument("--dry-run", action="store_true", help="只算不写库")
    resolve.add_argument("--json", action="store_true", help="机器可读输出")
    resolve.set_defaults(func=cmd_resolve)

    # ── 待裁决条目列表（B-127，批73）：GUI「待裁决」页数据源；只读 ──────────────
    conf = sub.add_parser(
        "conflicts", parents=[common],
        help="列出冲突条目（只读；--json 给界面待裁决页；--pending-only 只列待裁决）")
    conf.add_argument("--pending-only", action="store_true",
                      help="只列待裁决（没有 resolved_at）的冲突条目")
    conf.add_argument("--json", action="store_true", help="机器可读输出")
    conf.set_defaults(func=cmd_conflicts)

    # ── 从备份恢复整库（B-127，批73；P4 拍板：用备份恢复代替 reopen）──────────
    rst = sub.add_parser(
        "restore", parents=[common],
        help="从备份恢复整库（校验备份结构 + 可选 expect sha → 原子写回；不做 reopen）")
    rst.add_argument("--backup", required=True, metavar="PATH",
                     help="备份文件路径（`bibliography-<UTCts>-<库sha前12位>.jsonl`）")
    rst.add_argument("--expect-db-sha256", default="", metavar="SHA",
                     help="写前校验当前库字节 sha256（完整 64 位十六进制）；"
                          "不符 → 退出 2 且不恢复")
    rst.add_argument("--dry-run", action="store_true",
                     help="只校验备份、不写库（输出前后 sha）")
    rst.add_argument("--json", action="store_true", help="机器可读输出")
    rst.set_defaults(func=cmd_restore)

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

    # ── B-205：人工确认「两条不同条目」（写 sidecar；默认 dry-run，--apply 才写）──
    ack = sub.add_parser(
        "ack-duplicates", parents=[common],
        help="人工确认「两条不同条目」（写库同目录 health-ignore.json；默认 dry-run，"
             "--apply 才写；幂等；不写库）")
    ack.add_argument("--code", required=True, choices=bibdb.HEALTH_IGNORE_CODES,
                     help="告警 code：near_duplicates（一对）/ duplicate_content（一整组）")
    ack.add_argument("--keys", required=True, metavar="A,B[,...]",
                     help="要确认不同的 key（逗号分隔；也认唯一 alias；写盘用 canonical 排序）")
    ack.add_argument("--reason", default="",
                     help=f"裁决理由（默认「{ACK_DUPLICATES_DEFAULT_REASON}」）")
    ack_mode = ack.add_mutually_exclusive_group()
    ack_mode.add_argument("--dry-run", action="store_true",
                          help="预览（默认就是；只算 added / 条目数 / 库 sha，不写 sidecar）")
    ack_mode.add_argument("--apply", action="store_true",
                          help="真正写 sidecar（原子写；库文件一个字节都不动）")
    ack.add_argument("--json", action="store_true", help="机器可读输出")
    ack.set_defaults(func=cmd_ack_duplicates)

    # ── B1 确定性规范化 dry-run（第 131 条后续）：只出报告、绝不写库 ──────────
    norm = sub.add_parser(
        "normalize", parents=[common],
        help="B1 确定性规范化 dry-run（逐条重建→bibnorm.normalize_bib→比较；只出报告，"
             "**绝不写 bibliography.jsonl**）")
    mode = norm.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="显式 dry-run（默认就是；与 --apply 互斥）")
    mode.add_argument("--apply", action="store_true",
                      help="**真正写库**（默认 dry-run）：只改安全条目的 "
                           "entry_type/fields/content_sha256/updated_at；必须先 --backup")
    norm.add_argument("--backup", default="", metavar="PATH",
                      help="--apply 前须先 cp 库文件；这里指定备份路径并校验 sha256 与当前库一致"
                           "（防误 apply）")
    norm.add_argument("--report", default="", metavar="PATH",
                      help="Markdown 报告输出路径（不写则只打印摘要）")
    norm.add_argument("--out-dir", default="", metavar="DIR",
                      help=f"输出目录：写 {NORMALIZE_REPORT_NAME} + {NORMALIZE_JSON_NAME}")
    norm.add_argument("--json-out", default="", metavar="PATH",
                      help="另写 JSON 摘要文件（可选；--out-dir 时默认写同名 .json）")
    norm.add_argument("--json", action="store_true", help="stdout 输出 JSON 摘要")
    norm.set_defaults(func=cmd_normalize)

    # ── B-209：外部 .bib 只读规范化（唯一实现 bibnorm.normalize_bib；无 --db）─────
    nbib = sub.add_parser(
        "normalize-bib",
        help="只读：外部 .bib → 规范文本（--json 给逐条字段前/后 diff；不写库/文件）")
    nsrc = nbib.add_mutually_exclusive_group(required=True)
    nsrc.add_argument("--bib", default="", metavar="PATH",
                      help="输入 .bib 文件（UTF-8；只读，不落报告）")
    nsrc.add_argument("--stdin", action="store_true",
                      help="从 stdin 读 UTF-8 文本（与 --bib 二选一必选）")
    nbib.add_argument("--json", action="store_true",
                      help="stdout 只输出一个 JSON（含 normalized_text 与逐条 diff）")
    nbib.set_defaults(func=cmd_normalize_bib)

    # ── B2 作者倒装（第 136 条后续）：默认 dry-run，--apply 才写库 ──────────
    auth = sub.add_parser(
        "normalize-authors", parents=[common],
        help="B2 英文作者倒装（姓, 名缩写）：默认 dry-run；--apply 才写库，只改 author")
    amode = auth.add_mutually_exclusive_group()
    amode.add_argument("--dry-run", action="store_true", help="显式 dry-run（默认就是）")
    amode.add_argument("--apply", action="store_true",
                       help="真正写库：只改安全条目的 author（+ content_sha256/updated_at），"
                            "必须先 --backup")
    auth.add_argument("--backup", default="", metavar="PATH",
                      help="--apply 前须先 cp 库文件；这里指定备份路径并校验 sha256")
    auth.add_argument("--report", default="", metavar="PATH",
                      help="Markdown 报告路径（dry-run 默认 B2-author-normalize-dryrun.md；"
                           "apply 默认 B2-author-normalize-apply.md）")
    auth.add_argument("--out-dir", default="", metavar="DIR", help="报告输出目录")
    auth.add_argument("--json-out", default="", metavar="PATH", help="另写 JSON 摘要文件")
    auth.add_argument("--json", action="store_true", help="stdout 输出 JSON 摘要")
    auth.set_defaults(func=cmd_normalize_authors)

    # ── B3 编者倒装（dry-run only；apply 须用户拍板后另批实现）────────────────
    edt = sub.add_parser(
        "normalize-editors", parents=[common],
        help="B3 英文编者倒装 **dry-run**（只出报告、绝不写库；传 --apply 一律拒绝）")
    emode = edt.add_mutually_exclusive_group()
    emode.add_argument("--dry-run", action="store_true", help="显式 dry-run（默认就是）")
    emode.add_argument("--apply", action="store_true",
                       help="真正写库：只改安全条目的 editor（+ content_sha256/updated_at），"
                            "必须先 --backup")
    edt.add_argument("--backup", default="", metavar="PATH",
                     help="--apply 前须先 cp 库文件；这里指定备份路径并校验 sha256")
    edt.add_argument("--report", default="", metavar="PATH",
                     help=f"Markdown 报告路径（dry-run 默认 {EDITOR_REPORT_DRY}；"
                          f"apply 默认 {EDITOR_REPORT_APPLY}）")
    edt.add_argument("--out-dir", default="", metavar="DIR",
                     help=f"输出目录：写 {EDITOR_REPORT_DRY} + {EDITOR_REPORT_CSV} "
                          f"+ {EDITOR_REPORT_JSON}")
    edt.add_argument("--csv-out", default="", metavar="PATH",
                     help="全量 CSV 输出路径（只含「将倒装」行；UTF-8 带 BOM）")
    edt.add_argument("--json-out", default="", metavar="PATH", help="JSON 摘要输出路径")
    edt.add_argument("--json", action="store_true", help="stdout 输出 JSON 摘要")
    edt.set_defaults(func=cmd_normalize_editors)

    # ── 重复候选合并计划（dry-run only；apply 另卡）────────────────────────
    mrg = sub.add_parser(
        "merge-duplicates", parents=[common],
        help="重复候选合并计划 **dry-run**（聚类+key 规范化+字段合并；不写库；apply 另卡）")
    mrg.add_argument("--pairs", default="", metavar="PATH",
                     help="重复候选清单 duplicates.csv 路径（默认库同目录）")
    mrg.add_argument("--out-dir", default="", metavar="DIR",
                     help="输出目录：写 DUPLICATES-MERGE-PLAN.md/.csv + RENAMES/MANUAL/PLAN.json")
    mmode = mrg.add_mutually_exclusive_group()
    mmode.add_argument("--dry-run", action="store_true", help="显式 dry-run（默认就是）")
    mmode.add_argument("--apply", action="store_true",
                       help="真正写库：按计划合并 + 改 key + 写 aliases（必须先 --backup）")
    mrg.add_argument("--backup", default="", metavar="PATH",
                     help="--apply 前须先 cp 库文件；这里指定备份路径并校验 sha256")
    mrg.add_argument("--plan", default="", metavar="PATH",
                     help="--apply 用的计划 JSON（默认库同目录 DUPLICATES-MERGE-PLAN.json）")
    mrg.add_argument("--research", default="", metavar="PATH",
                     help="--apply 报告用：人工簇联网核实建议 JSON（可选）")
    mrg.add_argument("--decisions", default="", metavar="PATH",
                     help="--apply 用：人工簇决策 JSON（[{keys, entry_type, note?, doubt?}]；"
                          "第二批合并用；未列出的 manual 簇保持不动）")
    mrg.add_argument("--json", action="store_true", help="stdout 输出 JSON 摘要")
    mrg.set_defaults(func=cmd_merge_duplicates)

    # ── B-188：通用条目编辑 / 合并 / 字段归位建议（GUI 写库入口）────────────
    edt = sub.add_parser(
        "edit", parents=[common],
        help="编辑一条库记录（默认 dry-run；--apply 走 sha 闸 + 原子写 + 写后复核；可 --new-key）")
    edt.add_argument("--key", required=True, metavar="K", help="库内 canonical key（定位用）")
    edt.add_argument("--bib", default="", metavar="PATH",
                     help="编辑后的单条 BibTeX 文件（key 必须等于 --key；改 key 用 --new-key）")
    edt.add_argument("--json-fields", default="", metavar="JSON",
                     help='编辑后的字段 JSON：{"entry_type":...,"fields":{...}} 或扁平字段表')
    edt.add_argument("--new-key", default="", metavar="NEW",
                     help="改 key（默认不改；旧 key 自动进记录 aliases）")
    edt.add_argument("--entry-type", default="", metavar="T", help="覆盖条目类型")
    emode = edt.add_mutually_exclusive_group()
    emode.add_argument("--dry-run", action="store_true", help="预览（默认就是；只算不写）")
    emode.add_argument("--apply", action="store_true",
                       help="真正写库（可选 --backup 目录；不传 = 不写备份）")
    edt.add_argument("--expect-db-sha256", default="", metavar="SHA",
                     help="写前校验库字节 sha256（完整 64 位十六进制）")
    edt.add_argument("--backup", default="", metavar="DIR",
                     help="写前自动备份目录（可选；不传 = 不写备份。文件名 "
                          "bibliography-<UTCts>-<sha12>.jsonl）")
    edt.add_argument("--note", default="", help="写库痕迹备注（记进记录 edits[]）")
    edt.add_argument("--json", action="store_true", help="机器可读输出")
    edt.set_defaults(func=cmd_edit)

    mrec = sub.add_parser(
        "merge-records", parents=[common],
        help="合并 2+ 条重复记录为 1 条（默认 dry-run；new-key 可沿用被合并键之一；旧 key 全进 aliases）")
    mrec.add_argument("--keys", required=True, metavar="A,B[,...]", help="要合并的记录 key（逗号分隔）")
    mrec.add_argument("--new-key", required=True, metavar="K",
                      help="合并后的规范 key（可等于 --keys 之一，B-201；撞其它已有 key/alias 仍拒绝）")
    mrec.add_argument("--entry-type", default="", metavar="T",
                      help="合并后条目类型（各条不一致时必须给）")
    mrec.add_argument("--field-source", action="append", default=[], metavar="FIELD=KEY",
                      help="逐字段选源（可重复；不给的字段按 --keys 顺序取第一条非空值）")
    mmode2 = mrec.add_mutually_exclusive_group()
    mmode2.add_argument("--dry-run", action="store_true", help="预览（默认就是；只算不写）")
    mmode2.add_argument("--apply", action="store_true",
                        help="真正写库（可选 --backup 目录；不传 = 不写备份）")
    mrec.add_argument("--expect-db-sha256", default="", metavar="SHA",
                      help="写前校验库字节 sha256（完整 64 位十六进制）")
    mrec.add_argument("--backup", default="", metavar="DIR",
                      help="写前自动备份目录（可选；不传 = 不写备份）")
    mrec.add_argument("--note", default="", help="写库痕迹备注（记进幸存的记录 edits[]）")
    mrec.add_argument("--json", action="store_true", help="机器可读输出")
    mrec.set_defaults(func=cmd_merge_records)

    sff = sub.add_parser(
        "suggest-field-fixes", parents=[common],
        help="只读：缺 title/year 的字段归位候选（A 类）/ 异常 entry_type 建议（不写库）")
    sff.add_argument("--registry", default="missing_field",
                     choices=("missing_field", "unknown_entry_type"),
                     help="候选注册表：missing_field（默认）/ unknown_entry_type")
    sff.add_argument("--limit", type=int, default=0, metavar="N", help="只输出前 N 条（0=全部）")
    sff.add_argument("--json", action="store_true", help="机器可读输出")
    sff.set_defaults(func=cmd_suggest_field_fixes)

    return parser
