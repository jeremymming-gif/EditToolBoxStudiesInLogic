"""批 112 GUI-11：TextTheme 非按钮槽位必须有显式前景色。

用户反馈（m01975）：引用库 → 查库 → 基本检索，输入框内已输入字符是白色，与浅色
背景撞色不可读。

根因：批 112 GUI-09 把 Material TextTheme 15 槽位投影成 tokens 字号时只给了 size，
color=None；TextField 的输入文字读到无颜色的 bodyLarge 后落回客户端默认前景色
（深色主题的浅色），而页面背景是浅色 → 白字白底。

契约：display/headline/title/body 槽位 color = palette.text；label 槽位保持无色
（按钮前景由控件自身 color / ButtonStyle 决定，避免把主按钮文字染成正文色）。
"""
from __future__ import annotations

import flet as ft

import main as M
from ui.tokens import DARK, LIGHT, Tokens


def _themes(theme: str):
    t = Tokens(theme, 1.0, 96)
    return M.App._flet_theme(LIGHT if theme == "light" else DARK, t)


_TEXT_SLOTS = (
    "display_large", "display_medium", "display_small",
    "headline_large", "headline_medium", "headline_small",
    "title_large", "title_medium", "title_small",
    "body_large", "body_medium", "body_small",
)
_LABEL_SLOTS = ("label_large", "label_medium", "label_small")


def test_text_slots_carry_palette_text_color():
    for theme, palette in (("light", LIGHT), ("dark", DARK)):
        tex = _themes(theme).text_theme
        for slot in _TEXT_SLOTS:
            style = getattr(tex, slot)
            assert style is not None, f"{theme}.{slot} 缺失"
            assert style.color == palette.text, f"{theme}.{slot} 缺显式前景色"


def test_body_large_color_enables_readable_textfield_input():
    """TextField 输入文字取 bodyLarge；浅色主题必须是深色前景。"""
    tex = _themes("light").text_theme
    assert tex.body_large.color == LIGHT.text
    assert tex.body_large.color != DARK.text


def test_label_slots_keep_widget_color():
    """label_* 不写死颜色，按钮前景仍由控件自身 color/style 决定。"""
    tex = _themes("light").text_theme
    for slot in _LABEL_SLOTS:
        style = getattr(tex, slot)
        assert style is not None
        assert style.color is None, f"{slot} 不应写死颜色（会污染按钮前景）"


def test_checkbox_theme_projects_palette_accent():
    """批 112 A-03：复选框走主题投影，不再落客户端 1px 黑框。"""
    for theme, palette in (("light", LIGHT), ("dark", DARK)):
        theme_obj = _themes(theme)
        assert theme_obj.checkbox_theme is not None, f"{theme} 缺 checkbox_theme"
        box = theme_obj.checkbox_theme
        assert box.fill_color == palette.accent
        assert box.check_color == palette.on_accent
        assert box.border_side is not None and box.border_side.width > 0
        assert box.shape is not None
