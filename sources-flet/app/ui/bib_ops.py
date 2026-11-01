"""引用库高级操作视图（批 109 W1b）：编辑 / 查重合并 / 字段归位 / 库健康与批量。

页面只做三件事：取值（输入控件）→ 拼参数（services.engine_bridge）→ 显示结果
（services.bib_git 只管本地 git）。**不复制任何引擎判据**：字段校验、合并字段、
近似重复、字段归位候选全由 CLI 输出决定。写库一律三步：

1. `--dry-run --json` 预览（只算不写），记下 `db_sha256_before`；
2. 用户二次确认后去掉 `--dry-run`，带 `--expect-db-sha256` 真写（sha 不符引擎会拒写）；
3. 写成功后只提交 `bib_db/bibliography.jsonl` 一条（批量操作也合 1 条；绝不推送）。

两条硬边界：

* `report-duplicates` 的 `--out` 必须传临时目录 —— 默认写进库目录 = 笔记仓；本模块
  读完 `duplicates.csv` 立即删掉临时目录，笔记仓不落一个字节。
* `ack-duplicates` 只写 sidecar `health-ignore.json`（库字节一个都不动），不自动提交。
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
import shutil
import tempfile
from pathlib import Path

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import components as C
from ui.bib_write import explain_result

#: 引擎认的 15 种条目类型（与 `Engine/slcn/bibdb.py` 的 ENTRY_TYPES 一致，只做下拉候选）
ENTRY_TYPES = ("article", "book", "booklet", "collection", "inbook", "incollection",
               "inproceedings", "manual", "mastersthesis", "misc", "online",
               "phdthesis", "proceedings", "techreport", "unpublished")
#: 常用字段的编辑顺序（其余字段按字母序排在后面）
EDIT_FIELD_ORDER = ("title", "author", "editor", "translator", "year", "publisher",
                    "address", "journal", "booktitle", "volume", "number", "pages",
                    "edition", "series", "school", "institution", "doi", "url",
                    "isbn", "language", "note")
FIELD_LABEL = {
    "title": "题名", "author": "作者", "editor": "编者", "translator": "译者",
    "year": "年份", "publisher": "出版社", "address": "出版地", "booktitle": "书名",
    "journal": "期刊", "volume": "卷", "number": "期", "pages": "页码",
    "edition": "版次", "series": "丛书", "school": "学校", "institution": "机构",
    "doi": "DOI", "url": "URL", "isbn": "ISBN", "issn": "ISSN", "language": "语言",
    "note": "备注", "month": "月份", "chapter": "章节", "organization": "组织",
    "type": "类型", "eprint": "电子印本", "howPublished": "出版方式",
}
#: 查重候选一屏最多显示多少对（CSV 全量仍在，翻页留给后续批）
DUPE_SHOW = 20
#: 字段归位一次最多拉多少条候选
FIX_LIMIT = 50


# ── 纯函数（可直接单测，不碰 flet / 不调引擎）──────────────────────────
def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(_text(item) for item in value)
    return str(value)


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def field_label(name: str) -> str:
    name = str(name or "")
    return FIELD_LABEL.get(name, name)


def record_from_payload(payload: dict | None) -> dict:
    """从 `query --json` 的 payload 取记录（`hits[].record`；兼容直接给 record）。"""
    payload = payload or {}
    hits = payload.get("hits")
    if isinstance(hits, list):
        for hit in hits:
            record = (hit or {}).get("record") if isinstance(hit, dict) else None
            if isinstance(record, dict) and record.get("key"):
                return record
    for name in ("record", "item"):
        record = payload.get(name)
        if isinstance(record, dict) and record.get("key"):
            return record
    return {}


def ordered_fields(record: dict | None) -> list[tuple[str, str]]:
    """字段按固定顺序 + 其余字母序；值一律转成字符串（控件只显示文本）。"""
    fields = (record or {}).get("fields") or {}
    if not isinstance(fields, dict):
        return []
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in EDIT_FIELD_ORDER:
        if name in fields:
            rows.append((name, _text(fields.get(name))))
            seen.add(name)
    for name in sorted(fields):
        if name not in seen:
            rows.append((name, _text(fields.get(name))))
    return rows


def edit_payload_fields(texts: dict | None) -> dict:
    """控件文本 → `--json-fields` 的字段字典（去首尾空白；**空 = 删除该字段**）。"""
    payload: dict[str, str] = {}
    for name, value in (texts or {}).items():
        name = str(name or "").strip()
        if not name:
            continue
        value = _text(value).strip()
        if value:
            payload[name] = value
    return payload


def edit_validation_error(key: str, fields: dict | None) -> str:
    if not str(key or "").strip():
        return "缺条目 key：先按 key 载入要编辑的条目。"
    if not fields:
        return "所有字段都空着：至少留一个字段（清空某一格 = 删除该字段）。"
    if "entry_type" in (fields or {}) and not _text(fields["entry_type"]).strip():
        return "条目类型不能为空。"
    return ""


def edit_diff_lines(changes: dict | None, limit: int = 8) -> list[str]:
    changes = changes if isinstance(changes, dict) else {}
    lines: list[str] = []
    for name, change in changes.items():
        change = change if isinstance(change, dict) else {"new": change}
        old = _text(change.get("old")) or "（空）"
        new = _text(change.get("new")) or "（空）"
        lines.append(f"{field_label(name)}：{old} → {new}")
        if len(lines) >= limit:
            rest = len(changes) - limit
            if rest > 0:
                lines.append(f"…另 {rest} 个字段有变化")
            break
    return lines


def ops_notice(label: str, payload: dict | None, commit: dict | None) -> str:
    """写库结果人读一句（是否落盘 / 是否生成版本提交）。"""
    payload, commit = payload or {}, commit or {}
    if not payload.get("written"):
        return f"{label}（库文件没有改动，未生成版本提交）"
    if commit.get("committed"):
        return f"{label}；已生成版本提交 {GIT.short_sha(str(commit.get('sha') or ''), 8)}"
    reason = str(commit.get("error") or commit.get("reason") or "").strip()
    return f"{label}；" + (f"版本层提示：{reason}" if reason else "未生成版本提交")


def merge_preview_lines(payload: dict | None, limit: int = 12) -> list[str]:
    payload = payload or {}
    keys = "、".join(_text(item) for item in payload.get("keys") or [])
    lines = [f"合并 {keys or '?'} → {payload.get('new_key') or '?'}"
             f"（{payload.get('entry_type') or '?'}）"]
    comparison = payload.get("field_comparison") or {}
    if isinstance(comparison, dict):
        for field, item in comparison.items():
            if not isinstance(item, dict):
                continue
            chosen = _text(item.get("chosen")) or "默认"
            value = _text(item.get("value")) or "（空）"
            options = [f"{key}={_text(val) or '空'}" for key, val in item.items()
                       if key not in ("chosen", "value")]
            tail = f"（候选：{'；'.join(options)}）" if options else ""
            lines.append(f"{field_label(field)} ← {chosen}：{value}{tail}")
            if len(lines) >= limit:
                lines.append("…（更多字段见引擎输出）")
                break
    if len(lines) == 1:
        fields = payload.get("fields") or {}
        lines += [f"{field_label(name)}：{_text(value)}" for name, value in fields.items()]
    return lines


def duplicate_rows(csv_text: str, limit: int = 200) -> list[dict]:
    """`duplicates.csv` 文本 → 候选对列表（列序见 `slcn/bibdb_rEports`；容忍 BOM）。"""
    if not str(csv_text or "").strip():
        return []
    rows: list[dict] = []
    for row in csv.DictReader(io.StringIO(str(csv_text).lstrip("\ufeff"))):
        if not _text(row.get("key_a")).strip() or not _text(row.get("key_b")).strip():
            continue
        rows.append(dict(row))
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def duplicate_pair_title(row: dict | None) -> str:
    row = row or {}
    left = _text(row.get("key_a")) or "?"
    right = _text(row.get("key_b")) or "?"
    similarity = _text(row.get("similarity"))
    return f"{left} ≈ {right}" + (f"（相似度 {similarity}）" if similarity else "")


def duplicate_pair_lines(row: dict | None) -> list[str]:
    row = row or {}
    lines = []
    for side in ("a", "b"):
        title = _text(row.get(f"title_{side}")) or "（无题名）"
        author = _text(row.get(f"author_{side}")) or "（无作者）"
        year = _text(row.get(f"year_{side}")) or "—"
        kind = _text(row.get(f"entry_type_{side}")) or "?"
        verified = _text(row.get(f"verified_{side}")) or "?"
        lines.append(f"{side.upper()}：{title} · {author} · {year} · {kind} · 已核实 {verified}")
    issues = _text(row.get("issues_a")) or _text(row.get("issues_b"))
    if issues:
        lines.append(f"库检查提示：{issues}")
    return lines


def field_fix_rows(payload: dict | None) -> list[dict]:
    rows = (payload or {}).get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def field_fix_missing(row: dict | None) -> list[str]:
    return [str(item) for item in (row or {}).get("missing") or [] if str(item).strip()]


def field_fix_candidates(row: dict | None, field: str) -> list[dict]:
    found = []
    for item in (row or {}).get("candidates") or []:
        if isinstance(item, dict) and str(item.get("field") or "") == str(field or ""):
            found.append(item)
    return found


def field_fix_fields(row: dict | None, picked: dict | None = None) -> dict:
    """原始字段 + 本次选中的候选值（提交给 `edit --json-fields` 的完整字段）。"""
    fields = dict((row or {}).get("fields") or {})
    for field, value in (picked or {}).items():
        if str(value or "").strip():
            fields[str(field)] = str(value).strip()
    return fields


def field_fix_line(row: dict | None, picked: dict | None = None) -> str:
    key = _text((row or {}).get("key")) or "?"
    kind = _text((row or {}).get("entry_type")) or "?"
    chosen = {name: value for name, value in (picked or {}).items() if str(value or "").strip()}
    text = "、".join(f"{field_label(name)}＝{value}" for name, value in chosen.items())
    return f"{key}（{kind}）：{text or '（未选候选）'}"


def health_counts(lint_payload: dict | None, conflicts_payload: dict | None = None) -> dict:
    counts = (lint_payload or {}).get("counts") or {}
    conflicts = counts.get("conflicts") if isinstance(counts.get("conflicts"), dict) else {}
    resolved = counts.get("conflicts_resolved")
    resolved_records = _int((resolved or {}).get("records")) if isinstance(resolved, dict) else 0
    totals = (conflicts_payload or {}).get("totals") or {}
    invert = counts.get("needs_inversion") or {}
    return {
        "records": _int(counts.get("records")),
        "verified": _int(counts.get("verified")),
        "unverified": _int(counts.get("unverified")),
        "errors": _int(counts.get("errors")),
        "warnings": _int(counts.get("warnings")),
        "conflicts": _int(conflicts.get("records")),
        "conflicts_total": _int(conflicts.get("total")),
        "pending": _int(totals.get("pending")),
        "resolved": _int(totals.get("resolved")) or resolved_records,
        "near": _int(counts.get("near_duplicates")),
        "acknowledged": _int(counts.get("near_duplicates_acknowledged")),
        "invert_author": _int(invert.get("author")),
        "invert_editor": _int(invert.get("editor")),
        "invert_institution": _int(invert.get("institution")),
    }


def health_lines(counts: dict | None) -> list[str]:
    counts = counts or {}
    return [
        f"记录 {_int(counts.get('records'))} 条：已核实 {_int(counts.get('verified'))}，"
        f"未核实 {_int(counts.get('unverified'))}",
        f"库检查：错误 {_int(counts.get('errors'))}，警告 {_int(counts.get('warnings'))}",
        f"冲突 {_int(counts.get('conflicts_total'))} 条"
        f"（涉及 {_int(counts.get('conflicts'))} 条记录）："
        f"待裁决 {_int(counts.get('pending'))}，已裁决 {_int(counts.get('resolved'))}",
        f"近似重复 {_int(counts.get('near'))} 对（已确认非重复 {_int(counts.get('acknowledged'))}）",
        f"需反转字段：作者 {_int(counts.get('invert_author'))}、"
        f"编者 {_int(counts.get('invert_editor'))}、"
        f"机构 {_int(counts.get('invert_institution'))}",
    ]


def batch_preview_lines(payload: dict | None) -> list[str]:
    """批量裁决 dry-run 的人读预览（条数 / 受影响记录 / sha 前后）。"""
    payload = payload or {}
    before = payload.get("totals_before") or {}
    after = payload.get("totals_after") or {}
    adopted = payload.get("adopted")
    lines = [
        f"待裁决 {_int(before.get('pending'))} 条 → {_int(after.get('pending'))} 条",
        f"受影响记录 {_int(payload.get('records'))} 条、冲突 {_int(payload.get('conflicts'))} 条"
        f"（采用 {len(adopted) if isinstance(adopted, list) else 0} 条）",
        f"库 sha256：{GIT.short_sha(str(payload.get('db_sha256_before') or ''), 8)}"
        f" → {GIT.short_sha(str(payload.get('db_sha256_after') or ''), 8)}",
    ]
    skipped = payload.get("skipped")
    if isinstance(skipped, list) and skipped:
        lines.append(f"跳过 {len(skipped)} 条（缺新稿值或冲突指纹）")
    superseded = payload.get("superseded")
    if isinstance(superseded, list) and superseded:
        lines.append(f"被新稿取代 {len(superseded)} 条")
    if not payload.get("changed"):
        lines.append("本次没有可采用的待裁决冲突（库与预览一致，零写入）。")
    return lines


def git_recent_lines(probe: dict | None, limit: int = 5) -> list[str]:
    probe = probe or {}
    if not probe.get("ok"):
        return [f"版本层：{probe.get('error') or '不可用'}"]
    commits = [row for row in probe.get("commits") or [] if isinstance(row, dict)]
    if not commits:
        return ["最近库提交：（读不到）"]
    return [f"{GIT.short_sha(str(row.get('sha') or ''), 8)} {row.get('subject') or ''}"
            for row in commits[:limit]]


# ── 视图共同部分 ────────────────────────────────────────────────────────
class _OpsView:
    """四个视图的共同部分：取数（只读/预览）、写库 + 版本提交、提示与刷新。"""

    def __init__(self, page) -> None:
        self.page = page
        self.busy = False
        self.loading = False
        self.loaded = False
        self.error = ""
        self.notice = ""
        self.notice_tone = "info"
        self.commit: dict | None = None

    @property
    def t(self):
        return self.page.t

    def _notes(self) -> str:
        return str(getattr(self.page.app, "notes_repo", "") or "")

    def _db(self) -> str:
        stats = self.page.stats_data or {}
        db = str(stats.get("db") or "").strip()
        if db:
            return db
        try:
            return GIT.default_db(self._notes())
        except Exception:  # noqa: BLE001  只用于显示与降级
            return ""

    def _refresh(self) -> None:
        self.page._render()

    def _toast(self, message: str, tone: str = "") -> None:
        self.notice, self.notice_tone = str(message or ""), (tone or "info")
        try:
            self.page.app.toast(str(message or ""))
        except Exception:  # noqa: BLE001  提示失败不影响主流程
            pass

    def _confirm(self, *, title: str, message: str, confirm_label: str, on_confirm) -> None:
        # components.confirm 的 on_confirm 不带参数（它会自己拉起对话框）；这里适配各视图
        # 统一写的 `lambda e: …` 形式。
        C.confirm(self.page.page, self.t, title=title, message=message,
                  confirm_label=confirm_label, on_confirm=lambda: on_confirm(None))

    def _run(self, task) -> None:
        """调度协程对象（调用点写 `self._run(self._load())`）。

        flet 1.0.5 的 `Page.run_task` 只收 coroutine function（GUI-04b），所以把协程
        对象包成 async 函数再交给它；签名与行为不变，调用点无需改动。
        """
        async def _await_task() -> None:
            await task

        self.page.page.run_task(_await_task)

    async def _read(self, call) -> dict:
        try:
            return await asyncio.to_thread(EB.read_json_command, call, notes=self._notes())
        except Exception as exc:  # noqa: BLE001  读引擎失败降级为可见错误
            return {"ok": False, "json": {}, "code": -1, "lines": [],
                    "error": f"读取引擎失败：{exc}"}

    async def _commit_write(self, payload: dict | None, *, label: str, count: int) -> dict:
        payload = payload or {}
        self.commit = None
        if not payload.get("written"):
            return {}
        db = str(payload.get("db") or "") or self._db()
        try:
            message = GIT.commit_message(
                label, count, str(payload.get("db_sha256_before") or ""),
                str(payload.get("db_sha256_after") or ""))
            commit = await asyncio.to_thread(GIT.commit_db, db, message)
        except Exception as exc:  # noqa: BLE001  版本层异常只降级提示，不回滚数据
            commit = {"ok": False, "committed": False, "soft": True,
                      "error": f"版本层异常：{exc}"}
        self.commit = commit if isinstance(commit, dict) else {"committed": bool(commit)}
        try:
            self.page.mark("bib_git_commit", **self.commit)
        except Exception:  # noqa: BLE001
            pass
        return self.commit

    def _notice_bar(self) -> ft.Control | None:
        if not self.notice:
            return None
        return C.notice_bar(self.notice, self.t, self.notice_tone)

    def _error_bar(self) -> ft.Control | None:
        if not self.error:
            return None
        return C.notice_bar(self.error, self.t, "error")


# ── R1 条目编辑 ─────────────────────────────────────────────────────────
class BibEditorView(_OpsView):
    """编辑条目：按 key 载入 → 改字段 → dry-run 预览 → 二次确认 → 写库 + 1 条提交。"""

    def __init__(self, page) -> None:
        super().__init__(page)
        self.key_text = ""
        self.record: dict = {}
        self.field_values: dict[str, str] = {}
        self.entry_type = ""
        self.new_key = ""
        self.preview: dict | None = None
        self.pending: dict | None = None

    def load_key(self, key: str) -> None:
        """供外部（查库 / 待处理）按 key 打开编辑器。"""
        self.key_text = str(key or "").strip()
        self.record, self.field_values = {}, {}
        self.entry_type, self.new_key = "", ""
        self.preview, self.pending = None, None
        self._run(self._load())

    async def _load(self) -> None:
        key = str(self.key_text or "").strip()
        if not key:
            self.error = "先填要编辑的条目 key（也可以从「查库」复制）。"
            self._refresh()
            return
        self.busy, self.error = True, ""
        self._refresh()
        result = await self._read(EB.bibdb_query_basic_call("key", key, bibtex=False))
        self.busy = False
        record = record_from_payload(result.get("json") or {})
        if not record:
            self.record, self.field_values = {}, {}
            self.error = (explain_result(result) if result.get("code") not in (0, None)
                          else f"引用库里没有 key = {key} 的条目。")
            self._refresh()
            return
        self.record = record
        self.field_values = {name: value for name, value in ordered_fields(record)}
        self.entry_type = _text(record.get("entry_type"))
        self.new_key, self.preview, self.pending = "", None, None
        self.page.mark("bib_edit_load", key=_text(record.get("key")), ok=True,
                       entry_type=self.entry_type)
        self._refresh()

    def _ask_preview(self) -> None:
        self._run(self._preview())

    async def _preview(self) -> None:
        record_key = _text((self.record or {}).get("key"))
        fields = edit_payload_fields(self.field_values)
        error = edit_validation_error(record_key, fields)
        if error:
            self.error = error
            self._refresh()
            return
        self.busy, self.error = True, ""
        self._refresh()
        result = await self._read(EB.bibdb_edit_call(
            key=record_key, fields=fields, dry_run=True,
            new_key=str(self.new_key or "").strip(),
            entry_type=str(self.entry_type or "").strip()))
        self.busy = False
        payload = result.get("json") or {}
        if not result.get("ok") or not payload:
            self.preview, self.pending = None, None
            self.error = explain_result(result)
            self._refresh()
            return
        self.preview = payload
        self.pending = {"key": record_key, "fields": fields, "dry_run": False,
                        "new_key": str(self.new_key or "").strip(),
                        "entry_type": str(self.entry_type or "").strip(),
                        "expect_db_sha256": str(payload.get("db_sha256_before") or "")}
        self.page.mark("bib_edit_preview", key=record_key, dry_run=True,
                       changed=len(payload.get("field_changes") or {}))
        self._refresh()

    def _ask_write(self) -> None:
        if not self.pending:
            return
        self._confirm(
            title="写入引用库？",
            message=("确认后去掉 --dry-run 并带 --expect-db-sha256 真写；"
                     "写成功后只提交 bib_db/bibliography.jsonl 一条（不推送）。\n"
                     "预览时看到的字段变化就是全部改动；库若已被别处修改，引擎会拒写。"),
            confirm_label="确认写入",
            on_confirm=lambda e: self._run(self._write()))

    async def _write(self) -> None:
        pending = dict(self.pending or {})
        if not pending:
            return
        self.busy, self.error = True, ""
        self._refresh()
        result = await self._read(EB.bibdb_edit_call(**pending))
        self.busy = False
        payload = result.get("json") or {}
        if not result.get("ok") or not payload:
            self.error = explain_result(result)
            self._refresh()
            return
        self.preview, self.pending = None, None
        label = "编辑 " + (_text(payload.get("new_key")) or _text(payload.get("key")) or "")
        await self._commit_write(payload, label=label.strip(), count=1)
        self.page.stats_data = None
        self._toast(ops_notice("编辑已写入", payload, self.commit))
        self.key_text = _text(payload.get("new_key")) or _text(payload.get("key")) or self.key_text
        await self._load()

    # -- 构建 -----------------------------------------------------------
    def build(self) -> ft.Control:
        t = self.t
        controls: list = [self._input_card()]
        if self.record:
            controls.append(self._fields_card())
        for bar in (self._error_bar(),):
            if bar is not None:
                controls.append(bar)
        if self.preview:
            controls.append(self._preview_card())
        bar = self._notice_bar()
        if bar is not None:
            controls.append(bar)
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _input_card(self) -> ft.Control:
        t = self.t
        key_field = ft.TextField(
            label="条目 key", value=self.key_text, dense=True, width=t.px(220),
            text_size=t.font("body"), border_color=t.palette.border,
            focused_border_color=t.palette.accent,
            on_change=lambda e: setattr(self, "key_text", e.control.value or ""))
        buttons = ft.Row(spacing=t.sp("1"), wrap=True,
                         vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.secondary_button("载入条目", lambda e: self._run(self._load()), t, icon="SEARCH"),
            C.text("按 key 载入（也认别名）；只读预览不改库。", t, "caption",
                   t.palette.text_faint),
        ])
        return C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("编辑条目", t, note="先 dry-run 预览，再二次确认写库"),
            ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[key_field, buttons]),
        ]), t)

    def _fields_card(self) -> ft.Control:
        t = self.t
        record = self.record or {}
        meta = " · ".join([
            _text(record.get("key")),
            self.entry_type or "?",
            "已验证" if record.get("verified") else "未验证",
            f"别名 {len(record.get('aliases') or [])}",
            f"冲突 {len(record.get('conflicts') or [])}",
        ])
        boxes = []
        for name, value in ordered_fields(record):
            boxes.append(ft.TextField(
                label=field_label(name), value=_text(self.field_values.get(name, value)),
                dense=True, width=t.px(280), text_size=t.font("body"),
                border_color=t.palette.border, focused_border_color=t.palette.accent,
                on_change=lambda e, n=name: self.field_values.__setitem__(
                    n, e.control.value or "")))
        meta_row = ft.Row(spacing=t.sp("1"), wrap=True,
                          vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.TextField(
                label="条目类型", value=self.entry_type, dense=True, width=t.px(160),
                text_size=t.font("body"), border_color=t.palette.border,
                focused_border_color=t.palette.accent,
                on_change=lambda e: setattr(self, "entry_type", e.control.value or "")),
            ft.TextField(
                label="新 key（留空 = 不改）", value=self.new_key, dense=True, width=t.px(220),
                text_size=t.font("body"), border_color=t.palette.border,
                focused_border_color=t.palette.accent,
                on_change=lambda e: setattr(self, "new_key", e.control.value or "")),
        ])
        actions = ft.Row(spacing=t.sp("1"), wrap=True,
                         vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.primary_button("预览（dry-run）", lambda e: self._ask_preview(), t,
                             icon="VISIBILITY", disabled=self.busy),
            C.text("整份字段替换：清空一格 = 删除该字段；换 key 会把旧 key 记成别名。",
                   t, "caption", t.palette.text_faint),
        ])
        return C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title(f"字段 · {meta}", t, note="必填项由引擎校验，这里不复制判据"),
            meta_row,
            ft.Row(spacing=t.sp("1"), wrap=True, controls=boxes),
            actions,
        ]), t)

    def _preview_card(self) -> ft.Control:
        t = self.t
        payload = self.preview or {}
        lines = edit_diff_lines(payload.get("field_changes"))
        controls: list = [C.section_title(
            "预览（未写库）", t,
            note="库 sha256 " + GIT.short_sha(_text(payload.get("db_sha256_before")), 8))]
        controls += [C.text(line, t, "small", t.palette.text) for line in lines] or [
            C.text("字段没有变化。", t, "small", t.palette.text_muted)]
        aliases = payload.get("aliases_added")
        if isinstance(aliases, list) and aliases:
            controls.append(C.text("别名将新增：" + "、".join(map(str, aliases)),
                                   t, "small", t.palette.text_muted))
        missing = payload.get("missing_after")
        if isinstance(missing, list) and missing:
            controls.append(C.notice_bar(
                "写后缺字段：" + "、".join(field_label(name) for name in missing), t, "warn"))
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.primary_button("确认写入并提交", lambda e: self._ask_write(), t,
                             icon="SAVE", disabled=self.busy),
            C.secondary_button("放弃预览", lambda e: self._clear_preview(), t),
        ]))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _clear_preview(self) -> None:
        self.preview, self.pending = None, None
        self._refresh()
