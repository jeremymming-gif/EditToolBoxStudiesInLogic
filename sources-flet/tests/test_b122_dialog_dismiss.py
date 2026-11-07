"""批 122 / 用户 m02467：清理确认框必须能关闭（取消 / 右上 X / 确认后自动关闭）。

根因：`pages.IssuePage._open_clean_confirm` 直接 `page.show_dialog(dialog)`，没把对话框
登记到 `components._open_dialogs`；于是 `C.dismiss_top_dialog()` 栈空直接返回 False，
「取消」「移入回收目录」都关不掉框。本测试锁住三件事：

1. `C.show_dialog` 入栈、`C.dismiss_top_dialog` 出栈并调用 `page.pop_dialog`；
2. 清理确认框标题栏有右上 X（关闭）；点 X / 取消能关、确认后先关再启动清理；
3. 历史未登记的对话框，栈空时 `dismiss_top_dialog` 仍回退 `page.pop_dialog`（自愈）。
"""
from __future__ import annotations

import flet as ft

from ui import assembly as ASM
from ui import components as C
from ui import pages as P
from test_b112_post_b import _build_issue, _walk


def _fresh_stack() -> None:
    C._open_dialogs.clear()


def _patch_page(page):
    shown: list = []
    popped: list = []
    page.app.page.show_dialog = lambda dialog: shown.append(dialog)
    page.app.page.pop_dialog = lambda: popped.append(1)
    return shown, popped


# ── 1. 组件级：登记 / 出栈 / 关闭 ─────────────────────────────────────
def test_show_dialog_registers_and_dismiss_pops():
    _fresh_stack()

    class _Page:
        def __init__(self) -> None:
            self.shown: list = []
            self.pops: int = 0

        def show_dialog(self, dialog) -> None:
            self.shown.append(dialog)

        def pop_dialog(self) -> None:
            self.pops += 1

    page = _Page()
    dialog = ft.AlertDialog(modal=True, title=ft.Text("x"))
    C.show_dialog(page, dialog)
    assert page.shown == [dialog]
    assert C._open_dialogs == [dialog]

    assert C.dismiss_top_dialog(page) is True
    assert page.pops == 1 and C._open_dialogs == []


def test_dismiss_unregistered_dialog_still_pops():
    """历史入口（批 121 清理框）没入栈也不能再卡死：栈空时回退 pop_dialog。"""
    _fresh_stack()

    class _Page:
        def __init__(self) -> None:
            self.pops: int = 0

        def pop_dialog(self):
            # 与 Flet 1.0.5 一致：返回被关掉的对话框；无对话框时 None。
            self.pops += 1
            return ft.AlertDialog() if self.pops == 1 else None

    page = _Page()
    assert C.dismiss_top_dialog(page) is True
    assert page.pops == 1
    assert C.dismiss_top_dialog(page) is False, "没有框时不能误报已关闭"
    assert page.pops == 2


# ── 2. T3 清理页：没有确认框；明细框仍然入栈 / 可关闭 ─────────────────
def _seed_cleanup(page, root: str) -> None:
    batch = root + "/.toolbox-trash/期-2026_11_07_110000-abcd1234"
    page.app.store.set("issue_state", {root: {"cleanup": {
        "schema": 1, "root": root, "operationId": "2026_11_07_110000-abcd1234",
        "status": "success", "count": 2,
        "entries": [{"path": "a.aux", "action": "move"},
                    {"path": "b.log", "action": "move"}],
        "kept": [], "failures": [], "warnings": [], "error": "",
        "trashDir": batch,
        "startedAt": "2026-11-07 11:00:00",
        "finishedAt": "2026-11-07 11:00:02"}}})


def test_cleanup_start_has_no_confirm_dialog():
    _fresh_stack()
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    shown, popped = _patch_page(page)

    page._cleanup.start()

    assert shown == [] and popped == [], "T3 单入口点击即清理，不再弹危险确认框"
    assert C._open_dialogs == []
    assert page._cleanup.phase == "scanning"


def test_cleanup_details_dialog_registers_and_dismisses():
    _fresh_stack()
    page, _root = _build_issue()
    root = "/tmp/期目录"
    page.set_field("issue_dir", root)
    _seed_cleanup(page, root)
    shown, popped = _patch_page(page)

    page._cleanup.details_dialog()
    assert len(shown) == 1
    dialog = shown[0]
    assert dialog.modal is True
    assert C._open_dialogs[-1] is dialog
    texts = [str(ctl.value) for ctl in _walk(dialog.content) if isinstance(ctl, ft.Text)]
    assert any("2026_11_07_110000-abcd1234" in value for value in texts), texts
    assert any("a.aux" in value for value in texts), texts

    icons = [ctl for ctl in _walk(dialog.title) if isinstance(ctl, ft.IconButton)]
    assert len(icons) == 1 and icons[0].tooltip == "关闭"
    icons[0].on_click(None)
    assert C._open_dialogs == [] and len(popped) == 1


def test_cleanup_details_close_button_dismisses():
    _fresh_stack()
    page, _root = _build_issue()
    root = "/tmp/期目录"
    page.set_field("issue_dir", root)
    _seed_cleanup(page, root)
    shown, popped = _patch_page(page)

    page._cleanup.details_dialog()
    shown[0].actions[0].on_click(None)
    assert len(popped) == 1 and C._open_dialogs == []


# ── 3. 其它经 dismiss_top_dialog 关闭的框也须入栈（运行整期 / 覆盖确认）──
def test_runall_dialog_registered_and_cancel_closes():
    _fresh_stack()
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    shown, popped = _patch_page(page)

    page._open_runall()
    assert len(shown) == 1 and C._open_dialogs[-1] is shown[0]
    cancel = shown[0].actions[0]
    cancel.on_click(None)
    assert len(popped) == 1 and C._open_dialogs == []


def test_overwrite_dialog_registered_and_cancel_closes():
    _fresh_stack()
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    shown, popped = _patch_page(page)

    page._confirm_overwrite("/tmp/不存在但无妨.pdf")
    assert len(shown) == 1 and C._open_dialogs[-1] is shown[0]
    cancel = shown[0].actions[0]
    cancel.on_click(None)
    assert len(popped) == 1 and C._open_dialogs == []


def test_pages_source_uses_registered_helper_for_managed_dialogs():
    src = __import__("inspect").getsource(P.IssuePage)
    assert "C.show_dialog(self.page, dialog)" in src
    # 三个走 dismiss_top_dialog 的框不再直接 show_dialog
    assert "self.page.show_dialog(dialog)" not in src
