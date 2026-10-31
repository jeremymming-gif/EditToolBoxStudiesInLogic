"""学衡工具箱 · Flet 单壳（B-246c P2 / P3）。

一个窗口 = 一个应用：左侧分组导航 + 页面区 + 状态栏。
页面只做「取值 → 拼参数 → 显示日志」，判定全部在 Engine（见 app/services/engine_bridge.py）。

P2 自动化钩子（证据脚本用，生产环境不设即无副作用）：
  P2_MARKER / P2_SETTINGS / P2_THEME(light|dark) / P2_PAGE / P2_DPI(96|120|144|192)
  P2_ZOOM / P2_WINSIZE(WxH) / P2_AUTO_CLOSE_MS / P2_AUTORUN / P2_CANCEL_MS / P2_OPEN_PICKER(dir|file)
  P2_DEMO_SINGLE_SOURCE / P2_DEMO_SINGLE_OUT / P2_DEMO_BIB / P2_DEMO_PROOF_TEX
  P2_DEMO_ISSUE / P2_DEMO_TOC / P2_SELFTEST(theme|zoom)
P3 扩展（同样只在设置时生效；P3_* 会自动归一为 P2_* 复用既有钩子）：
  P3_MARKER / P3_SETTINGS / P3_THEME / P3_PAGE / P3_DPI / P3_ZOOM / P3_WINSIZE / P3_AUTORUN …
  P3_TRAY=1           启动托盘（显示/隐藏/退出）
  P3_TRAY_TEST=show,hide,show   自动触发托盘动作并记录 marker
  P3_TITLEBAR_PROBE=1 实测 DwmSetWindowAttribute 深色标题栏并记录结果
  P3_DRAG_PROBE=1     尝试 DragAcceptFiles 并记录窗口拖放能力
  P3_GEOMETRY_TEST=1  移动窗口 → 记录几何 → 保存 → 校验恢复钳制
退出语义：窗口关闭 = 立即终止进程；因此启用 prevent_close + WindowEvent.CLOSE，
先落盘设置再 destroy（Flet 1.0 的 atexit/缓冲写不保证执行）。
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import sys
import time
from importlib.metadata import version as _pkg_version
from pathlib import Path

import flet as ft

from services import engine_bridge as EB
from services import winplat
from services.settings_store import SettingsStore
from ui import components as C
from ui import pages as P
from ui.ia_map import NAV_GROUPS
from ui.tokens import BASE_DPI, BASE_H, BASE_W, ZOOM_MAX, ZOOM_MIN, Tokens

APP_TITLE = "学衡工具箱"
APP_VERSION = "v0.4.0-p3"
try:
    FLET_VERSION = _pkg_version("flet")
except Exception:  # noqa: BLE001
    FLET_VERSION = "1.0.5"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
START = time.monotonic()


def _bridge_p3_env() -> list[str]:
    """P3_* 钩子归一为 P2_*（复用既有启动钩子）；返回本次归一的键。"""
    bridged: list[str] = []
    for key, value in list(os.environ.items()):
        if not key.startswith("P3_"):
            continue
        target = "P2_" + key[3:]
        if target not in os.environ:
            os.environ[target] = value
            bridged.append(key)
    return bridged


P3_BRIDGED = _bridge_p3_env()


# ── DPI awareness（Python 默认 unaware：GetDpiForSystem 会假报 96）─────────
def _make_dpi_aware() -> bool:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return True
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return True
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
                return True
            except (AttributeError, OSError):
                return False


DPI_AWARE = _make_dpi_aware()


def _system_dpi() -> int:
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        if dpi > 0:
            return dpi
    except (AttributeError, OSError):
        pass
    return 96


def _screen_work() -> tuple[int, int, int]:
    """返回 (工作区宽, 工作区高, 系统 DPI) 的物理像素；拿不到就退回整屏。"""
    dpi = _system_dpi()
    try:

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width > 0 and height > 0:
                return width, height, dpi
    except (AttributeError, OSError):
        pass
    try:
        return int(ctypes.windll.user32.GetSystemMetrics(0)), int(ctypes.windll.user32.GetSystemMetrics(1)), dpi
    except (AttributeError, OSError):
        return 1920, 1080, dpi


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float | None) -> float | None:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_size(raw: str) -> tuple[int, int] | None:
    if not raw or "x" not in raw.lower():
        return None
    left, _, right = raw.lower().partition("x")
    try:
        return max(200, int(left)), max(150, int(right))
    except ValueError:
        return None


class App:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.app_version = APP_VERSION
        self.flet_version = FLET_VERSION
        self.python_version = sys.version.split()[0]
        self.system_dpi = _system_dpi()
        self.dpi = int(_env_float("P2_DPI", None) or self.system_dpi) if _env_str("P2_DPI") else self.system_dpi
        self.dpi_aware = DPI_AWARE
        self.work_w, self.work_h, _ = _screen_work()
        self.store = SettingsStore()
        self.auto_confirm = os.environ.get("P2_AUTO_CONFIRM", "") == "1"
        settings, exists = self.store.load()
        self.settings_exists = exists
        if self.store.get("engine_override"):
            os.environ.setdefault("P2_ENGINE", str(self.store.get("engine_override")))
        self.notes_repo = str(self.store.get("paths.notes_repo", "") or "")
        self.theme = _env_str("P2_THEME") or str(self.store.get("theme", "light") or "light")
        requested_zoom = _env_float("P2_ZOOM", float(self.store.get("zoom", 1.0) or 1.0)) or 1.0
        self.zoom_requested = max(ZOOM_MIN, min(ZOOM_MAX, float(requested_zoom)))
        self.max_zoom = self._compute_max_zoom()
        self.zoom = min(self.zoom_requested, self.max_zoom)
        self.t = Tokens(self.theme, self.zoom, self.dpi)
        self.running = False
        self.run_seen = False
        self.proc = None
        self.current_call = None
        self.current_page_key = ""
        self.pages: dict[str, P.BasePage] = {}
        self.engine_status = EB.engine_status()
        self.last_line = ""
        self.line_count = 0
        self.first_line_ms = None
        self.run_start_mono = None
        self.cancel_requested = False
        self._marker_path = Path(_env_str("P2_MARKER")) if _env_str("P2_MARKER") else None
        self._marker_lock = asyncio.Lock()
        self._close_requested = False
        self._browse_task = None
        self.prefill_hooks: dict = {}
        self._own_size = _parse_size(_env_str("P2_WINSIZE"))
        self.window_w, self.window_h = self._own_size or self._window_size()
        # ── P3 平台补齐状态 ─────────────────────────────────────────
        self.client_hwnd = 0
        self.tray = None
        self.titlebar_info: dict = {}
        self.geometry: dict = {}
        self.page_switches: list[float] = []
        self._configure_window()

    # ── 窗口 / 规模 ────────────────────────────────────────────────────
    def _window_size(self) -> tuple[int, int]:
        width, height = self.t.window_size
        work_logical_w = self.work_w / (self.system_dpi / 96.0)
        work_logical_h = self.work_h / (self.system_dpi / 96.0)
        width = min(width, int(work_logical_w) - 80)
        height = min(height, int(work_logical_h) - 160)
        return max(1000, width), max(700, height)

    def _compute_max_zoom(self) -> float:
        """用户缩放上限按屏幕工作区钳制：窗口不越界（本屏约 1.6×）。"""
        dpi_k = self.dpi / BASE_DPI
        work_logical_w = self.work_w / (self.system_dpi / 96.0)
        work_logical_h = self.work_h / (self.system_dpi / 96.0)
        by_w = (work_logical_w - 80) / (BASE_W * dpi_k)
        by_h = (work_logical_h - 160) / (BASE_H * dpi_k)
        return max(ZOOM_MIN, round(min(ZOOM_MAX, by_w, by_h), 2))

    def _configure_window(self) -> None:
        page = self.page
        page.title = APP_TITLE
        page.theme_mode = ft.ThemeMode.DARK if self.theme == "dark" else ft.ThemeMode.LIGHT
        page.theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO, use_material3=True)
        page.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO, use_material3=True)
        page.bgcolor = self.t.palette.bg
        page.padding = 0
        page.spacing = 0
        page.window.width = self.window_w
        page.window.height = self.window_h
        min_w, min_h = self.t.min_window_size
        page.window.min_width = min_w
        page.window.min_height = min_h
        page.window.prevent_close = True
        page.window.on_event = self.on_window_event
        page.on_resize = self.on_resize
        # 多屏记忆：恢复上次位置（钳制到所在监视器工作区，绝不恢复到屏外）
        left = self.store.get("window.left")
        top = self.store.get("window.top")
        if isinstance(left, (int, float)) and isinstance(top, (int, float)):
            x, y = winplat.clamp_logical_position(int(left), int(top),
                                                 self.window_w, self.window_h, self.system_dpi)
            page.window.left = x
            page.window.top = y
            self.mark("geometry_restored", saved={"left": int(left), "top": int(top)},
                      applied={"left": x, "top": y})

    # ── 标记（证据脚本读取）────────────────────────────────────────────
    def mark(self, event: str, **fields) -> None:
        payload = {
            "event": event,
            "ts": time.time(),
            "mono": time.monotonic(),
            "pid": os.getpid(),
            "dpi_aware": self.dpi_aware,
        }
        payload.update(fields)
        line = json.dumps(payload, ensure_ascii=False)
        if self._marker_path is not None:
            try:
                self._marker_path.parent.mkdir(parents=True, exist_ok=True)
                with self._marker_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass
        print("P2MARK " + line, flush=True)

    # ── 外壳 ────────────────────────────────────────────────────────────
    def boot(self) -> None:
        self.mark("process_start", title=APP_TITLE, argv=sys.argv[1:])
        self.rebuild_shell()

    def rebuild_shell(self) -> None:
        self.t = Tokens(self.theme, self.zoom, self.dpi)
        self.engine_status = EB.engine_status()
        key = self.current_page_key or str(self.store.get("last_page", "home") or "home")
        self.pages = {cls.key: cls(self) for cls in P.PAGE_CLASSES}
        for page_obj in self.pages.values():      # 所有页都先建好：导航/预填不依赖惰性构建
            if page_obj.root is None:
                page_obj.root = page_obj.build()
        if key not in self.pages:
            key = "home"
        self.current_page_key = key
        self.status_bar = C.StatusBar(self.t)
        self.toast_ctl = C.Toast(self.t)
        self.sidebar = ft.Container(
            width=self.t.px(236), bgcolor=self.t.palette.sidebar,
            border=ft.Border(right=C._side(self.t)), content=self._sidebar_content(),
        )
        self.content = ft.Container(expand=True, content=self.pages[key].root)
        self.toast_slot = ft.Container(padding=C._pad(self.t, 24, 0, 24, 0), content=self.toast_ctl.root)
        body = ft.Column(spacing=0, expand=True, controls=[self.toast_slot, self.content])
        shell = ft.Column(spacing=0, expand=True, controls=[
            ft.Row(spacing=0, expand=True, controls=[self.sidebar, body]),
            self.status_bar.root,
        ])
        self.page.bgcolor = self.t.palette.bg
        self.page.controls.clear()
        self.page.add(shell)
        self.window_w, self.window_h = self._own_size or self._window_size()
        self.page.window.width = self.window_w
        self.page.window.height = self.window_h
        min_w, min_h = self.t.min_window_size
        self.page.window.min_width = min_w
        self.page.window.min_height = min_h
        self.status_bar.set_meta(self._status_meta())
        self.page.update()
        self.mark("shell_built", page=key, theme=self.theme, zoom=self.zoom, dpi=self.dpi)

    def _sidebar_content(self) -> ft.Control:
        t = self.t
        items: list = []
        brand = ft.Container(
            padding=C._pad(t, 18, 18, 18, 16),
            content=ft.Row(spacing=t.sp("2"), controls=[
                ft.Container(
                    width=t.px(36), height=t.px(36), border_radius=t.radius("md"),
                    bgcolor=t.palette.accent_soft, alignment=ft.Alignment.CENTER,
                    content=ft.Icon(C.ic("AUTO_AWESOME", "STAR"), size=t.px(20), color=t.palette.accent),
                ),
                ft.Column(spacing=0, controls=[
                    C.text("学衡工具箱", t, "h3", weight="semibold"),
                    C.text("Flet 单壳 · P3", t, "caption", t.palette.text_faint),
                ]),
            ]),
        )
        items.append(brand)
        for group, entries in NAV_GROUPS:
            items.append(C.nav_group_label(group, t))
            for key, label, icon in entries:
                active = key == self.current_page_key
                items.append(C.nav_item(icon, label, t, active=active,
                                        on_click=lambda e, k=key: self.goto(k)))
        items.append(ft.Container(expand=True))
        items.append(C.divider(t))
        items.append(ft.Container(
            padding=C._pad(t, 18, 8, 18, 8),
            content=ft.Row(spacing=t.sp("1"), controls=[
                ft.Icon(C.ic("DARK_MODE"), size=t.px(18), color=t.palette.text_muted),
                C.text("深色模式", t, "small", t.palette.text_muted),
                ft.Container(expand=True),
                ft.Switch(value=self.theme == "dark", active_color=t.palette.accent,
                          on_change=self.on_theme_switch),
            ]),
        ))
        engine_ok = self.engine_status.ok
        items.append(ft.Container(
            padding=C._pad(t, 18, 2, 18, 12),
            content=ft.Column(spacing=0, controls=[
                C.text(self.app_version + " · " + FLET_VERSION, t, "caption", t.palette.text_faint),
                C.text(("引擎就绪" if engine_ok else "引擎缺失") + " · 本地运行", t, "caption",
                       t.palette.text_faint),
            ]),
        ))
        return ft.Column(spacing=t.sp("half"), expand=True, controls=items)

    def goto(self, key: str) -> None:
        if key not in self.pages or key == self.current_page_key:
            return
        t0 = time.monotonic()
        self.current_page_key = key
        self.store.set("last_page", key)
        self.content.content = self.pages[key].root
        self.sidebar.content = self._sidebar_content()
        self.page.update()
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        self.page_switches.append(elapsed_ms)
        try:
            self.pages[key].on_show()
        except Exception as exc:  # noqa: BLE001
            self.mark("on_show_error", page=key, error=f"{type(exc).__name__}: {exc}")
        self.mark("nav_change", page=key, switch_ms=elapsed_ms,
                  switch_avg_ms=round(sum(self.page_switches) / len(self.page_switches), 1))

    def _status_meta(self) -> str:
        engine = "引擎就绪" if self.engine_status.ok else "引擎缺失"
        return (f"{APP_TITLE} · Flet {FLET_VERSION} · {self.theme} · {self.dpi}dpi · "
                f"{self.dpi / 96.0:.0%} · {self.zoom:.2f}× · {engine}")

    # ── 主题 / 缩放 / 设置 ──────────────────────────────────────────────
    def on_theme_switch(self, e) -> None:
        self.set_theme("dark" if e.control.value else "light")

    def set_theme(self, theme: str) -> None:
        if self.running:
            self.toast("运行中不能切换主题")
            return
        if theme not in ("light", "dark") or theme == self.theme:
            return
        self.theme = theme
        self.store.set("theme", theme)
        self.page.theme_mode = ft.ThemeMode.DARK if theme == "dark" else ft.ThemeMode.LIGHT
        self.rebuild_shell()
        self.persist()
        self.mark("theme_changed", theme=theme)
        self.apply_titlebar()
        self.toast("已切换到" + ("深色" if theme == "dark" else "浅色") + "模式（已落盘）")

    def apply_titlebar(self) -> None:
        """深色标题栏跟随主题（DwmSetWindowAttribute；失败降级为系统默认，不影响界面）。"""
        if not self.client_hwnd:
            self.client_hwnd = winplat.find_client_window(APP_TITLE) or 0
        if not self.client_hwnd:
            return
        self.titlebar_info = winplat.set_dark_titlebar(self.client_hwnd, self.theme == "dark")
        self.mark("titlebar", theme=self.theme, **self.titlebar_info)

    def set_zoom(self, zoom: float) -> None:
        if self.running:
            self.toast("运行中不能调整缩放")
            return
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, round(float(zoom), 2)))
        self.zoom_requested = zoom
        self.zoom = min(zoom, self.max_zoom)
        self.store.set("zoom", zoom)
        self.window_w, self.window_h = Tokens(self.theme, self.zoom, self.dpi).window_size
        self.page.window.width, self.page.window.height = self.window_w, self.window_h
        self.rebuild_shell()
        self.persist()
        self.mark("zoom_changed", requested=zoom, effective=self.zoom, max=self.max_zoom)
        self.toast(f"缩放 {self.zoom:.2f}×（请求 {zoom:.2f}×，上限 {self.max_zoom:.2f}×）")

    @property
    def effective_zoom(self) -> float:
        return self.zoom

    # ── 提示条（内联 Toast，不依赖 overlay/SnackBar） ────────────────────
    def toast(self, message: str) -> None:
        try:
            self.toast_ctl.show(self.page, message)
        except Exception as exc:  # noqa: BLE001
            self.mark("toast_error", error=f"{type(exc).__name__}: {exc}")

    def toast_message(self, message: str) -> None:
        self.toast(message)

    def set_option(self, key: str, value) -> None:
        self.store.update_options(**{key: value})
        self.persist()

    def set_check_done(self, value: bool) -> None:
        self.store.set("env_check_done", bool(value))
        self.persist()

    def persist(self) -> None:
        try:
            self.store.save()
        except OSError as exc:
            self.mark("settings_save_error", error=str(exc))

    def save_settings(self, toast: bool = False) -> None:
        self.store.set("last_page", self.current_page_key)
        self.persist()
        self.mark("settings_saved", file=str(self.store.path), exists=self.store.path.exists())
        if toast:
            self.toast("设置已保存")

    def refresh_engine_status(self) -> None:
        self.engine_status = EB.engine_status()
        self.mark("engine_status", **self.engine_status.to_dict())

    # ── 运行编排 ────────────────────────────────────────────────────────
    def begin_run(self, call: EB.EngineCall) -> None:
        self.running = True
        self.run_seen = True
        self.cancel_requested = False
        self.proc = None
        self.current_call = call
        self.last_line = ""
        self.line_count = 0
        self.first_line_ms = None
        self.run_start_mono = time.monotonic()
        label = call.label or call.entry
        self.status_bar.set_status(f"运行中：{label}")
        for page in self.pages.values():
            try:
                page.refresh_running()
            except Exception:  # noqa: BLE001
                pass
        self.mark("run_start", entry=call.entry, label=label, argv=call.argv)

    def track_proc(self, proc) -> None:
        self.proc = proc
        self.mark("child_spawn", pid=getattr(proc, "pid", None), entry=self.current_call.entry if self.current_call else "")

    def record_line(self, line: str) -> None:
        self.line_count += 1
        self.last_line = line
        if self.first_line_ms is None and self.run_start_mono:
            self.first_line_ms = round((time.monotonic() - self.run_start_mono) * 1000, 1)
            self.mark("first_line", ms=self.first_line_ms, text=line[:120])
        if self.line_count % 20 == 0:
            self.mark("run_line", n=self.line_count, text=line[:120])

    def finish_run(self, call: EB.EngineCall, code: int) -> None:
        duration = round(time.monotonic() - (self.run_start_mono or time.monotonic()), 2)
        self.running = False
        self.proc = None
        label = call.label or call.entry
        cancelled = self.cancel_requested
        self.status_bar.set_status("已取消" if cancelled else ("就绪" if code == 0 else f"结束（退出码 {code}）"))
        for page in self.pages.values():
            try:
                page.refresh_running()
            except Exception:  # noqa: BLE001
                pass
        self.mark("run_done", entry=call.entry, label=label, code=code, lines=self.line_count,
                  duration_s=duration, first_line_ms=self.first_line_ms, cancelled=cancelled,
                  last_line=self.last_line[-200:])
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def stop_run(self) -> None:
        if not self.running:
            return
        self.cancel_requested = True
        self.mark("cancel_requested", pid=getattr(self.proc, "pid", None),
                  entry=self.current_call.entry if self.current_call else "")
        self.status_bar.set_status("正在停止…")
        if self.proc is not None:
            EB.kill_tree(self.proc)
        else:
            self.mark("cancel_no_proc")

    # ── 文件选择 / 剪贴板 / 打开 ────────────────────────────────────────
    def browse_field(self, ref, dialog_title: str, kind: str, key: str,
                     store_prefix: str = "recent") -> None:
        if ref is None:
            return
        if self.running:
            self.toast("运行中不能打开文件对话框")
            return
        self.mark("picker_open_begin", kind=kind, key=key)
        self.page.run_task(self._browse_task, ref, dialog_title, kind, key, store_prefix)

    async def _browse_task(self, ref, dialog_title: str, kind: str, key: str, store_prefix: str) -> None:
        try:
            if self._picker is None:
                self.mark("picker_error", error="file picker 未初始化")
                return
            if kind == "dir":
                value = await self._picker.get_directory_path(dialog_title=dialog_title)
            else:
                files = await self._picker.pick_files(dialog_title=dialog_title, allow_multiple=False)
                value = files[0].path if files else None
            self.mark("picker_result", kind=kind, key=key, value=value or "")
            if not value:
                return
            ref[0].value = value
            try:
                ref[0].update()
            except Exception:  # noqa: BLE001
                pass
            if store_prefix == "paths":
                self.store.set(f"paths.{key}", value)
                if key == "engine_override":
                    self.store.set("engine_override", value)
                    os.environ["P2_ENGINE"] = value
                    self.refresh_engine_status()
            else:
                self.store.set_recent(key, value)
            self.persist()
        except Exception as exc:  # noqa: BLE001
            self.mark("picker_error", error=f"{type(exc).__name__}: {exc}")

    def open_path(self, value: str) -> None:
        path = (value or "").strip()
        if not path or not Path(path).exists():
            self.toast("没有可打开的产物")
            return
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open", path])
            self.mark("open_artifact", path=path)
        except OSError as exc:
            self.toast(f"打开失败：{exc}")

    def copy_text(self, value: str) -> None:
        text = (value or "").strip()
        if not text:
            self.toast("没有可复制的路径")
            return
        try:
            if os.name == "nt":
                subprocess.run(["clip"], input=text.encode("utf-16le"),
                               creationflags=CREATE_NO_WINDOW, timeout=5, check=False)
            else:
                subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=5, check=False)
            self.toast("已复制路径")
            self.mark("copy_path", path=text)
        except (OSError, subprocess.SubprocessError):
            self.toast("复制失败")

    # ── 事件 ────────────────────────────────────────────────────────────
    def on_resize(self, e) -> None:
        try:
            width = int(self.page.width or 0)
            height = int(self.page.height or 0)
        except (TypeError, ValueError):
            width = height = 0
        self.mark("resize", page_width=width, page_height=height)

    def on_window_event(self, e) -> None:
        if getattr(e, "type", None) == ft.WindowEventType.CLOSE:
            self._close_requested = True
            self.mark("close_requested")
            if self.client_hwnd:
                try:
                    self.geometry = winplat.window_logical_rect(self.client_hwnd, self.system_dpi)
                    if self.geometry:
                        self.store.set("window.left", self.geometry["left"])
                        self.store.set("window.top", self.geometry["top"])
                        self.mark("geometry_saved", **self.geometry)
                except Exception as exc:  # noqa: BLE001
                    self.mark("geometry_error", error=f"{type(exc).__name__}: {exc}")
            if self.tray is not None:
                try:
                    self.tray.stop()
                except Exception:  # noqa: BLE001
                    pass
            self.save_settings()
            self.page.run_task(self._destroy_window)

    async def _destroy_window(self) -> None:
        await asyncio.sleep(0.05)
        try:
            await self.page.window.destroy()
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "session closed" in text.lower():
                # 关窗后客户端先关 session，此时 destroy 抛错无害（进程随之退出）。
                self.mark("destroy_skipped", reason=text)
            else:
                self.mark("destroy_error", error=text)

    # ── 状态落盘 / 自动化 ───────────────────────────────────────────────
    def write_state(self) -> None:
        try:
            window_w = int(self.page.window.width or 0)
            window_h = int(self.page.window.height or 0)
        except (TypeError, ValueError):
            window_w = window_h = 0
        self.mark(
            "state",
            page_width=round(self.page.width or 0, 2),
            page_height=round(self.page.height or 0, 2),
            window_w=window_w,
            window_h=window_h,
            dpi_design=self.dpi,
            dpi_system=self.system_dpi,
            dpi_aware=self.dpi_aware,
            zoom=self.zoom,
            zoom_requested=self.zoom_requested,
            max_zoom=self.max_zoom,
            theme=self.theme,
            platform=str(self.page.platform),
            pages=len(self.pages),
            current_page=self.current_page_key,
            settings_path=str(self.store.path),
            settings_exists=self.settings_exists,
            engine=self.engine_status.to_dict(),
            hwnd=self.client_hwnd,
            titlebar=self.titlebar_info,
            tray={"started": bool(getattr(self.tray, "started", False))} if self.tray else None,
            geometry=self.geometry,
            page_switches=self.page_switches[-10:],
            page_switch_avg_ms=(round(sum(self.page_switches) / len(self.page_switches), 1)
                                if self.page_switches else None),
            p3_bridged=P3_BRIDGED,
            flet=FLET_VERSION,
            python=self.python_version,
        )

    async def after_boot(self) -> None:
        try:
            await self.page.wait_until_visible()
        except Exception:  # noqa: BLE001
            pass
        self.mark("visible")
        self.write_state()
        await self._wake_platform()
        await self._platform_hooks()
        self.write_state()
        self.mark("engine_status", **self.engine_status.to_dict())
        page_key = _env_str("P2_PAGE")
        if page_key and page_key in self.pages:
            self.goto(page_key)
        await self._apply_demo_hooks()
        if _env_str("P2_ENVCHECK") in ("1", "true", "yes"):
            settings_page = self.pages.get("settings")
            if settings_page is not None and hasattr(settings_page, "_envcheck_task"):
                self.page.run_task(settings_page._envcheck_task)
        bib_query = _env_str("P2_BIB_QUERY")
        if bib_query:
            self.page.run_task(self._bib_query_probe, bib_query)
        bib_query_ui = _env_str("P2_BIB_QUERY_UI")
        if bib_query_ui:
            bib_page = self.pages.get("bib")
            if bib_page is not None:
                self.goto("bib")
                bib_page._switch("query")
                if bib_page.query_field is not None:
                    bib_page.query_field.value = bib_query_ui
                self.page.run_task(bib_page._run_query)
        autorun = _env_str("P2_AUTORUN")
        if autorun:
            await self._autorun(autorun)
        selftest = _env_str("P2_SELFTEST")
        if selftest == "theme":
            await asyncio.sleep(0.3)
            self.set_theme("dark")
            await asyncio.sleep(0.4)
            self.mark("selftest_done", case="theme", theme=self.theme)
        elif selftest == "zoom":
            await asyncio.sleep(0.3)
            self.set_zoom(1.25)
            await asyncio.sleep(0.4)
            self.mark("selftest_done", case="zoom", zoom=self.zoom)
        picker = _env_str("P2_OPEN_PICKER")
        if picker:
            await asyncio.sleep(0.5)
            self.page.run_task(self._open_picker, picker)
        auto_close = _env_float("P2_AUTO_CLOSE_MS", None)
        if auto_close:
            await asyncio.sleep(max(0.0, auto_close / 1000.0))
            self.mark("auto_close_begin")
            try:
                await self.page.window.close()
            except Exception as exc:  # noqa: BLE001
                self.mark("auto_close_error", error=str(exc))

    # ── P3 平台补齐：客户端窗口旁路 / 标题栏 / 拖放探测 / 托盘 / 几何 ────
    async def _bib_query_probe(self, title: str) -> None:
        """证据钩子：直接打一次只读 bibliographic query 并记录结果（不写库）。"""
        try:
            call = EB.bibdb_query_call(title=title, limit=3)
            result = await asyncio.to_thread(EB.read_json_command, call)
            payload = result.get("json") or {}
            hits = payload.get("hits") or []
            self.mark("bib_query", ok=bool(result.get("ok")), title=title,
                      code=result.get("code"), count=payload.get("count"),
                      ambiguous=payload.get("ambiguous"),
                      keys=[str(h.get("key")) for h in hits][:5],
                      error=str(result.get("error") or ""))
        except Exception as exc:  # noqa: BLE001
            self.mark("bib_query", ok=False, title=title, error=f"{type(exc).__name__}: {exc}")

    async def _wake_platform(self) -> None:
        """首帧可见后接管外部窗口能力（跨进程，不侵入 Flutter 内部）。"""
        self.client_hwnd = winplat.find_client_window(APP_TITLE) or 0
        if not self.client_hwnd:
            self.mark("client_window", ok=False, error="未找到 Flutter 客户端窗口")
            return
        info = winplat.window_info(self.client_hwnd)
        self.mark("client_window", ok=True, **info)
        self.titlebar_info = winplat.set_dark_titlebar(self.client_hwnd, self.theme == "dark")
        self.mark("titlebar", theme=self.theme, **self.titlebar_info)
        if _env_str("P3_DRAG_PROBE") in ("1", "true", "yes"):
            self.mark("drag_register", **winplat.accept_drag_files(self.client_hwnd, True))

    async def _platform_hooks(self) -> None:
        tray_want = _env_str("P3_TRAY") in ("1", "true", "yes")
        tray_test = _env_str("P3_TRAY_TEST")
        if tray_want or tray_test:
            from services.tray import TrayService

            self.tray = TrayService(self)
            self.mark("tray", **self.tray.start())
        if tray_test and self.tray is not None:
            for action in [a.strip() for a in tray_test.split(",") if a.strip()]:
                await asyncio.sleep(0.45)
                result = self.tray.invoke(action)
                self.mark("tray_invoke", action=action, ok=result.get("ok"),
                          error=result.get("error", ""))
                await asyncio.sleep(0.25)
                info = winplat.window_info(self.client_hwnd or 0)
                self.mark("tray_window_state", action=action, visible=info.get("visible"),
                          hwnd=info.get("hwnd"))
                if action == "show":
                    winplat.show_window(self.client_hwnd or 0, True, foreground=True)
        if _env_str("P3_GEOMETRY_TEST") in ("1", "true", "yes"):
            await asyncio.sleep(0.3)
            await self._geometry_roundtrip()
        if _env_str("P3_TITLEBAR_PROBE") in ("1", "true", "yes"):
            for theme in ("dark", "light"):
                if theme != self.theme and not self.running:
                    self.set_theme(theme)
                    await asyncio.sleep(0.4)
                self.mark("titlebar_probe", theme=theme,
                          **winplat.set_dark_titlebar(self.client_hwnd or 0, theme == "dark"))

    async def _geometry_roundtrip(self) -> None:
        """移动窗口 → 记录并落盘几何 → 越界钳制验证（多屏记忆的证据）。"""
        before = winplat.window_info(self.client_hwnd or 0)
        self.mark("geometry_before", rect_px=before.get("rect_px"))
        mon = winplat._monitor_work_rect(hwnd=self.client_hwnd or 0) or {}
        work = mon.get("work")
        if not before.get("rect_px") or not work:
            self.mark("geometry_skip", error="no rect/monitor")
            return
        scale = self.system_dpi / 96.0
        self.page.window.left = int(round((work["right"] - 260) / scale))
        self.page.window.top = int(round((work["top"] + 60) / scale))
        self.page.update()
        await asyncio.sleep(0.7)
        after = winplat.window_info(self.client_hwnd or 0)
        self.geometry = winplat.window_logical_rect(self.client_hwnd or 0, self.system_dpi)
        if self.geometry:
            self.store.set("window.left", self.geometry["left"])
            self.store.set("window.top", self.geometry["top"])
            self.persist()
        self.mark("geometry_moved", rect_px=after.get("rect_px"), logical=self.geometry)
        x, y = winplat.clamp_logical_position(99999, 99999, self.window_w, self.window_h,
                                              self.system_dpi)
        self.mark("geometry_clamp", requested={"left": 99999, "top": 99999},
                  clamped={"left": x, "top": y}, monitor_work=work)
        self.page.window.left = x
        self.page.window.top = y
        self.page.update()

    async def _apply_demo_hooks(self) -> None:
        source = _env_str("P2_DEMO_SINGLE_SOURCE")
        out = _env_str("P2_DEMO_SINGLE_OUT")
        bib = _env_str("P2_DEMO_BIB")
        proof = _env_str("P2_DEMO_PROOF_TEX")
        issue = _env_str("P2_DEMO_ISSUE")
        toc = _env_str("P2_DEMO_TOC")
        if source or out or bib or proof:
            page = self.pages.get("single")
            if page:
                page.prefill(source=source, out=out, bib=bib, proof_tex=proof,
                             flow=_env_str("P2_DEMO_FLOW", "convert"))
        if issue or toc:
            page = self.pages.get("issue")
            if page:
                page.prefill(issue_dir=issue, toc_report=toc)

    async def _open_picker(self, kind: str) -> None:
        try:
            self.mark("picker_open_begin", kind=kind, key="env")
            if self._picker is None:
                self.mark("picker_error", error="file picker 未初始化")
                return
            if kind == "dir":
                value = await self._picker.get_directory_path(dialog_title="选择稿件所在目录")
            else:
                files = await self._picker.pick_files(dialog_title="选择稿件", allow_multiple=False)
                value = files[0].path if files else None
            self.mark("picker_result", kind=kind, value=value or "")
        except Exception as exc:  # noqa: BLE001
            self.mark("picker_error", error=f"{type(exc).__name__}: {exc}")

    async def _autorun(self, target: str) -> None:
        await asyncio.sleep(0.5)
        self.mark("autorun_begin", target=target)
        if target in ("convert", "checkbib", "proofcheck"):
            page = self.pages.get("single")
            if page is None:
                self.mark("autorun_error", error="no single page")
                return
            page.prefill(flow=target,
                         source=_env_str("P2_DEMO_SINGLE_SOURCE"),
                         out=_env_str("P2_DEMO_SINGLE_OUT"),
                         bib=_env_str("P2_DEMO_BIB"),
                         proof_tex=_env_str("P2_DEMO_PROOF_TEX"))
            page.run_demo()
        elif target in ("infocheck", "toc", "compileall", "merge", "cleantemp", "dirpush"):
            page = self.pages.get("issue")
            if page is None:
                self.mark("autorun_error", error="no issue page")
                return
            page.prefill(issue_dir=_env_str("P2_DEMO_ISSUE"), toc_report=_env_str("P2_DEMO_TOC"))
            page.select_step(target)
            page.run_step_demo(target)
        elif target == "all":
            page = self.pages.get("issue")
            if page is None:
                return
            page.prefill(issue_dir=_env_str("P2_DEMO_ISSUE"), toc_report=_env_str("P2_DEMO_TOC"))
            page.run_all_demo()
        await self._wait_run_finished()
        cancel_after = _env_float("P2_CANCEL_MS", None)
        self.mark("autorun_end", target=target, code_seen=self.line_count)

    async def _wait_run_finished(self) -> None:
        cancel_ms = _env_float("P2_CANCEL_MS", None)
        cancelled_once = False
        for _ in range(1200):
            if self.run_seen and not self.running:
                return
            if cancel_ms and self.running and not cancelled_once and self.proc is not None:
                await asyncio.sleep(max(0.0, cancel_ms / 1000.0))
                self.stop_run()
                cancelled_once = True
            await asyncio.sleep(0.1)

    # ── 启动 ────────────────────────────────────────────────────────────
    def run_main(self) -> None:
        self._picker = None
        try:
            self._picker = ft.FilePicker()
        except Exception as exc:  # noqa: BLE001
            self.mark("picker_init_error", error=str(exc))


def main(page: ft.Page) -> None:
    page.title = APP_TITLE
    app = App(page)
    app.run_main()
    app.boot()
    page.update()
    app.mark("first_update_sent", theme=app.theme)
    page.run_task(app.after_boot)


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP)
