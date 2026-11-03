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
    def _design_width(self) -> float:
        """设计像素宽：窗口 CSS 宽 ÷ 缩放（断点判断不看物理像素）。"""
        try:
            width = float(self.page.page.width or 0)
        except Exception:  # noqa: BLE001
            width = 0.0
        if width <= 0:
            width = float(getattr(self.page.app, "window_w", 0) or 0)
        return width / max(self.page.t.k, 1e-6)

    def on_resize(self) -> None:
        """按设计宽切换列集 / 详情排布；只在跨阈值时重建，不随拖动抖。"""
        width = self._design_width()
        if abs(width - getattr(self, "_build_width", width)) >= 40:
            self._build_width = width
            self._refresh()

    def build(self) -> ft.Control:
        self._build_width = self._design_width()
        t = self.page.t
        panel = self._basic_panel() if self.tab_index == 0 else self._advanced_panel()
        controls: list = [panel]
        controls += [C.inline_note(msg, t, tone) for tone, msg in self.notices]
        controls += [self._meta_row(), self._results()]
        if self.log_lines:
            controls.append(self._log_console())
        return ft.Column(spacing=t.sp("1"), controls=controls, expand=True)

    def _tab_bar(self) -> ft.Control:
        """兼容旧入口（测试按源码断言 tab role）；新页用搜索行里的「高级检索」切换。"""
        t = self.page.t
        tabs = []
        for index, label in enumerate(("基本检索", "高级检索")):
            active = index == self.tab_index
            tabs.append(ft.Container(
                on_click=lambda e, i=index: self._switch_tab(i),
                padding=C._pad(t, 14, 6, 14, 6),
                border=ft.Border(bottom=C._side(t, t.buttons.tab_underline if active
                                                else t.palette.border, 2 if active else 1)),
                content=C.text(label, t, role="tab-selected" if active else "small",
                               color=t.palette.text if active else t.palette.text_muted),
            ))
        return ft.Row(spacing=t.sp("1"), controls=tabs)

    def _switch_tab(self, index: int) -> None:
        self.tab_index = index
        self.page.mark("bib_query_tab", tab="basic" if index == 0 else "advanced")
        self._refresh()

    def _search_input(self, value: str, *, hint: str, on_change, on_submit,
                      width: float | None = None, expand: bool = False,
                      align=ft.TextAlign.LEFT, bold: bool = False) -> ft.TextField:
        t = self.page.t
        return C.text_field(
            t, value=value, width=t.px(width) if width else None,
            expand=expand, text_align=align, hint_text=hint,
            on_change=on_change, on_submit=on_submit,
        )

    def _basic_panel(self) -> ft.Control:
        t = self.page.t
        mode_box = C.select(
            t, value=self.mode,
            options=[(key, mode_label(key)) for key in EB.QUERY_MODES],
            on_select=self._on_mode, width=t.px(96),
        )
        field = C.text_field(
            t, value=self.basic_text, expand=True, hint_text=QUERY_HINTS[self.mode],
            on_change=self._on_basic_change,
            on_submit=lambda e: self._run(self._run_basic()),
        )
        toggle_label = "基本检索" if self.tab_index else "高级检索"
        bar = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                     controls=[mode_box, field,
                               C.primary_button("查询", lambda e: self._run(self._run_basic()), t,
                                                disabled=self.busy),
                               C.text_button(toggle_label,
                                             lambda e: self._switch_tab(1 - self.tab_index), t,
                                             height=t.control_h())])
        self.page.query_field = field  # main.py 的 P2_BIB_QUERY_UI 自检钩子沿用旧入口
        # 批 112 收尾（用户反馈）：示例只留在输入框 hint_text 一处，下方不再重复同一句。
        return bar

    def _advanced_panel(self) -> ft.Control:
        t = self.page.t
        row_controls = [self._adv_row(index, row) for index, row in enumerate(self.rows)]
        add = C.secondary_button("+ 加一行", lambda e: self._add_row(), t)
        reset = C.text_button("重置", lambda e: self._reset_advanced(), t,
                              height=t.control_h())
        sort_box = C.select(
            t, value=self.sort,
            options=[(key, sort_label(key)) for key in EB.QUERY_SORTS],
            on_select=self._on_sort, width=t.px(112),
        )
        limit = self._search_input(
            self.limit_text, hint="最多", width=64, align=ft.TextAlign.CENTER,
            on_change=self._on_limit_change,
            on_submit=lambda e: self._run(self._run_advanced()),
        )
        strict = ft.Checkbox(label="严格（模糊门槛 0.90）", value=self.strict,
                             on_change=self._on_strict)
        bar = ft.Row(spacing=t.sp("1"), wrap=True,
                     vertical_alignment=ft.CrossAxisAlignment.CENTER,
                     controls=[C.text("排序", t, "caption", t.palette.text_muted), sort_box,
                               C.text("最多", t, "caption", t.palette.text_muted), limit,
                               strict,
                               C.primary_button("查询", lambda e: self._run(self._run_advanced()),
                                                t, disabled=self.busy),
                               C.text("正在查询…" if self.busy else "", t, "caption",
                                      t.palette.text_faint)])
        return ft.Column(spacing=t.sp("half"), controls=[
            *row_controls,
            ft.Row(spacing=t.sp("1"), controls=[add, reset]),
            bar,
            C.text(ADVANCED_HINT, t, "caption", t.palette.text_faint),
        ])

    def _adv_row(self, index: int, row: dict) -> ft.Control:
        t = self.page.t
        field = str(row.get("field") or "author")
        field_box = C.select(
            t, value=field,
            options=[(key, field_label(key)) for key in EB.QUERY_FIELDS],
            on_select=lambda e, i=index: self._on_field(i, e), width=t.px(96),
        )
        mode_box = C.select(
            t, value=normalize_mode(field, str(row.get("mode") or "contains")),
            options=[(key, match_label(key)) for key in mode_options(field)],
            on_select=lambda e, i=index: self._on_match_mode(i, e), width=t.px(96),
        )
        value = self._search_input(
            str(row.get("value") or ""), hint="值（如 Smith / 1975 / 10.1007/…）",
            expand=True,
            on_change=lambda e, i=index: self._on_value(i, e),
            on_submit=lambda e: self._run(self._run_advanced()),
        )
        delete = C.secondary_button("删除", lambda e, i=index: self._remove_row(i), t,
                                    disabled=len(self.rows) <= 1)
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[field_box, mode_box, value, delete])

    def _columns(self) -> list[tuple[str, float, bool]]:
        """断点列集：窄窗先收「来源/状态/作者」，不做横向滚动。"""
        width = self._design_width()
        columns: list[tuple[str, float, bool]] = [
            ("题名", 3.2, False), ("作者", 1.8, False),
            ("年份", 0.8, True), ("key", 1.9, True),
        ]
        if width >= 900:
            columns.append(("状态", 0.9, False))
        if width >= 980:
            columns.append(("来源", 1.6, False))
        return columns

    def _meta_text(self) -> str:
        if not self.hits:
            return ""  # 空态提示只在输入框 hint_text 一处，行内不再重复
        total = self.payload.get("total_matched") or len(self.hits)
        try:
            total_n = int(total)
        except (TypeError, ValueError):
            total_n = len(self.hits)
        seconds = "很快" if (self.elapsed or 0) < 0.05 else f"{self.elapsed:.2f} s"
        return f"1–{len(self.hits)} / {total_n:,} · {seconds}"

    def _meta_row(self) -> ft.Control:
        t = self.page.t
        right: list = []
        if self.hits:
            right.append(C.text(f"按{sort_label(self.sort)}排序", t, "caption",
                                t.palette.text_faint))
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[C.text(self._meta_text(), t, "caption",
                                       t.palette.text_muted),
                                ft.Container(expand=True), *right])

    def _table(self) -> ft.Control:
        t = self.page.t
        columns = self._columns()
        labels = [name for name, _w, _mono in columns]
        weights = [w for _name, w, _mono in columns]
        mono = tuple(i for i, (_name, _w, is_mono) in enumerate(columns) if is_mono)
        header = C.table_header(labels, t, weights)
        rows = [self._table_row(index, hit_view(hit, index), columns, weights, mono)
                for index, hit in enumerate(self.hits)]
        empty = C.empty_line("输入条件后点「查询」" if not self.payload
                             else "没有可显示的记录", t)
        return C.table_panel(header, rows, t, empty=empty)

    def _table_row(self, index: int, view: dict, columns, weights, mono) -> ft.Control:
        t = self.page.t
        selected = index == self.selected
        values = {
            "题名": view["title"] or "（无题名）",
            "作者": view["author"] or "—",
            "年份": view["year"] or "—",
            "key": view["key"] or "—",
            "状态": view["status"] or "—",
            "来源": view["source"] or "（无来源记录）",
        }
        cells = [values.get(name, "—") for name in (c[0] for c in columns)]
        return C.table_row(cells, t, weights=weights, selected=selected,
                           on_click=lambda e, i=index: self._select(i),
                           height=32, mono=mono)

    def _results(self) -> ft.Control:
        t = self.page.t
        table = ft.Container(content=self._table(), expand=True)
        if not self.hits:
            return table  # 空态只留表头 + 一句动作提示，详情区等有结果再出现
        detail = self._detail_panel(width=360 if self._design_width() >= 1120 else None)
        if self._design_width() >= 1120:
            return ft.Row(spacing=t.sp("2"), expand=True,
                          controls=[table, ft.Container(width=t.px(360), content=detail)])
        return ft.Column(spacing=t.sp("2"), expand=True, controls=[table, detail])

    def _detail_panel(self, width: float | None = None) -> ft.Control:
        t = self.page.t
        view = self.current()
        count = len(self.hits)
        lede = f"已选 {self.selected + 1}/{count}" if view else (
            f"0/{count} 条结果" if count else "尚未查询")
        copy_btn = C.primary_button(
            "复制 BibTeX", lambda e: self._run(self._copy_bibtex()), t,
            disabled=not view or not view["bibtex"])
        log_btn = C.text_button("在日志中查看", lambda e: self._show_in_log(), t)
        log_btn.disabled = not view
        head = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                      controls=[C.section_label(lede, t), ft.Container(expand=True),
                                log_btn, copy_btn])
        controls: list = [head]
        if view:
            controls.append(C.text(f"来源：{view['source'] or '（无来源记录）'}", t,
                                   "caption", t.palette.text_muted, selectable=True))
            if view["why"]:
                controls.append(C.text("命中原因：" + "；".join(view["why"]), t, "caption",
                                       t.palette.text_faint, max_lines=2,
                                       overflow=ft.TextOverflow.ELLIPSIS,
                                       selectable=True))
        bibtex = (view or {}).get("bibtex") or BIBTEX_PLACEHOLDER
        box = C.text_field(
            t, value=bibtex if view else "", read_only=True, multiline=True,
            min_lines=6, max_lines=6, mono=True,
        )
        controls.append(box)
        panel = ft.Column(spacing=t.sp("1"), controls=controls)
        return ft.Container(width=t.px(width) if width else None, content=panel)

    def _log_console(self) -> ft.Control:
        console = C.LogConsole(self.page.t, height=120, title="运行日志（本页）",
                               toast=self.page.app.toast, mark=self.page.mark)
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
