"""引用库 · 字段归位与库健康 / 批量视图（批 109 W1b R3–R5；从 bib_ops 拆出的 submodule）。

* 字段归位（R3）：`suggest-field-fixes --json` 只读候选 → 勾选 + 选值 → 逐条
  `edit --dry-run` 预览 → 确认后逐条 `edit` 真写（sha 逐条链式更新）→ 全部写完
  **合 1 条**版本提交。绝不静默批量写。
* 库健康（R4）：`lint --json` 计数 + `conflicts --json --pending-only` + 版本层
  最近库提交（已处理留痕）。`ack-duplicates` 只写 sidecar，不改库、不自动提交。
* 审阅批量（R5）：先 `resolve --adopt-all-pending --dry-run`（或所选逐条 dry-run）
  出账 → 二次确认 → 真写 → **合 1 条**提交；没有待裁决项时按钮禁用并提示。
"""

from __future__ import annotations

import asyncio

import flet as ft

from services import bib_git as GIT
from services import engine_bridge as EB
from ui import components as C
from ui.bib_ops import (FIX_LIMIT, _OpsView, _int, _text, batch_preview_lines,
                        field_fix_candidates, field_fix_fields, field_fix_line,
                        field_fix_missing, field_fix_rows, field_label,
                        git_recent_lines, health_counts, health_lines, ops_notice)
from ui.bib_write import explain_result


# ── R3 字段归位 ─────────────────────────────────────────────────────────
class BibFieldFixView(_OpsView):
    """字段归位：候选列表 → 勾选 + 选候选值 → 逐条 dry-run 预览 → 批量写库 + 合 1 条提交。"""

    def __init__(self, page) -> None:
        super().__init__(page)
        self.rows: list[dict] = []
        self.selected: set[int] = set()
        self.choice: dict[int, dict[str, str]] = {}
        self.plan: list[dict] = []
        self.preview: dict | None = None

    async def ensure_loaded(self, force: bool = False) -> None:
        if self.loading or (self.loaded and not force):
            return
        self.loading = True
        try:
            await self._load()
        finally:
            self.loading = False
            self.loaded = True
        self._refresh()

    async def _load(self) -> None:
        self.busy, self.error = True, ""
        self._refresh()
        result = await self._read(EB.bibdb_suggest_fixes_call(
            registry="missing_field", limit=FIX_LIMIT))
        self.busy = False
        payload = result.get("json") or {}
        self.rows = field_fix_rows(payload) if result.get("ok") else []
        self.selected = {index for index in self.selected if index < len(self.rows)}
        if not result.get("ok"):
            self.error = explain_result(result)
        self.page.mark("bib_fieldfix_load", count=len(self.rows))

    def _ask_preview(self) -> None:
        self._run(self._preview())

    async def _preview(self) -> None:
        picked = sorted(self.selected)
        if not picked:
            self.error = "先勾选要归位的条目（可多选）。"
            self._refresh()
            return
        self.busy, self.error = True, ""
        self.plan = []
        self._refresh()
        plan: list[dict] = []
        for index in picked:
            row = self.rows[index]
            key = _text(row.get("key"))
            fields = field_fix_fields(row, self.choice.get(index) or {})
            result = await self._read(EB.bibdb_edit_call(
                key=key, fields=fields, dry_run=True,
                entry_type=_text(row.get("entry_type"))))
            payload = result.get("json") or {}
            if not result.get("ok") or not payload:
                self.error = f"预览失败：{key}：{explain_result(result)}"
                self.plan = []
                self._refresh()
                return
            plan.append({"index": index, "key": key, "fields": fields,
                         "expect": _text(payload.get("db_sha256_before")),
                         "changes": payload.get("field_changes") or {},
                         "missing_after": payload.get("missing_after") or []})
        self.busy = False
        self.plan = plan
        self.page.mark("bib_fieldfix_preview", count=len(plan))
        self._refresh()

    def _ask_apply(self) -> None:
        if not self.plan:
            return
        self._confirm(
            title=f"应用 {len(self.plan)} 条字段归位？",
            message=("确认后逐条真写（每条都带前一条的写后 sha 做闸门）；中途失败立即停，"
                     "已写入的保留。\n全部写完后只提交 bib_db/bibliography.jsonl 一条（不推送）。"),
            confirm_label="确认应用",
            on_confirm=lambda e: self._run(self._apply()))

    async def _apply(self) -> None:
        plan = list(self.plan or [])
        if not plan:
            return
        self.busy, self.error = True, ""
        self._refresh()
        expect = _text(plan[0].get("expect"))
        written, last = 0, {}
        for item in plan:
            result = await self._read(EB.bibdb_edit_call(
                key=item["key"], fields=item["fields"], dry_run=False,
                entry_type=_text(self.rows[item["index"]].get("entry_type")),
                expect_db_sha256=expect if written == 0 else _text(expect)))
            payload = result.get("json") or {}
            if not result.get("ok") or not payload:
                self.error = f"第 {written + 1} 条写入失败：{explain_result(result)}"
                break
            written += 1
            last = payload
            expect = _text(payload.get("db_sha256_after")) or GIT.file_sha256(self._db())
        self.busy = False
        self.plan, self.preview = [], None
        if written:
            await self._commit_write(dict(last, written=True),
                                     label=f"字段归位 {written} 条", count=written)
            self.page.stats_data = None
            self._toast(ops_notice(f"字段归位 {written} 条", {"written": True}, self.commit))
            await self._load()
            return
        self._refresh()

    # -- 构建 -----------------------------------------------------------
    def build(self) -> ft.Control:
        t = self.t
        if not self.loaded and not self.loading:
            self._run(self.ensure_loaded())
        controls: list = [self._head_card()]
        for index, row in enumerate(self.rows):
            controls.append(self._row_card(index, row))
        if self.plan:
            controls.append(self._plan_card())
        bar = self._error_bar()
        if bar is not None:
            controls.append(bar)
        bar = self._notice_bar()
        if bar is not None:
            controls.append(bar)
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _head_card(self) -> ft.Control:
        t = self.t
        count = len(self.rows)
        return C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("字段归位", t,
                            note="suggest-field-fixes --json（只读候选；应用走 edit --json-fields）"),
            C.text("引擎按本库已有条目猜缺失字段的候选值（附证据与风险）。勾选 + 选值后先逐条 "
                   "dry-run 预览，确认后逐条真写，全部写完合 1 条提交；不静默批量写。",
                   t, "small", t.palette.text_muted),
            ft.Row(spacing=t.sp("1"), wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                       C.secondary_button("重新读取候选",
                                          lambda e: self._run(self.ensure_loaded(True)), t,
                                          icon="REFRESH", disabled=self.busy),
                       C.text(f"候选 {count} 条（一次最多 {FIX_LIMIT} 条）" if self.loaded
                              else "正在读取候选…", t, "small", t.palette.text_faint),
                       C.text("处理中…" if self.busy else "", t, "small",
                              t.palette.text_faint),
                   ]),
        ]), t)

    def _row_card(self, index: int, row: dict) -> ft.Control:
        t = self.t
        key = _text(row.get("key"))
        picked = self.choice.get(index) or {}
        boxes = []
        for field in field_fix_missing(row):
            candidates = field_fix_candidates(row, field)
            options = []
            seen = set()
            for item in candidates:
                value = _text(item.get("value")).strip()
                if not value or value in seen:
                    continue
                seen.add(value)
                source = _text(item.get("source")) or "候选"
                risk = _text(item.get("risk"))
                options.append(ft.DropdownOption(
                    key=value, text=f"{value} · {source}" + (f"（风险：{risk}）" if risk else "")))
            if not options:
                boxes.append(C.text(f"{field_label(field)}：无候选（需人工补）", t, "small",
                                    t.palette.text_faint))
                continue
            boxes.append(ft.Dropdown(
                label=field_label(field), value=picked.get(field, ""), dense=True,
                width=t.px(300), text_size=t.font("body"), options=options,
                on_change=lambda e, i=index, f=field: self.choice.setdefault(i, {}).__setitem__(
                    f, e.control.value or "")))
        checked = index in self.selected
        head = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[
                          ft.Checkbox(value=checked, label=key or "?",
                                      on_change=lambda e, i=index: self._toggle(i, e.control.value)),
                          C.text(_text(row.get("entry_type")) or "?", t, "small",
                                 t.palette.text_muted),
                          C.text("缺：" + "、".join(field_label(name)
                                                   for name in field_fix_missing(row)),
                                 t, "caption", t.palette.text_faint),
                      ])
        return C.card(ft.Column(spacing=t.sp("half"), controls=[
            head, ft.Row(spacing=t.sp("1"), wrap=True, controls=boxes)]), t)

    def _toggle(self, index: int, value: bool) -> None:
        if value:
            self.selected.add(index)
        else:
            self.selected.discard(index)
        self.plan = []
        self._refresh()

    def _plan_card(self) -> ft.Control:
        t = self.t
        lines = [field_fix_line(self.rows[item["index"]], (self.choice.get(item["index"]) or {}))
                 for item in self.plan]
        controls: list = [C.section_title(
            f"预览 · {len(self.plan)} 条（未写库）", t,
            note="逐条 edit --dry-run；确认后写库合 1 条提交")]
        controls += [C.text(line, t, "small", t.palette.text) for line in lines[:30]]
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            C.primary_button("确认应用并提交", lambda e: self._ask_apply(), t,
                             icon="SAVE", disabled=self.busy),
            C.secondary_button("放弃预览", lambda e: self._clear_plan(), t),
        ]))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _clear_plan(self) -> None:
        self.plan = []
        self._refresh()


# ── R4 库健康 + R5 审阅批量 ─────────────────────────────────────────────
class BibHealthView(_OpsView):
    """库健康（lint 计数 + 版本层留痕）与批量裁决（先 dry-run 出账，再合 1 条提交）。"""

    def __init__(self, page) -> None:
        super().__init__(page)
        self.counts: dict = {}
        self.conflicts: dict = {}
        self.items: list[dict] = []
        self.probe: dict = {}
        self.selected: set[int] = set()
        self.preview: dict | None = None
        self.pending: dict | None = None
        self.batch_label = ""

    async def ensure_loaded(self, force: bool = False) -> None:
        if self.loading or (self.loaded and not force):
            return
        self.loading = True
        try:
            await self._load()
        finally:
            self.loading = False
            self.loaded = True
        self._refresh()

    async def _load(self) -> None:
        self.busy, self.error = True, ""
        self._refresh()
        lint = await self._read(EB.bibdb_lint_call())
        conflicts = await self._read(EB.bibdb_conflicts_call(pending_only=True))
        payload = conflicts.get("json") or {}
        self.conflicts = payload
        self.items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
        self.selected = {index for index in self.selected if index < len(self.items)}
        self.counts = health_counts(lint.get("json") or {}, payload)
        try:
            self.probe = await asyncio.to_thread(GIT.status, self._db(),
                                                 notes_repo=self._notes())
        except Exception as exc:  # noqa: BLE001
            self.probe = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.busy = False
        if not lint.get("ok"):
            self.error = explain_result(lint)
        self.page.mark("bib_health_load", records=self.counts.get("records"),
                       pending=self.counts.get("pending"))

    def _ask_adopt_all(self) -> None:
        self._run(self._preview_all())

    async def _preview_all(self) -> None:
        self.busy, self.error = True, ""
        self._refresh()
        result = await self._read(EB.bibdb_resolve_call(adopt_all_pending=True, dry_run=True))
        self.busy = False
        payload = result.get("json") or {}
        if not result.get("ok") or not payload:
            self.error = explain_result(result)
            self._refresh()
            return
        self.preview = payload
        self.batch_label = "一键采用（全部待裁决）"
        self.pending = {"adopt_all_pending": True, "dry_run": False,
                        "expect_db_sha256": _text(payload.get("db_sha256_before"))}
        self.page.mark("bib_batch_preview", pending=self.counts.get("pending"),
                       records=payload.get("records"))
        self._refresh()

    def _ask_preview_selected(self) -> None:
        if not self.selected:
            self.error = "先勾选要采用的冲突条目。"
            self._refresh()
            return
        self._run(self._preview_selected())

    async def _preview_selected(self) -> None:
        items = [self.items[index] for index in sorted(self.selected)]
        self.busy, self.error = True, ""
        self._refresh()
        plan, total, changed = [], 0, 0
        for item in items:
            result = await self._read(EB.bibdb_resolve_call(
                key=_text(item.get("key")), action="adopt",
                incoming_sha256=_text(item.get("incoming_sha256")), dry_run=True))
            payload = result.get("json") or {}
            if not result.get("ok") or not payload:
                self.error = f"预览失败：{_text(item.get('key'))}：{explain_result(result)}"
                self.busy = False
                self._refresh()
                return
            plan.append({"key": _text(item.get("key")),
                         "incoming_sha256": _text(item.get("incoming_sha256")),
                         "expect": _text(payload.get("db_sha256_before"))})
            total += len(payload.get("adopted") or []) or 1
            changed += 1
        self.busy = False
        preview = dict(self.conflicts)
        preview.update({"adopted": [None] * changed, "records": changed,
                        "conflicts": total, "changed": True,
                        "totals_before": (self.conflicts.get("totals") or {}),
                        "totals_after": {"pending": max(0, self.counts.get("pending", 0) - changed),
                                         "resolved": self.counts.get("resolved", 0) + changed},
                        "db_sha256_before": plan[0].get("expect") if plan else "",
                        "db_sha256_after": plan[0].get("expect") if plan else ""})
        self.preview = preview
        self.batch_label = f"采用所选 {len(plan)} 条待裁决"
        self.pending = {"plan": plan, "dry_run": False}
        self.page.mark("bib_batch_preview", selected=len(plan))
        self._refresh()

    def _ask_write_batch(self) -> None:
        if not self.pending:
            return
        self._confirm(
            title=f"{self.batch_label}？",
            message=("确认后真写（带 --expect-db-sha256 闸门）；本次所有采用合并为 1 条版本提交，"
                     "只提交 bib_db/bibliography.jsonl（不推送）。\n"
                     "失败会显示引擎原文；已写入的保留（不回滚）。"),
            confirm_label="确认写入",
            on_confirm=lambda e: self._run(self._write_batch()))

    async def _write_batch(self) -> None:
        pending = dict(self.pending or {})
        if not pending:
            return
        self.busy, self.error = True, ""
        self._refresh()
        if pending.get("adopt_all_pending"):
            result = await self._read(EB.bibdb_resolve_call(
                adopt_all_pending=True, dry_run=False,
                expect_db_sha256=_text(pending.get("expect_db_sha256"))))
            self.busy = False
            payload = result.get("json") or {}
            if not result.get("ok") or not payload:
                self.error = explain_result(result)
                self._refresh()
                return
            count = len(payload.get("adopted") or []) or _int(payload.get("records"))
            await self._commit_write(payload, label=f"一键采用 {count} 条", count=count)
            self.page.stats_data = None
            self.preview, self.pending = None, None
            self._toast(ops_notice(f"一键采用 {count} 条", payload, self.commit))
            await self._load()
            return
        plan = list(pending.get("plan") or [])
        written, last, expect = 0, {}, ""
        for item in plan:
            expect = expect or _text(item.get("expect"))
            result = await self._read(EB.bibdb_resolve_call(
                key=_text(item.get("key")), action="adopt",
                incoming_sha256=_text(item.get("incoming_sha256")), dry_run=False,
                expect_db_sha256=expect))
            payload = result.get("json") or {}
            if not result.get("ok") or not payload:
                self.error = f"第 {written + 1} 条写入失败：{explain_result(result)}"
                break
            written += 1
            last = payload
            expect = _text(payload.get("db_sha256_after")) or GIT.file_sha256(self._db())
        self.busy = False
        self.preview, self.pending = None, None
        if written:
            await self._commit_write(dict(last, written=True),
                                     label=f"采用所选 {written} 条", count=written)
            self.page.stats_data = None
            self._toast(ops_notice(f"采用所选 {written} 条", {"written": True}, self.commit))
            await self._load()
            return
        self._refresh()

    def _toggle(self, index: int, value: bool) -> None:
        if value:
            self.selected.add(index)
        else:
            self.selected.discard(index)
        self._refresh()

    # -- 构建 -----------------------------------------------------------
    def build(self) -> ft.Control:
        t = self.t
        if not self.loaded and not self.loading:
            self._run(self.ensure_loaded())
        controls: list = [self._health_card(), self._history_card(), self._batch_card()]
        if self.preview:
            controls.append(self._preview_card())
        bar = self._error_bar()
        if bar is not None:
            controls.append(bar)
        bar = self._notice_bar()
        if bar is not None:
            controls.append(bar)
        return ft.Column(spacing=t.sp("2"), controls=controls)

    def _health_card(self) -> ft.Control:
        t = self.t
        counts = self.counts
        cards = ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.stat_card("记录", str(_int(counts.get("records"))), "库内条目", t),
            C.stat_card("待裁决", str(_int(counts.get("pending"))), "冲突队列", t),
            C.stat_card("警告", str(_int(counts.get("warnings"))), "lint", t),
            C.stat_card("近似重复", str(_int(counts.get("near"))), "对", t),
        ])
        controls: list = [C.section_title(
            "库健康", t, note="lint --json（退出码 1 = 有警告，仍出完整 JSON）"), cards]
        controls += [C.text(line, t, "small", t.palette.text) for line in health_lines(counts)]
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                                   C.secondary_button("刷新", lambda e: self._run(
                                       self.ensure_loaded(True)), t, icon="REFRESH",
                                                      disabled=self.busy),
                                   C.text("处理中…" if self.busy else "", t, "small",
                                          t.palette.text_faint),
                               ]))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _history_card(self) -> ft.Control:
        t = self.t
        controls: list = [C.section_title(
            "已处理留痕", t, note="最近库提交 + 累计计数（完整历史见 git log）")]
        controls += [C.text(line, t, "small", t.palette.text)
                     for line in git_recent_lines(self.probe)]
        controls.append(C.text(
            f"已裁决冲突 {_int(self.counts.get('resolved'))} 条 · "
            f"已确认非重复 {_int(self.counts.get('acknowledged'))} 对"
            "（确认非重复只写 health-ignore.json，不产生库提交）",
            t, "caption", t.palette.text_muted))
        return C.card(ft.Column(spacing=t.sp("half"), controls=controls), t)

    def _batch_card(self) -> ft.Control:
        t = self.t
        pending = _int(self.counts.get("pending"))
        controls: list = [C.section_title(
            "审阅批量裁决", t, note="先 dry-run 出账，再二次确认；合 1 条提交")]
        if pending <= 0:
            controls.append(C.text("当前没有待裁决冲突（库与预览一致，零写入）。",
                                   t, "small", t.palette.text_muted))
        for index, item in enumerate(self.items[:30]):
            controls.append(ft.Row(spacing=t.sp("1"),
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Checkbox(value=index in self.selected,
                            label=_text(item.get("key")) or "?",
                            on_change=lambda e, i=index: self._toggle(i, e.control.value)),
                C.text("冲突指纹 " + (GIT.short_sha(
                    _text(item.get("incoming_sha256")), 8) if item.get("incoming_sha256")
                    else "（缺失）"), t, "caption", t.palette.text_faint),
            ]))
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.secondary_button("预览所选（逐条 dry-run）",
                               lambda e: self._ask_preview_selected(), t,
                               disabled=self.busy or pending <= 0),
            C.secondary_button("全部采用预览（--adopt-all-pending --dry-run）",
                               lambda e: self._ask_adopt_all(), t,
                               disabled=self.busy or pending <= 0),
        ]))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _preview_card(self) -> ft.Control:
        t = self.t
        payload = self.preview or {}
        controls: list = [C.section_title(f"{self.batch_label} · 预览（未写库）", t,
                                          note="确认后合 1 条版本提交")]
        controls += [C.text(line, t, "small", t.palette.text)
                     for line in batch_preview_lines(payload)]
        controls.append(ft.Row(spacing=t.sp("1"), wrap=True, controls=[
            C.primary_button("确认批量写入并提交", lambda e: self._ask_write_batch(), t,
                             icon="SAVE", disabled=self.busy),
            C.secondary_button("放弃预览", lambda e: self._clear_preview(), t),
        ]))
        return C.card(ft.Column(spacing=t.sp("1"), controls=controls), t)

    def _clear_preview(self) -> None:
        self.preview, self.pending, self.batch_label = None, None, ""
        self._refresh()
