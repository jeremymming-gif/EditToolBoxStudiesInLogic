"""批 112 S1：字体 / 选中态排版契约（GUI-01 / GUI-07）。

覆盖：
- 字体 S1：正文族 None、等宽平台单族 + 显式回退、输入框主题字号 = 正文；
- 选中态字重唯一来源：TEXT_ROLE 四个 selected 角色全部 medium(W500)；
- 五处选中态都走角色（chip / nav 运行时，步骤卡 / tab / 配对卡静态契约）；
- 8 处 h3 旁路清零；gui_auto 断言与产品侧 item-title 对齐。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import flet as ft
import pytest

import main as M
from ui import components as C
from ui import tokens as T

APP_DIR = Path(__file__).resolve().parents[1] / "app"
SCENARIOS = Path(__file__).resolve().parent / "gui_auto" / "scenarios.py"

SELECTED_ROLES = {
    "item-selected": ("body", "medium"),
    "chip-selected": ("small", "medium"),
    "tab-selected": ("small", "medium"),
    "step-selected": ("body", "medium"),
}


# ── 字体 S1 ─────────────────────────────────────────────────────────────────
def test_body_font_family_is_none_not_comma_string() -> None:
    assert T.FONT_FAMILY is None
    assert T.Tokens().font_family is None
    # 历史 bug：逗号串被 Flet 当成单个族名（记住这个形状，别再写回去）。
    assert "," not in str(T.FONT_FAMILY or "")


@pytest.mark.parametrize("platform,family,fallback", [
    ("darwin", "Menlo", ("SF Mono", "Monaco")),
    ("win32", "Consolas", ("Cascadia Mono", "Courier New")),
    ("linux", "DejaVu Sans Mono", ("Liberation Mono", "Courier New")),
])
def test_mono_family_and_fallback_per_platform(platform, family, fallback) -> None:
    # tokens 在 import 期按 sys.platform 定值；这里验证「当前平台值 = 规范表」。
    expected = {"darwin": ("Menlo", ("SF Mono", "Monaco")),
                "win32": ("Consolas", ("Cascadia Mono", "Courier New")),
                "linux": ("DejaVu Sans Mono", ("Liberation Mono", "Courier New"))}
    current = "darwin" if sys.platform == "darwin" else (
        "win32" if sys.platform == "win32" else "linux")
    tokens = T.Tokens()
    assert (tokens.mono_family, tokens.mono_fallback) == expected[current]
    assert tokens.mono_family and tokens.mono_family != T.FONT_FAMILY
    assert isinstance(tokens.mono_fallback, tuple) and tokens.mono_fallback


def test_mono_text_carries_fallback() -> None:
    t = T.Tokens()
    label = C.text("日志", t, role="log")
    assert label.font_family == t.mono_family
    assert label.font_family_fallback == list(t.mono_fallback)
    # 非等宽文字不硬塞等宽族。
    assert C.text("正文", t, role="body").font_family in (None, t.font_family)
    assert t.mono_family == T.FONT_FAMILY_MONO


def test_theme_unifies_textfield_size_to_body() -> None:
    t = T.Tokens()
    theme = M.App._flet_theme(T.LIGHT)
    assert theme.font_family is None
    assert theme.text_theme is not None
    assert theme.text_theme.body_large is not None
    assert theme.text_theme.body_large.size == t.font("body") == 13
    # 带 Tokens 时按当前缩放取字号（不引入第二个真源）。
    zoomed = T.Tokens(zoom=1.25)
    assert M.App._flet_theme(T.LIGHT, zoomed).text_theme.body_large.size == zoomed.font("body")


# ── 选中态：唯一来源 TEXT_ROLE ──────────────────────────────────────────────
def test_selected_roles_all_medium() -> None:
    for role, expected in SELECTED_ROLES.items():
        assert T.TEXT_ROLE[role] == expected
        assert T.Tokens().text_role(role) == expected
        assert T.Tokens().weight(expected[1]) == "W_500"


def test_chip_and_nav_selected_weight_in_runtime() -> None:
    t = T.Tokens()
    active_chip = C.chip("查库", t, active=True, on_click=lambda e: None)
    idle_chip = C.chip("查库", t, active=False, on_click=lambda e: None)
    assert active_chip.content.weight == ft.FontWeight.W_500
    assert idle_chip.content.weight == ft.FontWeight.W_400
    assert active_chip.content.size == t.font("small") == idle_chip.content.size

    active_nav = C.nav_item("HOME", "工作台", t, active=True, on_click=lambda e: None)
    idle_nav = C.nav_item("HOME", "工作台", t, active=False, on_click=lambda e: None)
    assert active_nav.content.controls[1].weight == ft.FontWeight.W_500
    assert idle_nav.content.controls[1].weight == ft.FontWeight.W_400


def _read(rel: str) -> str:
    return (APP_DIR / rel).read_text(encoding="utf-8")


def test_step_rows_use_role_not_w600() -> None:
    # 批 114：步骤卡列表删除（改导航子页）；概览行/进度带仍走角色表而非裸字重。
    source = _read("ui/pages.py")
    assert 'role="step-selected"' not in source
    assert 'C.text(str(step["title"]), t, role="body"' in source
    assert 'weight="semibold"' not in source.split("def _overview_step_row")[1].split("def ")[0]


def test_tab_bar_uses_role() -> None:
    source = _read("ui/bib_advanced.py")
    assert 'role="tab-selected" if active else "small"' in source
    assert 'weight="medium" if active else "regular"' not in source


def test_pair_card_uses_role_not_semibold() -> None:
    source = _read("ui/bib_ops_dupes.py")
    assert 'role="item-selected" if active else "body"' in source
    assert 'weight="semibold" if active else "regular"' not in source


# ── h3 旁路清零（8 处）─────────────────────────────────────────────────────
def test_no_h3_weight_bypass_left() -> None:
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if '"h3", weight=' in text:
            offenders.append(path.relative_to(APP_DIR).as_posix())
    assert offenders == []


def test_eight_section_roles_present() -> None:
    expected = {
        "ui/pages.py": 3,
        "ui/checks.py": 3,
        "ui/bib_advanced.py": 1,
        "main.py": 1,
    }
    for rel, count in expected.items():
        source = _read(rel)
        # S1m：区块标题也可由 section_label 统一渲染（11.5/600），两者都算 section 角色。
        assert (source.count('role="section"') + source.count("C.section_label(")
                + source.count("self._section(") >= count), rel


# ── gui_auto 断言与产品侧对齐 ──────────────────────────────────────────────
def test_gui_auto_option_title_expects_w600() -> None:
    source = SCENARIOS.read_text(encoding="utf-8")
    assert re.search(r'选项名「编译 PDF」字重", label\.weight, ft\.FontWeight\.W_600', source)
    assert "产品侧 item-title=W600" in source
