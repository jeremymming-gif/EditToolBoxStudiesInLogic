"""引用库 · 入藏（写库视图；批 115 夜间重设计，BPAGES-B）。

契约：`evidence/b115-bib-redesign/pages/import/spec.md`。四步固定：
预览（dry-run，不写盘）→ 页内二次确认（影响 + 库 sha）→ 原子写
（`--expect-db-sha256` + `--backup`）→ 本地提交（不 push）→ 可回退本次提交。

纪律：只走 `EB.*` 拼命令、`EB.read_json_command` 执行；本模块不碰 subprocess，
不写库（写库是引擎的事），不 reset / 不 force push。
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C

VIEW_KEY = "import"
TITLE = "入藏"
ROLE = "write"

#: 三种来源（段控 28 高）
MODES = (("bib", "单个 .bib"), ("dir", "整期目录"), ("paste", "粘贴文本"))
#: 预览结果筛选（chip 28）
FILTERS = (("all", "全部"), ("new", "新增"), ("updated", "更新"),
           ("conflict", "冲突"), ("skipped", "跳过"))
_STATE_KIND = {"ready": None, "empty": "empty", "loading": "loading",
               "running": "running", "failure": "failure", "stale": "stale",
               "cancelled": "cancelled", "success": "success"}
_STATUS_LABEL = {"new": ("新增", "success"), "add": ("新增", "success"),
                 "added": ("新增", "success"), "updated": ("更新", "accent"),
                 "update": ("更新", "accent"), "merged": ("合并", "accent"),
                 "conflict": ("冲突", "warn"), "conflicts": ("冲突", "warn"),
                 "skipped": ("跳过", "muted"), "skip": ("跳过", "muted")}


def _sha8(sha: str) -> str:
    return (sha or "")[:8]


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class BibImportView:
    """入藏页：左参数 356 / 右预览 520（1000 宽收单列）。"""

    def __init__(self, page) -> None:
        self.page = page
        self.app = getattr(page, "app", None)
        self.t = getattr(page, "t", None)
        self.mode = "bib"
        self.path = self._store_get("recent.bib_import", "") or ""
        self.issue = ""
        self.slug = ""
        self.verified = False
        self.through_year = ""
        self.finished_only = False
        self.force = False
        self.more_open = False
        self.paste_text = ""
        self.params_open = False
        self.payload: dict | None = None
        self.expect_sha = ""
        self.state = "empty"
        self.filter = "all"
        self.error = ""
        self.stderr = ""
        self.elapsed = 0.0
        self.result: dict | None = None
        self.commit: dict | None = None
        self.confirm_layer = False
        self.busy = False
        self.dirty_reason = ""
        self.shown = 100
        self._root: ft.Container | None = None

    # ── 宿主 / 状态小工具 ──────────────────────────────────────────────
    def _flet(self):
        return getattr(self.page, "page", None)

    def _store(self):
        store = getattr(self.page, "store", None)
        if store is None and self.app is not None:
            store = getattr(self.app, "store", None)
        return store

    def _store_get(self, key: str, default=None):
        store = None
        try:
            store = self._store()
        except Exception:  # noqa: BLE001 - 宿主未接线时保持可用
            store = None
        if store is None or not hasattr(store, "get"):
            return default
        try:
            return store.get(key, default)
        except Exception:  # noqa: BLE001
            return default

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
            except Exception:  # noqa: BLE001 - 遥测失败不影响写库
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
            except Exception:  # noqa: BLE001 - 控件未挂载时忽略
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
        """宿主切到本页时调用；入藏不需要预取，只在首次把路径回填进表单。"""
        if self.path:
            self._store_set("recent.bib_import", self.path)

    def refresh_running(self) -> None:
        """宿主重绘时调用：不主动重启任何引擎任务。"""

    def header_actions(self) -> list[ft.Control]:
        return []

    def primary_action(self) -> ft.Control | None:
        return None

    def _store_set(self, key: str, value) -> None:
        store = self._store()
        if store is not None and hasattr(store, "set"):
            try:
                store.set(key, value)
            except Exception:  # noqa: BLE001
                pass

    # ── 表单状态 ─────────────────────────────────────────────────────
    def _source(self) -> str:
        if self.mode == "paste":
            return self.paste_text if self.paste_text.strip() else ""
        return self.path if self.path.strip() else ""

    def _can_preview(self) -> bool:
        return bool(self._source()) and not self.busy

    def _is_stale(self) -> bool:
        return bool(self.dirty_reason) and self.payload is not None

    def _can_confirm(self) -> bool:
        return bool(self.payload) and bool(self.expect_sha) and not self._is_stale() \
            and not self.busy and self.state not in ("loading", "running", "failure",
                                                     "stale", "success")

    def _invalidate(self, reason: str) -> None:
        if self.payload is not None and not self.dirty_reason:
            self.dirty_reason = reason
        self.confirm_layer = False

    def _set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self._invalidate("来源已切换，预览已失效")
        self.payload = None if mode != self.mode else self.payload
        self.state = "empty" if self.payload is None else self.state
        self._render()

    def _on_path(self, e) -> None:
        self.path = e.control.value or ""
        self._store_set("recent.bib_import", self.path)
        self._invalidate("来源已修改，请重新预览")

    def _on_issue(self, e) -> None:
        self.issue = e.control.value or ""
        self._invalidate("期号已修改，请重新预览")

    def _on_slug(self, e) -> None:
        self.slug = e.control.value or ""
        self._invalidate("slug 已修改，请重新预览")

    def _on_year(self, e) -> None:
        self.through_year = e.control.value or ""
        self._invalidate("截止年份已修改，请重新预览")

    def _on_paste(self, e) -> None:
        self.paste_text = e.control.value or ""
        self._invalidate("粘贴内容已修改，请重新预览")

    def _toggle(self, name: str, e=None) -> None:
        value = bool(getattr(e.control, "value", False))
        setattr(self, name, value)
        self._invalidate("选项已修改，请重新预览")

    def _toggle_more(self, e=None) -> None:
        self.more_open = not self.more_open
        self._render()

    def _clear(self, e=None) -> None:
        self.path = ""
        self.paste_text = ""
        self.issue = ""
        self.slug = ""
        self.through_year = ""
        self.verified = False
        self.finished_only = False
        self.force = False
        self.payload = None
        self.expect_sha = ""
        self.state = "empty"
        self.dirty_reason = ""
        self.confirm_layer = False
        self.filter = "all"
        self.result = None
        self.commit = None
        self._notify("表单已清空")

    # ── 引擎命令 ─────────────────────────────────────────────────────
    def _build_call(self, *, dry_run: bool, expect_sha: str = "") -> EB.EngineCall:
        if self.mode == "dir":
            return EB.bibdb_import_dir_call(
                root=self.path, through_year=self.through_year,
                finished_only=self.finished_only, dry_run=dry_run,
                force=self.force, expect_db_sha256=expect_sha)
        bib = self._paste_file() if self.mode == "paste" else self.path
        return EB.bibdb_import_call(
            bib=bib, issue=self.issue, slug=self.slug, dry_run=dry_run,
            force=self.force, verified=self.verified, expect_db_sha256=expect_sha)

    def _paste_file(self) -> str:
        """粘贴文本落一个临时 .bib（temp dir，不碰笔记仓）。"""
        folder = Path(tempfile.mkdtemp(prefix="bib-import-"))
        target = folder / "pasted.bib"
        target.write_text(self.paste_text, encoding="utf-8")
        return str(target)

    async def _preview(self) -> None:
        if self.busy:
            return
        if not self._source():
            self._notify("先选择要导入的来源")
            return
        self.busy = True
        self.state = "loading"
        self.error = ""
        self.stderr = ""
        self.confirm_layer = False
        self._render()
        started = time.time()
        res = await self._call(self._build_call(dry_run=True))
        self.elapsed = max(0.0, time.time() - started)
        self.busy = False
        if not res.get("ok"):
            self.state = "failure"
            self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self._mark("bib_import_preview", ok=False, code=res.get("code"),
                       error=self.error[:200])
            self._render()
            return
        payload = res.get("json") or {}
        self.payload = payload
        self.expect_sha = str(payload.get("db_sha256_before") or "")
        self.dirty_reason = ""
        self.state = "ready"
        self.shown = 100
        self.filter = "all"
        self._mark("bib_import_preview", ok=True, code=res.get("code"),
                   entries=_int(payload.get("entries")),
                   sha=_sha8(self.expect_sha))
        self._render()

    def _open_confirm(self, e=None) -> None:
        if not self._can_confirm():
            return
        self.confirm_layer = True
        self._render()

    def _cancel_confirm(self, e=None) -> None:
        self.confirm_layer = False
        self.state = "cancelled" if False else self.state
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
        code = res.get("code")
        if not res.get("ok"):
            if self._sha_mismatch(res):
                self.state = "stale"
                self.dirty_reason = "库 sha256 已变化，预览不再可信"
                self.error = self._error_text(res)
            else:
                self.state = "failure"
                self.error = self._error_text(res)
            self.stderr = str(res.get("stderr") or "")
            self.busy = False
            self._mark("bib_import_write", ok=False, code=code,
                       error=self.error[:200])
            self._render()
            return
        payload = res.get("json") or {}
        self.result = payload
        added = _int(payload.get("new") or payload.get("added") or payload.get("imported"))
        updated = _int(payload.get("updated") or payload.get("merged"))
        db = str(payload.get("db") or self._db())
        message = f"引用库：入藏 {added} 条新增 · {updated} 条更新"
        commit: dict = {}
        if db:
            try:
                commit = await asyncio.to_thread(GIT.commit_db, db, message)
            except Exception as exc:  # noqa: BLE001 - 提交失败要让人看见
                commit = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.commit = commit if isinstance(commit, dict) else {}
        self.state = "success"
        self.busy = False
        self.expect_sha = str(payload.get("db_sha256_after") or "")
        self._mark("bib_import_write", ok=True, code=code, added=added,
                   updated=updated, commit=_sha8(str((self.commit or {}).get("sha") or "")))
        self._notify(f"已写入 {added} 条新增 · {updated} 条更新（未推送）")
        self._render()

    def _sha_mismatch(self, res: dict) -> bool:
        text = f"{res.get('error') or ''} {res.get('stderr') or ''}"
        return res.get("code") == 2 or "sha256" in text or "sha 不符" in text

    def _revert_confirm(self, e=None) -> None:
        commit = self.commit or {}
        if not commit.get("ok"):
            self._notify("没有可回退的本次提交")
            return
        impact = "影响 1 个文件（bib_db/bibliography.jsonl）· 1 条回退提交"
        dialog = S.danger_confirm(
            self._flet(), self.t,
            title="回退本次入藏提交？",
            message=("把 bibliography.jsonl 恢复到本次写入前的版本，再生成一条新提交；"
                     "其它文件不动。"),
            impact=impact, confirm_label="回退这次提交",
            on_confirm=lambda e: self._task(self._revert))
        if dialog is not None:
            self._render()

    async def _revert(self) -> None:
        db = self._db()
        if not db:
            self._notify("找不到库路径")
            return
        try:
            result = await asyncio.to_thread(GIT.rollback, db)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict) and result.get("ok"):
            self.state = "empty"
            self.payload = None
            self.expect_sha = ""
            self.commit = None
            self._notify("已回退本次写入（生成了一条新提交，未推送）")
        else:
            error = (result or {}).get("error") if isinstance(result, dict) else ""
            self._notify(f"回退失败：{error or '未知错误'}")
        self._render()

    def _error_text(self, res: dict) -> str:
        payload = res.get("json") or {}
        for value in (payload.get("error"), res.get("error"), res.get("stderr")):
            if value:
                return str(value).strip().splitlines()[0][:300]
        return "引擎没有返回原因。"

    # ── 计数 / 行 ───────────────────────────────────────────────────
    def _changes(self) -> list[dict]:
        payload = self.payload or {}
        raw = payload.get("changes")
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            record = item.get("record") if isinstance(item.get("record"), dict) else {}
            fields = record.get("fields") or {}
            key = str(item.get("key") or record.get("key") or "")
            status = str(item.get("status") or item.get("action") or item.get("kind") or "")
            title = str(item.get("title") or fields.get("title") or item.get("message") or "")
            out.append({"key": key, "status": status, "title": title})
        return out

    def _counts(self) -> dict:
        payload = self.payload or {}
        changes = self._changes()
        new = _int(payload.get("new") or payload.get("added"))
        updated = _int(payload.get("updated") or payload.get("merged"))
        conflicts = _int(payload.get("conflicts"))
        skipped = _int(payload.get("skipped"))
        if not (new or updated or conflicts or skipped) and changes:
            for row in changes:
                label, _tone = _STATUS_LABEL.get(row["status"], ("", "muted"))
                if label == "新增":
                    new += 1
                elif label in ("更新", "合并"):
                    updated += 1
                elif label == "冲突":
                    conflicts += 1
                elif label == "跳过":
                    skipped += 1
        return {"entries": _int(payload.get("entries")) or len(changes),
                "new": new, "updated": updated, "conflicts": conflicts,
                "skipped": skipped}

    def _rows(self) -> list[dict]:
        rows = self._changes()
        if self.filter != "all":
            want = _STATUS_LABEL.get(self.filter, ("", ""))[0]
            rows = [row for row in rows if _STATUS_LABEL.get(row["status"], ("", ""))[0] == want]
        return rows

    def _consequence(self) -> str:
        counts = self._counts()
        parts = []
        if counts["new"]:
            parts.append(f"将写入 {counts['new']} 条新增")
        if counts["updated"]:
            parts.append(f"{counts['updated']} 条更新")
        if counts["conflicts"]:
            parts.append(f"{counts['conflicts']} 条冲突（进待处理，不覆盖）")
        if not parts:
            parts.append("预览没有可写入的变化")
        return " · ".join(parts)

    # ── 渲染：左参数 ─────────────────────────────────────────────────
    def _body(self) -> ft.Control:
        t = self.t
        width = int(getattr(self._flet(), "width", 1160) or 1160)
        narrow = width <= S.NARROW_W
        params = self._params(narrow=narrow)
        layout = S.write_layout(t, params=params, preview=self._preview_col(),
                                confirm=None, narrow=narrow)
        # P1-7：确认条满宽常驻在页面底部，不再缩在右预览栏；窄屏同样通栏。
        self._confirm_holder = ft.Container(content=self._bottom())
        body = ft.Column(spacing=0, expand=True,
                         horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                         controls=[
                             ft.Container(content=layout, expand=True),
                             self._confirm_holder,
                         ])
        return S.page_shell(
            t, title=TITLE,
            meta="预览 → 确认 → 写入；只提交本地",
            role_chip=S.role_chip(t, "write"), body=body,
            notice=None, dock=self._dock())

    def _dock(self) -> ft.Control:
        t = self.t
        if not self.stderr and not self.error:
            return ft.Container(height=S.DOCK_H)
        text = (self.stderr or self.error).strip()
        return ft.Container(
            height=S.DOCK_H, bgcolor=t.palette.log_bg,
            padding=ft.Padding(left=t.px(12), top=t.px(6), right=t.px(12), bottom=t.px(6)),
            content=C.text(text.splitlines()[0][:400], t, role="log",
                           color=t.palette.log_text, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS))

    def _params(self, *, narrow: bool) -> ft.Control:
        t = self.t
        if narrow and not self.params_open:
            return ft.Container(
                padding=ft.Padding(left=0, top=t.px(4), right=0, bottom=t.px(4)),
                content=ft.Column(spacing=t.sp("half"), controls=[
                    C.text(f"来源：{dict(MODES)[self.mode]} · "
                           f"{self.path or ('粘贴文本 %d 行' % len(self.paste_text.splitlines())) or '未选择'}",
                           t, "small", t.palette.text_muted, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS),
                    C.text(f"期号 {self.issue or '—'} · slug {self.slug or '—'}",
                           t, "caption", t.palette.text_faint, max_lines=1),
                    C.text_button("编辑参数…", lambda e: self._open_params(), t),
                ]))
        controls: list[ft.Control] = [self._segment()]
        if self.mode == "paste":
            controls.append(C.floating_field(
                t, "粘贴 .bib 文本 *", value=self.paste_text, hint="整段 BibTeX 条目",
                multiline=True, min_lines=6, max_lines=10, on_change=self._on_paste,
                expand=True))
        else:
            field = C.floating_field(
                t, "来源 *" if self.mode == "bib" else "整期目录 *",
                value=self.path,
                hint=".bib 文件路径" if self.mode == "bib" else "期目录路径",
                on_change=self._on_path, expand=True)
            controls.append(ft.Container(
                height=t.px(48),
                content=ft.Row(spacing=t.sp("1"), controls=[
                    field,
                    C.secondary_button("浏览…", lambda e: self._browse(), t,
                                       icon="FOLDER_OPEN", height=t.px(48)),
                ])))
        if self.mode == "bib":
            controls.append(ft.Row(spacing=t.sp("1"), controls=[
                C.floating_field(t, "期号", value=self.issue, hint="可空，如 2026-3",
                                 on_change=self._on_issue, width=t.px(174)),
                C.floating_field(t, "slug", value=self.slug, hint="可空",
                                 on_change=self._on_slug, width=t.px(174)),
            ]))
            controls.append(self._check("verified", "标为已核实",
                                        self.verified))
        else:
            controls.append(ft.Row(spacing=t.sp("1"), controls=[
                C.floating_field(t, "截止年份", value=self.through_year,
                                 hint="可空，如 2026", on_change=self._on_year,
                                 width=t.px(174)),
                ft.Container(width=t.px(174), height=t.px(48),
                             alignment=ft.Alignment.CENTER_LEFT,
                             content=self._check("finished_only", "只要已完期",
                                                 self.finished_only)),
            ]))
        more_label = "更多选项 ▾" if self.more_open else "更多选项 ▸"
        controls.append(C.text_button(more_label, self._toggle_more, t))
        if self.more_open:
            controls.append(self._check("force", "覆盖待裁决冲突",
                                        self.force))
        controls.append(ft.Container(height=t.px(32), content=ft.Row(
            spacing=t.sp("half"), controls=[
                C.text("写入范围", t, "caption", t.palette.text_faint),
                C.text("bib_db/bibliography.jsonl", t, role="mono",
                       color=t.palette.text_muted),
                C.text("· 1 个文件", t, "caption", t.palette.text_faint),
            ])))
        busy = self.state == "loading"
        preview_label = "运行中" if busy else "预览（不写库）"
        preview_btn = (C.primary_button if self.payload is None
                       else C.secondary_button)(
            preview_label, lambda e: self._task(self._preview), t,
            disabled=not self._can_preview())
        # P1-7：预览前页内只此一个 accent 主操作；预览成功后让位给确认条。
        preview_btn._bib_primary = self.payload is None
        controls.append(ft.Row(spacing=t.sp("1"), controls=[
            preview_btn,
            C.text("⌘↵", t, "caption", t.palette.text_faint),
        ]))
        controls.append(C.text_button("清空表单", self._clear, t))
        controls.append(C.text(
            "预览不写库；写入前校验库 sha。",
            t, "caption", t.palette.text_faint, max_lines=3))
        if not narrow:
            controls.append(C.text("", t, "caption", t.palette.text_faint))
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _open_params(self) -> None:
        self.params_open = True
        self._render()

    def _browse(self) -> None:
        """用 Flet 文件选择器挑一个来源；宿主没挂 picker 时只提示。"""
        page = self._flet()
        picker = getattr(page, "file_picker", None) if page is not None else None
        if picker is None:
            self._notify("可直接粘贴路径，或点「浏览…」")
            return
        try:
            picker.pick_files(allow_multiple=False)
        except Exception:  # noqa: BLE001 - 选择器实现因平台而异
            self._notify("文件选择器不可用；请直接粘贴路径")

    def _segment(self) -> ft.Control:
        t = self.t
        p = t.palette
        cells = []
        for key, label in MODES:
            active = key == self.mode
            cells.append(ft.Container(
                height=t.px(28), border_radius=t.radius("control"),
                padding=ft.Padding(left=t.px(10), top=0, right=t.px(10), bottom=0),
                alignment=ft.Alignment.CENTER,
                bgcolor=p.accent_soft if active else None,
                content=C.text(label, t, "small",
                               p.on_accent_soft if active else p.text_muted,
                               weight="medium" if active else "regular"),
                on_click=lambda e, k=key: self._set_mode(k)))
        return ft.Container(
            height=t.px(28), border=C.box(t), border_radius=t.radius("control"),
            content=ft.Row(spacing=0, controls=cells))

    def _check(self, attr: str, label: str, value: bool) -> ft.Control:
        t = self.t
        return ft.Container(
            height=t.px(28), alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Row(spacing=t.sp("1"), controls=[
                ft.Checkbox(value=value, splash_radius=0,
                            visual_density=ft.VisualDensity.COMPACT,
                            on_change=lambda e, name=attr: self._toggle(name, e)),
                C.text(label, t, "small", t.palette.text_muted),
            ]))

    # ── 渲染：右预览 ─────────────────────────────────────────────────
    def _preview_col(self) -> ft.Control:
        t = self.t
        controls: list[ft.Control] = [
            C.section_label("预览结果", t,
                            note="预览" if self.payload is None else "预览 · 未写库"),
            ft.Container(height=t.px(6)),
        ]
        if self.state == "success":
            controls.append(self._success_banner())
            controls.append(ft.Container(height=t.px(8)))
            controls.append(self._payload_block(dim=True))
        elif self.state in ("loading",):
            controls.append(S.state_banner(t, "loading", message="运行中"))
        elif self.state == "failure":
            controls.append(S.state_banner(
                t, "failure", message=self.error or "预览失败",
                stderr=self.stderr, page=self._flet()))
        elif self.state == "stale":
            controls.append(S.stale_notice(
                t, title="预览已过期，本次未写库",
                detail=self.dirty_reason or "库已被其他操作修改",
                on_repreview=lambda e: self._task(self._preview)))
        elif self.payload is None:
            controls.append(S.state_banner(
                t, "empty", message="尚未预览：选好来源后点「预览（不写库）」"))
        if self.payload is not None and self.state in ("ready", "stale", "running",
                                                       "cancelled"):
            controls.append(self._payload_block())
        if self.confirm_layer:
            controls.append(ft.Container(height=t.px(8)))
            controls.append(self._confirm_layer())
        controls.append(ft.Container(expand=True))
        # 确认条由外层 _body 通栏挂在页面底部，右栏不再重复。
        return ft.Column(spacing=0, expand=True, controls=controls)

    def _payload_block(self, *, dim: bool = False) -> ft.Control:
        t = self.t
        counts = self._counts()
        payload = self.payload or {}
        sha = _sha8(str(payload.get("db_sha256_before") or ""))
        band = S.summary_band(t, [
            {"label": "新增", "value": str(counts["new"]), "hint": "条 · 写进库", "tone": "success"},
            {"label": "更新", "value": str(counts["updated"]), "hint": "条 · 覆盖旧值", "tone": "accent"},
            {"label": "冲突", "value": str(counts["conflicts"]), "hint": "条 · 进待处理", "tone": "warn"},
            {"label": "跳过", "value": str(counts["skipped"]), "hint": "条 · 未改动", "tone": "neutral"},
        ])
        meta = (f"库 sha {sha or '—'} · 解析 {counts['entries']} 条 · "
                f"耗时 {self.elapsed:.1f}s")
        chips = ft.Row(spacing=t.sp("half"), controls=[
            C.chip(f"{label} {self._chip_count(key)}", t, active=self.filter == key,
                   on_click=lambda e, k=key: self._set_filter(k))
            for key, label in FILTERS])
        rows = self._rows()
        body_rows = []
        for row in rows[: self.shown]:
            label, tone = _STATUS_LABEL.get(row["status"], (row["status"] or "—", "muted"))
            color = {"success": t.palette.success, "accent": t.palette.accent,
                     "warn": t.palette.warn, "muted": t.palette.text_faint}.get(
                         tone, t.palette.text_muted)
            body_rows.append(S.table_row(
                t, [label, row["key"], "写入", row["title"]],
                weights=[0.7, 1.5, 0.8, 3.0], mono=(1,),
                align=["left", "left", "left", "left"]))
        if not rows:
            body_rows.append(S.empty_row(t, "没有变化记录"))
        # ≤8 行走 shrink-wrap（滚轮归页体）；只有长变化列表才保留独立滚动，
        # 此时常显条让“这里还能滚”可见。
        scrolling = len(rows) > 8
        table = ft.Column(spacing=0, expand=scrolling,
                          scroll=S.scrollbar() if scrolling else None,
                          controls=[
                              S.table_header_row(t, ["状态", "key", "动作", "题名 / 变化"],
                                                 weights=[0.7, 1.5, 0.8, 3.0],
                                                 mono=(1,)),
                              ft.Column(spacing=0, controls=body_rows),
                          ])
        table._bib_shrink = not scrolling
        if len(rows) > self.shown:
            table.controls.append(ft.Container(
                height=t.px(36), alignment=ft.Alignment.CENTER_LEFT,
                content=C.text_button(f"载入更多（还有 {len(rows) - self.shown} 条）",
                                      lambda e: self._more(), t)))
        warnings = payload.get("warnings")
        warn_lines = [str(w) for w in warnings if str(w).strip()][:2] \
            if isinstance(warnings, list) else []
        controls: list[ft.Control] = [
            ft.Container(opacity=0.55 if dim else 1.0, content=band),
            C.text(meta, t, "caption", t.palette.text_faint),
            ft.Container(height=t.px(4)),
            chips if not dim else ft.Container(),
            ft.Container(height=t.px(4)),
            ft.Container(expand=True,
                         content=table if not dim else ft.Container()),
        ]
        for line in warn_lines:
            controls.append(C.text(f"注意 · {line}", t, "caption", t.palette.warn,
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
        return ft.Column(spacing=t.sp("half"), controls=controls)

    def _chip_count(self, key: str) -> str:
        counts = self._counts()
        return str(counts.get(key, 0))

    def _set_filter(self, key: str) -> None:
        self.filter = key
        self._render()

    def _more(self) -> None:
        self.shown += 100
        self._render()

    def _confirm_layer(self) -> ft.Control:
        t = self.t
        p = t.palette
        counts = self._counts()
        sha = _sha8(self.expect_sha)
        lines = [
            f"新增 {counts['new']} 条 · 更新 {counts['updated']} 条 · "
            f"冲突 {counts['conflicts']} 条（进待处理）",
            f"库 sha {sha} · 1 条本地提交 · 不推送",
            "页内不可撤销，只能回退本次提交。",
        ]
        return ft.Container(
            bgcolor=p.accent_soft, border=C.box(t, p.accent),
            border_radius=t.radius("sm"),
            padding=ft.Padding(left=t.px(12), top=t.px(8), right=t.px(12), bottom=t.px(8)),
            content=ft.Column(spacing=t.sp("half"), controls=[
                C.text("确认写入引用库？", t, role="body-strong"),
                *[C.text(line, t, "caption", p.on_accent_soft) for line in lines],
                ft.Row(spacing=t.sp("1"), controls=[
                    C.text_button("取消", self._cancel_confirm, t),
                    C.primary_button("确认写入", lambda e: self._task(self._write), t),
                ]),
            ]))

    def _success_banner(self) -> ft.Control:
        t = self.t
        payload = self.result or {}
        added = _int(payload.get("new") or payload.get("added") or payload.get("imported"))
        updated = _int(payload.get("updated") or payload.get("merged"))
        sha = _sha8(str((self.commit or {}).get("sha") or ""))
        detail = f"已提交 {sha} · 未推送" if sha else "已写入；未生成提交"
        return S.result_banner(
            t, title=f"已写入 {added} 新增 · {updated} 更新", detail=detail,
            on_review=lambda e: self._goto_pending(),
            on_revert=self._revert_confirm if (self.commit or {}).get("ok") else None)

    def _goto_pending(self) -> None:
        nav = getattr(self.page, "goto", None)
        if callable(nav):
            nav("review")
            return
        store = self._store()
        if store is not None and hasattr(store, "set"):
            try:
                store.set("bib_view", "review")
            except Exception:  # noqa: BLE001
                pass
        self._notify("去「待处理」裁决冲突")

    def _bottom(self) -> ft.Control:
        t = self.t
        if self.state == "success":
            return ft.Container(height=S.BANNER_H)
        has_preview = self.payload is not None
        stale = has_preview and (self._is_stale() or self.state == "stale")
        if not has_preview:
            consequence = "尚未预览"
            detail = "先预览（不写库），才能确认写入"
        elif stale:
            consequence = self._consequence()
            detail = f"预览已过期：{self.dirty_reason or '库 sha 已变化'}，先重新预览"
        else:
            consequence = self._consequence()
            detail = "只写 bibliography.jsonl · 不推送"
        bar = S.confirm_bar(
            t, consequence=consequence,
            detail=detail, sha=_sha8(self.expect_sha) if has_preview else None,
            on_confirm=self._open_confirm,
            confirm_disabled=not self._can_confirm(),
            confirm_label="运行中" if self.state == "running" else "确认写入",
            # P1-7：重新预览只在已有陈旧预览时出现；首次预览/已就绪都不占位。
            on_repreview=(lambda e: self._task(self._preview)) if stale else None,
            stale=stale)
        if not stale:
            bar._bib_repreview_btn.visible = False
        if not has_preview:
            bar._bib_confirm_btn.tooltip = "尚未预览：先点「预览（不写库）」"
        return bar
