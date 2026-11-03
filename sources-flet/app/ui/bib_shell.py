"""批 115 夜间 B0：引用库统一页面骨架（页面壳 / 只读 / 写库 / 状态）。

为什么单独成模块
----------------
引用库 10 个视图此前各画各的页头、筛选行、详情栏和确认条，结果同一批里出现
52/28/44 三种筛高度、360 与 340 两种详情栏、圆角与描边各自为政。这里把
``report.md`` §4 的骨架固化成**唯一实现**：页面只拼内容，不再自己量页头高度。
本模块不 import 页面、不持有 ``page`` 引用（剪贴板与对话框除外），因此十个视图
可以并行改造而不会互相踩。

设计依据
--------
- ``evidence/b115-bib-redesign/report.md`` §4（壳/只读骨架/写库骨架/表格/七态）
- ``evidence/b115-redesign/design-brief.md``（tokens、字号、间距、Flet 1.0.5 限制）
- 1160 与 1000 两张线框：页头 72、筛选 44、表头 30、行 32、确认条 56、
  参数列 356、详情 360、日志 dock 40/200、状态栏 26 不归本壳管。

硬约束（测试锁住）
------------------
- 颜色只经 ``t.palette``；本文件没有十六进制色值。
- 控件 28、输入 48（浮动标签）、表格行 32、表头 30、确认条 56、横幅 44。
- 页面主体禁用 ``C.card``（卡片流是旧版式），表格/详情都是平铺 + 发丝线。
- 不直接构造 ``ft.TextField`` / ``ft.Dropdown`` / ``ft.*Button``：
  一律走 ``C.search_field`` / ``C.text_button`` / ``C.primary_button`` 等——
  它们是本仓唯一能保证 28/48 几何和主题重染的入口。
"""

from __future__ import annotations

import inspect
from typing import Any, Iterable, Sequence

import flet as ft

from ui import components as C
from ui.tokens import Tokens

# ── 设计 px（不随 zoom 变；使用时统一经 t.px）────────────────────────────────
PAGE_HEADER_H = 72
PAGE_H_PAD = 24
FILTER_ROW_H = 44
TABLE_HEADER_H = 30
TABLE_ROW_H = 32
DETAIL_W = 360
PARAMS_W = 356
SUMMARY_H = 58
CONFIRM_H = 56
BANNER_H = 44
BATCH_H = 44
DOCK_H = 40
DOCK_H_EXPANDED = 200
NARROW_W = 1000
DETAIL_STACK_BELOW = 1100

WRITE_HINT = "预览 → 确认 → 只写 bibliography.jsonl，不推送"
READONLY_HINT = "只读视图 · 不写库"

_ROLE_CHIPS = {
    "readonly": ("只读", "neutral", READONLY_HINT),
    "write": ("写库", "accent", WRITE_HINT),
    "stale": ("过期", "error", "库已被其他操作修改，确认写入已禁用"),
}

_TONES = {
    "info": ("accent_soft", "on_accent_soft", "INFO_OUTLINE", "INFO"),
    "success": ("success_soft", "success", "CHECK_CIRCLE_OUTLINE", "CHECK_CIRCLE"),
    "warn": ("warn_soft", "warn", "WARNING_AMBER_OUTLINED", "WARNING_AMBER"),
    "error": ("error_soft", "error", "ERROR_OUTLINE", "ERROR"),
    "neutral": ("surface_2", "text_muted", "INBOX", "INBOX"),
}


# ── 私有几何/语气工具（不导出，避免页面绕过 tokens）─────────────────────────
def _pad(t: Tokens, left: float, top: float, right: float, bottom: float) -> ft.Padding:
    """设计 px 到 Padding：24/12/8 只在这里乘一次 zoom。"""
    return ft.Padding(left=t.px(left), top=t.px(top), right=t.px(right), bottom=t.px(bottom))


def _side(t: Tokens, width: float = 1, color: str | None = None) -> ft.BorderSide:
    """发丝线边：设计 1px 至少落到 1 物理像素，0.6 行底线保留小数。"""
    return ft.BorderSide(width=max(0.5, round(width * t.k, 2)),
                         color=color or t.palette.border)


def _soft(t: Tokens, tone: str) -> tuple[str, str]:
    """语气 → (底色, 前景)。所有语气色都来自 palette，D 主题自动跟随。"""
    spec = _TONES.get(tone, _TONES["neutral"])
    return getattr(t.palette, spec[0]), getattr(t.palette, spec[1])


def _tone_icon(t: Tokens, tone: str, size: float = 16) -> ft.Icon:
    """语气图标：颜色跟随语义色，尺寸默认 16。"""
    spec = _TONES.get(tone, _TONES["neutral"])
    fg = getattr(t.palette, spec[1])
    return ft.Icon(C.ic(spec[2], spec[3]), size=t.px(size), color=fg)


def _as_controls(value: Any) -> list:
    """单控件/序列/None 统一成 list，省得每个 helper 各写判空。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item is not None]
    return [value]


def _weight(value: float | None) -> int:
    """flex 权重放大 10 倍，保住 3.2/1.8/0.8 这类设计比例（int 才收）。"""
    return max(1, int(round((value if value is not None else 1) * 10)))


def _align(value) -> ft.Alignment:
    """列对齐到 Container alignment：数字右对齐，文本左对齐。"""
    right = value == ft.TextAlign.RIGHT or str(value).lower() in ("right", "end")
    return ft.Alignment.CENTER_RIGHT if right else ft.Alignment.CENTER_LEFT


def _text_of(value: Any) -> str:
    """None 显示空串而不是 None 四个字母。"""
    return "" if value is None else str(value)


# ── 页面壳 ──────────────────────────────────────────────────────────────────
def _role_chip_control(t: Tokens, mode: str) -> ft.Container:
    """权限 chip 的唯一构造：三种语气 + tooltip + data。"""
    if mode not in _ROLE_CHIPS:
        raise ValueError(f"未知权限 chip：{mode!r}（只接受 readonly/write/stale）")
    label, tone, hint = _ROLE_CHIPS[mode]
    chip = C.badge(label, t, tone)
    chip.tooltip = hint
    chip.data = f"bib-role:{mode}"
    chip._bib_role = mode
    return chip


def role_chip(t: Tokens, mode: str = "readonly") -> ft.Container:
    """权限 chip：只读 / 写库 / 过期三种语气。

    为什么固定三种：权限是页面级事实，不是自由文案。写库页把后果提示放在
    ``meta``（见 ``WRITE_HINT``），chip 本身只回答“能不能写”，避免页头右侧
    同时出现两句长话、把标题挤成省略号。
    """
    return _role_chip_control(t, mode)


def page_shell(t: Tokens, *, title: str, meta: str, role_chip: Any = None,
               action: Any = None, gear: Any = True, on_gear=None, notice: Any = None,
               body: ft.Control, dock: Any = None, dock_height: float = DOCK_H) -> ft.Container:
    """白面板页面壳：页头 72 + 主体 24 padding + 日志 dock，不碰状态栏。

    为什么自己搭而不复用 ``C.page_header``：设计稿的页头是**固定 72**（标题带
    46、meta 带 25、发丝线 1），且右侧最多一个动作 + 齿轮；``C.page_header`` 是
    内容高度自适应，放进固定 72 会随字号/缩放溢出。这里逐段钉死，页面只传内容。

    ``role_chip`` 可以是 ``"readonly"``/``"write"``/``"stale"`` 或现成控件；
    ``action`` 最多一个（传列表会在这里报错，避免页面偷偷塞两个主按钮）；
    ``gear=True`` 但没有 ``on_gear`` 时按钮禁用——宁可不响，也不做假按钮。
    ``notice`` 只接 28 高单行提示；需要两行/可复制 stderr 时用 ``state_banner``
    放进主体。``dock`` 是页底日志槽（40 收起 / 200 展开由页面切），它是主体的
    兄弟节点而不是浮层，所以**不会盖住写库确认条**。
    """
    p = t.palette

    actions = _as_controls(action)
    if len(actions) > 1:
        raise ValueError("页头最多一个动作（设计稿 §9.7）；次要动作请放进页面主体")
    action = actions[0] if actions else None

    if isinstance(role_chip, str):
        chip_ctl = _role_chip_control(t, role_chip)
    else:
        chip_ctl = role_chip

    if on_gear is None and callable(gear):
        on_gear = gear
    gear_ctl = None
    if gear:
        gear_ctl = C.icon_button("SETTINGS", "设置", on_gear, t, width=28, height=28)
        gear_ctl.disabled = on_gear is None

    title_ctl = C.text(title, t, role="page-title", max_lines=1,
                       overflow=ft.TextOverflow.ELLIPSIS)
    meta_ctl = C.text(meta or "", t, role="meta", color=p.text_muted,
                      max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

    right = [ctl for ctl in (chip_ctl, action, gear_ctl) if ctl is not None]
    title_row = ft.Row(
        spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[ft.Container(content=title_ctl, expand=True), *right],
    )
    header = ft.Container(
        height=t.px(PAGE_HEADER_H), padding=_pad(t, PAGE_H_PAD, 0, PAGE_H_PAD, 0),
        border=ft.Border(bottom=_side(t)),
        content=ft.Column(spacing=0, expand=True, controls=[
            ft.Container(height=t.px(46), content=title_row),
            ft.Container(height=t.px(25), alignment=ft.Alignment.TOP_LEFT,
                         content=meta_ctl),
        ]),
    )

    children: list[ft.Control] = [header]
    notice_box = None
    if notice is not None:
        nctl = notice if isinstance(notice, ft.Control) else _notice_28(t, str(notice))
        # 8 顶距 + 28 通知条 = 36：通知条正好落在设计稿的 164..192；
        # 其后的主体再吃 12 上距，内容从 204 起（无通知时从 168 起）。
        notice_box = ft.Container(
            height=t.px(8 + 28), padding=_pad(t, PAGE_H_PAD, 8, PAGE_H_PAD, 0),
            content=ft.Container(height=t.px(28), content=nctl),
        )
        children.append(notice_box)

    body_box = ft.Container(
        expand=True, padding=_pad(t, PAGE_H_PAD, 12, PAGE_H_PAD, 0), content=body,
    )
    children.append(body_box)

    dock_box = None
    if dock is not None:
        dock_box = ft.Container(
            height=t.px(dock_height), border=ft.Border(top=_side(t)),
            padding=_pad(t, PAGE_H_PAD, 0, PAGE_H_PAD, 0), content=dock,
        )
        children.append(dock_box)

    panel = ft.Container(
        expand=True, bgcolor=p.surface,
        border_radius=ft.BorderRadius(
            top_left=t.radius("panel"), top_right=t.radius("panel"),
            bottom_left=0, bottom_right=0),
        content=ft.Column(spacing=0, expand=True, controls=children),
    )
    # 自定义元数据只为本包测试/页面取用，不参与渲染。
    panel._bib_shell = True
    panel._bib_header = header
    panel._bib_body = body_box
    panel._bib_notice = notice_box
    panel._bib_dock = dock_box
    panel._bib_title = title_ctl
    panel._bib_meta = meta_ctl
    panel._bib_chip = chip_ctl
    return panel


def _notice_28(t: Tokens, message: str, tone: str = "info") -> ft.Container:
    """页头通知条 28 高（设计稿固定 164..192）。

    为什么不用 ``C.notice_bar``：它的 8px 上下内边距 + 12 号正文实际高度约 32，
    会在 72 页头下面顶出 4px，1160/1000 两版都会把筛选行挤离基线。这里固定 28。
    """
    bg, fg = _soft(t, tone)
    return ft.Container(
        height=t.px(28), bgcolor=bg, border_radius=t.radius("control"),
        padding=_pad(t, 10, 0, 10, 0),
        content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
                           _tone_icon(t, tone, 14),
                           C.text(message, t, "caption", fg, expand=True, max_lines=1,
                                  overflow=ft.TextOverflow.ELLIPSIS),
                       ]),
    )


# ── 只读骨架 ────────────────────────────────────────────────────────────────
def bind_filter_shortcut(page, field) -> Any:
    """把 ⌘F / Ctrl+F 绑到筛选框，并**串接**页面原有键盘处理。

    为什么不直接 ``page.on_keyboard_event = ...``：主壳的 ``settings_shell``
    已装了一套全局热键，覆盖赋值会把 ⌘K/⌘R 全吞掉。这里保留 previous handler，
    只拦 F；同一页面重复调用时复用同一个闭包，避免越包越深。
    """
    state = getattr(page, "_bib_filter_state", None)
    if state is not None:
        state["field"] = field
        return state["handler"]

    prev = getattr(page, "on_keyboard_event", None)
    state: dict[str, Any] = {"field": field, "prev": prev}

    def _handler(event) -> None:
        """先拦 ⌘F/Ctrl+F，其余键原样交给主壳热键。"""
        key = str(getattr(event, "key", "") or "").lower()
        mod = bool(getattr(event, "meta", False) or getattr(event, "ctrl", False))
        alt = bool(getattr(event, "alt", False))
        if mod and not alt and key == "f":
            target = state["field"]
            try:
                target.focus()
                target.update()
            except Exception:  # noqa: BLE001 —— 未挂载时焦点由下一帧再落
                pass
            return
        if state["prev"] is not None:
            state["prev"](event)

    state["handler"] = _handler
    page._bib_filter_state = state
    page.on_keyboard_event = _handler
    return _handler


def filter_row(t: Tokens, *, search: Any = None,
               search_hint: str = "搜索题名 / 作者 / key / DOI", on_search=None,
               on_submit=None, controls: Iterable[Any] = (), count: Any = None,
               on_advanced=None, page=None, search_width: float = 300) -> ft.Container:
    """筛选行：44 高、控件 28、⌘F 聚焦搜索框。

    为什么 44 而不是 28：28 高控件直接贴表头会显得“行与行打架”；44 = 8 + 28 +
    8，给控件留出上下呼吸。搜索框固定 300，右侧计数/高级检索靠右，1000 宽也
    不会把主按钮挤没了。传 ``page=`` 时顺手绑 ⌘F（见 ``bind_filter_shortcut``）。
    """
    if search is None:
        search = C.search_field(t, hint_text=search_hint, on_change=on_search,
                                on_submit=on_submit, width=search_width)
    search._bib_search_field = True

    row_controls: list[Any] = [search, *list(controls or ())]
    if on_advanced is not None:
        row_controls.append(C.text_button("高级检索", on_advanced, t, height=28))
    row_controls.append(ft.Container(expand=True))
    if count is not None:
        if isinstance(count, str):
            count = C.text(count, t, "caption", t.palette.text_faint)
        row_controls.append(count)

    row = ft.Container(
        height=t.px(FILTER_ROW_H), padding=_pad(t, 0, 8, 0, 8),
        content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=row_controls),
    )
    row._bib_filter_row = True
    row._bib_filter_field = search
    if page is not None:
        bind_filter_shortcut(page, search)
    return row


def table_header_row(t: Tokens, cells: Sequence[Any], *, weights: Sequence[float] | None = None,
                     mono: Sequence[int] = (), align: Sequence[Any] | None = None,
                     sortable: Sequence[bool] | None = None, sort_index: int | None = None,
                     sort_desc: bool = False, on_sort=None) -> ft.Container:
    """表头 30 高：11/600 灰、可点列显示 8px ▲▼。

    为什么表头独立于 ``BibTable``：设置页/字段核对页只有三五行动态列，不值得
    引入表格类；但高度、排序箭头、发丝线必须和主表一致，所以这里给一份无状态
    的最小实现。``on_sort(index)`` 由页面自己翻转排序。
    """
    cells = list(cells)
    n = len(cells)
    weights = list(weights or [1] * n)
    mono = set(mono)
    sortable = list(sortable or [True] * n)
    align = list(align or ["left"] * n)
    p = t.palette
    controls: list[Any] = []

    for i, cell in enumerate(cells):
        if isinstance(cell, ft.Control):
            content = cell
        else:
            content = C.text(_text_of(cell), t, "caption", p.text_faint,
                             weight="semibold", max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS)
            if sort_index == i:
                arrow = "ARROW_DOWNWARD" if sort_desc else "ARROW_UPWARD"
                content = ft.Row(
                    spacing=t.sp("half"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=(ft.MainAxisAlignment.END if _align(align[i]) == ft.Alignment.CENTER_RIGHT
                               else ft.MainAxisAlignment.START),
                    controls=[content, ft.Icon(C.ic(arrow), size=t.px(8),
                                               color=p.text_muted)],
                )
        click = None
        if on_sort is not None and sortable[i]:
            click = (lambda e, index=i: on_sort(index))
        ctl = ft.Container(
            content=content, expand=_weight(weights[i]), padding=_pad(t, 0, 0, 8, 0),
            alignment=_align(align[i]), on_click=click,
            tooltip="点击排序" if click is not None else None,
        )
        controls.append(ctl)

    box = ft.Container(
        height=t.px(TABLE_HEADER_H), padding=_pad(t, 8, 0, 8, 0),
        border=ft.Border(top=_side(t), bottom=_side(t)),
        content=ft.Row(spacing=0,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=controls),
    )
    box._bib_header_row = True
    box._bib_sort_index = sort_index
    box._bib_sorted = ("desc" if sort_desc else "asc") if sort_index is not None else None
    return box


def table_row(t: Tokens, cells: Sequence[Any], *, weights: Sequence[float] | None = None,
              mono: Sequence[int] = (), align: Sequence[Any] | None = None,
              selected: bool = False, on_click=None,
              height: float = TABLE_ROW_H) -> ft.Container:
    """表格行 32 高：整行可点、选中 accent_soft、hover surface_2、行底 0.6。

    为什么行底不是 1px：32 行密排时 1px 会形成“栅栏”，设计定 0.6 让线退到
    背景里。首列 11.5/500 建立扫描锚点，其余 11/常规，数字右对齐、key 用等宽。
    """
    cells = list(cells)
    n = len(cells)
    weights = list(weights or [1] * n)
    mono = set(mono)
    align = list(align or ["left"] * n)
    p = t.palette
    controls: list[Any] = []

    for i, cell in enumerate(cells):
        if isinstance(cell, ft.Control):
            content = cell
        elif i == 0:
            content = C.text(_text_of(cell), t, "section-label", p.text,
                             weight="medium", max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS,
                             tooltip=_text_of(cell) or None)
        else:
            content = C.text(_text_of(cell), t, "caption", p.text_muted,
                             max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                             tooltip=_text_of(cell) or None,
                             font_family=(t.mono_family if i in mono else None))
        controls.append(ft.Container(
            content=content, expand=_weight(weights[i]), padding=_pad(t, 0, 0, 8, 0),
            alignment=_align(align[i]),
        ))

    box = ft.Container(
        height=t.px(height), padding=_pad(t, 8, 0, 8, 0),
        bgcolor=p.accent_soft if selected else None,
        border=ft.Border(bottom=_side(t, 0.6)),
        content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=controls),
        on_click=on_click, ink=on_click is not None,
    )

    if on_click is not None:
        def _hover(e, box=box, selected=selected) -> None:
            """hover 只在未选中时铺 surface_2；选中底色不闪。"""
            on = e.data in (True, "true", "True")
            if selected:
                box.bgcolor = t.palette.accent_soft
            else:
                box.bgcolor = t.palette.surface_2 if on else None
            try:
                box.update()
            except Exception:  # noqa: BLE001 —— 未挂载时只改属性
                pass

        box.on_hover = _hover
    box._bib_row = True
    box._bib_selected = selected
    return box


def detail_panel(t: Tokens, *, body: Any, title: str | None = None,
                 meta: str | None = None, actions: Any = None, width: float = DETAIL_W,
                 on_close=None) -> ft.Container:
    """右侧详情栏 360：左发丝线 + 16 内边距，底部动作。

    为什么宽度钉死 360：1160 窗口内容宽 892，主表留 516、详情 360、中间 16 的
    竖线，正好是设计稿的三段；<1100 时页面应改 BottomSheet（``page.show_dialog``），
    本 helper 不持有 page，所以宽度判断必须由页面做——这也是为了并行改造时不
    出现“helper 偷偷换布局”的隐式行为。
    """
    content: list[ft.Control] = []
    if title is not None or on_close is not None:
        head = [ft.Container(expand=True, content=C.text(title or "", t, role="body-strong",
                                                           max_lines=1,
                                                           overflow=ft.TextOverflow.ELLIPSIS))]
        if on_close is not None:
            head.append(C.icon_button("CLOSE", "关闭详情", on_close, t, width=26, height=28))
        content.append(ft.Row(spacing=t.sp("1"),
                              vertical_alignment=ft.CrossAxisAlignment.CENTER,
                              controls=head))
    if meta:
        content.append(C.text(meta, t, "caption", t.palette.text_faint,
                              max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))

    if isinstance(body, (list, tuple)):
        body_ctl = ft.Column(controls=list(body), spacing=t.sp("2"), scroll=ft.ScrollMode.AUTO,
                             expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    else:
        body_ctl = ft.Container(expand=True, content=body)
    content.append(body_ctl)

    acts = _as_controls(actions)
    if acts:
        content.append(ft.Row(spacing=t.sp("1"),
                              vertical_alignment=ft.CrossAxisAlignment.CENTER,
                              controls=[ft.Container(expand=True), *acts]))

    panel = ft.Container(
        width=t.px(width), border=ft.Border(left=_side(t)),
        padding=_pad(t, 16, 0, 0, 0),
        content=ft.Column(spacing=t.sp("2"), expand=True,
                          horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                          controls=content),
    )
    panel._bib_detail_panel = True
    panel._bib_detail_body = body_ctl
    return panel


def empty_row(t: Tokens, message: str, *, action: Any = None,
              height: float = TABLE_ROW_H) -> ft.Container:
    """空态只占一行：一句 ≤20 字的话 + 一个下一步动作。

    为什么不做 180 高空状态：引用库 10 个视图里空态是常态（筛选无结果、库为空），
    大插画会把表头推走；保留表头 + 一行话，用户立刻知道“列还在、只是没数据”。
    """
    box = ft.Container(
        height=t.px(height), padding=_pad(t, 8, 0, 8, 0),
        border=ft.Border(bottom=_side(t, 0.6)),
        content=C.empty_line(message, t, action=action),
    )
    box._bib_empty_row = True
    return box


def batch_bar(t: Tokens, *, count: int, actions: Any = (), on_clear=None) -> ft.Container | None:
    """批量条 44：仅在多选时出现；count<=0 返回 None，页面可直接塞进 Column。

    为什么返回 None 而不是 visible=False：``visible`` 仍占位，空批量条会在表格
    和详情之间留一条 44 的空白；None 让页面零分支地“有选中才挂”。
    """
    if count <= 0:
        return None
    acts = _as_controls(actions)
    row_controls: list[Any] = [
        C.text(f"已选 {count} 条", t, "small", t.palette.text, weight="medium"),
        ft.Container(expand=True),
        *acts,
    ]
    if on_clear is not None:
        row_controls.append(C.text_button("取消选择", on_clear, t, height=28))
    bar = ft.Container(
        height=t.px(BATCH_H), padding=_pad(t, 12, 0, 12, 0),
        bgcolor=t.palette.surface_2, border_radius=t.radius("control"),
        content=ft.Row(spacing=t.sp("1"),
                       vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=row_controls),
    )
    bar._bib_batch_bar = True
    return bar


# ── 写库骨架 ────────────────────────────────────────────────────────────────
def _as_control(value: Any) -> ft.Control:
    """参数/预览单控件或控件列统一成 Column。"""
    if isinstance(value, (list, tuple)):
        return ft.Column(controls=list(value), spacing=12, expand=True,
                         horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    return value


def write_layout(t: Tokens, *, params: Any, preview: Any, confirm: Any = None,
                 narrow: bool = False, params_width: float = PARAMS_W) -> ft.Control:
    """写库两栏：左参数 356 / 右预览 flex，1000 宽退单列。

    为什么确认条放在右栏底部而不是页面最底：设计稿里它只覆盖预览区
    （x=616..1136），左侧参数区仍能看见“期/篇”上下文；如果做成通栏，参数区最后
    一行会被 56 高的条子压住。窄屏则参数→预览→确认竖排，确认条自然通栏。
    竖线用右栏 border-left 而不是单独 1px Container：Flet 里空 Container 在
    Row 中不会自动拉满高度，border 能稳定跟着右栏走。
    """
    params_ctl = _as_control(params)
    preview_ctl = _as_control(preview)

    if narrow:
        controls = [params_ctl, preview_ctl]
        if confirm is not None:
            controls.append(confirm)
        layout = ft.Column(spacing=t.sp("2"), expand=True,
                           horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                           controls=controls)
        layout._bib_narrow = True
        layout._bib_left = params_ctl
        layout._bib_preview = preview_ctl
        layout._bib_confirm = confirm
        return layout

    right_children: list[ft.Control] = [ft.Container(expand=True, content=preview_ctl)]
    if confirm is not None:
        right_children.extend([ft.Container(height=t.sp("1")), confirm])
    right = ft.Container(
        expand=True, padding=_pad(t, 16, 0, 0, 0), border=ft.Border(left=_side(t)),
        content=ft.Column(spacing=0, expand=True,
                          horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                          controls=right_children),
    )
    layout = ft.Row(spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[ft.Container(width=t.px(params_width), content=params_ctl), right])
    layout._bib_narrow = False
    layout._bib_left = params_ctl
    layout._bib_preview = preview_ctl
    layout._bib_right = right
    layout._bib_confirm = confirm
    return layout


def _band_item(t: Tokens, item: Any) -> tuple[str, str, str | None, str | None]:
    """摘要带条目归一：dict 或 (label, value, hint, tone)。"""
    if isinstance(item, dict):
        return (str(item.get("label", "")), _text_of(item.get("value")),
                item.get("hint"), item.get("tone"))
    if isinstance(item, (tuple, list)):
        label = str(item[0]) if len(item) > 0 else ""
        value = _text_of(item[1]) if len(item) > 1 else ""
        hint = item[2] if len(item) > 2 else None
        tone = item[3] if len(item) > 3 else None
        return label, value, hint, tone
    raise TypeError("summary_band 的条目必须是 dict 或 (label, value[, hint[, tone]])")


def summary_band(t: Tokens, items: Sequence[Any], *, height: float = SUMMARY_H,
                 dense: bool = False) -> ft.Container:
    """写库摘要带 58：等宽四格 + 竖发丝线，数值用 18/600。

    为什么单独一块：预览表格只回答“改哪条”，摘要带回答“总共多少条”；把两者
    混在一张表里会让用户以为 12/8/3/1 也是可点的行。``dense=True`` 给 1000 宽
    单列用（52 高、15 号值），避免四个格子把确认条顶出视口。
    """
    p = t.palette
    controls: list[Any] = []
    rows = [_band_item(t, item) for item in items]
    for i, (label, value, hint, tone) in enumerate(rows):
        if i:
            controls.append(ft.Container(width=max(1, t.px(1)), height=t.px(36),
                                         bgcolor=p.border))
        value_color = _soft(t, tone)[1] if tone else p.text
        col_controls = [
            C.text(label, t, "caption", p.text_faint, max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS),
            C.text(value, t, "section" if dense else "h2", value_color,
                   weight="semibold", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        ]
        if hint:
            col_controls.append(C.text(hint, t, "caption", p.text_faint, max_lines=1,
                                       overflow=ft.TextOverflow.ELLIPSIS))
        controls.append(ft.Container(
            expand=1, padding=_pad(t, 12, 0, 12, 0),
            content=ft.Column(spacing=t.sp("half"), alignment=ft.MainAxisAlignment.CENTER,
                              horizontal_alignment=ft.CrossAxisAlignment.START,
                              controls=col_controls),
        ))

    band = ft.Container(
        height=t.px(52 if dense else height), bgcolor=p.surface_2,
        border_radius=t.radius("sm"), padding=_pad(t, 0, 6, 0, 6),
        content=ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=controls),
    )
    band._bib_band = True
    return band


def _detail_line(t: Tokens, text: str) -> ft.Text:
    """确认条第二行：单行省略，保证 56 高只占一行。"""
    return C.text(text, t, "caption", t.palette.text_faint, max_lines=1,
                  overflow=ft.TextOverflow.ELLIPSIS)


def confirm_bar(t: Tokens, *, consequence: str, detail: str | None = None,
                sha: str | None = None, on_confirm=None, confirm_label: str = "确认写入",
                confirm_disabled: bool = False, on_repreview=None,
                repreview_label: str = "重新预览", stale: bool = False) -> ft.Container:
    """写库确认条 56：左后果 + sha，右次/主按钮；stale 时主按钮换成重新预览。

    为什么后果必须写在这里：确认按钮旁边只放“确认写入”等于赌用户记得预览内容。
    左侧第一行说清“将新增几条/更新几条/冲突几条”，第二行固定写“只写
    bibliography.jsonl，不 push”并附库 sha——写库是原子且可回退的，用户需要
    知道边界。stale 时禁用确认、把重新预览提为主按钮，避免拿旧 sha 覆盖新库。
    """
    p = t.palette
    detail_text = detail or "只写 bibliography.jsonl，不 push"
    if sha:
        detail_text = f"库 sha {sha} · {detail_text}"

    if on_repreview is None:
        rep_btn = C.secondary_button(repreview_label, None, t, height=28)
        rep_btn.disabled = True
    elif stale:
        rep_btn = C.primary_button(repreview_label, on_repreview, t, height=28)
    else:
        rep_btn = C.secondary_button(repreview_label, on_repreview, t, height=28)

    if stale:
        conf_btn = C.secondary_button(confirm_label, on_confirm, t, height=28,
                                      disabled=True)
        conf_btn.tooltip = "库已变化，先重新预览"
    else:
        conf_btn = C.primary_button(confirm_label, on_confirm, t, height=28,
                                    disabled=confirm_disabled or on_confirm is None)

    bar = ft.Container(
        height=t.px(CONFIRM_H), padding=_pad(t, 12, 0, 12, 0),
        bgcolor=p.surface_2, border=ft.Border.all(1, p.border),
        border_radius=t.radius("control"),
        content=ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
                           ft.Column(spacing=t.sp("half"), expand=True,
                                     controls=[
                                         C.text(consequence, t, "section-label", p.text,
                                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                         _detail_line(t, detail_text),
                                     ]),
                           ft.Row(spacing=t.sp("1"),
                                  vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                  controls=[rep_btn, conf_btn]),
                       ]),
    )
    bar._bib_confirm_bar = True
    bar._bib_confirm_btn = conf_btn
    bar._bib_repreview_btn = rep_btn
    bar._bib_stale = stale
    return bar


def _banner_44(t: Tokens, tone: str, icon: str, fallback: str, title: str,
               detail: str | None = None, actions: Sequence[Any] = ()) -> ft.Container:
    """44 高语气横幅：图标 + 标题/说明 + 右侧动作，七态共用。"""
    bg, fg = _soft(t, tone)
    text_color = t.palette.text if tone == "neutral" else fg
    body = [C.text(title, t, "section-label", text_color, max_lines=1,
                   overflow=ft.TextOverflow.ELLIPSIS)]
    if detail:
        body.append(C.text(detail, t, "caption", t.palette.text_muted, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS))
    acts = _as_controls(actions)
    banner = ft.Container(
        height=t.px(BANNER_H), bgcolor=bg, border_radius=t.radius("control"),
        padding=_pad(t, 12, 0, 12, 0),
        content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
                           ft.Icon(C.ic(icon, fallback), size=t.px(16), color=text_color),
                           ft.Column(spacing=0, expand=True,
                                     alignment=ft.MainAxisAlignment.CENTER,
                                     controls=body),
                           *acts,
                       ]),
    )
    banner._bib_state_banner = True
    return banner


def result_banner(t: Tokens, *, title: str = "写入完成", detail: str | None = None,
                  on_review=None, on_revert=None, tone: str = "success") -> ft.Container:
    """写入结果横幅 44：去待处理 / 回退本次写入，两个动作都必须在原位。

    为什么不做 toast：写库成功后用户下一步要么去看待处理、要么撤销；toast 3 秒
    消失，用户还没读完就没了。横幅留在预览区顶部，直到用户操作或离开页面。
    """
    acts: list[Any] = []
    if on_review is not None:
        acts.append(C.text_button("去待处理", on_review, t, height=28))
    if on_revert is not None:
        acts.append(C.text_button("回退本次写入", on_revert, t, height=28, icon="UNDO"))
    banner = _banner_44(t, tone, "CHECK_CIRCLE_OUTLINE", "CHECK_CIRCLE", title, detail, acts)
    banner._bib_result_banner = True
    banner._bib_review_btn = acts[0] if on_review is not None else None
    banner._bib_revert_btn = acts[-1] if on_revert is not None else None
    return banner


def stale_notice(t: Tokens, *, expected_sha: str | None = None, actual_sha: str | None = None,
                 on_repreview=None, title: str | None = None,
                 detail: str | None = None) -> ft.Container:
    """过期提示：库 sha 变了就禁写，并解释“预览基于哪个 sha、当前是哪个”。

    为什么比一行红字更厚：用户看到“过期”第一反应是关页面重来；把两个 sha 并排
    写出来，才能让人相信草稿没丢、重新预览即可继续。重新预览按钮固定在这里，
    与确认条的“主按钮”指向同一个动作。
    """
    if detail is None:
        if expected_sha or actual_sha:
            detail = f"预览基于 {expected_sha or '未知'}；当前库为 {actual_sha or '未知'}。"
        else:
            detail = "预览后库文件被其他操作修改，本次不会写入。"
    acts = []
    if on_repreview is not None:
        acts.append(C.secondary_button("重新预览", on_repreview, t, height=28))
    box = ft.Container(
        bgcolor=t.palette.error_soft, border_radius=t.radius("control"),
        padding=_pad(t, 12, 8, 12, 8),
        content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
                           _tone_icon(t, "error", 16),
                           ft.Column(spacing=t.sp("half"), expand=True,
                                     controls=[
                                         C.text(title or "库已被其他操作修改", t, "section-label",
                                                t.palette.error, max_lines=1,
                                                overflow=ft.TextOverflow.ELLIPSIS),
                                         C.text(detail, t, "caption", t.palette.text_muted,
                                                max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                                     ]),
                           *acts,
                       ]),
    )
    box._bib_stale = True
    return box


# ── 状态 / 规则 ─────────────────────────────────────────────────────────────
def section(t: Tokens, label: str) -> ft.Column:
    """分区标题：11.5/600 标签 + 1px 发丝线，线在标签下方 6px。

    为什么不用 ``C.card`` 包一层：分区是“纸上的横线”，不是卡片；一屏 4 个分区
    若都加圆角描边，页面会重新变成卡片流，直接违反设计稿 §9.1。
    """
    return ft.Column(
        spacing=t.sp("half") + t.px(2),
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        controls=[C.section_label(label, t), C.hairline(t)],
    )


def _skeletons(t: Tokens, count: int = 3) -> list[ft.Control]:
    """加载骨架 3 行：短条而非转圈，避免布局跳动。"""
    widths = (360, 280, 200)
    rows = []
    for i in range(count):
        rows.append(ft.Container(
            height=t.px(28), alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Container(
                width=t.px(widths[i % len(widths)]), height=t.px(10),
                bgcolor=t.palette.surface_2, border_radius=t.radius("mark"),
            ),
        ))
    return rows


def _copy_button(t: Tokens, text: str, page, on_copy):
    """复制 stderr：默认 page.run_task + Clipboard，也可调用方接管。"""
    if on_copy is not None:
        return C.text_button("复制 stderr", lambda e: on_copy(text), t,
                             icon="CONTENT_COPY", height=28)
    if page is None or not hasattr(page, "run_task"):
        return None

    def _click(e) -> None:
        """点击后把 stderr 写进剪贴板。"""
        async def _job() -> None:
            """协程体：登记 Clipboard 服务后写入。"""
            clipboard = ft.Clipboard()
            page.services.append(clipboard)
            await clipboard.set(text)

        page.run_task(_job)

    return C.text_button("复制 stderr", _click, t, icon="CONTENT_COPY", height=28)


def state_banner(t: Tokens, kind: str, *, message: str | None = None,
                 detail: str | None = None, stage: str | None = None,
                 action: Any = None, next_label: str = "下一步", on_next=None,
                 on_cancel=None, stderr: str | None = None, page=None,
                 on_copy=None, expected_sha: str | None = None,
                 actual_sha: str | None = None, on_repreview=None) -> ft.Control:
    """七态统一出口：empty/loading/running/success/failure/cancelled/stale。

    为什么一个函数管七态：十个视图各写各的“空/失败/取消”最容易漏掉可复制 stderr
    或“取消后继续”。这里按状态返回不同高度的控件——空态 44 一行、加载 3 行骨架、
    运行/成功/失败/取消 44 横幅、过期约 50 的说明块——页面只需 ``if`` 选 kind。
    failure 的 stderr 默认接 ``page.run_task`` 写剪贴板（与 ``bib_advanced`` 同一
    服务模式）；也可以传 ``on_copy`` 自己处理。
    """
    aliases = {"error": "failure", "ok": "success", "canceled": "cancelled"}
    kind = aliases.get(kind, kind)
    if kind not in ("empty", "loading", "running", "success", "failure", "cancelled", "stale"):
        raise ValueError(f"未知状态：{kind!r}")

    def _next_action(default_label: str):
        """优先用调用方给的 action，否则按需造一个扁平按钮。"""
        if action is not None:
            return action
        if on_next is not None:
            return C.text_button(default_label, on_next, t, height=28)
        return None

    if kind == "empty":
        box = ft.Container(
            height=t.px(BANNER_H), padding=_pad(t, 8, 0, 8, 0),
            content=C.empty_line(message or "没有匹配记录", t,
                                 action=_next_action(next_label)),
        )
        box._bib_state = kind
        return box

    if kind == "loading":
        box = ft.Column(spacing=t.sp("1"),
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        controls=[C.busy_bar(t, visible=True, width=160), *_skeletons(t)]) 
        box._bib_state = kind
        box._bib_skeletons = 3
        return box

    if kind == "running":
        bg, fg = _soft(t, "info")
        box = ft.Container(
            height=t.px(BANNER_H), bgcolor=bg, border_radius=t.radius("control"),
            padding=_pad(t, 12, 0, 12, 0),
            content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[
                               C.busy_bar(t, visible=True, width=80),
                               ft.Column(spacing=0, expand=True,
                                         alignment=ft.MainAxisAlignment.CENTER,
                                         controls=[
                                             C.text(stage or message or "运行中…", t,
                                                    "section-label", fg, max_lines=1,
                                                    overflow=ft.TextOverflow.ELLIPSIS),
                                             *([C.text(detail, t, "caption",
                                                       t.palette.text_muted, max_lines=1)]
                                               if detail else []),
                                         ]),
                               *([C.text_button("取消", on_cancel, t,
                                                icon="STOP_CIRCLE", height=28)]
                                 if on_cancel is not None else []),
                           ]),
        )
        box._bib_state = kind
        box._bib_cancel = on_cancel
        return box

    if kind == "success":
        box = _banner_44(t, "success", "CHECK_CIRCLE_OUTLINE", "CHECK_CIRCLE",
                         message or "已完成", detail,
                         [a for a in (_next_action(next_label),) if a is not None])
        box._bib_state = kind
        return box

    if kind == "cancelled":
        box = _banner_44(t, "warn", "UNDO", "UNDO", message or "已取消", detail,
                         [a for a in (_next_action("继续"),) if a is not None])
        box._bib_state = kind
        return box

    if kind == "stale":
        box = stale_notice(t, expected_sha=expected_sha, actual_sha=actual_sha,
                           on_repreview=on_repreview or on_next, title=message, detail=detail)
        box._bib_state = kind
        return box

    # failure
    copy_btn = _copy_button(t, stderr or "", page, on_copy) if stderr else None
    acts = [a for a in (copy_btn, _next_action("重试")) if a is not None]
    banner = _banner_44(t, "error", "ERROR_OUTLINE", "ERROR", message or "运行失败",
                        detail or ("可复制 stderr 后重试" if stderr else None), acts)
    if not stderr:
        banner._bib_state = kind
        return banner
    stderr_box = ft.Container(
        bgcolor=t.palette.log_bg, border=ft.Border.all(1, t.palette.border),
        border_radius=t.radius("control"), padding=_pad(t, 10, 8, 10, 8),
        content=C.text(stderr, t, role="log", color=t.palette.log_text,
                       selectable=True, max_lines=6, overflow=ft.TextOverflow.ELLIPSIS),
    )
    stack = ft.Column(spacing=t.sp("1"), horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                      controls=[banner, stderr_box])
    stack._bib_state = kind
    stack._bib_stderr = stderr_box
    return stack


def danger_confirm(page, t: Tokens, *, title: str, message: str, impact: int | str,
                   confirm_label: str = "确认", on_confirm=None,
                   cancel_label: str = "取消") -> ft.AlertDialog:
    """危险操作二次确认：红色主按钮 + 影响范围，必须带条数/范围。

    为什么强制 ``impact``：设计稿 §9.9 要求危险动作第二道确认里出现“影响 N 条”。
    没有范围描述的确认框只是多一次点击，用户会条件反射地点“确认”。这里把
    impact 放进 error_soft 高亮块，并在调用方漏传时直接报错，防止悄悄退化。
    对话框登记到 ``components._open_dialogs``，主壳的 Esc 才能关掉它；本函数
    不改 components.py，只复用它的官方对话框栈。
    """
    if impact is None:
        raise ValueError("danger_confirm 必须给 impact（影响条数/范围）")
    impact_text = f"将影响 {impact} 条记录" if isinstance(impact, int) else str(impact)
    p = t.palette

    dialog = ft.AlertDialog(
        modal=True,
        title=C.text(title, t, role="dialog-title"),
        content=ft.Container(width=t.px(360), content=ft.Column(
            spacing=t.sp("2"), tight=True,
            controls=[
                C.text(message, t, "body", p.text_muted),
                ft.Container(
                    bgcolor=p.error_soft, border_radius=t.radius("control"),
                    padding=_pad(t, 10, 8, 10, 8),
                    content=C.text(impact_text, t, "small", p.error, weight="medium"),
                ),
            ],
        )),
        actions=[
            C.text_button(cancel_label, lambda e: _close(), t),
            C.danger_button(confirm_label, lambda e: _ok(), t),
        ],
    )

    registry = getattr(C, "_open_dialogs", None)

    def _close() -> None:
        """先出组件对话框栈，再关页面对话框，Esc 才不会错位。"""
        if isinstance(registry, list) and registry and registry[-1] is dialog:
            registry.pop()
        page.pop_dialog()

    def _ok() -> None:
        """关闭后按回调签名调用（零参或一参都兼容）。"""
        _close()
        if on_confirm is not None:
            try:
                params = inspect.signature(on_confirm).parameters
            except (TypeError, ValueError):
                params = {}
            if params:
                on_confirm(None)
            else:
                on_confirm()

    if isinstance(registry, list):
        registry.append(dialog)
    dialog._bib_danger = True
    dialog._bib_impact = impact_text
    page.show_dialog(dialog)
    return dialog
