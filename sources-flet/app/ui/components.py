"""B-246c P1 · 可复用组件库（D3）。

全部组件只吃 `Tokens`（颜色/间距/字号/缩放都从 token 来），因此：
- 换主题 = 换一张色板重建，不改组件；
- 换 DPI / 用户缩放 = 换一个 `Tokens.k` 重建，组件代码零改动；
- 本模块不碰业务状态，页面只负责把回调接进来。
"""
from __future__ import annotations

import asyncio
import time

import flet as ft

from ui.tokens import MONO_ROLES, Tokens

# ── 小工具 ──────────────────────────────────────────────────────────────────
def ic(name: str, fallback: str = "CIRCLE") -> str:
    """图标名容错：Flet 版本差异下缺图标只降级，不让界面起不来。"""
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


def page_body(*controls, t: Tokens, gap: float = 16, overlay=None,
              overlay_reserve: float = 0) -> ft.Control:
    """页面内容区。给了 `overlay` 就改成 Stack：内容在下、浮层在上（后入 = z 更高）。

    `overlay_reserve` 是给浮层留出的底部内边距（px），避免浮层遮住内容最后一行。
    浮层自身用 `left/right/bottom=0` 贴住内容区底部（VSCode 输出面板式）。
    """
    body = ft.Column(
        controls=list(controls),
        spacing=t.sp("2") if gap == 16 else t.px(gap),
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    if overlay is not None and overlay_reserve:
        body.controls.append(ft.Container(height=t.px(overlay_reserve)))
    box = ft.Container(
        padding=_pad(t, 24, 12, 24, 18), expand=True, content=body,
        bgcolor=t.palette.bg,
    )
    if overlay is None:
        return box
    return ft.Stack(controls=[box, overlay], expand=True)


# ── 按钮 ────────────────────────────────────────────────────────────────────
def _button_style(t: Tokens) -> ft.ButtonStyle:
    """批 112 GUI-09：按钮字号锚定正文 role（×k），不再吃 Material labelLarge 的固定值。

    与 main._flet_theme 的 label_large 双保险：theme 管全局（输入框/勾选/下拉标签），
    这里再显式给四个按钮补 text_style/icon_size，保证任何主题/版本下都同源缩放。
    """
    return ft.ButtonStyle(
        text_style=ft.TextStyle(size=t.font("body"), weight=ft.FontWeight.W_500),
        icon_size=t.px(18),
    )


def primary_button(label: str, on_click, t: Tokens, *, icon: str | None = None,
                   disabled: bool = False) -> ft.FilledButton:
    return ft.FilledButton(
        label,
        icon=ic(icon) if icon else None,
        bgcolor=t.palette.accent,
        color=t.palette.on_accent,
        style=_button_style(t),
        on_click=on_click,
        disabled=disabled,
    )


def secondary_button(label: str, on_click, t: Tokens, *, icon: str | None = None,
                     disabled: bool = False) -> ft.OutlinedButton:
    return ft.OutlinedButton(
        label,
        icon=ic(icon) if icon else None,
        style=_button_style(t),
        on_click=on_click,
        disabled=disabled,
    )


def text_button(label: str, on_click, t: Tokens) -> ft.TextButton:
    # 批 112 GUI-09：TextButton 同样吃 labelLarge，显式锚定正文 role。
    return ft.TextButton(label, style=_button_style(t), on_click=on_click)


# ── 下拉框 ──────────────────────────────────────────────────────────────────
def dropdown(t: Tokens, *, value: str | None, options: list[tuple[str, str]],
             on_select=None, width: float | None = None, dense: bool = True,
             label: str | None = None, height: float | None = None,
             **kwargs) -> ft.Dropdown:
    """统一下拉框字号：关闭态（text_style）与菜单项（option.style）同锚正文 role。

    背景（批 112 GUI-10）：flet 1.0.5 关闭态字号走 `text_size`/`text_style`，菜单项走
    客户端 TextTheme；两者任一来源不同步就会出现「上面 key 大、菜单里 key 小」。这里
    显式双写，菜单项与关闭态永远同字号（与 main._flet_theme 的全量投影互补）。
    """
    body = t.font("body")
    text_color = t.palette.text
    item_style = ft.ButtonStyle(text_style=ft.TextStyle(size=body, color=text_color))
    # 高度与同排 TextField（dense + 上下 content_padding 8）齐平：默认 Material 下拉
    # 高度固定 48，不随 Tokens 缩放，放大后会比输入框高出一截（批 112 收尾 GUI-16）。
    box_h = height if height is not None else (t.px(18) + body * 1.5)
    return ft.Dropdown(
        value=value,
        options=[ft.DropdownOption(key=key, text=text, style=item_style)
                 for key, text in options],
        width=width,
        height=box_h,
        content_padding=_pad(t, 10, 8, 10, 8),
        dense=dense,
        text_size=t.font("body"),  # 守卫 test_b109_w45：输入类控件字号必须锚 body role
        text_style=ft.TextStyle(size=body, color=text_color),
        label=label,
        on_select=on_select,
        **kwargs,
    )


def icon_button(icon: str, tooltip: str, on_click, t: Tokens, *,
                color: str | None = None) -> ft.IconButton:
    return ft.IconButton(ic(icon), tooltip=tooltip, on_click=on_click,
                         icon_size=t.px(20),
                         icon_color=color or t.palette.text_muted)


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
        bgcolor=bg, border_radius=t.radius("pill"),
        padding=_pad(t, 8, 2, 8, 2),
        content=text(label, t, "caption", fg, weight="medium"),
    )


def status_chip(label: str, t: Tokens, state: str = "idle") -> ft.Container:
    tone = {"idle": "neutral", "done": "success", "running": "accent", "warn": "warn",
            "error": "error"}.get(state, "neutral")
    return badge(label, t, tone)


def progress_bar(value: float, t: Tokens, width: float | None = None) -> ft.ProgressBar:
    return ft.ProgressBar(
        value=max(0.0, min(1.0, value)),
        bar_height=t.px(6),
        width=width or t.px(160),
        color=t.palette.accent,
        bgcolor=t.palette.surface_3,
        border_radius=t.radius("pill"),
    )


# ── 导航 ────────────────────────────────────────────────────────────────────
def nav_item(icon: str, label: str, t: Tokens, *, active: bool, on_click) -> ft.Container:
    p = t.palette
    color = p.on_accent_soft if active else p.text_muted
    return ft.Container(
        on_click=on_click,
        bgcolor=p.accent_soft if active else None,
        border_radius=t.radius("sm"),
        padding=_pad(t, 10, 9, 10, 9),
        content=ft.Row(
            spacing=t.sp("1"),
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ic(icon), size=t.px(18), color=color),
                text(label, t, role="item-selected" if active else "body", color=color),
            ],
        ),
    )


def nav_group_label(label: str, t: Tokens) -> ft.Container:
    return ft.Container(
        padding=_pad(t, 10, 14, 10, 4),
        content=text(label, t, "caption", t.palette.text_faint, weight="medium"),
    )


# ── 路径选择 / 选项行 ───────────────────────────────────────────────────────
def path_picker(label: str, value: str, hint: str, t: Tokens, *, on_change=None,
                on_browse=None, required: bool = True, show_note: bool = True,
                field_ref: list | None = None) -> ft.Column:
    field = ft.TextField(
        value=value or "",
        hint_text=hint,
        label=label,
        dense=True,
        expand=True,
        on_change=on_change,
        text_size=t.font("body"),
    )
    if field_ref is not None:      # 页面拿得到 TextField，才能把「浏览」结果写回去
        field_ref.append(field)
    browse = secondary_button("浏览…", on_browse, t, icon="FOLDER_OPEN")
    controls = [
        ft.Row(controls=[field, browse], spacing=t.sp("1"),
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
    ]
    if show_note:
        controls.append(text("必填" if required else "可选", t, "caption", t.palette.text_faint))
    return ft.Column(spacing=t.sp("half"), controls=controls)


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
    title_row.append(ft.Checkbox(value=checked, on_change=on_change))
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


def stat_card(label: str, value: str, hint: str, t: Tokens) -> ft.Container:
    return card(
        ft.Column(spacing=t.sp("half"), controls=[
            text(label, t, "caption", t.palette.text_muted, weight="medium"),
            text(value, t, "h1", weight="semibold"),
            text(hint, t, "caption", t.palette.text_faint),
        ]),
        t, pad=14, expand=True,
    )


def chip(label: str, t: Tokens, *, active: bool, on_click) -> ft.Container:
    p = t.palette
    return ft.Container(
        on_click=on_click,
        bgcolor=p.accent_soft if active else None,
        border=box(t) if not active else box(t, p.accent_soft),
        border_radius=t.radius("pill"),
        padding=_pad(t, 14, 7, 14, 7),
        content=text(label, t, role="chip-selected" if active else "small",
                     color=p.on_accent_soft if active else p.text_muted),
    )


def group_tab(label: str, t: Tokens, *, active: bool, on_click=None,
              badge_label: str | None = None) -> ft.Container:
    """两级导航的一级（组 tab）：文字色 + 下划线表示选中，比二级 chip 矮。

    `badge_label` 预留给「治理」组的未处理数（P2 钩子暂不传，接口先留）。
    """
    p = t.palette
    row = [text(label, t, size="small", weight="semibold",
                color=p.accent if active else p.text_muted)]
    if badge_label:
        row.append(badge(str(badge_label), t, "accent" if active else "neutral"))
    return ft.Container(
        on_click=on_click,
        padding=_pad(t, 10, 6, 10, 6),
        border_radius=t.radius("sm"),
        content=ft.Column(spacing=t.px(4), controls=[
            ft.Row(spacing=t.px(4), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=row),
            ft.Container(height=max(1, t.px(2)), bgcolor=p.accent if active else None),
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
                 dock: bool = False) -> None:
        self.t = t
        self._lines: list[tuple[str, str, bool]] = []
        self._fold_only = False
        self._empty_visible = True
        self._dirty = 0
        self._last_update = 0.0
        self._flush_scheduled = False
        self.dock = dock
        self._expanded = not dock
        self.list = ft.ListView(expand=True, spacing=t.px(2), auto_scroll=True, padding=0)
        self.empty = text("等待运行…", t, "small", t.palette.log_muted)
        self.list.controls.append(self.empty)
        self._fold_switch = ft.Switch(
            value=False, label="只看摘要", on_change=self._on_fold, active_color=t.palette.accent,
        )
        # 浮层标题栏（收起后只剩这一行）：标题 + 展开/收起 + 只看摘要 + 清空。
        self._toggle_btn = None
        toolbar_controls = [
            ft.Icon(ic("TERMINAL"), size=t.px(18), color=t.palette.text_muted),
            text(title, t, role="section"),
            ft.Container(expand=True),
            self._fold_switch,
        ]
        if dock:
            self._toggle_btn = icon_button(
                "KEYBOARD_ARROW_UP", "展开日志", self._on_toggle, t)
            toolbar_controls.append(self._toggle_btn)
        toolbar_controls.append(text_button("清空", self._on_clear, t))
        toolbar = ft.Row(
            spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=toolbar_controls,
        )
        # 收起 = 标题栏仍在；展开 = 固定高度日志区（VSCode 输出面板观感）。
        self._log_box = ft.Container(
            height=t.px(height), bgcolor=t.palette.log_bg,
            border=box(t), border_radius=t.radius("md"),
            padding=_pad(t, 12, 10, 12, 10),
            content=self.list, visible=self._expanded,
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

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def _update_root(self) -> None:
        try:
            self.root.update()
        except Exception:  # noqa: BLE001 未挂载（构建期/证据脚本）
            pass


    # -- 内部 ---------------------------------------------------------
    def _on_fold(self, e) -> None:
        self._fold_only = bool(e.control.value)
        self._render()

    def _on_clear(self, e) -> None:
        self.clear()

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
    def __init__(self, t: Tokens) -> None:
        self.text = text("就绪", t, "caption", t.palette.text_muted)
        self.progress = progress_bar(0, t, width=t.px(140))
        self.progress.visible = False
        self.meta = text("", t, "caption", t.palette.text_faint)
        self.root = ft.Container(
            height=t.px(38),
            bgcolor=t.palette.surface,
            border=ft.Border(top=_side(t)),
            padding=_pad(t, 20, 0, 20, 0),
            content=ft.Row(
                spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[self.text, self.progress, ft.Container(expand=True), self.meta],
            ),
        )

    def set_status(self, value: str, progress: float | None = None) -> None:
        self.text.value = value
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
def confirm(page: ft.Page, t: Tokens, *, title: str, message: str,
            confirm_label: str = "继续", on_confirm=None) -> None:
    def _ok(e) -> None:
        page.pop_dialog()
        if on_confirm:
            on_confirm()

    def _cancel(e) -> None:
        page.pop_dialog()

    dialog = ft.AlertDialog(
        modal=True,
        title=text(title, t, role="dialog-title"),
        content=ft.Container(width=t.px(360), content=text(message, t, "body", t.palette.text_muted)),
        actions=[
            text_button("取消", _cancel, t),
            primary_button(confirm_label, _ok, t),
        ],
    )
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
