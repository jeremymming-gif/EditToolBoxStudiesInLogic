"""P6a · Windows 平台旁路（窗口查找/几何/明暗标题栏/显示隐藏/拖放能力探测）。

热修要点（B-246c P6a）：**纯 ctypes 实现，不再依赖 pywin32**。
v1.5.0 发布版 `flet pack` 环境中没有 pywin32，旧实现 `import win32gui` 在
`set_theme -> apply_titlebar -> find_client_window -> list_windows` 链路抛出
`ModuleNotFoundError` 并冒到 UI 回调，表现为 Flet 错误页。

纪律：只在 Windows 生效；所有公开函数容错（找不到窗口/权限不足/平台不符一律返回安全值，
绝不抛异常打断主流程），也不 import 任何第三方包。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Optional

IS_WIN = os.name == "nt"
APP_TITLE_DEFAULT = "编辑部工具箱"
FLUTTER_CLASS = "FLUTTER_RUNNER_WIN32_WINDOW"
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
WM_CLOSE = 0x0010
GWL_EXSTYLE = -20
WS_EX_ACCEPTFILES = 0x00000010

_HWND = wintypes.HWND
_BOOL = wintypes.BOOL
_DWORD = wintypes.DWORD
_LONG = wintypes.LONG
_LPARAM = wintypes.LPARAM
_UINT = wintypes.UINT

#: EnumWindows 回调类型。`ctypes.WINFUNCTYPE` 只存在于 Windows；mac/Linux 上
#: 模块导入不得崩（GUI-08），因此条件定义。所有使用点都在 `IS_WIN` 分支内。
_WNDENUMPROC = ctypes.WINFUNCTYPE(_BOOL, _HWND, _LPARAM) if IS_WIN else None


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


_user32_dll = None
_shell32_dll = None
_dwm_dll = None


def _u32():
    """user32（显式声明 argtypes/restype，避免 64 位 hwnd 被截断成 32 位）。"""
    global _user32_dll
    if _user32_dll is not None:
        return _user32_dll
    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.EnumWindows.argtypes = [_WNDENUMPROC, _LPARAM]
    dll.EnumWindows.restype = _BOOL
    dll.IsWindowVisible.argtypes = [_HWND]
    dll.IsWindowVisible.restype = _BOOL
    dll.GetWindowTextW.argtypes = [_HWND, wintypes.LPWSTR, ctypes.c_int]
    dll.GetWindowTextW.restype = ctypes.c_int
    dll.GetClassNameW.argtypes = [_HWND, wintypes.LPWSTR, ctypes.c_int]
    dll.GetClassNameW.restype = ctypes.c_int
    dll.GetWindowThreadProcessId.argtypes = [_HWND, ctypes.POINTER(_DWORD)]
    dll.GetWindowThreadProcessId.restype = _DWORD
    dll.GetWindowRect.argtypes = [_HWND, ctypes.POINTER(_RECT)]
    dll.GetWindowRect.restype = _BOOL
    dll.GetWindowLongW.argtypes = [_HWND, ctypes.c_int]
    dll.GetWindowLongW.restype = _LONG
    dll.GetWindowLongPtrW.argtypes = [_HWND, ctypes.c_int]
    dll.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    dll.ShowWindow.argtypes = [_HWND, ctypes.c_int]
    dll.ShowWindow.restype = _BOOL
    dll.SetForegroundWindow.argtypes = [_HWND]
    dll.SetForegroundWindow.restype = _BOOL
    dll.PostMessageW.argtypes = [_HWND, _UINT, wintypes.WPARAM, wintypes.LPARAM]
    dll.PostMessageW.restype = _BOOL
    dll.MonitorFromPoint.argtypes = [wintypes.POINT, _DWORD]
    dll.MonitorFromPoint.restype = wintypes.HANDLE
    dll.MonitorFromWindow.argtypes = [_HWND, _DWORD]
    dll.MonitorFromWindow.restype = wintypes.HANDLE
    dll.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
    dll.GetMonitorInfoW.restype = _BOOL
    if hasattr(dll, "GetDpiForWindow"):
        dll.GetDpiForWindow.argtypes = [_HWND]
        dll.GetDpiForWindow.restype = _UINT
    _user32_dll = dll
    return dll


def _shell32():
    global _shell32_dll
    if _shell32_dll is not None:
        return _shell32_dll
    dll = ctypes.WinDLL("shell32", use_last_error=True)
    dll.DragAcceptFiles.argtypes = [_HWND, _BOOL]
    dll.DragAcceptFiles.restype = None
    _shell32_dll = dll
    return dll


def _dwm():
    global _dwm_dll
    if _dwm_dll is not None:
        return _dwm_dll
    dll = ctypes.WinDLL("dwmapi", use_last_error=True)
    dll.DwmSetWindowAttribute.argtypes = [_HWND, _DWORD, ctypes.c_void_p, _DWORD]
    dll.DwmSetWindowAttribute.restype = ctypes.c_long
    _dwm_dll = dll
    return dll


def _text(hwnd: int, getter: str) -> str:
    """GetWindowTextW / GetClassNameW 的安全封装（失败返回空串）。"""
    try:
        buf = ctypes.create_unicode_buffer(512)
        n = getattr(_u32(), getter)(hwnd, buf, len(buf))
        return buf.value if n else ""
    except Exception:  # noqa: BLE001
        return ""


def _pid_of(hwnd: int) -> int:
    try:
        pid = _DWORD(0)
        _u32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:  # noqa: BLE001
        return 0


def list_windows(title_hint: str = APP_TITLE_DEFAULT) -> list[dict]:
    """枚举可见顶层窗口（标题包含 hint 或类名是 Flutter 窗口），Flutter 窗口优先排在前面。

    纯 ctypes；任何异常都归一为 `[]`（发布版无 pywin32 也必须可用）。
    """
    if not IS_WIN:
        return []
    try:
        u32 = _u32()
        found: list[dict] = []

        def _cb(hwnd, _lparam):  # noqa: ANN001
            try:
                h = int(hwnd or 0)
                if not h or not u32.IsWindowVisible(h):
                    return True
                title = _text(h, "GetWindowTextW")
                cls = _text(h, "GetClassNameW")
                if title_hint and title_hint not in title and cls != FLUTTER_CLASS:
                    return True
                found.append({"hwnd": h, "pid": _pid_of(h), "title": title, "class": cls})
            except Exception:  # noqa: BLE001 - 单个窗口出错不影响枚举
                pass
            return True

        try:
            u32.EnumWindows(_WNDENUMPROC(_cb), 0)
        except Exception:  # noqa: BLE001 - 枚举失败一律视为无
            return []
        found.sort(key=lambda w: (w["class"] != FLUTTER_CLASS, w["title"] != title_hint))
        return found
    except Exception:  # noqa: BLE001
        return []


def find_client_window(title_hint: str = APP_TITLE_DEFAULT) -> Optional[int]:
    """找 Flet 客户端窗口：优先 FLUTTER_CLASS，其次标题命中的第一个；找不到返回 None。"""
    try:
        windows = list_windows(title_hint)
    except Exception:  # noqa: BLE001
        return None
    for win in windows:
        if win.get("class") == FLUTTER_CLASS:
            try:
                return int(win["hwnd"])
            except Exception:  # noqa: BLE001
                return None
    if windows:
        try:
            return int(windows[0]["hwnd"])
        except Exception:  # noqa: BLE001
            return None
    return None


def window_info(hwnd: int) -> dict:
    """窗口信息（标题/类名/可见/物理 rect/扩展样式/DPI）；非 Windows 或无窗口返回 {}。"""
    if not IS_WIN or not hwnd:
        return {}
    try:
        h = int(hwnd)
        u32 = _u32()
        rect = _RECT()
        if not u32.GetWindowRect(h, ctypes.byref(rect)):
            return {}
        ex_style = int(u32.GetWindowLongPtrW(h, GWL_EXSTYLE) or 0)
        return {
            "hwnd": h,
            "title": _text(h, "GetWindowTextW"),
            "class": _text(h, "GetClassNameW"),
            "visible": bool(u32.IsWindowVisible(h)),
            "rect_px": {"left": rect.left, "top": rect.top, "right": rect.right,
                        "bottom": rect.bottom, "width": rect.right - rect.left,
                        "height": rect.bottom - rect.top},
            "ex_style": hex(ex_style & 0xFFFFFFFF),
            "accept_files": bool(ex_style & WS_EX_ACCEPTFILES),
            "dpi": get_dpi(h),
        }
    except Exception:  # noqa: BLE001
        return {}


def get_dpi(hwnd: int) -> int:
    if not IS_WIN or not hwnd:
        return 96
    try:
        dpi = _u32().GetDpiForWindow(int(hwnd))
        return int(dpi) if dpi else 96
    except Exception:  # noqa: BLE001
        return 96


def show_window(hwnd: int, show: bool = True, foreground: bool = True) -> bool:
    if not IS_WIN or not hwnd:
        return False
    try:
        u32 = _u32()
        u32.ShowWindow(int(hwnd), SW_SHOW if show else SW_HIDE)
        if show and foreground:
            u32.ShowWindow(int(hwnd), SW_RESTORE)
            u32.SetForegroundWindow(int(hwnd))
        return bool(u32.IsWindowVisible(int(hwnd))) == show
    except Exception:  # noqa: BLE001
        return False


def close_window(hwnd: int) -> bool:
    """向 flet 客户端窗口发 WM_CLOSE：走应用 prevent_close 保存→destroy→退出链路。"""
    if not IS_WIN or not hwnd:
        return False
    try:
        return bool(_u32().PostMessageW(int(hwnd), WM_CLOSE, 0, 0))
    except Exception:  # noqa: BLE001
        return False


def set_dark_titlebar(hwnd: int, dark: bool) -> dict:
    """DwmSetWindowAttribute 20（Win10 20H1+）/19（旧）跨进程设置深色标题栏。

    永不抛：失败返回 {"ok": False, ..., "error": "..."}，调用方降级为系统默认标题栏。
    """
    result: dict = {"ok": False, "hwnd": hwnd, "dark": bool(dark), "attr": None}
    if not IS_WIN or not hwnd:
        result["error"] = "not-windows-or-no-hwnd"
        return result
    hr = None
    try:
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            hr = _dwm().DwmSetWindowAttribute(int(hwnd), attr, ctypes.byref(value),
                                              ctypes.sizeof(value))
            if hr == 0:
                result.update({"ok": True, "attr": attr})
                break
        result["hr"] = hr
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def accept_drag_files(hwnd: int, accept: bool = True) -> dict:
    """尝试开启 WS_EX_ACCEPTFILES + DragAcceptFiles（跨进程允许）；失败返回安全结构。"""
    result = {"ok": False, "hwnd": hwnd, "registered_before": None, "registered_after": None}
    if not IS_WIN or not hwnd:
        result["error"] = "not-windows-or-no-hwnd"
        return result
    try:
        info = window_info(hwnd)
        result["registered_before"] = info.get("accept_files")
        _shell32().DragAcceptFiles(int(hwnd), bool(accept))
        after = window_info(hwnd)
        result.update({"ok": True, "registered_after": after.get("accept_files"),
                       "ex_style": after.get("ex_style")})
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _monitor_work_rect(hwnd: int = 0, point: tuple[int, int] | None = None) -> dict:
    """目标监视器（默认窗口所在）的工作区（物理像素）；失败返回 {}。"""
    if not IS_WIN:
        return {}
    try:
        u32 = _u32()
        if point is not None:
            mon = u32.MonitorFromPoint(wintypes.POINT(int(point[0]), int(point[1])), 2)
        elif hwnd:
            mon = u32.MonitorFromWindow(int(hwnd), 2)
        else:
            mon = u32.MonitorFromWindow(None, 2)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not u32.GetMonitorInfoW(mon, ctypes.byref(info)):
            return {}
        work, full = info.rcWork, info.rcMonitor
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
    try:
        scale = (dpi or 96) / 96.0
        px, py = int(round(x * scale)), int(round(y * scale))
        mon = _monitor_work_rect(point=(px + 10, py + 10))
        if not mon:
            return max(0, int(x)), max(0, int(y))
        work = mon["work"]
        pw, ph = int(round(w * scale)), int(round(h * scale))
        if px + pw > work["right"]:
            px = max(work["left"], work["right"] - pw)
        if px < work["left"]:
            px = work["left"]
        if py + ph > work["bottom"]:
            py = max(work["top"], work["bottom"] - ph)
        if py < work["top"]:
            py = work["top"]
        return int(round(px / scale)), int(round(py / scale))
    except Exception:  # noqa: BLE001
        return max(0, int(x)), max(0, int(y))


def window_logical_rect(hwnd: int, dpi: int = 144, frame: bool = True) -> dict:
    """窗口 rect → 逻辑坐标（用于把位置存进 settings）；失败返回 {}。"""
    try:
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
    except Exception:  # noqa: BLE001
        return {}
