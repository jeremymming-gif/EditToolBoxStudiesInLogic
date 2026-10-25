"""bibdb 底层（B-228 第一段）：`Engine/bibdb.py` 的共用小工具与库/写保护层。

纯搬运自 v1.4.0-9 `Engine/bibdb.py`（B-228 第一段，零行为变化）：只放被
`bibdb_reports` / `bibdb_import` / `bibdb_cli` 多模块共用的常量与底层函数。
依赖方向 `bibdb_core ← bibdb_reports ← bibdb_import ← bibdb_cli`（无循环 import）。
函数体逐字节未改；唯一改动是模块头 import 与本 docstring。门面 `Engine/bibdb.py`
re-export 全部旧名字。
"""
from __future__ import annotations

import datetime, json, os, re, shutil, sys

from slcn import bibdb

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


# ── B-127（批73）：写前安全闸 / 备份 / 失败 JSON 出口 ────────────────────────
#
# 为什么放在 CLI 层：读写库的语义在 `slcn/bibdb.py`（引擎唯一一份），「写前校验 sha、
# 自动备份、把失败原因回给界面」是**外壳契约**，与具体子命令无关 → 一处实现，所有写
# 动作（keep/defer/adopt/replace/adopt-all、restore）共用。


def _fail(args, exc) -> int:
    """统一失败出口：stderr 中文原因；给了 `--json` 时 stdout 另出 `{"error": …}`。

    GUI 读 stdout 的 JSON（失败时也要能显示原因），非 JSON 调用方看 stderr 即可。
    """
    message = str(exc)
    print(f"✗ {message}", file=sys.stderr)
    if getattr(args, "json", False):
        _print_json({"error": message})
    return 2


def _normalize_sha256_arg(value: object, label: str) -> str:
    """`--expect-db-sha256` 口径：**完整** 64 位十六进制（不收前缀/大写混写按小写算）。"""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise bibdb.BibDBError(
            f"{label} 需要完整的 64 位十六进制 sha256（收到 {value!r}）")
    return text


def _backup_dir_arg(args) -> str:
    """`--backup` 口径（B-190 起对 `edit`/`merge-records` 也**可选**）："" = 不写备份。

    给了就必须是**目录**（不存在则由写前备份创建；已存在同名文件 → 退出 2）。
    单点解析，`_write_guard` 与 `_check_edit_apply_guards` 同源，避免两套口径漂移。
    """
    backup_dir = str(getattr(args, "backup", "") or "").strip()
    if not backup_dir:
        return ""
    if os.path.exists(backup_dir) and not os.path.isdir(backup_dir):
        raise bibdb.BibDBError(f"--backup 需要目录，但已存在同名文件：{backup_dir}")
    return os.path.abspath(backup_dir)


def _write_guard(path: str, args) -> tuple[str, str]:
    """写动作通用安全闸（B-127）：返回 `(库当前字节 sha256, 备份目录绝对路径或 "")`。

    · `--expect-db-sha256`：完整 64 位十六进制，且与库当前字节 sha256 **一致**才继续；
      不符 → `BibDBError`（退出 2）。调用方在**任何写入之前**调用本函数 → 天然零写入。
    · `--backup`：可选；参数是**目录**（不存在则由写前备份创建；已存在同名文件 → 退出 2）。
      不传 = 不写备份（`""`）。
    """
    before_sha = bibdb.db_sha256(path)
    wanted = _normalize_sha256_arg(getattr(args, "expect_db_sha256", ""),
                                   "--expect-db-sha256")
    if wanted:
        if not before_sha:
            raise bibdb.BibDBError(f"库文件不存在，无法校验 --expect-db-sha256：{path}")
        if before_sha != wanted:
            raise bibdb.BibDBError(
                f"库 sha256 与 --expect-db-sha256 不符（当前 {before_sha[:12]}…，期望 "
                f"{wanted[:12]}…）：库可能已被其它操作修改，请刷新后重试（本次未写库）")
    return before_sha, _backup_dir_arg(args)


def _utc_stamp() -> str:
    """备份文件名里的 UTC 时间戳（定长、按时间可排序）：`YYYYmmddTHHMMSSZ`。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_db_backup(path: str, backup_dir: str, before_sha: str,
                     *, stamp: str = "") -> str:
    """写库前备份（B-127）：复制库字节 → `bibliography-<UTCts>-<before_sha12>.jsonl`。

    **只在真的会发生写入时调用**（干跑/无变化不产生备份）。同名备份已存在 → 拒绝覆盖并
    退出 2（宁可不写，也不静默盖掉上一份可恢复的证据）；备份后立刻校验字节 sha256。
    `stamp` 仅供测试注入固定时间戳。
    """
    os.makedirs(backup_dir, exist_ok=True)
    name = f"bibliography-{stamp or _utc_stamp()}-{before_sha[:12]}.jsonl"
    target = os.path.join(backup_dir, name)
    if os.path.exists(target):
        raise bibdb.BibDBError(f"备份文件已存在（拒绝覆盖）：{target}")
    try:
        shutil.copyfile(path, target)
    except OSError as exc:
        raise bibdb.BibDBError(f"备份失败（本次未写库）：{exc}") from exc
    if bibdb.db_sha256(target) != before_sha:
        try:
            os.remove(target)
        except OSError:
            pass
        raise bibdb.BibDBError(
            f"备份校验失败（库可能正在被其它操作修改；本次未写库）：{target}")
    return target


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


def _store_snapshot(store) -> dict:
    """导入前的只读快照：`key → 记录`（记录本身不复制，只用于比对引用）。"""
    return {str(record.get("key") or ""): record for record in store.records()}


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


def _report_out_dir(args) -> str:
    return str(getattr(args, "out", "") or "")


def _resolve_report_out(args) -> str:
    """报告输出目录：`--out`；不给就用库文件所在目录（默认 = 笔记仓 bib_db/）。"""
    out = str(getattr(args, "out", "") or "")
    if out:
        return os.path.abspath(out)
    return os.path.dirname(os.path.abspath(_db_path(args)))


def _print_written(written: list[dict]) -> None:
    for item in written:
        state = "已写入" if item["written"] else "内容未变（未重写）"
        print(f"   {state}：{item['path']}")
        print(f"      {item['bytes']:,} 字节，sha256: {item['sha256']}")
    print("   （清单不含生成时间：同一份库重复生成逐字节相同）")
