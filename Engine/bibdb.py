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
    rehash      [--dry-run | --apply] [--json]
                                            规范化规则升级后**重算 content_sha256**：默认
                                            dry-run（只报告 old/new，不写库）；--apply 才写
                                            （只改 content_sha256 + updated_at，原子写）
    resolve     --key K --action keep|defer|adopt|replace
                (--all-fields | --fields F1,F2 | --incoming-sha256 SHA)
                [--bib PATH] [--allow-overlap] [--note TEXT]
                [--expect-db-sha256 SHA] [--backup DIR] [--dry-run] [--json]
                                            记录冲突裁决：keep=保留旧值 / defer=待人工 /
                                            adopt=**逐字段采用新稿值**（改 fields、重算 sha，
                                            必须用指纹；重叠冲突默认拒绝或用 --allow-overlap
                                            标 superseded）；replace=采用**人工裁决 bib**
                                            （B-127：恰好 1 条、key 必须等于库内 key、
                                            整条写 entry_type+fields）；整条替换仍走
                                            import-bib --force
    resolve --adopt-all-pending [--dry-run] [--note TEXT]
                [--expect-db-sha256 SHA] [--backup DIR] [--json]
                                            B-127 一键全部采用：全部待裁决冲突逐条按 adopt
                                            语义应用 incoming 值；同记录待裁决冲突字段重叠 →
                                            整体拒绝（列出冲突 #，请逐条处理）；真写单次
                                            原子写 + 单次备份
    conflicts   [--pending-only] [--json]    B-127：列出冲突条目（只读；C 编号与
                                            CONFLICTS.md 同源；--json 给界面待裁决页）
    restore     --backup PATH [--expect-db-sha256 SHA] [--dry-run] [--json]
                                            B-127：从备份整库恢复（校验备份可 Store.load +
                                            行级 schema；expect sha 一致才原子写回；
                                            **不做 reopen**）
    report      [--out DIR]                 一次生成全部待裁决清单（推荐）
    report-conflicts  [--out DIR]           字段冲突待裁决清单 CONFLICTS.md（中文、可勾选）
    report-duplicates [--out DIR]           同作品不同 key 候选对 DUPLICATES.md + duplicates.csv
    normalize-authors [--dry-run | --apply --backup PATH] [--report PATH | --out-dir DIR]
                [--json-out PATH] [--json]
                                            B2 英文作者倒装（姓, 名缩写）：默认 dry-run；
                                            --apply 才写库（只改 author；中文/机构/others 跳过）
    normalize   [--report PATH | --out-dir DIR] [--json-out PATH] [--json]
                [--apply --backup PATH]
                                            B1 确定性规范化：默认 dry-run（只出报告、绝不写库）；
                                            --apply 才写库（只改安全条目的 entry_type/fields/
                                            content_sha256/updated_at；须先 --backup 备份）
    merge-duplicates [--dry-run | --apply --backup PATH] [--pairs PATH]
                [--plan PATH] [--out-dir DIR] [--json]
                                            重复候选合并 + key 规范化：默认 dry-run（出计划，
                                            **不写库**）；--apply 才写库（须先 --backup；
                                            人工簇暂不合并；旧 key 全量进 aliases）
    normalize-editors [--dry-run | --apply --backup PATH]
                [--report PATH | --out-dir DIR] [--csv-out PATH]
                [--json-out PATH] [--json]
                                            B3 英文编者倒装（姓, 名缩写）：默认 dry-run（只出
                                            报告、绝不写库）；--apply 才写库（只改 editor；
                                            中文/机构/others 跳过；含「只有 editor」的记录）

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
    cp ../EditToolBox-notes/bib_db/bibliography.jsonl /tmp/before_B1.jsonl
    /opt/homebrew/bin/python3.14 bibdb.py normalize --apply --backup /tmp/before_B1.jsonl \
        --report ../EditToolBox-notes/bib_db/B1-apply-2026-10-18.md
    # ↑ 真正 apply（只改安全条目；解析失败/高危形状/待裁决冲突字段 → 跳过并列清单）
    /opt/homebrew/bin/python3.14 bibdb.py normalize-authors --out-dir ../EditToolBox-notes/bib_db
    /opt/homebrew/bin/python3.14 bibdb.py normalize-authors --apply --backup /tmp/before_B2.jsonl \
        --report ../EditToolBox-notes/bib_db/B2-author-normalize-2026-10-18.md
    # ↑ 生成 CONFLICTS.md / DUPLICATES.md / duplicates.csv（同一份库重复生成逐字节相同）
    /opt/homebrew/bin/python3.14 bibdb.py normalize-editors --out-dir ../EditToolBox-notes/bib_db
    # ↑ B3 编者倒装 dry-run 报告（B3-editor-normalize-dryrun.md/.csv/.json）；只读库、
    #   不写 bibliography.jsonl
    cp ../EditToolBox-notes/bib_db/bibliography.jsonl /tmp/before_B3_editors.jsonl
    /opt/homebrew/bin/python3.14 bibdb.py normalize-editors --apply \
        --backup /tmp/before_B3_editors.jsonl \
        --report ../EditToolBox-notes/bib_db/B3-editor-normalize-apply.md
    # ↑ 真正 apply（只改 editor / content_sha256 / updated_at；备份 sha 必须与当前库一致）
    /opt/homebrew/bin/python3.14 bibdb.py merge-duplicates --out-dir ../EditToolBox-notes/bib_db
    # ↑ 重复候选合并计划 dry-run（DUPLICATES-MERGE-PLAN.md/.csv + RENAMES/MANUAL/PLAN.json；只读库）
    cp ../EditToolBox-notes/bib_db/bibliography.jsonl /tmp/bibliography_before_merge.jsonl
    /opt/homebrew/bin/python3.14 bibdb.py merge-duplicates --apply \
        --backup /tmp/bibliography_before_merge.jsonl \
        --out-dir ../EditToolBox-notes/bib_db
    # ↑ 真正合并（按 dry-run 计划；13 人工簇暂不合并；旧 key→aliases；须备份 sha 一致）

退出码
------
`0` = 成功（查询 0 命中、lint 只有 warning 也算成功）；`1` = `lint` 有 error；
`2` = 用法 / IO / 库结构错误（消息在 stderr）。库文件不存在时 `query/stats/lint` /
`report*` 退出 2（先 init/import）；`import-bib` / `import-dir` 会自动建库。
`report*` 只写清单文件（可重复生成、内容幂等），不改库本体；`resolve` 才写库：
keep/defer 只改 `conflicts[]` 的裁决元数据（不改字段值），adopt/replace 按规则改
`entry_type`/`fields` 并重算 `content_sha256`（`--dry-run` 一律不写）。
`resolve --adopt-all-pending` 一次原子写 + 一次备份；`restore` 从备份整库写回。
写动作可加 `--expect-db-sha256`（完整 64 位十六进制，不符退出 2 且零写入）与
`--backup DIR`（真的会写入时先备份 `bibliography-<UTCts>-<库sha前12位>.jsonl`）。
`normalize` 默认是**纯 dry-run**：只写报告文件，绝不写 `bibliography.jsonl`；
`--apply` 才写库（且必须先用 `--backup` 指定备份、sha256 与当前库一致才允许执行）。
`--dry-run`（预览）：照常解析/查重/算冲突，但**一个字节都不写**（库文件不存在也不会
新建），`--json` 里带 `dry_run=true` 与逐条 `changes` 明细；界面「入藏」页的
「预览」按钮用的就是它。`--bibtex`（query）：每条命中多带一份可直接复制的 BibTeX。

**⚠️ 改规范化规则必须迁移真实库**：`content_sha256` 由 `slcn/bibnorm.py` 的
`DROP_FIELDS` / `DROP_FIELD_PREFIXES` / 值清理规则决定。**改了规则（或升级引擎）后，
必须对真实库跑一遍** `python3 Engine/bibdb.py rehash --db <库> --apply`，并更新冻结
fixture `Engine/tests/test_bibdb_rehash.py` 里的期望 sha —— 否则真实库 `lint` 会报
`content_sha256 与字段内容不一致`（批45 `6a6ff4e` 改了 `DROP_FIELDS` 没迁移库，
6 条 stale-sha 一直没被发现，就是本命令要根治的事故）。`rehash` 只改
`content_sha256` + `updated_at`，`fields` / `sources` / `verified` 等**一字不动**。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
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


def _write_guard(path: str, args) -> tuple[str, str]:
    """写动作通用安全闸（B-127）：返回 `(库当前字节 sha256, 备份目录绝对路径或 "")`。

    · `--expect-db-sha256`：完整 64 位十六进制，且与库当前字节 sha256 **一致**才继续；
      不符 → `BibDBError`（退出 2）。调用方在**任何写入之前**调用本函数 → 天然零写入。
    · `--backup`：参数是**目录**（不存在则由写前备份创建；已存在同名文件 → 退出 2）。
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
    backup_dir = str(getattr(args, "backup", "") or "").strip()
    if backup_dir:
        if os.path.exists(backup_dir) and not os.path.isdir(backup_dir):
            raise bibdb.BibDBError(f"--backup 需要目录，但已存在同名文件：{backup_dir}")
        backup_dir = os.path.abspath(backup_dir)
    return before_sha, backup_dir


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
_INSTITUTION_RE = re.compile(
    r"\b(?:Association|Society|Organization|Organisation|Institute|University|Universit[äa]t|"
    r"College|School|Department|Faculty|Center|Centre|Committee|Council|Group|Team|Consortium|"
    r"Network|Agency|Office|Bureau|Board|Panel|Project|Workshop|Conference|Congress|Foundation|"
    r"Corporation|Company|Inc|Ltd|Press|Publishing|Ministry|Government|Bank|Laboratory|Lab|"
    r"Commission|Federation|Union|Academy|Trust|Forum|Initiative|Platform|Alliance|Coalition|"
    r"Staff|Members|Contributors|Editors|Editorial)\b", re.I)


def _author_skip_reason(value: str) -> str:
    """作者值不适合（或不必要）倒装时的中文原因（空串 = 可以倒装）。"""
    text = (value or "").strip()
    if not text:
        return "空值"
    if _CJK_RE.search(text):
        return "含中文/日文（不倒装）"
    if _INSTITUTION_RE.search(text):
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
#     与库内既有风格一致（`ChenBo2005` / `Haack1978` / `Li2026a`）。
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
    """第一作者姓名 → 规范化 key 前缀（拉丁 `Haack` / 中文 `ChenBo`）。认不出返回 ""。"""
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
        "  与库内既有风格一致（`ChenBo2005` / `Haack1978` / `Li2026a`）；只对 **similarity=1 的簇**",
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
