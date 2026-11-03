"""批 113 E → 批 114 F1：全 app 表单控件统一口径（用户 m01257）。

新口径（批 114 用户 m02696：不要灰底块）：
* 单行输入 / 下拉 / 单行按钮同高 = `t.control_h()`（28 设计 px）；
* 纸色底（`palette.surface`）+ 1px 发丝描边（`_floating_border`），不再填充
  `palette.surface_2` 灰块；圆角 `t.radius("control")`、正文 13；
* 浮动 label 改成 `form_field()` 上置 12 muted；
* 不再裸建 Material `ft.TextField` / `ft.Dropdown`，也不再出现 48 高默认边框。

本文件锁结构契约，不启动 GUI。
"""
from __future__ import annotations

import ast
from pathlib import Path

import flet as ft
import pytest

from ui import components as C
from ui.tokens import Tokens

APP = Path(__file__).resolve().parents[1] / "app"
DEPRECATED = {"border_color", "focused_border_color"}


def _py_files() -> list[Path]:
    return sorted(path for path in APP.rglob("*.py") if "__pycache__" not in path.parts)


def _t(theme: str = "light", zoom: float = 1.0) -> Tokens:
    return Tokens(theme, zoom, 96)


def _contrast(a: str, b: str) -> float:
    def lum(color: str) -> float:
        value = color.lstrip("#")
        rgb = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ── 1. 旧口径彻底退场 ────────────────────────────────────────────────────
def test_no_deprecated_border_kwargs_anywhere():
    offenders = []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in DEPRECATED:
                        offenders.append(f"{path.relative_to(APP)}:{node.lineno}:{kw.arg}=")
    assert offenders == [], "仍有弃用参数：" + ", ".join(offenders)


def test_no_raw_material_form_controls_outside_helpers():
    """除 components.py 的 helper 外，页面不许再裸建 TextField / Dropdown。"""
    offenders: list[str] = []
    legacy = ("OutlineInputBorder(", "UnderlineInputBorder(", "ft.InputBorder.NONE")
    for path in _py_files():
        if path.name == "components.py":
            continue
        source = path.read_text(encoding="utf-8")
        for needle in legacy:
            if needle in source:
                offenders.append(f"{path.relative_to(APP)}: {needle}")
        for needle in ("ft.TextField(", "ft.Dropdown(", "ft.Switch("):
            if needle in source:
                offenders.append(f"{path.relative_to(APP)}: {needle}")
    assert offenders == [], "仍有裸建/旧口径控件：" + ", ".join(offenders)


# ── 2. helper 几何口径 ───────────────────────────────────────────────────
def test_text_field_geometry_matches_unified_spec():
    t = _t()
    field = C.text_field(t, value="x")
    assert field.height == pytest.approx(t.control_h(), rel=1e-3)
    # 批 114：不要灰底块 —— 不填充、用 1px 描边（深浅色都走 Tokens 语义色）。
    assert field.filled is False and field.fill_color is None
    assert field.bgcolor != t.palette.surface_2
    assert isinstance(field.border, dict)
    default_border = field.border.get(ft.ControlState.DEFAULT)
    assert isinstance(default_border, ft.OutlineInputBorder)
    assert default_border.side.width == pytest.approx(t.px(1))
    assert default_border.border_radius == pytest.approx(t.radius("control"), rel=1e-3)
    assert field.text_size == pytest.approx(t.font("body"), rel=1e-3)
    # 描边盒的可视高度 = 文本行 + 上下内边距（Material InputDecorator 不认外层 height）：
    # 关 dense、上下各 6 设计 px，实测与 control_h 等高（批 114 用户 m02816：输入框只有按钮一半高）。
    assert field.dense is False
    assert field.content_padding.top == pytest.approx(t.px(6), abs=0.51)
    assert field.content_padding.bottom == pytest.approx(t.px(6), abs=0.51)
    assert field.content_padding.left == pytest.approx(t.px(8), rel=1e-3)


def test_select_geometry_and_menu_items_are_28_high():
    t = _t()
    select = C.select(t, value="b", options=[("a", "A"), ("b", "B")])
    assert select.height == pytest.approx(t.control_h(), rel=1e-3)
    assert select.content.height == pytest.approx(t.control_h(), rel=1e-3)
    assert select.content.bgcolor == t.palette.surface
    assert isinstance(select.content.border, ft.Border)
    assert select.content.border_radius == pytest.approx(t.radius("control"), rel=1e-3)
    assert len(select.items) == 2
    for item in select.items:
        assert item.height == pytest.approx(t.control_h(), rel=1e-3)
        assert isinstance(item.content, ft.Container)
        assert item.content.height == pytest.approx(t.control_h(), rel=1e-3)


def test_same_class_controls_share_one_height_across_theme_and_zoom():
    """同类控件高度一致：输入/下拉在深浅色、各缩放下都等于 control_h。"""
    heights = set()
    for theme in ("light", "dark"):
        for zoom in (1.0, 1.25, 1.6):
            t = _t(theme, zoom)
            field = C.text_field(t)
            select = C.select(t, options=[("a", "A")])
            assert field.height == pytest.approx(t.control_h(), rel=1e-3)
            assert select.height == pytest.approx(t.control_h(), rel=1e-3)
            assert select.content.height == pytest.approx(t.control_h(), rel=1e-3)
            heights.add(round(field.height - select.height, 6))
    assert heights == {0.0}


def test_select_value_is_programmatically_settable():
    """toc / bib_advanced 依赖 .value 赋值（旧 Dropdown 行为）。"""
    t = _t()
    select = C.select(t, options=[("a", "A"), ("b", "B")])
    select.value = "b"
    assert select.value == "b"
    assert select._value_text.value == "B"
    select.options = [("c", "C")]
    select.value = "c"
    assert select._value_text.value == "C"


def test_form_field_makes_single_line_field_floating():
    """form_field 对单行可编辑 TextField 直接改造成描边浮动标签（m02483）。"""
    t = _t()
    field = C.text_field(t)
    wrapped = C.form_field(t, "卷", field, required=True)
    assert isinstance(wrapped, ft.TextField)
    assert wrapped is field
    assert wrapped.label == "卷（必填）"
    assert wrapped._form_label == "卷"
    assert wrapped.align_label_with_hint is True
    assert wrapped.height == pytest.approx(t.px(48))
    assert wrapped.filled is False
    assert isinstance(wrapped.border, dict)
    default_border = wrapped.border.get(ft.ControlState.DEFAULT)
    focused_border = wrapped.border.get(ft.ControlState.FOCUSED)
    assert isinstance(default_border, ft.OutlineInputBorder)
    assert default_border.side.width == pytest.approx(t.px(1))
    assert focused_border.side.color == t.palette.accent


def test_form_field_keeps_stack_for_non_text_control():
    """非 TextField（下拉/多行/只读）仍走「标签上置」布局。"""
    t = _t()
    box = C.select_box(t, "语种", value_text="自动判断", options=[("", "自动判断")])
    wrapped = C.form_field(t, "语种", box)
    assert isinstance(wrapped, ft.Column)
    assert wrapped.controls[0].controls[0].value == "语种"
    assert wrapped.controls[1] is box
    assert box._form_label == "语种"


# ── 3. 深浅色开关：浅色轨道对纸面 ≥ 3:1 ───────────────────────────────────
def test_switch_has_explicit_four_state_colors_and_light_contrast():
    t = _t()
    switch = C.switch(t, value=False)
    assert isinstance(switch.track_color, dict)
    assert set(switch.track_color) == {
        ft.ControlState.DEFAULT, ft.ControlState.SELECTED,
        ft.ControlState.DISABLED, ft.ControlState.HOVERED,
    }
    assert switch.track_color[ft.ControlState.DEFAULT] == t.palette.text_muted
    assert switch.track_color[ft.ControlState.SELECTED] == t.palette.accent
    assert _contrast(switch.track_color[ft.ControlState.DEFAULT], t.palette.surface) >= 3.0
    assert _contrast(switch.track_color[ft.ControlState.DEFAULT], t.palette.surface_2) >= 3.0
    assert switch.thumb_color[ft.ControlState.SELECTED] == t.palette.on_accent
