"""批 114 验收期：高级参数输入约束 + 可自定义下拉（用户 m02483）。"""
from __future__ import annotations

import flet as ft
import pytest

from ui import components as C
from ui import pages as P
from ui.tokens import Tokens


def _t() -> Tokens:
    return Tokens("light", 1.0, 144)


# ── 输入格式校验 ───────────────────────────────────────────────────────────
def test_valid_digits():
    assert P._valid_digits("") is None
    assert P._valid_digits("12") is None
    assert P._valid_digits("1a") == "只填数字"


def test_valid_year():
    assert P._valid_year("") is None
    assert P._valid_year("2025") is None
    assert P._valid_year("25") == "年份写 4 位数字，如 2025"
    assert P._valid_year("1899") == "年份应在 1900–2100"
    assert P._valid_year("2101") == "年份应在 1900–2100"


def test_valid_pages():
    assert P._valid_pages("") is None
    assert P._valid_pages("137-152") is None
    assert P._valid_pages("12,15-18") is None
    assert P._valid_pages("12–18") is None
    assert P._valid_pages("abc") is not None
    assert P._valid_pages("12-") is not None


def test_valid_date():
    assert P._valid_date("") is None
    assert P._valid_date("2025-09-19") is None
    assert P._valid_date("2025-13-01") == "日期不存在"
    assert P._valid_date("2025/09/19") == "日期写法如 2025-09-19"


# ── 48 高描边下拉（PopupMenuButton） ───────────────────────────────────────
def test_select_box_renders_popup_button_with_options():
    t = _t()
    picked: list[tuple[str, str]] = []
    box = C.select_box(t, "语种", value_text="自动判断",
                       options=[("", "自动判断"), ("zh", "中文")],
                       on_select=lambda k, v: picked.append((k, v)))
    assert isinstance(box, ft.PopupMenuButton)
    assert box._form_label == "语种"
    assert box._value_text.value == "自动判断"
    assert len(box.items) == 2
    box.items[1].on_click(None)
    assert picked == [("zh", "中文")]


def test_form_field_migrates_other_pages_to_floating_border():
    """非 pages.py 的普通表单字段也走浮动描边（推广 m02483）。"""
    t = _t()
    field = C.text_field(t)
    wrapped = C.form_field(t, "作者", field)
    assert wrapped is field
    assert isinstance(wrapped.border, dict)
    assert wrapped.height == pytest.approx(t.px(48))
