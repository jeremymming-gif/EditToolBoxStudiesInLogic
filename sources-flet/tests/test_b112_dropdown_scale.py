"""批 112 GUI-10：下拉框「关闭态 vs 菜单项」字号同源契约测试。

用户反馈（m01752）：引用库 → 查库 → 基本检索，关闭态 `key` 明显大于展开菜单项的
`key/DOI/题名`。

根因（本文件锁定不许回退）：
1. `App.rebuild_shell()` 在缩放/DPI 变化时换了 `Tokens`，却没有把新字号重放到
   `page.theme/dark_theme` 的 TextTheme；客户端自绘文本（下拉菜单项等）停在旧 k，
   而控件自己的 `text_size` 已乘新 k → 同屏两套字号，比例恰为缩放比。
2. 下拉框没有同一处字号来源：关闭态看 `text_size/text_style`，菜单项看主题；
   任一不同步就错开。统一下拉框 helper 双写 text_style 与 DropdownOption.style。

不启动 Flet 窗口。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import flet as ft
import pytest

import main as M
from ui import components as C
from ui.tokens import LIGHT, Tokens


def _t(zoom: float = 1.0) -> Tokens:
    return Tokens("light", zoom, 96)


# ── 1. 统一下拉框 helper：关闭态与菜单项同字号 ────────────────────────────
def test_dropdown_helper_anchors_closed_and_menu_to_body():
    t = _t(1.6)
    dd = C.dropdown(t, value="key",
                    options=[("key", "key"), ("DOI", "DOI"), ("title", "题名")],
                    on_select=lambda e: None)
    body = t.font("body")
    assert dd.text_size == body
    assert dd.text_style is not None and dd.text_style.size == body
    assert dd.text_style.color == t.palette.text
    assert len(dd.options) == 3
    for opt in dd.options:
        assert opt.style is not None, "菜单项必须显式 style，否则吃客户端主题默认值"
        assert opt.style.text_style.size == body
        assert opt.style.text_style.color == t.palette.text


def test_dropdown_helper_item_size_scales_with_zoom():
    small, big = _t(1.0), _t(1.6)
    dd_small = C.dropdown(small, value="key", options=[("key", "key")])
    dd_big = C.dropdown(big, value="key", options=[("key", "key")])
    assert dd_big.text_style.size == pytest.approx(dd_small.text_style.size * 1.6, rel=1e-3)
    assert dd_big.options[0].style.text_style.size == pytest.approx(
        dd_small.options[0].style.text_style.size * 1.6, rel=1e-3)


# ── 2. 缩放后必须重放 page.theme（GUI-10 根因）────────────────────────────
def _app_stub(zoom: float) -> M.App:
    app = M.App.__new__(M.App)  # 不进入 __init__（会连引擎/设置/窗口）
    app.page = SimpleNamespace()
    app.theme = "light"
    app.t = Tokens("light", zoom, 96)
    return app


def test_apply_page_theme_projects_current_tokens():
    app = _app_stub(1.0)
    app._apply_page_theme()
    assert app.page.theme_mode == ft.ThemeMode.LIGHT
    assert app.page.theme.text_theme.body_large.size == app.t.font("body")
    assert app.page.dark_theme.text_theme.body_large.size == app.t.font("body")


def test_theme_reapplied_after_zoom_change():
    """模拟 rebuild_shell 换了 Tokens 后再重放：字号必须跟着走，不许停旧值。"""
    app = _app_stub(1.0)
    app._apply_page_theme()
    base = app.page.theme.text_theme.body_large.size
    app.t = Tokens("light", 1.6, 96)
    app._apply_page_theme()
    assert app.page.theme.text_theme.body_large.size == app.t.font("body")
    assert app.page.theme.text_theme.body_large.size == pytest.approx(base * 1.6, rel=1e-3)
    assert app.page.dark_theme.text_theme.label_large.size == app.t.font("body")


def test_rebuild_shell_reapplies_page_theme():
    """契约：缩放/DPI 重建路径必须调用 _apply_page_theme()。"""
    src = inspect.getsource(M.App.rebuild_shell)
    assert "_apply_page_theme()" in src


# ── 3. 被报告页面不再裸建 Dropdown ────────────────────────────────────────
def test_bib_query_page_uses_shared_dropdown_helper():
    from ui import bib_advanced as BA

    src = inspect.getsource(BA)
    assert "ft.Dropdown(" not in src, "查库页应统一走 C.dropdown（字号同源）"
    assert src.count("C.dropdown(") >= 4, "基本检索 2 个 + 高级检索 2 个"


# ── 4. GUI-16：下拉框高度与同排输入框齐平（Material 默认 48 不随 Tokens 缩放）────
def test_dropdown_helper_default_height_tracks_tokens():
    """用户反馈（m02432）：查库基本检索里左侧下拉框比右侧输入框大太多。

    锁死：默认高度随 Tokens 缩放（不再吃 Material 固定 48），且与 dense TextField
    同一量级（行高 + 上下留白），缩放后两边一起长。
    """
    small, big = _t(1.0), _t(1.6)
    dd_small = C.dropdown(small, value="k", options=[("k", "key")])
    dd_big = C.dropdown(big, value="k", options=[("k", "key")])

    def expected(t):
        return t.px(18) + t.font("body") * 1.5

    assert dd_small.height == pytest.approx(expected(small), rel=1e-3)
    assert dd_big.height == pytest.approx(expected(big), rel=1e-3)
    assert dd_big.height > dd_small.height, "下拉框必须随缩放一起变高"
    # 与同排 dense TextField 的可见高度同一量级（不许再出现「下拉高出一大截」）
    tf_like = big.font("body") * 1.2 + big.px(16)
    assert abs(dd_big.height - tf_like) <= big.px(10)


def test_dropdown_helper_explicit_height_and_padding():
    t = _t(1.0)
    dd = C.dropdown(t, value="k", options=[("k", "key")], height=t.px(44))
    assert dd.height == pytest.approx(t.px(44), rel=1e-3)
    # 收紧上下 padding：Material 默认留白太大，会让下拉比输入框肥
    pad = dd.content_padding
    assert pad is not None and pad.top <= t.px(8) + 0.5
