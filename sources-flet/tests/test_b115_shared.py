"""批 115 P3-C：S1m-native 共享件（vline / hairline / kv_row）+ 控件主题重放。

锁三件事，不启动 GUI：
1. 三个 helper 的签名 / 几何 / 配色只走 `Tokens`·`Palette`（源码无写死 hex）；
2. `main.App._flet_theme` 的 Switch / Checkbox / Slider / Radio 四类控件主题
   从色板强调色投影，light + dark 各一份，禁 `color_scheme_seed` / `use_material3`；
3. 热切换（retint 不重建）也重放 `_apply_page_theme()`，控件主题不留在旧皮肤。

`C.card` 的既有 API 不动（新页面不再用它，但旧调用方/测试仍在）。
"""
from __future__ import annotations

import ast
import inspect
import re
from dataclasses import fields
from pathlib import Path

import flet as ft
import pytest

import main as M
from ui import components as C
from ui.tokens import DARK, LIGHT, Tokens

APP = Path(__file__).resolve().parents[1] / "app"
HEX = re.compile(r"^#[0-9A-Fa-f]{3,8}$")


def _t(theme: str = "light", zoom: float = 1.0) -> Tokens:
    return Tokens(theme, zoom, 96)


def _py_source(name: str) -> str:
    return (APP / name).read_text(encoding="utf-8")


def _func_node(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"找不到函数 {name}")


def _func_source(source: str, name: str) -> str:
    node = _func_node(source, name)
    return ast.get_source_segment(source, node) or ""


# ── 1. 共享件契约 ─────────────────────────────────────────────────────────
def test_helpers_exist_with_stable_signatures():
    vline = inspect.signature(C.vline).parameters
    assert list(vline) == ["t", "height"]
    assert vline["height"].default == 16

    assert list(inspect.signature(C.hairline).parameters) == ["t"]

    kv = inspect.signature(C.kv_row).parameters
    assert list(kv) == ["t", "label", "value", "mono"]
    assert kv["mono"].default is False


@pytest.mark.parametrize(("theme", "zoom"), (("light", 1.0), ("dark", 1.0),
                                             ("light", 3.0), ("dark", 0.8)))
def test_vline_is_one_design_px_tone_line(theme, zoom):
    t = _t(theme, zoom)
    line = C.vline(t, 28)
    assert line.width == max(1, round(1 * t.k))     # 缩放 <1 也保 1 物理 px
    assert line.height == pytest.approx(t.px(28))   # 高度按设计像素缩放
    assert line.bgcolor == t.palette.border
    assert line.data == "tone:border"               # 换肤按字段名精确改写
    assert C.vline(t).height == pytest.approx(t.px(16))


@pytest.mark.parametrize(("theme", "zoom"), (("light", 1.0), ("dark", 1.0), ("dark", 3.0)))
def test_hairline_stretches_and_carries_tone(theme, zoom):
    t = _t(theme, zoom)
    line = C.hairline(t)
    assert line.width is None                       # 不写死宽度：父 Column STRETCH
    assert line.height == C.vline(t).width          # 与竖线同一条发丝
    assert line.bgcolor == t.palette.border
    assert line.data == "tone:border"


def test_kv_row_geometry_and_right_aligned_value():
    t = _t("light")
    row = C.kv_row(t, "版本", "v1.5.0")
    assert row.height == pytest.approx(t.row_h("table"))
    assert row.height == pytest.approx(t.px(32))

    inner = row.content
    assert isinstance(inner, ft.Row)
    assert inner.vertical_alignment == ft.CrossAxisAlignment.CENTER
    label, spacer, slot = inner.controls

    assert isinstance(label, ft.Text) and label.value == "版本"
    assert label.color == t.palette.text_muted
    assert label.max_lines == 1 and label.overflow == ft.TextOverflow.ELLIPSIS

    assert spacer.expand is True and slot.expand is True
    assert slot.alignment == ft.Alignment.CENTER_RIGHT
    value = slot.content
    assert value.value == "v1.5.0"
    assert value.color == t.palette.text
    assert value.max_lines == 1 and value.overflow == ft.TextOverflow.ELLIPSIS
    assert value.tooltip == "v1.5.0"


def test_kv_row_mono_tooltip_and_empty_value():
    t = _t("light")
    mono = C.kv_row(t, "路径", "/tmp/out/稿件.pdf", mono=True)
    value = mono.content.controls[2].content
    assert value.font_family == t.mono_family
    assert list(value.font_family_fallback or []) == list(t.mono_fallback)
    assert value.tooltip == "/tmp/out/稿件.pdf"

    empty = C.kv_row(t, "备注", None)
    empty_value = empty.content.controls[2].content
    assert empty_value.value == "" and empty_value.tooltip is None


def test_kv_row_scales_with_tokens():
    small = C.kv_row(_t("light", 1.0), "a", "b").height
    big = C.kv_row(_t("light", 1.6), "a", "b").height
    assert small < big
    assert small == pytest.approx(_t("light", 1.0).row_h("table"))


def test_helpers_have_no_hardcoded_hex():
    source = (APP / "ui" / "components.py").read_text(encoding="utf-8")
    for name in ("vline", "hairline", "kv_row"):
        node = _func_node(source, name)
        literals = [n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        offenders = [s for s in literals if HEX.match(s)]
        assert offenders == [], f"C.{name} 写死颜色：{offenders}"
        snippet = _func_source(source, name)
        assert "palette" in snippet or "_side(t)" in snippet

    kv_src = _func_source(source, "kv_row")
    assert "t.row_h(" in kv_src and "t.sp(" in kv_src and "palette" in kv_src


# ── 2. 控件主题：从 Palette 投影 + light/dark 各一份 ──────────────────────
@pytest.mark.parametrize(("palette", "theme"), ((LIGHT, "light"), (DARK, "dark")))
def test_control_themes_projected_from_palette(palette, theme):
    t = Tokens(theme, 1.0, 96)
    flet_theme = M.App._flet_theme(palette, t)

    switch = flet_theme.switch_theme
    assert isinstance(switch, ft.SwitchTheme)
    assert switch.track_color[ft.ControlState.SELECTED] == palette.accent
    assert switch.thumb_color[ft.ControlState.SELECTED] == palette.on_accent
    assert switch.track_color[ft.ControlState.DEFAULT] == palette.text_muted
    assert switch.track_outline_color[ft.ControlState.SELECTED] == palette.accent

    slider = flet_theme.slider_theme
    assert isinstance(slider, ft.SliderTheme)
    assert slider.active_track_color == palette.accent
    assert slider.inactive_track_color == palette.border
    assert slider.thumb_color == palette.accent
    assert slider.value_indicator_color == palette.accent

    radio = flet_theme.radio_theme
    assert isinstance(radio, ft.RadioTheme)
    assert radio.fill_color[ft.ControlState.SELECTED] == palette.accent
    assert radio.fill_color[ft.ControlState.DEFAULT] == palette.border_strong

    assert isinstance(flet_theme.checkbox_theme, ft.CheckboxTheme)
    assert flet_theme.checkbox_theme.fill_color[ft.ControlState.SELECTED] == palette.accent


def _theme_colors(obj) -> list[tuple[str, str]]:
    """收集主题对象里的颜色串（状态映射只取 value）。"""
    found: list[tuple[str, str]] = []
    for name, value in vars(obj).items():
        if isinstance(value, str) and (HEX.match(value) or value == "transparent"):
            found.append((name, value))
        elif isinstance(value, dict):
            for state, color in value.items():
                if isinstance(color, str) and (HEX.match(color) or color == "transparent"):
                    found.append((f"{name}[{state}]", color))
    return found


@pytest.mark.parametrize("palette", (LIGHT, DARK))
def test_control_theme_colors_all_come_from_palette(palette):
    t = Tokens("light" if palette is LIGHT else "dark", 1.0, 96)
    flet_theme = M.App._flet_theme(palette, t)
    allowed = {getattr(palette, field.name) for field in fields(palette)} | {"transparent"}
    offenders = []
    for theme_name in ("switch_theme", "checkbox_theme", "slider_theme", "radio_theme"):
        for path, color in _theme_colors(getattr(flet_theme, theme_name)):
            if color not in allowed:
                offenders.append(f"{theme_name}.{path}={color}")
    assert offenders == [], "控件主题出现色板外颜色：" + ", ".join(offenders)


def test_theme_is_replayed_on_hot_switch():
    source = _py_source("main.py")
    apply_src = _func_source(source, "_apply_page_theme")
    assert "page.theme = " in apply_src
    assert "page.dark_theme = " in apply_src
    assert "_flet_theme(LIGHT" in apply_src and "_flet_theme(DARK" in apply_src

    # 热切换走 retint 不重建，也必须重放两套主题对象（审计 §2.4 gap3）。
    set_theme_src = _func_source(source, "set_theme")
    assert "_apply_page_theme()" in set_theme_src


def test_theme_bans_material_seed_sources():
    source = _py_source("main.py")
    for token in ("color_scheme_seed", "use_material3"):
        assert token not in source, f"main.py 不应再出现 {token}"
    for theme_name in ("switch_theme=", "checkbox_theme=", "slider_theme=", "radio_theme="):
        assert theme_name in source, f"main.py 缺少 {theme_name}"


def test_card_api_is_unchanged():
    params = inspect.signature(C.card).parameters
    assert list(params) == ["content", "t", "pad", "bgcolor", "bordered",
                            "radius", "expand", "width", "on_click"]
    assert params["pad"].default == 16 and params["bordered"].default is True
