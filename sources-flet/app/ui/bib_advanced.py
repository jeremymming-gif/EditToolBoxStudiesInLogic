"""引用库 · 查库（批 109 W1a）：基本检索 / 高级检索两页签，**只读**。

逐项复刻旧壳 Tk `bibdb_ui.py::BibDbQueryPage`（B-261）与 Swift `BibDbQueryView`：

* 基本：key（也认别名）/ DOI / 题名（模糊匹配）三选一，回车即查，示例随方式变；
* 高级：多行条件（10 个字段 × 4 种模式，行间 AND）、年份表达式（2003 / 1990-2000 /
  >=2010）、排序（相关度/年份/作者）、最多 1–200 条、严格（模糊门槛 0.90）、增删行；
* 结果：题名 / 作者 / 年份 / key / 状态 / 来源 六列，单选后看详情；
* 详情：已核实标记、来源、高级检索的命中原因（why-hit）、只读 BibTeX、复制 BibTeX、
  在运行日志里显示。

本页只调 `bibdb.py query|query-advanced --json`（不写库、不联网、不调 AI）。纯函数放在
文件上半部，便于 pytest 直接核对文案与格式；控件全部由 `build()` 每次重建，状态留在视图
对象上（与 `pages.BibPage` 的其它子视图同一模式）。
"""
from __future__ import annotations

import asyncio
import time

import flet as ft

from services import engine_bridge as EB
from ui import components as C

#: 六列表头与列宽（旧壳 Treeview：题名/作者/年份/key/状态/来源）
TABLE_COLUMNS = (("题名", 300), ("作者", 130), ("年份", 54),
                 ("key", 130), ("状态", 60), ("来源", 170))

DEFAULT_ROWS_TEXT = "输入查询内容后按回车或点「查询」。"
NO_NOTES = "找不到私有笔记仓。请先到「引用库 → 引用库设置」页指定笔记仓路径。"
ADVANCED_HINT = ("多行之间是 AND（都要满足）；年份行可填 2003 / 1990-2000 / >=2010；"
                 "「模糊」只对文本字段有效。")
QUERY_HINTS = {
    "key": "如 Fine1975（也认 aliases）",
    "doi": "如 10.1007/s10992-020-09558-5（大小写/前缀不敏感）",
    "title": "题名模糊匹配（返回相似度与置信度）",
}
DEFAULT_ROWS = (
    {"field": "author", "mode": "contains", "value": ""},
    {"field": "title", "mode": "contains", "value": ""},
)


# ── 纯函数（可单测，不依赖 flet 页面）──────────────────────────────────────
def fmt_seconds(seconds: float) -> str:
    """用时（<0.05 秒说「很快」，免得显示 0.0 s 像没跑；旧壳同款）。"""
    return "很快" if (seconds or 0) < 0.05 else f"{seconds:.1f} s"


def mode_label(mode: str) -> str:
    return EB.QUERY_MODE_LABELS.get(mode, mode)


def field_label(field: str) -> str:
    return EB.QUERY_FIELD_LABELS.get(field, field)


def match_label(mode: str) -> str:
    return EB.QUERY_MATCH_LABELS.get(mode, mode)


def sort_label(sort: str) -> str:
    return EB.QUERY_SORT_LABELS.get(sort, sort)


def normalize_mode(field: str, mode: str) -> str:
    """字段换了就收紧模式：年份/DOI/别名不收「模糊」，非法值回「精确」（旧壳）。"""
    if field in EB.QUERY_NO_FUZZY and mode == "fuzzy":
        return "exact"
    return mode if mode in EB.QUERY_MATCH_MODES else "exact"


def mode_options(field: str) -> list[str]:
    """该字段可选的匹配方式：年份 / DOI / 别名不列「模糊」（旧壳下拉同款）。"""
    return [mode for mode in EB.QUERY_MATCH_MODES
            if not (field in EB.QUERY_NO_FUZZY and mode == "fuzzy")]


def default_rows() -> list[dict]:
    return [dict(row) for row in DEFAULT_ROWS]


def adv_terms(rows: list[dict]) -> list[tuple[str, str, str]]:
    """行 → (field, value, mode)；**空值行跳过**（旧壳语义：不算条件）。"""
    terms: list[tuple[str, str, str]] = []
    for row in rows or []:
        value = str(row.get("value") or "").strip()
        if not value:
            continue
        field = str(row.get("field") or "author")
        terms.append((field, value, normalize_mode(field, str(row.get("mode") or "contains"))))
    return terms


def subject_of_terms(terms: list[tuple[str, str, str]]) -> str:
    return "、".join(f"{field_label(field)}「{value}」" for field, value, _mode in terms or [])


def subject_of_query(mode: str, text: str) -> str:
    return f"{mode_label(mode)}「{text}」"


def source_text(record: dict) -> str:
    """来源：sources[0] 的 issue / slug / file，非空值用「 / 」连接（旧壳口径）。"""
    sources = (record or {}).get("sources") or []
    if not sources or not isinstance(sources[0], dict):
        return ""
    first = sources[0]
    parts = [str(first.get(key) or "").strip() for key in ("issue", "slug", "file")]
    return " / ".join(part for part in parts if part)


def year_text(fields: dict) -> str:
    fields = fields or {}
    return str(fields.get("year") or "").strip() or str(fields.get("date") or "")[:4]


def hit_view(hit: dict, index: int = -1) -> dict:
    """一条命中 → 表格/详情要用的纯数据（回退顺序与旧壳一致）。"""
    record = hit.get("record") or {}
    fields = record.get("fields") or {}
    key = str(record.get("key") or hit.get("key") or "")
    title = str(fields.get("title") or fields.get("booktitle") or "（无题名）")
    author = str(fields.get("author") or fields.get("editor") or "（无作者）")
    verified = bool(hit.get("verified", record.get("verified")))
    status = "已核实" if verified else "未核实"
    confidence = hit.get("confidence")
    if confidence == "high":
        status += "（高置信）"
    elif confidence == "low":
        status += "（低置信）"
    return {
        "index": index,
        "key": key,
        "title": title,
        "author": author,
        "year": year_text(fields),
        "status": status,
        "verified": verified,
        "source": source_text(record),
        "bibtex": str(hit.get("bibtex") or ""),
        "why": why_lines(hit.get("matched") or []),
        "record": record,
    }


def why_lines(matched: list[dict]) -> list[str]:
    """why-hit：`[field] mode · 0.912 · snippet`（score 三位小数，换行压成空格）。"""
    lines: list[str] = []
    for item in matched or []:
        if not isinstance(item, dict):
            continue
        snippet = " ".join(str(item.get("snippet") or "").split())
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        lines.append(f"[{item.get('field') or ''}] {item.get('mode') or ''} · "
                     f"{score:.3f} · {snippet}")
    return lines


def basic_summary(count: int, elapsed: float) -> str:
    return f"命中 {count} 条（用时 {fmt_seconds(elapsed)}）。" if count else "没有命中。"


def basic_miss(subject: str, elapsed: float) -> str:
    return f"没有命中：{subject}（用时 {fmt_seconds(elapsed)}）"


def advanced_summary(count: int, total_matched: int, truncated: bool, elapsed: float) -> str:
    parts = [f"命中 {count} 条"]
    if total_matched and total_matched > count:
        parts.append(f"共 {total_matched} 条匹配")
    if truncated:
        parts.append("已截断")
    return "，".join(parts) + f"（用时 {fmt_seconds(elapsed)}）。"


def advanced_miss(subject: str, elapsed: float) -> str:
    return f"高级检索没有命中：{subject}（用时 {fmt_seconds(elapsed)}）"


def output_tail(result: dict, count: int = 2) -> str:
    """引擎输出末 N 行（旧壳 E.output_tail 的同义实现）。"""
    lines = [str(line).strip() for line in (result or {}).get("lines") or [] if str(line).strip()]
    if lines:
        return " ".join(lines[-max(1, count):])
    return str((result or {}).get("error") or "引擎没有返回说明")


def detail_title(view: dict | None) -> str:
    if not view:
        return "（未选中）"
    return f"已选中：{view['key']}（{'已核实' if view['verified'] else '未核实'}）"


def copy_done_message(key: str) -> str:
    return f"已复制 BibTeX（{key}）到剪贴板。"


NO_BIBTEX_TEXT = "这一条没有 BibTeX 文本（引擎未返回）。"
BIBTEX_PLACEHOLDER = "（引擎未返回 BibTeX）"


def log_lines(view: dict) -> list[str]:
    """「在运行日志里显示」：key / 题名 / 作者 + 全部字段逐行（旧壳 log_hit）。"""
    record = view.get("record") or {}
    fields = record.get("fields") or {}
    lines = [f"key：{view.get('key') or ''}", f"题名：{view.get('title') or ''}",
             f"作者：{view.get('author') or ''}", f"年份：{view.get('year') or ''}"]
    for name in sorted(fields):
        value = fields.get(name)
        if value in (None, "", [], {}):
            continue
        lines.append(f"{name}：{value}")
    return lines


# ── 视图 ────────────────────────────────────────────────────────────────────
class BibQueryView:
    """查库视图；状态在视图对象上，`build()` 只按状态重建控件。"""

    def __init__(self, page) -> None:
        self.page = page  # pages.BibPage
        self.tab_index = 0
        self.mode = "key"
        self.basic_text = ""
        self.rows = default_rows()
        self.sort = "relevance"
        self.limit_text = "20"
        self.strict = False
        self.hits: list[dict] = []
        self.selected = -1
        self.payload: dict = {}
        self.elapsed = 0.0
        self.rows_text = DEFAULT_ROWS_TEXT
        self.notices: list[tuple[str, str]] = []
        self.busy = False
        self.log_lines: list[str] = []

    # -- 小工具 ---------------------------------------------------------
    def _notes(self) -> str:
        return str(getattr(self.page.app, "notes_repo", "") or "")

    def _db(self) -> str:
        stats = self.page.stats_data or {}
        return str(stats.get("db") or "")

    def _refresh(self) -> None:
        self.page._render()

    def _notice(self, tone: str, message: str) -> None:
        self.notices.append((tone, message))
        self.notices = self.notices[-4:]

    def _set_notes(self, *notes: tuple[str, str]) -> None:
        self.notices = list(notes)[-4:]

    def _run(self, task) -> None:
        """调度协程对象（调用点写 `self._run(self._run_basic())`）。

        flet 1.0.5 的 `Page.run_task` 只收 coroutine function（GUI-04b），所以把协程
        对象包成 async 函数再交给它；签名与行为不变，调用点无需改动。
        """
        async def _await_task() -> None:
            await task

        self.page.page.run_task(_await_task)

    def current(self) -> dict | None:
        if 0 <= self.selected < len(self.hits):
            return hit_view(self.hits[self.selected], self.selected)
        return None

    # -- 构建 -----------------------------------------------------------
    def build(self) -> ft.Control:
        t = self.page.t
        panel = self._basic_panel() if self.tab_index == 0 else self._advanced_panel()
        intro = C.text("按 key（也认别名）、DOI 或题名（模糊匹配）查引用库；高级检索是多条件 AND。"
                       "这一页只读：查询不会改库，也不会调检索 / AI。", t, "caption",
                       t.palette.text_faint)
        controls: list = [intro, self._tab_bar(), panel]
        controls += [C.notice_bar(msg, t, tone) for tone, msg in self.notices]
        controls += [C.text(self.rows_text, t, "small", t.palette.text_muted), self._table()]
        controls += [C.divider(t), self._detail_panel()]
        if self.log_lines:
            controls.append(self._log_console())
        return ft.Column(spacing=t.sp("1"), controls=controls)

    def _tab_bar(self) -> ft.Control:
        t = self.page.t
        tabs = []
        for index, label in enumerate(("基本检索", "高级检索")):
            active = index == self.tab_index
            tabs.append(ft.Container(
                on_click=lambda e, i=index: self._switch_tab(i),
                padding=C._pad(t, 14, 6, 14, 6),
                border=ft.Border(bottom=C._side(t, t.palette.accent if active else t.palette.border,
                                               2 if active else 1)),
                content=C.text(label, t, role="tab-selected" if active else "small",
                               color=t.palette.accent if active else t.palette.text_muted),
            ))
        return ft.Row(spacing=t.sp("1"), controls=tabs)

    def _switch_tab(self, index: int) -> None:
        self.tab_index = index
        self.page.mark("bib_query_tab", tab="basic" if index == 0 else "advanced")
        self._refresh()

    def _basic_panel(self) -> ft.Control:
        t = self.page.t
        mode_box = C.dropdown(
            t, value=self.mode,
            options=[(key, mode_label(key)) for key in EB.QUERY_MODES],
            on_select=self._on_mode,
        )
        field = ft.TextField(
            value=self.basic_text,
            dense=True,
            expand=True,
            text_size=t.font("body"),
            hint_text=QUERY_HINTS[self.mode],
            border_color=t.palette.border,
            focused_border_color=t.palette.accent,
            content_padding=C._pad(t, 10, 8, 10, 8),
            on_change=self._on_basic_change,
            on_submit=lambda e: self._run(self._run_basic()),
        )
        bar = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                     controls=[mode_box, field,
                               C.primary_button("查询", lambda e: self._run(self._run_basic()), t,
                                                disabled=self.busy),
                               C.text("正在查询…" if self.busy else "", t, "small",
                                      t.palette.text_faint)])
        self.page.query_field = field  # main.py 的 P2_BIB_QUERY_UI 自检钩子沿用旧入口
        hint = C.text(f"例：{QUERY_HINTS[self.mode]}", t, "caption", t.palette.text_faint)
        return ft.Column(spacing=t.sp("half"), controls=[bar, hint])

    def _advanced_panel(self) -> ft.Control:
        t = self.page.t
        row_controls = [self._adv_row(index, row) for index, row in enumerate(self.rows)]
        add = C.secondary_button("+ 加一行", lambda e: self._add_row(), t)
        reset = C.text_button("重置", lambda e: self._reset_advanced(), t)
        sort_box = C.dropdown(
            t, value=self.sort,
            options=[(key, sort_label(key)) for key in EB.QUERY_SORTS],
            on_select=self._on_sort,
        )
        limit = ft.TextField(
            value=self.limit_text, width=t.px(64), dense=True, text_size=t.font("body"),
            text_align=ft.TextAlign.CENTER, border_color=t.palette.border,
            focused_border_color=t.palette.accent,
            content_padding=C._pad(t, 6, 8, 6, 8), on_change=self._on_limit_change,
            on_submit=lambda e: self._run(self._run_advanced()),
        )
        strict = ft.Checkbox(label="严格（模糊门槛 0.90）", value=self.strict,
                             on_change=self._on_strict)
        controls = ft.Row(spacing=t.sp("1"), wrap=True,
                          vertical_alignment=ft.CrossAxisAlignment.CENTER,
                          controls=[C.text("排序", t, "small", t.palette.text_muted), sort_box,
                                    C.text("最多", t, "small", t.palette.text_muted), limit,
                                    strict,
                                    C.primary_button("查询", lambda e: self._run(self._run_advanced()),
                                                     t, disabled=self.busy),
                                    C.text("正在查询…" if self.busy else "", t, "small",
                                           t.palette.text_faint)])
        return ft.Column(spacing=t.sp("half"), controls=[
            *row_controls,
            ft.Row(spacing=t.sp("1"), controls=[add, reset]),
            controls,
            C.text(ADVANCED_HINT, t, "caption", t.palette.text_faint),
        ])

    def _adv_row(self, index: int, row: dict) -> ft.Control:
        t = self.page.t
        field = str(row.get("field") or "author")
        field_box = C.dropdown(
            t, value=field,
            options=[(key, field_label(key)) for key in EB.QUERY_FIELDS],
            on_select=lambda e, i=index: self._on_field(i, e),
        )
        mode_box = C.dropdown(
            t, value=normalize_mode(field, str(row.get("mode") or "contains")),
            options=[(key, match_label(key)) for key in mode_options(field)],
            on_select=lambda e, i=index: self._on_match_mode(i, e),
        )
        value = ft.TextField(
            value=str(row.get("value") or ""), width=t.px(360), dense=True,
            text_size=t.font("body"), hint_text="值（如 Smith / 1975 / 10.1007/…）",
            border_color=t.palette.border, focused_border_color=t.palette.accent,
            content_padding=C._pad(t, 10, 8, 10, 8),
            on_change=lambda e, i=index: self._on_value(i, e),
            on_submit=lambda e: self._run(self._run_advanced()),
        )
        delete = C.secondary_button("删除", lambda e, i=index: self._remove_row(i), t,
                                    disabled=len(self.rows) <= 1)
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[field_box, mode_box, value, delete])

    def _table(self) -> ft.Control:
        t = self.page.t
        header = ft.Container(
            bgcolor=t.palette.surface_2,
            padding=C._pad(t, 8, 6, 8, 6),
            content=ft.Row(spacing=0, controls=[
                ft.Container(width=t.px(width), content=C.text(name, t, "caption",
                                                              t.palette.text_muted, weight="medium"))
                for name, width in TABLE_COLUMNS
            ]),
        )
        rows = [self._table_row(index, hit_view(hit, index))
                for index, hit in enumerate(self.hits)]
        if not rows:
            rows = [C.text("（结果会显示在这里）" if not self.payload else "（没有可显示的记录）",
                           t, "small", t.palette.text_faint)]
        total = sum(width for _name, width in TABLE_COLUMNS)
        # 旧壳 Treeview 高 8 行：超过 8 条时固定高度、内部纵向滚动，最多 200 条也不会把页面撑长。
        height = t.px(288) if len(self.hits) > 8 else None
        body = ft.Column(spacing=0, controls=rows,
                         scroll=ft.ScrollMode.AUTO if height else None)
        table = ft.Column(spacing=0, width=t.px(total),
                          controls=[header, ft.Container(height=height, content=body)])
        return ft.Row(scroll=ft.ScrollMode.AUTO, controls=[table])

    def _table_row(self, index: int, view: dict) -> ft.Control:
        t = self.page.t
        selected = index == self.selected
        cells = [
            C.text(view["title"], t, "small", max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            C.text(view["author"], t, "small", t.palette.text_muted, max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS),
            C.text(view["year"], t, "small", t.palette.text_muted),
            C.text(view["key"], t, "small", t.palette.text_muted, max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS),
            C.text(view["status"], t, "small", t.palette.text_muted),
            C.text(view["source"] or "（无来源记录）", t, "small", t.palette.text_muted,
                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        ]
        return ft.Container(
            on_click=lambda e, i=index: self._select(i),
            bgcolor=t.palette.accent_soft if selected else None,
            padding=C._pad(t, 8, 4, 8, 4),
            content=ft.Row(spacing=0, controls=[
                ft.Container(width=t.px(width), content=cell)
                for cell, (_name, width) in zip(cells, TABLE_COLUMNS)
            ]),
        )

    def _detail_panel(self) -> ft.Control:
        t = self.page.t
        view = self.current()
        copy_btn = C.primary_button(
            "复制 BibTeX", lambda e: self._run(self._copy_bibtex()), t,
            disabled=not view or not view["bibtex"])
        log_btn = C.secondary_button(
            "在运行日志里显示", lambda e: self._show_in_log(), t, disabled=not view)
        head = ft.Row(controls=[
            C.text(detail_title(view), t, role="section"),
            ft.Container(expand=True), log_btn, copy_btn,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
        controls: list = [head]
        if view:
            controls.append(C.text(f"来源：{view['source'] or '（无来源记录）'}", t, "small",
                                   t.palette.text_muted, selectable=True))
            if view["why"]:
                controls.append(C.text(f"命中原因（{len(view['why'])} 项条件都满足）：", t,
                                       "small", t.palette.text_muted))
                controls.append(C.text("\n".join(view["why"]), t, role="mono",
                                       color=t.palette.text_muted, selectable=True))
        bibtex = (view or {}).get("bibtex") or BIBTEX_PLACEHOLDER
        controls.append(ft.TextField(
            value=bibtex if view else "", read_only=True, multiline=True,
            min_lines=7, max_lines=7, text_size=t.font("body"),
            border_color=t.palette.border, content_padding=C._pad(t, 10, 8, 10, 8),
        ))
        return ft.Column(spacing=t.sp("half"), controls=controls)

    def _log_console(self) -> ft.Control:
        console = C.LogConsole(self.page.t, height=120, title="运行日志（本页）")
        for line in self.log_lines:
            console.add_line(line)
        return console.root

    # -- 交互 -----------------------------------------------------------
    def _on_basic_change(self, e) -> None:
        self.basic_text = e.control.value or ""

    def _on_mode(self, e) -> None:
        self.mode = e.control.value or "key"
        self.hits = []
        self.selected = -1
        self.payload = {}
        self.rows_text = DEFAULT_ROWS_TEXT
        self._set_notes()
        self.page.mark("bib_query_mode", mode=self.mode)
        self._refresh()

    def _on_field(self, index: int, e) -> None:
        if not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        row["field"] = e.control.value or "author"
        row["mode"] = normalize_mode(row["field"], str(row.get("mode") or "contains"))
        self._refresh()

    def _on_match_mode(self, index: int, e) -> None:
        if 0 <= index < len(self.rows):
            self.rows[index]["mode"] = e.control.value or "contains"

    def _on_value(self, index: int, e) -> None:
        if 0 <= index < len(self.rows):
            self.rows[index]["value"] = e.control.value or ""

    def _on_sort(self, e) -> None:
        self.sort = e.control.value or "relevance"

    def _on_limit_change(self, e) -> None:
        self.limit_text = e.control.value or ""

    def _on_strict(self, e) -> None:
        self.strict = bool(e.control.value)

    def _add_row(self) -> None:
        self.rows.append({"field": "author", "mode": "contains", "value": ""})
        self.page.mark("bib_adv_add_row", rows=len(self.rows))
        self._refresh()

    def _remove_row(self, index: int) -> None:
        if len(self.rows) <= 1:
            return
        if 0 <= index < len(self.rows):
            self.rows.pop(index)
        self.page.mark("bib_adv_remove_row", rows=len(self.rows))
        self._refresh()

    def _reset_advanced(self) -> None:
        self.rows = default_rows()
        self.sort = "relevance"
        self.limit_text = "20"
        self.strict = False
        self.hits = []
        self.selected = -1
        self.payload = {}
        self.rows_text = DEFAULT_ROWS_TEXT
        self._set_notes()
        self.page.mark("bib_adv_reset")
        self._refresh()

    def _select(self, index: int) -> None:
        self.selected = index
        self.page.mark("bib_query_select", index=index)
        self._refresh()

    def _show_in_log(self) -> None:
        view = self.current()
        if not view:
            return
        self.log_lines = log_lines(view)
        self.page.mark("bib_query_log", key=view["key"])
        self._refresh()

    async def _set_clipboard(self, text: str) -> bool:
        page = getattr(self.page, "page", None)
        try:
            clipboard = ft.Clipboard()
            page.services.append(clipboard)
            await clipboard.set(text)
            return True
        except Exception:  # noqa: BLE001  客户端剪贴板不可用时回落到平台命令
            pass
        try:
            self.page.app.copy_text(text)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _copy_bibtex(self) -> None:
        view = self.current()
        if not view:
            return
        if not view["bibtex"]:
            self._notice("warn", NO_BIBTEX_TEXT)
            self._refresh()
            return
        if await self._set_clipboard(view["bibtex"]):
            self.page.app.toast(copy_done_message(view["key"]))
        else:
            self._notice("error", "复制失败：剪贴板不可写。")
            self._refresh()

    # -- 查询 -----------------------------------------------------------
    async def _run_basic(self) -> None:
        if self.busy:
            return
        text = (self.basic_text or "").strip()
        if not text:
            self._set_notes(("warn", f"先填要查的内容（{mode_label(self.mode)}）。"))
            self._refresh()
            return
        if not self._notes():
            self._set_notes(("warn", NO_NOTES))
            self._refresh()
            return
        self.busy = True
        self._set_notes()
        self._refresh()
        started = time.perf_counter()
        try:
            call = EB.bibdb_query_basic_call(self.mode, text, db=self._db())
            result = await asyncio.to_thread(EB.read_json_command, call, notes=self._notes())
        except Exception as exc:  # noqa: BLE001  argv 校验 / 引擎缺失都按失败提示
            self.busy = False
            self._set_notes(("error", f"查库失败：{exc}"))
            self._refresh()
            return
        elapsed = time.perf_counter() - started
        self.busy = False
        payload = result.get("json") or {}
        hits = payload.get("hits") or []
        self.hits, self.payload, self.elapsed = hits, payload, elapsed
        self.selected = 0 if hits else -1
        self._set_notes()
        ok = bool(result.get("ok"))
        if not ok:
            self.rows_text = "没有命中。"
            self._notice("error", f"查库失败：{output_tail(result)}")
        elif not hits:
            self.rows_text = "没有命中。"
            self._notice("warn", basic_miss(subject_of_query(self.mode, text), elapsed))
        else:
            self.rows_text = basic_summary(len(hits), elapsed)
            if self.mode == "key" and payload.get("ambiguous"):
                self._notice("warn", "⚠ 有多条同 key/alias 的记录，需人工确认。")
        if payload.get("error"):
            self._notice("warn", f"（引擎说明：{payload['error']}）")
        self.page.mark("bib_query_basic", ok=ok, mode=self.mode, count=len(hits),
                       ambiguous=payload.get("ambiguous"))
        self._refresh()

    async def _run_advanced(self) -> None:
        if self.busy:
            return
        terms = adv_terms(self.rows)
        if not terms:
            self._set_notes(("warn", "高级检索至少填一行条件（值不能为空）。"))
            self._refresh()
            return
        if not self._notes():
            self._set_notes(("warn", NO_NOTES))
            self._refresh()
            return
        try:
            call = EB.bibdb_query_advanced_call(
                terms=terms, sort=self.sort, limit=self.limit_text, strict=self.strict,
                db=self._db())
        except ValueError as exc:
            message = str(exc)
            tone = "error" if message.startswith("limit ") else "warn"
            self._set_notes((tone, message))
            self._refresh()
            return
        self.busy = True
        self._set_notes()
        self._refresh()
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(EB.read_json_command, call, notes=self._notes())
        except Exception as exc:  # noqa: BLE001
            self.busy = False
            self._set_notes(("error", f"高级检索失败：{exc}"))
            self._refresh()
            return
        elapsed = time.perf_counter() - started
        self.busy = False
        payload = result.get("json") or {}
        hits = payload.get("hits") or []
        self.hits, self.payload, self.elapsed = hits, payload, elapsed
        self.selected = 0 if hits else -1
        self._set_notes()
        ok = bool(result.get("ok"))
        subject = subject_of_terms(terms)
        if not ok:
            self.rows_text = "没有命中。"
            self._notice("error", f"高级检索失败：{output_tail(result)}")
        elif not hits:
            self.rows_text = "没有命中。"
            self._notice("warn", advanced_miss(subject, elapsed))
        else:
            self.rows_text = advanced_summary(
                len(hits), int(payload.get("total_matched") or len(hits)),
                bool(payload.get("truncated")), elapsed)
        if payload.get("error"):
            self._notice("warn", f"（引擎说明：{payload['error']}）")
        self.page.mark("bib_query_advanced", ok=ok, sort=self.sort, count=len(hits),
                       total_matched=payload.get("total_matched"),
                       truncated=payload.get("truncated"), strict=self.strict)
        self._refresh()
