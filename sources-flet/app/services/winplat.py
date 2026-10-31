"""P3 · Windows 平台旁路（窗口查找/几何/明暗标题栏/显示隐藏/拖放能力探测）。

纪律：只在 Windows 生效；所有函数容错（找不到窗口/权限不足返回 None/False），
绝不因为平台补齐失败而中断主流程。pywin32 的 win32gui 只用于枚举/消息，不注入进程。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Optional

IS_WIN = os.name == "nt"
APP_TITLE_DEFAULT = "学衡工具箱"
FLUTTER_CLASS = "FLUTTER_RUNNER_WIN32_WINDOW"
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
WM_CLOSE = 0x0010
GWL_EXSTYLE = -20
WS_EX_ACCEPTFILES = 0x00000010


def _u32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _shell32():
    return ctypes.WinDLL("shell32", use_last_error=True)


def _dwm():
    return ctypes.WinDLL("dwmapi", use_last_error=True)


def list_windows(title_hint: str = APP_TITLE_DEFAULT) -> list[dict]:
    """枚举所有可见顶层窗口（标题包含 hint 且类名为 Flutter 窗口的优先排前面）。"""
    if not IS_WIN:
        return []
    import win32gui
    import win32process

    found: list[dict] = []

    def _cb(hwnd, _lparam):  # noqa: ANN001
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if title_hint and title_hint not in title and cls != FLUTTER_CLASS:
            return
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        found.append({"hwnd": hwnd, "pid": pid, "title": title, "class": cls})
        return

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:  # noqa: BLE001 - 枚举窗口类错误一律视为无
        return []
    found.sort(key=lambda w: (w["class"] != FLUTTER_CLASS, w["title"] != title_hint))
    return found


def find_client_window(title_hint: str = APP_TITLE_DEFAULT) -> Optional[int]:
    windows = list_windows(title_hint)
    for win in windows:
        if win["class"] == FLUTTER_CLASS:
            return int(win["hwnd"])
    return int(windows[0]["hwnd"]) if windows else None


def window_info(hwnd: int) -> dict:
    if not IS_WIN or not hwnd:
        return {}
    import win32gui

    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        ex_style = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
        return {
            "hwnd": hwnd,
            "title": win32gui.GetWindowText(hwnd),
            "class": win32gui.GetClassName(hwnd),
            "visible": bool(win32gui.IsWindowVisible(hwnd)),
            "rect_px": {"left": left, "top": top, "right": right, "bottom": bottom,
                        "width": right - left, "height": bottom - top},
            "ex_style": hex(ex_style & 0xFFFFFFFF),
            "accept_files": bool(ex_style & WS_EX_ACCEPTFILES),
            "dpi": get_dpi(hwnd),
        }
    except Exception:  # noqa: BLE001
        return {}


def get_dpi(hwnd: int) -> int:
    if not IS_WIN or not hwnd:
        return 96
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        return int(dpi) if dpi else 96
    except Exception:  # noqa: BLE001
        return 96


def show_window(hwnd: int, show: bool = True, foreground: bool = True) -> bool:
    if not IS_WIN or not hwnd:
        return False
    u32 = _u32()
    try:
        u32.ShowWindow(hwnd, SW_SHOW if show else SW_HIDE)
        if show and foreground:
            u32.ShowWindow(hwnd, SW_RESTORE)
            u32.SetForegroundWindow(hwnd)
        return bool(u32.IsWindowVisible(hwnd)) == show
    except Exception:  # noqa: BLE001
        return False


def close_window(hwnd: int) -> bool:
    """向 flet 客户端窗口发 WM_CLOSE：走应用 prevent_close 保存→destroy→退出链路。"""
    if not IS_WIN or not hwnd:
        return False
    try:
        return bool(_u32().PostMessageW(hwnd, WM_CLOSE, 0, 0))
    except Exception:  # noqa: BLE001
        return False


def set_dark_titlebar(hwnd: int, dark: bool) -> dict:
    """DwmSetWindowAttribute 20（Win10 20H1+）/19（旧）跨进程设置深色标题栏。"""
    result = {"ok": False, "hwnd": hwnd, "dark": bool(dark), "attr": None}
    if not IS_WIN or not hwnd:
        return result
    try:
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            hr = _dwm().DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
            if hr == 0:
                result.update({"ok": True, "attr": attr})
                break
        result["hr"] = hr
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def accept_drag_files(hwnd: int, accept: bool = True) -> dict:
    """探测/尝试开启 WS_EX_ACCEPTFILES + DragAcceptFiles（跨进程允许）。"""
    result = {"ok": False, "hwnd": hwnd, "registered_before": None, "registered_after": None}
    if not IS_WIN or not hwnd:
        return result
    info = window_info(hwnd)
    result["registered_before"] = info.get("accept_files")
    try:
        ok = bool(_shell32().DragAcceptFiles(hwnd, accept))
        after = window_info(hwnd)
        result.update({"ok": ok, "registered_after": after.get("accept_files"),
                       "ex_style": after.get("ex_style")})
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _monitor_work_rect(hwnd: int = 0, point: tuple[int, int] | None = None) -> dict:
    """目标监视器（默认窗口所在）的工作区（物理像素）。"""
    if not IS_WIN:
        return {}
    u32 = _u32()

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

    try:
        if point is not None:
            mon = u32.MonitorFromPoint(wintypes.POINT(point[0], point[1]), 2)
        elif hwnd:
            mon = u32.MonitorFromWindow(hwnd, 2)
        else:
            mon = u32.MonitorFromWindow(0, 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not u32.GetMonitorInfoW(mon, ctypes.byref(info)):
            return {}
        work = info.rcWork
        full = info.rcMonitor
        return {
            "work": {"left": work.left, "top": work.top, "right": work.right, "bottom": work.bottom},
            "monitor": {"left": full.left, "top": full.top, "right": full.right, "bottom": full.bottom},
        }
    except Exception:  # noqa: BLE001
        return {}


def clamp_logical_position(x: int, y: int, w: int, h: int, dpi: int = 144) -> tuple[int, int]:
    """把「逻辑坐标窗口左上角」钳制到所在/最近监视器工作区内（多屏记忆用，纯函数可测）。

    逻辑坐标 = 物理坐标 / (dpi/96)；窗口至少露出标题栏（水平整宽可见优先）。
    """
    if not IS_WIN:
        return max(0, int(x)), max(0, int(y))
    scale = (dpi or 96) / 96.0
    px, py = int(round(x * scale)), int(round(y * scale))
    mon = _monitor_work_rect(point=(px + 10, py + 10))
    if not mon:
        return max(0, int(x)), max(0, int(y))
    work = mon["work"]
    pw, ph = int(round(w * scale)), int(round(h * scale))
    # 水平：完全放不下就贴左边；垂直：保证标题栏在 work 内
    if px + pw > work["right"]:
        px = max(work["left"], work["right"] - pw)
    if px < work["left"]:
        px = work["left"]
    if py + ph > work["bottom"]:
        py = max(work["top"], work["bottom"] - ph)
    if py < work["top"]:
        py = work["top"]
    return int(round(px / scale)), int(round(py / scale))


def window_logical_rect(hwnd: int, dpi: int = 144, frame: bool = True) -> dict:
    """窗口 rect → 逻辑坐标（用于把位置存进 settings）。"""
    info = window_info(hwnd)
    if not info:
        return {}
    scale = (dpi or 96) / 96.0
    rect = info["rect_px"]
    return {
        "left": int(round(rect["left"] / scale)),
        "top": int(round(rect["top"] / scale)),
        "width": int(round(rect["width"] / scale)),
        "height": int(round(rect["height"] / scale)),
        "dpi": dpi,
    }
