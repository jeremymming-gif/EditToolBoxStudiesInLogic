"""批 116：主题切换的跨页一致性契约（深/浅往返）。

用户复现（2026-11-04）：
  浅色启动 → 切深色 → 从侧栏进 工作台/单篇/整期/设置 → 颜色错误；
  再切回浅色 → 引用库 编辑/入藏/查重合并/字段归位 → 颜色错误。

根因（见 `main._retint_shell` / `navigate`）：
  热切换只重染 `self.page` 这一棵「当前挂载树」；`self.pages` 里已 build 的页面
  root、`BibHost._views` 里懒加载过的视图 root 都停在旧皮肤，`navigate()` 直接
  挂缓存树，于是跨页混色；视图还各自抓着旧 `Tokens` 快照，重建也还是旧色。

本文件不启动 Flet 窗口（沿用 tests/test_b112_theme.py 的 `App.__new__` 假壳），
四条契约：
1. 切主题后，**未挂载但已构建**的页面 root 与嵌套控件已是新皮肤；
2. `BibHost` 缓存的视图 root 被重染，且视图的 `t` 快照刷新到新 Tokens；
3. 浅→深→浅往返后所有树回到原色（不是只回来一半）；
4. 已挂载树只映射一次——明暗两板同值色（#F5F5F7 既是浅 bg 又是深 text）
   若被二次映射会串成深底，必须保持 DARK.text。
"""
from __future__ import annotations

from types import SimpleNamespace

import flet as ft
import pytest

import main as M
from ui import bib_host as BH
from ui import base_page as BP
from ui.tokens import DARK, LIGHT, Tokens


class _CachedPage(BP.BasePage):
    """模拟 BasePage 子类：root 一旦 build 就长期复用（工作台/单篇/整期/设置）。"""

    key = "cached"

    def __init__(self, app, rows: int = 3) -> None:
        super().__init__(app)
        self._rows = rows

    def build(self) -> ft.Control:
        t = self.t
        return ft.Container(
            bgcolor=t.palette.surface,
            content=ft.Column(controls=[
                ft.Container(
                    # 浅色 #F2F2F7 同时是 sidebar/surface_2，靠 tone 标签消歧
                    data="tone:surface_2",
                    bgcolor=t.palette.surface_2,
                    content=ft.Text(f"第 {i} 行", color=t.palette.text),
                )
                for i in range(self._rows)
            ]),
        )


class _FakeView:
    """模拟 bib_* 视图：构造时抓 Tokens 快照，build 时缓存 _root。"""

    def __init__(self, app) -> None:
        self.t = app.t
        self._root = ft.Container(
            bgcolor=self.t.palette.surface,
            content=ft.Text("视图正文", color=self.t.palette.text),
        )


def _app() -> M.App:
    app = M.App.__new__(M.App)
    app.running = False
    app.theme = "light"
    app.zoom = 1.0
    app.dpi = 96
    app.t = Tokens("light", 1.0, 96)
    app.engine_status = SimpleNamespace(ok=True)
    app.dpi_aware = False
    app._marker_path = None
    app.client_hwnd = 0
    app.titlebar_info = {}
    app.log_result = SimpleNamespace(t=app.t)
    app.page = SimpleNamespace(theme_mode=None, bgcolor=None, controls=[],
                               theme=None, dark_theme=None, update=lambda: None)
    app.sidebar = ft.Container(bgcolor=LIGHT.sidebar,
                               border=ft.Border(right=ft.BorderSide(1, LIGHT.border)))
    app.theme_switch = ft.Switch(value=False, active_color=LIGHT.accent)
    app.status_bar = SimpleNamespace(
        root=ft.Container(bgcolor=LIGHT.surface), metas=[],
        set_meta=lambda meta: app.status_bar.metas.append(meta))
    app.toast_ctl = SimpleNamespace(root=ft.Container(bgcolor=LIGHT.accent_soft))
    app.pages = {}
    app.store = SimpleNamespace(set=lambda key, value: None)
    app.apply_titlebar = lambda: None
    app.persist = lambda: None
    app.toast = lambda message: None
    return app


def _mount(app: M.App, home: _CachedPage) -> None:
    """把 home.root 挂进假壳（相当于 rebuild_shell 当前页 + content）。"""
    app.content = ft.Container(bgcolor=LIGHT.surface, content=home.ensure_built())
    app.page.controls = [app.sidebar, app.content, app.status_bar.root]


def _text_of(page: _CachedPage) -> ft.Text:
    return page.root.content.controls[0].content


# ── 1. 未挂载页面 root 也换肤 ─────────────────────────────────────────────
def test_switch_retints_unmounted_pages():
    app = _app()
    home = _CachedPage(app)
    single = _CachedPage(app)
    issue = _CachedPage(app, rows=5)
    settings = _CachedPage(app, rows=6)
    app.pages = {"home": home, "single": single, "issue": issue, "settings": settings}
    for page in app.pages.values():
        page.ensure_built()
    _mount(app, home)

    app.set_theme("dark")

    for page in (single, issue, settings):
        assert page.root.bgcolor == DARK.surface
        assert page.root.content.controls[0].bgcolor == DARK.surface_2
        assert _text_of(page).color == DARK.text
    # 当前页也换到同一套皮肤
    assert home.root.bgcolor == DARK.surface
    assert _text_of(home).color == DARK.text


# ── 2. BibHost 缓存视图：root 与 Tokens 快照都刷新 ────────────────────────
def test_switch_retints_cached_bib_views_and_refreshes_tokens():
    app = _app()
    home = _CachedPage(app)
    host = BH.BibHost.__new__(BH.BibHost)
    BP.BasePage.__init__(host, app)
    host._views = {}
    host._view_errors = {}
    host.root = ft.Container(
        bgcolor=LIGHT.surface,
        content=ft.Row(controls=[ft.Container(data="tone:surface_2",
                                              bgcolor=LIGHT.surface_2)]))
    view = _FakeView(app)
    host._views["edit"] = view
    app.pages = {"home": home, "bib": host}
    _mount(app, home)

    assert host.theme_roots() == [host.root, view._root]  # 未挂载视图也在覆盖范围

    old_view_t = view.t
    app.set_theme("dark")

    assert host.root.bgcolor == DARK.surface
    assert host.root.content.controls[0].bgcolor == DARK.surface_2
    assert view._root.bgcolor == DARK.surface
    assert view._root.content.color == DARK.text
    assert view.t is app.t and view.t is not old_view_t  # 快照已刷新，后续 build 不再用旧色


# ── 3. 浅→深→浅往返 ─────────────────────────────────────────────────────
def test_theme_roundtrip_light_dark_light():
    app = _app()
    home = _CachedPage(app)
    single = _CachedPage(app)
    app.pages = {"home": home, "single": single}
    for page in app.pages.values():
        page.ensure_built()
    _mount(app, home)

    app.set_theme("dark")
    assert single.root.bgcolor == DARK.surface
    assert _text_of(single).color == DARK.text

    app.set_theme("light")
    assert single.root.bgcolor == LIGHT.surface
    assert single.root.content.controls[0].bgcolor == LIGHT.surface_2
    assert _text_of(single).color == LIGHT.text
    assert home.root.bgcolor == LIGHT.surface
    assert _text_of(home).color == LIGHT.text

    # 第二次往返同样收敛（换肤不能只对第一次生效）
    app.set_theme("dark")
    assert single.root.bgcolor == DARK.surface
    assert _text_of(single).color == DARK.text
    app.set_theme("light")
    assert home.root.bgcolor == LIGHT.surface
    assert _text_of(home).color == LIGHT.text


# ── 4. 挂载树不被二次映射（同值色串色守卫）──────────────────────────────
def test_mounted_tree_is_retinted_once():
    app = _app()
    home = _CachedPage(app)
    single = _CachedPage(app)
    app.pages = {"home": home, "single": single}
    for page in app.pages.values():
        page.ensure_built()
    _mount(app, home)

    app.set_theme("dark")

    # DARK.text = #F5F5F7 同时是 LIGHT.bg；若当前页被 offscreen 再染一次，
    # fg_map 会把它改成 DARK.bg (#1E1E1E)，深底深字。
    assert _text_of(home).color == DARK.text == "#F5F5F7"
    assert _text_of(single).color == DARK.text
    # 背景侧 #F2F2F7（LIGHT.sidebar/surface_2）同理不能被二次映射。
    assert single.root.content.controls[0].bgcolor == DARK.surface_2


# ── 5. 切换后导航：挂缓存树 + 新 build 都拿当前主题 ───────────────────────
def test_navigation_after_switch_gets_current_theme():
    app = _app()
    home = _CachedPage(app)
    single = _CachedPage(app)
    app.pages = {"home": home, "single": single}
    for page in app.pages.values():
        page.ensure_built()
    _mount(app, home)

    app.set_theme("dark")
    # 等价于 navigate("single")：直接挂此前缓存的 root，不重建页面
    app.content.content = single.root
    assert single.root.bgcolor == DARK.surface
    assert _text_of(single).color == DARK.text

    # 切换后才第一次构建的页面（懒加载）用 app.t 的新色板
    late = _CachedPage(app)
    app.pages["settings"] = late
    late.ensure_built()
    assert late.root.bgcolor == DARK.surface
    assert _text_of(late).color == DARK.text


if __name__ == "__main__":  # pragma: no cover - 手工单跑
    raise SystemExit(pytest.main([__file__, "-q"]))
