"""Build the nine settings detail views from existing settings controls."""
from __future__ import annotations

import flet as ft

from ui import ai_panel as AIP
from ui import components as C
from ui import settings_shell as SHELL
from ui.ia_map import route_labels, stats as ia_stats, view_label


def _titled_column(control: ft.Control):
    if isinstance(control, ft.Container) and isinstance(control.content, ft.Column):
        column = control.content
    elif isinstance(control, ft.Column):
        column = control
    else:
        return None, None
    if column.controls:
        head = column.controls[0]
        if (isinstance(head, ft.Row) and head.controls
                and isinstance(head.controls[0], ft.Text)):
            return column, head
    return None, None


def _row_note(head: ft.Row, title: str) -> str | None:
    for item in head.controls[1:]:
        if isinstance(item, ft.Text) and item.value and str(item.value) != title:
            return str(item.value)
    return None


def flat(page, control: ft.Control, title: str) -> ft.Control:
    t = page.t
    column, head = _titled_column(control)
    if column is None:
        return control
    if head is None or str(head.controls[0].value) != title:
        return column
    note = _row_note(head, title)
    column.controls = [C.section_label(title, t, note=note), *column.controls[1:]]
    return column


def _block(page, label: str, *controls, note: str | None = None) -> ft.Column:
    t = page.t
    return ft.Column(spacing=t.sp("1"), controls=[
        C.section_label(label, t, note=note),
        C.hairline(t),
        *controls,
    ])


def _appearance(page) -> ft.Control:
    t = page.t
    return _block(page, "外观",
        C.block(
            ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                C.text("深色模式", t, "body"),
                ft.Container(expand=True),
                C.switch(t, value=page.app.t.is_dark,
                         on_change=page.app.on_theme_switch),
            ]),
            ft.Row(spacing=t.sp("2"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                   controls=[
                C.text("界面缩放", t, "body"),
                ft.Slider(**page._zoom_slider_spec(t), active_color=t.palette.accent,
                          expand=True, on_change=page._zoom_changed),
                C.text(f"{page.app.effective_zoom:.2f}×（上限 {page.app.max_zoom:.2f}×）",
                       t, "small", t.palette.text_muted, "medium"),
            ]),
            SHELL.button_palette_row(page),
            C.text(f"屏幕工作区钳制：本屏最大 {page.app.max_zoom:.2f}×；"
                   "更大缩放会被限制，窗口不会越界。",
                   t, "caption", t.palette.text_faint),
            t=t, gap="2",
        ),
    )


def _engine(page) -> ft.Control:
    t = page.t
    page._engine_status_note = page._engine_note()
    envcheck = flat(page, SHELL.envcheck_card(page), "环境检查")
    return _block(page, "引擎与环境", page._engine_status_note, envcheck,
                  note="状态行和环境检查均为只读信息")


def _paths(page) -> ft.Control:
    t = page.t
    return _block(page, "路径",
        C.text("引擎目录留空 = 自动定位", t, "caption", t.palette.text_faint),
        page._path_field("engine_override", "引擎目录（可空）", kind="dir"),
        page._path_field("notes_repo", "私有笔记仓（可空）", kind="dir",
                         hint="笔记仓根目录（含 cases/）",
                         note="填笔记仓根目录（里面要有 cases/），不要填它下面的 bib_db/。"),
    )


def _diagnostics(page) -> ft.Control:
    return ft.Column(spacing=page.t.sp("2"), controls=[
        flat(page, SHELL.logs_card(page), "日志"),
        flat(page, SHELL.diagnostics_card(page), "诊断包"),
    ])


def _advanced(page) -> ft.Control:
    t = page.t
    about_version = C.kv_row(t, "应用版本", page.app.app_version, mono=True)
    page._about_version = about_version.value_ctl
    about_rows = [
        ("Flet / Python", f"{page.app.flet_version} · {page.app.python_version}"),
        ("窗口 / DPI", f"{page.app.window_w}×{page.app.window_h} @ {page.app.dpi} "
                        f"({page.app.dpi / 96:.0%})"),
        ("IA", f"{ia_stats()['legacy_pages']} 个旧页 → "
                f"{ia_stats()['new_pages']} 页 + 子流程"),
        ("许可", "专有软件 · © 2026 Jeremymming（第三方许可见 THIRD_PARTY.md）"),
    ]
    about = _block(page, "关于", about_version,
                   *[C.kv_row(t, label, value) for label, value in about_rows])
    return ft.Column(spacing=t.sp("2"), controls=[
        flat(page, SHELL.advanced_card(page), "高级"),
        about,
    ])


def _window(page) -> ft.Control:
    return flat(page, SHELL.tray_card(page), "窗口与托盘")


def _language(page) -> ft.Control:
    t = page.t
    return _block(page, "语言与地区",
        C.kv_row(t, "界面语言", "简体中文"),
        C.kv_row(t, "排序区域", "使用系统区域设置"),
    )


def _ai(page) -> ft.Control:
    return flat(page, AIP.AiSettings(page).build(), "AI")


def _proxy(page) -> ft.Control:
    return flat(page, SHELL.proxy_card(page), "网络代理")


_BUILDERS = {
    "appearance": _appearance,
    "engine": _engine,
    "paths": _paths,
    "diagnostics": _diagnostics,
    "advanced": _advanced,
    "window": _window,
    "language": _language,
    "ai": _ai,
    "proxy": _proxy,
}


def build(page, view: str) -> ft.Control:
    t = page.t
    builder = _BUILDERS.get(view)
    if builder is None:
        return ft.Column()
    labels = route_labels(f"settings:{view}")
    back = C.text_button("返回设置", lambda e: page.app.navigate(
        "settings", source="settings_back"), t, icon="ARROW_BACK")
    header = C.page_header(view_label("settings", view), labels["crumb"], t,
                           actions=[back])
    return ft.Column(spacing=t.sp("2"), controls=[header, builder(page)])
