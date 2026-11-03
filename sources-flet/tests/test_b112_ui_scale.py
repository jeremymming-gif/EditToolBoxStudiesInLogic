"""批 112 S2b：GUI-09 按钮字号与正文同源缩放契约测试。

背景（用户 m01013 反馈）：按钮文本吃 Flet 客户端默认 `labelLarge`（固定值，不乘
`Tokens.k`），其余文本全部 `t.font(...)×k`（DPI 仿真 × 用户缩放）→ 缩放/DPI 偏离 1.0
时按钮与正文比例漂移；1.0 时也因字重/字距显得更大。

本文件锁三件事：
1. `App._flet_theme` 的 TextTheme 全量投影到 tokens 字号（body/label/title/headline/display）；
2. 按钮四个 helper 显式 `ButtonStyle(text_style=TextStyle(size=t.font("body"), W_500))`
   且 icon 尺寸过 `px()`；
3. 同一 role 随 zoom 线性放大（1.0× → 1.5× 字号 ×1.5）。

不启动 Flet 窗口。
"""
from __future__ import annotations

import flet as ft
import pytest

import main as M
from ui import components as C
from ui.tokens import LIGHT, Tokens


def _t(zoom: float = 1.0) -> Tokens:
    return Tokens("light", zoom, 96)


# ── 1. TextTheme 全量投影 ──────────────────────────────────────────────────
def test_text_theme_projects_every_token_role():
    """Material TextTheme 15 个槽位都必须有值，且字号来自 tokens（×k）。"""
    t = _t()
    theme = M.App._flet_theme(LIGHT, t)
    tt = theme.text_theme
    assert tt is not None
    slots = ("display_large", "display_medium", "display_small",
             "headline_large", "headline_medium", "headline_small",
             "title_large", "title_medium", "title_small",
             "body_large", "body_medium", "body_small",
             "label_large", "label_medium", "label_small")
    missing = [name for name in slots if getattr(tt, name) is None]
    assert missing == [], f"TextTheme 槽位缺投影: {missing}"
    assert tt.body_large.size == t.font("body")
    assert tt.label_large.size == t.font("body")
    assert tt.label_large.weight == ft.FontWeight.W_500
    assert tt.title_small.size == t.font("body")
    assert tt.body_medium.size == t.font("small")
    assert tt.body_small.size == t.font("caption")


def test_text_theme_scales_linearly_with_zoom():
    """同一 role 在 1.5× 窗口 = 1.0× 的 1.5 倍（用户「同步缩放」的实质）。"""
    t1, t15 = _t(1.0), _t(1.5)
    base = M.App._flet_theme(LIGHT, t1).text_theme
    big = M.App._flet_theme(LIGHT, t15).text_theme
    assert base.label_large.size == t1.font("body")
    assert big.label_large.size == t15.font("body")
    assert big.label_large.size == pytest.approx(base.label_large.size * 1.5, rel=1e-3)
    assert big.body_large.size == pytest.approx(base.body_large.size * 1.5, rel=1e-3)
    assert big.label_small.size == pytest.approx(base.label_small.size * 1.5, rel=1e-3)


# ── 2. 按钮 helper 显式锚定正文 role ─────────────────────────────────────
def test_button_helpers_anchor_body_role_and_icons():
    t = _t()
    for factory in (
        lambda: C.primary_button("运行", None, t),
        lambda: C.secondary_button("取消", None, t),
        lambda: C.text_button("更多", None, t),
    ):
        btn = factory()
        style = btn.style
        assert style is not None, f"{type(btn).__name__} 未设 ButtonStyle"
        assert style.text_style is not None
        assert style.text_style.size == t.font("body")
        assert style.text_style.weight == ft.FontWeight.W_500
        assert style.icon_size == t.px(14)

    icon_btn = C.icon_button("REFRESH", "刷新", None, t)
    assert icon_btn.icon_size == t.px(16)


def test_button_helpers_scale_with_zoom():
    one, two = _t(1.0), _t(1.25)
    btn_one = C.primary_button("运行", None, one)
    btn_two = C.primary_button("运行", None, two)
    assert btn_one.style.text_style.size == one.font("body")
    assert btn_two.style.text_style.size == pytest.approx(
        btn_one.style.text_style.size * 1.25, rel=1e-3)
    # 与同窗口的正文文本字号严格相等（按钮字 = 非按钮字）
    assert btn_two.style.text_style.size == two.font("body")


# ── 3. 防回退：按钮 helper 不接受裸默认值 ────────────────────────────────
def test_no_button_helper_without_style():
    """批 112 回归闸：4 个 helper 任一把 style 丢掉，这个用例就红。"""
    t = _t()
    built = [
        C.primary_button("a", None, t),
        C.secondary_button("b", None, t),
        C.text_button("c", None, t),
    ]
    assert all(item.style is not None for item in built)
    assert C.icon_button("REFRESH", "d", None, t).icon_size is not None
