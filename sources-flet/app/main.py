"""编辑部工具箱 · Flet 单壳（B-246c P2 / P3）。

一个窗口 = 一个应用：左侧分组导航 + 页面区 + 状态栏。
页面只做「取值 → 拼参数 → 显示日志」，判定全部在 Engine（见 app/services/engine_bridge.py）。

P2 自动化钩子（证据脚本用，生产环境不设即无副作用）：
  P2_MARKER / P2_SETTINGS / P2_THEME(light|dark) / P2_PAGE / P2_DPI(96|120|144|192)
  P2_ZOOM / P2_WINSIZE(WxH) / P2_AUTO_CLOSE_MS / P2_AUTORUN / P2_CANCEL_MS / P2_OPEN_PICKER(dir|file)
  P2_BIB_VIEW(view) / P2_SINGLE_FLOW(convert|proofcheck) / P2_AUTORUN_LEGACY=1
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
from services import startup_log as SLOG
from services import version_info as VERSION
from services import winplat
from services.settings_store import SettingsStore, normalize_button_palette
from ui import brand
from ui import components as C
from ui import pages as P
from ui import search_index as SI
from ui import settings_shell as SHELL
from ui import ia_map as IA
from ui.tokens import (BASE_DPI, BASE_H, BASE_W, BREAK, DARK, FONT_FAMILY, LIGHT,
                       SIDEBAR_W, TONE_TAG, TOOLBAR_H, ZOOM_MAX, ZOOM_MIN, Tokens,
                       palette_remap)

APP_TITLE = "编辑部工具箱"
# 兜底值；正常显示走 services.version_info（引擎 __version__ 单一来源）。
APP_VERSION = VERSION.FALLBACK_VERSION
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


def _env_int(name: str, default: int = 0) -> int:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return int(float(raw))
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


#: 批 112 S2「换肤不重建」：要原地改写的颜色属性（背景 / 前景两张映射表分工）。
_RETINT_BG_ATTRS = ("bgcolor", "fill_color", "border_color", "focused_border_color",
                    "hover_color", "icon_color", "active_color", "thumb_color",
                    "track_color", "inactive_track_color", "cursor_color",
                    "selection_color", "check_color")
_RETINT_FG_ATTRS = ("color",)
_RETINT_CHILD_ATTRS = ("content", "controls", "actions", "title", "subtitle", "leading",
                       "trailing", "children", "label", "rows", "columns", "cells")
_RETINT_BORDER_SIDES = ("top", "right", "bottom", "left")


def _retint_attr(obj, name: str):
    """控件没设过的属性在 flet 1.0.5 下抛 AttributeError；统一降级为 None。"""
    try:
        return getattr(obj, name)
    except AttributeError:
        return None


def _retint_walkable(obj) -> bool:
    return (isinstance(obj, ft.Control) or hasattr(obj, "controls")
            or type(obj).__module__.startswith("flet"))



def _retint_border_side(side, mapping: dict[str, str]):
    """`BorderSide` → 目标主题色；无需改写返回 None（宽度/风格/对齐保持原值）。"""
    if not isinstance(side, ft.BorderSide):
        return None
    if not isinstance(side.color, str) or side.color not in mapping:
        return None
    return ft.BorderSide(width=side.width, color=mapping[side.color],
                         stroke_align=side.stroke_align, style=side.style)


def _retint_input_border(border, mapping: dict[str, str]):
    """`InputBorder`（Outline / Underline）换肤色：只改 `side`，保留圆角与间隙。

    批 113 E 起所有 TextField 都用 `border=ControlStateValue[InputBorder]`
    （弃用 border_color/focused_border_color 后），所以原地换肤也必须能重染它们，
    否则切主题后输入框边框会停在上一个主题的颜色。返回 None 表示无需替换。
    """
    if not isinstance(border, (ft.OutlineInputBorder, ft.UnderlineInputBorder)):
        return None
    side = _retint_border_side(getattr(border, "side", None), mapping)
    if side is None:
        return None
    kwargs: dict = {"side": side}
    for attr in ("border_radius", "gap_padding"):
        value = getattr(border, attr, None)
        if value is not None:
            kwargs[attr] = value
    return type(border)(**kwargs)

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
        # 批 114 P2 v2：主按钮 C（石板蓝，默认）/ D（石墨）；旧配置缺键 → c。
        self.button_palette = normalize_button_palette(self.store.get("button_palette"))
        self.sidebar_open = True
        self._sidebar_compact_cache: bool | None = None
        self.t = Tokens(self.theme, self.zoom, self.dpi, self.button_palette)
        self.running = False
        self.run_seen = False
        self.proc = None
        self.current_call = None
        self.current_page_key = ""
        # 批 114 导航（用户 m03256）：唯一路由状态 + accordion 展开状态。
        self.current_route = ""
        self.nav_open: dict = {"page": "", "group": ""}
        self.pages: dict[str, P.BasePage] = {}
        self.engine_status = EB.engine_status()
        self.last_line = ""
        self.line_count = 0
        self.first_line_ms = None
        self.run_start_mono = None
        self.cancel_requested = False
        self._marker_path = Path(_env_str("P2_MARKER")) if _env_str("P2_MARKER") else None
        self._marker_lock = asyncio.Lock()
        self._version_info = VERSION.resolve(self.engine_status)
        self.app_version = self._version_info["version"]
        if self._version_info["source"] == "fallback":
            self.mark("version_fallback", engine_version=self._version_info["engine_version"],
                      fallback=self.app_version)
        self._close_requested = False
        # 批 114：顶栏搜索（真实索引，见 ui/search_index.py）。焦点用于全局 ↑↓ 选择。
        self._search_query = ""
        self._search_matches: list = []
        self._search_selected = 0
        self._search_focus = False
        self._search_slot = None
        # 注意：不要在这里存 browse task 句柄 —— 会遮蔽下方同名 async 方法（GUI-04：
        # flet 1.0.5 的 run_task 只收 coroutine function）。句柄需要的话改名，别复用方法名。
        self.prefill_hooks: dict = {}
        self._own_size = _parse_size(_env_str("P2_WINSIZE"))
        self.window_w, self.window_h = self._own_size or self._window_size()
        # ── P3 平台补齐状态 ─────────────────────────────────────────
        self.client_hwnd = 0
        self.tray = None
        self.tray_enabled = bool(self.store.get("tray_enabled")) and winplat.IS_WIN
        self._quitting = False
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

    @staticmethod
    def _flet_theme(palette, t: Tokens | None = None) -> ft.Theme:
        """Flet 主题是 palette 的投影，不再用 INDIGO 种子色另开第二真源。

        `font_family` 只收单个族名（FONT_FAMILY=None 即交给系统默认）；输入框默认字号
        用 Material 3 的 bodyLarge 统一成正文 14（批 112 GUI-07）。
        """
        tokens = t or Tokens()
        # 批 112 GUI-09：按钮/输入框等「客户端自绘」文本走 Material TextTheme 默认值，
        # 不乘 Tokens.k → 与非按钮文本（全部 t.font()×k）在缩放/DPI 偏离 1.0 时比例漂移。
        # 这里把 TextTheme 全量投影到 tokens 字号（×k），按钮字与正文同源、同缩放。
        def _ts(key: str, weight: ft.FontWeight | None = None,
                color: str | None = None) -> ft.TextStyle:
            return ft.TextStyle(size=tokens.font(key), weight=weight, color=color)

        text_color = palette.text

        label_weight = ft.FontWeight.W_500
        return ft.Theme(
            color_scheme=ft.ColorScheme(
                primary=palette.accent, on_primary=palette.on_accent,
                secondary=palette.accent_hover, on_secondary=palette.on_accent,
                surface=palette.surface, on_surface=palette.text,
                error=palette.error, on_error=palette.on_accent,
                outline=palette.border,
            ),
            font_family=FONT_FAMILY,
            # 批 112 A-03：复选框走主题投影（默认 1px 黑框与全站胶囊/紫色语言不一致）。
            # 勾选态才填 accent；未选保持透明（只在 hover 时给淡 tint）。
            # 旧写法 fill_color=palette.accent 会让「未勾选」也变实心色块（用户 m02238 复核发现）。
            checkbox_theme=ft.CheckboxTheme(
                fill_color={
                    ft.ControlState.DEFAULT: "transparent",
                    ft.ControlState.HOVERED: palette.accent_soft,
                    ft.ControlState.SELECTED: palette.accent,
                    ft.ControlState.PRESSED: palette.accent_hover,
                    ft.ControlState.DISABLED: "transparent",
                },
                check_color=palette.on_accent,
                border_side=ft.BorderSide(tokens.px(1), palette.border_strong),
                shape=ft.RoundedRectangleBorder(radius=tokens.px(4)),
            ),
            # 批 115 P3-C（审计 §2.4 gap3）：开关 / 滑杆 / 单选默认吃 Material 蓝，
            # 与石板蓝强调色不一致。四类控件主题全部按 Palette 投影（light + dark 各一份，
            # 都放在 _apply_page_theme() 里重放）；不设 Material 种子色，也不用 M3
            # 第二真源（测试 test_b109_w45 会盯这两条）。
            # 开关四态与 C.switch 同值（关闭态轨道 vs 纸面 ≥3:1，见 components.switch）。
            switch_theme=ft.SwitchTheme(
                thumb_color={
                    ft.ControlState.DEFAULT: palette.surface,
                    ft.ControlState.SELECTED: palette.on_accent,
                    ft.ControlState.HOVERED: palette.surface,
                    ft.ControlState.DISABLED: palette.surface_3,
                },
                track_color={
                    ft.ControlState.DEFAULT: palette.text_muted,
                    ft.ControlState.SELECTED: palette.accent,
                    ft.ControlState.HOVERED: palette.text_muted,
                    ft.ControlState.DISABLED: palette.border,
                },
                track_outline_color={
                    ft.ControlState.DEFAULT: palette.text_muted,
                    ft.ControlState.SELECTED: palette.accent,
                    ft.ControlState.DISABLED: palette.border,
                },
                overlay_color={
                    ft.ControlState.DEFAULT: "transparent",
                    ft.ControlState.HOVERED: palette.accent_soft,
                    ft.ControlState.SELECTED: palette.accent_soft,
                    ft.ControlState.PRESSED: palette.accent_soft,
                },
            ),
            # 缩放缓调节：活动轨/滑块 = accent，未活动轨 = border，悬浮晕 = accent_soft。
            slider_theme=ft.SliderTheme(
                active_track_color=palette.accent,
                inactive_track_color=palette.border,
                thumb_color=palette.accent,
                overlay_color=palette.accent_soft,
                value_indicator_color=palette.accent,
                active_tick_mark_color=palette.accent,
                inactive_tick_mark_color=palette.border_strong,
                secondary_active_track_color=palette.accent_hover,
            ),
            # 单选（按钮配色 C/D）：选中环 = accent，未选 = border_strong，悬浮晕 = accent_soft。
            radio_theme=ft.RadioTheme(
                fill_color={
                    ft.ControlState.DEFAULT: palette.border_strong,
                    ft.ControlState.SELECTED: palette.accent,
                    ft.ControlState.HOVERED: palette.accent,
                    ft.ControlState.DISABLED: palette.border,
                },
                overlay_color={
                    ft.ControlState.DEFAULT: "transparent",
                    ft.ControlState.HOVERED: palette.accent_soft,
                    ft.ControlState.SELECTED: palette.accent_soft,
                },
            ),
            # 批 112 GUI-11：非按钮文本槽位必须带显式颜色。TextTheme 只给 size 时 color 为
            # None，TextField 的输入文字会落到客户端默认色（深色主题的浅色）而背景是浅色
            # → 白字白底不可读。label_* 不带色，按钮自己的 color/style 决定前景。
            text_theme=ft.TextTheme(
                display_large=_ts("display", color=text_color),
                display_medium=_ts("display", color=text_color),
                display_small=_ts("h1", color=text_color),
                headline_large=_ts("h1", color=text_color),
                headline_medium=_ts("h2", color=text_color),
                headline_small=_ts("h3", color=text_color),
                title_large=_ts("h2", color=text_color),
                title_medium=_ts("h3", color=text_color),
                title_small=_ts("body", color=text_color),
                body_large=_ts("body", color=text_color),
                body_medium=_ts("small", color=text_color),
                body_small=_ts("caption", color=text_color),
                label_large=_ts("body", label_weight), label_medium=_ts("small"),
                label_small=_ts("caption"),
            ),
        )

    def _apply_page_theme(self) -> None:
        """把当前 Tokens 投影到 page.theme / page.dark_theme。

        必须在 `rebuild_shell()` 里重放（缩放/DPI 变化会换 Tokens）：flet 客户端自绘文本
        （下拉菜单项、对话框、Tooltip 等）读的是 page.theme 的 TextTheme，不重放就会停在
        旧 k 上；而控件自己的 `text_size` 已乘新 k → 同屏两套字号（GUI-10 根因）。
        """
        page = self.page
        page.theme_mode = ft.ThemeMode.DARK if self.theme == "dark" else ft.ThemeMode.LIGHT
        page.theme = self._flet_theme(LIGHT, self.t)
        page.dark_theme = self._flet_theme(DARK, self.t)

    def _configure_window(self) -> None:
        page = self.page
        page.title = APP_TITLE
        self._apply_page_theme()
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
        # 窗口图标：品牌 ICO 存在才设，缺失/打包异常静默降级（不弹错误页）
        if brand.apply_window_icon(page):
            self.mark("brand_icon", source="window")
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

    # ── 托盘（仅 Windows；设置项 tray_enabled，默认关）──────────────────
    @property
    def tray_active(self) -> bool:
        return bool(self.tray is not None and getattr(self.tray, "started", False))

    def _start_tray(self, *, force: bool = False, reason: str = "setting") -> dict:
        """启动托盘；失败自动回退（关闭窗口仍退出），返回 start() 的结果。"""
        if not winplat.IS_WIN:
            return {"started": False, "error": "托盘仅 Windows 可用"}
        if self.tray_active:
            return {"started": True, "error": ""}
        from services.tray import TrayService

        service = TrayService(self)
        result = service.start()
        self.mark("tray", **result, reason=reason, forced=force)
        if result.get("started"):
            self.tray = service
            return result
        self.tray = None
        if not force:
            self.tray_enabled = False
            self.store.set("tray_enabled", False)
            self.persist()
            self.toast("托盘启动失败，已回退为关闭窗口即退出（可稍后重试）。")
        return result

    def set_tray_enabled(self, value: bool) -> None:
        """设置页开关：立即生效并落盘；非 Windows 不接受开启。"""
        if not winplat.IS_WIN:
            self.tray_enabled = False
            self.store.set("tray_enabled", False)
            self.persist()
            self.toast("托盘是 Windows 功能，当前平台不可用。")
            return
        enabled = bool(value)
        self.tray_enabled = enabled
        self.store.set("tray_enabled", enabled)
        self.persist()
        self.mark("tray_pref", enabled=enabled)
        if enabled:
            if self._start_tray(reason="setting").get("started"):
                self.toast("已启用：关闭窗口时最小化到托盘，从托盘菜单退出。")
            return
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception as exc:  # noqa: BLE001
                self.mark("tray_stop_error", error=f"{type(exc).__name__}: {exc}")
            self.tray = None
        self.toast("已关闭托盘：关闭窗口即退出。")

    def _hide_to_tray(self) -> None:
        self.mark("close_to_tray")
        try:
            self.tray.hide()
        except Exception as exc:  # noqa: BLE001 - 隐藏失败退回正常退出
            self.mark("tray_hide_error", error=f"{type(exc).__name__}: {exc}")
            self.tray = None
            self.save_settings()
            self.page.run_task(self._destroy_window)

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
        if event in SLOG.LIFECYCLE_EVENTS:
            SLOG.log(event, **fields)

    # ── 外壳 ────────────────────────────────────────────────────────────
    def boot(self) -> None:
        self.mark("process_start", title=APP_TITLE, argv=sys.argv[1:])
        self.rebuild_shell()
        SHELL.install_hotkeys(self)
        cycle_count = _env_int("P2_THEME_CYCLES", 0)
        if cycle_count > 0:
            self.page.run_task(self._theme_cycles, cycle_count)

    # ── 设计像素 / 断点 ────────────────────────────────────────────────
    def _design_width(self) -> float:
        """当前内容宽换算回设计像素（1240 基准）；<720 收 56 图标条。"""
        width = 0.0
        try:
            width = float(self.page.width or 0)
        except (TypeError, ValueError):
            width = 0.0
        if width <= 0:
            width = float(self.window_w or 0)
        return width / max(self.t.k, 1e-6)

    def _sidebar_compact(self) -> bool:
        return self._design_width() < BREAK["rail"]

    def _page_label(self, key: str) -> str:
        for entry in IA.nav_parent_entries():
            if entry["key"] == key:
                return str(entry["label"])
        return APP_TITLE

    # ── 工具栏（52 高，横跨全宽）───────────────────────────────────────
    def _toolbar_spec(self):
        page = self.pages.get(self.current_page_key)
        labels = IA.route_labels(self.current_route or self.current_page_key)
        base = getattr(page, "TOOLBAR_TITLE", "") or labels["page_label"]
        title = base
        default_view = IA.page_defaults().get(labels["page"], "")
        if labels["view"] and labels["view"] != default_view:
            # 二级视图页：顶栏同时给出父页与子项，避免「只有子项」找不到归属。
            title = f"{base} · {labels['view_label']}"
        meta = ""
        reader = getattr(page, "toolbar_meta", None)
        if callable(reader):
            try:
                meta = str(reader() or "")
            except Exception as exc:  # noqa: BLE001 - 页头 meta 失败不得拖垮外壳
                self.mark("toolbar_meta_error", page=self.current_page_key,
                          error=f"{type(exc).__name__}: {exc}")
        if not meta:
            meta = "引擎就绪 · 本地只读" if self.engine_status.ok else "引擎缺失 · 只读"
        action = None
        reader = getattr(page, "toolbar_action", None)
        if callable(reader):
            try:
                action = reader()
            except Exception as exc:  # noqa: BLE001
                self.mark("toolbar_action_error", page=self.current_page_key,
                          error=f"{type(exc).__name__}: {exc}")
        return title, meta, action

    def _ensure_toolbar_parts(self) -> None:
        """顶部栏控件只建一次：导航只改文案/动作，避免每次重建 TextField（perf 回归）。"""
        if getattr(self, "_toolbar_row", None) is not None:
            return
        t = self.t
        self._toolbar_title = C.text("", t, role="page-title")
        self._toolbar_meta = C.text("", t, role="meta", color=t.palette.text_muted)
        # 批 114：搜索输入不再铺灰（surface_2），改为纸色底 + 发丝描边 + 放大镜。
        self._toolbar_search = C.search_field(
            t, value=str(getattr(self, "_search_query", "") or ""),
            hint_text="搜索页面、命令或最近记录",
            width=t.px(236),
            on_submit=self._on_search_submit,
            on_change=self._on_search_change,
        )
        self._toolbar_search.on_focus = lambda e: self._set_search_focus(True)
        self._toolbar_search.on_blur = lambda e: self._set_search_focus(False)
        # 图标按钮与搜索框同高，工具栏不出现 26 vs 28 的 2px 高差（用户 m02816）。
        self._toolbar_menu = C.icon_button("MENU", "收起 / 展开侧栏", self.toggle_sidebar, t,
                                           color=t.palette.text_muted, height=t.control_h())
        self._toolbar_tune = C.icon_button("TUNE", "外观与缩放",
                                           lambda e: self.goto("settings"), t,
                                           color=t.palette.text_muted, height=t.control_h())
        self._toolbar_action_slot = ft.Container()
        self._toolbar_action_key = None
        self._toolbar_action_btn = None
        # 用户 m02855：搜索框 + 右侧「外观」图标一起放进白色圆角胶囊，
        # 在 sidebar 灰工具栏上也是一块完整的白卡搜索区（搜索框本身不再描边）。
        self._toolbar_search_card = ft.Container(
            height=t.px(32),
            bgcolor=t.palette.surface,
            border=C.box(t),
            border_radius=t.radius("pill"),
            padding=C._pad(t, 12, 0, 4, 0),
            content=ft.Row(
                spacing=t.sp("half"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[self._toolbar_search, self._toolbar_tune]),
        )
        self._toolbar_row = ft.Row(
            spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[self._toolbar_menu, self._toolbar_title, self._toolbar_meta,
                      ft.Container(expand=True), self._toolbar_search_card,
                      self._toolbar_action_slot])
        self._toolbar_tokens = t
        self.toolbar_search = self._toolbar_search

    def _apply_toolbar_theme(self) -> None:
        """Tokens 变了（换主题/缩放/DPI）时只改样式，不重建控件。"""
        t = self.t
        title_size, title_weight = t.text_role("page-title")
        meta_size, meta_weight = t.text_role("meta")
        self._toolbar_title.size = t.font(title_size)
        self._toolbar_title.weight = C._w(t, title_weight)
        self._toolbar_title.color = t.palette.text
        self._toolbar_meta.size = t.font(meta_size)
        self._toolbar_meta.weight = C._w(t, meta_weight)
        self._toolbar_meta.color = t.palette.text_muted
        search = self._toolbar_search
        search.width = t.px(236)
        search.height = t.control_h()
        search.text_size = t.font("body")
        search.filled = False
        search.fill_color = None
        search.bgcolor = t.palette.surface
        # 描边交给外层白卡胶囊，输入框本身透明无框（用户 m02855）。
        search.border = None
        search.content_padding = C._pad(t, 8, 4, 8, 4)
        search.hint_style = ft.TextStyle(size=t.font("body"), color=t.palette.text_faint)
        search.text_style = ft.TextStyle(size=t.font("body"), color=t.palette.text)
        card = self._toolbar_search_card
        card.height = t.px(32)
        card.bgcolor = t.palette.surface
        card.border = C.box(t, color=(t.palette.accent if getattr(self, "_search_focus", False)
                                      else t.palette.border))
        card.border_radius = t.radius("pill")
        card.padding = C._pad(t, 12, 0, 4, 0)
        card.content.spacing = t.sp("half")
        self._toolbar_menu.icon_color = t.palette.text_muted
        self._toolbar_tune.icon_color = t.palette.text_muted
        self._toolbar_menu.height = t.control_h()
        self._toolbar_tune.height = t.control_h()
        self._toolbar_row.spacing = t.sp("1")

    def _toolbar_content(self) -> ft.Control:
        t = self.t
        title, meta, action = self._toolbar_spec()
        self._ensure_toolbar_parts()
        self._apply_toolbar_theme()
        self._toolbar_title.value = title
        self._toolbar_meta.value = meta
        if action:
            label, icon, handler = action
            key = (label, icon)
            fresh = self._toolbar_tokens is not t
            if key != self._toolbar_action_key or self._toolbar_action_btn is None or fresh:
                btn = C.primary_button(label, handler, t, icon=icon)
                self._toolbar_action_btn = btn
                self._toolbar_action_key = key
                self._toolbar_action_slot.content = btn
            else:
                self._toolbar_action_btn.on_click = handler
        else:
            self._toolbar_action_slot.content = None
            self._toolbar_action_key = None
            self._toolbar_action_btn = None
        self._toolbar_tokens = t
        self.toolbar_search = self._toolbar_search
        return self._toolbar_row

    def _toolbar(self) -> ft.Container:
        t = self.t
        self.toolbar = ft.Container(
            height=t.px(TOOLBAR_H), bgcolor=t.palette.sidebar,
            padding=C._pad(t, 12, 0, 12, 0), content=self._toolbar_content(),
        )
        return self.toolbar

    def _sync_toolbar(self) -> None:
        toolbar = getattr(self, "toolbar", None)
        if toolbar is not None:
            toolbar.content = self._toolbar_content()

    # ── 顶栏搜索：真实索引（页面 / 命令 / 最近记录）─────────────────────
    # 旧实现只存字符串 + 6 个页面别名 = 用户 m02696 说的「假搜索」。现在：
    # · 数据源见 ui/search_index.py（页面、命令、recent.* 路径、notes_repo）；
    # · 输入即时过滤并展示可点结果，Enter 执行第一条，↑↓ 由全局热键前置处理；
    # · 结果是真动作（goto / open_bib_view / issue step / 主题 / 引擎 / 文件记录），
    #   不存在「点一下只弹提示」的假结果。
    def _set_search_focus(self, value: bool) -> None:
        self._search_focus = bool(value)
        # 搜索胶囊是唯一描边层：聚焦时描边换成 accent，失焦恢复发丝边。
        card = getattr(self, "_toolbar_search_card", None)
        if card is None:
            return
        card.border = C.box(self.t, color=(self.t.palette.accent if value
                                           else self.t.palette.border))
        try:
            card.update()
        except Exception:  # noqa: BLE001
            pass

    def _search_entries(self) -> list:
        store = getattr(self, "store", None)
        data = getattr(store, "data", None)
        data = data if isinstance(data, dict) else {}
        bib_views = list(getattr(P.BibPage, "VIEWS", []) or [])
        issue_steps = [(step.get("key"), step.get("title"))
                       for step in (getattr(P.IssuePage, "STEPS", []) or [])]
        return SI.build_index(
            nav_tree=IA.NAV_TREE, bib_views=bib_views, issue_steps=issue_steps,
            recent=data.get("recent"), paths=data.get("paths"),
        )

    def _search_matches_now(self, query: str | None = None) -> list:
        text = (self._search_query if query is None else str(query or "")).strip()
        if not text:
            return []
        try:
            return SI.match_entries(self._search_entries(), text, limit=SI.DEFAULT_LIMIT)
        except Exception as exc:  # noqa: BLE001 索引坏掉不能拖垮顶栏
            self.mark("search_index_error", error=f"{type(exc).__name__}: {exc}")
            return []

    def _update_search_slot(self, slot=None) -> None:
        slot = slot if slot is not None else getattr(self, "_search_slot", None)
        if slot is None:
            return
        try:
            slot.update()
        except Exception:  # noqa: BLE001 构建期/未挂载
            pass

    def _search_panel_content(self, query: str, matches: list) -> ft.Column:
        t, p = self.t, self.t.palette
        icons = {"page": "APPS", "command": "BOLT", "recent": "HISTORY",
                 "path": "FOLDER_OPEN"}
        rows: list = []
        for index, entry in enumerate(matches):
            selected = index == getattr(self, "_search_selected", 0)
            rows.append(ft.Container(
                padding=C._pad(t, 8, 6, 8, 6),
                border_radius=t.radius("sm"),
                bgcolor=p.sel_tint if selected else None,
                on_click=lambda e, item=entry: self._run_search_entry(item),
                content=ft.Row(spacing=t.sp("1"),
                               vertical_alignment=ft.CrossAxisAlignment.CENTER,
                               controls=[
                    ft.Icon(C.ic(icons.get(entry.kind, "SEARCH"), "CIRCLE"),
                            size=t.px(14), color=p.text_muted),
                    ft.Column(spacing=0, expand=True, controls=[
                        C.text(entry.label, t, "small", p.text, max_lines=1,
                               overflow=ft.TextOverflow.ELLIPSIS),
                        *([C.text(entry.detail, t, "caption", p.text_faint, max_lines=1,
                                  overflow=ft.TextOverflow.ELLIPSIS)]
                          if entry.detail else []),
                    ]),
                    C.text(SI.KIND_LABELS.get(entry.kind, ""), t, "caption", p.text_faint),
                ]),
            ))
        header = ft.Row(spacing=t.sp("1"), controls=[
            C.text(f"搜索「{query}」", t, "caption", p.text_muted),
            ft.Container(expand=True),
            C.text(f"{len(matches)} 条", t, "caption", p.text_faint),
        ])
        body: list = rows or [C.text("没有匹配的页面、命令或最近记录。", t, "small", p.text_muted)]
        return ft.Column(spacing=t.px(2), controls=[
            header, *body, C.text(SI.SOURCE_NOTE, t, "caption", p.text_faint),
        ])

    def _refresh_search_panel(self) -> None:
        slot = getattr(self, "_search_slot", None)
        if slot is None:
            return
        query = str(getattr(self, "_search_query", "") or "").strip()
        if not query:
            self._search_matches = []
            self._search_selected = 0
            slot.visible = False
            slot.content = None
            self._update_search_slot(slot)
            return
        matches = self._search_matches_now(query)
        self._search_matches = matches
        if matches:
            self._search_selected = max(0, min(int(getattr(self, "_search_selected", 0) or 0),
                                              len(matches) - 1))
        else:
            self._search_selected = 0
        slot.content = self._search_panel_content(query, matches)
        slot.visible = True
        self._update_search_slot(slot)

    def _on_search_change(self, e) -> None:
        # F2：导航/重建后保留搜索词；批 114：即时过滤 + 结果显示（不再只是存字符串）。
        self._search_query = str(getattr(e.control, "value", "") or "")
        self._search_selected = 0
        self._refresh_search_panel()

    def _on_search_submit(self, e) -> None:
        query = str(getattr(e.control, "value", "") or "").strip()
        self._search_query = str(getattr(e.control, "value", "") or "")
        if not query:
            self._refresh_search_panel()
            return
        matches = self._search_matches_now(query)
        if not matches:
            self.toast("没有匹配的页面、命令或最近记录")
            try:
                self.page.update()
            except Exception:  # noqa: BLE001 构建期
                pass
            self._refresh_search_panel()
            return
        self._run_search_entry(matches[min(int(getattr(self, "_search_selected", 0) or 0),
                                          len(matches) - 1)])

    def _clear_search(self) -> None:
        self._search_query = ""
        self._search_matches = []
        self._search_selected = 0
        search = getattr(self, "_toolbar_search", None)
        if search is not None:
            search.value = ""
            C.sync_clearable(search)     # m03131：程序化清空后收起输入框内 ✕
            try:
                search.update()
            except Exception:  # noqa: BLE001
                pass
        slot = getattr(self, "_search_slot", None)
        if slot is not None:
            slot.visible = False
            slot.content = None
            self._update_search_slot(slot)

    def _run_search_entry(self, entry) -> None:
        """执行一条搜索结果：全部落到真实动作，失败有中文兜底。"""
        action = str(getattr(entry, "action", "") or "")
        label = str(getattr(entry, "label", "") or "")
        self._clear_search()
        try:
            if action.startswith("goto:"):
                route = action.split(":", 1)[1]
                self.navigate(route, source="search")
                self.toast(f"已打开「{IA.route_labels(route)['title']}」")
                return
            elif action.startswith("bib:"):
                self.open_bib_view(action.split(":", 1)[1], source="search")
                self.toast(f"已打开「{label}」")
                return
            elif action.startswith("issue:"):
                step = action.split(":", 1)[1]
                self.navigate(f"issue:{step}", source="search")
                self.toast(f"已打开「{label}」")
                return
            elif action.startswith("open:"):
                path = action.split(":", 1)[1]
                if Path(path).exists():
                    self.open_path(path)
                else:
                    self.toast(f"记录不存在：{path}")
                return
            elif action == "theme.toggle":
                self.set_theme("light" if self.theme == "dark" else "dark")
                return
            elif action in ("theme.light", "theme.dark"):
                self.set_theme(action.split(".", 1)[1])
                return
            elif action == "engine.refresh":
                self.refresh_engine_status()
                self.rebuild_shell()
                self.toast("引擎状态已刷新")
                return
            elif action == "run.current":
                SHELL._run_current(self)
                return
            elif action == "stop.current":
                self.stop_run()
                return
            elif action == "new.single":
                self.goto("single")
                self.toast("已打开「单篇稿件」")
                return
            elif action in ("zoom.in", "zoom.out", "zoom.reset"):
                if action == "zoom.reset":
                    target = 1.0
                else:
                    target = round(self.zoom + (0.1 if action == "zoom.in" else -0.1), 2)
                self.set_zoom(target)
                return
        except Exception as exc:  # noqa: BLE001 单条结果打不开不能炸壳
            self.mark("search_error", action=action, error=f"{type(exc).__name__}: {exc}")
            self.toast("这个结果暂时打不开，请改用左侧导航")
            return
        self.toast("这个结果暂时打不开，请改用左侧导航")

    def _search_move(self, delta: int) -> None:
        matches = getattr(self, "_search_matches", []) or []
        if not matches:
            return
        self._search_selected = (int(getattr(self, "_search_selected", 0) or 0) + delta) % len(matches)
        self._refresh_search_panel()

    def search_key_handle(self, event) -> bool:
        """全局热键前置：搜索框聚焦时 ↑↓ 选择、Escape 收起结果。

        Enter 走 TextField 的 on_submit（Flet 里文本框内的 Enter 不会冒泡成普通键），
        所以这里不重复处理 Enter，避免两条路径各执行一次。
        """
        if not getattr(self, "_search_focus", False):
            return False
        key = str(getattr(event, "key", "") or "")
        if key in ("Arrow Down", "ArrowDown", "Down", "Arrow Up", "ArrowUp", "Up"):
            self._search_move(1 if "Down" in key or key == "Down" else -1)
            return True
        if key == "Escape" and getattr(self, "_search_query", ""):
            self._clear_search()
            try:
                self.page.update()
            except Exception:  # noqa: BLE001
                pass
            return True
        return False

    def rebuild_shell(self) -> None:
        self.t = Tokens(self.theme, self.zoom, self.dpi, self.button_palette)
        self._apply_page_theme()  # GUI-10：缩放换了 Tokens → 客户端自绘文本的字号必须同步重放
        self.engine_status = EB.engine_status()
        # 批 114：重启恢复上次路由（last_route，旧配置由 settings_store 迁移而来）。
        page_key, view_key = IA.normalize_route(self._restore_route())
        key = page_key
        self.pages = {cls.key: cls(self) for cls in P.PAGE_CLASSES}
        if key not in self.pages:
            key, view_key = "home", ""
        self.current_page_key = key
        self.current_route = IA.route_string(key, view_key)
        self.nav_open = self._nav_open_for(self.current_route)
        # 惰性构建：启动/换主题/改缩放只建当前页；其余页首次导航时再 build（goto 内兜底）。
        self.pages[key].ensure_built()
        activate = getattr(self.pages[key], "activate", None)
        if view_key and callable(activate):
            try:
                activate(view_key, source="restore")
            except Exception as exc:  # noqa: BLE001 恢复失败回落默认视图
                self.mark("activate_error", page=key, view=view_key,
                          error=f"{type(exc).__name__}: {exc}")
                view_key = IA.page_defaults().get(key, "")
                self.current_route = IA.route_string(key, view_key)
        self._sidebar_compact_cache = None
        self.status_bar = C.StatusBar(self.t)
        self.toast_ctl = C.Toast(self.t)
        self.toolbar = self._toolbar()
        compact = self._sidebar_compact() or not self.sidebar_open
        self.sidebar = ft.Container(
            width=self.t.px(self.t.rail_width if compact else SIDEBAR_W),
            bgcolor=self.t.palette.sidebar, content=self._sidebar_content(),
        )
        self.content = ft.Container(
            expand=True, bgcolor=self.t.palette.surface,
            border_radius=ft.BorderRadius(top_left=self.t.px(10), top_right=self.t.px(10),
                                          bottom_left=0, bottom_right=0),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=self.pages[key].root,
        )
        self.toast_slot = ft.Container(
            padding=C._pad(self.t, 24, 12, 24, 0), alignment=ft.Alignment.TOP_CENTER,
            left=0, right=0, top=0, content=self.toast_ctl.root,
        )
        # 批 114：搜索结果下拉（Overlay 层，位于页面内容之上、toast 之下）。
        self._search_slot = ft.Container(
            visible=False, width=self.t.px(380), top=self.t.px(6), right=self.t.px(12),
            bgcolor=self.t.palette.surface, border=C.box(self.t),
            border_radius=self.t.radius("md"),
            padding=C._pad(self.t, 8, 8, 8, 8),
        )
        self.stage = ft.Stack(expand=True,
                              controls=[self.content, self._search_slot, self.toast_slot])
        body = ft.Container(expand=True, bgcolor=self.t.palette.bg, content=self.stage)
        shell = ft.Column(spacing=0, expand=True, controls=[
            self.toolbar,
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
        self.mark("shell_built", page=key, route=self.current_route,
                  theme=self.theme, zoom=self.zoom, dpi=self.dpi,
                  sidebar=self.t.px(SIDEBAR_W), toolbar=self.t.px(TOOLBAR_H))
        # 族 B：重建后页面是全新实例，必须补 on_show（幂等）：恢复磁盘视图 + 触发数据加载。
        try:
            self.pages[key].on_show()
        except Exception as exc:  # noqa: BLE001
            self.mark("on_show_error", page=key, error=f"{type(exc).__name__}: {exc}")

    # ── 批 114 导航：唯一路由入口 + accordion 侧栏（用户 m03256）────────────
    def _restore_route(self) -> str:
        raw = self.current_route or str(self.store.get("last_route", "") or "")
        page, view = IA.normalize_route(raw)
        return IA.route_string(page, view)

    def _nav_open_for(self, route: str) -> dict:
        page, view = IA.normalize_route(route)
        return {"page": page, "group": IA.group_of_view(page, view) if view else "",
                "collapsed": False}

    def _nav_default_route(self, page_key: str) -> str:
        """进入父项时打开的默认视图（旧行为：单篇/引用库记住上次视图）。"""
        defaults = IA.page_defaults()
        view = defaults.get(page_key, "")
        if page_key == "single":
            saved = str(self.store.get("single_flow", "") or "")
            if saved in {item["key"] for item in IA.nav_views("single")}:
                view = saved
        elif page_key == "bib":
            saved = str(self.store.get("bib_view", "") or "")
            if saved in {item["key"] for item in IA.nav_views("bib")}:
                view = saved
        elif page_key == "issue":
            view = "overview"
        return IA.route_string(page_key, view)

    def _route_view(self) -> str:
        return IA.normalize_route(self.current_route)[1]

    def _toggle_nav_page(self, page_key: str) -> None:
        """父行 chevron：只展开/收起，不导航（规格：箭头点击区不触发导航）。"""
        current = dict(self.nav_open or {})
        same = current.get("page") == page_key
        collapsed = bool(current.get("collapsed")) if same else False
        self.nav_open = {
            "page": page_key if not same or collapsed else page_key,
            "group": (current.get("group") or "") if same else self._nav_open_for(
                IA.route_string(page_key, IA.page_defaults().get(page_key, "")))["group"],
            "collapsed": (not collapsed) if same else False,
        }
        if same and not collapsed:
            self.nav_open["collapsed"] = True
        self.store.set("nav_open", dict(self.nav_open))
        sidebar = getattr(self, "sidebar", None)
        if sidebar is not None:
            sidebar.content = self._sidebar_content()
            self.page.update()
        self.mark("nav_toggle", page=page_key, expanded=not self.nav_open["collapsed"])

    def _toggle_nav_group(self, group_key: str) -> None:
        current = dict(self.nav_open or {})
        opened = current.get("group") == group_key
        current["group"] = "" if opened else group_key
        current["page"] = "bib"
        current["collapsed"] = False
        self.nav_open = current
        self.store.set("nav_open", dict(self.nav_open))
        sidebar = getattr(self, "sidebar", None)
        if sidebar is not None:
            sidebar.content = self._sidebar_content()
            self.page.update()
        self.mark("nav_toggle", page="bib", group=group_key, expanded=not opened)

    def _sidebar_content(self) -> ft.Control:
        t = self.t
        compact = self._sidebar_compact() or not self.sidebar_open
        self._sidebar_compact_cache = compact
        items: list = []
        brand_row = ft.Row(spacing=t.sp("1"), vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[brand.aleph_mark(t, 22)])
        if not compact:
            brand_row.controls.append(C.text("编辑部工具箱", t, role="section"))
        items.append(ft.Container(padding=C._pad(t, 16, 14, 16, 10), content=brand_row))

        active_page = self.current_page_key or "home"
        active_view = self._route_view()
        open_state = dict(self.nav_open or {})
        open_page = str(open_state.get("page") or active_page)
        open_group = str(open_state.get("group") or "")
        collapsed = bool(open_state.get("collapsed"))

        nav_items: list = []
        for section in IA.NAV_TREE:
            if not compact:
                nav_items.append(C.nav_group_label(section["group"], t))
            for entry in section["entries"]:
                key = str(entry["key"])
                active = key == active_page
                views = IA.nav_views(key)
                groups = IA.group_views(key)
                has_children = bool(views or groups)
                if compact:
                    nav_items.append(C.nav_item(
                        str(entry["icon"]), str(entry["label"]), t, active=active,
                        compact=True, on_click=lambda e, k=key: self.goto(k)))
                    continue
                expanded = has_children and open_page == key and not collapsed
                nav_items.append(C.nav_parent(
                    str(entry["icon"]), str(entry["label"]), t, active=active,
                    expanded=expanded, has_children=has_children,
                    on_click=lambda e, k=key: self.navigate(
                        self._nav_default_route(k), source="nav"),
                    on_toggle=lambda e, k=key: self._toggle_nav_page(k)))
                if not expanded:
                    continue
                if groups:
                    for group in groups:
                        gkey = str(group["key"])
                        gexpanded = gkey == open_group
                        nav_items.append(C.nav_group(
                            str(group["label"]), t, expanded=gexpanded, indent=28,
                            on_click=lambda e, g=gkey: self._toggle_nav_group(g)))
                        if not gexpanded:
                            continue
                        for view in group["views"]:
                            vkey = str(view["key"])
                            nav_items.append(C.nav_child(
                                str(view["label"]), t, indent=52,
                                active=active and vkey == active_view,
                                on_click=lambda e, v=vkey: self.navigate(
                                    f"bib:{v}", source="nav")))
                else:
                    for view in views:
                        vkey = str(view["key"])
                        nav_items.append(C.nav_child(
                            str(view["label"]), t, indent=52,
                            active=active and vkey == active_view,
                            on_click=lambda e, k=key, v=vkey: self.navigate(
                                f"{k}:{v}", source="nav")))

        # 导航超高时内部滚动；主题开关与引擎页脚固定在最底部。
        if compact:
            items.extend(nav_items)
            items.append(ft.Container(expand=True))
        else:
            items.append(ft.Container(
                expand=True, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=ft.Column(spacing=0, expand=True, scroll=ft.ScrollMode.AUTO,
                                  controls=nav_items)))
        if compact:
            items.append(ft.Container(padding=C._pad(t, 15, 4, 15, 4), content=C.icon_button(
                "DARK_MODE", "切换深浅色", lambda e: self.set_theme(
                    "light" if self.theme == "dark" else "dark"), t,
                color=t.palette.text_muted)))
        else:
            self.theme_switch = C.switch(
                t, value=self.theme == "dark", on_change=self.on_theme_switch)
            items.append(ft.Container(
                padding=C._pad(t, 16, 2, 16, 2),
                content=ft.Row(spacing=t.sp("1"), controls=[
                    ft.Icon(C.ic("DARK_MODE"), size=t.px(16), color=t.palette.text_muted),
                    C.text("深色模式", t, "small", t.palette.text_muted),
                    ft.Container(expand=True),
                    self.theme_switch,
                ]),
            ))
            engine_ok = self.engine_status.ok
            engine_version = str(self._version_info.get("engine_version") or self.app_version)
            items.append(C.divider(t))
            items.append(ft.Container(
                padding=C._pad(t, 16, 8, 16, 10),
                content=ft.Column(spacing=t.sp("xs"), controls=[
                    ft.Row(spacing=t.sp("half"), controls=[
                        C.status_dot("ok" if engine_ok else "error", t),
                        C.text(f"引擎 {engine_version} · " + ("就绪" if engine_ok else "缺失"),
                               t, "caption", t.palette.text_muted),
                    ]),
                    C.text(f"本地运行 · {self.app_version}", t, "caption", t.palette.text_faint),
                ]),
            ))
        return ft.Column(spacing=0, expand=True, controls=items)

    def toggle_sidebar(self, e=None) -> None:
        self.sidebar_open = not self.sidebar_open
        self._rebuild_sidebar()
        self.mark("sidebar_toggled", open=self.sidebar_open,
                  compact=self._sidebar_compact())

    def _rebuild_sidebar(self) -> None:
        sidebar = getattr(self, "sidebar", None)
        if sidebar is None:
            return
        compact = self._sidebar_compact() or not self.sidebar_open
        sidebar.width = self.t.px(self.t.rail_width if compact else SIDEBAR_W)
        sidebar.content = self._sidebar_content()
        self._sync_toolbar()
        self.page.update()

    def navigate(self, route: str, *, source: str = "nav") -> None:
        """唯一路由入口：`single:proofcheck` / `issue:toc` / `bib:edit`（用户 m03256）。

        - 无冒号的 route 走页面默认视图；非法 route 回落工作台（不静默丢用户）。
        - 同一页面内切换二级视图也要 activate + 重建侧栏（展开态/高亮随 route 变）。
        """
        page_key, view_key = IA.normalize_route(route)
        route_str = IA.route_string(page_key, view_key)
        if page_key not in getattr(self, "pages", {}):
            return
        same_page = page_key == self.current_page_key
        t0 = time.monotonic()
        self.current_page_key = page_key
        self.current_route = route_str
        self.nav_open = self._nav_open_for(route_str)
        self.store.set("last_route", route_str)
        self.store.set("last_page", page_key)
        self.store.set("nav_open", dict(self.nav_open))
        page = self.pages[page_key]
        root = page.ensure_built()
        activate = getattr(page, "activate", None)
        if callable(activate):
            try:
                activate(view_key, source=source)
            except Exception as exc:  # noqa: BLE001 单页激活失败不得炸壳
                self.mark("activate_error", page=page_key, view=view_key,
                          error=f"{type(exc).__name__}: {exc}")
        content = getattr(self, "content", None)
        if content is not None and content.content is not root:
            content.content = root
        sidebar = getattr(self, "sidebar", None)
        if sidebar is not None:
            sidebar.content = self._sidebar_content()
        self._sync_toolbar()
        self.page.update()
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        if not same_page:
            self.page_switches.append(elapsed_ms)
            try:
                page.on_show()
            except Exception as exc:  # noqa: BLE001
                self.mark("on_show_error", page=page_key,
                          error=f"{type(exc).__name__}: {exc}")
        self.mark("nav_change", route=route_str, page=page_key, view=view_key,
                  source=source, switch_ms=elapsed_ms,
                  switch_avg_ms=round(sum(self.page_switches) / len(self.page_switches), 1)
                  if self.page_switches else None)

    def goto(self, key: str) -> None:
        """兼容旧调用：`goto("bib")` = 去该页默认路由；带冒号时等价 navigate。"""
        if ":" in str(key or ""):
            self.navigate(key, source="goto")
            return
        if key not in self.pages:
            return
        if key == self.current_page_key and self.page is not None:
            return
        self.navigate(self._nav_default_route(key), source="goto")

    def open_bib_view(self, view: str, *, source: str = "nav") -> None:
        """跨页入口：去「引用库」并切到指定视图（source=single 表示从单篇页跳来）。

        批 114 起与导航共用 `navigate`：同页/跨页都会真正激活该视图，不会早退。
        """
        self.navigate(f"bib:{view}", source=source)

    def _status_meta(self) -> str:
        """状态栏右侧只留一句人话：不写 DPI / Flet 版本 / 缩放等实现细节。"""
        version = str(getattr(self, "app_version", "") or "")
        if not version:
            return "本地只读"
        return f"本地只读 · {version if version.startswith('v') else 'v' + version}"

    # ── 主题 / 缩放 / 设置 ──────────────────────────────────────────────
    def on_theme_switch(self, e) -> None:
        target = "dark" if e.control.value else "light"
        if target == self.theme:
            return  # 侧栏重建会重放一次 Switch 的初始值，忽略回声，避免启动即被翻面
        self.set_theme(target)

    def set_theme(self, theme: str) -> None:
        if self.running:
            self.toast("运行中不能切换主题")
            return
        if theme not in ("light", "dark") or theme == self.theme:
            return
        t0 = time.perf_counter()
        old = self.t
        built = bool(self.page.controls)
        mode = "retint" if built else "rebuild"
        self.mark("theme_begin", theme=theme, mode=mode)
        self.theme = theme
        self.store.set("theme", theme)
        self.t = Tokens(theme, self.zoom, self.dpi, getattr(self, "button_palette", "c"))
        self.page.theme_mode = ft.ThemeMode.DARK if theme == "dark" else ft.ThemeMode.LIGHT
        retinted = 0
        if built:
            try:
                retinted = self._retint_shell(old)
            except Exception as exc:  # noqa: BLE001 - 换肤失败退回重建，不能停在半套皮肤
                mode = "rebuild"
                self.mark("theme_retint_fallback", theme=theme,
                          error=f"{type(exc).__name__}: {exc}")
                self.rebuild_shell()
        else:
            self.rebuild_shell()
        # 审计 §2.4 gap3：热切换走 retint、不重建控件，但 page.theme/dark_theme 还是
        # 上一个 Tokens 的投影。这里按当前色板重放一遍（幂等；rebuild 分支已跑过），
        # Switch/Slider/Radio 的控件主题才会随皮肤一起换。
        self._apply_page_theme()
        self.page.bgcolor = self.t.palette.bg
        self.status_bar.set_meta(self._status_meta())
        self.page.update()
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.mark("theme_changed", theme=theme, mode=mode, duration_ms=elapsed_ms,
                  retinted=retinted)
        self.apply_titlebar()
        self.persist()
        self.toast("已切换到" + ("深色" if theme == "dark" else "浅色") + "模式（已落盘）")

    # ── 换肤不重建（批 112 S2 · GUI-03）────────────────────────────────
    def _retint_shell(self, old: Tokens) -> int:
        """把已构建的外壳从 old 皮肤原地换到 self.t：只改颜色属性，不重建控件。

        返回改写的颜色属性个数（marker 里记 retinted）。兜底见 set_theme：
        这里抛异常时由调用方退回 rebuild_shell 并记 theme_retint_fallback。
        """
        target = "dark" if self.theme == "dark" else "light"
        bg_map = palette_remap(target)
        fg_map = palette_remap(target, role="fg")
        # 主按钮 C/D 的颜色不在 23 色板里，单独按按钮语义映射（同主题浅/深互换）。
        button_map = self._button_color_map(old.buttons, self.t.buttons)
        bg_map.update(button_map)
        fg_map.update(button_map)
        log_areas = self._retint_log_areas(self.page, old.palette.log_bg)
        changed = self._retint(self.page, bg_map, fg_map)
        # 浅色板里 #FFFFFF 一词多义：_retint 按「背景」角色统一换成 surface，
        # 侧栏与日志区在这里按语义名改回自己的目标色（仍然只改属性）。
        sidebar = getattr(self, "sidebar", None)
        if sidebar is not None and sidebar.bgcolor != self.t.palette.sidebar:
            sidebar.bgcolor = self.t.palette.sidebar
            changed += 1
        toolbar = getattr(self, "toolbar", None)
        if toolbar is not None and toolbar.bgcolor != self.t.palette.sidebar:
            toolbar.bgcolor = self.t.palette.sidebar
            changed += 1
        panel = getattr(self, "content", None)
        if panel is not None and panel.bgcolor != self.t.palette.surface:
            panel.bgcolor = self.t.palette.surface
            changed += 1
        for area in log_areas:
            if area.bgcolor != self.t.palette.log_bg:
                area.bgcolor = self.t.palette.log_bg
                changed += 1
        switch = getattr(self, "theme_switch", None)
        if switch is not None:
            switch.value = self.theme == "dark"
        changed += self._refresh_tokens(old)
        return changed

    # ── 按钮配色 C/D（批 114 P2 v2）────────────────────────────────────
    @staticmethod
    def _button_color_map(old, new) -> dict[str, str]:
        """两套按钮配色之间的旧→新颜色映射（同色不映射）。"""
        mapping: dict[str, str] = {}
        for name in getattr(old, "__dataclass_fields__", {}):
            before = getattr(old, name, None)
            after = getattr(new, name, None)
            if isinstance(before, str) and isinstance(after, str) and before != after:
                mapping[before] = after
        return mapping

    def set_button_palette(self, palette: str) -> None:
        """切换主按钮 C（石板蓝）/ D（石墨）：原地 retint，不重建窗口或页面。"""
        key = normalize_button_palette(palette)
        if key == self.button_palette:
            return
        old = self.t
        self.button_palette = key
        self.store.set("button_palette", key)
        self.t = Tokens(self.theme, self.zoom, self.dpi, key)
        built = bool(self.page.controls)
        changed = 0
        if built:
            mapping = self._button_color_map(old.buttons, self.t.buttons)
            changed = self._retint(self.page, mapping, mapping)
            changed += self._refresh_tokens(old)
            self.status_bar.set_meta(self._status_meta())
            self.page.update()
        self.mark("button_palette_changed", palette=key, changed=changed,
                  mode="retint" if built else "deferred")
        self.persist()
        self.toast("按钮配色：" + self.t.button_label)

    def _retint(self, root, bg_map: dict[str, str],
                fg_map: dict[str, str] | None = None) -> int:
        """递归改写控件树上的颜色属性（不重建控件）；返回改写个数。"""
        return self._retint_node(root, bg_map, fg_map or bg_map, set())

    def _retint_node(self, node, bg_map: dict[str, str], fg_map: dict[str, str],
                     seen: set[int]) -> int:
        if not _retint_walkable(node) or id(node) in seen:
            return 0
        seen.add(id(node))
        changed = 0
        for attr in _RETINT_BG_ATTRS:
            value = _retint_attr(node, attr)
            if isinstance(value, str) and value in bg_map:
                setattr(node, attr, bg_map[value])
                changed += 1
        for attr in _RETINT_FG_ATTRS:
            value = _retint_attr(node, attr)
            if isinstance(value, str) and value in fg_map:
                setattr(node, attr, fg_map[value])
                changed += 1
        border = _retint_attr(node, "border")
        if isinstance(border, dict):
            # 批 113 E：border=ControlStateValue[InputBorder]（DEFAULT/FOCUSED…）
            new_map: dict = {}
            map_changed = False
            for key, item in border.items():
                new_item = _retint_input_border(item, bg_map)
                new_map[key] = new_item if new_item is not None else item
                map_changed = map_changed or new_item is not None
            if map_changed:
                setattr(node, "border", new_map)
                changed += 1
        elif border is not None and not isinstance(border, ft.Border):
            new_border = _retint_input_border(border, bg_map)
            if new_border is not None:
                setattr(node, "border", new_border)
                changed += 1
        if isinstance(border, ft.Border):
            sides = {}
            border_changed = False
            for name in _RETINT_BORDER_SIDES:
                side = getattr(border, name, None)
                if not isinstance(side, ft.BorderSide):
                    sides[name] = side
                    continue
                new_color = bg_map.get(side.color) if isinstance(side.color, str) else None
                if new_color is None:
                    sides[name] = side
                else:
                    sides[name] = ft.BorderSide(width=side.width, color=new_color,
                                                stroke_align=side.stroke_align,
                                                style=side.style)
                    border_changed = True
            if border_changed:
                setattr(node, "border", ft.Border(**sides))
                changed += 1
        changed += self._retint_style(node, bg_map)
        # 语义色标签（hover / 选中行）：浅色板里 surface_2 与 sidebar 同为 #F2F2F7，
        # 只靠颜色换肤会歧义；带 tone: 标签的容器按调色板字段名改回目标色。
        tag = _retint_attr(node, "data")
        if isinstance(tag, str) and tag.startswith(TONE_TAG) and isinstance(node, ft.Container):
            color = getattr(self.t.palette, tag[len(TONE_TAG):], None)
            if isinstance(color, str) and node.bgcolor != color:
                node.bgcolor = color
                changed += 1
        for attr in _RETINT_CHILD_ATTRS:
            child = _retint_attr(node, attr)
            if isinstance(child, list):
                for item in child:
                    changed += self._retint_node(item, bg_map, fg_map, seen)
            elif child is not None:
                changed += self._retint_node(child, bg_map, fg_map, seen)
        return changed

    @staticmethod
    def _retint_style(node, mapping: dict[str, str]) -> int:
        """按钮的 bgcolor/color 状态字典不在控件属性上，换肤与 C/D 都要单独重染。"""
        style = getattr(node, "style", None)
        if not isinstance(style, ft.ButtonStyle) or not mapping:
            return 0
        changed = 0
        for attr in ("bgcolor", "color", "icon_color", "overlay_color"):
            value = getattr(style, attr, None)
            if isinstance(value, dict):
                new = {state: (mapping.get(color, color) if isinstance(color, str) else color)
                       for state, color in value.items()}
                if new != value:
                    setattr(style, attr, new)
                    changed += 1
            elif isinstance(value, str) and value in mapping:
                setattr(style, attr, mapping[value])
                changed += 1
        side = getattr(style, "side", None)
        if isinstance(side, dict):
            new_side = {state: (_retint_border_side(item, mapping) or item)
                        for state, item in side.items()}
            if new_side != side:
                style.side = new_side
                changed += 1
        elif isinstance(side, ft.BorderSide):
            new_side = _retint_border_side(side, mapping)
            if new_side is not None:
                style.side = new_side
                changed += 1
        return changed

    def _retint_log_areas(self, node, log_bg: str, out: list | None = None,
                          seen: set[int] | None = None) -> list:
        """收集日志区容器（浅色下也是 #FFFFFF，换肤前先按「ListView + log_bg」认出来）。"""
        out = [] if out is None else out
        seen = set() if seen is None else seen
        if not _retint_walkable(node) or id(node) in seen:
            return out
        seen.add(id(node))
        if (isinstance(node, ft.Container) and node.bgcolor == log_bg
                and isinstance(node.content, ft.ListView)):
            out.append(node)
        for attr in _RETINT_CHILD_ATTRS:
            child = _retint_attr(node, attr)
            if isinstance(child, list):
                for item in child:
                    self._retint_log_areas(item, log_bg, out, seen)
            elif child is not None:
                self._retint_log_areas(child, log_bg, out, seen)
        return out

    def _refresh_tokens(self, old: Tokens) -> int:
        """刷新持 Tokens 快照的 helper（LogConsole / ResultBanner…），后续新增内容不再用旧色。"""
        new = self.t
        seen: set[int] = set()
        changed = 0
        for owner in [self, *self.pages.values()]:
            try:
                values = list(vars(owner).values())
            except TypeError:
                continue
            for obj in values:
                if obj is None or id(obj) in seen:
                    continue
                seen.add(id(obj))
                if getattr(obj, "t", None) is old:
                    try:
                        obj.t = new
                        changed += 1
                    except AttributeError:
                        pass
        return changed

    async def _theme_cycles(self, count: int) -> None:
        """P2_THEME_CYCLES：连续切主题 count 次（走公开 set_theme，验证热路径不重建）。"""
        try:
            await asyncio.sleep(0.6)
            self.mark("theme_cycles_begin", cycles=count)
            for index in range(1, count + 1):
                self.set_theme("dark" if self.theme == "light" else "light")
                self.mark("theme_cycle", index=index, theme=self.theme)
                await asyncio.sleep(0.4)
            self.mark("theme_cycles_done", cycles=count)
        except Exception as exc:  # noqa: BLE001 - 证据钩子失败不得影响应用
            self.mark("theme_cycles_error", error=f"{type(exc).__name__}: {exc}")

    def apply_titlebar(self) -> None:
        """深色标题栏跟随主题（DwmSetWindowAttribute；失败降级为系统默认，不影响界面）。

        P6a 热修：平台旁路（winplat）在发布包里可能不可用（如无窗口/权限），
        任何异常都必须吞掉并记 mark，绝不能冒到 Flet UI 回调导致错误页。
        """
        try:
            if not self.client_hwnd:
                self.client_hwnd = winplat.find_client_window(APP_TITLE) or 0
            if not self.client_hwnd:
                return
            self.titlebar_info = winplat.set_dark_titlebar(self.client_hwnd, self.theme == "dark")
            self.mark("titlebar", theme=self.theme, **self.titlebar_info)
        except Exception as exc:  # noqa: BLE001 - 平台旁路失败不得影响主题切换
            self.mark("titlebar_error", theme=self.theme,
                      error=f"{type(exc).__name__}: {exc}")

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
            # 批 109 Wave 0 / S5：保存失败不再静默——给出可读原因（磁盘/权限），
            # 不弹栈、不影响后续操作（见任务书 §S5 与 b109-parity/report.md）。
            self.mark("settings_save_error", error=str(exc))
            reason = exc.strerror or str(exc) or "未知错误"
            self.toast(f"设置保存失败：{reason}（设置未写入，请检查磁盘空间与写入权限）")

    def save_settings(self, toast: bool = False) -> None:
        self.store.set("last_page", self.current_page_key)
        self.store.set("last_route", self.current_route or self.current_page_key or "home")
        self.store.set("nav_open", dict(self.nav_open or {}))
        self.persist()
        self.mark("settings_saved", file=str(self.store.path), exists=self.store.path.exists())
        if toast:
            self.toast("设置已保存")

    def refresh_engine_status(self) -> None:
        self.engine_status = EB.engine_status()
        VERSION.cache_clear()
        self._version_info = VERSION.resolve(self.engine_status)
        self.app_version = self._version_info["version"]
        self.mark("engine_status", **self.engine_status.to_dict())
        self.mark("app_version", version=self.app_version, source=self._version_info["source"])

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
        SHELL.log_buffer(self).add(line)
        if self.current_page_key == "settings" and self.line_count % 5 == 0:
            page = self.pages.get("settings")
            if page is not None and getattr(page, "log_list", None) is not None:
                try:
                    SHELL.render_logs(page)
                except Exception:  # noqa: BLE001
                    pass
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
        try:
            self.page.run_task(self._browse_task, ref, dialog_title, kind, key, store_prefix)
        except Exception as exc:  # noqa: BLE001  调度失败也落到中文提示（GUI-04 加固）
            self._picker_error(f"run_task: {type(exc).__name__}: {exc}")

    async def _browse_task(self, ref, dialog_title: str, kind: str, key: str, store_prefix: str) -> None:
        try:
            if self._picker is None:
                self._picker_error("file picker 未初始化")
                return
            if kind == "dir":
                value = await self._picker.get_directory_path(dialog_title=dialog_title)
            else:
                files = await self._picker.pick_files(dialog_title=dialog_title, allow_multiple=False)
                value = files[0].path if files else None
            self.mark("picker_result", kind=kind, key=key, value=value or "")
            if not value:
                self._picker_error("", cancel=True)
                return
            ref[0].value = value
            C.sync_clearable(ref[0])     # m03131：浏览回填后显示框内 ✕
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
            self._picker_error(f"{type(exc).__name__}: {exc}")

    def _picker_error(self, error: str, *, cancel: bool = False) -> None:
        """文件选择失败/取消都要有中文提示（B4）；失败不弹系统错误，可改手输。"""
        if cancel:
            self.mark("picker_cancel")
            self.toast("已取消选择")
            return
        self.mark("picker_error", error=error)
        self.toast("无法打开文件选择器。可重试「浏览…」，或把路径直接粘贴到输入框。")

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
        # 720 设计像素断点：收成 56 图标条 / 展开 220。只在跨断点时重建侧栏，拖动不抖动。
        if getattr(self, "sidebar", None) is not None:
            compact = self._sidebar_compact() or not self.sidebar_open
            if compact != self._sidebar_compact_cache:
                self._rebuild_sidebar()
        # 页面级断点（表格列集 / 工作入口 3→2→1）：只在跨阈值时重建当前视图。
        page_obj = self.pages.get(self.current_page_key)
        if page_obj is not None and hasattr(page_obj, "on_resize"):
            try:
                page_obj.on_resize()
            except Exception as exc:  # noqa: BLE001
                self.mark("page_resize_error", error=f"{type(exc).__name__}: {exc}")

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
            if self.tray_active and not self._quitting:
                # 启用托盘：关闭窗口 = 最小化到托盘；退出只走托盘菜单「退出」（A2）。
                self.save_settings()
                self._hide_to_tray()
                return
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
            current_route=self.current_route,
            nav_open=dict(self.nav_open or {}),
            settings_path=str(self.store.path),
            settings_exists=self.settings_exists,
            engine=self.engine_status.to_dict(),
            hwnd=self.client_hwnd,
            titlebar=self.titlebar_info,
            tray={"started": bool(getattr(self.tray, "started", False)) if self.tray else False,
                  "enabled": self.tray_enabled,
                  "active": self.tray_active,
                  "error": str(getattr(self.tray, "error", "") or "") if self.tray else ""},
            geometry=self.geometry,
            page_switches=self.page_switches[-10:],
            page_switch_avg_ms=(round(sum(self.page_switches) / len(self.page_switches), 1)
                                if self.page_switches else None),
            p3_bridged=P3_BRIDGED,
            flet=FLET_VERSION,
            python=self.python_version,
        )

    def _on_page_error(self, e) -> None:
        """`Page.on_error`：未捕获的界面异常落 startup.log（便于事后定位）。"""
        message = str(getattr(e, "data", "") or e)
        SLOG.log("page_error", message=message[:2000])
        try:
            self.toast("界面出现未处理异常（已记录到启动日志）")
        except Exception:  # noqa: BLE001 - 出错兜底本身不能再炸
            pass

    async def after_boot(self) -> None:
        # 批 113 C-2：客户端未处理异常兜底——写启动日志 + 轻提示，不再静默。
        self.page.on_error = self._on_page_error
        SLOG.log("after_boot_begin")
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
            self.navigate(self._nav_default_route(page_key), source="env")
        # 批 112 T-C：跨平台自动化钩子——直接落到 bib 的指定子视图（10 视图覆盖用）。
        bib_view = _env_str("P2_BIB_VIEW")
        if bib_view:
            bib_page = self.pages.get("bib")
            if bib_page is not None and bib_view in bib_page.view_keys():
                self.navigate(f"bib:{bib_view}", source="env")
                self.mark("bib_view_hook", view=bib_view, source="env")
            elif bib_page is not None:
                # 非法视图不静默：回落概况并留证据（P2 冒烟据此断言）。
                self.mark("bib_view_invalid", view=bib_view, reason="unknown_view")
                self.navigate("bib:overview", source="env_fallback")
                self.mark("bib_view_hook", view="overview", source="env_fallback")
            else:
                self.mark("bib_view_error", view=bib_view, reason="no_bib_page")
        # 批 112 S3a2：单篇页只剩 convert/proofcheck 两步，启动即可直达。
        single_flow = _env_str("P2_SINGLE_FLOW")
        if single_flow:
            single_page = self.pages.get("single")
            if single_page is not None and single_flow in single_page.FLOWS:
                self.navigate(f"single:{single_flow}", source="env")
                self.mark("single_flow_hook", flow=single_flow, source="env")
            elif single_page is not None:
                self.mark("single_flow_invalid", flow=single_flow, reason="unknown_flow")
        await self._apply_demo_hooks()
        # 批 114（用户 m03037）：GUI 自检可直达整期装配的某一步，只选中不运行。
        issue_step = _env_str("P2_ISSUE_STEP")
        if issue_step:
            issue_page = self.pages.get("issue")
            steps = getattr(issue_page, "STEPS", []) if issue_page is not None else []
            if issue_page is not None and any(step["key"] == issue_step for step in steps):
                self.navigate(f"issue:{issue_step}", source="env")
                self.mark("issue_step_hook", step=issue_step, source="env")
            else:
                self.mark("issue_step_invalid", step=issue_step, reason="unknown_step")
        if _env_str("P2_ENVCHECK") in ("1", "true", "yes"):
            settings_page = self.pages.get("settings")
            if settings_page is not None and hasattr(settings_page, "_envcheck_task"):
                settings_page.ensure_built()
                self.page.run_task(settings_page._envcheck_task)
        elif not self.settings_exists and SHELL.first_run_needed(self.store):
            # v1.6.1：首启不再自动跑环境检查（打包形态每次解包 sidecar ≈10s，且失败会误报成
            # 「环境检查失败」黄条）。改为设置页里的显式入口 + 首启提示，失败不阻塞启动。
            settings_page = self.pages.get("settings")
            if settings_page is not None:
                settings_page.ensure_built()
            self.mark("envcheck_first_run_skipped", reason="v1.6.1: first-run auto check disabled")
            try:
                self.toast("首次启动：建议到「设置 → 环境检查」跑一次，确认 TeX 工具链可用。")
            except Exception:  # noqa: BLE001 - 提示失败不影响启动
                pass
        bib_query = _env_str("P2_BIB_QUERY")
        if bib_query:
            self.page.run_task(self._bib_query_probe, bib_query)
        bib_query_ui = _env_str("P2_BIB_QUERY_UI")
        if bib_query_ui:
            bib_page = self.pages.get("bib")
            if bib_page is not None:
                self.navigate("bib:query", source="env")
                if bib_page.query_field is not None:
                    bib_page.query_field.value = bib_query_ui
                    C.sync_clearable(bib_page.query_field)   # m03131
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
        SLOG.log("after_boot_done")

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
        """首帧可见后接管外部窗口能力（跨进程，不侵入 Flutter 内部）。

        P6a 热修：整个平台接管段包 try/except —— winplat 在发布包里可能整体不可用，
        失败只记 mark 并放弃平台增强，绝不向 UI 抛异常。
        """
        try:
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
        except Exception as exc:  # noqa: BLE001 - 平台增强失败不影响应用
            self.mark("platform_error", where="wake",
                      error=f"{type(exc).__name__}: {exc}")

    async def _platform_hooks(self) -> None:
        tray_want = _env_str("P3_TRAY") in ("1", "true", "yes")
        tray_test = _env_str("P3_TRAY_TEST")
        if tray_want or tray_test:
            # 环境变量是调试入口；行为与设置项一致（同样走 _start_tray 的失败回退）。
            self._start_tray(force=True, reason="env")
        elif self.tray_enabled:
            self._start_tray(reason="setting")
        if tray_test and self.tray_active:
            for action in [a.strip() for a in tray_test.split(",") if a.strip()]:
                if action.startswith("wait:"):
                    try:
                        await asyncio.sleep(max(0.0, float(action.split(":", 1)[1])))
                    except ValueError:
                        self.mark("tray_invoke", action=action, ok=False,
                                  error="wait 需要秒数，例如 wait:15")
                    continue
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
                page.ensure_built()
                page.prefill(source=source, out=out, bib=bib, proof_tex=proof,
                             flow=_env_str("P2_DEMO_FLOW", "convert"))
        if issue or toc:
            page = self.pages.get("issue")
            if page:
                page.ensure_built()
                page.prefill(issue_dir=issue, toc_report=toc)

    async def _open_picker(self, kind: str) -> None:
        try:
            self.mark("picker_open_begin", kind=kind, key="env")
            if self._picker is None:
                self._picker_error("file picker 未初始化")
                return
            if kind == "dir":
                value = await self._picker.get_directory_path(dialog_title="选择稿件所在目录")
            else:
                files = await self._picker.pick_files(dialog_title="选择稿件", allow_multiple=False)
                value = files[0].path if files else None
            self.mark("picker_result", kind=kind, value=value or "")
            if not value:
                self._picker_error("", cancel=True)
        except Exception as exc:  # noqa: BLE001
            self._picker_error(f"{type(exc).__name__}: {exc}")

    async def _autorun_normalize(self) -> None:
        """P2_AUTORUN=checkbib 的新路径：进「引用库 → 规范化」，有样例才真跑。

        checkbib 只读（产出报告文件、绝不写库）；没给 P2_DEMO_BIB 时只到面板预填，
        不触发运行，避免旧证据脚本在无输入时把 `_wait_run_finished` 空等到超时。
        """
        bib_page = self.pages.get("bib")
        if bib_page is None:
            self.mark("autorun_error", error="no bib page")
            return
        self.open_bib_view("normalize", source="autorun")
        view = bib_page.normalize_view
        view.ensure_built()
        bib = _env_str("P2_DEMO_BIB")
        if not bib:
            self.mark("autorun_normalize_ready", bib="", ran=False)
            return
        view.set_field("bib_returned", bib)
        bib_page.mark("demo_prefill", flow="normalize", bib=bib)
        self.mark("autorun_normalize_run", bib=bib)
        view._on_run()

    async def _autorun(self, target: str) -> None:
        await asyncio.sleep(0.5)
        self.mark("autorun_begin", target=target)
        legacy_checkbib = _env_str("P2_AUTORUN_LEGACY") in ("1", "true", "yes")
        normalize_redirect = False
        if target == "checkbib" and not legacy_checkbib:
            # 批 112 S3a2：交回 .bib 体检改道「引用库 → 规范化」；旧单篇路径只在
            # P2_AUTORUN_LEGACY=1 时保留一次（兼容旧证据脚本，下批可删）。
            await self._autorun_normalize()
            await asyncio.sleep(0.2)  # 让 run_task 的 begin_run 先落地
            normalize_redirect = True
        elif target in ("convert", "checkbib", "proofcheck"):
            page = self.pages.get("single")
            if page is None:
                self.mark("autorun_error", error="no single page")
                return
            page.ensure_built()
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
            page.ensure_built()
            page.prefill(issue_dir=_env_str("P2_DEMO_ISSUE"), toc_report=_env_str("P2_DEMO_TOC"))
            page.select_step(target)
            page.run_step_demo(target)
        elif target == "all":
            page = self.pages.get("issue")
            if page is None:
                return
            page.ensure_built()
            page.prefill(issue_dir=_env_str("P2_DEMO_ISSUE"), toc_report=_env_str("P2_DEMO_TOC"))
            page.run_all_demo()
        if not normalize_redirect or self.run_seen or self.running:
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


def _launch() -> None:
    """进程入口：先落启动日志/首启提示，再交给 Flet。"""
    SLOG.install()
    SLOG.log("launch_begin", argv=sys.argv[1:], platform=sys.platform)
    if getattr(sys, "frozen", False) and sys.platform == "darwin":
        # B113：打包版把 Flet 桌面客户端随包放在 `.app/Contents/Resources/flet-client/`。
        # 用 FLET_VIEW_PATH 指过去，首启就不再从 GitHub 下载并解压到 `~/.flet/client`
        # （约 135MB，10–30s 无窗口）。只设环境变量；源码模式 / 旧包（没有随包客户端）一律 no-op。
        flet_client_dir = Path(sys.executable).resolve().parent.parent / "Resources" / "flet-client"
        if (flet_client_dir / "Flet.app").is_dir():
            os.environ.setdefault("FLET_VIEW_PATH", str(flet_client_dir))
            SLOG.log("flet_view_path", path=str(flet_client_dir))
        try:
            _settings, exists = SettingsStore().load()
        except Exception:  # noqa: BLE001
            exists = True
        if not exists:
            SLOG.log("first_run_detected")
            # 首启要解压 Flet 桌面客户端（~135MB）与 onefile 解包，可能 10–40s 无窗口：
            # 先给系统通知，避免用户以为没启动而反复点击（v1.6.1）。
            try:
                subprocess.Popen(
                    ["osascript", "-e",
                     'display notification "首次启动正在准备运行环境（约 10–30 秒），请稍候…" '
                     'with title "编辑部工具箱"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:  # noqa: BLE001
                pass
    ft.run(main, view=ft.AppView.FLET_APP)


if __name__ == "__main__":
    _launch()
