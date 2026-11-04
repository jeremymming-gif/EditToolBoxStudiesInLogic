"""引用库 · 待处理（批 115 夜间重设计 BPAGES-A，VIEW_KEY="review"）。

冲突裁决页。最关键的口径：**157 条冲突 ≠ 135 条记录**——一条记录可以有多条
冲突（C014/C017 可能同属 li2024analysis），所以页面任何位置都必须分开写
「157 条冲突 · 涉及 135 条记录」，绝不出现裸 135 或「135 条冲突」。

本页是写库页：勾选/详情里选择字段只是**暂存意图**，必须再点确认条上的
「确认写入」才会调用引擎写库（dry-run 先行、带 db sha 闸、失败即停）。
"""

from __future__ import annotations

import asyncio

import flet as ft

from services import engine_bridge as EB
from ui import bib_shell as S
from ui import components as C
from ui.tokens import Tokens

VIEW_KEY = "review"
TITLE = "待处理"
ROLE = "write"

QUEUE_W = 506
DETAIL_W = 376
KEY_FIELDS = ("entry_type", "title", "author", "year", "doi")
SEVERITY_ORDER = {"高": 0, "中": 1, "低": 2}


def _short_sha(sha: str) -> str:
    sha = str(sha or "")
    return f"{sha[:8]}…" if len(sha) > 8 else sha


def severity_of(item: dict) -> str:
    """严重度是界面估算（引擎 payload 没有该字段）：关键字段差异 → 高。"""
    diff = item.get("diff") or {}
    if not diff:
        return "低"
    if all(not str((change or {}).get("new") or "").strip()
           for change in diff.values()):
        return "低"
    if any(field in KEY_FIELDS for field in diff):
        return "高"
    return "中"


def display_key(record_key: str) -> str:
    key = str(record_key or "").strip()
    return key


def match_text(item: dict, text: str) -> bool:
    text = str(text or "").strip().lower()
    if not text:
        return True
    hay = " ".join(str(item.get(name) or "") for name in
                   ("record_key", "title", "field", "field_name", "issue", "slug"))
    return text in hay.lower()


def rows_from(data: dict) -> list[dict]:
    """把 conflicts --json 的 pending items 映射成队列表行。"""
    items = list(data.get("items") or [])
    out: list[dict] = []
    pending = 0
    for index, item in enumerate(items):
        if item.get("pending") is False or item.get("resolution"):
            continue
        pending += 1
        number = item.get("stable_no") or f"C{index + 1:03d}"
        diff = item.get("diff") or {}
        field = item.get("field") or (next(iter(diff), "") if diff else "")
        field_label = item.get("field_label") or field or "—"
        out.append({
            "no": str(number),
            "key": str(item.get("record_key") or item.get("bib_key") or ""),
            "field": field_label,
            "source": " · ".join(x for x in (item.get("issue"), item.get("slug")) if x) or "—",
            "severity": severity_of(item),
            "item": item,
            "_pending_index": pending,
        })
    return out


class ReviewView:
    def __init__(self, page) -> None:
        self.page = page
        self.items: list[dict] = []
        self.all_rows: list[dict] = []
        self.rows: list[dict] = []
        self.total_conflicts = 0
        self.total_records = 0
        self.records_known = False
        self.pending = 0
        self.db_sha = ""
        self.loaded = False
        self.loading = False
        self.running = False
        self.error = ""
        self.success = ""
        self.cancelled = False
        self.text = ""
        self.field_filter = ""
        self.severity_filter = ""
        self.sort_key = "severity"
        self.sort_desc = True
        self.selected: set[str] = set()
        self.selected_key: str | None = None
        self.staged: dict | None = None
        self.adopt: dict[str, bool] = {}
        self.note = ""
        self.limit = 100
        self._task_running = False
        self._build_width = 0.0

    # ── 基础 ────────────────────────────────────────────────────────────
    @property
    def t(self) -> Tokens:
        return self.page.t

    def _notes(self) -> str:
        return str(getattr(getattr(self.page, "app", None), "notes_repo", "") or "")

    def _db(self) -> str:
        return str((getattr(self.page, "stats_data", None) or {}).get("db") or "")

    def _refresh(self) -> None:
        render = getattr(self.page, "_render", None)
        if callable(render):
            render()

    def _mark(self, event: str, **kwargs) -> None:
        marker = getattr(self.page, "mark", None)
        if callable(marker):
            try:
                marker(event, **kwargs)
            except Exception:  # noqa: BLE001
                pass

    def _schedule(self, runner) -> None:
        if self._task_running:
            return
        self._task_running = True
        run = getattr(self.page.page, "run_task", None)
        if callable(run):
            run(runner)
        else:
            asyncio.run(runner())

    def activate(self) -> None:
        if not self.loaded and not self.loading:
            self._schedule(self._load)

    def refresh_running(self) -> None:
        return None

    def primary_action(self):
        return None

    # ── 加载 ────────────────────────────────────────────────────────────
    async def _load(self) -> None:
        if self.loading:
            return
        self.loading = True
        self.error = ""
        self._refresh()
        try:
            result = await asyncio.to_thread(
                EB.read_json_command, EB.bibdb_conflicts_call(pending_only=True, db=self._db()),
                timeout=30.0, notes=self._notes())
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.loading = False
        self._task_running = False
        if not result.get("ok"):
            self.error = f"读取失败：{_first_line(result.get('error'))}"
            self._refresh()
            return
        data = result.get("json") or {}
        totals = data.get("totals") or {}
        self.db_sha = str(data.get("db_sha256") or "")
        self.items = list(data.get("items") or [])
        self.all_rows = rows_from(data)
        raw_total = totals.get("total")
        raw_pending = totals.get("pending")
        self.total_conflicts = (int(raw_total) if raw_total is not None
                                else len(self.items))
        self.pending = (int(raw_pending) if raw_pending is not None
                        else len(self.all_rows))
        stats = getattr(self.page, "stats_data", None) or {}
        # 记录数是「冲突涉及的记录数」（含已裁决）；优先取 conflicts payload，
        # 再退回 stats。只有全部冲突都还在待处理时才能从行重算，否则宁可不写。
        raw_records = totals.get("records")
        if raw_records is None:
            raw_records = (stats.get("conflicts") or {}).get("records")
        try:
            self.total_records = int(raw_records or 0)
        except (TypeError, ValueError):
            self.total_records = 0
        self.records_known = self.total_records > 0
        if not self.records_known and self.total_conflicts == self.pending:
            self.total_records = len({row["key"] for row in self.all_rows})
            self.records_known = self.total_records > 0
        self.loaded = True
        self._apply_filters()
        self._mark("bib_review_loaded", conflicts=self.total_conflicts,
                   records=self.total_records)
        self._refresh()

    def reload(self) -> None:
        self._schedule(self._load)

    # ── 单一计数投影 ────────────────────────────────────────────────────
    def _counts(self) -> dict[str, int]:
        """header / 筛选计数 / 表尾 / 确认条 / dock 的唯一计数来源。

        ``totals`` 是引擎 conflicts payload 的权威口径：total 是累计冲突数，
        pending 是仍待裁决数，resolved 由 total-pending 推出（不另读一个
        可能过期的字段）。records 只在已知时展示，避免把「统计缺失」写成
        「涉及 0 条记录」——这正是复评里同屏三套数的根因。
        """
        total = max(0, int(self.total_conflicts or 0))
        pending = min(max(0, int(self.pending or 0)), total)
        return {
            "total": total,
            "pending": pending,
            "resolved": max(0, total - pending),
            "records": max(0, int(self.total_records or 0)),
        }

    def _pending_line(self) -> str:
        """待处理/累计口径：pending=0 时只写已裁决，不再露记录数。"""
        counts = self._counts()
        if counts["pending"] == 0:
            return f"0 条待处理 · 累计 {counts['resolved']} 条已裁决"
        parts = [f"{counts['pending']} 条待处理", f"{counts['total']} 条冲突"]
        if counts["records"] > 0:
            parts.append(f"涉及 {counts['records']} 条记录")
        return " · ".join(parts)

    # ── 筛选/排序 ───────────────────────────────────────────────────────
    def _apply_filters(self) -> None:
        rows = [row for row in self.all_rows if match_text(row["item"], self.text)]
        if self.field_filter:
            rows = [row for row in rows
                    if str(row["item"].get("field") or "") == self.field_filter
                    or row["field"] == self.field_filter]
        if self.severity_filter:
            rows = [row for row in rows if row["severity"] == self.severity_filter]
        reverse = self.sort_desc
        if self.sort_key == "severity":
            rows.sort(key=lambda row: (SEVERITY_ORDER.get(row["severity"], 9), row["no"]),
                      reverse=reverse)
        elif self.sort_key == "no":
            rows.sort(key=lambda row: row["no"], reverse=reverse)
        self.rows = rows[: self.limit]

    def _on_text(self, e) -> None:
        self.text = str(getattr(e.control, "value", "") or "")
        self._apply_filters()
        self._refresh()

    def _on_field(self, e) -> None:
        key = str(getattr(e, "data", "") or "")
        self.field_filter = "" if key in ("", "全部") else key
        self._apply_filters()
        self._refresh()

    def _on_severity(self, e) -> None:
        key = str(getattr(e, "data", "") or "")
        self.severity_filter = "" if key in ("", "全部") else key
        self._apply_filters()
        self._refresh()

    def _on_sort(self, index: int) -> None:
        keys = ("no", "key", "field", "source", "severity")
        if index >= len(keys):
            return
        key = keys[index]
        self.sort_desc = not self.sort_desc if self.sort_key == key else key == "severity"
        self.sort_key = key
        self._apply_filters()
        self._refresh()

    def load_more(self) -> None:
        self.limit += 100
        self._apply_filters()
        self._refresh()

    # ── 选择 ────────────────────────────────────────────────────────────
    def _toggle(self, no: str, value: bool) -> None:
        if value:
            self.selected.add(no)
        else:
            self.selected.discard(no)
        # 批选与详情意图互斥（单槽）
        self.staged = None
        self._refresh()

    def _toggle_row(self, e, no: str) -> None:
        self._toggle(no, bool(getattr(e.control, "value", False)))

    def _select_page(self, e) -> None:
        value = bool(getattr(e.control, "value", False))
        self.selected = {row["no"] for row in self.rows} if value else set()
        self.staged = None
        self._refresh()

    def _row_clicked(self, row: dict) -> None:
        self.selected_key = row["no"]
        self.adopt = {field: True for field in (row["item"].get("diff") or {})}
        self._refresh()

    def _selected_row(self) -> dict | None:
        for row in self.all_rows:
            if row["no"] == self.selected_key:
                return row
        return None

    def _batch_rows(self) -> list[dict]:
        return [row for row in self.all_rows if row["no"] in self.selected]

    def _batch_records(self) -> int:
        return len({row["key"] for row in self._batch_rows()})

    def clear_selection(self) -> None:
        self.selected.clear()
        self.staged = None
        self._refresh()

    # ── 暂存意图 ────────────────────────────────────────────────────────
    def _stage_detail(self, adopt_all: bool) -> None:
        row = self._selected_row()
        if row is None:
            return
        diff = row["item"].get("diff") or {}
        adopted = [field for field in diff if self.adopt.get(field)]
        if not adopted:
            return
        if adopt_all:
            adopted = list(diff)
        self.staged = {"kind": "detail", "no": row["no"], "row": row,
                       "fields": adopted, "note": self.note}
        self.selected.clear()
        self._refresh()

    def _stage_keep(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self.staged = {"kind": "keep", "no": row["no"], "row": row,
                       "fields": [], "note": self.note}
        self.selected.clear()
        self._refresh()

    def _stage_batch(self) -> None:
        rows = self._batch_rows()
        if len(rows) < 2:
            return
        self.staged = {"kind": "batch", "rows": rows, "fields": [], "note": self.note}
        self._refresh()

    def _unstage(self) -> None:
        self.staged = None
        self._refresh()

    def _confirm_disabled(self) -> bool:
        if self.staged is None:
            return True
        if self.staged["kind"] == "detail":
            return not self.staged.get("fields")
        return False

    # ── 写入 ────────────────────────────────────────────────────────────
    def _confirm(self) -> None:
        if self._confirm_disabled():
            return
        self._schedule(self._write)

    async def _write(self) -> None:
        staged = self.staged
        if staged is None or self.running:
            return
        self.running = True
        self.error = ""
        self.success = ""
        self._refresh()
        plans = []
        if staged["kind"] == "batch":
            plans = [{"row": row, "action": "adopt", "fields": []}
                     for row in staged["rows"]]
        elif staged["kind"] == "keep":
            plans = [{"row": staged["row"], "action": "keep", "fields": []}]
        else:
            plans = [{"row": staged["row"], "action": "adopt",
                      "fields": staged.get("fields") or []}]
        written = 0
        fields_written = 0
        try:
            for plan in plans:
                item = plan["row"]["item"]
                sha = str(item.get("incoming_sha256") or "")
                for dry in (True, False):
                    call = EB.bibdb_resolve_call(
                        key=str(plan["row"]["key"]), action=plan["action"],
                        fields=",".join(plan["fields"]), incoming_sha256=sha,
                        note=staged.get("note") or "", dry_run=dry,
                        expect_db_sha256=self.db_sha, db=self._db())
                    result = await asyncio.to_thread(
                        EB.read_json_command, call, timeout=60.0, notes=self._notes())
                    if not result.get("ok"):
                        self.error = (f"写入失败：{_first_line(result.get('error'))}"
                                      if not dry else
                                      f"预览失败：{_first_line(result.get('error'))}")
                        break
                    if not dry:
                        written += 1
                        fields_written += len(plan["fields"])
                if self.error:
                    break
        except Exception as exc:  # noqa: BLE001
            self.error = f"写入失败：{_first_line(str(exc))}"
        self.running = False
        self._task_running = False
        if not self.error:
            self.staged = None
            self.selected.clear()
            if written:
                self.success = (f"已完成 · {written} 条记录 · {fields_written} 个字段"
                                " · 未推送")
            else:
                self.success = "没有需要写入的内容。"
            self._mark("bib_review_write", records=written)
            # 写后重读队列与计数
            self._schedule(self._load)
        self._refresh()

    def cancel(self) -> None:
        self.cancelled = True
        self.running = False
        self._refresh()

    # ── 视图 ────────────────────────────────────────────────────────────
    def _design_width(self) -> float:
        try:
            width = float(self.page.page.width or 0)
        except Exception:  # noqa: BLE001
            width = 0.0
        if width <= 0:
            width = float(getattr(getattr(self.page, "app", None), "window_w", 0) or 0)
        return width / max(self.t.k, 1e-6)

    def _queue(self) -> ft.Control:
        t = self.t
        weight = [28, 42, 142, 112, 116, 66]
        header = ft.Container(
            height=t.px(30), padding=ft.Padding(left=t.px(8), right=t.px(8), top=0, bottom=0),
            border=ft.Border(top=ft.BorderSide(0.6, t.palette.border),
                             bottom=ft.BorderSide(0.6, t.palette.border)),
            content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[ft.Container(width=t.px(28), content=ft.Checkbox(
                               on_change=self._select_page,
                               visual_density=ft.VisualDensity.COMPACT, splash_radius=0)),
                               *[ft.Container(
                                   expand=w, padding=ft.Padding(right=t.px(8), left=0, top=0, bottom=0),
                                   content=C.text(label, t, "caption", t.palette.text_faint,
                                                  weight="semibold", max_lines=1))
                                 for w, label in zip(weight[1:],
                                                     ["编号", "记录 key", "字段", "来源", "严重度"])]]))
        rows = []
        for row in self.rows:
            selected = row["no"] in self.selected
            cells = [
                ft.Container(width=t.px(28), content=ft.Checkbox(
                    value=selected, on_change=lambda e, no=row["no"]: self._toggle_row(e, no),
                    visual_density=ft.VisualDensity.COMPACT, splash_radius=0)),
                C.text(row["no"], t, "caption", t.palette.text_muted),
                C.text(row["key"], t, "caption", t.palette.text,
                       font_family=t.mono_family, max_lines=1,
                       overflow=ft.TextOverflow.ELLIPSIS),
                C.text(row["field"], t, "caption", t.palette.text_muted),
                C.text(row["source"], t, "caption", t.palette.text_faint, max_lines=1),
                C.badge(row["severity"], t, "error" if row["severity"] == "高" else
                        ("warn" if row["severity"] == "中" else "neutral")),
            ]
            rows.append(ft.Container(
                height=t.px(32), bgcolor=t.palette.accent_soft
                if row["no"] == self.selected_key else None,
                padding=ft.Padding(left=t.px(8), right=t.px(8), top=0, bottom=0),
                border=ft.Border(bottom=ft.BorderSide(0.6, t.palette.border)),
                on_click=lambda e, row=row: self._row_clicked(row),
                content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=cells)))
        if not rows:
            rows.append(S.empty_row(t, "没有待处理冲突", height=32))
        more = self._counts()["pending"] - len(self.rows)
        footer = []
        if more > 0 and len(self.all_rows) > len(self.rows):
            footer.append(C.text_button(f"载入更多 · 还剩 {more} 条",
                                        lambda e: self.load_more(), t, height=28))
        footer.append(C.text(f"显示 {len(self.rows)} / {self._counts()['pending']} 条待处理",
                             t, "caption", t.palette.text_faint))
        return ft.Column(spacing=0, controls=[header, *rows, *footer])

    def _detail(self) -> ft.Control:
        t = self.t
        row = self._selected_row()
        if row is None:
            return S.detail_panel(
                t, title="冲突详情", meta="选一行看字段；默认不自动选中。",
                body=ft.Column(spacing=t.sp("1"), controls=[
                    C.empty_line("未选择冲突", t),
                    C.text("一行一条冲突；同一记录可能有多条。", t,
                           "caption", t.palette.text_faint, max_lines=3),
                ]), width=DETAIL_W)
        item = row["item"]
        diff = item.get("diff") or {}
        meta_bits = [f"{row['key']} · 记录共 "
                     f"{sum(1 for r in self.all_rows if r['key'] == row['key'])} 条冲突",
                     "已核实" if item.get("verified") else "未核实"]
        head = C.text((item.get("title") or "（无题名）"), t, role="item-title",
                      max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        lines = [head, C.text(" · ".join(meta_bits), t, "caption", t.palette.text_faint)]
        field_rows = []
        for field, change in diff.items():
            change = change or {}
            old, new = str(change.get("old") or "—"), str(change.get("new") or "—")
            field_rows.append(ft.Container(
                height=t.px(44), border=ft.Border(bottom=ft.BorderSide(0.6, t.palette.border)),
                content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[
                                   ft.Container(width=t.px(20), content=ft.Checkbox(
                                       value=bool(self.adopt.get(field, True)),
                                       on_change=lambda e, f=field: self.adopt.__setitem__(
                                           f, bool(e.control.value)),
                                       visual_density=ft.VisualDensity.COMPACT, splash_radius=0)),
                                   C.text(field, t, "caption", t.palette.text_faint,
                                          font_family=t.mono_family, width=86),
                                   C.text(old, t, "caption", t.palette.text_muted,
                                          expand=True, max_lines=2,
                                          overflow=ft.TextOverflow.ELLIPSIS, tooltip=old),
                                   C.text("→", t, "caption", t.palette.text_faint),
                                   C.text(new, t, "caption", t.palette.text, expand=True,
                                          max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                                          tooltip=new),
                               ])))
        adopted = sum(1 for field in diff if self.adopt.get(field, True))
        actions = [
            C.secondary_button(f"逐字段合并（{adopted}）",
                               lambda e: self._stage_detail(False), t, height=28),
            C.secondary_button("采用全部字段", lambda e: self._stage_detail(True),
                               t, height=28),
            C.secondary_button("保留当前", lambda e: self._stage_keep(), t, height=28),
        ]
        if adopted == 0:
            actions[0].disabled = True
        note = C.text_field(t, value=self.note, hint_text="备注（可选）",
                            on_change=lambda e: setattr(self, "note",
                                                        str(e.control.value or "")),
                            width=340)
        body = ft.Column(spacing=t.sp("1"), scroll=S.scrollbar(), controls=[
            C.text(f"{row['no']} · {row['severity']}", t, "caption", t.palette.text_faint),
            *lines,
            ft.Container(height=t.sp("half")),
            *field_rows,
            note,
            C.text(f"已选 {adopted} / {len(diff)}", t, "caption", t.palette.text_faint),
        ])
        return S.detail_panel(t, title="冲突详情", body=body, actions=actions,
                              meta=f"{item.get('issue') or ''}/{item.get('slug') or ''}".strip("/")
                              or None, width=DETAIL_W)

    def _batch(self) -> ft.Control | None:
        t = self.t
        rows = self._batch_rows()
        if len(rows) < 2:
            return None
        return S.batch_bar(
            t, count=len(rows),
            actions=[C.text_button("暂存批选", lambda e: self._stage_batch(), t,
                                   height=28)],
            on_clear=lambda e: self.clear_selection())

    def _confirm_bar(self) -> ft.Control | None:
        t = self.t
        counts = self._counts()
        if counts["pending"] == 0:
            # 没有待处理就没有可确认的写入：整条隐藏，避免出现无对象的主按钮。
            return None
        staged = self.staged
        if staged is None:
            consequence = "先在详情选字段或批选，再确认写入。"
            detail = f"{self._pending_line()} · 本页只暂存意图"
        elif staged["kind"] == "batch":
            consequence = f"将写入 {len(staged['rows'])} 条记录（逐条）"
            detail = "失败即停；同一记录不同交回稿版本会被拦下。"
        elif staged["kind"] == "keep":
            consequence = "保留当前值，标记这条冲突为已裁决"
            detail = f"{staged['row']['no']} · {staged['row']['key']}"
        else:
            consequence = f"采用 {len(staged.get('fields') or [])} 个字段的来稿值"
            detail = f"{staged['no']} · {staged['row']['key']}"
        bar = S.confirm_bar(
            t, consequence=consequence, detail=detail, sha=_short_sha(self.db_sha),
            on_confirm=lambda e: self._confirm(),
            confirm_label="确认写入", confirm_disabled=self._confirm_disabled(),
            on_repreview=(lambda e: self._unstage()) if staged is not None else None,
            repreview_label="取消暂存")
        return bar

    def _filter(self) -> ft.Control:
        t = self.t
        field = C.text_field(t, value=self.text, hint_text="按记录 key / 题名筛选",
                             on_change=self._on_text, width=220)
        field._bib_search_field = True
        field_select = C.select(
            t, value=self.field_filter or "全部",
            options=[("", "全部字段"), ("title", "title"), ("author", "author"),
                     ("year", "year"), ("doi", "doi"), ("entry_type", "类型")],
            on_select=self._on_field, width=104)
        severity = C.select(
            t, value=self.severity_filter or "全部",
            options=[("", "全部严重度"), ("高", "高"), ("中", "中"), ("低", "低")],
            on_select=self._on_severity, width=100)
        return S.filter_row(t, search=field, on_search=self._on_text,
                            controls=[field_select, severity],
                            count=C.text(self._pending_line(), t, "caption",
                                         t.palette.text_faint),
                            page=self.page.page, search_width=220)

    def _body(self) -> ft.Control:
        t = self.t
        self._build_width = self._design_width()
        narrow = self._build_width < 1100
        parts: list = []
        if self.error:
            parts.append(S.state_banner(t, "failure", message=self.error,
                                        next_label="复制错误",
                                        on_next=lambda e: self._copy(self.error)))
        if self.success:
            parts.append(S.state_banner(t, "success", message=self.success))
        if self.cancelled:
            parts.append(S.state_banner(t, "cancelled", message="已取消，未写入。"))
        if self.loading and not self.loaded:
            parts.append(S.state_banner(t, "loading", message="运行中"))
        if self.error:
            pass
        queue = self._queue()
        if narrow:
            parts.extend([queue, self._detail()])
        else:
            parts.append(ft.Row(expand=True, spacing=0,
                                vertical_alignment=ft.CrossAxisAlignment.START,
                                controls=[ft.Container(expand=True, content=queue),
                                          self._detail()]))
        batch = self._batch()
        if batch is not None:
            parts.append(batch)
        confirm = self._confirm_bar()
        if confirm is not None:
            parts.append(confirm)
        return ft.Column(spacing=t.sp("1"), expand=True,
                         scroll=S.scrollbar(), controls=parts)

    def _copy(self, text: str) -> None:
        fn = getattr(getattr(self.page, "app", None), "copy_text", None)
        if callable(fn):
            try:
                fn(text)
            except Exception:  # noqa: BLE001
                pass

    def _dock(self) -> ft.Control:
        t = self.t
        counts = self._counts()
        if counts["pending"] == 0:
            text = f"待处理 0 条 · 累计 {counts['resolved']} 条已裁决"
        elif counts["records"] > 0:
            text = f"待处理 {counts['pending']} 条冲突 · 涉及 {counts['records']} 条记录"
        else:
            text = f"待处理 {counts['pending']} 条冲突"
        return C.text(text, t, "caption", t.palette.text_faint)

    def build(self) -> ft.Control:
        self._build_width = self._design_width()
        return S.page_shell(
            self.t, title=TITLE,
            meta=(self._pending_line()
                  if self.loaded else "运行中"),
            role_chip="write", action=None,
            on_gear=lambda e: self._goto("settings"),
            body=ft.Column(spacing=0, expand=True, controls=[
                self._filter(), self._body()]),
            dock=self._dock(), dock_height=S.DOCK_H)

    def _goto(self, view: str) -> None:
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter(view)


def _first_line(text) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return "引擎没有返回说明"
