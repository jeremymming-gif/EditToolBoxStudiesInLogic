"""P6a 回归：winplat 不依赖 pywin32（发布包 `flet pack` 环境无 pywin32）。

背景：v1.5.0 发布版点「深色模式」抛 `No module named 'win32gui'`（Flet 错误页），
链路 set_theme → apply_titlebar → winplat.find_client_window → list_windows。
本测试在**毒化 sys.modules**（模拟发布环境）后 reload winplat，断言：
- 模块可导入、不 import 任何 win32* 第三方包；
- 所有公开函数在无窗口/非 Windows/异常输入下返回安全值、绝不抛；
- main.App.apply_titlebar 的防护层把平台异常吞掉并记 mark。
"""
from __future__ import annotations

import importlib
import re
import sys
import types
from pathlib import Path

import pytest


# ── 工具 ────────────────────────────────────────────────────────────────
class _Recorder:
    """最小假 App：记录 mark 调用（main.App.apply_titlebar 只需这些属性）。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.client_hwnd = 0
        self.theme = "dark"
        self.titlebar_info: dict = {}

    def mark(self, event: str, **fields) -> None:
        self.events.append((event, fields))


@pytest.fixture()
def poisoned_win32(monkeypatch):
    """模拟发布环境：win32gui / win32process / win32api 等全部不可用。"""
    names = ("win32gui", "win32process", "win32api", "win32con", "pywintypes")
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)
    yield names


def test_winplat_reloads_without_pywin32(poisoned_win32):
    from services import winplat

    reloaded = importlib.reload(winplat)
    assert reloaded is not None
    # 只允许内置 ctypes/标准库；不得 import 任何 win32* 第三方包
    src = Path(reloaded.__file__).read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:import|from)\s+(win32\w*)", src, re.M)
    assert imports == [], f"winplat 仍 import pywin32 模块: {imports}"
    for name in poisoned_win32:
        assert sys.modules.get(name) is None  # 毒化仍在（winplat 没把它救回来）


def test_winplat_safe_values_without_windows(monkeypatch):
    from services import winplat

    monkeypatch.setattr(winplat, "IS_WIN", False)
    assert winplat.list_windows() == []
    assert winplat.find_client_window() is None
    assert winplat.window_info(0) == {}
    assert winplat.get_dpi(0) == 96
    assert winplat.show_window(0, True) is False
    assert winplat.close_window(0) is False
    assert winplat.set_dark_titlebar(0, True)["ok"] is False
    assert winplat.accept_drag_files(0)["ok"] is False
    assert winplat.window_logical_rect(0) == {}
    x, y = winplat.clamp_logical_position(99999, 99999, 1240, 860)
    assert isinstance(x, int) and isinstance(y, int)


def test_winplat_no_window_never_raises():
    """真实 Windows 上：非法 hwnd / 零窗口也要返回安全值、不抛。"""
    from services import winplat

    assert winplat.window_info(0) == {}
    assert winplat.window_info(123456789) == {}
    assert winplat.get_dpi(0) == 96
    assert winplat.close_window(0) is False
    dm = winplat.set_dark_titlebar(0, True)
    assert isinstance(dm, dict) and dm["ok"] is False
    assert winplat.accept_drag_files(0)["ok"] is False
    assert isinstance(winplat.list_windows(), list)
    found = winplat.find_client_window()
    assert found is None or isinstance(found, int)


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 有意义")
def test_list_windows_keeps_flutter_class_semantics():
    """title_hint 不命中时仍要找到 Flet 客户端窗口（若测试期有窗口）；结构键固定。"""
    from services import winplat

    for win in winplat.list_windows("___no_such_window_title___"):
        assert set(win) >= {"hwnd", "pid", "title", "class"}
        assert isinstance(win["hwnd"], int)
        assert isinstance(win["title"], str) and isinstance(win["class"], str)
    # 有 Flet 窗口时：find_client_window 必须优先返回 FLUTTER_CLASS 那个
    wins = winplat.list_windows()
    flutter = [w for w in wins if w.get("class") == winplat.FLUTTER_CLASS]
    hwnd = winplat.find_client_window()
    if flutter:
        assert hwnd == flutter[0]["hwnd"]
        assert winplat.window_info(hwnd).get("class") == winplat.FLUTTER_CLASS


def test_apply_titlebar_swallows_platform_errors(monkeypatch):
    """main.App.apply_titlebar 的平台异常必须被吞掉并记 titlebar_error（不再冒到 UI）。"""
    import main
    from services import winplat

    def _boom(*_args, **_kw):
        raise RuntimeError("simulated platform failure")

    monkeypatch.setattr(winplat, "find_client_window", _boom)
    app = _Recorder()
    main.App.apply_titlebar(app)  # 不得抛
    events = [e for e, _ in app.events]
    assert "titlebar_error" in events
    err = dict(app.events)["titlebar_error"]
    assert "RuntimeError" in err.get("error", "")


def test_apply_titlebar_swallows_winplat_set_errors(monkeypatch):
    """set_dark_titlebar 抛异常时同样降级（不依赖窗口存在）。"""
    import main
    from services import winplat

    monkeypatch.setattr(winplat, "find_client_window", lambda *_a, **_k: 424242)
    monkeypatch.setattr(winplat, "window_info", lambda *_a, **_k: {})

    def _boom(*_args, **_kw):
        raise OSError("simulated dwm failure")

    monkeypatch.setattr(winplat, "set_dark_titlebar", _boom)
    app = _Recorder()
    main.App.apply_titlebar(app)
    assert "titlebar_error" in [e for e, _ in app.events]


def test_tray_start_degrades_without_pystray(monkeypatch):
    """托盘：发布包没有 pystray 时 start() 返回 started=False 并 mark，不抛。"""
    monkeypatch.setitem(sys.modules, "pystray", None)
    from services import tray

    recorded: list[tuple[str, dict]] = []

    class _App:
        client_hwnd = 0

        def mark(self, event: str, **fields) -> None:
            recorded.append((event, fields))

    svc = tray.TrayService(_App())
    info = svc.start()  # 不得抛
    assert info["started"] is False
    assert info["error"]
    assert "tray_started" in [e for e, _ in recorded]
    assert svc.started is False
    svc.stop()  # 未启动时 stop 也要安全
