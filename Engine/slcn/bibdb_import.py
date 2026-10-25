"""bibdb 导入族（B-228 第一段）：`init` / `import-bib` / `import-dir` / `rehash`。

纯搬运自 v1.4.0-9 `Engine/bibdb.py`（B-228 第一段，零行为变化）：期扫描、成品 bib
入库、内容 sha 迁移（rehash）与导入前后逐条变化明细（`_change_rows`）。
依赖方向 `bibdb_core ← bibdb_reports ← bibdb_import ← bibdb_cli`（无循环 import）。
函数体逐字节未改；唯一改动是模块头 import 与本 docstring。门面 re-export 全部旧名字。
"""
from __future__ import annotations

import os, re, sys

from slcn import bibdb, fsutil, paths
from slcn.bibdb_core import (
    _db_path, _open_store, _print_changes, _print_import_report, _print_json,
    _report_line, _source_note, _store_snapshot,
)  # noqa: F401
from slcn.bibdb_reports import (
    _record_brief,
)  # noqa: F401

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


# ── rehash：规范化规则升级后的库迁移（B-115）────────────────────────────────
#
# 为什么要有这个命令：`content_sha256` = f(entry_type, fields) → `canonical_fields()`
# → `bibnorm._clean_field_value()`。一旦改 `bibnorm.DROP_FIELDS` 之类的规则，**旧库的
# 摘要立刻全部 stale**，而 lint 会把它报成 error。批45 `6a6ff4e` 改规则后没迁移
# 10-18 冻结的真实库 → 6 条 stale-sha 潜伏了一个月。本命令是事故的补偿入口，
# 也是常态化门禁：以后改规则必须「改代码 + rehash --apply 迁移库 + 更新冻结 fixture」。

REHASH_MIGRATION_NOTE = (
    "改 Engine/slcn/bibnorm.py 的 DROP_FIELDS / DROP_FIELD_PREFIXES / 值清理规则后，"
    "必须对真实库跑 `bibdb.py rehash --apply` 迁移，并更新 "
    "Engine/tests/test_bibdb_rehash.py 的冻结 fixture"
)


def _clip_sha(sha: str) -> str:
    """sha 的前 12 位（空/非 64 位十六进制的旧值原样显示，便于人读报告）。"""
    text = str(sha or "")
    return f"{text[:12]}…" if len(text) > 12 else (text or "（空）")


def collect_rehash_rows(store) -> list[dict]:
    """逐条重算 `content_sha256`，返回与库存值不一致的行（old/new 都是完整 sha）。

    `old` 可能是空串（历史上手工/旧 schema 留下的无摘要记录）——一并补上。
    """
    rows: list[dict] = []
    for record in store.records():
        stored = record.get("content_sha256")
        old = stored if isinstance(stored, str) else ""
        actual = bibdb.content_sha256(str(record.get("entry_type") or ""),
                                      record.get("fields") or {})
        if old == actual:
            continue
        rows.append({"key": str(record.get("key") or ""), "old": old, "new": actual})
    return rows


def _rehash_write(store, rows: list[dict]) -> str:
    """把重算结果写回库：**只改 content_sha256 + updated_at**，按 key 排序原子写。

    为什么不用 `store.save()`：`Store.save()` 会顺带重算 aliases（那是 merge/import 的口径），
    而 rehash 的承诺是「除 sha/updated_at 外一个字节都不动」。这里直接按 `save()`
    同一套序列化（key 排序 + `_record_line()` + `fsutil.atomic_write`）落盘，绕开别名刷新。
    原子写保证失败时旧库完好（`fsutil.atomic_write` 先写同目录临时文件再 rename）。
    """
    now = bibdb._now_iso()
    new_by_key = {row["key"]: row["new"] for row in rows}
    for record in store.records():
        new_sha = new_by_key.get(str(record.get("key") or ""))
        if new_sha is None:
            continue
        record["content_sha256"] = new_sha
        record["updated_at"] = now
    text = "".join(bibdb._record_line(record) + "\n" for record in store.records())
    fsutil.atomic_write(store.path, text, keep_backups=0)
    return now


def cmd_rehash(args) -> int:
    """重算 `content_sha256`；默认 dry-run，`--apply` 才写（只改 sha + updated_at）。"""
    path = _db_path(args)
    try:
        # 用 lenient 载入：rehash 要治的恰好是「content_sha256 不合规」这一类结构问题
        # （缺失/非 64 位十六进制/已 stale）。其余结构问题下面一律拒绝执行。
        store = bibdb.Store.load(path, lenient=True)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    # 写回是按记录重序列化的：坏 JSON 行 / 缺 key / 重复 key 会在写回时被悄悄丢掉，
    # 所以只允许修 content_sha256 相关问题，别的结构问题先 lint 修。
    blocking = [problem for problem in store.problems if "content_sha256" not in problem]
    if blocking:
        print(f"✗ 库有 {len(blocking)} 处与 content_sha256 无关的结构问题，rehash 拒绝执行"
              "（先跑 `bibdb.py lint` 修好，避免写回时丢行）：", file=sys.stderr)
        for problem in blocking[:5]:
            print(f"  · {problem}", file=sys.stderr)
        return 2
    try:
        rows = collect_rehash_rows(store)
        before_sha = bibdb.db_sha256(path)
        written_at = ""
        if args.apply and rows:
            written_at = _rehash_write(store, rows)
        after_sha = bibdb.db_sha256(path) if written_at else before_sha
    except (bibdb.BibDBError, OSError) as exc:
        print(f"✗ rehash 失败（库未改动）：{exc}", file=sys.stderr)
        return 2
    payload = {
        "db": path,
        "dry_run": not args.apply,
        "applied": bool(written_at),
        "records": len(store.records()),
        "stale": len(rows),
        "changes": rows,
        "sha256_before": before_sha,
        "sha256_after": after_sha,
        "updated_at": written_at or None,
        "notes": REHASH_MIGRATION_NOTE,
    }
    if args.json:
        _print_json(payload)
        return 0
    print(f"库：{path}")
    print(f"条目 {payload['records']}；content_sha256 与当前规范化规则不一致 "
          f"{len(rows)} 条")
    for row in rows:
        print(f"  ✗ {row['key']}：{_clip_sha(row['old'])} → {_clip_sha(row['new'])}")
    if not rows:
        print("✓ 全部条目一致（0 条需迁移）——库已与当前规范化规则对齐。")
    elif args.apply:
        print(f"✓ 已重算并写回 {len(rows)} 条（只改 content_sha256 + updated_at；原子写）")
        print(f"  库 sha256：{before_sha[:12]}… → {after_sha[:12]}…")
        print(f"  下一步：`bibdb.py lint --db {path}` 应 error 0（warning 不变）。")
    else:
        print(f"dry-run：未写库（{len(rows)} 条待迁移）；确认后用 `--apply` 写回。")
    if rows:
        print(f"  说明：{REHASH_MIGRATION_NOTE}。")
    return 0
