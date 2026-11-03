"""批 115 夜间 B0：引用库轻量表格（列定义 / 排序 / 选中 / 空态 / 载入更多）。

为什么要有这个类
----------------
引用库有 6 处表格（查询、去重、字段核对、待处理、导入差异、健康报告），旧代码
每处各写一套 ``table_row`` 循环，行高 30/32 混用、排序箭头有时是文字 ``▲``、
空态有时退回 180 高的大卡片。这里把「列定义 → 行数据 → 表头/行/空态/加载更多」
收成一个小类：**不继承 ft.Control**（Flet 1.0.5 没有 UserControl），页面每次
重建时调用 ``build()`` 取一棵新树，状态（排序、选中、limit）留在对象里。

几何与颜色（由测试锁住）
------------------------
- 表头 30、行 32，行底 0.6 发丝线，无竖线；选中整行 ``accent_soft``，hover
  ``surface_2``；排序箭头 8px。
- 颜色只经 ``t.palette``；本文件没有十六进制色值；不使用裸 ``ft.*Button``。
- ≤300 行用 ``Column(scroll=AUTO)``（全部构建，无虚拟化开销）；
  >300 行用 ``ListView(on_scroll=...)``，滚到距底 24px 内自动 ``load_more``。

首列与横向滚动（Flet 1.0.5 限制，已在 notes 说明）
--------------------------------------------------
Flet 没有原生 sticky 列，也没有可同步的两个纵向滚动区。因此：
- 默认 ``scroll_x=False``：所有列用 flex 权重均分，首列固定 280（``first_col_width``），
  表格永远不横向溢出——1000 宽时按设计稿**减列**，而不是挤成一团。
- ``scroll_x=True``：整张表（表头 + 所有行）作为一个整体横向滚动，首列仍是固定宽，
  但会随整表一起滚走，**不是 sticky**。这是「无解时退化为不分列横滚」的落地：
  列与列始终对齐，不会出现每行各自横滚的错位。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

import flet as ft

from ui import components as C
from ui.tokens import Tokens

TABLE_HEADER_H = 30
TABLE_ROW_H = 32
DEFAULT_FIRST_COL_W = 280
LOAD_MORE_H = 36
STICKY_FALLBACK_NOTE = "Flet 1.0.5 无原生 sticky：scroll_x 模式整表横滚，首列固定宽但不吸附"


@dataclass
class BibColumn:
    """一列的定义。``width`` 只在 scroll_x 模式生效；``flex`` 是默认模式的权重。"""

    key: str
    label: str
    width: float | None = None
    flex: float = 1.0
    align: str = "left"
    mono: bool = False
    sortable: bool = True
    format: Callable[[Any], str] | None = None


def _side(t: Tokens, width: float = 1, color: str | None = None) -> ft.BorderSide:
    """发丝线边：0.6 的行底线保留小数，1px 至少落一个物理像素。"""
    return ft.BorderSide(width=max(0.5, round(width * t.k, 2)),
                         color=color or t.palette.border)


def _weight(value: float) -> int:
    """flex 权重 ×10 后取整：保留 3.2/1.8/0.8 的设计比例（Row 只收 int）。"""
    return max(1, int(round(value * 10)))


def _is_right(align: str) -> bool:
    """数字/年份列右对齐；其余左对齐。"""
    return str(align).lower() in ("right", "end", "num")


class BibTable:
    """引用库表格：持有行/列/排序/选中/limit，``build()`` 产出当前一帧。

    为什么不缓存控件：排序后行顺序、选中底色、剩余条数都会变；重建一棵树比就地
    改十几个 Container 更不容易漏更新，也避免跨页面复用控件时报 ``Control must be
    added to page before``。
    """

    def __init__(self, t: Tokens, columns: Sequence[BibColumn | dict], rows: Iterable[dict], *,
                 limit: int = 100, page_size: int = 100,
                 empty_message: str = "没有匹配记录", empty_action: Any = None,
                 on_row_click: Callable[[dict], None] | None = None,
                 on_sort: Callable[[str, bool], None] | None = None,
                 on_load_more: Callable[[int], None] | None = None,
                 on_change: Callable[[], None] | None = None,
                 row_key: str | None = None,
                 first_col_width: float = DEFAULT_FIRST_COL_W,
                 scroll_x: bool = False,
                 height: float | None = None,
                 page=None) -> None:
        """收列定义与行数据；默认值即设计稿几何。"""
        self.t = t
        self.columns = [c if isinstance(c, BibColumn) else BibColumn(**c) for c in columns]
        if not self.columns:
            raise ValueError("BibTable 至少需要一列")
        self.rows = list(rows)
        self.limit = max(1, int(limit))
        self.page_size = max(1, int(page_size))
        self.empty_message = empty_message
        self.empty_action = empty_action
        self.on_row_click = on_row_click
        self.on_sort = on_sort
        self.on_load_more = on_load_more
        self.on_change = on_change
        self.row_key = row_key
        self.first_col_width = first_col_width
        self.scroll_x = bool(scroll_x)
        self.height = height
        self.page = page
        self._sort_key: str | None = None
        self._sort_desc = False
        self._selected: tuple[str, Any] | None = None
        self._last_control: ft.Control | None = None

    # ── 数据与状态 ────────────────────────────────────────────────────────
    @property
    def total(self) -> int:
        """全部行数（不是当前可见行数）。"""
        return len(self.rows)

    @property
    def visible_count(self) -> int:
        """当前 limit 下实际可见的行数，用于“显示 100 / 3,883”这类计数。"""
        return min(self.limit, self.total)

    @property
    def remaining(self) -> int:
        """还有多少行没被“载入更多”放出来。"""
        return max(0, self.total - self.visible_count)

    @property
    def has_more(self) -> bool:
        """是否还有未载入的行。"""
        return self.remaining > 0

    @property
    def sort_key(self) -> str | None:
        """当前排序列键；未排序为 None。"""
        return self._sort_key

    @property
    def sort_desc(self) -> bool:
        """当前是否降序；空值永远沉底，所以只对“有值行”有意义。"""
        return self._sort_desc

    def visible_rows(self) -> list[dict]:
        """排序后的前 ``limit`` 行；排序是稳定的，空值永远排最后。"""
        ordered = self._sorted_rows()
        return ordered[: self.limit]

    def _sorted_rows(self) -> list[dict]:
        """稳定排序：数字按数值、文本小写、空值沉底。"""
        if self._sort_key is None:
            return list(self.rows)
        key = self._sort_key
        present = [r for r in self.rows if r.get(key) is not None]
        missing = [r for r in self.rows if r.get(key) is None]

        def _value(row: dict):
            """排序键：数字按数值、文本按小写。"""
            v = row.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return (0, float(v))
            return (1, str(v).lower())

        present.sort(key=_value, reverse=self._sort_desc)
        return present + missing

    def sort_by(self, key: str, desc: bool | None = None) -> None:
        """按列键排序；``desc=None`` 时同一列翻转方向，换列默认升序。

        为什么自己排而不是只回调页面：排序是表格的显示职责，页面只关心“用户想按
        哪列看”。``on_sort`` 仍会收到 ``(key, desc)``，供服务端排序的页面覆盖。
        """
        col = next((c for c in self.columns if c.key == key), None)
        if col is None:
            raise KeyError(f"没有这一列：{key!r}")
        if not col.sortable:
            raise ValueError(f"列 {key!r} 不可排序")
        if desc is None:
            # 换列必须回到升序；只有同一列再点才翻转。旧写法对 None 取反会让
            # 首次点击就降序，和设计稿“点列头先升序”的约定相反。
            desc = self._sort_key == key and not self._sort_desc
        self._sort_key = key
        self._sort_desc = bool(desc)
        if self.on_sort is not None:
            self.on_sort(key, self._sort_desc)

    def set_rows(self, rows: Iterable[dict], *, reset_limit: bool = True) -> None:
        """换一批行（筛选/刷新后）：保留仍存在的那条选中，limit 归位到 100。

        为什么按 ``row_key`` 而不是 id 保留选中：刷新会重新查库生成新 dict，id 必变；
        有稳定业务键时用户刚选中的那条不该因为一次刷新就丢。
        """
        old = self._selected
        self.rows = list(rows)
        if reset_limit:
            self.limit = self.page_size
        if old is not None and old[0] == "k":
            match = next((r for r in self.rows if r.get(self.row_key or "") == old[1]), None)
            self._selected = self._identity(match) if match is not None else None
        elif old is not None:
            self._selected = None

    def load_more(self) -> int:
        """多放出 ``page_size`` 行，返回本次新增可见的行数。

        为什么不是分页：设计稿明确“不分页”，用户心智是连续列表；limit+载入更多
        既保住首屏 100 的性能，又不会把用户送到第 2 页后失去上下文。
        """
        before = self.visible_count
        self.limit = min(max(self.limit, before) + self.page_size, max(self.total, before))
        added = self.visible_count - before
        if self.on_load_more is not None:
            self.on_load_more(self.visible_count)
        return added

    def _identity(self, row: dict | None) -> tuple[str, Any] | None:
        """行身份：有 row_key 用它，否则用对象 id。"""
        if row is None:
            return None
        if self.row_key and row.get(self.row_key) is not None:
            return ("k", row[self.row_key])
        return ("id", id(row))

    def is_selected(self, row: dict) -> bool:
        """整行选中判定；排序后仍按稳定身份比较，而不是下标。"""
        return self._selected is not None and self._selected == self._identity(row)

    def select(self, row: dict | int | str | None) -> None:
        """选中一行（dict / 可见下标 / row_key 值）；传 None 清空。"""
        if row is None:
            self._selected = None
            return
        if isinstance(row, int):
            visible = self.visible_rows()
            if not 0 <= row < len(visible):
                raise IndexError(f"行下标越界：{row}")
            row = visible[row]
        elif isinstance(row, str):
            if not self.row_key:
                raise ValueError("按字符串选中需要 row_key")
            row = next((r for r in self.rows if str(r.get(self.row_key)) == row), None)
            if row is None:
                raise KeyError(f"找不到 {self.row_key}={row!r}")
        self._selected = self._identity(row)

    def clear_selection(self) -> None:
        """清空选中（Esc / 批量取消选择共用）。"""
        self._selected = None

    # ── 交互 ──────────────────────────────────────────────────────────────
    def _row_clicked(self, row: dict) -> None:
        """点击整行：先落选中，再通知页面；页面可用回调打开 360 详情。"""
        self.select(row)
        if self.on_row_click is not None:
            self.on_row_click(row)
        if self.on_change is not None:
            self.on_change()

    def _header_clicked(self, index: int) -> None:
        """点表头：翻转/切换排序，然后让页面重建。"""
        self.sort_by(self.columns[index].key)
        if self.on_change is not None:
            self.on_change()

    def _load_more_clicked(self, e=None) -> None:
        """点“载入更多”：加 limit；有 on_change 时由页面重建，否则就地更新。"""
        if not self.has_more:
            return
        self.load_more()
        if self.on_change is not None:
            self.on_change()
        elif self._last_control is not None:
            # 控件可能尚未挂到 Page（单测/首帧前）：.page 属性会直接抛
            # RuntimeError，因此用 update 的失败来判定“未挂载”，而不是查 .page。
            fresh = self.build()
            last = self._last_control
            if isinstance(last, ft.Container):
                last.content = fresh
            elif isinstance(last, ft.Column) and isinstance(fresh, ft.Column):
                last.controls = fresh.controls
            try:
                last.update()
            except RuntimeError:
                pass

    def _scrolled(self, e) -> None:
        """滚到距底 24px 内自动载入更多；只在 total>300 的 ListView 上挂。"""
        if self.total <= 300 or not self.has_more:
            return
        pixels = float(getattr(e, "pixels", 0) or 0)
        viewport = float(getattr(e, "viewport_dimension", 0)
                         or getattr(e, "viewport_height", 0) or 0)
        extent = float(getattr(e, "max_scroll_extent", 0) or 0)
        if extent <= 0 or pixels + viewport >= extent - 24:
            self._load_more_clicked()

    # ── 构建 ──────────────────────────────────────────────────────────────
    def _cell_width(self, col: BibColumn) -> float:
        """scroll_x 单列宽：width 优先，否则 flex 近似 120px/份。"""
        if col.width is not None:
            return col.width
        return max(60, col.flex * 120)

    def _total_width(self) -> float:
        """整表固定宽 = 首列宽 + 其余列宽。"""
        total = 0.0
        for i, col in enumerate(self.columns):
            if i == 0 and col.width is None:
                total += self.first_col_width
            else:
                total += self._cell_width(col)
        return total

    def _cell_box(self, col: BibColumn, content: ft.Control, *, first: bool) -> ft.Container:
        """一格容器：默认模式首列固定宽、其余 flex；scroll_x 模式全部固定宽。"""
        t = self.t
        if self.scroll_x:
            size = self.first_col_width if first and col.width is None else self._cell_width(col)
            return ft.Container(
                width=t.px(size), content=content,
                padding=ft.Padding(left=0, top=0, right=t.px(8), bottom=0),
                alignment=(ft.Alignment.CENTER_RIGHT if _is_right(col.align)
                           else ft.Alignment.CENTER_LEFT),
            )
        if first:
            return ft.Container(
                width=t.px(self.first_col_width), content=content,
                padding=ft.Padding(left=0, top=0, right=t.px(8), bottom=0),
                alignment=(ft.Alignment.CENTER_RIGHT if _is_right(col.align)
                           else ft.Alignment.CENTER_LEFT),
            )
        return ft.Container(
            expand=_weight(col.flex), content=content,
            padding=ft.Padding(left=0, top=0, right=t.px(8), bottom=0),
            alignment=(ft.Alignment.CENTER_RIGHT if _is_right(col.align)
                       else ft.Alignment.CENTER_LEFT),
        )

    def _header(self) -> ft.Container:
        """表头 30：11/600 灰 + 8px 排序箭头；无竖线，上下各一条发丝线。"""
        t = self.t
        p = t.palette
        cells: list[ft.Control] = []
        for i, col in enumerate(self.columns):
            active = self._sort_key == col.key
            label = C.text(col.label, t, "caption", p.text_faint, weight="semibold",
                           max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                           text_align=ft.TextAlign.RIGHT if _is_right(col.align) else None)
            parts: list[ft.Control] = [label]
            if col.sortable:
                arrow_name = ("ARROW_DOWNWARD" if self._sort_desc else "ARROW_UPWARD") \
                    if active else "SORT"
                arrow = ft.Icon(C.ic(arrow_name), size=t.px(8),
                                color=p.text_muted if active else p.border_strong)
                arrow._bib_sort_arrow = "desc" if (active and self._sort_desc) else (
                    "asc" if active else "idle")
                parts.append(arrow)
            inner = ft.Row(
                spacing=t.sp("half"), controls=parts,
                alignment=ft.MainAxisAlignment.END if _is_right(col.align) else ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            box = self._cell_box(col, inner, first=(i == 0))
            box.on_click = (lambda e, index=i: self._header_clicked(index)) if col.sortable else None
            box.tooltip = f"按{col.label}排序" if col.sortable else None
            box.ink = False
            box._bib_col_key = col.key
            box._bib_sorted = ("desc" if self._sort_desc else "asc") if active else None
            cells.append(box)
        header = ft.Container(
            height=t.px(TABLE_HEADER_H),
            padding=ft.Padding(left=t.px(8), top=0, right=t.px(8), bottom=0),
            border=ft.Border(top=_side(t), bottom=_side(t)),
            content=ft.Row(spacing=0, controls=cells,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )
        header._bib_header = True
        return header

    def _row(self, row: dict, index: int) -> ft.Container:
        """行 32：首列 11.5/500、其余 11 灰、数字右对齐、key/DOI 等宽。"""
        t = self.t
        p = t.palette
        selected = self.is_selected(row)
        cells: list[ft.Control] = []
        for i, col in enumerate(self.columns):
            raw = row.get(col.key)
            value = col.format(raw) if col.format is not None else ("" if raw is None else str(raw))
            if i == 0:
                text = C.text(value, t, "section-label", p.text, weight="medium",
                              max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                              tooltip=value or None,
                              text_align=ft.TextAlign.RIGHT if _is_right(col.align) else None)
            else:
                kwargs = {}
                if col.mono:
                    kwargs = {"font_family": t.mono_family,
                              "font_family_fallback": t.mono_fallback}
                text = C.text(value, t, "caption", p.text_muted, max_lines=1,
                              overflow=ft.TextOverflow.ELLIPSIS, tooltip=value or None,
                              text_align=ft.TextAlign.RIGHT if _is_right(col.align) else None,
                              **kwargs)
            cells.append(self._cell_box(col, text, first=(i == 0)))

        box = ft.Container(
            height=t.px(TABLE_ROW_H),
            padding=ft.Padding(left=t.px(8), top=0, right=t.px(8), bottom=0),
            bgcolor=p.accent_soft if selected else None,
            border=ft.Border(bottom=_side(t, 0.6)),
            content=ft.Row(spacing=0, controls=cells,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
            on_click=lambda e, r=row: self._row_clicked(r),
            ink=True,
        )

        def _hover(e, box=box, selected=selected) -> None:
            """hover 只在未选中时铺 surface_2。"""
            on = e.data in (True, "true", "True")
            box.bgcolor = t.palette.accent_soft if selected else (
                t.palette.surface_2 if on else None)
            try:
                box.update()
            except Exception:  # noqa: BLE001 —— 未挂载时只改属性
                pass

        box.on_hover = _hover
        box._bib_row = True
        box._bib_selected = selected
        box._bib_row_key = self._identity(row)
        return box

    def _empty(self) -> ft.Container:
        """空态一行：保留表头，只给一句 ≤20 字的话 + 一个下一步动作。"""
        t = self.t
        action = self.empty_action
        if action is None:
            action = C.text_button("清除筛选", None, t, height=28)
            action.disabled = True
        box = ft.Container(
            height=t.px(TABLE_ROW_H),
            padding=ft.Padding(left=t.px(8), top=0, right=t.px(8), bottom=0),
            border=ft.Border(bottom=_side(t, 0.6)),
            content=C.empty_line(self.empty_message, t, action=action),
        )
        box._bib_empty = True
        return box

    def _load_more(self) -> ft.Container:
        """底部“载入更多”36 高，带剩余条数；没有剩余时返回空壳（不占位）。"""
        t = self.t
        label = f"载入更多 · 还剩 {self.remaining} 条"
        btn = C.text_button(label, self._load_more_clicked, t, height=28)
        box = ft.Container(height=t.px(LOAD_MORE_H), alignment=ft.Alignment.CENTER,
                           content=btn)
        box._bib_load_more = True
        return box

    def _footer(self) -> ft.Container | None:
        """底部加载更多；无剩余返回 None，不占 36 高。"""
        return self._load_more() if self.has_more else None

    def build(self) -> ft.Control:
        """构建当前帧：表头 + 行区 + 载入更多；空数据仍保留表头。

        行区按行数选容器：≤300 用 ``Column(scroll=AUTO)`` 全量构建；>300 用
        ``ListView(on_scroll)``，滚动近底自动加 limit。这条阈值是设计稿的
        “300 行以内不做虚拟化”，不是性能玄学——100 行首屏 + 300 行缓冲足够。
        """
        t = self.t
        header = self._header()
        visible = self.visible_rows()
        rows = [self._row(r, i) for i, r in enumerate(visible)]

        use_list = self.total > 300
        if use_list:
            body = ft.ListView(expand=True, spacing=0, padding=0, controls=rows,
                               on_scroll=self._scrolled)
            body._bib_list = True
        else:
            body = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True,
                             controls=rows or [self._empty()])
            body._bib_list = False

        if self.scroll_x:
            grid = ft.Container(width=t.px(self._total_width()), content=ft.Column(
                spacing=0, controls=[header, *rows] if rows else [header, self._empty()]))
            scroller = ft.Row(scroll=ft.ScrollMode.AUTO, expand=True, controls=[grid],
                              vertical_alignment=ft.CrossAxisAlignment.START)
            body = ft.ListView(expand=True, spacing=0, padding=0, controls=[scroller],
                               on_scroll=self._scrolled if use_list else None)
            body._bib_list = use_list
            body._bib_scroll_x = True

        footer = self._footer()
        # scroll_x 时 header 已经放进横向滚动的 grid；若再放一份到 root，
        # 页面会看到两条表头且第二条会随横滚错位。默认模式才把 header 独立出来。
        root = ft.Column(spacing=0, expand=True,
                         controls=[body] if self.scroll_x else [header, body])
        root._bib_table = True
        root._bib_scroll_x = self.scroll_x
        root._bib_visible = len(visible)
        if footer is not None:
            root.controls.append(footer)
        wrapped: ft.Control = root
        if self.height is not None:
            wrapped = ft.Container(height=t.px(self.height), content=root)
        self._last_control = wrapped
        return wrapped
