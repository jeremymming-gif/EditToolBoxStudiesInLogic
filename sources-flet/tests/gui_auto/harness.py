"""Win32 + Pillow 的真实窗口自动化底座（无第三方新依赖）。

能力：启动真实 Flet 进程、按后代 pid 找窗、PrintWindow 截图、真实鼠标点击、
JSONL 标记等待、进程树清理、视觉基线 diff（尺寸 / 全图 / 分区 / 颜色）。

设计约束（与 p6-gui-auto-plan.md 一致）：
- 只认被测 python 进程的后代窗口，绝不按标题全局找窗（Flet 会留 ``Working…`` 孤儿窗）。
- 截图为窗口 frame 尺寸（含标题栏），文件名写入窗口尺寸与 DPI，便于基线可追溯。
- 所有点击使用屏幕物理坐标，坐标常量以 client DIP 表示，按 ``resize`` 标记的
  ``page_width/page_height`` 换算当前 scale。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Thread

from PIL import Image, ImageChops, ImageStat

# ── 路径 / 常量 ────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[3]
APP_DIR = REPO_ROOT / "sources-flet" / "app"
APP_TITLE = "编辑部工具箱"
WINDOW_CLASS = "FLUTTER_RUNNER_WIN32_WINDOW"
WINDOW_SIZE = "1240x860"
EXPECTED_PAGE_W = 1225.0
EXPECTED_PAGE_H = 822.0
DEFAULT_EVIDENCE = Path(
    os.environ.get("SLCN_GUI_EVIDENCE")
    or r"C:\slcn-work\p6-2026-11-01\evidence\p6-gui-auto"
)
FATAL_EVENTS = ("platform_error", "on_show_error", "render_error", "error")
CREATE_NO_WINDOW = 0x08000000
WM_CLOSE = 0x0010

# 150% DPI 实测布局（client DIP）；其它 DPI 由 scale 换算。
NAV_X = 110.7
NAV_Y = {
    "home": 136.7,
    "single": 178.7,
    "issue": 220.7,
    "bib": 301.7,
    "settings": 383.7,
    "help": 425.7,
}
SWITCH = (186.7, 703.3)
SINGLE_CHIPS = [(301.3, 96.7), (391.3, 96.7)]  # 两 flow：初排转换 / 一校核查
# 跳转按钮「去引用库 → 规范化」：胶囊行右侧（busy_bar 不可见时贴右缘）。**估位**：
# 内容右缘≈1201、按钮宽≈195 → 中心≈1103.5；Windows 复核按实测校正。
JUMP_BTN = (1103.5, 96.7)
RUN_BTN = (1155.0, 41.0)
STOP_BTN = (1051.3, 42.0)
# 引用库两级导航（10 视图 / 4 组）：组条在上一行、胶囊在下一行。
# 坐标按 150% DPI 的布局公式估位：内容左≈259.7；chip 宽 2 字≈56.7、3 字≈67.3、4 字≈77.9，
# 间距 8；组 tab 宽≈46、间距 8；组条中心 y≈100.0、胶囊中心 y≈143.0。
# **未在 Windows 实测**：复核时以 S5 的高亮断言为准（偏差 >12 DIP 即失败）。
BIB_GROUP_POS = {
    "read": (282.7, 100.0),
    "intake": (336.7, 100.0),
    "treat": (390.7, 100.0),
    "maint": (444.7, 100.0),
}
BIB_CHIP_POS = {
    "overview": (288.0, 143.0),
    "query": (352.7, 143.0),
    "normalize": (293.4, 143.0),
    "import": (363.4, 143.0),
    "review": (293.4, 143.0),
    "edit": (363.4, 143.0),
    "dupes": (438.7, 143.0),
    "fieldfix": (524.6, 143.0),
    "health": (293.4, 143.0),
    "settings": (363.4, 143.0),
}
_BIB_VIEW_ORDER = (
    "overview", "query", "normalize", "import", "review",
    "edit", "dupes", "fieldfix", "health", "settings",
)
BIB_CHIPS = [BIB_CHIP_POS[view] for view in _BIB_VIEW_ORDER]


# ── 批 114：二级视图迁入左侧导航（accordion；父行 30 / 组行 28 / 子行 28）──
# 下列坐标是 150% DPI 的**估算**（导航改造后尚未在 Windows 实测）：
# 复核时以 nav_change 事件的 route 字段为准，坐标偏差不应作为唯一判据。
NAV_PARENT_H = 30
NAV_GROUP_H = 28
NAV_CHILD_H = 28
NAV_CHILD_INDENT = 52
NAV_GROUP_INDENT = 28
NAV_CHILD_X = NAV_X + NAV_CHILD_INDENT - 6
NAV_GROUP_X = NAV_X + NAV_GROUP_INDENT - 4
# 子行从「父行中心 + 24」开始，逐行 +28；组行同高（28）。
# 引用库：父项展开 → 4 组行 → 当前组展开的子行。
_BIB_GROUPS_ORDER = ("read", "intake", "treat", "maint")
_BIB_GROUP_VIEWS = {
    "read": ("overview", "query"),
    "intake": ("normalize", "import"),
    "treat": ("review", "edit", "dupes", "fieldfix"),
    "maint": ("health", "settings"),
}


def nav_route_clicks(page: str, view: str = "") -> list[tuple[float, float]]:
    """route → 侧栏点击序列（父项 → [组行] → 子行）；估算坐标，未实测。"""
    if view in ("", "overview") and page == "issue":
        view = ""
    if not view:
        return [(NAV_X, NAV_Y[page])]
    if page == "single":
        offset = {"convert": 24, "proofcheck": 52}[view]
        return [(NAV_X, NAV_Y["single"]), (NAV_CHILD_X, NAV_Y["single"] + offset)]
    if page == "issue":
        steps = ("infocheck", "toc", "compileall", "merge", "cleantemp", "dirpush")
        offset = 24 + 28 * steps.index(view)
        return [(NAV_X, NAV_Y["issue"]), (NAV_CHILD_X, NAV_Y["issue"] + offset)]
    if page == "bib":
        group = next(g for g, views in _BIB_GROUP_VIEWS.items() if view in views)
        gidx = _BIB_GROUPS_ORDER.index(group)
        group_y = NAV_Y["bib"] + 24 + 28 * gidx
        before = sum(len(_BIB_GROUP_VIEWS[g]) for g in _BIB_GROUPS_ORDER[:gidx])
        idx = _BIB_GROUP_VIEWS[group].index(view)
        view_y = group_y + 24 + 28 * (idx if group == _BIB_GROUPS_ORDER[0] else idx)
        # 目标组行点击只展开该组；非首组时子行起点为组行 + 24。
        return [(NAV_X, NAV_Y["bib"]), (NAV_GROUP_X, group_y),
                (NAV_CHILD_X, view_y)]
    return [(NAV_X, NAV_Y[page])]


def bib_group_pos(group: str) -> tuple[float, float]:
    """组条中心（client DIP）；未知组回落到第一组。"""
    return BIB_GROUP_POS.get(group, BIB_GROUP_POS["read"])


def bib_chip_pos(view: str) -> tuple[float, float]:
    """视图胶囊中心（client DIP）；未知视图回落到「概况」。"""
    return BIB_CHIP_POS.get(view, BIB_CHIP_POS["overview"])
LIGHT_SIDEBAR = (255, 255, 255)
LIGHT_BG = (246, 248, 250)
DARK_SIDEBAR = (15, 20, 27)
DARK_BG = (13, 17, 23)
ACCENT = (79, 70, 229)
ACCENT_SOFT = (224, 231, 255)
ACCENT_SOFT_DARK = (42, 47, 99)

# ── Win32 绑定 ─────────────────────────────────────────────────────────────
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.ClientToScreen.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.GetWindowDC.argtypes = [wt.HWND]
user32.GetWindowDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.PrintWindow.restype = wt.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
if hasattr(user32, "GetDpiForSystem"):
    user32.GetDpiForSystem.restype = wt.UINT
if hasattr(user32, "SetProcessDpiAwarenessContext"):
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = wt.BOOL

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.GetDIBits.argtypes = [
    wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.DeleteDC.restype = wt.BOOL

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.GetExitCodeProcess.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.Process32FirstW.restype = wt.BOOL
kernel32.Process32NextW.restype = wt.BOOL


def _make_dpi_aware() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness(2)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


_make_dpi_aware()


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


class BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
        ("colors", wt.DWORD * 3),
    ]


def system_dpi() -> int:
    try:
        return int(user32.GetDpiForSystem())
    except Exception:  # noqa: BLE001
        return 96


def screen_size() -> tuple[int, int]:
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def _process_map() -> dict[int, tuple[int, str]]:
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if not snap or int(snap) == ctypes.c_void_p(-1).value:
        return {}
    out: dict[int, tuple[int, str]] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                entry.szExeFile,
            )
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return out


def is_alive(pid: int) -> bool:
    if not pid:
        return False
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wt.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def tree_pids(root_pid: int) -> set[int]:
    pmap = _process_map()
    children: dict[int, list[int]] = defaultdict(list)
    for pid, (ppid, _name) in pmap.items():
        children[ppid].append(pid)
    out = {int(root_pid)}
    stack = [int(root_pid)]
    while stack:
        cur = stack.pop()
        for child in children.get(cur, []):
            if child not in out:
                out.add(child)
                stack.append(child)
    return out


def kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        pass


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str


def top_windows() -> list[WindowInfo]:
    out: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(length + 1, 2))
        user32.GetWindowTextW(hwnd, buf, len(buf))
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append(WindowInfo(int(hwnd), int(pid.value), buf.value, cls.value))
        return True

    user32.EnumWindows(_cb, 0)
    return out


def find_app_window(pids: set[int], timeout: float = 45.0) -> WindowInfo | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for win in top_windows():
            if win.pid in pids and win.class_name == WINDOW_CLASS:
                if APP_TITLE in win.title:
                    return win
        time.sleep(0.2)
    return None


def window_metrics(hwnd: int) -> tuple[tuple[int, int, int, int], tuple[int, int], tuple[int, int]]:
    frame = wt.RECT()
    client = wt.RECT()
    pt = wt.POINT(0, 0)
    if not user32.GetWindowRect(hwnd, ctypes.byref(frame)):
        raise RuntimeError(f"GetWindowRect failed hwnd={hwnd} err={ctypes.get_last_error()}")
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        raise RuntimeError(f"GetClientRect failed hwnd={hwnd}")
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        raise RuntimeError(f"ClientToScreen failed hwnd={hwnd}")
    frect = (frame.left, frame.top, frame.right - frame.left, frame.bottom - frame.top)
    csize = (client.right, client.bottom)
    origin = (pt.x, pt.y)
    return frect, csize, origin


def capture_window(hwnd: int) -> Image.Image:
    left, top, width, height = window_metrics(hwnd)[0]
    if width <= 0 or height <= 0:
        raise RuntimeError(f"window size invalid: {width}x{height}")
    hdc_win = user32.GetWindowDC(hwnd)
    if not hdc_win:
        raise RuntimeError("GetWindowDC failed")
    mdc = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, width, height)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        ok = bool(user32.PrintWindow(hwnd, mdc, 2))  # PW_RENDERFULLCONTENT
        bi = BMIH()
        bi.biSize = ctypes.sizeof(BMIH)
        bi.biWidth = width
        bi.biHeight = -height  # top-down
        bi.biPlanes = 1
        bi.biBitCount = 32
        buf = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(mdc, bmp, 0, height, buf, ctypes.byref(bi), 0)
        if not ok or lines == 0:
            raise RuntimeError(f"PrintWindow/GetDIBits failed ok={ok} lines={lines}")
        img = Image.frombuffer("RGB", (width, height), buf, "raw", "BGRX", 0, 1).copy()
    finally:
        try:
            gdi32.SelectObject(mdc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mdc)
        finally:
            user32.ReleaseDC(hwnd, hdc_win)
    if img.getbbox() is None:  # 全黑（窗口被遮挡/最小化）→ 屏幕抓取兜底
        from PIL import ImageGrab

        img = ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True)
        img = img.convert("RGB")
    return img


def click_screen(x: float, y: float) -> None:
    user32.SetCursorPos(int(round(x)), int(round(y)))
    time.sleep(0.08)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    time.sleep(0.2)


def move_away() -> None:
    user32.SetCursorPos(5, 5)
    time.sleep(0.08)


def markers(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


# ── 像素工具（Pillow，不依赖 numpy）──────────────────────────────────────
def diff_stats(a: Image.Image, b: Image.Image, threshold: int = 16) -> dict:
    if a.size != b.size:
        return {"size_equal": False, "mean": 999.0, "pct": 100.0, "p90": 255,
                "bbox": None, "size": a.size}
    d = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    hist = d.histogram()
    total = a.size[0] * a.size[1]
    changed = sum(hist[threshold + 1:])
    mean = ImageStat.Stat(d).mean[0]
    cum = 0
    p90 = 255
    target = total * 0.9
    for value, count in enumerate(hist):
        cum += count
        if cum >= target:
            p90 = value
            break
    mask = d.point(lambda v: 255 if v > threshold else 0)
    return {
        "size_equal": True,
        "mean": round(mean, 3),
        "pct": round(100.0 * changed / total, 3),
        "p90": p90,
        "bbox": mask.getbbox(),
        "size": a.size,
    }


def save_diff(a: Image.Image, b: Image.Image, path: Path) -> None:
    if a.size != b.size:
        return
    d = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    d.point(lambda v: 255 if v > 16 else 0).save(path)


def color_bbox(img: Image.Image, color: tuple[int, int, int], region: tuple[int, int, int, int],
               tol: int = 2) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = region
    crop = img.crop((left, top, right, bottom))
    px = crop.load()
    minx = miny = 10 ** 9
    maxx = maxy = -1
    r, g, b = color
    for y in range(crop.size[1]):
        for x in range(crop.size[0]):
            pr, pg, pb = px[x, y]
            if abs(pr - r) <= tol and abs(pg - g) <= tol and abs(pb - b) <= tol:
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if maxx < 0:
        return None
    return (left + minx, top + miny, left + maxx, top + maxy)


def active_row_center(img: Image.Image, color: tuple[int, int, int], region: tuple[int, int, int, int],
                      *, tol: int = 2, min_count: int = 30) -> tuple[float, float] | None:
    """返回横向高亮条（active 导航/选项卡）的几何中心（图像绝对坐标）。"""
    bbox = color_bbox(img, color, region, tol)
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    # 高亮条是圆角矩形：取颜色像素最多的连续行段，避免把文字/图标算进高度
    crop = img.crop(region)
    px = crop.load()
    counts = []
    r, g, b = color
    for y in range(region[3] - region[1]):
        c = 0
        for x in range(region[2] - region[0]):
            pr, pg, pb = px[x, y]
            if abs(pr - r) <= tol and abs(pg - g) <= tol and abs(pb - b) <= tol:
                c += 1
        counts.append(c)
    rows = [i for i, c in enumerate(counts) if c >= min_count]
    if not rows:
        return None
    spans = []
    start = prev = rows[0]
    for y in rows[1:]:
        if y > prev + 1:
            spans.append((start, prev))
            start = y
        prev = y
    spans.append((start, prev))
    start, end = max(spans, key=lambda s: s[1] - s[0])
    cy = region[1] + (start + end) / 2
    cx = (left + right) / 2
    return cx, cy


def regions_for(session: "AppSession", width: int, height: int) -> dict[str, tuple[int, int, int, int]]:
    bx, by = session.border
    sidebar = min(int(round(bx + 236 * session.scale)), width)
    bottom = min(int(round(by + session.client_size[1])), height)
    status_h = int(round(28 * session.scale))
    status_top = max(by, height - status_h)
    return {
        "titlebar": (0, 0, width, by),
        "sidebar": (bx, by, sidebar, bottom),
        "content": (sidebar, by, width, bottom),
        "statusbar": (sidebar, status_top, width, height),
    }


def assert_color(img: Image.Image, x: int, y: int, rgb: tuple[int, int, int],
                 tol: int = 6, label: str = "") -> tuple[int, int, int]:
    actual = img.load()[int(x), int(y)]
    if any(abs(a - b) > tol for a, b in zip(actual, rgb)):
        raise AssertionError(f"颜色断言失败 {label} @({x},{y}) 期望{rgb} 实际{actual} 容差{tol}")
    return actual


def expect(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


# ── 运行上下文 / 视觉基线 ─────────────────────────────────────────────────
@dataclass
class BaselineResult:
    name: str
    status: str  # created | updated | passed | failed
    detail: dict = field(default_factory=dict)


class Baseline:
    def __init__(self, root: Path, diff_dir: Path, update: bool = False) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.diff_dir = Path(diff_dir)
        self.diff_dir.mkdir(parents=True, exist_ok=True)
        self.update = update
        self.results: list[BaselineResult] = []

    def expect(self, name: str, img: Image.Image, *,
               regions: dict[str, tuple[int, int, int, int]] | None = None,
               mean_max: float = 1.5, pct_max: float = 0.8,
               region_mean_max: float = 2.0) -> BaselineResult:
        target = self.root / name
        if self.update and target.exists():
            img.save(target)
            result = BaselineResult(name, "updated")
            self.results.append(result)
            return result
        if not target.exists():
            img.save(target)
            result = BaselineResult(name, "created")
            self.results.append(result)
            return result
        base = Image.open(target).convert("RGB")
        if base.size != img.size:
            raise AssertionError(
                f"基线尺寸不一致 {name}: 基线{base.size} 当前{img.size}"
                f"（删除/更新基线后重跑：--gui-update-baseline）"
            )
        stats = diff_stats(base, img)
        region_stats = {}
        for key, region in (regions or {}).items():
            left, top, right, bottom = region
            region_stats[key] = diff_stats(
                base.crop((left, top, right, bottom)), img.crop((left, top, right, bottom))
            )
        detail = {"full": stats, "regions": region_stats}
        bad = [k for k, v in region_stats.items() if v["mean"] > region_mean_max]
        if stats["mean"] > mean_max or stats["pct"] > pct_max or bad:
            save_diff(base, img, self.diff_dir / f"diff_{name}")
            (self.diff_dir / f"diff_{name}.json").write_text(
                json.dumps({"name": name, **detail}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result = BaselineResult(name, "failed", detail)
            self.results.append(result)
            worst = max(region_stats.items(), key=lambda kv: kv[1]["mean"]) if region_stats else ("-", {})
            raise AssertionError(
                f"视觉基线不一致 {name}: mean={stats['mean']} (上限{mean_max}) "
                f"pct={stats['pct']}% (上限{pct_max}%) 分区最差={worst[0]}:{worst[1].get('mean')} "
                f"diff 见 {self.diff_dir / ('diff_' + name)}"
            )
        result = BaselineResult(name, "passed", detail)
        self.results.append(result)
        return result


class RunContext:
    def __init__(self, evidence_root: Path, baseline_root: Path | None = None,
                 update_baseline: bool = False) -> None:
        self.evidence_root = Path(evidence_root)
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = self.evidence_root / "runs" / self.run_id
        self.screens = self.run_dir / "screens"
        self.sessions_dir = self.run_dir / "sessions"
        self.diffs = self.run_dir / "diffs"
        for d in (self.screens, self.sessions_dir, self.diffs):
            d.mkdir(parents=True, exist_ok=True)
        self.baseline = Baseline(baseline_root or self.evidence_root / "baseline",
                                 self.diffs, update_baseline)
        self.records: list[dict] = []
        self.sessions: list[dict] = []
        self.notes: list[str] = []
        self.started = datetime.now()
        self.meta = self._collect_meta()
        (self.run_dir / "run.json").write_text(
            json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _collect_meta(self) -> dict:
        versions: dict[str, str] = {}
        for mod in ("flet", "PIL"):
            try:
                versions[mod] = __import__(mod).__version__
            except Exception:  # noqa: BLE001
                versions[mod] = "unknown"
        try:
            git = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO_ROOT),
                capture_output=True, text=True, timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            git_head = git.stdout.strip() or "unknown"
        except Exception:  # noqa: BLE001
            git_head = "unknown"
        width, height = screen_size()
        return {
            "run_id": self.run_id,
            "started_at": self.started.isoformat(timespec="seconds"),
            "command": " ".join(sys.argv),
            "cwd": os.getcwd(),
            "repo": str(REPO_ROOT),
            "git_head": git_head,
            "python": sys.version.split()[0],
            "python_exe": sys.executable,
            "flet": versions.get("flet"),
            "pillow": versions.get("PIL"),
            "screen": {"width": width, "height": height, "dpi": system_dpi()},
            "platform": sys.platform,
        }

    def session(self, tag: str, *, page: str = "home",
                extra_env: dict[str, str] | None = None,
                settings: dict | None = None) -> "AppSession":
        return AppSession(self, tag, page=page, extra_env=extra_env, settings=settings)

    def record(self, name: str, **data) -> None:
        row = {"scenario": name, **data}
        self.records.append(row)
        (self.run_dir / f"{name}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def note(self, text: str) -> None:
        self.notes.append(text)

    def finalize(self) -> None:
        payload = {
            "meta": self.meta,
            "records": self.records,
            "baseline": [
                {"name": r.name, "status": r.status, "detail": r.detail}
                for r in self.baseline.results
            ],
            "sessions": self.sessions,
            "notes": self.notes,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        (self.run_dir / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ── 会话：真实进程 + 窗口 + 交互 ─────────────────────────────────────────
class AppSession:
    def __init__(self, ctx: RunContext, tag: str, *, page: str = "home",
                 extra_env: dict[str, str] | None = None,
                 settings: dict | None = None) -> None:
        self.ctx = ctx
        self.tag = tag
        self.page = page
        self.extra_env = dict(extra_env or {})
        self.dir = ctx.sessions_dir / tag
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings = self.dir / "settings.json"
        self.settings.write_text(json.dumps(settings or {}, ensure_ascii=False), encoding="utf-8")
        self.marker_path = self.dir / "markers.jsonl"
        self.marker_path.unlink(missing_ok=True)
        self.stdout_path = self.dir / "stdout.log"
        self.log_path = self.dir / "session.log"
        self.proc: subprocess.Popen | None = None
        self.window: WindowInfo | None = None
        self.frame = (0, 0, 0, 0)
        self.client_size = (0, 0)
        self.client_origin = (0, 0)
        self.border = (0, 0)
        self.scale = 1.0
        self.page_size = (0.0, 0.0)
        self.dpi = system_dpi()
        self._lines: list[str] = []
        self._closed = False
        self._rc: int | None = None
        self.native_close_ok = False
        self.cleanup_killed: list[int] = []

    # -- 日志 --------------------------------------------------------------
    def _log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {message}\n")

    # -- 生命周期 ----------------------------------------------------------
    def __enter__(self) -> "AppSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            self._dump_failure(exc)
            self._shutdown(force=True)
            return
        try:
            self.close()  # 正常场景也走真实 WM_CLOSE，验证退出码与进程清理
        except Exception as close_exc:  # noqa: BLE001
            self._log(f"graceful close failed: {close_exc!r}")
            self._shutdown(force=True)

    def start(self) -> None:
        cleanup_orphans(self._log)
        env = os.environ.copy()
        env.update({
            "P2_MARKER": str(self.marker_path),
            "P2_SETTINGS": str(self.settings),
            "P2_WINSIZE": WINDOW_SIZE,
            "P2_PAGE": self.page,
            "P3_TRAY": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        })
        env.update(self.extra_env)
        self._log(f"launch page={self.page} env_extra={self.extra_env}")
        self.proc = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=str(APP_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )

        def _drain() -> None:
            assert self.proc is not None and self.proc.stdout is not None
            with self.stdout_path.open("w", encoding="utf-8") as fh:
                for line in self.proc.stdout:
                    stripped = line.rstrip("\n")
                    self._lines.append(stripped)
                    fh.write(stripped + "\n")
                    fh.flush()

        Thread(target=_drain, daemon=True, name=f"stdout-{self.tag}").start()
        self.wait_ready()

    def wait_ready(self, timeout: float = 55.0) -> None:
        assert self.proc is not None
        deadline = time.monotonic() + timeout
        resize_event = None
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"[{self.tag}] 进程提前退出 rc={self.proc.returncode}\n{self.stdout_tail()}"
                )
            if self.window is None:
                self.window = find_app_window(tree_pids(self.proc.pid), timeout=0.05)
            resize_event = self.last_event("resize")
            if self.window is not None and resize_event is not None:
                break
            time.sleep(0.2)
        if self.window is None:
            raise AssertionError(
                f"[{self.tag}] 未找到窗口（后代 pid 窗口）。events={self.event_names()}\n"
                f"{self.stdout_tail()}"
            )
        if resize_event is None:
            raise AssertionError(
                f"[{self.tag}] 未等到 resize 标记。events={self.event_names()}\n{self.stdout_tail()}"
            )
        self.page_size = (float(resize_event.get("page_width") or 0),
                          float(resize_event.get("page_height") or 0))
        self.frame, self.client_size, self.client_origin = window_metrics(self.window.hwnd)
        self.border = (self.client_origin[0] - self.frame[0], self.client_origin[1] - self.frame[1])
        if self.page_size[0] <= 0:
            raise AssertionError(f"[{self.tag}] resize page_width 无效: {resize_event}")
        self.scale = self.client_size[0] / self.page_size[0]
        state = self.last_event("state") or {}
        self.dpi = int(state.get("dpi_system") or system_dpi())
        self._log(
            f"ready hwnd={self.window.hwnd} frame={self.frame} client={self.client_size} "
            f"page={self.page_size} scale={self.scale:.3f} dpi={self.dpi} title={self.window.title!r}"
        )
        try:
            user32.SetForegroundWindow(self.window.hwnd)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
        self.wait_stable(timeout=3.0)
        traceback = "Traceback (most recent call last)" in self.stdout_text()
        if traceback:
            raise AssertionError(f"[{self.tag}] 启动日志出现 Traceback\n{self.stdout_tail()}")
        fatal = [e for e in self.events() if e.get("event") in FATAL_EVENTS]
        if fatal:
            raise AssertionError(f"[{self.tag}] 启动出现错误标记: {fatal[-3:]}")

    def stdout_text(self) -> str:
        return "\n".join(self._lines)

    def stdout_tail(self, n: int = 40) -> str:
        return "\n".join(self._lines[-n:])

    # -- 事件 --------------------------------------------------------------
    def events(self) -> list[dict]:
        return markers(self.marker_path)

    def event_names(self, after: int = 0) -> list[str]:
        return [e.get("event", "?") for e in self.events()[after:]]

    def last_event(self, name: str) -> dict | None:
        hits = [e for e in self.events() if e.get("event") == name]
        return hits[-1] if hits else None

    def wait_event(self, name: str, pred=None, *, timeout: float = 20.0,
                   after: int = 0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hits = [e for e in self.events() if e.get("event") == name][after:]
            for event in hits:
                if pred is None or pred(event):
                    return event
            if self.proc is not None and self.proc.poll() is not None:
                break
            time.sleep(0.15)
        raise AssertionError(
            f"[{self.tag}] 等不到事件 {name} (after={after}, timeout={timeout}s)；"
            f"现有事件={self.event_names()[-20:]}\n{self.stdout_tail(20)}"
        )

    def has_event(self, name: str, pred=None, *, after: int = 0) -> bool:
        return any(
            (pred is None or pred(e)) for e in self.events()[after:] if e.get("event") == name
        )

    def engine_ok(self) -> bool:
        ev = self.last_event("engine_status")
        if ev is not None:
            return bool(ev.get("ok"))
        state = self.last_event("state") or {}
        engine = state.get("engine")
        return bool(engine.get("ok")) if isinstance(engine, dict) else False

    # -- 截图 / 交互 -------------------------------------------------------
    def wait_stable(self, *, timeout: float = 6.0, interval: float = 0.3,
                    mean_tol: float = 0.6) -> Image.Image:
        assert self.window is not None
        prev = capture_window(self.window.hwnd)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(interval)
            cur = capture_window(self.window.hwnd)
            if diff_stats(prev, cur)["mean"] <= mean_tol:
                return cur
            prev = cur
        return prev

    def screenshot(self, label: str, *, compare: bool = True,
                   regions: dict[str, tuple[int, int, int, int]] | None = None,
                   mean_max: float = 1.5, pct_max: float = 0.8) -> Image.Image:
        img = self.wait_stable()
        width, height = img.size
        name = (
            f"{label}__win{width}x{height}_client{self.client_size[0]}x"
            f"{self.client_size[1]}_dpi{self.dpi}.png"
        )
        img.save(self.ctx.screens / name)
        self._log(f"screenshot {name}")
        if compare:
            self.ctx.baseline.expect(
                name, img, regions=regions or regions_for(self, width, height),
                mean_max=mean_max, pct_max=pct_max,
            )
        return img

    def settle(self, seconds: float = 0.6) -> None:
        time.sleep(seconds)
        self.wait_stable()

    def click_image(self, ix: float, iy: float) -> None:
        assert self.window is not None
        try:
            user32.SetForegroundWindow(self.window.hwnd)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.05)
        sx = self.frame[0] + ix
        sy = self.frame[1] + iy
        click_screen(sx, sy)
        self._log(f"click image=({ix:.1f},{iy:.1f}) screen=({sx:.1f},{sy:.1f})")

    def click_dip(self, dx: float, dy: float) -> None:
        ix = self.border[0] + dx * self.scale
        iy = self.border[1] + dy * self.scale
        self.click_image(ix, iy)

    def dip_to_image(self, dx: float, dy: float) -> tuple[float, float]:
        return self.border[0] + dx * self.scale, self.border[1] + dy * self.scale

    # -- 关闭 / 清理 -------------------------------------------------------
    def close(self, *, native: bool = False, timeout: float = 25.0) -> int:
        if self._closed:
            assert self._rc is not None
            return self._rc
        assert self.proc is not None and self.window is not None
        known = tree_pids(self.proc.pid)
        self._log(f"close native={native} known_pids={sorted(known)}")
        if native:
            width, height = self.frame[2], self.frame[3]
            self.click_image(width - 22, 22)
            try:
                self.proc.wait(8)
                self.native_close_ok = True
            except subprocess.TimeoutExpired:
                self._log("native caption X 未关闭，回退 WM_CLOSE")
        if self.proc.poll() is None:
            user32.PostMessageW(self.window.hwnd, WM_CLOSE, 0, 0)
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self._log("WM_CLOSE 超时，强杀进程树")
                kill_tree(self.proc.pid)
                try:
                    self.proc.wait(10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        self._rc = int(self.proc.returncode if self.proc.returncode is not None else -1)
        self._shutdown(force=False, known=known)
        return self._rc

    def _shutdown(self, *, force: bool, known: set[int] | None = None) -> None:
        if self._closed:
            return
        if self.proc is not None and self.proc.poll() is None:
            kill_tree(self.proc.pid)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if known:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                alive = [pid for pid in known if is_alive(pid)]
                if not alive:
                    break
                time.sleep(0.2)
            for pid in list(known):
                if is_alive(pid):
                    self._log(f"残留进程清理 pid={pid}")
                    self.cleanup_killed.append(pid)
                    kill_tree(pid)
        self._closed = True
        # 证据：复制 marker 到 run 根，便于查看
        if self.marker_path.exists():
            target = self.ctx.run_dir / f"markers_{self.tag}.jsonl"
            target.write_text(self.marker_path.read_text(encoding="utf-8", errors="replace"),
                              encoding="utf-8")
        self.ctx.sessions.append({
            "tag": self.tag,
            "page": self.page,
            "rc": self._rc,
            "cleanup_killed": self.cleanup_killed,
            "events": len(self.events()),
            "stdout_tail": self._lines[-5:],
        })
        self._log(f"closed rc={self._rc} cleanup_killed={self.cleanup_killed}")

    def leftover_pids(self) -> list[int]:
        if self.proc is None:
            return []
        pids = tree_pids(self.proc.pid)
        pids.discard(self.proc.pid)
        return [pid for pid in pids if is_alive(pid)]

    def _dump_failure(self, exc: BaseException) -> None:
        tag = getattr(exc, "__class__", type(exc)).__name__
        lines = [
            f"tag={self.tag} page={self.page}",
            f"error={type(exc).__name__}: {exc}",
            f"frame={self.frame} client={self.client_size} page_size={self.page_size} "
            f"scale={self.scale:.3f} dpi={self.dpi}",
            f"window={self.window}",
            f"events={self.event_names()}",
            "",
            "stdout tail:",
            self.stdout_tail(80),
        ]
        (self.dir / "failure.txt").write_text("\n".join(lines), encoding="utf-8")
        try:
            if self.window is not None:
                img = capture_window(self.window.hwnd)
                img.save(self.ctx.screens / f"FAIL_{self.tag}_{tag}.png")
        except Exception as cap_exc:  # noqa: BLE001
            self._log(f"failure screenshot failed: {cap_exc}")


def cleanup_orphans(log=None) -> list[int]:
    """清理「标题是编辑部工具箱、但父进程已死」的孤儿 Flet 客户端窗口。

    这不是产品问题：Flet 桌面壳是独立进程，测试若只杀 python 会留下
    显示 Working… 的孤儿窗，之后按标题找窗会命中旧窗（已实测）。
    """
    killed: list[int] = []
    pmap = _process_map()
    for win in top_windows():
        if APP_TITLE not in win.title or win.class_name != WINDOW_CLASS:
            continue
        ppid, name = pmap.get(win.pid, (0, ""))
        if name.lower().startswith("flet") and (not ppid or not is_alive(ppid)):
            if log:
                log(f"cleanup orphan flet window pid={win.pid} title={win.title!r}")
            kill_tree(win.pid)
            killed.append(win.pid)
    if killed:
        time.sleep(0.5)
    return killed
