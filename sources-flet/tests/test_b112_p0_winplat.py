"""批 112 · P0 GUI-08 回归：winplat 在 macOS 上必须可导入（不再一启动即崩）。

根因：`services/winplat.py` 模块级执行 `ctypes.WINFUNCTYPE(...)`，该属性只在 Windows
存在；`main.py` 导入它 → mac 上 `import main` 即 AttributeError。
修法：`_WNDENUMPROC = ctypes.WINFUNCTYPE(...) if IS_WIN else None`（懒加载思路一致：
真正的 DLL 也在 `_u32()` 里才加载）。

本测试是**跨平台**的：mac/Linux 断言「导入成功 + 公开函数安全默认值」，
Windows 专属语义（回调类型、EnumWindows 等）用 skipif 保留。
"""
from __future__ import annotations

import ctypes
import importlib
import sys

import pytest

IS_WIN = sys.platform == "win32"


# ── 导入与模块级结构 ──────────────────────────────────────────────────────
def test_import_winplat_succeeds():
    """mac 上 import 即崩的回归点（旧代码在模块级取 ctypes.WINFUNCTYPE）。"""
    from services import winplat

    assert winplat is not None
    assert winplat.IS_WIN == (sys.platform == "win32")
    # 导入期不得加载任何 Windows DLL（懒加载：首次调用 _u32/_shell32/_dwm 才加载）
    assert winplat._user32_dll is None
    assert winplat._shell32_dll is None
    assert winplat._dwm_dll is None


def test_wndenumproc_conditional_definition():
    """非 Windows 上 _WNDENUMPROC 为 None；Windows 上仍是 ctypes 回调类型。"""
    from services import winplat

    if IS_WIN:
        assert winplat._WNDENUMPROC is not None
        assert ctypes.WINFUNCTYPE is not None
    else:
        assert winplat._WNDENUMPROC is None
        assert not hasattr(ctypes, "WINFUNCTYPE")


def test_module_import_via_ui_shell():
    """真正崩过的链路：ui.settings_shell（以及 main）导入 services.winplat。"""
    settings_shell = importlib.import_module("ui.settings_shell")
    assert settings_shell is not None
    main = importlib.import_module("main")
    assert main.App is not None


# ── 公开函数：非 Windows 安全默认值（不抛异常）───────────────────────────
def test_public_functions_safe_defaults_on_mac():
    from services import winplat

    # 真实平台是 mac 时才有意义；Windows 上这些函数会走真实实现（由下面 skipif 用例覆盖）
    if IS_WIN:
        pytest.skip("Windows 上走真实 API，安全默认值由 test_winplat_no_pywin32 覆盖")

    assert winplat.list_windows() == []
    assert winplat.list_windows("任意标题") == []
    assert winplat.find_client_window() is None
    assert winplat.window_info(0) == {}
    assert winplat.window_info(123456789) == {}
    assert winplat.get_dpi(0) == 96
    assert winplat.get_dpi(424242) == 96
    assert winplat.show_window(0, True) is False
    assert winplat.close_window(0) is False
    dark = winplat.set_dark_titlebar(0, True)
    assert isinstance(dark, dict) and dark["ok"] is False and dark["error"]
    drag = winplat.accept_drag_files(0, True)
    assert isinstance(drag, dict) and drag["ok"] is False and drag["error"]
    assert winplat.window_logical_rect(0) == {}
    assert winplat._monitor_work_rect(0) == {}
    assert winplat._monitor_work_rect(point=(10, 10)) == {}
    x, y = winplat.clamp_logical_position(99999, 99999, 1240, 860)
    assert (x, y) == (99999, 99999)  # 非 Windows 只做下界钳制，不查监视器
    assert winplat.clamp_logical_position(-5, -9, 100, 100) == (0, 0)


def test_public_functions_never_raise_on_garbage(monkeypatch):
    """非 Windows 下所有公开函数对垃圾输入都不抛（发布版纪律）。"""
    from services import winplat

    monkeypatch.setattr(winplat, "IS_WIN", False)
    for hwnd in (None, 0, -1, "not-a-hwnd", object()):
        assert winplat.window_info(hwnd) == {}
        assert winplat.get_dpi(hwnd) == 96
        assert winplat.show_window(hwnd) is False
        assert winplat.close_window(hwnd) is False
        assert winplat.window_logical_rect(hwnd) == {}
        assert winplat.set_dark_titlebar(hwnd, True)["ok"] is False
        assert winplat.accept_drag_files(hwnd)["ok"] is False


# ── Windows 专属语义（mac 上跳过）────────────────────────────────────────
@pytest.mark.skipif(not IS_WIN, reason="仅 Windows 有 WINFUNCTYPE/EnumWindows")
def test_windows_callback_semantics():
    from services import winplat

    assert winplat._WNDENUMPROC is not None
    assert issubclass(winplat._WNDENUMPROC, ctypes._CFuncPtr)  # noqa: SLF001
    u32 = winplat._u32()
    assert u32.EnumWindows.argtypes[0] is winplat._WNDENUMPROC


@pytest.mark.skipif(not IS_WIN, reason="仅 Windows 有真实窗口")
def test_windows_public_functions_real_platform():
    from services import winplat

    assert isinstance(winplat.list_windows(), list)
    assert winplat.get_dpi(0) == 96
    assert winplat.show_window(0) is False
    assert winplat.close_window(0) is False
