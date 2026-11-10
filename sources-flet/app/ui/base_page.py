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

    def theme_roots(self) -> list:
        """换肤时要重染的根控件；懒加载宿主覆盖此方法补充未挂载的缓存树。

        批 116：`root` 一旦 build 就长期复用，主题热切换必须把「已构建但当前
        未挂载」的页面也染到新皮肤，否则导航回来是旧色。
        """
        return [self.root] if self.root is not None else []

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
    def _meta_field(self, key: str, label: str, width: float = 0):
        """高级参数单行输入；width=0 表示随父容器流式铺满（网格用）。"""
        def _changed(e) -> None:
            self.app.store.set(f"form.{key}", e.control.value.strip())

        control = C.text_field(
            self.t, value=self.app.store.get(f"form.{key}", "") or "",
            width=(self.t.px(width) if width else None), on_change=_changed,
        )
        self.fields[key] = [control]
        return C.form_field(self.t, label, control)

    def field(self, recent_key: str) -> str:
        ref = self.fields.get(recent_key)
        if not ref:
            return ""
        return (ref[0].value or "").strip()

    def set_field(self, recent_key: str, value: str) -> None:
        ref = self.fields.get(recent_key)
        if ref:
            ref[0].value = value
            C.sync_clearable(ref[0])     # m03131：程序化回填后同步框内 ✕
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

    def focus_after_nav(self):
        """导航落地后要接收焦点的控件（R03）；默认 = 本页第一个已挂载的输入框。

        只有输入页才自动聚焦：按 `self.fields` 插入序取第一个已注册字段的控件，
        要求它已挂载（`page is not None`）且 `focus` 可调用；找不到就返回 None
        （纯展示页不抢焦点）。返回控件时由调用方 `page.run_task(ctrl.focus)`
        （Flet 1.0.5 的 `focus` 是 async，不能直接调）。同一页面内切视图（same_page）
        是否重复聚焦由调用方语义负责——基类只给「落点」，不判断来源。
        """
        try:
            for ref in self.fields.values():
                control = ref[0] if ref else None
                if control is None:
                    continue
                try:
                    # 未挂载的 Flet 控件读 `.page` 会抛 RuntimeError（不是返回 None）
                    mounted = getattr(control, "page", None) is not None
                except Exception:  # noqa: BLE001 未挂载/已销毁控件 = 不是落点
                    mounted = False
                if mounted and callable(getattr(control, "focus", None)):
                    return control
        except Exception:  # noqa: BLE001 字段残缺不得炸导航
            return None
        return None
