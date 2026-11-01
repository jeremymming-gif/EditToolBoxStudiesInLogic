"""帮助与关于页：入口地图 + 平台差异 + 托盘/输入法/诊断 + 文档位置 + 许可。

从 `pages.py` 拆出：页面只排版，文案集中在 `help_content.py`；
`pages.py` 只保留 `PAGE_CLASSES` 里的引用，避免 1200+ 行文件继续膨胀。
"""
from __future__ import annotations

import flet as ft

from ui import components as C
from ui import help_content as HC
from ui import settings_shell as SHELL
from ui.base_page import BasePage


class HelpPage(BasePage):
    key = "help"

    def build(self) -> ft.Control:
        t = self.t
        app = self.app
        header = C.page_header(HC.TITLE, HC.SUBTITLE, t)
        entries = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("功能入口", t, note="侧栏六个入口 + 单篇页里的 AI 面板"),
            *[self._entry(name, where, self._entry_detail(name, detail))
              for name, where, detail in HC.ENTRIES],
        ]), t)
        platform = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("平台差异与托盘", t, note="托盘仅 Windows"),
            *[self._platform_row(name, text) for name, text in HC.PLATFORM],
            C.divider(t),
            C.section_title("关闭到托盘怎么用", t),
            *[self._bullet(step) for step in HC.TRAY_STEPS],
            *[C.text(line, t, "caption", t.palette.text_faint) for line in HC.TRAY_LIMITS],
        ]), t)
        details = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("输入法与操作细节", t),
            *[self._detail(label, text) for label, text in HC.DETAILS],
        ]), t)
        shortcuts = C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("快捷键", t, note="与外壳实际绑定同源"),
            *SHELL.shortcut_rows(t),
        ]), t)
        docs = C.card(ft.Column(spacing=t.sp("2"), controls=[
            C.section_title("文档在哪", t),
            *[self._doc(label, text) for label, text in HC.DOCS],
        ]), t)
        boundary = C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("边界与安全", t, note="写操作都有前置闸门"),
            *[self._bullet(line) for line in HC.BOUNDARY],
        ]), t)
        about = C.card(ft.Column(spacing=t.sp("1"), controls=[
            C.section_title("版本与许可", t),
            self._kv("应用版本", app.app_version),
            self._kv("引擎", f"{app.engine_status.version} · {app.engine_status.mode}"),
            self._kv("外壳", f"Flet {app.flet_version} · Python {app.python_version}"),
            self._kv("页面", "6 个（工作台 / 单篇稿件 / 整期装配 / 引用库 / 设置 / 帮助与关于）"),
            self._kv("许可", HC.LICENSE),
            self._kv("第三方", HC.THIRD_PARTY),
        ]), t)
        return C.page_body(header, entries, platform, details, shortcuts, docs,
                           boundary, about, t=t, gap=12)

    # ── 小工具 ─────────────────────────────────────────────────────────
    def _entry_detail(self, name: str, detail: str) -> str:
        if name != "AI 任务书":
            return detail
        enabled = bool(self.app.store.get("ai.enabled", False))
        state = "已启用" if enabled else "已关闭"
        return f"当前{state}；开启后按预算调用，禁止把未刊稿发给外部模型。"

    def _entry(self, name: str, where: str, detail: str) -> ft.Row:
        t = self.t
        body = [C.text(where, t, "small", t.palette.text)]
        if detail:
            body.append(C.text(detail, t, "caption", t.palette.text_muted))
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            ft.Container(width=t.px(92), content=C.text(name, t, "small", weight="medium")),
            ft.Column(spacing=t.px(2), expand=True, controls=body),
        ])

    def _platform_row(self, name: str, text: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            ft.Container(width=t.px(92), content=C.text(name, t, "small", weight="medium")),
            C.text(text, t, "small", t.palette.text_muted),
        ])

    def _detail(self, label: str, text: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            ft.Container(width=t.px(92), content=C.text(label, t, "small", weight="medium")),
            ft.Column(spacing=0, expand=True, controls=[C.text(text, t, "small", t.palette.text_muted)]),
        ])

    def _doc(self, label: str, text: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            ft.Icon(C.ic("DESCRIPTION_OUTLINED"), size=t.px(16), color=t.palette.accent),
            ft.Column(spacing=t.px(2), expand=True, controls=[
                C.text(label, t, "small", weight="medium"),
                C.text(text, t, "caption", t.palette.text_muted),
            ]),
        ])

    def _bullet(self, value: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            ft.Icon(C.ic("CHEVRON_RIGHT"), size=t.px(16), color=t.palette.accent),
            C.text(value, t, "small"),
        ])

    def _kv(self, label: str, value: str) -> ft.Row:
        t = self.t
        return ft.Row(spacing=t.sp("1"), controls=[
            C.text(label, t, "small", t.palette.text_muted),
            ft.Container(expand=True),
            C.text(str(value), t, "small", t.palette.text),
        ])

    def refresh_running(self) -> None:
        pass
