"""F2 回归：顶部栏控件复用（导航不重建 TextField/样式只改不换）。"""
from __future__ import annotations

import flet as ft

import app.main as M
from app.ui.tokens import Tokens


def _app(zoom=1.0, theme="light"):
    app = M.App.__new__(M.App)
    app.t = Tokens(theme, zoom, 96, "c")
    app._search_query = "唐"
    app._toolbar_row = None
    app.current_page_key = "home"
    app.goto = lambda *a, **k: None
    app.toggle_sidebar = lambda *a, **k: None
    app._toolbar_spec = lambda: ("文稿", "3 篇", ("新建", ft.Icons.ADD, lambda e: None))
    return app


def test_toolbar_parts_are_reused_and_search_text_survives():
    app = _app()
    first = app._toolbar_content()
    search = app._toolbar_search
    assert search.value == "唐"
    search.value = "苏轼"
    second = app._toolbar_content()
    assert first is second is app._toolbar_row
    assert app._toolbar_search is search
    assert search.value == "苏轼"          # 导航不清空用户输入
    assert app.toolbar_search is search


def test_action_button_rebuilt_only_when_action_changes():
    app = _app()
    app._toolbar_content()
    btn = app._toolbar_action_btn
    app._toolbar_content()
    assert app._toolbar_action_btn is btn
    app._toolbar_spec = lambda: ("文稿", "3 篇", ("保存", ft.Icons.SAVE, lambda e: None))
    app._toolbar_content()
    assert app._toolbar_action_btn is not btn


def test_theme_and_zoom_update_styles_without_replacing_parts():
    app = _app()
    app._toolbar_content()
    search, title = app._toolbar_search, app._toolbar_title
    t2 = Tokens("light", 1.25, 96, "c")
    app.t = t2
    app._toolbar_content()
    assert app._toolbar_search is search and app._toolbar_title is title
    assert round(search.width, 2) == round(t2.px(236), 2)
    assert search.height == t2.control_h()
    assert title.size == t2.font("h1")
    app.t = Tokens("dark", 1.0, 96, "c")
    app._toolbar_content()
    assert title.color == app.t.palette.text
    # 批 114 收口（用户 m02855）：搜索框 + 外观图标装进白色圆角胶囊，
    # 描边由胶囊提供，输入框本身无框，视觉上是灰色工具栏上的一块白卡搜索区。
    assert search.bgcolor == app.t.palette.surface
    assert search.fill_color is None and search.filled is False
    assert search.border is None
    capsule = app._toolbar_search_card
    assert isinstance(capsule, ft.Container)
    assert capsule.bgcolor == app.t.palette.surface
    assert isinstance(capsule.border, ft.Border)
    assert capsule.border.top.color == app.t.palette.border
    assert capsule.border_radius == app.t.radius("pill")
    assert capsule.height == app.t.px(32)
    assert capsule.content.controls == [search, app._toolbar_tune]
    assert app._toolbar_tune.height == app.t.control_h()


def test_bib_toolbar_meta_uses_real_records_and_lint_warnings():
    """P1-1 回归：页头条数=stats.records、警告=lint.counts.warnings，不是 aliases/conflicts。"""
    from types import SimpleNamespace
    from app.ui.pages import BibPage

    page = SimpleNamespace(
        stats_data={"records": 3883, "aliases": {"records": 2081, "total": 2708},
                    "conflicts": {"records": 135, "total": 157}},
        lint_counts={"warnings": 76, "records": 3883, "errors": 0},
    )
    assert BibPage.toolbar_meta(page) == "3,883 条 · 76 条警告"

    page.lint_counts = None  # lint 未回时只显示条数
    assert BibPage.toolbar_meta(page) == "3,883 条"

    page.stats_data = None  # 未加载
    assert BibPage.toolbar_meta(page) == "运行中"


def test_search_capsule_focus_ring_switches_border_to_accent():
    """用户 m02855：搜索区是白卡胶囊，聚焦时描边换 accent、失焦恢复发丝边。"""
    app = _app()
    app._toolbar_content()
    card = app._toolbar_search_card
    assert card.border.top.color == app.t.palette.border
    app._set_search_focus(True)
    assert card.border.top.color == app.t.palette.accent
    app._set_search_focus(False)
    assert card.border.top.color == app.t.palette.border
