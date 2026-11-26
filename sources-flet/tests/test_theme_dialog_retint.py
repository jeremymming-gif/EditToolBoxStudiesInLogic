"""换肤时已打开的 AlertDialog 浮层也要原地重染（回归测试）。

真机复现：浅色启动 → 打开确认框 → 切深色 → 对话框文字仍是深色，
压在深色底上不可读；切到别的页面再回来（navigate 重建）才正常。

根因：set_theme 的 retint 路径只染 self.page 挂载树 + self.pages[].theme_roots，
而 show_dialog/confirm 打开的对话框活在 Flet 的 page._dialogs 栈并登记在
components._open_dialogs，两棵树都没覆盖到；对话框颜色是打开时用当时 Tokens
算好写死的，必须随换肤原地改写。

本测试沿用 tests/test_b116_theme_roundtrip.py 的 App.__new__ 假壳，不启真 GUI。
"""
from __future__ import annotations

from types import SimpleNamespace

import flet as ft
import pytest

import main as M
from ui import components as C
from ui.tokens import DARK, LIGHT, Tokens


def _app() -> M.App:
    """与 test_b116_theme_roundtrip._app() 同构的假壳（无真实 GUI）。"""
    app = M.App.__new__(M.App)
    app.running = False
    app.theme = "light"
    app.zoom = 1.0
    app.dpi = 96
    app.t = Tokens("light", 1.0, 96)
    app.engine_status = SimpleNamespace(ok=True)
    app.dpi_aware = False
    app._marker_path = None
    app.client_hwnd = 0
    app.titlebar_info = {}
    app.log_result = SimpleNamespace(t=app.t)
    app.page = SimpleNamespace(
        theme_mode=None, bgcolor=None, controls=[],
        theme=None, dark_theme=None,
        show_dialog=lambda dialog: None,
        # 记录 page.update(*controls) 的实参：普通整页 patch 传空，对话框子树
        # 补丁必须把 dialog 作为实参显式传入（Flet: Control.update→page.update(self)）。
        updates=[],
    )
    app.page.update = lambda *controls: app.page.updates.extend(controls)
    app.sidebar = ft.Container(bgcolor=LIGHT.sidebar,
                               border=ft.Border(right=ft.BorderSide(1, LIGHT.border)))
    app.theme_switch = ft.Switch(value=False, active_color=LIGHT.accent)
    app.status_bar = SimpleNamespace(
        root=ft.Container(bgcolor=LIGHT.surface), metas=[],
        set_meta=lambda meta: app.status_bar.metas.append(meta))
    app.toast_ctl = SimpleNamespace(root=ft.Container(bgcolor=LIGHT.accent_soft))
    app.pages = {}
    app.store = SimpleNamespace(set=lambda key, value: None)
    app.apply_titlebar = lambda: None
    app.persist = lambda: None
    app.toast = lambda message: None
    # 已构建外壳：page.controls 非空 → set_theme 走 retint 而非 rebuild。
    app.content = ft.Container(bgcolor=LIGHT.surface)
    app.page.controls = [app.sidebar, app.content, app.status_bar.root]
    app.rebuild_shell = lambda: None
    return app


def _dialog(t: Tokens) -> ft.AlertDialog:
    """用真实组件 helper 造一个打开时颜色写死的确认框。"""
    return ft.AlertDialog(
        modal=True,
        title=C.text("确认删除", t, role="dialog-title"),
        content=ft.Container(
            bgcolor=t.palette.surface,
            content=C.text("正文", t, "body", t.palette.text_muted),
        ),
    )


@pytest.fixture(autouse=True)
def _clean_dialog_stack(monkeypatch):
    """每个用例独立对话框栈，避免污染别的测试。"""
    monkeypatch.setattr(C, "_open_dialogs", [])


def test_set_theme_retints_open_dialog_in_place(capsys):
    """已打开对话框的文字/背景随切主题原地改写，且走 retint 不重建。"""
    app = _app()
    rebuilds: list[int] = []
    app.rebuild_shell = lambda: rebuilds.append(1)

    dialog = _dialog(app.t)
    C.show_dialog(app.page, dialog)
    assert C._open_dialogs == [dialog]

    # 切主题前：颜色确实是浅色板写死的。
    assert dialog.title.color == LIGHT.text
    assert dialog.content.content.color == LIGHT.text_muted
    assert dialog.content.bgcolor == LIGHT.surface

    app.set_theme("dark")

    assert rebuilds == []
    assert dialog.title.color == DARK.text
    assert dialog.content.content.color == DARK.text_muted
    assert dialog.content.bgcolor == DARK.surface
    marks = [line for line in capsys.readouterr().out.splitlines() if "P2MARK " in line]
    assert any('"event": "theme_begin"' in line and '"mode": "retint"' in line
               for line in marks)
    assert any('"event": "theme_changed"' in line and '"mode": "retint"' in line
               for line in marks)


def test_open_dialog_is_retinted_once_when_registered_twice():
    """同一对话框重复入栈时不能被二次映射串色（#FFFFFF 一词多义守卫）。"""
    app = _app()
    dialog = _dialog(app.t)
    C.show_dialog(app.page, dialog)
    C._open_dialogs.append(dialog)  # 模拟异常路径的重复登记

    app.set_theme("dark")

    # DARK.text=#F5F5F7 也是 LIGHT.bg；若被 fg_map 再映射一次会变成 #1E1E1E。
    assert dialog.title.color == DARK.text == "#F5F5F7"
    assert dialog.content.content.color == DARK.text_muted
    assert dialog.content.bgcolor == DARK.surface


def test_set_theme_patches_open_dialog_subtree_to_client():
    """换肤后必须为打开的对话框单独发 patch（F2-9 主树仍只 1 次整页 patch）。

    对话框活在 Flet 的 page._dialogs 子树，不在 page.controls：只改 Python
    颜色属性而不调用 page.update(dialog)，客户端永远收不到新颜色（真机
    浅→深时文字仍是深色，压在深色底上不可读）。这里在 app 层断言契约
    「显式 dialog 子树 update 已发出」，不依赖真实 GUI/客户端渲染。
    """
    app = _app()
    dialog = _dialog(app.t)
    C.show_dialog(app.page, dialog)

    app.set_theme("dark")

    assert dialog in app.page.updates, (
        "set_theme 必须为已打开对话框发显式 page.update(dialog) 子树补丁；"
        f"实际 page.update 实参={app.page.updates!r}"
    )


def test_set_theme_without_dialog_emits_no_dialog_patch():
    """无对话框打开时不得多发补丁（保持原有行为：主树合并 Patch）。"""
    app = _app()

    app.set_theme("dark")

    assert app.page.updates == [], (
        "无打开对话框时不应有额外子树补丁"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
