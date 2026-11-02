"""批 113：换肤不重建（GUI-03 retint）对 InputBorder 的补全 + Page.on_error 兜底。

背景：批 113 E 把 TextField 从弃用的 `border_color`/`focused_border_color` 迁到
`border=ControlStateValue[InputBorder]`。`_retint_node` 原来只认 `ft.Border`，
主题热切换后 `OutlineInputBorder.side.color` 会停在上一个主题色 —— 本文件锁住补全行为。
"""
from __future__ import annotations

from pathlib import Path

import flet as ft
import pytest

import main
from ui.tokens import LIGHT, DARK, palette_remap

APP = Path(__file__).resolve().parents[1] / "app"


def _field() -> ft.TextField:
    return ft.TextField(
        border={
            ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                side=ft.BorderSide(1, LIGHT.border), border_radius=6),
            ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                side=ft.BorderSide(2, LIGHT.accent), border_radius=6),
        })


def test_retint_input_border_recolors_side_and_keeps_geometry():
    border = _field().border[ft.ControlState.DEFAULT]
    mapped = main._retint_input_border(border, palette_remap("dark"))
    assert isinstance(mapped, ft.OutlineInputBorder)
    assert mapped.side.color == DARK.border
    assert mapped.side.width == 1
    assert mapped.border_radius == 6


def test_retint_node_rewrites_state_mapped_border():
    app = object.__new__(main.App)  # 只调用纯递归方法，不依赖 App.__init__
    field = _field()
    changed = app._retint_node(field, palette_remap("dark"), palette_remap("dark", role="fg"), set())
    assert changed >= 1
    default = field.border[ft.ControlState.DEFAULT]
    focused = field.border[ft.ControlState.FOCUSED]
    assert default.side.color == DARK.border
    assert focused.side.color == DARK.accent
    assert default.side.width == 1 and focused.side.width == 2


def test_page_error_logs_and_toasts(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(main.SLOG, "log", lambda event, **fields: events.append((event, fields)))
    shown: list[str] = []
    app = object.__new__(main.App)
    app.toast = shown.append  # type: ignore[attr-defined]
    app._on_page_error(ft.Event("error", None, data="boom"))
    assert events and events[0][0] == "page_error"
    assert "boom" in events[0][1]["message"]
    assert shown and "启动日志" in shown[0]


def test_after_boot_installs_page_error_handler():
    source = (APP / "main.py").read_text(encoding="utf-8")
    assert "self.page.on_error = self._on_page_error" in source
