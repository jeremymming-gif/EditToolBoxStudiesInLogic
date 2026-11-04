"""批 115 夜间 UX 修复（FIX-SHELL）：faint 对比度 / 侧栏可达 / 右缘 gutter。

对应评审 `b115-ux-review2`：
- P1 #11：11px `text_faint` 浅色白底仅 3.26:1，暗色 #2C2C2E 上 4.27:1；
- P1 #12：1160×780「单篇稿件」展开后系统段被裁，1000 折叠时不可见；
- P2 #11：内容面板右缘贴窗，右上 10px 圆角被裁；
- P2 #10：⌘K 无面板 —— 当前无用户可见 ⌘K 提示（仅 ia_map 注释），见证据 notes。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import flet as ft

import main as M
from ui.tokens import (CONTENT_GUTTER, DARK, LIGHT, SIDEBAR_SCROLL_H,
                       SIDEBAR_W, Tokens, palette_remap)


# ── WCAG 2.x 相对亮度 / 对比度 ───────────────────────────────────────────────
def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    raw = color.lstrip("#")
    assert len(raw) == 6, color
    r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(fg: str, bg: str) -> float:
    high, low = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# ── 侧栏 App 骨架（只调 _sidebar_content，不连引擎/窗口）────────────────────
class _Page:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.updated = 0

    def update(self) -> None:
        self.updated += 1


def _sidebar_app(width: int = 1160, height: int = 780) -> M.App:
    app = object.__new__(M.App)
    app.t = Tokens()
    app.page = _Page(width, height)
    app.window_w, app.window_h = width, height
    app.current_page_key = "single"
    app.current_route = "single:convert"
    app.nav_open = {"page": "single", "group": "", "collapsed": False}
    app.sidebar_open = True
    app.bib_badge = None
    app._bib_nav_marked = False
    app.theme = "light"
    app.engine_status = SimpleNamespace(ok=True)
    app._version_info = {"engine_version": "9.9.9"}
    app.app_version = "v9.9.9"
    app.marks = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.navigate = lambda route, source="nav": None
    app.on_theme_switch = lambda e: None
    return app


def _nav_column(app: M.App) -> ft.Column:
    root = app._sidebar_content()
    assert isinstance(root, ft.Column)
    brand, nav_box = root.controls[0], root.controls[1]
    assert brand is not None
    assert nav_box.expand is True
    assert nav_box.clip_behavior == ft.ClipBehavior.ANTI_ALIAS, \
        "导航列必须裁剪，滚动时不能溢出品牌行/主题开关"
    assert isinstance(nav_box.content, ft.Column)
    return nav_box.content


def _texts(control) -> list[str]:
    found: list[str] = []

    def walk(node) -> None:
        if node is None:
            return
        if isinstance(node, ft.Text):
            found.append(str(node.value or ""))
        for attr in ("controls", "content"):
            child = getattr(node, attr, None)
            if isinstance(child, (list, tuple)):
                for item in child:
                    walk(item)
            elif child is not None and not isinstance(child, str):
                walk(child)

    walk(control)
    return found


# ── P1 #11：11px faint 对比度 ≥ 4.5（WCAG AA 正文）─────────────────────────
def test_old_faint_would_fail_aa() -> None:
    # 反例：修复前的值在评审实测背景上不足 4.5，防止以后有人改回去。
    assert contrast("#8E8E93", "#FFFFFF") < 4.5
    assert contrast("#8E8E93", DARK.surface) < 4.5


def test_light_faint_meets_aa_on_every_11px_background() -> None:
    for bg in (LIGHT.surface, LIGHT.surface_2, LIGHT.sidebar,
               LIGHT.bg, LIGHT.log_bg):
        ratio = contrast(LIGHT.text_faint, bg)
        assert ratio >= 4.5, f"light faint on {bg}: {ratio:.2f}"
    # log 区的 muted 同样按 11.5px 日志正文看待。
    assert contrast(LIGHT.log_muted, LIGHT.log_bg) >= 4.5


def test_dark_faint_meets_aa_on_every_11px_background() -> None:
    for bg in (DARK.surface, DARK.surface_2, DARK.sidebar,
               DARK.bg, DARK.log_bg):
        ratio = contrast(DARK.text_faint, bg)
        assert ratio >= 4.5, f"dark faint on {bg}: {ratio:.2f}"
    assert contrast(DARK.log_muted, DARK.log_bg) >= 4.5


def test_faint_value_is_shared_with_log_muted_and_retints_through_palette() -> None:
    # log_muted 与 faint 同值：两处 11px 说明文字一起抬到 AA。
    assert LIGHT.text_faint == LIGHT.log_muted
    assert DARK.text_faint == DARK.log_muted == DARK.text_muted
    # 全链路走 palette_remap（组件里没有 faint 字面 hex）；浅→深、深→浅都落到新值。
    to_dark = palette_remap("dark", "fg")
    to_light = palette_remap("light", "fg")
    assert to_dark[LIGHT.text_faint] == DARK.text_faint
    assert to_dark[LIGHT.log_muted] == DARK.log_muted
    assert to_light[DARK.text_faint] == LIGHT.text_faint


# ── P1 #12：导航列可滚动，短窗口常显滚动条，系统段可达 ─────────────────────
def test_flat_nav_is_scrollable_with_all_rows_at_1160x780() -> None:
    # 批 116 决策：侧栏全扁平；引用库只剩一个叶子（9 视图移入页内 chip）。
    app = _sidebar_app(1160, 780)
    nav = _nav_column(app)
    # 8 行 = 2 段标签（工作区/系统）+ 3 工作区叶 + 1 引用库叶 + 2 系统叶
    assert len(nav.controls) == 8
    assert nav.scroll == ft.ScrollMode.ALWAYS, "780 高必须常显滚动条，否则用户不知道下面还有内容"
    labels = _texts(nav)
    for wanted in ("工作台", "单篇稿件", "整期装配", "引用库",
                   "系统", "设置", "帮助与关于"):
        assert wanted in labels, f"导航缺少 {wanted}"
    # 单篇/整期/引用库的二级视图都不再出现在侧栏（批 116 NAV-B / BIB-NAV2）。
    for gone in ("初排转换", "一校核查", "检查tex信息",
                 "查阅", "采录", "治理", "概览", "待处理", "库健康"):
        assert gone not in labels, f"扁平侧栏不应再有子项/组标签 {gone}"
    # 系统段是列表尾部：滚动到底即可见（控件存在，不依赖窗口高度重建）。
    assert labels[-3:] == ["系统", "设置", "帮助与关于"]


def test_short_windows_show_scrollbar_and_tall_windows_use_auto() -> None:
    assert _sidebar_app(1000, 700)._sidebar_short() is True
    assert _sidebar_app(1160, 780)._sidebar_short() is True
    assert _sidebar_app(1240, 860)._sidebar_short() is False
    assert _nav_column(_sidebar_app(1000, 700)).scroll == ft.ScrollMode.ALWAYS
    assert _nav_column(_sidebar_app(1240, 860)).scroll == ft.ScrollMode.AUTO
    assert SIDEBAR_SCROLL_H == 820


def test_compact_rail_keeps_settings_and_help_without_scrollbar_clutter() -> None:
    app = _sidebar_app(680, 700)
    assert app._sidebar_compact() is True
    nav = _nav_column(app)
    assert nav.scroll == ft.ScrollMode.AUTO, "56px 图标条不常显滚动条"
    # home / single / issue / bib / settings / help：系统两行仍在图标条里。
    assert len(nav.controls) == 6
    assert all(isinstance(row, ft.Container) for row in nav.controls)
    assert _texts(nav) == []  # 紧凑条只有图标，不显示分组标签/文字


def test_sidebar_source_keeps_both_scroll_modes_and_guards_height() -> None:
    src = inspect.getsource(M.App._sidebar_content)
    assert "ft.ScrollMode.AUTO" in src
    assert "ft.ScrollMode.ALWAYS" in src
    assert "self._sidebar_short()" in src
    assert SIDEBAR_W == 220


# ── P2 #11：内容面板右缘 10px gutter（圆角不再贴窗被裁）────────────────────
def test_content_padding_is_ten_design_px() -> None:
    app = object.__new__(M.App)
    app.t = Tokens()
    pad = app._content_padding()
    assert pad.left == 0 and pad.top == 0 and pad.bottom == 0
    assert pad.right == CONTENT_GUTTER == 10


def test_content_gutter_scales_with_zoom_and_shell_uses_helper() -> None:
    app = object.__new__(M.App)
    app.t = Tokens(zoom=1.5)
    assert app._content_padding().right == 15
    src = inspect.getsource(M.App.rebuild_shell)
    assert "padding=self._content_padding()" in src
    assert "content_gutter=self.t.px(CONTENT_GUTTER)" in src
