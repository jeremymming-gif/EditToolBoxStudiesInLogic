"""批 114 · 日志框可拖拽缩放（用户 m03166）。

全壳日志共用一个 LogConsole：标题栏下方有一条可上下拖动的把手（顶部小横条），
拖动改变日志区高度（夹在 min/max 之间），双击恢复默认高度；四个调用点自动获得该能力。
"""

from types import SimpleNamespace

import flet as ft

from ui.components import LogConsole
from ui.tokens import Tokens


def _console(**kwargs) -> LogConsole:
    return LogConsole(Tokens("light", 1.0, 96), height=200, **kwargs)


def test_log_console_has_resize_handle_with_mouse_cursor():
    console = _console()
    handle = console._resize_handle
    assert isinstance(handle, ft.GestureDetector)
    assert handle.mouse_cursor == ft.MouseCursor.RESIZE_UP_DOWN
    assert handle.on_vertical_drag_update is not None
    assert handle.on_double_tap is not None
    # 把手在日志正文上方（Column 第一个控件），正文仍是 SelectionArea（可拖选/复制）。
    box = console._log_box.content
    assert isinstance(box, ft.Column)
    assert box.controls[0] is handle
    assert isinstance(box.controls[-1], ft.SelectionArea)


def test_drag_up_increases_height_and_is_clamped():
    console = _console()
    base = console._log_height
    console._on_resize_update(SimpleNamespace(primary_delta=-120))
    assert console._log_box.height > base
    assert console._log_box.height == console._log_height
    # 一次巨大的向上拖动被 max 夹住。
    console._on_resize_update(SimpleNamespace(primary_delta=-100000))
    assert console._log_height == console._max_height
    # 一次巨大的向下拖动被 min 夹住。
    console._on_resize_update(SimpleNamespace(primary_delta=100000))
    assert console._log_height == console._min_height


def test_double_tap_resets_to_default_height():
    console = _console()
    base = console._base_height
    console._on_resize_update(SimpleNamespace(primary_delta=-140))
    assert console._log_height != base
    console._on_resize_reset()
    assert console._log_height == base
    assert console._log_box.height == base


def test_all_log_consoles_share_resize_ability():
    # 四个调用点（单篇/整期/引用库/规范化）都走同一个 LogConsole 类：
    # 静态断言源码里没有别的手写日志容器绕过它，且拖拽能力只在此处实现一次。
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    src = root.joinpath("ui/components.py").read_text()
    assert src.count("RESIZE_UP_DOWN") == 1
    assert "def _on_resize_update(self, e) -> None:" in src
    calls = 0
    for path in root.rglob("*.py"):
        calls += path.read_text().count("LogConsole(")
    assert calls >= 4, f"应至少 4 个页面用 LogConsole，实际 {calls}"
    for path in root.rglob("*.py"):
        text = path.read_text()
        if path.name == "components.py":
            continue
        assert "RESIZE_UP_DOWN" not in text, f"{path} 不应重复实现日志缩放"
