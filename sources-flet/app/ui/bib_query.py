"""引用库 · 查库（批 115 夜间重设计 BPAGES-A，VIEW_KEY="query"；P06 加分页）。

只读检索页：key / DOI / 题名三种方式，首查 100 条；列表型查询（题名 / 高级检索）
按引擎 ``page`` 六字段（offset/limit/total_matched/has_more/next_offset/db_sha256）
续页，单次 ``--limit`` 仍是 1–200，绝不靠放宽 limit 伪装「载入更多」。命中总数一律
用引擎的 ``total_matched``（不是 ``len(hits)``）；库在分页之间变化时拒绝合并并提示
按当前库重查。右侧 360 详情在 <1100 时折成表格下方的展开面板（不横向滚动）。

所有引擎调用走 ``EB.bibdb_query_basic_call`` / ``EB.bibdb_query_advanced_call``，
通过 ``EB.read_json_command`` 执行；测试 monkeypatch 该函数或直接检查 argv。
"""

from __future__ import annotations

import asyncio
import functools
import time

import flet as ft

from services import engine_bridge as EB
from ui import bib_shell as S
from ui import bib_table as BT
from ui import components as C
from ui.tokens import Tokens

VIEW_KEY = "query"
TITLE = "查库"
ROLE = "readonly"

MODES = (("key", "key"), ("doi", "DOI"), ("title", "题名"))
MODE_LABELS = dict(MODES)
HINTS = {
    "key": "如 Fine1975",
    "doi": "如 10.1007/s10992-020-09558-5",
    "title": "如 逻辑研究（可模糊）",
}
EMPTY_INPUT = "输入要查的内容：key、DOI 或题名。"
NO_RESULT = "没有匹配的记录"
NO_RESULT_HINT = "试试放宽年份，或只按题名检索。"
QV_NO_RESULT_HINT = NO_RESULT_HINT
ENGINE_CAP = 200
PAGE_SIZE = 100
DB_CHANGED_MSG = "引用库已变化，请刷新后重新查询（未合并新页）"
PAGE_MISMATCH_MSG = "分页位置不一致，已丢弃本页；请刷新后重新查询（未合并新页）"
DETAIL_W = 360


def _clock(seconds: float) -> str:
    return f"{seconds:.2f} s"


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return "引擎没有返回说明"


def _hit_fields(hit: dict) -> dict:
    record = hit.get("record") or {}
    return record.get("fields") or {}


def _source_of(hit: dict) -> str:
    sources = (hit.get("record") or {}).get("sources") or []
    if not sources:
        return "—"
    first = sources[0] or {}
    issue, slug = str(first.get("issue") or ""), str(first.get("slug") or "")
    return " · ".join(x for x in (issue, slug) if x) or "—"


def hit_row(hit: dict) -> dict:
    """把引擎 hit 映射成表格行；字段缺失回落到 record.entry_type/key。"""
    fields = _hit_fields(hit)
    record = hit.get("record") or {}
    year = fields.get("year") or fields.get("date") or ""
    if year:
        year = str(year)[:4]
    return {
        "title": hit.get("title") or fields.get("title") or "（无题名）",
        "author": hit.get("author") or fields.get("author") or "—",
        "year": year or "—",
        "key": hit.get("key") or record.get("key") or "",
        "status": "已核实" if (hit.get("verified") or record.get("verified")) else "未核实",
        "source": _source_of(hit),
        "_hit": hit,
    }


class QueryView:
    def __init__(self, page) -> None:
        self.page = page
        self.mode = "key"
        self.text = ""
        self.rows: list[dict] = []
        self.hits: list[dict] = []
        self.total_matched: int | None = None
        self.truncated = False
        self.count = 0
        self.limit = 100
        self.loaded = False
        self.running = False
        self.cancel_requested = False
        self.error = ""
        self.input_error = ""
        self.success = ""
        self.cancelled = False
        self.elapsed = 0.0
        self.selected_key: str | None = None
        self.sort_key = "relevance"
        self.sort_desc = False
        self._task_running = False
        self._build_width = 0.0
        # ── P06 分页与复用状态（CONTRACT §5）：page 字段只消费、不重算 ──
        self.query_advanced = False
        self.adv_values: dict[str, str] = {}
        self.adv_strict = False
        self._gen = 0
        self._loaded = 0
        self._total: int | None = None
        self._has_more = False
        self._db_sha = ""
        self._cond: dict | None = None
        self._items: list[dict] = []
        self._page_error = ""

    # ── 基础 ────────────────────────────────────────────────────────────
    @property
    def t(self) -> Tokens:
        return self.page.t

    def _notes(self) -> str:
        return str(getattr(getattr(self.page, "app", None), "notes_repo", "") or "")

    def _db(self) -> str:
        return str((getattr(self.page, "stats_data", None) or {}).get("db") or "")

    def _store(self):
        return getattr(getattr(self.page, "app", None), "store", None)

    def _set_store(self, key: str, value) -> None:
        store = self._store()
        if store is not None:
            try:
                store.set(key, value)
            except Exception:  # noqa: BLE001
                pass

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

    def _schedule(self, runner=None) -> None:
        if self._task_running:
            return
        self._task_running = True
        run = getattr(self.page.page, "run_task", None)
        fn = runner or self._run
        if callable(run):
            run(fn)
        else:
            asyncio.run(fn())

    def _schedule_async(self, fn, *args) -> None:
        """把「只读取数」协程交给页面事件循环（flet 只收 coroutine function）。"""
        if self._task_running:
            return
        self._task_running = True
        run = getattr(self.page.page, "run_task", None)
        handler = functools.partial(fn, *args)
        if callable(run):
            run(handler)
        else:
            asyncio.run(handler())

    # ── 生命周期 ────────────────────────────────────────────────────────
    def activate(self) -> None:
        # 查库不自动跑查询（没有条件时不猜），首屏保留「还没有查询」。
        self._set_store("bib_query_mode", self.mode)

    def refresh_running(self) -> None:
        return None

    def primary_action(self):
        return self._query_button()

    # ── 条件 ────────────────────────────────────────────────────────────
    def _mode_of(self, raw) -> str:
        key = str(getattr(raw, "data", raw) or "").strip().lower()
        return key if key in dict(MODES) else "key"

    def _on_mode(self, e) -> None:
        mode = self._mode_of(e)
        if mode == self.mode and not self.query_advanced:
            self._set_store("bib_query_mode", self.mode)
            self._refresh()
            return
        self.mode = mode
        self.query_advanced = False
        had = self.loaded and bool(str(self.text or "").strip())
        self._set_store("bib_query_mode", self.mode)
        # 条件变化（契约 §5）：重置分页状态与选择，再走首查。
        self._reset_paging()
        if had:
            self.error = ""
            self.cancelled = False
            self._schedule()
        else:
            self._refresh()

    def _on_text(self, e) -> None:
        control = getattr(e, "control", e)
        value = str(getattr(control, "value", None)
                    if hasattr(control, "value") else getattr(e, "data", "") or "")
        if value == self.text and not self.input_error:
            return
        had_error = bool(self.input_error)
        self.text = value
        self._set_store("bib_query_text", self.text)
        self.input_error = ""
        self.query_advanced = False
        # 条件变化（契约 §5）：作废旧分页/在途结果与选择。输入中不整页重建，
        # 免得重建 TextField 丢焦点；真正的首查在回车/「查询」时发起。
        self._reset_paging()
        if had_error:
            self._refresh()

    def _on_submit(self, e=None) -> None:
        self.query()

    def query(self) -> None:
        """校验后启动查询；空输入只报错，不禁用按钮。"""
        text = str(self.text or "").strip()
        if not text:
            self.input_error = EMPTY_INPUT
            self.error = ""
            self._refresh()
            self._focus_search()
            return
        self.input_error = ""
        self.error = ""
        self.cancelled = False
        self.query_advanced = False
        # 新首查（契约 §5）：先清空旧分页/选择，再显式 offset=0 拉第一页。
        self._reset_paging()
        self._schedule()

    def _focus_search(self) -> None:
        field = getattr(self, "_field", None)
        if field is not None:
            try:
                run = getattr(getattr(self.page, "page", None), "run_task", None)
                if callable(run):
                    run(field.focus)
                else:
                    field.focus()
            except Exception:  # noqa: BLE001
                pass

    def cancel(self) -> None:
        if not self.running:
            return
        self.cancel_requested = True
        # 取消 = 让在途结果世代过期（契约 §5）：迟到结果丢弃，可重跑不串结果。
        self._gen += 1
        self.cancelled = True
        self._refresh()

    def _cancel_clicked(self, e=None) -> None:
        self.cancel()

    # ── 分页状态（CONTRACT §5：page 字段只消费不重算）───────────────────
    def _is_list_query(self) -> bool:
        """只有列表型查询（题名 / 高级检索）返回 page，可翻页；key/doi 是单条定位。"""
        return bool(getattr(self, "query_advanced", False)) or self.mode == "title"

    def _has_condition(self) -> bool:
        if getattr(self, "query_advanced", False):
            return any(str(value or "").strip()
                       for value in (getattr(self, "adv_values", {}) or {}).values())
        return bool(str(self.text or "").strip())

    def _cond_snapshot(self) -> dict:
        """首查条件快照；载入更多必须与它逐字段一致（同条件才允许续页）。"""
        return {
            "advanced": bool(getattr(self, "query_advanced", False)),
            "mode": self.mode,
            "text": str(self.text or "").strip(),
            "sort": self.sort_key,
            "strict": bool(getattr(self, "adv_strict", False)),
            "terms": {str(field): str(value or "").strip()
                      for field, value in (getattr(self, "adv_values", {}) or {}).items()},
        }

    def _reset_paging(self) -> None:
        """条件变化 / 新首查：清已载入、分页元数据与选择；在途结果靠世代过期丢弃。"""
        self._gen += 1
        if self.running:
            self.cancelled = True
        self._items = []
        self.hits = self._items
        self.rows = []
        self._loaded = 0
        self._total = None
        self._has_more = False
        self._db_sha = ""
        self._cond = None
        self._page_error = ""
        self.count = 0
        self.total_matched = None
        self.truncated = False
        self.loaded = False
        self.selected_key = None

    def _restart_query(self) -> None:
        """库变化后的刷新入口：按当前条件/当前库重新走一次首查（offset=0）。"""
        if not self._has_condition():
            return
        self.error = ""
        self.cancelled = False
        self._reset_paging()
        self._schedule()

    # ── 执行 ────────────────────────────────────────────────────────────
    def _call(self, offset: int | None = None):
        text = str(self.text or "").strip()
        if getattr(self, "query_advanced", False):
            terms = []
            for field, mode in (("author", "contains"), ("title", "contains"),
                                ("year", "exact"), ("doi", "exact")):
                value = str(self.adv_values.get(field) or "").strip()
                if value:
                    terms.append((field, value, mode))
            return EB.bibdb_query_advanced_call(
                terms=terms, sort=self.sort_key, limit=self.limit, offset=offset,
                strict=bool(self.adv_strict), bibtex=True, db=self._db())
        return EB.bibdb_query_basic_call(
            self.mode, text, limit=self.limit, offset=offset, bibtex=True,
            db=self._db())

    def _adopt_first(self, data: dict) -> None:
        """首查采纳：列表型用 page 六字段；无 page（旧引擎 / key/doi）退回旧字段。"""
        hits = list(data.get("hits") or [])
        page = data.get("page") if isinstance(data.get("page"), dict) else None
        self._items = list(hits)
        self.hits = self._items
        self.rows = [hit_row(hit) for hit in hits]
        self._loaded = len(hits)
        self.count = len(hits)
        self.selected_key = None
        self.loaded = True
        if page is not None:
            self._total = int(page.get("total_matched") or 0)
            self._db_sha = str(page.get("db_sha256") or "")
            self._has_more = bool(page.get("has_more"))
        elif self._is_list_query():
            # 旧引擎/旧假数据没有 page：只能如实显示，不能假装可续页。
            self._total = int(data.get("total_matched") or 0)
            self._db_sha = ""
            self._has_more = False
        else:
            # key/doi：单条定位，total = 命中条数，不翻页。
            self._total = int(data.get("total_matched") or len(hits))
            self._db_sha = ""
            self._has_more = False
        self.total_matched = self._total
        self.truncated = self._has_more
        self.success = (f"找到 {self.total_matched:,} 条"
                        if self.total_matched else "0 条 · 没有匹配")
        self._mark("bib_query_run", mode=self.mode, limit=self.limit,
                   count=self.count, total=self.total_matched)

    def _adopt_more(self, data: dict) -> None:
        """载入更多采纳：sha / offset 任一不符即拒绝合并（契约 §5）。"""
        page = data.get("page") if isinstance(data.get("page"), dict) else None
        if page is None:
            self._page_error = DB_CHANGED_MSG
            self._has_more = False
            self.truncated = False
            return
        sha = str(page.get("db_sha256") or "")
        try:
            offset = int(page.get("offset"))
        except (TypeError, ValueError):
            offset = -1
        if sha != self._db_sha:
            # 库在分页之间变了：不合并、保留已载入、给刷新入口。
            self._page_error = DB_CHANGED_MSG
            self._has_more = False
            self.truncated = False
            return
        if offset != self._loaded:
            # 位置错位：绝不拼接，提示按当前库重查。
            self._page_error = PAGE_MISMATCH_MSG
            self._has_more = False
            self.truncated = False
            return
        hits = list(data.get("hits") or [])
        self._items = self._items + hits
        self.hits = self._items
        self.rows = [hit_row(hit) for hit in self._items]
        self._loaded = offset + len(hits)
        self.count = self._loaded
        if page.get("total_matched") is not None:
            self._total = int(page.get("total_matched") or 0)
        self.total_matched = self._total if self._total is not None else self._loaded
        self._has_more = bool(page.get("has_more")) and bool(hits)
        self.truncated = self._has_more
        self.success = (f"找到 {self.total_matched:,} 条"
                        if self.total_matched else "0 条 · 没有匹配")
        self._mark("bib_query_more", mode=self.mode, limit=self.limit,
                   count=self.count, total=self.total_matched)

    async def _run(self, *, more: bool = False) -> None:
        if self.running:
            return
        if more:
            gen = self._gen
        else:
            # 首查：新世代 + 条件快照（契约 §5）。
            gen = self._gen + 1
            self._gen = gen
            self._cond = self._cond_snapshot()
        self.running = True
        self.cancel_requested = False
        self.success = ""
        self.error = ""
        if not more:
            self.cancelled = False
            self._page_error = ""
        started = time.monotonic()
        try:
            offset = self._loaded if more else (0 if self._is_list_query() else None)
            call = self._call(offset=offset)
        except Exception as exc:  # noqa: BLE001 —— 参数问题按失败展示
            self.running = False
            self._task_running = False
            self.error = f"查询失败：{_first_line(str(exc))}"
            self._refresh()
            return
        self._refresh()
        try:
            result = await asyncio.to_thread(
                EB.read_json_command, call, timeout=30.0, notes=self._notes())
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "json": {}, "error": f"{type(exc).__name__}: {exc}"}
        self.elapsed = time.monotonic() - started
        self.running = False
        self._task_running = False
        if gen != self._gen:
            # 迟到结果 / 取消 / 条件变化：世代不符，一律丢弃，不串结果。
            self._refresh()
            return
        if self.cancel_requested:
            self.cancelled = True
            self.success = ""
            self._refresh()
            return
        if not result.get("ok"):
            self.error = f"查询失败：{_first_line(result.get('error') or '引擎返回失败')}"
            self._refresh()
            return
        data = result.get("json") or {}
        if more:
            self._adopt_more(data)
        else:
            self._adopt_first(data)
        self._refresh()

    async def _run_more(self) -> None:
        await self._run(more=True)

    def load_more(self) -> None:
        """载入更多：同一条件快照 + offset=_loaded，固定 PAGE_SIZE，绝不放宽 limit。"""
        if self.running or not self._has_more:
            return
        if self.limit >= ENGINE_CAP:
            return
        if not self._is_list_query():
            return
        if not self._db_sha:
            # 没有 page 元数据（旧引擎/旧数据）无法安全续页。
            return
        if self._cond is None or self._cond != self._cond_snapshot():
            # 条件已变但还没重查：旧页不再属于当前条件。
            return
        self._schedule(self._run_more)

    # ── 排序 ────────────────────────────────────────────────────────────
    def _on_sort(self, key: str, desc: bool) -> None:
        mapping = {"title": ("relevance", False), "year": ("year", True),
                   "author": ("author", False)}
        if key not in mapping:
            return
        engine_sort, engine_desc = mapping[key]
        had = bool(self.loaded and self._has_condition())
        self.sort_key = engine_sort
        self.sort_desc = engine_desc
        # 排序是查询条件的一部分：先重置分页与选择，再按新排序走首查。
        self.error = ""
        self.cancelled = False
        self._reset_paging()
        if had:
            self._schedule()
        else:
            self._refresh()

    # ── 高级检索 ────────────────────────────────────────────────────────
    def open_advanced(self, e=None) -> None:
        t = self.t
        self.adv_values = dict(getattr(self, "adv_values", {}) or {})
        self.adv_strict = bool(getattr(self, "adv_strict", False))
        fields = []
        for key, label in (("author", "作者"), ("title", "题名"), ("year", "年份"),
                           ("doi", "DOI")):
            fields.append(C.text_field(
                t, value=self.adv_values.get(key, ""), hint_text=f"{label}（可空）",
                on_change=lambda e, key=key: self.adv_values.__setitem__(
                    key, getattr(e.control, "value", "")),
                width=420))

        def _close(e=None) -> None:
            try:
                self.page.page.pop_dialog()
            except Exception:  # noqa: BLE001
                pass

        def _submit(e=None) -> None:
            if not any(str(v or "").strip() for v in self.adv_values.values()):
                self.adv_error = "至少填一行检索条件。"
                return
            self.adv_strict = bool(self.adv_strict)
            self.query_advanced = True
            _close()
            self.error = ""
            self.cancelled = False
            self.limit = 100
            # 高级检索提交 = 条件变化（契约 §5）：清分页/选择，再首查。
            self._reset_paging()
            self._schedule()

        dialog = ft.AlertDialog(
            modal=True,
            title=C.text("高级检索", t, "h2"),
            content=ft.Container(width=t.px(520), content=ft.Column(
                spacing=t.sp("1"), tight=True, controls=[
                    C.text("空行忽略，条件取交集。", t, "caption", t.palette.text_muted),
                    *fields,
                    ft.Row(spacing=t.sp("1"), controls=[
                        C.text("排序", t, "caption", t.palette.text_muted),
                        C.select(t, value=self.sort_key,
                                 options=[("relevance", "相关度"), ("year", "年份"),
                                          ("author", "作者")],
                                 on_select=lambda e: setattr(self, "adv_sort", e.data),
                                 width=120),
                    ]),
                    ft.Row(spacing=t.sp("half"), controls=[
                        ft.Checkbox(value=bool(self.adv_strict),
                                    on_change=lambda e: setattr(
                                        self, "adv_strict", bool(e.control.value)),
                                    visual_density=ft.VisualDensity.COMPACT,
                                    splash_radius=0),
                        C.text("严格匹配", t, "caption", t.palette.text_muted),
                    ]),
                ])),
            actions=[
                C.text_button("取消", _close, t, height=28),
                C.primary_button("高级检索", _submit, t, height=28),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.adv_error = ""
        try:
            self.page.page.show_dialog(dialog)
        except Exception:  # noqa: BLE001
            self.error = "无法打开高级检索对话框。"

    # ── 控件的构建 ──────────────────────────────────────────────────────
    def _design_width(self) -> float:
        try:
            width = float(self.page.page.width or 0)
        except Exception:  # noqa: BLE001
            width = 0.0
        if width <= 0:
            width = float(getattr(getattr(self.page, "app", None), "window_w", 0) or 0)
        return width / max(self.t.k, 1e-6)

    def _query_button(self):
        label = "运行中" if self.running else ("重新查询" if self.loaded else "查询")
        btn = C.primary_button(label, lambda e: self.query(), self.t, height=28)
        btn.disabled = self.running
        btn._bib_query_btn = True
        return btn

    def _count_control(self):
        t = self.t
        if self.input_error:
            return C.text(self.input_error, t, "caption", t.palette.error, max_lines=1)
        if self.error:
            return C.text(self.error, t, "caption", t.palette.error, max_lines=1,
                          tooltip=self.error)
        if self.running:
            return ft.Row(spacing=t.sp("1"), controls=[
                C.text(f"运行中 · {self.elapsed:.1f} s", t, "caption", t.palette.text_muted),
                C.text_button("取消", self._cancel_clicked, t, height=24),
            ])
        if self.cancelled:
            return C.text("已取消，可重跑。", t, "caption", t.palette.text_muted)
        if self.loaded:
            total = self._total if self._total is not None else (self.total_matched or 0)
            shown = self._loaded or min(self.count, total)
            tail = " · 已截断" if (self.truncated and not self._has_more) else ""
            return C.text(f"1–{shown} / {total:,} · {_clock(self.elapsed)}{tail}",
                          t, "caption", t.palette.text_faint)
        return C.text("", t, "caption", t.palette.text_faint)

    def _filter(self) -> ft.Control:
        t = self.t
        self._field = C.text_field(
            t, value=self.text, hint_text=HINTS.get(self.mode, HINTS["key"]),
            on_change=self._on_text, on_submit=self._on_submit, width=352,
            field_ref=None)
        self._field._bib_search_field = True
        mode = C.select(
            t, value=self.mode, options=[(key, label) for key, label in MODES],
            on_select=self._on_mode, width=88, hint="方式")
        return S.filter_row(
            t, search=self._field, on_search=self._on_text, on_submit=self._on_submit,
            controls=[mode, self._query_button()],
            count=self._count_control(), on_advanced=self.open_advanced,
            page=self.page.page if hasattr(self.page, "page") else None,
            search_width=352)

    def _table(self) -> ft.Control:
        t = self.t
        if self._build_width < 1100:
            # 1000 宽：只留题名/年/状态，详情折到表格下方，不横向滚动。
            columns = [
                BT.BibColumn("title", "题名", flex=3.2, sortable=True),
                BT.BibColumn("year", "年", flex=0.8, align="right", sortable=True),
                BT.BibColumn("status", "状态", flex=0.9, sortable=False),
            ]
        else:
            columns = [
                BT.BibColumn("title", "题名", flex=3.2, sortable=True),
                BT.BibColumn("author", "作者", flex=1.8, sortable=True),
                BT.BibColumn("year", "年", flex=0.8, align="right", sortable=True),
                BT.BibColumn("key", "key", flex=1.9, mono=True, sortable=False),
                BT.BibColumn("status", "状态", flex=0.9, sortable=False),
                BT.BibColumn("source", "来源", flex=1.6, sortable=False),
            ]
        table = BT.BibTable(
            t, columns, self.rows, limit=max(PAGE_SIZE, len(self.rows)),
            page_size=PAGE_SIZE,
            empty_message=(NO_RESULT if self.loaded else "还没有查询"),
            empty_action=None, on_row_click=self._row_clicked,
            on_sort=self._on_sort, row_key="key", first_col_width=160,
            scroll_x=(self._build_width >= 1100), height=None, page=self.page.page)
        table._bib_first_col_width = 160
        table.empty_action = C.text_button(
            "清空筛选" if self.loaded else "查询", 
            (lambda e: self._clear()) if self.loaded else (lambda e: self.query()),
            t, height=28)
        return table.build()

    def _row_clicked(self, row: dict) -> None:
        key = str(row.get("key") or "")
        self.selected_key = key
        self._refresh()

    def _clear(self) -> None:
        self.text = ""
        self.error = ""
        self.input_error = ""
        self.success = ""
        self.limit = 100
        self.query_advanced = False
        self._reset_paging()
        self._set_store("bib_query_text", "")
        self._refresh()

    def _selected_hit(self) -> dict | None:
        for hit in self.hits:
            if str(hit.get("key") or (hit.get("record") or {}).get("key")) == self.selected_key:
                return hit
        return None

    def _detail_body(self) -> ft.Control:
        t = self.t
        hit = self._selected_hit()
        if hit is None:
            return C.empty_line("未选择记录", t)
        fields = _hit_fields(hit)
        record = hit.get("record") or {}
        rows = [
            ("key", hit.get("key") or record.get("key") or "—"),
            ("类型", record.get("entry_type") or fields.get("entry_type") or "—"),
            ("题名", hit.get("title") or fields.get("title") or "—"),
            ("作者", hit.get("author") or fields.get("author") or "—"),
            ("年份", fields.get("year") or fields.get("date") or "—"),
            ("DOI", fields.get("doi") or "—"),
            ("来源", _source_of(hit)),
            ("核实", "已核实" if (hit.get("verified") or record.get("verified")) else "未核实"),
        ]
        body = []
        for label, value in rows:
            body.append(ft.Column(spacing=0, controls=[
                C.text(label, t, "caption", t.palette.text_faint),
                C.text(str(value), t, "caption", t.palette.text,
                       max_lines=2, overflow=ft.TextOverflow.ELLIPSIS, tooltip=str(value)),
            ]))
        return ft.Column(spacing=t.sp("1"), scroll=C.scrollbar(t), controls=body)

    def _detail_actions(self) -> list:
        t = self.t
        hit = self._selected_hit()
        if hit is None:
            return []
        key = str(hit.get("key") or "")
        return [
            C.secondary_button("去编辑", lambda e: self._edit(key), t, height=28),
            C.secondary_button("复制 BibTeX", lambda e: self._copy_bibtex(hit), t, height=28),
            C.text_button("打开库文件", lambda e: self._open_log(key), t, height=28),
        ]

    def _edit(self, key: str) -> None:
        self._set_store("bib_focus_key", key)
        self._set_store("bib_nav_origin", "query")
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter("edit")

    def _copy_bibtex(self, hit: dict) -> None:
        """复制引擎给的 BibTeX；命中没带时按 key 向引擎再取（绝不自己拼）。"""
        text = str(hit.get("bibtex") or "").strip()
        if text:
            self._copy(text)
            return
        key = str(hit.get("key") or (hit.get("record") or {}).get("key") or "")
        if not key:
            return
        self._schedule_async(self._copy_bibtex_fetch, key)

    async def _copy_bibtex_fetch(self, key: str) -> None:
        text = ""
        try:
            call = EB.bibdb_query_basic_call(
                "key", key, limit=1, bibtex=True, db=self._db())
            result = await asyncio.to_thread(
                EB.read_json_command, call, timeout=30.0, notes=self._notes())
            data = (result or {}).get("json") or {}
            for hit in list(data.get("hits") or []):
                if str(hit.get("key") or "") == key:
                    text = str(hit.get("bibtex") or "").strip()
                    break
        except Exception:  # noqa: BLE001 —— 取不到就提示，不伪造
            text = ""
        finally:
            self._task_running = False
        if text:
            self._copy(text)
            return
        self.error = f"没有取到 {key} 的 BibTeX；可打开详情核对记录。"
        self._refresh()

    def _open_log(self, key: str) -> None:
        opener = getattr(getattr(self.page, "app", None), "open_path", None)
        path = self._db()
        if callable(opener) and path:
            try:
                opener(path)
            except Exception:  # noqa: BLE001
                pass

    def _copy(self, text: str) -> None:
        fn = getattr(getattr(self.page, "app", None), "copy_text", None)
        if callable(fn):
            try:
                fn(text)
                return
            except Exception:  # noqa: BLE001
                pass
        setter = getattr(getattr(self.page, "page", None), "set_clipboard", None)
        if callable(setter):
            try:
                setter(text)
            except Exception:  # noqa: BLE001
                pass

    def _detail_panel(self) -> ft.Control:
        t = self.t
        hit = self._selected_hit()
        title = "记录详情" if hit else "未选择记录"
        return S.detail_panel(
            t, body=self._detail_body(), title=title,
            meta=(f"{hit.get('key')} · 单条记录" if hit else "选一行看字段，或按 key 精确定位。"),
            actions=self._detail_actions() or None)

    def _placeholder(self) -> ft.Control:
        t = self.t
        return S.detail_panel(t, body=C.empty_line("未选择记录", t),
                              title="未选择记录",
                              meta="选一行看字段，或按 key 精确定位。")

    def _footer(self) -> ft.Control:
        t = self.t
        parts: list = []
        cap_legacy = self.limit >= ENGINE_CAP
        if self.loaded and self.count:
            if cap_legacy:
                parts.append(C.text(f"已载入 {min(self.count, self.limit)} / "
                                    f"{(self.total_matched or 0):,}",
                                    t, "caption", t.palette.text_faint))
            elif self._has_more or self._page_error:
                # 分页中 / 库变化拒绝合并：如实显示已载入与总数，不冒充「全部载入」。
                parts.append(C.text(f"已载入 {self._loaded or self.count} / 共 "
                                    f"{self._total or 0} 条",
                                    t, "caption", t.palette.text_faint))
            else:
                parts.append(C.text(f"已全部载入（共 {self._total or self.count} 条）",
                                    t, "caption", t.palette.text_faint))
        if cap_legacy:
            parts.append(C.text("最多 200 条；请加条件", t, "caption",
                                t.palette.text_faint))
        elif self._has_more and self._db_sha:
            parts.append(C.text_button("载入更多", lambda e: self.load_more(), t, height=28))
        if not parts:
            return ft.Container(height=t.px(28))
        return ft.Row(spacing=t.sp("1"), controls=parts)

    def _initial_banner(self) -> ft.Control | None:
        t = self.t
        if self.running or self.loaded:
            return None
        if self.error:
            return S.state_banner(t, "failure", message=self.error,
                                  detail="条件与结果保留；可直接重试。",
                                  next_label="重试", on_next=lambda e: self.query(),
                                  page=self.page.page)
        return ft.Column(spacing=0, controls=[
            S.state_banner(t, "empty", message="还没有查询",
                           next_label="高级检索", on_next=self.open_advanced),
            C.text("输入 key、DOI 或题名；⌘F 聚焦。", t, "caption",
                   t.palette.text_faint),
        ])

    def _body(self) -> ft.Control:
        t = self.t
        self._build_width = self._design_width()
        narrow = self._build_width < 1100
        parts: list = []
        if self.error and self.loaded:
            parts.append(S.state_banner(
                t, "failure", message=self.error,
                next_label="复制错误", on_next=lambda e: self._copy(self.error),
                page=self.page.page))
        if self._page_error:
            # 库在分页之间变化 / 位置错位：不合并、保留已载入，给「按当前库重查」入口。
            parts.append(S.state_banner(
                t, "cancelled", message=self._page_error,
                detail="已载入的结果保留；按当前库重新查询会从第一页开始。",
                action=C.text_button("刷新后重查", lambda e: self._restart_query(),
                                     t, height=28),
                page=self.page.page))
        banner = self._initial_banner()
        if banner is not None:
            parts.append(banner)
        if self.cancelled:
            parts.append(S.state_banner(t, "cancelled", message="已取消，可重跑。",
                                        next_label="重试", on_next=lambda e: self.query()))
        table = self._table()
        footer = self._footer()
        if narrow:
            parts.extend([table, footer, self._placeholder() if not self.selected_key
                          else self._detail_panel()])
        else:
            parts.append(ft.Row(
                expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[ft.Container(expand=True, content=ft.Column(
                    spacing=0, expand=True, controls=[table, footer])),
                    self._detail_panel()]))
        return ft.Column(spacing=t.sp("1"), expand=True, controls=parts)

    def build(self) -> ft.Control:
        self._build_width = self._design_width()
        meta = f"只读 · 每页 {PAGE_SIZE} 条"
        total = (getattr(self.page, "stats_data", None) or {}).get("records")
        if isinstance(total, int):
            meta = f"库 {total:,} 条 · {meta}"
        return S.page_shell(
            self.t, title=TITLE, meta=meta, role_chip="readonly",
            action=None, on_gear=lambda e: self._goto_settings(),
            body=ft.Column(spacing=0, expand=True,
                           controls=[self._filter(), self._body()]))

    def _goto_settings(self) -> None:
        setter = getattr(self.page, "set_view", None)
        if callable(setter):
            setter("settings")
