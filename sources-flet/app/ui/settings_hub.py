"""Settings overview: safe summaries and route rows."""
from __future__ import annotations

import os

import flet as ft

from ui import components as C
from ui import settings_shell as SHELL
from ui.ia_map import group_views


_ICONS = {
    "appearance": "DARK_MODE",
    "engine": "SETTINGS_OUTLINED",
    "paths": "FOLDER_OPEN_OUTLINED",
    "diagnostics": "BUG_REPORT_OUTLINED",
    "advanced": "INFO_OUTLINE",
    "window": "WINDOW_OUTLINED",
    "language": "LANGUAGE_OUTLINED",
    "ai": "PSYCHOLOGY_OUTLINED",
    "proxy": "NETWORK_CHECK",
}


def _summary(page, key: str) -> str:
    store = page.app.store
    if key == "appearance":
        mode = "深色" if page.app.t.is_dark else "浅色"
        return f"{mode} · {float(page.app.effective_zoom):.2f}×"
    if key == "engine":
        engine = page.app.engine_status
        state = "就绪" if engine.ok else "未就绪"
        checked = "已检查" if store.get("env_check_done", False) else "未检查"
        return f"{state} · {engine.version or '版本未知'} · {checked}"
    if key == "paths":
        paths = store.get("paths", {}) or {}
        configured = sum(bool(value) for value in (
            paths.get("engine_override") or store.get("engine_override", ""),
            paths.get("notes_repo", ""),
        ))
        return f"已配置 {configured} 项" if configured else "未配置自定义路径"
    if key == "diagnostics":
        return f"内存日志 {SHELL.log_buffer(page.app).count()} 行 · 可导出诊断包"
    if key == "advanced":
        return f"应用版本 {page.app.app_version}"
    if key == "window":
        return "托盘已启用" if bool(getattr(page.app, "tray_enabled", False)) else "托盘未启用"
    if key == "language":
        return "简体中文 · 使用系统区域设置"
    if key == "ai":
        ai = store.get("ai", {}) or {}
        enabled = "已开启" if bool(ai.get("enabled", False)) else "已关闭"
        model = str(ai.get("model") or "默认模型").strip()
        if len(model) > 28:
            model = model[:27] + "…"
        return f"{enabled} · {model}"
    if key == "proxy":
        configured = any(os.environ.get(name) or os.environ.get(name.lower())
                         for name in ("HTTPS_PROXY", "HTTP_PROXY"))
        return "已配置代理" if configured else "未配置代理"
    if key == "bib":
        bibdb = store.get("bibdb", {}) or {}
        if "lookup_enabled" in bibdb:
            return "默认查库已开启" if bool(bibdb["lookup_enabled"]) else "默认查库已关闭"
        return "引用库稳定偏好"
    return "查看当前设置"


def _row(page, label: str, route: str, icon: str, summary: str) -> ft.Control:
    t, p = page.t, page.t.palette
    style = C._button_style(t)
    style.padding = C._pad(t, 8, 8, 8, 8)
    style.shape = ft.RoundedRectangleBorder(radius=t.radius("sm"))
    style.color = {
        ft.ControlState.DEFAULT: p.text,
        ft.ControlState.HOVERED: p.text,
        ft.ControlState.DISABLED: p.text_faint,
    }
    content = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                     controls=[
        ft.Icon(C.ic(icon), size=t.px(18), color=p.accent),
        C.text(label, t, "body"),
        ft.Container(expand=True),
        C.text(summary, t, "caption", p.text_muted, max_lines=1,
               overflow=ft.TextOverflow.ELLIPSIS),
        ft.Icon(C.ic("CHEVRON_RIGHT", "ARROW_FORWARD"),
                size=t.px(18), color=p.text_faint),
    ])
    button = ft.TextButton(
        content=content,
        on_click=lambda e: page.app.navigate(route, source="settings"),
        tooltip=f"{label}：{summary}",
        expand=True,
        style=style,
    )
    return button


def _group(page, label: str, rows: list[ft.Control]) -> ft.Column:
    t = page.t
    return ft.Column(spacing=t.sp("1"),
                     horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
        C.section_label(label, t),
        C.hairline(t),
        *rows,
    ])


def build(page) -> ft.Control:
    t = page.t
    groups = []
    for group in group_views("settings"):
        rows = [
            _row(page, str(view["label"]), f"settings:{view['key']}",
                 _ICONS.get(str(view["key"]), "SETTINGS_OUTLINED"),
                 _summary(page, str(view["key"])))
            for view in group["views"]
        ]
        groups.append(_group(page, str(group["label"]), rows))
    groups.append(_group(page, "引用库", [
        _row(page, "引用库偏好", "bib:settings", "MENU_BOOK", _summary(page, "bib")),
    ]))
    return ft.Column(spacing=t.sp("3"), controls=groups)
