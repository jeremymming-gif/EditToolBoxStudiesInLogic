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
    ack-duplicates --code near_duplicates|duplicate_content --keys A,B[,...]
                [--reason TEXT] [--apply] [--json]
                                            人工确认「两条不同条目，不再提示」（B-205）：
                                            默认 dry-run（只预览）；--apply 才原子写库同目录
                                            `health-ignore.json`（**不写 bibliography.jsonl**）；
                                            幂等（已 ack → rc0 added=false 零写入）；坏 sidecar /
                                            非当前 pair 或组 → rc2 且零写入
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
    edit        --key K (--bib PATH | --json-fields JSON) [--new-key NEW]
                [--entry-type T] [--dry-run | --apply [--backup DIR]]
                [--expect-db-sha256 SHA] [--note TEXT] [--json]
                                            B-188 通用条目编辑：默认 dry-run（逐字段 old→new、
                                            库 sha 前后、必填缺字段影响）；--apply 才写（sha 闸 +
                                            原子写 + 重新读库复核）。B-190 起 --backup 可选：不传 = 不写备份
                                             （GUI 改用 git 版本层），传了行为不变。--new-key 改 key
                                            （默认不改；旧 key 自动进记录 aliases）；有待裁决
                                            冲突的记录拒绝编辑；写库痕迹进记录 edits[]
    merge-records --keys A,B[,...] --new-key K [--entry-type T]
                [--field-source FIELD=KEY ...] [--dry-run | --apply [--backup DIR]]
                [--expect-db-sha256 SHA] [--note TEXT] [--json]
                                            B-188 合并 2+ 条重复记录为 1 条：新规范 key、
                                            旧 key 全进 aliases、来源合并、记录数 -N+1；
                                            B-201：--new-key 可**沿用 --keys 之一**
                                            （合并后其余旧 key 进 aliases；撞其它记录仍拒绝）；
                                            近重复用 --field-source 逐字段选源；默认 dry-run；
                                            B-190 起 --backup 同样可选（不传 = 不写备份）
    suggest-field-fixes [--registry missing_field|unknown_entry_type] [--limit N] [--json]
                                            B-188 只读：缺 title/year 的字段归位候选
                                            （A 类 265 条口径）+ 异常 entry_type 建议；
                                            不写库、不调 AI（GUI「字段归位预览」同源）
    normalize-editors [--dry-run | --apply --backup PATH]
                [--report PATH | --out-dir DIR] [--csv-out PATH]
                [--json-out PATH] [--json]
                                            B3 英文编者倒装（姓, 名缩写）：默认 dry-run（只出
                                            报告、绝不写库）；--apply 才写库（只改 editor；
                                            中文/机构/others 跳过；含「只有 editor」的记录）
    normalize-bib (--bib PATH | --stdin) [--json]
                                            只读：外部 .bib 交给**唯一实现**
                                            `slcn.bibnorm.normalize_bib()`（默认
                                            strip_urls=False）→ stdout 规范文本；--json 另给
                                            逐条字段前/后 diff。不改 key、不写库/文件、不调 AI；
                                            含 @string/@preamble/@comment 或 0 条目 → 退出 2；
                                            引号未闭合的条目原样保留（kept，需人工核对）

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
    /opt/homebrew/bin/python3.14 bibdb.py ack-duplicates --code near_duplicates \
        --keys <keyA>,<keyB> [--apply]   # 默认 dry-run；确认不同只写 sidecar、不改库
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
B-190 起 `edit` / `merge-records` 的 `--backup` 也**可选**：不传 = 不写备份
（`backup_path=""`、`edits[].backup_sha256=""`；GUI 改用 git 版本层），传了行为不变。
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

B-228 第一段（2026-10-26）：本文件已拆为薄门面 —— 逻辑搬进
`slcn/bibdb_cli.py`（`cmd_*` + argparse）、`slcn/bibdb_reports.py`（报告收集/渲染/写出）、
`slcn/bibdb_import.py`（导入/期扫描/rehash）与 `slcn/bibdb_core.py`（共用小工具），
本文件只 re-export 全部旧名字并保留 `main()`/`__main__` 入口。纯搬运、零行为变化；
`bibdb.X` 的引用与 CLI 契约逐字不变。
"""

from __future__ import annotations

import argparse
import copy
import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from slcn import bibcheck, bibdb, bibnorm, bibparse, fsutil, paths  # noqa: E402
from slcn.platform import utf8_stdio  # noqa: E402


# ── 小工具 ──────────────────────────────────────────────────────────────────



# ── B-228 第一段：门面 re-export（旧 `bibdb.X` 用法逐字不变；无循环 import）──
from slcn.bibdb_core import (
    _backup_dir_arg, _clip, _db_path, _fail, _field, _normalize_sha256_arg, _open_store,
    _print_changes, _print_import_report, _print_json, _print_stats, _print_written,
    _report_line, _report_out_dir, _resolve_report_out, _source_note, _store_snapshot,
    _utc_stamp, _write_db_backup, _write_guard,
)  # noqa: F401
from slcn.bibdb_reports import (
    AUTHOR_REPORT_APPLY, AUTHOR_REPORT_DRY, DUPLICATES_CSV_COLUMNS, DUPLICATES_MD_LIMIT,
    EDITOR_CSV_COLUMNS, EDITOR_REPORT_APPLY, EDITOR_REPORT_APPLY_CSV,
    EDITOR_REPORT_APPLY_JSON, EDITOR_REPORT_CSV, EDITOR_REPORT_DRY, EDITOR_REPORT_JSON,
    MERGE_APPLY2_ARCHIVE_CSV, MERGE_APPLY2_CLUSTERS_CSV, MERGE_APPLY2_REPORT_JSON,
    MERGE_APPLY2_REPORT_MD, MERGE_APPLY_ARCHIVE_CSV, MERGE_APPLY_CLUSTERS_CSV,
    MERGE_APPLY_REPORT_JSON, MERGE_APPLY_REPORT_MD, MERGE_CSV_COLUMNS,
    MERGE_FIELD_DIFF_NOTE_LIMIT, MERGE_IGNORE_KEYS, MERGE_MANUAL_CSV, MERGE_PLAN_JSON,
    MERGE_RENAMES_CSV, MERGE_REPORT_CSV, MERGE_REPORT_MD, _AUTHOR_SEG_SPLIT_RE,
    _BAD_FIELD_NAME_RE, _BIBTEX_FIELD_ORDER, _CJK_RE, _CONFLICT_ITEM_FIELDS,
    _DOI_KEY_RE, _EDITOR_MARK_RE, _EMBEDDED_ASSIGN_RE, _FIELD_LABELS,
    _GLUED_INITIALS_RE, _INSTITUTION_RE, _KEY_INITIAL_ARTICLE, _KEY_PARTICLES,
    _PLACEHOLDER_KEY_RE, _PUBLISHER_TOKEN_RE, _TITLE_MARKER_RE, _TYPE_RANK,
    _YEAR_TOKEN_RE, _author_skip_reason, _author_stats, _build_bib_entry, _count_by,
    _editor_flags_text, _editor_risk_flags, _editor_sources_text, _editor_stats,
    _editor_table_row, _field_label, _first_person_surname, _issue_sort_key,
    _key_anomaly, _md_code, _md_escape, _merge_cluster_fields, _merge_cluster_row,
    _merge_conflicts_union, _merge_field_equivalent, _merge_key_name,
    _merge_manual_research_table, _merge_prefer_value, _merge_sources_union,
    _merge_title_sort_key, _merge_version_score, _normalize_error_reason,
    _normalize_issue_text, _normalize_pending_conflict_fields, _normalize_rule,
    _normalize_stats, _plain_cjk_len, _public_conflict_item, _record_brace_score,
    _record_brief, _record_fields, _record_filled, _record_issues, _record_sources,
    _record_year, _role_part_count, _segment_candidates, _source_text,
    _strip_title_wrapper, _unescaped, _value_shape_risks, _write_author_reports,
    _write_editor_reports, _write_merge_apply_reports, _write_merge_reports,
    _write_report, build_author_json, build_author_report_md, build_conflicts_md,
    build_duplicates_csv, build_duplicates_md, build_editor_csv, build_editor_json,
    build_editor_report_md, build_merge_apply_clusters_csv, build_merge_apply_json,
    build_merge_apply_report_md, build_merge_archive_csv, build_merge_manual_csv,
    build_merge_plan, build_merge_plan_csv, build_merge_plan_json,
    build_merge_renames_csv, build_merge_report_md, build_normalize_apply_json,
    build_normalize_apply_md, build_normalize_json, build_normalize_md,
    collect_author_rows, collect_conflict_items, collect_editor_rows,
    collect_field_fix_rows, conflict_totals,
)  # noqa: F401
from slcn.bibdb_import import (
    REHASH_MIGRATION_NOTE, _change_rows, _clip_sha, _import_one_issue,
    _issue_dir_if_any, _rehash_write, cmd_import_bib, cmd_import_dir, cmd_init,
    cmd_rehash, collect_rehash_rows,
)  # noqa: F401
from slcn.bibdb_cli import (
    ACK_DUPLICATES_DEFAULT_REASON, NORMALIZE_JSON_NAME, NORMALIZE_REPORT_NAME,
    _FIELD_NAME_RE, _NON_BIB_ENTRY_RE, _QUOTE_KEPT_RISK, _append_edit_trace,
    _apply_edit_plan, _apply_merge_plan, _author_verification,
    _build_normalize_bib_payload, _canonical_edit_fields, _check_edit_apply_guards,
    _clone_store, _cmd_normalize_apply, _duplicate_content_groups,
    _editor_apply_verification, _editor_verification, _entry_identity,
    _entry_type_or_error, _fields_by_name, _fields_diff, _generate_reports,
    _key_collision, _load_verify, _merge_fields, _missing_required,
    _normalize_verification, _normalized_entry_fields, _parse_field_sources,
    _pending_conflict_labels, _raw_field_diff, _read_edit_input, _read_json_fields_arg,
    _record_brief_payload, _replace_from_bib, _resolve_adopt_all, _source_identity,
    _store_sha, _store_text, apply_author_rows, apply_editor_rows,
    apply_merge_decisions, apply_merge_plan, apply_normalize_rows, build_parser,
    cmd_ack_duplicates, cmd_conflicts, cmd_edit, cmd_lint, cmd_merge_duplicates,
    cmd_merge_records, cmd_normalize, cmd_normalize_authors, cmd_normalize_bib,
    cmd_normalize_editors, cmd_query, cmd_report, cmd_report_conflicts,
    cmd_report_duplicates, cmd_resolve, cmd_restore, cmd_stats, cmd_suggest_field_fixes,
    collect_duplicate_rows, collect_normalize_rows, load_duplicate_pairs, render_bibtex,
)  # noqa: F401

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
