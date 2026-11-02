"""页面基类：惰性构建、字段引用、标记与运行态刷新。

从 `pages.py` 拆出（批 110）：帮助页独立成模块后不再反向 import 页面集合，
避免循环依赖；页面实现仍只做「取值 → 拼参数 → 显示日志」。
"""
from __future__ import annotations

import flet as ft

from ui import components as C


class BasePage:
    key = "base"

    def __init__(self, app) -> None:
        self.app = app
        self.page = app.page
        self.fields: dict[str, list] = {}
        self.root = None
        self._run_stats: dict = {}

    # 子类实现 ─────────────────────────────────────────────────────────
    def build(self):
        raise NotImplementedError

    def ensure_built(self):
        """惰性构建入口：没有 root 才 build；导航、预填、证据钩子都走这里。"""
        if self.root is None:
            self.root = self.build()
        return self.root

    # 公共小工具 ───────────────────────────────────────────────────────
    @property
    def t(self):
        return self.app.t

    def add_picker(self, label: str, recent_key: str, hint: str, *, kind: str = "file",
                   required: bool = True, show_note: bool = True,
                   on_change=None) -> ft.Container:
        ref: list = []
        value = self.app.store.get(f"recent.{recent_key}", "") or ""

        def _browse(e) -> None:
            self.app.browse_field(ref, f"选择{label}", kind, recent_key)

        def _changed(e) -> None:
            self.app.store.set_recent(recent_key, e.control.value.strip())
            self.mark("path_change", field=recent_key, value=e.control.value)
            if on_change is not None:
                on_change(e)

        control = C.path_picker(
            label, value, hint, self.t,
            on_change=_changed, on_browse=_browse, required=required,
            show_note=show_note, field_ref=ref,
        )
        self.fields[recent_key] = ref
        return control

    # ── 表单小工具 ─────────────────────────────────────────────────────
    def _meta_field(self, key: str, label: str, width: float):
        def _changed(e) -> None:
            self.app.store.set(f"form.{key}", e.control.value.strip())

        control = ft.TextField(
            label=label, value=self.app.store.get(f"form.{key}", "") or "",
            dense=True, text_size=self.t.font("body"),
            border={
                ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                    side=ft.BorderSide(1, self.t.palette.border)),
                ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                    side=ft.BorderSide(2, self.t.palette.accent)),
            },
            width=self.t.px(width), on_change=_changed,
            content_padding=C._pad(self.t, 10, 8, 10, 8),
        )
        self.fields[key] = [control]
        return control

    def field(self, recent_key: str) -> str:
        ref = self.fields.get(recent_key)
        if not ref:
            return ""
        return (ref[0].value or "").strip()

    def set_field(self, recent_key: str, value: str) -> None:
        ref = self.fields.get(recent_key)
        if ref:
            ref[0].value = value
            try:
                ref[0].update()
            except Exception:  # noqa: BLE001
                pass

    def mark(self, event: str, **fields) -> None:
        self.app.mark(event, page=self.key, **fields)

    def refresh_running(self) -> None:
        pass

    def on_show(self) -> None:
        pass
