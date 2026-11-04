"""引用库 · 编辑（写库视图；批 115 夜间重设计，BPAGES-B）。

契约：`evidence/b115-bib-redesign/pages/edit/spec.md`。按 key 载入一条记录，
改字段 / 类型 / key：dry-run 预览 → 页内二次确认（影响 + 库 sha）→ 原子写
（`--expect-db-sha256` + `--backup`）→ 本地提交（不 push）→ 可回退本次提交。

`--json-fields` 永远是**整份字段字典**：清空一个格子 = 删除该字段。
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C

VIEW_KEY = "edit"
TITLE = "编辑"
ROLE = "write"

#: 与 `Engine/slcn/bibdb.py` 的 ENTRY_TYPES 一致（只做下拉候选）
ENTRY_TYPES = ("article", "book", "booklet", "collection", "inbook", "incollection",
               "inproceedings", "manual", "mastersthesis", "misc", "online",
               "phdthesis", "proceedings", "techreport", "unpublished")
#: 常用字段顺序；其余按字母序排在后面（同 `bib_ops.ordered_fields`）
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


def _sha8(sha: str) -> str:
    return (sha or "")[:8]


def _s(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(_s(item) for item in value)
    return str(value)


def field_label(name: str) -> str:
    return FIELD_LABEL.get(str(name or ""), str(name or ""))


def record_from_payload(payload: dict | None) -> dict:
    """从 `query --json` 取记录（`hits[].record`，兼容直接给 record）。"""
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


def diff_rows(payload: dict | None) -> list[dict]:
    """把引擎的 `field_changes` 归一成 [{field, old, new}]；兼容 dict / list 两种形态。"""
    raw = (payload or {}).get("field_changes")
    rows: list[dict] = []
    if isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, dict):
                old = value.get("old", value.get("before", ""))
                new = value.get("new", value.get("after", ""))
            elif isinstance(value, (list, tuple)) and len(value) >= 2:
                old, new = value[0], value[1]
            else:
                old, new = "", value
            rows.append({"field": str(name), "old": _s(old), "new": _s(new)})
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("field") or item.get("name") or ""
            if not name:
                continue
            rows.append({"field": str(name),
                         "old": _s(item.get("old", item.get("before", ""))),
                         "new": _s(item.get("new", item.get("after", "")))})
    return [row for row in rows if row["field"]]


class BibEditView:
    """编辑页：左字段 504（两列 240）/ 右差异 354；确认条 56 横跨两栏。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = getattr(page, "app", None)
        self.t = getattr(page, "t", None)
        self.key_input = ""
        self.record: dict = {}
        self.loaded_key = ""
        self.values: dict[str, str] = {}
        self.original: dict[str, str] = {}
        self.entry_type = ""
        self.new_key = ""
        self.payload: dict | None = None
        self.expect_sha = ""
        self.state = "empty"          # empty/loading/ready/running/failure/stale/success
        self.error = ""
        self.stderr = ""
        self.conflicts: list = []
        self.busy = False
        self.dirty_reason = ""
        self.confirm_layer = False
        self.result: dict | None = None
        self.commit: dict | None = None
        self.drawer_open = False
        self._root: ft.Container | None = None
        self._diff_holder: ft.Container | None = None
        self._bottom_holder: ft.Container | None = None

    # ── 宿主小工具 ───────────────────────────────────────────────────
    def _flet(self):
        return getattr(self.page, "page", None)

    def _store(self):
        store = getattr(self.page, "store", None)
        if store is None and self.app is not None:
            store = getattr(self.app, "store", None)
        return store

    def _store_get(self, key, default=None):
        store = self._store()
        if store is None or not hasattr(store, "get"):
            return default
        try:
            return store.get(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _store_set(self, key, value) -> None:
        store = self._store()
        if store is not None and hasattr(store, "set"):
            try:
                store.set(key, value)
            except Exception:  # noqa: BLE001
                pass

    def _notes(self) -> str:
        return str(getattr(self.app, "notes_repo", "") or "")

    def _db(self) -> str:
        stats = getattr(self.page, "stats_data", None)
        db = stats.get("db") if isinstance(stats, dict) else ""
        if not db:
            try:
                db = GIT.default_db(self._notes())
            except Exception:  # noqa: BLE001
                db = ""
        return str(db or "")

    def _task(self, fn) -> None:
        runner = getattr(self.page, "run_task_ui", None)
        if callable(runner):
            runner(fn)
            return
        page = self._flet()
        if page is not None and hasattr(page, "run_task"):
            page.run_task(fn)
            return
        asyncio.ensure_future(fn())

    def _notify(self, message: str) -> None:
        notify = getattr(self.page, "notify", None)
        if callable(notify):
            notify(message)
            return
        toast = getattr(self.app, "toast", None)
        if callable(toast):
            toast(message)

    def _mark(self, event: str, **fields) -> None:
        mark = getattr(self.page, "mark", None)
        if callable(mark):
            try:
                mark(event, **fields)
            except Exception:  # noqa: BLE001
                pass

    def _render(self) -> None:
        if self._root is not None:
            self._root.content = self._body()
            replant = getattr(self.page, "_replant_nav", None)
            if callable(replant):
                replant()
        page = self._flet()
        update = getattr(page, "update", None)
        if callable(update):
            try:
                update()
            except Exception:  # noqa: BLE001
                pass

    def _touch(self) -> None:
        """只刷新差异栏与确认条：输入时重建整页会丢焦点。"""
        if self._diff_holder is not None:
            self._diff_holder.content = self._diff_col_body()
        if self._bottom_holder is not None:
            self._bottom_holder.content = self._bottom()
        page = self._flet()
        update = getattr(page, "update", None)
        if callable(update):
            try:
                update()
            except Exception:  # noqa: BLE001
                pass

    async def _call(self, call: EB.EngineCall) -> dict:
        try:
            return await asyncio.to_thread(EB.read_json_command, call, notes=self._notes())
        except RuntimeError as exc:
            return {"ok": False, "json": {}, "error": str(exc), "code": None}

    # ── 宿主接口 ─────────────────────────────────────────────────────
    def build(self) -> ft.Control:
        self._root = ft.Container(content=self._body(), expand=True)
        return self._root

    def activate(self) -> None:
        focus = self._store_get("bib_focus_key")
        if focus:
            self._store_set("bib_focus_key", None)
            self.key_input = str(focus)
            self._task(self._load)

    def refresh_running(self) -> None:
        return

    def header_actions(self) -> list[ft.Control]:
        return []

    def primary_action(self) -> ft.Control | None:
        return None

    # ── 表单状态 ─────────────────────────────────────────────────────
    def _fields_payload(self) -> dict:
        return {name: value for name, value in self.values.items() if value.strip()}

    def _is_stale(self) -> bool:
        return bool(self.dirty_reason) and self.payload is not None

    def _can_confirm(self) -> bool:
        return bool(self.payload) and bool(self.expect_sha) and not self._is_stale() \
            and not self.busy and not self.conflicts \
            and self.state not in ("failure", "stale", "success", "loading")

    def _invalidate(self, reason: str) -> None:
        if self.payload is not None and not self.dirty_reason:
            self.dirty_reason = reason
        self.confirm_layer = False

    def _field_names(self) -> list[str]:
        seen: list[str] = []
        for name in EDIT_FIELD_ORDER:
            if name in self.values:
                seen.append(name)
        for name in sorted(self.values):
            if name not in seen:
                seen.append(name)
        for name in EDIT_FIELD_ORDER:
            if len(seen) >= 12:
                break
            if name not in seen:
                seen.append(name)
        return seen

    def _changed_fields(self) -> list[str]:
        return [name for name, value in self.values.items()
                if value.strip() != self.original.get(name, "").strip()]

    # ── 载入 ─────────────────────────────────────────────────────────
    def _load_click(self, e=None) -> None:
        self.key_input = self.key_input.strip()
        if not self.key_input:
            self.error = "先输入要编辑的条目 key"
            self._render()
            return
        if self._changed_fields():
            self._ask_discard()
            return
        self._task(self._load)

    def _ask_discard(self) -> None:
        t = self.t
        page = self._flet()
        if page is None or not hasattr(page, "show_dialog"):
            return
        count = len(self._changed_fields())

        def _close(e=None) -> None:
            try:
                page.pop_dialog()
            except Exception:  # noqa: BLE001
                pass

        def _ok(e=None) -> None:
            _close()
            self._task(self._load)

        dialog = ft.AlertDialog(
            modal=True,
            title=C.text(f"放弃未写入的 {count} 处修改？", t, role="dialog-title"),
            content=ft.Container(width=t.px(360), content=C.text(
                "载入另一条记录会丢掉当前草稿；这些修改从未写库。", t, "body",
                t.palette.text_muted)),
            actions=[C.text_button("继续编辑", _close, t),
                     C.danger_button("放弃修改", _ok, t)])
        page.show_dialog(dialog)

    async def _load(self) -> None:
        if self.busy:
            return
        key = self.key_input.strip()
        self.busy = True
        self.state = "loading"
        self.error = ""
        self._render()
        res = await self._call(EB.bibdb_query_call(key=key, limit=10))
        self.busy = False
        if not res.get("ok"):
            self.state = "failure"
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self._mark("bib_edit_load", ok=False, code=res.get("code"))
            self._render()
            return
        record = record_from_payload(res.get("json") or {})
        if not record:
            self.state = "failure"
            self.error = f"没有找到 key = {key} 的记录"
            self._render()
            return
        self.record = record
        self.loaded_key = str(record.get("key") or key)
        self.key_input = self.loaded_key
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        self.original = {str(name): _s(value) for name, value in fields.items()}
        self.values = dict(self.original)
        self.entry_type = str(record.get("entry_type") or "")
        self.new_key = ""
        conflicts = record.get("conflicts")
        self.conflicts = [c for c in conflicts if isinstance(c, dict)] \
            if isinstance(conflicts, list) else []
        self.payload = None
        self.expect_sha = ""
        self.dirty_reason = ""
        self.confirm_layer = False
        self.state = "ready"
        self._mark("bib_edit_load", ok=True, key=self.loaded_key,
                   fields=len(self.original), conflicts=len(self.conflicts))
        self._render()

    def _error_text(self, res: dict) -> str:
        payload = res.get("json") or {}
        for value in (payload.get("error"), res.get("error"), res.get("stderr")):
            if value:
                return str(value).strip().splitlines()[0][:300]
        return "引擎没有返回原因。"

    # ── 输入 ─────────────────────────────────────────────────────────
    def _on_key_input(self, e) -> None:
        self.key_input = e.control.value or ""

    def _on_field(self, name: str, e) -> None:
        self.values[name] = e.control.value or ""
        self._invalidate("字段已修改，请重新预览")
        self._touch()

    def _on_type(self, e) -> None:
        self.entry_type = str(getattr(e, "data", None) or self.entry_type)
        self._invalidate("条目类型已修改，请重新预览")
        self._touch()

    def _on_new_key(self, e) -> None:
        self.new_key = (e.control.value or "").strip()
        self._invalidate("新 key 已修改，请重新预览")
        self._touch()

    def _clear_draft(self, e=None) -> None:
        if not self.record:
            self.key_input = ""
            self.values = {}
            self._render()
            return
        self.values = dict(self.original)
        self.entry_type = str(self.record.get("entry_type") or "")
        self.new_key = ""
        self.payload = None
        self.expect_sha = ""
        self.dirty_reason = ""
        self.state = "ready"
        self._notify("草稿已清空（回到载入时的内容）")
        self._render()

    # ── 引擎：预览 / 写入 / 回退 ─────────────────────────────────────
    def _build_call(self, *, dry_run: bool, expect_sha: str = "") -> EB.EngineCall:
        return EB.bibdb_edit_call(
            key=self.loaded_key, fields=self._fields_payload(),
            new_key=self.new_key, entry_type=self.entry_type,
            dry_run=dry_run, expect_db_sha256=expect_sha,
            backup="" if dry_run else self._backup_dir(), db=self._db())

    def _backup_dir(self) -> str:
        return tempfile.mkdtemp(prefix="bib-edit-backup-")

    async def _preview(self) -> None:
        if self.busy or not self.loaded_key:
            if not self.loaded_key:
                self._notify("先载入一条记录")
            return
        if self.conflicts:
            self._notify("这条记录还有待裁决冲突，先去「待处理」裁决")
            return
        self.busy = True
        self.state = "loading"
        self.error = ""
        self.confirm_layer = False
        self._render()
        res = await self._call(self._build_call(dry_run=True))
        self.busy = False
        if not res.get("ok"):
            self.state = "failure"
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self._mark("bib_edit_preview", ok=False, code=res.get("code"))
            self._render()
            return
        payload = res.get("json") or {}
        self.payload = payload
        self.expect_sha = str(payload.get("db_sha256_before") or "")
        self.dirty_reason = ""
        self.state = "ready"
        self._mark("bib_edit_preview", ok=True, code=res.get("code"),
                   changes=len(diff_rows(payload)))
        self._render()

    def _open_confirm(self, e=None) -> None:
        if not self._can_confirm():
            return
        self.confirm_layer = True
        self._render()

    def _cancel_confirm(self, e=None) -> None:
        self.confirm_layer = False
        self._notify("已取消，未写库（草稿保留）")
        self._render()

    async def _write(self) -> None:
        if self.busy or not self._can_confirm():
            return
        self.busy = True
        self.state = "running"
        self.confirm_layer = False
        self._render()
        res = await self._call(self._build_call(dry_run=False,
                                                expect_sha=self.expect_sha))
        if not res.get("ok"):
            if self._sha_mismatch(res):
                self.state = "stale"
                self.dirty_reason = "库指纹已变化，预览不再可信"
            else:
                self.state = "failure"
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self.busy = False
            self._mark("bib_edit_write", ok=False, code=res.get("code"))
            self._render()
            return
        payload = res.get("json") or {}
        changed = len(diff_rows(payload)) or len(self._changed_fields())
        db = str(payload.get("db") or self._db())
        commit: dict = {}
        if db:
            try:
                commit = await asyncio.to_thread(
                    GIT.commit_db, db,
                    f"引用库：编辑 {self.loaded_key} · {changed} 个字段")
            except Exception as exc:  # noqa: BLE001
                commit = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.busy = False
        self.state = "success"
        self.result = payload
        self.commit = commit if isinstance(commit, dict) else {}
        self.original = dict(self.values)
        self._mark("bib_edit_write", ok=True, changed=changed,
                   commit=_sha8(str((self.commit or {}).get("sha") or "")))
        self._notify(f"已写入 {changed} 个字段（未推送）")
        self._render()

    def _sha_mismatch(self, res: dict) -> bool:
        text = f"{res.get('error') or ''} {res.get('stderr') or ''}"
        return res.get("code") == 2 or "sha256" in text

    def _revert_confirm(self, e=None) -> None:
        if not (self.commit or {}).get("ok"):
            self._notify("没有可回退的本次提交")
            return
        dialog = S.danger_confirm(
            self._flet(), self.t, title="回退这次编辑？",
            message=("恢复到本次写入前的版本，再生成一条回退提交；只动 bibliography.jsonl，不推送。"),
            impact="1 个文件 · 1 条回退提交",
            confirm_label="回退这次提交", on_confirm=lambda e: self._task(self._revert))
        if dialog is not None:
            self._render()

    async def _revert(self) -> None:
        db = self._db()
        try:
            result = await asyncio.to_thread(GIT.rollback, db) if db else {"ok": False}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict) and result.get("ok"):
            self.state = "ready"
            self.payload = None
            self.expect_sha = ""
            self.commit = None
            self._notify("已回退本次写入（生成了一条新提交，未推送）")
        else:
            error = (result or {}).get("error") if isinstance(result, dict) else ""
            self._notify(f"回退失败：{error or '未知错误'}")
        self._render()

    # ── 渲染 ─────────────────────────────────────────────────────────
    def _body(self) -> ft.Control:
        t = self.t
        width = int(getattr(self._flet(), "width", 1160) or 1160)
        narrow = width <= S.NARROW_W
        if narrow:
            split: ft.Control = ft.Column(spacing=t.sp("1"), controls=[
                ft.Container(height=t.px(340), content=self._fields_col()),
                ft.Container(content=self._diff_col()),
            ])
        else:
            split = ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START,
                           controls=[
                               ft.Container(width=t.px(504), content=self._fields_col()),
                               ft.Container(width=t.px(354), content=self._diff_col()),
                           ])
        self._bottom_holder = ft.Container(content=self._bottom())
        body = ft.Column(spacing=t.sp("1"), expand=True, controls=[
            self._key_row(), self._props_row(), self._hint_line(),
            ft.Container(content=split, expand=True),
            self._bottom_holder,
        ])
        return S.page_shell(
            t, title=TITLE,
            meta="按 key 载入一条记录 · 预览 → 写入",
            role_chip=S.role_chip(t, "write"),
            action=C.text_button("清空草稿", self._clear_draft, t),
            body=body, dock=self._dock())

    def _dock(self) -> ft.Control:
        t = self.t
        if not self.stderr and not self.error:
            return ft.Container(height=S.DOCK_H)
        return ft.Container(
            height=S.DOCK_H, bgcolor=t.palette.log_bg,
            padding=ft.Padding(left=t.px(12), top=t.px(6), right=t.px(12), bottom=t.px(6)),
            content=C.text((self.stderr or self.error).splitlines()[0][:400], t,
                           role="log", color=t.palette.log_text, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS))

    def _key_row(self) -> ft.Control:
        t = self.t
        chips: list[ft.Control] = []
        record = self.record or {}
        if record:
            chips.append(C.badge(str(record.get("entry_type") or self.entry_type
                                      or "未标类型"), t, "neutral"))
            if record.get("verified"):
                chips.append(C.badge("已验证", t, "success"))
            aliases = record.get("aliases")
            if isinstance(aliases, list) and aliases:
                chips.append(C.badge(f"别名 {len(aliases)}", t, "neutral"))
            updated = record.get("updated_at") or record.get("date") or ""
            if updated:
                chips.append(C.text(f"更新于 {updated}", t, "caption",
                                    t.palette.text_faint))
        field = C.floating_field(
            t, "条目 * key", value=self.key_input, hint="如 li_2021_zhuangzi",
            on_change=self._on_key_input, width=t.px(420),
            **{"on_submit": lambda e: self._load_click()})
        return ft.Container(height=t.px(48), content=ft.Row(
            spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                field,
                C.secondary_button("运行中" if self.state == "loading" else "载入",
                                   self._load_click, t, height=t.px(48),
                                   disabled=self.busy),
                ft.Container(expand=True),
                *chips,
            ]))

    def _props_row(self) -> ft.Control:
        t = self.t
        type_select = C.select(
            t, value=self.entry_type or "article",
            options=[(name, name) for name in ENTRY_TYPES],
            on_select=self._on_type, width=t.px(180))
        new_key = C.text_field(
            t, value=self.new_key, hint_text="留空 = 不改；旧 key 记别名",
            on_change=self._on_new_key, width=t.px(300))
        status = f"已载入 · 冲突 {len(self.conflicts)}" if self.loaded_key else "未载入"
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[
                          type_select, new_key,
                          ft.Container(expand=True),
                          C.text(status, t, "caption",
                                 t.palette.warn if self.conflicts else t.palette.text_faint),
                      ])

    def _hint_line(self) -> ft.Control:
        t = self.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text("整份替换；清空字段 = 写入时删除", t, "caption",
                   t.palette.text_faint, max_lines=1),
            ft.Container(expand=True),
            C.text(f"字段 {len(self.original)} 个", t, "caption", t.palette.text_faint),
        ])

    def _fields_col(self) -> ft.Control:
        t = self.t
        names = self._field_names()
        half = (len(names) + 1) // 2
        columns = [names[:half], names[half:]]
        cols = [ft.Column(spacing=0, expand=True,
                          controls=[self._field_cell(name) for name in names_in])
                for names_in in columns]
        footnote = ("还有字段，滚动查看" if len(names) > 12 else "")
        return ft.Column(spacing=t.sp("half"), controls=[
            C.section_label("字段", t, note=f"{len(names)} 个"),
            ft.Container(
                height=t.px(330), content=ft.Row(
                    spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.START,
                    controls=[ft.Column(spacing=0, scroll=S.scrollbar(),
                                        width=t.px(240), controls=cols[0].controls),
                              ft.Column(spacing=0, scroll=S.scrollbar(),
                                        width=t.px(240), controls=cols[1].controls)])),
            C.text(footnote or " ", t, "caption", t.palette.text_faint, max_lines=1),
        ])

    def _field_cell(self, name: str) -> ft.Control:
        t = self.t
        value = self.values.get(name, "")
        field = C.floating_field(
            t, f"{field_label(name)}", value=value,
            hint="空 = 删除该字段" if not value else None,
            on_change=lambda e, n=name: self._on_field(n, e), width=t.px(240))
        return ft.Container(
            height=t.px(66), padding=ft.Padding(left=0, top=0, right=0, bottom=t.px(2)),
            content=ft.Column(spacing=0, controls=[
                field,
                C.text(name, t, "caption", t.palette.text_faint, max_lines=1),
            ]))

    def _diff_col(self) -> ft.Control:
        self._diff_holder = ft.Container(content=self._diff_col_body())
        return self._diff_holder

    def _diff_col_body(self) -> ft.Control:
        t = self.t
        p = t.palette
        rows = diff_rows(self.payload)
        changed = len(self._changed_fields())
        total = len(self._field_names())
        payload = self.payload or {}
        missing = payload.get("missing_after")
        missing_n = len(missing) if isinstance(missing, list) else 0
        band = S.summary_band(t, [
            {"label": "变更", "value": str(changed), "hint": "个字段", "tone": "accent"},
            {"label": "未变更", "value": str(max(0, total - changed)), "hint": "个字段",
             "tone": "neutral"},
            {"label": "写后必填", "value": f"{max(0, total - missing_n)}/{total}",
             "hint": "字段", "tone": "success" if not missing_n else "warn"},
        ])
        controls: list[ft.Control] = [
            C.section_label("差异", t, note="预览"),
            band,
        ]
        if not self.payload:
            controls.append(C.text("先预览，这里列出差异。", t, "caption",
                                   p.text_faint, max_lines=2))
        else:
            body = []
            for row in rows:
                body.append(S.table_row(
                    t, [row["field"], row["old"] or "—", row["new"] or "—"],
                    weights=[1.0, 1.4, 1.6], mono=(0,)))
            if not body:
                body.append(S.empty_row(t, "没有字段变化"))
            controls.append(ft.Container(
                height=t.px(210), content=ft.Column(
                    spacing=0, scroll=S.scrollbar(),
                    controls=[S.table_header_row(t, ["字段", "原值", "新值"],
                                                 weights=[1.0, 1.4, 1.6], mono=(0,))]
                    + body)))
            unchanged = max(0, total - len(rows))
            controls.append(ft.Container(height=t.px(30), alignment=ft.Alignment.CENTER_LEFT,
                                         content=C.text(
                                             f"未变化 {unchanged} 个字段已折叠", t,
                                             "caption", p.text_faint)))
        if self.state == "failure":
            controls.append(S.state_banner(t, "failure", message=self.error or "预览失败",
                                           stderr=self.stderr, page=self._flet()))
        elif self.state == "stale":
            controls.append(S.stale_notice(
                t, title="库已变化，本次未写库",
                detail=self.dirty_reason or "库已被其他操作修改",
                on_repreview=lambda e: self._task(self._preview)))
        elif self.confirm_layer:
            controls.append(self._confirm_layer())
        controls.append(ft.Container(height=t.px(28), alignment=ft.Alignment.CENTER_LEFT,
                                     content=C.text(
                                         "1 个文件 · 1 条提交",
                                         t, "caption", p.text_faint)))
        return ft.Column(spacing=t.sp("half"), controls=controls)

    def _confirm_layer(self) -> ft.Control:
        t = self.t
        p = t.palette
        changed = len(self._changed_fields())
        return ft.Container(
            bgcolor=p.accent_soft, border=C.box(t, p.accent),
            border_radius=t.radius("sm"),
            padding=ft.Padding(left=t.px(10), top=t.px(8), right=t.px(10), bottom=t.px(8)),
            content=ft.Column(spacing=t.sp("half"), controls=[
                C.text("确认写入这条记录？", t, role="body-strong"),
                C.text(f"{changed} 个字段 · 库指纹 {_sha8(self.expect_sha)} · "
                       f"1 条本地提交 · 不推送", t, "caption", p.on_accent_soft),
                C.text("页内不可撤销，只能回退本次提交。", t, "caption",
                       p.on_accent_soft),
                ft.Row(spacing=t.sp("1"), controls=[
                    C.text_button("取消", self._cancel_confirm, t),
                    C.primary_button("确认写入", lambda e: self._task(self._write), t),
                ]),
            ]))

    def _bottom(self) -> ft.Control:
        t = self.t
        if self.state == "success":
            commit = self.commit or {}
            sha = _sha8(str(commit.get("sha") or ""))
            changed = len(diff_rows(self.payload)) or len(self._changed_fields())
            return S.result_banner(
                t, title=f"已写入 {changed} 个字段",
                detail=f"已提交 {sha} · 未推送" if sha else "已写入；未生成提交",
                on_review=lambda e: self._goto("review"),
                on_revert=self._revert_confirm if commit.get("ok") else None)
        stale = self._is_stale() or self.state == "stale"
        changed = len(self._changed_fields())
        consequence = (f"将写入 {changed} 个字段（整份替换）" if self.loaded_key
                       else "先载入一条记录")
        detail = "只写 bibliography.jsonl · 不推送"
        if self.conflicts:
            consequence = f"还有 {len(self.conflicts)} 条待裁决冲突：先去待处理裁决"
        if stale:
            detail = f"预览已过期：{self.dirty_reason}，先重新预览"
        return S.confirm_bar(
            t, consequence=consequence, detail=detail, sha=_sha8(self.expect_sha),
            on_confirm=self._open_confirm, confirm_disabled=not self._can_confirm(),
            confirm_label="运行中" if self.state == "running" else "确认写入",
            on_repreview=lambda e: self._task(self._preview),
            stale=stale and self.payload is not None)

    def _goto(self, view: str) -> None:
        nav = getattr(self.page, "goto", None)
        if callable(nav):
            nav(view)
            return
        self._store_set("bib_view", view)
        self._notify("暂不可跳转")
