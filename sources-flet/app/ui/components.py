"""B-246c P1 · 可复用组件库（D3）。

全部组件只吃 `Tokens`（颜色/间距/字号/缩放都从 token 来），因此：
- 换主题 = 换一张色板重建，不改组件；
- 换 DPI / 用户缩放 = 换一个 `Tokens.k` 重建，组件代码零改动；
- 本模块不碰业务状态，页面只负责把回调接进来。
"""
from __future__ import annotations

import asyncio
import inspect
import re
import time
from types import SimpleNamespace

import flet as ft

from ui.tokens import MONO_ROLES, TONE_TAG, Tokens

# ── 小工具 ──────────────────────────────────────────────────────────────────
def ic(name, fallback: str = "CIRCLE"):
    """图标名容错：Flet 版本差异下缺图标只降级，不让界面起不来。

    已解析的 IconData 原样返回（调用方可能先 C.ic("SEARCH") 再交给 helper）。
    """
    if not isinstance(name, str):
        return name
    return getattr(ft.Icons, name, getattr(ft.Icons, fallback, ""))


def _w(t: Tokens, key: str):
    return getattr(ft.FontWeight, t.weight(key))


def _pad(t: Tokens, left: float = 0, top: float = 0, right: float = 0, bottom: float = 0) -> ft.Padding:
    return ft.Padding(left=t.px(left), top=t.px(top), right=t.px(right), bottom=t.px(bottom))


def _side(t: Tokens, color: str | None = None, width: float = 1) -> ft.BorderSide:
    return ft.BorderSide(max(1, round(width * t.k)), color or t.palette.border)


def box(t: Tokens, color: str | None = None, width: float = 1) -> ft.Border:
    side = _side(t, color, width)
    return ft.Border(left=side, top=side, right=side, bottom=side)


# ── 一键清空（批 114 用户 m03131：全壳输入框都要能清空，下沉到 helper）────────
def _clear_suffix_button(t: Tokens) -> ft.IconButton:
    """输入框右侧的紧凑 ✕：24 盒高，不改变输入框自身盒型（48/28 仍由字段决定）。"""
    p = t.palette
    return ft.IconButton(
        icon=ic("CLOSE"),
        tooltip="清空",
        icon_size=t.px(14),
        icon_color=p.text_faint,
        width=t.px(24),
        height=t.px(24),
        style=ft.ButtonStyle(padding=0),
    )


def _full_value_tooltip(value) -> str | None:
    """长值的悬停全文（R01）：空值不给 tooltip，避免空气泡。"""
    raw = "" if value is None else str(value).strip()
    return raw or None


def sync_clearable(field: ft.TextField) -> None:
    """按当前值切换 ✕ 的显隐（程序化赋值后调用；不触发 on_change）。

    R01：带 `_value_tooltip` 标记的字段（`path_picker` 挂的）同时把当前值全文同步到
    `tooltip`——被截断的长路径仍可悬停看全。值没变就不发 update，不增加噪声。
    """
    if getattr(field, "_value_tooltip", False):
        wanted = _full_value_tooltip(getattr(field, "value", ""))
        if getattr(field, "tooltip", None) != wanted:
            field.tooltip = wanted
            try:
                field.update()          # 输入中/回填后立刻生效；未挂载时吞掉
            except Exception:  # noqa: BLE001
                pass
    button = getattr(field, "_clear_button", None)
    if button is None:
        return
    visible = bool(str(getattr(field, "value", "") or "").strip())
    if button.visible == visible:
        return
    button.visible = visible
    try:
        button.update()
    except Exception:  # noqa: BLE001 构建期控件可能未挂载
        pass


def make_clearable(field: ft.TextField, t: Tokens, *, on_clear=None) -> ft.TextField:
    """给可编辑 TextField 挂「一键清空」✕（有值才显示）。

    清空时：值 → ""、error → None、调用 `on_clear`（页面同步 store/mark），
    再用合成的 `SimpleNamespace(control=field)` 事件触发原 `on_change`，
    保证 store / 校验 / 过滤等既有链路照常运行。

    不挂的两类（审计表已记录）：只读字段；`password + can_reveal_password`
    字段——flet 1.0.5 规定 reveal 图标占用 suffix 位、两者同时给时 suffix 不显示
    （见 flet material/textfield.py:413-415），这类字段由页面保留显式「清除」按钮。
    """
    if (field.read_only or (field.password and field.can_reveal_password)
            or getattr(field, "_clearable", False)):
        return field
    user_on_change = field.on_change
    button = _clear_suffix_button(t)

    def _clear(e=None) -> None:
        field.value = ""
        field.error = None
        if on_clear is not None:
            on_clear()
        if user_on_change is not None:
            user_on_change(SimpleNamespace(control=field))
        sync_clearable(field)
        for ctl in (field, button):
            try:
                ctl.update()
            except Exception:  # noqa: BLE001
                pass

    def _wrapped(e):
        if user_on_change is not None:
            user_on_change(e)
        sync_clearable(field)

    button.on_click = _clear
    existing_suffix = getattr(field, "suffix_icon", None)
    button.visible = bool(str(field.value or "").strip())
    if existing_suffix is not None:
        # 既有后缀图标（少数单位/后缀字段）与 ✕ 并排，避免互相覆盖。
        field.suffix = ft.Row(
            spacing=t.px(2), tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[ft.Icon(existing_suffix, size=t.px(16),
                              color=t.palette.text_faint), button])
        field.suffix_icon = None
    else:
        field.suffix = button
    field.on_change = _wrapped
    field._user_on_change = user_on_change
    field._clear_button = button
    field._clearable = True
    return field


def text(value: str, t: Tokens, size: str = "body", color: str | None = None,
         weight: str = "regular", *, role: str | None = None, **kwargs) -> ft.Text:
    """唯一的文字出口：给了 role 就用 tokens 里的字号+字重（忽略 size/weight）。"""
    font_family = kwargs.pop("font_family", None)
    if role is not None:
        size, weight = t.text_role(role)
        if font_family is None and role in MONO_ROLES:
            font_family = t.mono_family
    if font_family:
        kwargs["font_family"] = font_family
        # Flet 的 font_family 只收单个族名；回退必须显式给（批 112 GUI-07）。
        if font_family == t.mono_family:
            kwargs.setdefault("font_family_fallback", list(t.mono_fallback))
    return ft.Text(
        value, size=t.font(size), color=color or t.palette.text,
        weight=_w(t, weight), **kwargs,
    )


def divider(t: Tokens) -> ft.Divider:
    return ft.Divider(height=1, thickness=1, color=t.palette.border)


# ── 容器 / 标题区 ───────────────────────────────────────────────────────────
def card(content, t: Tokens, *, pad: float = 16, bgcolor: str | None = None,
         bordered: bool = True, radius: str = "md", expand: bool = False,
         width: float | None = None, on_click=None) -> ft.Container:
    return ft.Container(
        content=content,
        bgcolor=bgcolor or t.palette.surface,
        border=box(t) if bordered else None,
        border_radius=t.radius(radius),
        padding=_pad(t, pad, pad, pad, pad),
        expand=expand,
        width=width,
        on_click=on_click,
    )


def section_title(title: str, t: Tokens, note: str | None = None) -> ft.Row:
    if note:
        return ft.Row(
            controls=[text(title, t, role="section"),
                      ft.Container(expand=True),
                      text(note, t, "caption", t.palette.text_faint)],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
    return ft.Row(controls=[text(title, t, role="section")])


def page_header(title: str, subtitle: str, t: Tokens, actions: list | None = None) -> ft.Row:
    left = ft.Column(
        spacing=t.sp("half"),
        controls=[text(title, t, role="page-title"),
                  text(subtitle, t, role="page-subtitle", color=t.palette.text_muted)],
    )
    return ft.Row(
        controls=[ft.Container(content=left, expand=True),
                  ft.Row(controls=actions or [], spacing=t.sp("1"),
                         vertical_alignment=ft.CrossAxisAlignment.CENTER)],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


#: 常显滚动条宽度（设计像素）。批 118 P1-1 全壳统一；批 120 B-352 唯一实现。
SCROLLBAR_THICKNESS = 6


def scrollbar(t: Tokens, *, thickness: float = SCROLLBAR_THICKNESS,
              horizontal: bool = False, radius: float = 3.0) -> ft.Scrollbar:
    """统一滚动条配置：静止也显示细条、可拖拽；无溢出时 Flet 不画条。

    批 120 B-352：`bib_shell.scrollbar()` 合并进本函数，全壳唯一实现。
    flet 1.0.5 的 `Scrollbar` 是 `scroll=` 的配置对象，不是包裹控件；
    `thumb_visibility=True` 即「ALWAYS」语义（实测无溢出时不画条）。
    `thickness`/`radius` 走设计像素，随 DPI / 用户缩放一起缩放；
    `horizontal=True` 时横条贴底（`ScrollbarOrientation.BOTTOM`）。
    """
    return ft.Scrollbar(
        thumb_visibility=True,
        thickness=t.px(thickness),
        radius=t.px(radius),
        interactive=True,
        orientation=(ft.ScrollbarOrientation.BOTTOM if horizontal
                     else ft.ScrollbarOrientation.RIGHT),
    )


def page_body(*controls, t: Tokens, gap: float = 16, overlay=None,
              overlay_reserve: float = 0, panel: bool = False,
              pad: tuple | None = None, tail: ft.Control | None = None) -> ft.Control:
    """页面内容区。给了 `overlay` 就改成 Stack：内容在下、浮层在上（后入 = z 更高）。

    `overlay_reserve` 是给浮层留出的底部内边距（px），避免浮层遮住内容最后一行。
    浮层自身用 `left/right/bottom=0` 贴住内容区底部（VSCode 输出面板式）。

    `panel=True`（批 115 D1-115-12）：「一张纸」白面板——内容盒 bgcolor = surface、
    顶部圆角 `t.radius("panel")`，与 `C.panel` 观感一致；padding / 滚动 / overlay
    行为不变。默认 False（灰底，老页面零变化）。

    批 118 P1-1：滚动统一用 `scrollbar(t)`（常显细条），并在滚动内容右缘留
    `SCROLLBAR_THICKNESS` 设计像素，避免覆盖式细条压住卡片边；`pad` 可覆盖
    内容盒内边距（Home 沿用 `panel` 的 24/12/24/10）。

    `tail` 是留在内容右侧留白之外、但仍随页面滚动的尾控件（日志页的
    动态底部 spacer）；滚动列仍以它为最后一个 control，保持收起/展开契约。
    """
    inner = ft.Column(
        controls=list(controls),
        spacing=t.sp("2") if gap == 16 else t.px(gap),
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    scroll_controls = [ft.Container(
        content=inner,
        padding=_pad(t, 0, 0, SCROLLBAR_THICKNESS, 0),
    )]
    if tail is not None:
        scroll_controls.append(tail)
    body = ft.Column(
        controls=scroll_controls,
        spacing=0,
        scroll=scrollbar(t),
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    if overlay is not None and overlay_reserve:
        body.controls.append(ft.Container(height=t.px(overlay_reserve)))
    left, top, right, bottom = pad or (24, 12, 24, 18)
    box = ft.Container(
        padding=_pad(t, left, top, right, bottom), expand=True, content=body,
        bgcolor=t.palette.surface if panel else t.palette.bg,
        **({"border_radius": ft.BorderRadius(
            top_left=t.radius("panel"), top_right=t.radius("panel"),
            bottom_left=0, bottom_right=0)} if panel else {}),
    )
    if overlay is None:
        return box
    return ft.Stack(controls=[box, overlay], expand=True)


# ── S1m-native 版式：内容面板 / 分区 / 单行提示 ─────────────────────────────
def panel(*controls, t: Tokens, gap: float = 0, pad: tuple = (24, 18, 24, 12),
          expand: bool = True, bgcolor: str | None = None) -> ft.Container:
    """内容面板：白底 + 顶部圆角 10（S1m 的「一张纸」，不再是卡片流）。

    只做布局，不负责滚动；页面要滚动时自己在内部放 ListView（固定表头等）。
    """
    body = ft.Column(
        controls=list(controls), spacing=t.px(gap), expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    return ft.Container(
        content=body, expand=expand,
        bgcolor=bgcolor or t.palette.surface,
        border_radius=ft.BorderRadius(
            top_left=t.radius("panel"), top_right=t.radius("panel"),
            bottom_left=0, bottom_right=0),
        padding=_pad(t, *pad),
    )


def block(*controls, t: Tokens, gap: str = "s", expand: bool = False) -> ft.Column:
    """无底色的纵向块（分区内容），间距用 token 键。"""
    return ft.Column(
        controls=list(controls), spacing=t.sp(gap), expand=expand,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )


def section_label(title: str, t: Tokens, *, trailing=None, note: str | None = None) -> ft.Row:
    """11.5/600 分区小标题（不是 h3 标题，更不是卡片标题）。"""
    controls = [text(title, t, role="section-label", color=t.palette.text_muted)]
    if trailing is not None:
        controls.extend([ft.Container(expand=True), trailing])
    elif note:
        controls.extend([ft.Container(expand=True),
                         text(note, t, "caption", t.palette.text_faint)])
    return ft.Row(controls=controls, spacing=t.sp("1"),
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)


def status_dot(tone: str, t: Tokens, size: float = 6) -> ft.Container:
    """状态圆点；未知 tone 一律退成 idle 灰点（不抛错、不冒充成功）。

    映射（P08 I-1：`envcheck_rows` 的 `tone` 直接喂这里）：
    `ok`/`done`/`success` → 成功绿；`warn` → 警告黄；`error` → 错误红；
    `run` → accent（运行中）；`idle` → 最淡灰（未开始/停用）；
    `info`/`neutral` → 中性灰（「跳过 / 未参与结论」，**不是**绿色）。
    """
    p = t.palette
    color = {"ok": p.success, "done": p.success, "success": p.success,
             "warn": p.warn, "error": p.error, "run": p.accent,
             "idle": p.text_faint,
             "info": p.text_muted, "neutral": p.text_muted}.get(tone, p.text_faint)
    return ft.Container(width=t.px(size), height=t.px(size), bgcolor=color,
                        border_radius=t.radius("pill"))


def inline_note(message: str, t: Tokens, tone: str = "info", *, action=None) -> ft.Row:
    """单行行内提示：色点 + 一句话，不占底色、不弹窗（错误/警告统一走这里）。"""
    p = t.palette
    color = {"info": p.text_muted, "warn": p.warn, "error": p.error,
             "ok": p.success}.get(tone, p.text_muted)
    controls = [status_dot("warn" if tone == "warn" else tone if tone in ("error", "ok") else "idle", t),
                text(message, t, role="small", color=color, expand=True)]
    if action is not None:
        controls.append(action)
    return ft.Row(controls=controls, spacing=t.sp("1"),
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)


def empty_line(message: str, t: Tokens, *, action=None) -> ft.Row:
    """空态只留一行话（≤20 字）+ 一个动作，不再画 180 高的空状态插画。"""
    controls = [text(message, t, role="small", color=t.palette.text_faint, expand=True)]
    if action is not None:
        controls.append(action)
    return ft.Row(controls=controls, spacing=t.sp("1"),
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)


# ── 发丝线 / 键值行（批 115 P3-C 共享件，供去卡片后的页面复用）──────────────
def vline(t: Tokens, height: float = 16) -> ft.Container:
    """1px 竖直发丝线（统计带 / 入口列之间的分隔）；`height` 给设计像素。

    线宽与颜色同 `_side()`：缩放 < 1 时仍保 1 物理 px，不会淡成半像素。
    `data="tone:border"` 让 `main._retint_node` 换肤时按色板字段名精确改写
    （深色板里 border 与 surface_2 同值，纯值映射会认错角色）。
    """
    side = _side(t)
    return ft.Container(width=side.width, height=t.px(height), bgcolor=side.color,
                        data=f"{TONE_TAG}border")


def hairline(t: Tokens) -> ft.Container:
    """1px 水平发丝线（去卡片后的分段线，替代裸 `ft.Container` 与卡片描边）。

    宽度不写死：父 `Column` 用 `horizontal_alignment=STRETCH`（`page_body` /
    `panel` / `block` 已如此）即整宽拉伸。颜色/线宽同 `vline`，带 tone 标签。
    """
    side = _side(t)
    return ft.Container(height=side.width, bgcolor=side.color,
                        data=f"{TONE_TAG}border")


def kv_row(t: Tokens, label: str, value, mono: bool = False) -> ft.Container:
    """设置 / 帮助的键值行：32 高、label muted、value 右对齐且可截断。

    替代 Settings / Help 各自手写的 `_kv`（Bib 本批不动）。`mono=True` 走等宽角色
    （版本号 / 路径 / ID）；超长值不换行，`tooltip` 给全文。

    P08 I-2：返回的行容器同时挂 `.value_ctl`（value 那个 `ft.Text`），页面存住它就能
    原地刷新状态行（设置页「未保存改动」「设置文件」）：改 `value_ctl.value` +
    `value_ctl.tooltip` 后 `value_ctl.update()`，不必重建整页。走属性而**不是**新形参，
    是因为 `tests/test_b115_shared.py` 冻结了本函数的参数列表（`["t","label","value","mono"]`），
    既有调用与结构都不受影响。
    """
    raw = "" if value is None else str(value)
    label_ctl = text(label, t, "small", t.palette.text_muted,
                     max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    value_ctl = text(raw, t, role="mono" if mono else "small", color=t.palette.text,
                     max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                     tooltip=raw or None)
    row = ft.Container(
        height=t.row_h("table"),
        content=ft.Row(
            spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                label_ctl,
                ft.Container(expand=True),
                ft.Container(expand=True, alignment=ft.Alignment.CENTER_RIGHT,
                             content=value_ctl),
            ],
        ),
    )
    row.value_ctl = value_ctl       # P08 I-2：页面原地刷新状态行的句柄
    return row


# ── 表格：固定表头 + 内滚（不再依赖 page_body 的 Column 滚动）────────────────
def table_cell(value, t: Tokens, *, weight: float = 1, mono: bool = False,
               align=ft.TextAlign.LEFT, color: str | None = None,
               role: str | None = None) -> ft.Container:
    if isinstance(value, ft.Control):
        ctl = value
    elif role is not None:
        ctl = text(str(value), t, role=role, color=color,
                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    else:
        ctl = text(str(value), t, "mono" if mono else "table", color=color,
                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    return ft.Container(content=ctl, expand=max(1, int(round(weight))),
                        padding=_pad(t, 0, 0, 8, 0),
                        alignment=ft.Alignment.CENTER_LEFT if align == ft.TextAlign.LEFT
                        else ft.Alignment.CENTER_RIGHT)


def table_header(cells: list, t: Tokens, weights: list[float] | None = None) -> ft.Container:
    """表头 26 高、11/600 灰；与行同 padding，保证列对齐。"""
    weights = weights or [1] * len(cells)
    row = ft.Row(
        spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[table_cell(cell, t, weight=weights[i] or 1, role="table-head",
                             color=t.palette.text_faint)
                  for i, cell in enumerate(cells)],
    )
    return ft.Container(height=t.px(26), padding=_pad(t, 8, 0, 8, 0),
                        content=row)


def _row_hover(container: ft.Container, t: Tokens, selected: bool) -> None:
    def _hover(e) -> None:
        on = e.data in (True, "true", "True")
        if selected:
            container.bgcolor = t.palette.accent_soft
            container.data = f"{TONE_TAG}accent_soft"
        else:
            container.bgcolor = t.palette.surface_2 if on else None
            container.data = f"{TONE_TAG}surface_2" if on else None
        try:
            container.update()
        except Exception:  # noqa: BLE001
            pass
    container.on_hover = _hover


def table_row(cells: list, t: Tokens, *, weights: list[float] | None = None,
              selected: bool = False, on_click=None, height: float | None = None,
              mono: tuple[int, ...] = ()) -> ft.Container:
    """一行：整行可点；选中 = accent_soft 整行；hover = surface_2；行间 1px。"""
    weights = weights or [1] * len(cells)
    controls = []
    for i, cell in enumerate(cells):
        if isinstance(cell, ft.Control):
            controls.append(ft.Container(content=cell,
                                         expand=max(1, int(round(weights[i] or 1))),
                                         padding=_pad(t, 0, 0, 8, 0)))
        else:
            controls.append(table_cell(cell, t, weight=weights[i] or 1,
                                       mono=i in mono,
                                       color=t.palette.sel_text if selected else None))
    row = ft.Row(controls=controls, spacing=0,
                 vertical_alignment=ft.CrossAxisAlignment.CENTER)
    ctl = ft.Container(
        content=row, height=t.px(height or 32),
        padding=_pad(t, 8, 0, 8, 0),
        bgcolor=t.palette.accent_soft if selected else None,
        border=ft.Border(bottom=_side(t)),
        on_click=on_click, ink=on_click is not None,
        data=f"{TONE_TAG}accent_soft" if selected else None,
    )
    if on_click is not None:
        _row_hover(ctl, t, selected)
    return ctl


def table_panel(header: ft.Control, rows: list, t: Tokens, *,
                height: float | None = None, empty=None) -> ft.Control:
    """固定表头 + 表体内滚；高度由页面给（不参与外层的页面滚动）。"""
    body = ft.ListView(expand=True, spacing=0, padding=0,
                       controls=rows or [empty or empty_line("没有匹配结果", t)])
    column = ft.Column(spacing=0, expand=True,
                       controls=[header, ft.Container(content=body, expand=True)])
    if height is not None:
        return ft.Container(height=t.px(height), content=column)
    return column


# ── 按钮 ────────────────────────────────────────────────────────────────────
def _button_style(t: Tokens) -> ft.ButtonStyle:
    """控件统一：28 高 / 6 圆角 / 正文 13 / 图标 14（S1m-native）。

    与 main._flet_theme 的 label_large 双保险：theme 管全局（输入框/勾选/下拉标签），
    这里再显式给四个按钮补 text_style/icon_size，保证任何主题/版本下都同源缩放。
    """
    return ft.ButtonStyle(
        text_style=ft.TextStyle(size=t.font("body"), weight=ft.FontWeight.W_500),
        icon_size=t.px(14),
        shape=ft.RoundedRectangleBorder(radius=t.radius("control")),
        padding=ft.Padding(left=t.px(10), top=0, right=t.px(10), bottom=0),
    )


def primary_button(label: str, on_click, t: Tokens, *, icon: str | None = None,
                   disabled: bool = False, height: float | None = None) -> ft.FilledButton:
    """主按钮：C/D 两套配色由 token 决定；禁用态用同套 off 色。

    `height` 只在「和输入框同一行」时传：行高由输入框决定（浮动 48 / 紧凑 28），
    按钮跟随行高，见 `field_row()`；不传则用统一 control_h。
    """
    b = t.buttons
    style = _button_style(t)
    style.bgcolor = {
        ft.ControlState.DEFAULT: b.off_bg if disabled else b.fill,
        ft.ControlState.HOVERED: b.hover,
        ft.ControlState.PRESSED: b.press,
        ft.ControlState.DISABLED: b.off_bg,
    }
    style.color = {
        ft.ControlState.DEFAULT: b.off_fg if disabled else b.on,
        ft.ControlState.DISABLED: b.off_fg,
    }
    return ft.FilledButton(
        label,
        icon=ic(icon) if icon else None,
        height=height if height is not None else t.control_h(),
        style=style,
        on_click=on_click,
        disabled=disabled,
    )


def secondary_button(label: str, on_click, t: Tokens, *, icon: str | None = None,
                     disabled: bool = False, height: float | None = None) -> ft.OutlinedButton:
    p = t.palette
    style = _button_style(t)
    # 用户 m02990：次操作的禁用态必须一眼看出不可点（此前只有一个 muted 色，禁用和正常同色）。
    style.color = {
        ft.ControlState.DEFAULT: p.text_muted,
        ft.ControlState.HOVERED: p.text,
        ft.ControlState.DISABLED: p.text_faint,
    }
    style.side = {
        ft.ControlState.DEFAULT: _side(t),
        ft.ControlState.HOVERED: _side(t, p.border_strong),
        ft.ControlState.DISABLED: _side(t),
    }
    return ft.OutlinedButton(
        label,
        icon=ic(icon) if icon else None,
        height=height if height is not None else t.control_h(),
        style=style,
        on_click=on_click,
        disabled=disabled,
    )


def text_button(label: str, on_click, t: Tokens, *,
                icon: str | None = None, height: float | None = None) -> ft.TextButton:
    # 批 112 GUI-09：TextButton 同样吃 labelLarge，显式锚定正文 role。
    # 批 114：加 `icon`，让「预览元数据」这类动作从灰底按钮降级为扁平图标+文字。
    # 批 114 打磨（m02816）：`height` 用于和输入框同行的场景，与行内输入框等高。
    style = _button_style(t)
    style.color = t.buttons.link
    style.padding = ft.Padding(left=t.px(4), top=0, right=t.px(4), bottom=0)
    return ft.TextButton(label, icon=ic(icon) if icon else None,
                         height=height if height is not None else t.px(26),
                         style=style, on_click=on_click)


def toggle_style(t: Tokens, *, active: bool = False) -> ft.ButtonStyle:
    """扁平开关按钮的样式：选中只变字色（accent），不铺灰 pill／灰底块（m02696）。"""
    style = _button_style(t)
    accent = t.palette.accent
    muted = t.palette.text_muted
    style.color = {
        ft.ControlState.DEFAULT: accent if active else muted,
        ft.ControlState.HOVERED: accent if active else t.palette.text,
    }
    style.padding = ft.Padding(left=t.px(6), top=0, right=t.px(6), bottom=0)
    return style


def toggle_button(label: str, on_click, t: Tokens, *, icon: str | None = None,
                  active: bool = False, tooltip: str | None = None,
                  height: float | None = None) -> ft.TextButton:
    """裸开关按钮（图标+文字）：语义是开关，视觉是扁平按钮。

    LogConsole「只看摘要」用它替换 Material Switch —— 后者 48 宽轨道 + 灰 pill 正是
    用户 m02696 点名的灰底块。选中态只改字色，不换底。
    """
    btn = ft.TextButton(
        label,
        icon=ic(icon) if icon else None,
        height=height if height is not None else t.px(26),
        style=toggle_style(t, active=active),
        on_click=on_click,
        tooltip=tooltip,
    )
    btn._toggle_active = bool(active)
    return btn


def danger_button(label: str, on_click, t: Tokens, *, icon: str | None = None,
                  disabled: bool = False, height: float | None = None) -> ft.TextButton:
    """危险动作：tint 式（danger_soft 底 + danger 字），不用大红块。"""
    b = t.buttons
    style = _button_style(t)
    # P1-6：tint 只在可用态；disabled 必须是灰底灰字，不能仍亮红。
    style.color = {ft.ControlState.DEFAULT: b.danger,
                   ft.ControlState.HOVERED: b.danger,
                   ft.ControlState.DISABLED: b.off_fg}
    style.bgcolor = {ft.ControlState.DEFAULT: b.danger_soft,
                     ft.ControlState.HOVERED: b.danger_soft,
                     ft.ControlState.DISABLED: b.off_bg}
    return ft.TextButton(label, icon=ic(icon) if icon else None,
                         height=height if height is not None else t.control_h(),
                         style=style, on_click=on_click, disabled=disabled)


# ── 表单控件（批 114 打磨：输入 / 下拉统一 28 高、纸色底、同圆角）─────────────
# 用户 m02696：全壳输入框的 surface_2 灰底块不可接受 → 一律「纸色底 + 1px 发丝描边」，
# 与浮动标签输入框同一材质；聚焦只换描边色，不再铺灰。开关只在需要开关语义处保留。
# 四个出口共用同一份几何：height=control_h / radius=control / 正文 13 / 左右 8 内边距。
# 多行文本只放宽高度，不另起一套视觉。
def text_field(t: Tokens, *, value: str = "", hint_text: str | None = None,
               on_change=None, on_submit=None, width: float | None = None,
               expand: bool | None = None, multiline: bool = False,
               min_lines: int = 1, max_lines: int = 1,
               password: bool = False, can_reveal_password: bool = False,
               read_only: bool = False, autofocus: bool = False,
               prefix_icon: str | None = None, suffix_icon: str | None = None,
               keyboard_type=None, on_focus=None, on_blur=None,
               text_align=None, mono: bool = False,
               clearable: bool = True, on_clear=None,
               field_ref: list | None = None, **kwargs) -> ft.TextField:
    """统一单行输入框：28 高、纸色底、1px 发丝描边、6 圆角（m02696 去灰底块）。

    `multiline=True` 时只把高度交给 min/max_lines（报告、bib 粘贴框），描边/圆角/
    字号仍同源；多行上下内边距略大，避免文字贴住边线。

    批 114 用户 m03131：可编辑输入框默认挂「一键清空」✕（`clearable=True`），
    有值才显示、点击清空并触发原 on_change；只读字段不挂（`read_only` 优先）。
    """
    size = t.font("mono") if mono else t.font("body")
    field = ft.TextField(
        value=value,
        hint_text=hint_text,
        hint_style=ft.TextStyle(size=size, color=t.palette.text_faint),
        text_size=size,
        text_style=ft.TextStyle(size=size, color=t.palette.text,
                                font_family=t.mono_family if mono else None),
        text_align=text_align or ft.TextAlign.LEFT,
        height=None if multiline else t.control_h(),
        min_lines=min_lines if multiline else 1,
        max_lines=max_lines if multiline else 1,
        multiline=multiline,
        filled=False,
        fill_color=None,
        # 显式纸色底（m02816）：filled=False 的输入框若不给 bgcolor 就是透明，
        # 直接躺在页面灰底（bg）或侧栏底（sidebar）上会透成「灰底块」；白卡片里
        # 看不出来，单篇稿件的源稿/输出目录行就露馅 —— 所以一律显式给纸色。
        bgcolor=t.palette.surface,
        border=_floating_border(t),
        # 单行留 4 上下内边距：TextField 的高度 = 行高 + padding；0 padding 时实际只画
        # 20px（28 高会「矮一截」，m01257 的原始症状）。多行再放宽，文字不贴边线。
        # 可视描边高度≈control_h：单靠 height 不够（dense 下描边只有 ~19 高），
        # 关 dense + 上下 6/6 内边距后实测 28（m02816：输入框与同行按钮等高）。
        dense=False,
        content_padding=(_pad(t, 8, 6, 8, 6) if not multiline
                         else _pad(t, 8, 8, 8, 8)),
        cursor_color=t.palette.accent,
        text_vertical_align=(ft.VerticalAlignment.START if multiline
                             else ft.VerticalAlignment.CENTER),
        password=password,
        can_reveal_password=can_reveal_password,
        read_only=read_only,
        autofocus=autofocus,
        width=width,
        expand=expand,
        prefix_icon=ic(prefix_icon) if isinstance(prefix_icon, str) else prefix_icon,
        suffix_icon=ic(suffix_icon) if isinstance(suffix_icon, str) else suffix_icon,
        keyboard_type=keyboard_type,
        on_change=on_change,
        on_submit=on_submit,
        on_focus=on_focus,
        on_blur=on_blur,
        **kwargs,
    )
    if field_ref is not None:      # 页面拿得到 TextField，才能把「浏览」结果写回去
        field_ref.append(field)
    if clearable:                   # 批 114 m03131：默认带一键清空 ✕（只读字段内部自动跳过）
        make_clearable(field, t, on_clear=on_clear)
    return field


def search_field(t: Tokens, *, value: str = "", hint_text: str = "搜索页面、命令或最近记录",
                 on_change=None, on_submit=None, width: float | None = None,
                 expand: bool | None = None,
                 field_ref: list | None = None) -> ft.TextField:
    """检索输入框：纸色底 + 1px 描边 + 左侧放大镜，不留灰底块（m02696）。

    与 `text_field` 同一几何（28 高、6 圆角、正文 13），只是显式给纸色底：
    顶栏底色是 sidebar，透明输入框会透出灰底，看起来仍是「灰底块」。
    """
    field = text_field(t, value=value, hint_text=hint_text, on_change=on_change,
                       on_submit=on_submit, width=width, expand=expand,
                       prefix_icon="SEARCH", field_ref=field_ref)
    field.bgcolor = t.palette.surface
    field._search_chrome = True
    return field


class Select(ft.PopupMenuButton):
    """28 高自绘下拉（PopupMenuButton），保留旧 Dropdown 的 Python 用法。

    为什么不用 ft.Dropdown：Material 下拉的默认高度/描边/浮动标签不受 tokens 控制，
    与统一输入框并排时一大一小；这里用同一个 28 高 chrome 自绘，菜单项也钉死 28。
    兼容面：`.value`（可程序化赋值，toc / bib_advanced 依赖）、`.options`
    （读写，元素是 DropdownOption）、`.label`（仅元数据，视觉标签由 form_field 给）。
    """

    def __init__(self, t: Tokens, *, value: str | None = None,
                 options: list | None = None, on_select=None,
                 width: float | None = None, label: str | None = None,
                 hint: str | None = None, disabled: bool = False, **kwargs):
        self.t = t
        self.label = label
        self._hint = hint or ""
        self._callback = on_select
        self._options: list[ft.DropdownOption] = []
        self._value = ""
        self._value_text = ft.Text(
            "", size=t.font("body"), color=t.palette.text, max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS, no_wrap=True, expand=True)
        chrome = ft.Container(
            height=t.control_h(),
            bgcolor=t.palette.surface,
            border=box(t),
            border_radius=t.radius("control"),
            padding=_pad(t, 8, 0, 8, 0),
            alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Row(
                spacing=t.px(6),
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[self._value_text,
                          ft.Icon(ic("KEYBOARD_ARROW_DOWN"), size=t.px(14),
                                  color=t.palette.text_muted)],
            ),
        )
        super().__init__(
            content=chrome, items=[], width=width, height=t.control_h(),
            padding=0, bgcolor=t.palette.surface, elevation=6,
            shape=ft.RoundedRectangleBorder(radius=t.radius("popover")),
            disabled=disabled, **kwargs)
        if options:
            self.options = options
        self.value = value

    # -- 旧 Dropdown 兼容面 ------------------------------------------------
    @property
    def value(self) -> str:
        return self._value

    @value.setter
    def value(self, value) -> None:
        self._value = "" if value is None else str(value)
        self._sync_chrome()
        self._rebuild_items()

    @property
    def options(self) -> list[ft.DropdownOption]:
        return self._options

    @options.setter
    def options(self, options) -> None:
        self._options = []
        for option in options or []:
            if isinstance(option, ft.DropdownOption):
                key, label = option.key, option.text
            elif isinstance(option, dict):
                key, label = option.get("key", ""), option.get("text", option.get("key", ""))
            else:
                key, label = option
            self._options.append(ft.DropdownOption(key=str(key), text=str(label)))
        self._sync_chrome()
        self._rebuild_items()

    # -- 内部 --------------------------------------------------------------
    def _selected_text(self) -> tuple[str, str]:
        for option in self._options:
            if str(option.key) == self._value:
                return str(option.text), self.t.palette.text
        if self._value:
            return self._value, self.t.palette.text
        return self._hint, self.t.palette.text_faint

    def _sync_chrome(self) -> None:
        if not hasattr(self, "_value_text"):
            return
        label, color = self._selected_text()
        self._value_text.value = label
        self._value_text.color = color

    def _pick(self, key: str):
        def handler(_e=None) -> None:
            self.value = key
            if self._callback is not None:
                self._callback(SimpleNamespace(control=self, data=key,
                                               name="select", page=None))
        return handler

    def _rebuild_items(self) -> None:
        if not hasattr(self, "_options"):
            return
        body = self.t.font("body")
        self.items = [
            ft.PopupMenuItem(
                height=self.t.control_h(),
                padding=0,
                checked=(str(option.key) == self._value),
                content=ft.Container(
                    height=self.t.control_h(),
                    padding=_pad(self.t, 10, 0, 10, 0),
                    alignment=ft.Alignment.CENTER_LEFT,
                    content=ft.Text(str(option.text), size=body,
                                    color=self.t.palette.text),
                ),
                on_click=self._pick(str(option.key)),
            )
            for option in self._options
        ]


def select(t: Tokens, *, value: str | None = None, options: list | None = None,
           on_select=None, width: float | None = None, label: str | None = None,
           hint: str | None = None, disabled: bool = False, **kwargs) -> Select:
    """创建 28 高自绘下拉；签名对齐旧 `dropdown()`，便于逐点迁移。"""
    return Select(t, value=value, options=options, on_select=on_select,
                  width=width, label=label, hint=hint, disabled=disabled, **kwargs)


def dropdown(t: Tokens, *, value: str | None, options: list,
             on_select=None, width: float | None = None, label: str | None = None,
             height: float | None = None, **kwargs) -> Select:
    """旧名保留（批 112 测试/页面仍可调用）；统一转到 select。"""
    if height is not None:
        kwargs.setdefault("height", height)
    return select(t, value=value, options=options, on_select=on_select,
                  width=width, label=label, **kwargs)


def _floating_border(t: Tokens):
    """浮动标签输入框的描边：默认 1px 边线、聚焦 accent、错误 error。"""
    p = t.palette

    def _side(color: str, width_px: float) -> ft.OutlineInputBorder:
        return ft.OutlineInputBorder(
            side=ft.BorderSide(width_px, color),
            border_radius=t.radius("control"),
        )

    # Flet 1.0.5：ControlStateValue 是 typing Union，不能实例化；直接给 dict。
    return {
        ft.ControlState.DEFAULT: _side(p.border, t.px(1)),
        ft.ControlState.FOCUSED: _side(p.accent, t.px(1.5)),
        ft.ControlState.ERROR: _side(p.error, t.px(1.5)),
        ft.ControlState.DISABLED: _side(p.border, t.px(1)),
    }


def _apply_floating(field: ft.TextField, t: Tokens, label: str, *, hint: str | None = None,
                    width: float | None = None, expand: bool | None = None) -> ft.TextField:
    p = t.palette
    field.label = label
    field.label_style = ft.TextStyle(size=t.font("small"), color=p.text_muted)
    field.align_label_with_hint = True
    if hint and not field.hint_text:
        field.hint_text = hint
    field.hint_style = ft.TextStyle(size=t.font("body"), color=p.text_faint)
    field.height = t.px(48)
    field.filled = False
    field.fill_color = None
    field.bgcolor = p.surface      # 同 text_field：显式纸色底，不透出父容器
    field.border = _floating_border(t)
    # 描边盒的可视高度由 InputDecorator 的内边距决定，单给 height 只会得到「28 高边框 + 空白」（m02816 用户实拍：输入框只有浏览按钮一半高）。
    # dense 会压小最小高度；这里关掉 dense 并给上下不对称内边距，让可视描边≈48（与浏览按钮等高）。
    field.dense = False
    field.content_padding = ft.Padding(left=t.px(12), top=t.px(16), right=t.px(8), bottom=t.px(10))
    field.text_size = t.font("body")
    field.text_style = ft.TextStyle(size=t.font("body"), color=p.text)
    field.cursor_color = p.accent
    if width is not None:
        field.width = width
    if expand is not None:
        field.expand = expand
    field._form_label = label
    return field


def floating_field(t: Tokens, label: str, *, value: str = "", hint: str | None = None,
                   on_change=None, on_blur=None, width: float | None = None,
                   expand: bool | None = None, input_filter=None,
                   error: str | None = None, clearable: bool = True,
                   on_clear=None, **kwargs) -> ft.TextField:
    """Material 描边「浮动标签」输入框（用户 m02409/m02483 指定手感）。

    label 在框内、聚焦/有值时上浮成小号标签（Flutter 自带 ~200ms 动画），空且未聚焦
    时显示 `hint` 填写示例。48 设计 px 高、描边 1px（聚焦 accent 1.5px），不再用灰色
    实心块。校验错误通过 `error` 显示在框下方（红字）。

    批 114 用户 m03131：默认挂「一键清空」✕（有值才显示），清空时触发原 on_change。
    """
    p = t.palette
    field = ft.TextField(
        value=value,
        on_change=on_change,
        on_blur=on_blur,
        error=error,
        error_style=ft.TextStyle(size=t.font("small"), color=p.error),
        input_filter=input_filter,
        hint_style=ft.TextStyle(size=t.font("body"), color=p.text_faint),
        **kwargs,
    )
    field = _apply_floating(field, t, label, hint=hint, width=width, expand=expand)
    if clearable:
        make_clearable(field, t, on_clear=on_clear)
    return field


def form_field(t: Tokens, label: str, control, *, required: bool = False,
               hint: str | None = None, width: float | None = None,
               expand: bool | None = None, clearable: bool = True, on_clear=None):
    """表单标签。单行 TextField 直接改造成描边浮动标签（label 在框内上浮）；
    下拉/多行/只读控件沿用「标签上置」布局（这些控件本身没有浮动 label 语义）。

    批 114 用户 m03131：可编辑的 TextField（含多行）都挂一键清空 ✕；只读字段不挂。
    """
    if isinstance(control, ft.TextField) and not control.multiline and not control.read_only:
        shown = label + ("（必填）" if required else "")
        if clearable:
            make_clearable(control, t, on_clear=on_clear)
        field = _apply_floating(control, t, shown, hint=hint, width=width, expand=expand)
        field._form_label = label  # 元数据保留纯 label，渲染用 shown
        return field
    if isinstance(control, ft.TextField) and clearable:
        make_clearable(control, t, on_clear=on_clear)   # 多行可编辑：✕ 挂在框内 suffix
    # 必填语义写进文案（「（必填）」），不再用红色 `*`（用户 m02696）。
    title = [text(label + ("（必填）" if required else ""), t, "small",
                  t.palette.text_muted, weight="medium")]
    controls = [ft.Row(spacing=0, controls=title), control]
    if hint:
        controls.append(text(hint, t, "caption", t.palette.text_faint))
    # 非 Flet 字段：测试/可访问性可从控件反查标签，不参与渲染。
    control._form_label = label
    return ft.Column(spacing=t.px(4), width=width, expand=expand, controls=controls)


# 按钮类控件：`field_row()` 只把高度套在它们身上，文字/说明控件不碰。
_ROW_BUTTON_TYPES = (ft.FilledButton, ft.OutlinedButton, ft.TextButton,
                     ft.FilledTonalButton, ft.IconButton)


def _row_actions(actions) -> list:
    """把 `actions` 摊平成控件列表：单个控件 / 列表 / 已有的 Row 都接受。"""
    if actions is None:
        return []
    if not isinstance(actions, (list, tuple)):
        actions = [actions]
    flat: list = []
    for item in actions:
        if item is None:
            continue
        if isinstance(item, ft.Row):
            flat.extend(item.controls or [])
        else:
            flat.append(item)
    return flat


def field_row(field, actions, t: Tokens, *, height: float | None = None) -> ft.Row:
    """表单行：输入控件 + 右侧动作，按钮高度与输入框严格相等（用户 m02816）。

    背景：输入行里输入框是 48（浮动标签）/ 28（紧凑框），按钮 helper 默认却是
    control_h()=28 —— 单篇稿件的「源稿件 + 浏览…」一行高矮不齐就是这么来的。
    这里统一成一个出口：目标高度 = 显式 `height`，否则取 `field.height`；
    行内所有按钮按目标高度重设，`vertical_alignment` 用 CENTER（等高时即齐平）。

    `field` 若是 `form_field()` 的「标签上置」复合列（没有 height），说明控制行在
    列底：按钮保持 control_h 并改为 END 对齐，让按钮底边和下拉/输入行的底边齐平
    —— 这是唯一无法写成「同高 Row」的形态（标签在控件上方，不占同一行高）。
    """
    items = _row_actions(actions)
    explicit = height
    if not isinstance(explicit, (int, float)):
        candidate = getattr(field, "height", None)
        explicit = candidate if isinstance(candidate, (int, float)) else None
    if explicit is None:
        target = t.control_h()
        align = ft.CrossAxisAlignment.END
    else:
        target = float(explicit)
        align = ft.CrossAxisAlignment.CENTER
    for item in items:
        if isinstance(item, _ROW_BUTTON_TYPES):
            item.height = target
    return ft.Row(spacing=t.sp("1"), vertical_alignment=align,
                  controls=[field, *items])


def form_grid(t: Tokens, fields, *, columns: int = 2,
              min_cell: float = 160.0,
              spacing: float | None = None) -> ft.ResponsiveRow:
    """等宽表单网格：默认两列，可用宽度不足时自动降单列（用户 m03037）。

    `fields` 是已完成布局的 48 高字段控件（浮动输入框 / `select_box`）。每个字段
    挂 12 栅格（两列 → 每格 6），断点按 `min_cell * columns`（zoom 换算成实际 px）
    设置——右侧参数面板在 1240/1000 宽窗口下仍保持两列，真正窄时才单列，避免
    固定 width 把行挤出面板（生成目录的栏目编辑区此前就是这么溢出的）。
    """
    gap = spacing if spacing is not None else t.sp("1")
    cell = max(1, 12 // max(1, int(columns)))
    threshold = t.px(min_cell * max(1, int(columns)))
    breakpoints = {
        ft.ResponsiveRowBreakpoint.XS: 0,
        ft.ResponsiveRowBreakpoint.SM: threshold,
        ft.ResponsiveRowBreakpoint.MD: threshold,
        ft.ResponsiveRowBreakpoint.LG: threshold,
        ft.ResponsiveRowBreakpoint.XL: threshold,
        ft.ResponsiveRowBreakpoint.XXL: threshold,
    }
    cols = {"xs": 12, "sm": cell}
    return ft.ResponsiveRow(
        columns=12,
        spacing=gap,
        run_spacing=gap,
        breakpoints=breakpoints,
        controls=[ft.Container(col=cols, content=field) for field in fields],
    )


def select_box(t: Tokens, label: str, *, value_text: str = "未指定",
               options=None, on_select=None, width: float | None = None,
               expand: bool | None = None, reset: bool = False,
               reset_text: str | None = None, on_reset=None) -> ft.PopupMenuButton:
    """48 高描边「下拉框」：外观与浮动标签输入框一致，点击弹 PopupMenu。

    `options` 为 [(key, label)]；`on_select(key, label)` 回调。返回控件上挂
    `_value_text`（可原地更新显示值）与 `_form_label`（测试/可访问性）。

    批 114 用户 m03131：`reset=True` 时，非默认值状态在框内显示 ↻（`reset_text`
    为默认显示文案，缺省用 `value_text` 的初值）；点击回默认并回调 `on_reset`。
    """
    p = t.palette
    default_text = reset_text if reset_text is not None else value_text
    label_text = text(label, t, "small", p.text_muted)
    value_ctl = text(value_text, t, "body", p.text, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    chevron = ft.Icon(ic("EXPAND_MORE", "KEYBOARD_ARROW_DOWN"), size=t.px(16), color=p.text_muted)

    reset_btn: ft.IconButton | None = None
    if reset:
        reset_btn = ft.IconButton(
            icon=ic("RESTART_ALT"),
            tooltip=f"重置为「{default_text}」",
            icon_size=t.px(15),
            icon_color=p.text_faint,
            width=t.px(24),
            height=t.px(24),
            style=ft.ButtonStyle(padding=0),
            visible=(str(value_text or "") != str(default_text or "")),
        )

    def _sync_reset(e=None) -> None:
        if reset_btn is None:
            return
        visible = str(value_ctl.value or "") != str(default_text or "")
        if reset_btn.visible == visible:
            return
        reset_btn.visible = visible
        try:
            reset_btn.update()
        except Exception:  # noqa: BLE001 构建期未挂载
            pass

    def _do_reset(e=None) -> None:
        value_ctl.value = default_text
        if on_reset is not None:
            on_reset()
        _sync_reset()
        try:
            value_ctl.update()
        except Exception:  # noqa: BLE001
            pass

    if reset_btn is not None:
        reset_btn.on_click = _do_reset
    row_controls: list[ft.Control] = [ft.Container(expand=True, content=value_ctl), chevron]
    if reset_btn is not None:
        row_controls.insert(1, reset_btn)
    content = ft.Container(
        height=t.px(48),
        padding=_pad(t, 12, 5, 8, 5),
        bgcolor=p.surface,         # 同输入框：显式纸色底，不透出父容器
        border=ft.Border.all(t.px(1), p.border),
        border_radius=t.radius("control"),
        expand=True,
        content=ft.Column(spacing=0, alignment=ft.MainAxisAlignment.CENTER, controls=[
            label_text,
            ft.Row(spacing=t.px(4), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=row_controls),
        ]),
    )

    def _choose(item_key, item_label) -> None:
        shown = None
        if on_select is not None:
            shown = on_select(item_key, item_label)
        if shown is False:          # 调用方自行维护显示文字（如「自定义…」打开对话框）
            return
        value_ctl.value = shown if isinstance(shown, str) else item_label
        _sync_reset()
        try:
            value_ctl.update()
        except Exception:  # noqa: BLE001
            pass

    items: list[ft.PopupMenuItem] = []
    for item_key, item_label in (options or []):
        items.append(ft.PopupMenuItem(
            height=t.px(32),
            content=text(item_label, t, "body", p.text),
            on_click=(lambda e, k=item_key, v=item_label: _choose(k, v)),
        ))
    button = ft.PopupMenuButton(
        content=content,
        items=items,
        padding=0,
        height=t.px(48),           # 批 114 m03074：盒高显式 48，field_row 可据此对齐同行按钮
        menu_position=ft.PopupMenuPosition.UNDER,
        bgcolor=p.surface,
        shape=ft.RoundedRectangleBorder(radius=t.radius("md")),
    )
    if width is not None:
        button.width = width
    if expand is not None:
        button.expand = expand
    button._value_text = value_ctl
    button._form_label = label
    button._reset_button = reset_btn
    button._default_text = default_text
    button._sync_reset = _sync_reset
    button._choose = _choose
    return button


def switch(t: Tokens, *, value: bool = False, on_change=None, disabled: bool = False,
           label: str | None = None, labels: bool = False, **kwargs) -> ft.Switch:
    """统一开关：显式四态 thumb/track，浅色关闭态轨道 vs 纸面 ≥3:1（m01257）。

    旧写法只给 active_color，浅色下关闭态几乎融进背景；这里轨道用 text_muted
    （#6E6E73 vs #FFFFFF = 4.9:1），深色用同一角色（#A1A1A6 vs #252527 ≈ 5.9:1）。
    """
    p = t.palette
    thumb = {
        ft.ControlState.DEFAULT: p.surface,
        ft.ControlState.SELECTED: p.on_accent,
        ft.ControlState.DISABLED: p.surface_3,
        ft.ControlState.HOVERED: p.surface,
    }
    track = {
        ft.ControlState.DEFAULT: p.text_muted,
        ft.ControlState.SELECTED: p.accent,
        ft.ControlState.DISABLED: p.border,
        ft.ControlState.HOVERED: p.text_muted,
    }
    return ft.Switch(
        value=value,
        on_change=on_change,
        disabled=disabled,
        active_color=p.accent,
        active_track_color=p.accent,
        inactive_thumb_color=p.surface,
        inactive_track_color=p.text_muted,
        thumb_color=thumb,
        track_color=track,
        label=label if labels else None,
        label_text_style=ft.TextStyle(size=t.font("small"), color=p.text_muted),
        **kwargs,
    )


def icon_button(icon: str, tooltip: str, on_click, t: Tokens, *,
                color: str | None = None, width: float | None = None,
                height: float | None = None) -> ft.IconButton:
    size = height if height is not None else t.px(26)
    return ft.IconButton(
        ic(icon), tooltip=tooltip, on_click=on_click,
        icon_size=t.px(16),
        width=width if width is not None else t.px(26), height=size,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=t.radius("control")),
            padding=ft.Padding(left=t.px(4), top=0, right=t.px(4), bottom=0),
        ),
        icon_color=color or t.palette.text_muted,
    )


class CollapsibleSection:
    """自绘折叠区：区块标题 + 扁平箭头按钮（替换 Material ExpansionTile）。

    用户 m02696：元数据（可选）右侧的展开箭头是灰圆底块 → 这里用 26×26 扁平
    `icon_button`，默认展开（`expanded=True`）且只改箭头图标，不铺任何底色。
    对外保留 `expanded` / `toggle()` / `set_expanded()`，用法与 ExpansionTile 对齐。
    """

    def __init__(self, t: Tokens, *, title: str, content, expanded: bool = True,
                 note: str | None = None) -> None:
        self.t = t
        self.expanded = bool(expanded)
        self.title_label = text(title, t, role="item-title")
        self._note = (text(note, t, "caption", t.palette.text_faint)
                      if note else None)
        self._toggle_btn = icon_button(
            "EXPAND_LESS" if self.expanded else "EXPAND_MORE",
            "收起" if self.expanded else "展开", self.toggle, t)
        header_controls: list = [self.title_label]
        if self._note is not None:
            header_controls.append(self._note)
        header_controls.extend([ft.Container(expand=True), self._toggle_btn])
        header = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=header_controls)
        self.body = ft.Container(content=content, visible=self.expanded)
        self.root = ft.Column(spacing=t.px(6), controls=[header, self.body])

    # -- 折叠 ---------------------------------------------------------
    def toggle(self, e=None) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, value: bool) -> None:
        self.expanded = bool(value)
        self.body.visible = self.expanded
        self._toggle_btn.icon = ic("EXPAND_LESS" if self.expanded else "EXPAND_MORE")
        self._toggle_btn.tooltip = "收起" if self.expanded else "展开"
        try:
            self.root.update()
        except Exception:  # noqa: BLE001 构建期/未挂载（证据脚本）
            pass


# ── 徽标 / 状态 ─────────────────────────────────────────────────────────────
def badge(label: str, t: Tokens, tone: str = "accent") -> ft.Container:
    p = t.palette
    bg, fg = {
        "accent": (p.accent_soft, p.on_accent_soft),
        "success": (p.success_soft, p.success),
        "warn": (p.warn_soft, p.warn),
        "error": (p.error_soft, p.error),
        "neutral": (p.surface_2, p.text_muted),
    }.get(tone, (p.accent_soft, p.on_accent_soft))
    return ft.Container(
        bgcolor=bg, border_radius=t.radius("mark"),
        padding=_pad(t, 6, 1, 6, 1),
        content=text(label, t, "caption", fg, weight="medium"),
    )


def status_chip(label: str, t: Tokens, state: str = "idle") -> ft.Container:
    tone = {"idle": "neutral", "done": "success", "running": "accent", "warn": "warn",
            "error": "error"}.get(state, "neutral")
    return badge(label, t, tone)


def progress_bar(value: float, t: Tokens, width: float | None = None) -> ft.ProgressBar:
    return ft.ProgressBar(
        value=max(0.0, min(1.0, value)),
        bar_height=t.px(4),
        width=width or t.px(140),
        color=t.palette.accent,
        bgcolor=t.palette.surface_3,
        border_radius=t.radius("pill"),
    )


# ── 导航 ────────────────────────────────────────────────────────────────────
def nav_item(icon: str, label: str, t: Tokens, *, active: bool,
             on_click, compact: bool = False) -> ft.Container:
    """侧栏一行 28 高：选中 = NAV_SEL 灰底 + 6 圆角（不再是 accent_soft 蓝块）。"""
    p = t.palette
    icon_color = p.accent if active else p.text_muted
    label_color = p.text if active else p.text_muted
    controls = [ft.Icon(ic(icon), size=t.px(16), color=icon_color)]
    if not compact:
        controls.append(text(label, t, role="nav-selected" if active else "nav-label",
                             color=label_color, max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS))
    return ft.Container(
        on_click=on_click,
        bgcolor=p.nav_sel if active else None,
        border_radius=t.radius("control"),
        height=t.row_h("sidebar"),
        padding=_pad(t, 8, 0, 8, 0),
        alignment=ft.Alignment.CENTER_LEFT if compact else None,
        content=ft.Row(
            spacing=t.sp("1"),
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=controls,
        ),
    )


def nav_group_label(label: str, t: Tokens) -> ft.Container:
    return ft.Container(
        padding=_pad(t, 8, 12, 8, 4),
        content=text(label, t, "caption", t.palette.text_faint, weight="semibold"),
    )


# ── 批 114 / 用户 m03256：左侧导航 accordion（页面 → 组 → 视图）──────────────
# 父行 30 / 组行 28 / 子行 28；缩进 0 / 28 / 52；选中只用 nav_sel 灰底 + 圆角 6，
# 不反白、不加左侧色条；chevron 的点击区单独 20×20，只展开/收起、不导航。
def _nav_chevron(expanded: bool, t: Tokens) -> ft.Control:
    return ft.Icon(ic("EXPAND_MORE" if expanded else "CHEVRON_RIGHT"),
                   size=t.px(12), color=t.palette.text_faint)


def nav_parent(icon: str, label: str, t: Tokens, *, active: bool, expanded: bool,
               on_click, on_toggle=None, has_children: bool = True) -> ft.Container:
    """一级页面行：30 高；当前页 icon accent + 13/500 text；chevron 12 在 20×20 热区。"""
    p = t.palette
    controls: list[ft.Control] = [
        ft.Icon(ic(icon), size=t.px(16), color=p.accent if active else p.text_muted),
        text(label, t, role="nav-selected" if active else "nav-label",
             color=p.text if active else p.text_muted, max_lines=1,
             overflow=ft.TextOverflow.ELLIPSIS, expand=True),
    ]
    if has_children:
        controls.append(ft.Container(
            width=t.px(20), height=t.px(20), border_radius=t.radius("sm"),
            alignment=ft.Alignment.CENTER,
            content=_nav_chevron(expanded, t),
            tooltip="收起" if expanded else "展开",
            on_click=on_toggle,
        ))
    return ft.Container(
        height=t.px(30),
        border_radius=t.radius("control"),
        bgcolor=p.nav_sel if active else None,
        padding=_pad(t, 8, 0, 6, 0),
        on_click=on_click,
        content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=controls),
    )


def nav_group(label: str, t: Tokens, *, expanded: bool, on_click, indent: int = 28,
              tooltip: str | None = None) -> ft.Container:
    """引用库组行：28 高、缩进 28；12/500 muted；右侧 ⌄/› 只展开/收起。"""
    p = t.palette
    return ft.Container(
        height=t.px(28),
        border_radius=t.radius("control"),
        padding=_pad(t, indent, 0, 8, 0),
        on_click=on_click,
        tooltip=tooltip,
        content=ft.Row(spacing=t.sp("half"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[
            text(label, t, "small", p.text_muted, weight="medium", max_lines=1,
                 overflow=ft.TextOverflow.ELLIPSIS, expand=True),
            _nav_chevron(expanded, t),
        ]),
    )


def nav_child(label: str, t: Tokens, *, active: bool, on_click, indent: int = 52,
              tooltip: str | None = None) -> ft.Container:
    """二级视图行：28 高、缩进 52；选中 nav_sel 灰底 + 圆角 6 + 13/500。"""
    p = t.palette
    return ft.Container(
        height=t.px(28),
        border_radius=t.radius("control"),
        bgcolor=p.nav_sel if active else None,
        padding=_pad(t, indent, 0, 8, 0),
        on_click=on_click,
        tooltip=tooltip,
        content=ft.Row(spacing=t.sp("half"),
                       vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[text(label, t, role="nav-selected" if active else "nav-label",
                                      color=p.text if active else p.text_muted, max_lines=1,
                                      overflow=ft.TextOverflow.ELLIPSIS, expand=True)]),
    )


# ── 路径选择 / 选项行 ───────────────────────────────────────────────────────
def path_picker(label: str, value: str, hint: str, t: Tokens, *, on_change=None,
                on_browse=None, required: bool = True, show_note: bool = True,
                field_ref: list | None = None) -> ft.Column:
    """路径选择行：描边浮动标签 + 浏览按钮（与高级参数同一材质，m02483/m02696）。

    必填语义进 label（「（必填）」/「（可空）」），不再单起一行「必填」；剩下的
    caption 只提示输入方式。flet 1.0.5 无系统级文件拖放 API（见 impl-notes），
    所以这里只保证「可聚焦输入 + ⌘V 粘贴 + 浏览按钮」，不假装支持拖拽。
    """
    # 调用方若已在 label 里写「（可空）/（必填）」，不重复追加（用户 m02696：语义只出现一次）。
    if "（必填）" in label or "（可空）" in label:
        suffix = ""
    else:
        suffix = "（必填）" if required else "（可空）"
    field = text_field(t, value=value or "", on_change=on_change, field_ref=field_ref)
    field = _apply_floating(
        field, t, label + suffix,
        hint=hint or "路径", expand=True)
    # R01：长路径在窄窗里会被截断，悬停要能看全；`sync_clearable` 在用户输入与程序化
    # 回填（浏览…/set_field）两条链路都会跑，所以只打标记 + 给初值，不另包 on_change。
    field._value_tooltip = True
    field.tooltip = _full_value_tooltip(field.value)
    browse = secondary_button("浏览…", on_browse, t, icon="FOLDER_OPEN")
    controls: list[ft.Control] = [
        # 输入框 48（浮动标签）→ 浏览按钮跟随 48（用户 m02816：不能高矮不齐）。
        field_row(field, browse, t),
    ]
    if show_note:
        controls.append(text("可直接粘贴路径（⌘V），或点「浏览…」选择",
                             t, "caption", t.palette.text_faint))
    return ft.Column(spacing=t.sp("half"), controls=controls)


def _compact_checkbox(t: Tokens, value: bool, on_change=None) -> ft.Container:
    """20 盒勾选框：Material Checkbox 默认 ~48 点击区会把行高撑大（用户 m02238）。"""
    return ft.Container(
        width=t.px(20), height=t.px(20), alignment=ft.Alignment.CENTER,
        content=ft.Checkbox(value=value, on_change=on_change,
                            visual_density=ft.VisualDensity.COMPACT, splash_radius=0),
    )


def option_tile(label: str, desc: str, t: Tokens, *, icon: str | None = None,
                checked: bool = True, on_change=None,
                badge_label: str | None = None) -> ft.Container:
    """处理选项卡片：图标 + 标题 + 说明 + 右上勾选，2 列自适应。

    单栏列表在 1240 宽下「四行挤成一列」（用户 m02239 反馈）；卡片网格把四个选项
    平铺成 2×2，每项独立描边、留白一致，扫读与点击目标都更清楚。
    """
    p = t.palette
    head = ft.Row(spacing=t.px(8), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        ft.Container(width=t.px(26), height=t.px(26), bgcolor=p.surface_2,
                     border_radius=t.radius("sm"), alignment=ft.Alignment.CENTER,
                     content=ft.Icon(ic(icon or "CHECK_CIRCLE_OUTLINE"),
                                     size=t.px(15), color=p.text_muted)),
        text(label, t, "body", p.text, "medium"),
        *([badge(badge_label, t, "accent")] if badge_label else []),
        ft.Container(expand=True),
        _compact_checkbox(t, checked, on_change),
    ])
    return ft.Container(
        expand=True, height=t.px(80),
        padding=_pad(t, 12, 10, 12, 10),
        bgcolor=p.surface,
        border=box(t, p.border),
        border_radius=t.radius("md"),
        content=ft.Column(spacing=t.px(4), alignment=ft.MainAxisAlignment.CENTER, controls=[
            head,
            text(desc, t, "caption", p.text_muted, max_lines=2,
                 overflow=ft.TextOverflow.ELLIPSIS),
        ]),
    )


def option_row(label: str, desc: str, t: Tokens, *, icon: str | None = None,
               checked: bool = True, on_change=None, badge_label: str | None = None,
               note: str | None = None) -> ft.Row:
    p = t.palette
    leading = ft.Container(
        width=t.px(34), height=t.px(34),
        bgcolor=p.surface_2, border_radius=t.radius("sm"),
        alignment=ft.Alignment.CENTER,
        content=ft.Icon(ic(icon or "CHECK_CIRCLE_OUTLINE"), size=t.px(18), color=p.text_muted),
    )
    title_row = [text(label, t, role="item-title")]
    if badge_label:
        title_row.append(badge(badge_label, t, "accent"))
    title_row.append(ft.Container(expand=True))
    # 勾选框高度收进 20：Material Checkbox 自带约 48 高点击区，会把标题行撑高，
    # 造成同栏大/小字之间出现大空隙（用户 m02238）。固定 20 盒 + compact density。
    title_row.append(ft.Container(
        width=t.px(20), height=t.px(20), alignment=ft.Alignment.CENTER,
        content=ft.Checkbox(value=checked, on_change=on_change,
                            visual_density=ft.VisualDensity.COMPACT,
                            splash_radius=0),
    ))
    desc_controls = [text(desc, t, "caption", p.text_muted,
                          max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)]
    if note:
        desc_controls.append(text(note, t, "caption", p.text_faint))
    return ft.Row(
        spacing=t.sp("1"),
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            leading if icon else ft.Container(width=0, height=0),
            ft.Column(spacing=t.px(2), expand=True, controls=[
                ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=title_row),
                *desc_controls,
            ]),
        ],
    )


# ── 空状态 / 统计卡 ─────────────────────────────────────────────────────────
def empty_state(icon: str, title: str, desc: str, t: Tokens, *, height: float | None = None) -> ft.Container:
    return ft.Container(
        height=height or t.px(180),
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            spacing=t.sp("1"),
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ic(icon, "INBOX"), size=t.px(34), color=t.palette.text_faint),
                text(title, t, role="item-title", color=t.palette.text_muted),
                text(desc, t, "caption", t.palette.text_faint),
            ],
        ),
    )


def stat_card(label: str, value: str, hint: str, t: Tokens, *,
              expand: bool = True, width: float | None = None) -> ft.Container:
    """统计卡。注意：`ft.Row(wrap=True)` 的子控件不能带 expand（Flutter 会抛
    WrapParentData/FlexParentData 类型错误），换行行里必须传 expand=False + width。"""
    return card(
        ft.Column(spacing=t.sp("half"), controls=[
            text(label, t, "caption", t.palette.text_muted, weight="medium"),
            text(value, t, "h1", weight="semibold"),
            text(hint, t, "caption", t.palette.text_faint),
        ]),
        t, pad=14, expand=expand, width=width,
    )


def chip(label: str, t: Tokens, *, active: bool, on_click) -> ft.Container:
    """兼容旧页面的二级项：方角小底，不再是胶囊。"""
    p = t.palette
    return ft.Container(
        on_click=on_click,
        bgcolor=p.sel_tint if active else None,
        border_radius=t.radius("control"),
        padding=_pad(t, 10, 4, 10, 4),
        content=text(label, t, role="chip-selected" if active else "small",
                     color=p.sel_text if active else p.text_muted),
    )


# ── 页内子导航（批 119：单篇 / 整期 / 引用库唯一实现）──────────────────────
def nav_hover(t: Tokens, e, chip: ft.Container, active: bool,
              palette_getter=None) -> None:
    """页内导航 chip 的 hover：未选中 surface_2、选中保持 nav_sel。

    `palette_getter` 用于主题热切换后仍取当前皮肤（默认用构建时 Tokens）。
    """
    p = palette_getter() if palette_getter is not None else t.palette
    hovered = str(getattr(e, "data", "")).lower() in ("true", "1", "on")
    chip.bgcolor = p.nav_sel if active else (p.surface_2 if hovered else None)
    try:
        chip.update()
    except Exception:  # noqa: BLE001 —— 构建期/未挂载时只改属性
        pass


def nav_chip(label: str, t: Tokens, *, active: bool, on_click,
             tone: str | None = None, key: str | None = None,
             tooltip: str | None = None,
             palette_getter=None) -> ft.Container:
    """页内子导航的唯一 chip 实现（批 119）。

    规格：28 高、`radius("control")`、左右 padding 10、文字 role small；
    选中 `nav_sel` 底 + text + semibold，未选中 text_muted + medium；
    只有传 `tone` 的步骤 chip（整期）才在标题右侧挂 6px 状态点。
    """
    p = palette_getter() if palette_getter is not None else t.palette
    title = text(label, t, "small", p.text if active else p.text_muted,
                 weight="semibold" if active else "medium")
    content: ft.Control
    if tone is None:
        content = title
    else:
        content = ft.Row(
            spacing=t.px(6), vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[title, status_dot(tone, t)])
    chip = ft.Container(
        key=key, height=t.px(28), border_radius=t.radius("control"),
        padding=_pad(t, 10, 0, 10, 0), alignment=ft.Alignment.CENTER,
        bgcolor=p.nav_sel if active else None, tooltip=tooltip or None,
        on_click=on_click, content=content)
    chip.on_hover = lambda e, box=chip, sel=active: nav_hover(
        t, e, box, sel, palette_getter=palette_getter)
    return chip


def _nav_split_rows(chips_by_group: list[list[ft.Control]],
                    groups: list[list[dict]],
                    split_after: str | None) -> list[list[list[ft.Control]]]:
    """窄窗折行：把组切成两行；`split_after` 是首行最后一个 chip 的 key。"""
    if not split_after:
        return [chips_by_group]
    rows: list[list[list[ft.Control]]] = [[]]
    for group, group_chips in zip(groups, chips_by_group):
        keys = [str(item["key"]) for item in group]
        if split_after in keys:
            index = keys.index(split_after)
            rows[-1].append(group_chips[:index + 1])
            rest = group_chips[index + 1:]
            rows.append([rest] if rest else [])
        else:
            rows[-1].append(group_chips)
    return [row for row in rows if row]


def page_nav(t: Tokens, *, groups: list[list[dict]], active: str, on_click,
             narrow: bool = False, narrow_after: str | None = None,
             key_prefix: str | None = None, chip_attr=None,
             divider_attr: str | None = None, bar_attrs=None,
             bar_key: str | None = None,
             bar_pad: tuple[float, float, float, float] = (24, 0, 24, 0),
             bgcolor: str | None = None, palette_getter=None) -> ft.Container:
    """三页共用的页内子导航条（批 119 唯一实现）。

    `groups` 是组列表，每组是 item 列表；item = {"key", "label",
    "tone"(可选，步骤状态点), "tooltip"(可选)}。`active` 是选中 key；
    `narrow_after` 是窄窗首行最后一个 chip 的 key（整期 2+2、引用库 4+5）。
    组间只画 1×16 发丝线（21 宽占位），无组标题；条高 36（窄窗 72）。

    `chip_attr(chip, item, is_active)` / `bar_attrs(bar)` 供页面挂旧结构
    锚点（如 `_bib_nav_chip`、`_bib_inpage_nav`）；`bar_pad` 只在引用库
    全宽面板里用默认 24，单篇/整期页体本身已有 24 内边距故传 0。
    """
    chips_by_group: list[list[ft.Container]] = []
    for group in groups:
        row_chips: list[ft.Container] = []
        for item in group:
            item_key = str(item["key"])
            is_active = item_key == active
            chip = nav_chip(
                str(item["label"]), t, active=is_active,
                on_click=lambda e, k=item_key: on_click(k),
                tone=item.get("tone"),
                key=(f"{key_prefix}{item_key}" if key_prefix else None),
                tooltip=item.get("tooltip"),
                palette_getter=palette_getter)
            if chip_attr is not None:
                chip_attr(chip, item, is_active)
            row_chips.append(chip)
        chips_by_group.append(row_chips)

    rows_chips = (_nav_split_rows(chips_by_group, groups, narrow_after)
                  if narrow else [chips_by_group])

    def _row(segments: list[list[ft.Container]]) -> ft.Container:
        controls: list[ft.Control] = []
        for index, segment in enumerate(segments):
            if index:
                divider = ft.Container(
                    width=t.px(21), height=t.px(36),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Container(width=t.px(1), height=t.px(16),
                                         bgcolor=t.palette.border))
                if divider_attr:
                    setattr(divider, divider_attr, True)
                controls.append(divider)
            controls.append(ft.Row(
                spacing=t.px(4), wrap=False,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=list(segment)))
        return ft.Container(
            height=t.px(36), alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Row(spacing=0, wrap=False,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=controls))

    row_controls = [_row(segments) for segments in rows_chips]
    bar = ft.Container(
        key=bar_key, height=t.px(72 if narrow else 36),
        padding=_pad(t, *bar_pad), bgcolor=bgcolor,
        border=ft.Border(bottom=_side(t)),
        content=(ft.Column(spacing=0, controls=row_controls)
                 if narrow else row_controls[0]))
    bar._page_nav_chips = [chip for group in chips_by_group for chip in group]
    if bar_attrs is not None:
        bar_attrs(bar)
    return bar


def group_tab(label: str, t: Tokens, *, active: bool, on_click=None,
              badge_label: str | None = None) -> ft.Container:
    """组 tab（查阅/采录/治理/维护）：选中 = 正文色 + 2px 下划线，34 高。

    `badge_label` 预留给「治理」组的未处理数（P2 钩子暂不传，接口先留）。
    """
    p = t.palette
    row = [text(label, t, "small", color=p.text if active else p.text_muted,
                weight="semibold")]
    if badge_label:
        row.append(badge(str(badge_label), t, "neutral"))
    return ft.Container(
        on_click=on_click,
        height=t.row_h("tab"),
        padding=_pad(t, 12, 0, 12, 0),
        alignment=ft.Alignment.CENTER_LEFT,
        content=ft.Column(spacing=0, alignment=ft.MainAxisAlignment.CENTER, controls=[
            ft.Row(spacing=t.px(4), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=row),
            ft.Container(width=t.px(26), height=max(1, t.px(2)),
                         bgcolor=(t.buttons.tab_underline if active else None)),
        ]),
    )


def action_row(icon: str, title: str, caption: str, t: Tokens, *, on_click=None,
               trailing=None) -> ft.Container:
    """「下一步」动作行：图标方块 + 标题 + 一句操作结果，右侧箭头（或自定义尾控件）。

    `caption` 只写点了会发生什么（如「选择文件并运行」），不写教学说明。
    """
    p = t.palette
    leading = ft.Container(
        width=t.px(32), height=t.px(32), bgcolor=p.surface_2,
        border_radius=t.radius("sm"), alignment=ft.Alignment.CENTER,
        content=ft.Icon(ic(icon), size=t.px(18), color=p.accent),
    )
    middle = [text(title, t, role="item-title")]
    if caption:
        middle.append(text(caption, t, "caption", p.text_muted, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS))
    tail = trailing if trailing is not None else ft.Icon(
        ic("CHEVRON_RIGHT", "ARROW_FORWARD"), size=t.px(18), color=p.text_faint)
    return ft.Container(
        on_click=on_click,
        ink=on_click is not None,
        border_radius=t.radius("sm"),
        padding=_pad(t, 8, 8, 8, 8),
        content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                       controls=[leading,
                                 ft.Column(spacing=t.px(2), expand=True, controls=middle),
                                 tail]),
    )


# ── 日志台（流式 / 清空 / 折叠）────────────────────────────────────────────
class LogConsole:
    """运行日志：行级颜色 + 摘要/原始折叠 + 清空；页面只调 add_line/clear。

    渲染合并：引擎一次可刷上千行，逐行 `update()` 等于每行一次 UI 往返。
    这里「控件立即入列、渲染限流」（≥100ms 或攒够 32 行才发一次），
    并去掉逐行 `empty in controls` 的 O(n²) 判等（那是 2000 行 14 秒的主因）。
    """

    FLUSH_INTERVAL_S = 0.1
    FLUSH_BATCH = 32

    def __init__(self, t: Tokens, *, height: float = 200, title: str = "运行日志",
                 dock: bool = False, toast=None, mark=None) -> None:
        self.t = t
        self._lines: list[tuple[str, str, bool]] = []
        self._fold_only = False
        self._empty_visible = True
        self._dirty = 0
        self._last_update = 0.0
        self._flush_scheduled = False
        self.dock = dock
        self._expanded = not dock
        # 批 115 gap1：收展 / 拖拽后宿主页面按实际日志高度重算底部占位。
        # 默认 None（零行为变化）；回调无参，异常吞掉（构建期/未挂载）。
        self.on_layout_change = None
        # 批 114 用户 m03074：日志要能任意多行拖选/Shift 选择。
        # 选型 B（保留 ListView 行渲染 + 外层 SelectionArea）：A 方案把正文改成
        # 一整块只读多行 TextField 会丢掉逐行颜色、自动滚底与限流渲染；SelectionArea
        # 跨行选择交给 Flutter 选区机制，行控件/颜色/滚底全部保留，复制按钮兜底。
        self.list = ft.ListView(
            expand=True, spacing=t.px(2), auto_scroll=True,
            padding=_pad(t, 0, 0, 4, 0),
            scroll=scrollbar(t, thickness=4),
        )
        self._selection = ft.SelectionArea(content=self.list)
        # Column 主轴不给子项紧约束：SelectionArea 会按内容高度铺开，ListView
        # 便没有可滚动视口（也就没有滚动条，底行被 _log_box 裁掉）。套一层
        # expand Container 把 200px 高度传下去，日志才真正滚 + 常显细条。
        self._selection_host = ft.Container(expand=True, content=self._selection)
        self.empty = text("等待运行…", t, "small", t.palette.log_muted)
        self.list.controls.append(self.empty)
        # 用户 m02696：「只看摘要」原是 Material Switch（48 宽灰 pill）→ 改为扁平
        # 图标+文字 toggle；选中只变字色，不再有灰底块。
        self._fold_btn = toggle_button(
            "只看摘要", self._on_fold, t, icon="FILTER_LIST", active=self._fold_only)
        # 浮层标题栏（收起后只剩这一行）：标题 + 展开/收起 + 只看摘要 + 复制 + 清空。
        self._toggle_btn = None
        self._toast = toast
        self._mark = mark
        self._copy_btn = text_button("复制", self._on_copy, t)
        toolbar_controls = [
            ft.Icon(ic("TERMINAL"), size=t.px(18), color=t.palette.text_muted),
            text(title, t, role="section"),
            ft.Container(expand=True),
            self._fold_btn,
        ]
        if dock:
            self._toggle_btn = icon_button(
                "KEYBOARD_ARROW_UP", "展开日志", self._on_toggle, t)
            toolbar_controls.append(self._toggle_btn)
        toolbar_controls.append(self._copy_btn)
        toolbar_controls.append(text_button("清空", self._on_clear, t))
        toolbar = ft.Row(
            spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=toolbar_controls,
        )
        # 收起 = 标题栏仍在；展开 = 固定高度日志区（VSCode 输出面板观感）。
        # 用户 m03166：日志框要能上下拖拽放大/缩小阅读更多内容（全壳共用本组件）。
        self._base_height = t.px(height)
        self._min_height = t.px(96)
        self._max_height = t.px(640)
        self._log_height = self._base_height
        self._resize_handle = ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.RESIZE_UP_DOWN,
            # R03：拖拽把手没有文字/图标，只有悬停提示能说明它是干什么的。
            tooltip="拖拽调整日志高度（双击复位）",
            on_vertical_drag_update=self._on_resize_update,
            on_double_tap=self._on_resize_reset,
            content=ft.Container(
                height=t.px(9), alignment=ft.Alignment.CENTER,
                content=ft.Container(
                    width=t.px(36), height=t.px(3), border_radius=t.radius("pill"),
                    bgcolor=t.palette.border,
                ),
            ),
        )
        self._log_box = ft.Container(
            height=self._log_height, bgcolor=t.palette.log_bg,
            border=box(t), border_radius=t.radius("md"),
            padding=_pad(t, 12, 8, 12, 10),
            content=ft.Column(
                spacing=0, expand=True,
                controls=[self._resize_handle, self._selection_host],
            ),
            visible=self._expanded,
        )
        root_kwargs = {}
        if dock:
            root_kwargs = {
                "bgcolor": t.palette.surface,
                "border": ft.Border(top=_side(t)),
                "border_radius": 0,
                "padding": _pad(t, 16, 6, 16, 6),
            }
        else:
            root_kwargs = {
                "bgcolor": t.palette.surface_2,
                "border_radius": t.radius("lg"),
                "padding": _pad(t, 16, 14, 16, 14),
            }
        self.root = ft.Container(
            content=ft.Column(spacing=t.sp("1"), controls=[toolbar, self._log_box]),
            **root_kwargs,
        )
        # Stack 定位壳：贴内容区底部、跨满宽度（由 page_body(overlay=...) 使用）。
        self.dock_root = ft.Container(left=0, right=0, bottom=0, content=self.root)

    # -- 拖拽缩放（用户 m03166：日志框可上下拉伸；双击恢复默认高度）---------
    def _on_resize_update(self, e) -> None:
        delta = float(getattr(e, "primary_delta", 0) or 0)
        if not delta:
            return
        # 向上拖（delta 为负）→ 变高；夹在 [min, max] 之间。
        self._log_height = max(self._min_height,
                               min(self._max_height, self._log_height - delta))
        if self._log_box.height != self._log_height:
            self._log_box.height = self._log_height
            try:
                self._log_box.update()
            except Exception:  # noqa: BLE001 构建期/收起时可能未挂载
                pass
        self._notify_layout_change()

    def _on_resize_reset(self, e=None) -> None:
        self._log_height = self._base_height
        self._log_box.height = self._base_height
        try:
            self._log_box.update()
        except Exception:  # noqa: BLE001
            pass
        self._notify_layout_change()

    # -- 浮层收展 -----------------------------------------------------
    def _on_toggle(self, e) -> None:
        self.toggle()

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def expand(self) -> None:
        self.set_expanded(True)

    def collapse(self) -> None:
        self.set_expanded(False)

    def set_expanded(self, value: bool) -> None:
        if not self.dock:
            return
        self._expanded = bool(value)
        self._log_box.visible = self._expanded
        if self._toggle_btn is not None:
            self._toggle_btn.icon = ic("KEYBOARD_ARROW_DOWN" if self._expanded
                                       else "KEYBOARD_ARROW_UP")
            self._toggle_btn.tooltip = "收起日志" if self._expanded else "展开日志"
        self.list.auto_scroll = True
        self._update_root()
        self._notify_layout_change()

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def _notify_layout_change(self) -> None:
        callback = self.on_layout_change
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 宿主重排失败不能拖垮日志
            pass

    def _update_root(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001 未挂载（构建期/证据脚本）
            pass


    # -- 内部 ---------------------------------------------------------
    def _on_fold(self, e=None) -> None:
        self._fold_only = not self._fold_only
        self._fold_btn.style = toggle_style(self.t, active=self._fold_only)
        self._fold_btn._toggle_active = self._fold_only
        try:
            self._fold_btn.update()
        except Exception:  # noqa: BLE001 未挂载（构建期/证据脚本）
            pass
        self._render()

    def _on_clear(self, e) -> None:
        self.clear()

    def _display_text(self) -> str:
        """当前展示内容（尊重「只看摘要」过滤），供复制。"""
        return "\n".join(value for value, _level, summary in self._lines
                         if not (self._fold_only and not summary))

    async def _on_copy(self, e=None) -> None:
        # 批 114 用户 m03074：复制当前展示内容；拖选是 SelectionArea 的手势，
        # 这里用 Clipboard 服务兜底纯文本（尊重只看摘要）。
        value = self._display_text()
        lines = value.count("\n") + 1 if value else 0
        copied = False
        if value:
            page = None
            try:
                page = self.list.page
            except Exception:  # noqa: BLE001  未挂载（构建期/证据脚本）
                page = None
            if page is not None:
                try:
                    clipboard = ft.Clipboard()
                    page.services.append(clipboard)
                    await clipboard.set(value)
                    copied = True
                except Exception:  # noqa: BLE001  客户端剪贴板不可用时报错提示
                    copied = False
        if self._mark is not None:
            try:
                self._mark("log_copied", lines=lines, chars=len(value), ok=copied)
            except Exception:  # noqa: BLE001
                pass
        if self._toast is not None:
            if not value:
                message = "日志为空，没有可复制的内容"
            elif copied:
                message = f"已复制 {lines} 行日志"
            else:
                message = "复制失败：剪贴板不可用"
            try:
                self._toast(message)
            except Exception:  # noqa: BLE001
                pass

    def _color(self, level: str) -> str:
        p = self.t.palette
        return {"ok": p.success, "warn": p.warn, "error": p.error, "dim": p.log_muted}.get(
            level, p.log_text)

    def _item(self, value: str, level: str) -> ft.Text:
        return text(value, self.t, role="log", color=self._color(level), selectable=True)

    def _render(self) -> None:
        controls: list = []
        for value, level, summary in self._lines:
            if self._fold_only and not summary:
                continue
            controls.append(self._item(value, level))
        self._empty_visible = not controls
        self.list.controls = controls or [self.empty]
        self._update()

    def _update(self) -> None:
        try:
            self.list.update()
        except Exception:  # noqa: BLE001  页面还没挂载时忽略（构建期调用）
            pass

    def _touch(self) -> None:
        """合并渲染：控件已入列，限流发 update；尾部行由定时任务在 100ms 内补刷。"""
        self._dirty += 1
        now = time.monotonic()
        if self._dirty >= self.FLUSH_BATCH or (now - self._last_update) >= self.FLUSH_INTERVAL_S:
            self._flush(now)
            return
        try:
            target = self.list.page
        except RuntimeError:      # 未挂载的控件访问 .page 会抛错（构建期/证据脚本）
            target = None
        if target is None or self._flush_scheduled:
            return
        self._flush_scheduled = True
        try:
            target.run_task(self._flush_later)
        except Exception:  # noqa: BLE001 - 无事件循环（证据脚本/构建期）：下次 add_line 兜底
            self._flush_scheduled = False

    async def _flush_later(self) -> None:
        try:
            await asyncio.sleep(self.FLUSH_INTERVAL_S)
        finally:
            self._flush_scheduled = False
        self._flush(time.monotonic())

    def _flush(self, now: float) -> None:
        self._dirty = 0
        self._last_update = now
        self._update()

    # -- 对外 ---------------------------------------------------------
    def add_line(self, value: str, level: str = "info", summary: bool = True) -> None:
        self._lines.append((value, level, summary))
        if len(self._lines) > 500:
            del self._lines[:200]
            self._render()
            return
        if self._fold_only and not summary:
            return
        if self._empty_visible:
            self.list.controls = []
            self._empty_visible = False
        self.list.controls.append(self._item(value, level))
        self._touch()

    def clear(self) -> None:
        self._lines.clear()
        self.list.controls = [self.empty]
        self._empty_visible = True
        self._update()


# ── 状态栏 ──────────────────────────────────────────────────────────────────
class StatusBar:
    """26 高底部状态栏：只留一句人话（无 DPI / 缩放 / Flet 版本串）。"""

    def __init__(self, t: Tokens) -> None:
        self.t = t
        self.text = text("就绪", t, "caption", t.palette.text_muted)
        self.dot = status_dot("ok", t, 6)
        self.progress = progress_bar(0, t, width=t.px(140))
        self.progress.visible = False
        self.meta = text("", t, "caption", t.palette.text_faint)
        self.root = ft.Container(
            height=t.row_h("status"),
            bgcolor=t.palette.sidebar,
            border=ft.Border(top=_side(t)),
            padding=_pad(t, 12, 0, 12, 0),
            content=ft.Row(
                spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[self.dot, self.text, self.progress,
                          ft.Container(expand=True), self.meta],
            ),
        )

    def set_status(self, value: str, progress: float | None = None) -> None:
        self.text.value = value
        # D3（R04）：不能只看「错误/失败/运行」三词——`结束（退出码 2）` 这类
        # 失败结束、`退出码 1` / `已取消` 这类非成功非失败态都要有自己的颜色。
        # 判定优先级：显式失败 > 退出码 > 取消/停止请求 > 运行中 > 其余视为就绪。
        tone = "ok"
        if any(word in value for word in ("启动失败", "失败", "错误")):
            tone = "error"
        else:
            match = re.search(r"退出码\s*(-?\d+)", value)
            code = int(match.group(1)) if match else None
            if code is not None and (code >= 2 or code < 0):
                tone = "error"
            elif code == 1:
                tone = "warn"
            elif any(word in value for word in ("已取消", "停止请求")):
                tone = "warn"
            elif "运行" in value:
                tone = "run"
        self.dot.bgcolor = {
            "ok": self.t.palette.success, "run": self.t.palette.accent,
            "warn": self.t.palette.warn, "error": self.t.palette.error,
        }[tone]
        if progress is None:
            self.progress.visible = False
        else:
            self.progress.visible = True
            self.progress.value = max(0.0, min(1.0, progress))
        self._update()

    def set_meta(self, value: str) -> None:
        self.meta.value = value
        self._update()

    def _update(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass


# ── Toast（应用内横幅：不依赖 overlay/snackbar API，截图里也看得见）─────────
class Toast:
    def __init__(self, t: Tokens) -> None:
        self.t = t
        self.text_ctl = text("", t, "small", t.palette.on_accent_soft)
        self.root = ft.Container(
            visible=False,
            bgcolor=t.palette.accent_soft,
            border=box(t, t.palette.accent),
            border_radius=t.radius("pill"),
            padding=_pad(t, 14, 8, 14, 8),
            content=ft.Row(spacing=t.sp("1"), controls=[
                ft.Icon(ic("CHECK_CIRCLE_OUTLINE", "CHECK"), size=t.px(16),
                        color=t.palette.on_accent_soft),
                self.text_ctl,
            ]),
        )

    def show(self, page: ft.Page, message: str) -> None:
        self.text_ctl.value = message
        self.root.visible = True
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass
        page.run_task(self._hide_later)

    async def _hide_later(self) -> None:
        await asyncio.sleep(2.2)
        self.root.visible = False
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass


# ── 确认对话框（危险操作前确认）───────────────────────────────────────────
def _invoke_confirm(cb) -> None:
    """点击确认时调用业务回调。

    历史原因调用方存在两种写法：零参 `lambda: ...` 与事件参 `lambda e: ...`。
    统一在此兼容，避免 `TypeError: <lambda>() missing 1 required positional argument: 'e'`
    （BUG-3-1：入藏确认写入崩溃）。
    """
    try:
        params = inspect.signature(cb).parameters
    except (TypeError, ValueError):  # C 扩展/无法取签名时按零参处理
        params = {}
    if params:
        cb(None)
    else:
        cb()


_open_dialogs: list = []


def show_dialog(page, dialog) -> None:
    """展示对话框并登记到全局栈（Esc / 取消 / 确认统一走 dismiss_top_dialog）。

    为什么必须登记：批 121 的清理确认框直接 ``page.show_dialog()`` 未入栈，
    导致「取消」「移入回收目录」点了都关不掉（用户 m02467 反馈）。
    """
    _open_dialogs.append(dialog)
    page.show_dialog(dialog)


def dismiss_top_dialog(page) -> bool:
    """关闭最上层对话框；返回是否真的关掉了一个。

    兼容两类对话框：经 ``show_dialog()`` 登记的与历史未登记的（栈空时仍尝试
    ``page.pop_dialog()``，避免旧入口再次卡死）。
    """
    popped = False
    if _open_dialogs:
        _open_dialogs.pop()
        popped = True
    try:
        # Flet 1.0.5：Page.pop_dialog() 返回被关掉的对话框（无对话框时 None）。
        removed = page.pop_dialog()
        return popped or removed is not None
    except Exception:  # noqa: BLE001 —— 测试桩/无对话框时不阻塞
        return popped


def confirm(page: ft.Page, t: Tokens, *, title: str, message: str,
            confirm_label: str = "继续", on_confirm=None) -> None:
    def _close() -> None:
        if _open_dialogs:
            _open_dialogs.pop()
        page.pop_dialog()

    def _ok(e) -> None:
        _close()
        if on_confirm:
            _invoke_confirm(on_confirm)

    def _cancel(e) -> None:
        _close()

    dialog = ft.AlertDialog(
        modal=True,
        title=text(title, t, role="dialog-title"),
        content=ft.Container(width=t.px(360), content=text(message, t, "body", t.palette.text_muted)),
        actions=[
            text_button("取消", _cancel, t),
            primary_button(confirm_label, _ok, t),
        ],
    )
    _open_dialogs.append(dialog)
    page.show_dialog(dialog)


# ── 通知条（P2：代替对话框的轻量提示，三种语气）───────────────────────────
def notice_bar(message: str, t: Tokens, tone: str = "info") -> ft.Container:
    palette = {
        "info": (t.palette.accent_soft, t.palette.on_accent_soft, "INFO_OUTLINE", "INFO"),
        "warn": (t.palette.warn_soft, t.palette.warn, "WARNING_AMBER_OUTLINED", "WARNING_AMBER"),
        "error": (t.palette.error_soft, t.palette.error, "ERROR_OUTLINE", "ERROR"),
        "success": (t.palette.success_soft, t.palette.success, "CHECK_CIRCLE_OUTLINE", "CHECK_CIRCLE"),
    }.get(tone, (t.palette.accent_soft, t.palette.on_accent_soft, "INFO_OUTLINE", "INFO"))
    bgcolor, fg, icon, fallback = palette
    return ft.Container(
        bgcolor=bgcolor,
        border_radius=t.radius("sm"),
        padding=_pad(t, 12, 8, 12, 8),
        content=ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.Icon(ic(icon, fallback), size=t.px(16), color=fg),
            text(message, t, role="small", color=fg, expand=True),
        ]),
    )


# ── 结果横幅（退出码 + 契约摘要 + 产物打开/复制）──────────────────────────
class ResultBanner:
    def __init__(self, t: Tokens, *, on_open=None, on_copy=None) -> None:
        self.t = t
        self.on_open = on_open
        self.on_copy = on_copy
        self.icon = ft.Icon(ic("INFO_OUTLINE", "INFO"), size=t.px(18), color=t.palette.text_muted)
        self.title = text("尚未运行", t, role="body-strong")
        self.detail = text("", t, role="small", color=t.palette.text_muted)
        self.open_btn = text_button("打开产物", self._open, t)
        self.copy_btn = text_button("复制路径", self._copy, t)
        self.root = ft.Container(
            visible=False,
            bgcolor=t.palette.surface_2,
            border=box(t),
            border_radius=t.radius("md"),
            padding=_pad(t, 14, 10, 14, 10),
            content=ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                self.icon,
                ft.Column(spacing=t.sp("half"), expand=True, controls=[self.title, self.detail]),
                self.copy_btn,
                self.open_btn,
            ]),
        )

    def _open(self, e) -> None:
        if self.on_open:
            self.on_open((self.detail.value or "").strip())

    def _copy(self, e) -> None:
        if self.on_copy:
            self.on_copy((self.detail.value or "").strip())

    def show(self, result: str, detail: str = "", level: str = "info", artifact: str = "") -> None:
        color = {
            "ok": self.t.palette.success,
            "warn": self.t.palette.warn,
            "error": self.t.palette.error,
        }.get(level, self.t.palette.text_muted)
        self.icon.color = color
        self.title.value = result
        self.title.color = color
        self.detail.value = artifact or detail
        self.open_btn.visible = bool(artifact)
        self.copy_btn.visible = bool(artifact)
        self.root.visible = True
        self._update()

    def reset(self) -> None:
        self.root.visible = False
        self.title.value = "尚未运行"
        self.title.color = self.t.palette.text
        self.detail.value = ""
        self._update()

    def _update(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001
            pass


# ── 不确定进度条（长任务：引擎总行数未知）─────────────────────────────────
def busy_bar(t: Tokens, *, visible: bool = False, width: float | None = None) -> ft.ProgressBar:
    return ft.ProgressBar(
        width=t.px(width) if width else None,
        bar_height=max(2, round(4 * t.k)),
        value=None,
        visible=visible,
        color=t.palette.accent,
        bgcolor=t.palette.surface_3,
        border_radius=t.radius("pill"),
        expand=width is None,
    )
