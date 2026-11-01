"""P3 · 托盘（pystray 旁路）：显示 / 隐藏 / 退出。

说明：
- Flet 1.0.5 无 TrayIcon API；本模块在**服务进程**里用 pystray 建托盘图标，
  窗口操作走 winplat（跨进程 ShowWindow / WM_CLOSE），不碰 Flutter 内部。
- 退出 = 向 flet 客户端窗口发 WM_CLOSE，走应用 prevent_close 保存 → destroy 正常链路。
- 失败（无 pystray / 托盘不可用）一律降级：返回 started=False 并 mark，不影响主流程。
"""
from __future__ import annotations

import threading
from typing import Optional

from services import winplat

ICON_TITLE = "编辑部工具箱"


def _make_image():
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, size - 4, size - 4), radius=14, fill=(79, 70, 229, 255))
    # 简化的书页图形（不依赖中文字体）
    draw.rounded_rectangle((18, 14, 46, 50), radius=4, fill=(255, 255, 255, 255))
    for y in (24, 32, 40):
        draw.line((24, y, 40, y), fill=(79, 70, 229, 255), width=3)
    return image


class TrayService:
    def __init__(self, app) -> None:
        self.app = app
        self.icon = None
        self.started = False
        self.error = ""
        self._thread: Optional[threading.Thread] = None

    # ── 动作 ──────────────────────────────────────────────────────────
    def _hwnd(self) -> int:
        if not self.app.client_hwnd or not winplat.window_info(self.app.client_hwnd):
            self.app.client_hwnd = winplat.find_client_window()
        return self.app.client_hwnd or 0

    def _mark(self, event: str, **fields) -> None:
        try:
            self.app.mark(event, **fields)
        except Exception:  # noqa: BLE001 - 托盘线程不能因标记失败崩
            pass

    def show(self, *_args) -> None:  # pystray 回调签名 (icon, item)
        hwnd = self._hwnd()
        ok = winplat.show_window(hwnd, True, foreground=True)
        self._mark("tray_show", ok=ok, hwnd=hwnd)

    def hide(self, *_args) -> None:
        hwnd = self._hwnd()
        ok = winplat.show_window(hwnd, False)
        self._mark("tray_hide", ok=ok, hwnd=hwnd)
        if ok:
            self.app.status_bar.set_status("已隐藏到托盘")
            try:
                self.app.page.update()
            except Exception:  # noqa: BLE001
                pass

    def quit(self, *_args) -> None:
        hwnd = self._hwnd()
        self._mark("tray_quit", hwnd=hwnd)
        self.stop()
        winplat.close_window(hwnd)  # → 应用 prevent_close 保存/销毁链路

    # ── 生命周期 ─────────────────────────────────────────────────────
    def start(self) -> dict:
        info = {"started": False, "error": ""}
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", self.show, default=True),
                pystray.MenuItem("隐藏主窗口", self.hide),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self.quit),
            )
            self.icon = pystray.Icon("editorial-toolbox", _make_image(), ICON_TITLE, menu)
            self._thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
            self._thread.start()
            self.started = True
            info["started"] = True
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            info["error"] = self.error
        self._mark("tray_started", **info)
        return info

    def stop(self) -> None:
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass
        self.started = False

    def invoke(self, action: str) -> dict:
        """测试/脚本用：直接触发托盘菜单动作（不依赖鼠标点击托盘）。"""
        action = (action or "").strip().lower()
        fn = {"show": self.show, "hide": self.hide, "quit": self.quit}.get(action)
        if fn is None:
            return {"ok": False, "error": f"unknown tray action: {action}"}
        fn()
        return {"ok": True, "action": action}
