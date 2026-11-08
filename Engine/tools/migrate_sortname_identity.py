#!/usr/bin/env python3.14
r"""B-377 R03：把 `sortname` 从「内容身份」里挪出来的一次性迁移。

背景
----
B-377 起 `sortname` 是**纯排序层覆盖字段**（BibLaTeX 用 `<key>_sortname` 控制排序，
不改变引用内容）。既然 `bibdb._IDENTITY_IGNORE` 已把它排除在 `content_sha256` 之外，
历史上「库记录显式含 sortname」的条目，其存量摘要就不再匹配现行算法——本脚本按现行
规则重算一次。**真实库当前 0 条显式 sortname，所以 changed 预期=0**（本脚本不改规则，
只做一次性对齐）。

只改什么（承诺）
----------------
  · 只重算 `content_sha256`；
  · **仅当摘要真变化**时把 `updated_at` 置为当前时刻（与库内其它 updated_at 同口径）；
  · `fields` 及其它字段逐字节不动；行序保持库内原顺序；写回走与 `bibdb.save()`
    同一套序列化（`_record_line()` + `fsutil.atomic_write()` 原子写，keep_backups=0）。
  · 写回前自检：除 content_sha256/updated_at 外所有键值必须与迁移前完全相同，否则拒绝写。

用法
----
    cd Engine
    export PYTHONPATH="$PWD:$PWD/vendor"
    /opt/homebrew/bin/python3.14 tools/migrate_sortname_identity.py \
        --db /path/to/bibliography.jsonl --dry-run --out /tmp/b377-migrate
    /opt/homebrew/bin/python3.14 tools/migrate_sortname_identity.py \
        --db /path/to/bibliography.jsonl --apply

  `--db` 必填（不设默认值：防呆，绝不猜真实库路径）；`--dry-run` 与 `--apply` 二选一。
  `--out` 缺省当前目录；dry-run 产出 `<out>/sortname_identity_dryrun.csv`
  （columns: key,entry_type,old_sha256,new_sha256,changed）与 `<out>/summary.json`。
  apply 产出 `<out>/sortname_identity_apply.csv` + `<out>/summary.json`。
  dry-run **绝不写库**；apply 才写，且第二次运行 changed==0（幂等，不重写文件）。

退出码
------
`0` = 成功（dry-run 发现变化也是 0；变化是给人看的报告）；`2` = 用法/IO/结构错误
（含「库里有与 content_sha256 无关的结构问题」→ 拒绝写回，先 lint 修）。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)

from slcn import bibdb, fsutil  # noqa: E402

CSV_COLUMNS = ("key", "entry_type", "old_sha256", "new_sha256", "changed")

_NOTE = (
    "只重算 content_sha256（B-377：sortname 不再计入内容身份）；仅当摘要变化时更新 "
    "updated_at；fields 及其它字段逐字节不变。changed 预期=显式含 sortname 的记录数"
    "（真实笔记仓当前 0 条，故预期 0）。"
)


def collect_rows(store) -> tuple[list[dict], list[dict]]:
    """逐条重算现行 `content_sha256` → (rows, errors)。rows 覆盖全部记录（含未变）。"""
    rows: list[dict] = []
    errors: list[dict] = []
    for record in store.records():
        key = str(record.get("key") or "")
        old = record.get("content_sha256")
        old = old if isinstance(old, str) else ""
        try:
            new = bibdb.content_sha256(str(record.get("entry_type") or ""),
                                       record.get("fields") or {})
        except Exception as exc:  # noqa: BLE001  迁移必须逐条容错，不能半途丢记录
            errors.append({"key": key, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append({"key": key,
                     "entry_type": str(record.get("entry_type") or ""),
                     "old_sha256": old, "new_sha256": new,
                     "changed": old != new})
    return rows, errors


def explicit_sortname_records(store) -> int:
    """库记录里**显式**含 `sortname` 字段的条数（changed 的预期上界/应然值）。"""
    count = 0
    for record in store.records():
        fields = record.get("fields")
        if isinstance(fields, dict) and "sortname" in fields:
            count += 1
    return count


def _snapshot(store) -> list[tuple[str, str]]:
    """除 content_sha256/updated_at 外的逐记录快照（写回前自检用）。"""
    snap: list[tuple[str, str]] = []
    for record in store.records():
        rest = {name: value for name, value in record.items()
                if name not in ("content_sha256", "updated_at")}
        snap.append((str(record.get("key") or ""),
                     json.dumps(rest, ensure_ascii=False, sort_keys=True)))
    return snap


def write_db(store, rows: list[dict]) -> str:
    """只改变化记录的 content_sha256 + updated_at，按库内原顺序原子写回。"""
    new_by_key = {row["key"]: row["new_sha256"] for row in rows if row["changed"]}
    if not new_by_key:
        return ""
    before = _snapshot(store)
    now = bibdb._now_iso()
    for record in store.records():
        key = str(record.get("key") or "")
        new_sha = new_by_key.get(key)
        if new_sha is None:
            continue
        record["content_sha256"] = new_sha
        record["updated_at"] = now
    if _snapshot(store) != before:  # 防御：迁移承诺「除 sha/updated_at 外不动」
        raise RuntimeError("迁移自检失败：除 content_sha256/updated_at 外还有字段变化，"
                           "拒绝写回（库未改动）")
    text = "".join(bibdb._record_line(record) + "\n" for record in store.records())
    fsutil.atomic_write(store.path, text, keep_backups=0)
    return now


def _write_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row[name] for name in CSV_COLUMNS})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="B-377 R03：重算 content_sha256（sortname 已不计入身份）；"
                    "只改 content_sha256 与 updated_at。",
        epilog=_NOTE)
    parser.add_argument("--db", required=True,
                        help="引用库 .jsonl 路径（必填，不设默认值；真实库请先 dry-run）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="只算不写库，产出 CSV + summary.json")
    mode.add_argument("--apply", action="store_true",
                      help="写回库（只改 content_sha256 与 updated_at）")
    parser.add_argument("--out", default=".",
                        help="CSV/summary 输出目录（缺省当前目录）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print(f"✗ 引用库不存在：{db_path}", file=sys.stderr)
        return 2
    try:
        # lenient：本脚本要处理的恰好是 content_sha256 这一类结构问题（stale/缺失）。
        store = bibdb.Store.load(db_path, lenient=True)
    except bibdb.BibDBError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    # 写回是整行重序列化：坏 JSON 行/缺 key/重复 key 会被悄悄丢掉 → 只允许修 sha 这一类。
    blocking = [problem for problem in store.problems
                if "content_sha256" not in problem]
    if blocking:
        print(f"✗ 库有 {len(blocking)} 处与 content_sha256 无关的结构问题，拒绝迁移"
              "（先跑 `bibdb.py lint` 修好，避免写回时丢行）：", file=sys.stderr)
        for problem in blocking[:5]:
            print(f"  · {problem}", file=sys.stderr)
        return 2

    before_sha = bibdb.db_sha256(db_path)
    rows, errors = collect_rows(store)
    changed_rows = [row for row in rows if row["changed"]]
    explicit = explicit_sortname_records(store)
    written_at = ""
    if args.apply and errors:
        print(f"✗ {len(errors)} 条记录无法重算摘要，apply 拒绝写库：", file=sys.stderr)
        for err in errors[:5]:
            print(f"  · {err['key']}: {err['reason']}", file=sys.stderr)
        return 2
    try:
        if args.apply:
            written_at = write_db(store, rows)
    except (bibdb.BibDBError, OSError, RuntimeError) as exc:
        print(f"✗ 迁移失败（库未改动）：{exc}", file=sys.stderr)
        return 2
    after_sha = bibdb.db_sha256(db_path) if written_at else before_sha

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    csv_name = ("sortname_identity_apply.csv" if args.apply
                else "sortname_identity_dryrun.csv")
    csv_path = os.path.join(out_dir, csv_name)
    _write_csv(csv_path, rows)
    summary = {
        "db": db_path,
        "dry_run": not args.apply,
        "applied": bool(written_at),
        "records": len(rows),
        "changed": len(changed_rows),
        "unchanged": len(rows) - len(changed_rows),
        "explicit_sortname_records": explicit,
        "changed_equals_explicit_sortname": len(changed_rows) == explicit,
        "errors": errors,
        "skipped_errors": len(errors),
        "db_sha256_before": before_sha,
        "db_sha256_after": after_sha,
        "updated_at": written_at,
        "csv": csv_path,
        "note": _NOTE,
    }
    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    mode = "apply" if args.apply else "dry-run"
    print(f"[{mode}] {db_path}")
    print(f"  记录 {len(rows)}：changed {len(changed_rows)}，unchanged "
          f"{len(rows) - len(changed_rows)}，errors {len(errors)}"
          f"（显式含 sortname {explicit} 条）")
    if changed_rows:
        for row in changed_rows[:10]:
            print(f"  · {row['key']}: {row['old_sha256'][:12] or '（空）'}… → "
                  f"{row['new_sha256'][:12]}…")
        if len(changed_rows) > 10:
            print(f"  · ……另有 {len(changed_rows) - 10} 条，见 CSV")
    if not args.apply:
        print(f"  DB 未改动（sha256 {before_sha[:12]}…）；报告：{summary_path}")
    elif written_at:
        print(f"  已写回（sha256 {before_sha[:12]}… → {after_sha[:12]}…）；"
              f"报告：{summary_path}")
    else:
        print(f"  无需写回（changed=0，库与 updated_at 均未动）；报告：{summary_path}")
    if not args.apply and changed_rows and len(changed_rows) != explicit and not errors:
        print("  ⚠ changed 与「显式含 sortname 记录数」不一致：库里可能还有按其它规则"
              "过期的摘要（先跑 `bibdb.py rehash` 核对），本脚本只解释 sortname 这一项。",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
