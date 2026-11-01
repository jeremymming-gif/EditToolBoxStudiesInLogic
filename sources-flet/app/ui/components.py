"""B-246c P1 · 可复用组件库（D3）。

全部组件只吃 `Tokens`（颜色/间距/字号/缩放都从 token 来），因此：
- 换主题 = 换一张色板重建，不改组件；
- 换 DPI / 用户缩放 = 换一个 `Tokens.k` 重建，组件代码零改动；
- 本模块不碰业务状态，页面只负责把回调接进来。
"""
from __future__ import annotations

import asyncio

import flet as ft

from ui.tokens import Tokens

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
         weight: str = "regular", **kwargs) -> ft.Text:
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
    controls = [text(title, t, "h3", weight="semibold")]
    if note:
        controls[-1] = controls[0]
        return ft.Row(
            controls=[text(title, t, "h3", weight="semibold"),
                      ft.Container(expand=True),
                      text(note, t, "caption", t.palette.text_faint)],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
    return ft.Row(controls=controls)


def page_header(title: str, subtitle: str, t: Tokens, actions: list | None = None) -> ft.Row:
    left = ft.Column(
        spacing=t.sp("half"),
        controls=[text(title, t, "h1", weight="semibold"), text(subtitle, t, "body", t.palette.text_muted)],
    )
    return ft.Row(
        controls=[ft.Container(content=left, expand=True),
                  ft.Row(controls=actions or [], spacing=t.sp("1"),
                         vertical_alignment=ft.CrossAxisAlignment.CENTER)],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def page_body(*controls, t: Tokens, gap: float = 16) -> ft.Container:
    body = ft.Column(
        controls=list(controls),
        spacing=t.sp("2") if gap == 16 else t.px(gap),
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    return ft.Container(
        padding=_pad(t, 24, 12, 24, 18), expand=True, content=body,
        bgcolor=t.palette.bg,
    )


# ── 按钮 ────────────────────────────────────────────────────────────────────
def primary_button(label: str, on_click, t: Tokens, *, icon: str | None = None,
                   disabled: bool = False) -> ft.FilledButton:
    return ft.FilledButton(
        label,
        icon=ic(icon) if icon else None,
        bgcolor=t.palette.accent,
        color=t.palette.on_accent,
        on_click=on_click,
        disabled=disabled,
    )


def secondary_button(label: str, on_click, t: Tokens, *, icon: str | None = None,
                     disabled: bool = False) -> ft.OutlinedButton:
    return ft.OutlinedButton(
        label,
        icon=ic(icon) if icon else None,
        on_click=on_click,
        disabled=disabled,
    )


def text_button(label: str, on_click, t: Tokens) -> ft.TextButton:
    return ft.TextButton(label, on_click=on_click)


def icon_button(icon: str, tooltip: str, on_click, t: Tokens, *,
                color: str | None = None) -> ft.IconButton:
    return ft.IconButton(ic(icon), tooltip=tooltip, on_click=on_click,
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
                text(label, t, "body", color, weight="medium" if active else "regular"),
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
    title_row = [text(label, t, "h3", weight="medium")]
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
                text(title, t, "h3", t.palette.text_muted, weight="medium"),
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
        content=text(label, t, "small", p.on_accent_soft if active else p.text_muted,
                     weight="medium" if active else "regular"),
    )


# ── 日志台（流式 / 清空 / 折叠）────────────────────────────────────────────
class LogConsole:
    """运行日志：行级颜色 + 摘要/原始折叠 + 清空；页面只调 add_line/clear。"""

    def __init__(self, t: Tokens, *, height: float = 200, title: str = "运行日志") -> None:
        self.t = t
        self._lines: list[tuple[str, str, bool]] = []
        self._fold_only = False
        self.list = ft.ListView(expand=True, spacing=t.px(2), auto_scroll=True, padding=0)
        self.empty = text("等待运行…", t, "small", t.palette.log_muted)
        self.list.controls.append(self.empty)
        self._fold_switch = ft.Switch(
            value=False, label="只看摘要", on_change=self._on_fold, active_color=t.palette.accent,
        )
        toolbar = ft.Row(
            spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ic("TERMINAL"), size=t.px(18), color=t.palette.text_muted),
                text(title, t, "h3", weight="semibold"),
                ft.Container(expand=True),
                self._fold_switch,
                text_button("清空", self._on_clear, t),
            ],
        )
        self.root = ft.Container(
            bgcolor=t.palette.surface_2, border_radius=t.radius("lg"),
            padding=_pad(t, 16, 14, 16, 14),
            content=ft.Column(spacing=t.sp("1"), controls=[
                toolbar,
                ft.Container(
                    height=t.px(height), bgcolor=t.palette.log_bg,
                    border=box(t), border_radius=t.radius("md"),
                    padding=_pad(t, 12, 10, 12, 10),
                    content=self.list,
                ),
            ]),
        )

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
        return ft.Text(value, size=self.t.font("small"), color=self._color(level),
                       font_family="Consolas", selectable=True)

    def _render(self) -> None:
        controls: list = []
        for value, level, summary in self._lines:
            if self._fold_only and not summary:
                continue
            controls.append(self._item(value, level))
        self.list.controls = controls or [self.empty]
        self._update()

    def _update(self) -> None:
        try:
            self.list.update()
        except Exception:  # noqa: BLE001  页面还没挂载时忽略（构建期调用）
            pass

    # -- 对外 ---------------------------------------------------------
    def add_line(self, value: str, level: str = "info", summary: bool = True) -> None:
        self._lines.append((value, level, summary))
        if len(self._lines) > 500:
            del self._lines[:200]
            self._render()
            return
        if self._fold_only and not summary:
            return
        if self.empty in self.list.controls:
            self.list.controls.remove(self.empty)
        self.list.controls.append(self._item(value, level))
        self._update()

    def clear(self) -> None:
        self._lines.clear()
        self.list.controls = [self.empty]
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
        title=text(title, t, "h2", weight="semibold"),
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
            ft.Text(message, size=t.font("small"), color=fg, expand=True),
        ]),
    )


# ── 结果横幅（退出码 + 契约摘要 + 产物打开/复制）──────────────────────────
class ResultBanner:
    def __init__(self, t: Tokens, *, on_open=None, on_copy=None) -> None:
        self.t = t
        self.on_open = on_open
        self.on_copy = on_copy
        self.icon = ft.Icon(ic("INFO_OUTLINE", "INFO"), size=t.px(18), color=t.palette.text_muted)
        self.title = ft.Text("尚未运行", size=t.font("body"), weight=_w(t, "semibold"), color=t.palette.text)
        self.detail = ft.Text("", size=t.font("small"), color=t.palette.text_muted)
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
