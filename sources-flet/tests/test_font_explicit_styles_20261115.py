"""B-Windows 返工：所有显式字体出口都带 Windows 正文族（2026-11-15）。

Flet 1.0.5 的 `Theme.font_family` 不穿透显式 `ft.TextStyle` /
`ButtonStyle.text_style`，也不作用于直接构造的 `ft.Text`。这里固定三件事：

- `C.tstyle()` 是显式 TextStyle 的唯一出口，`C.text()` 是 Text 的唯一出口；
- 按钮 / 输入框 / 浮动标签 / 开关 / 下拉当前值与菜单项都带上同一族与回退；
- 全仓 `app/**/*.py` 不再出现绕过出口且没带 `font_family` 的构造点。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from ui import components as C
from ui import tokens as T

SOURCE = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def load_tokens(monkeypatch):
    """按平台独立加载 tokens，不 reload ui.tokens，避免换掉全局单例污染其它测试。"""
    def _load(platform: str):
        monkeypatch.setattr(sys, "platform", platform)
        name = f"_tokens_explicit_{platform}"
        spec = importlib.util.spec_from_file_location(name, Path(T.__file__))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    return _load


def _assert_win_family(style) -> None:
    assert style.font_family == "Microsoft YaHei UI"
    assert style.font_family_fallback == ["Microsoft YaHei"]


def test_button_style_carries_family(load_tokens) -> None:
    t = load_tokens("win32").Tokens()
    btn = C.primary_button("更换", None, t)
    _assert_win_family(btn.style.text_style)


def test_fields_and_switch_carry_family(load_tokens) -> None:
    t = load_tokens("win32").Tokens()
    field = C.text_field(t, value="2026年第5期")
    _assert_win_family(field.hint_style)
    _assert_win_family(field.text_style)
    floating = C.floating_field(t, "期目录", value="2026年第5期")
    _assert_win_family(floating.label_style)
    _assert_win_family(floating.hint_style)
    _assert_win_family(floating.text_style)
    toggle = C.switch(t, label="启用")
    _assert_win_family(toggle.label_text_style)


def test_mono_field_keeps_mono_family(load_tokens) -> None:
    t = load_tokens("win32").Tokens()
    field = C.text_field(t, value="a, b", mono=True)
    assert field.text_style.font_family == t.mono_family
    assert field.text_style.font_family_fallback == list(t.mono_fallback)


def test_select_value_and_items_carry_family(load_tokens) -> None:
    t = load_tokens("win32").Tokens()
    sel = C.Select(t, value="b", options=[
        {"key": "a", "text": "甲"}, {"key": "b", "text": "乙"},
    ])
    _assert_win_family(sel._value_text)
    item_texts = [item.content.content for item in sel.items]
    assert item_texts
    for item_text in item_texts:
        _assert_win_family(item_text)


def test_explicit_styles_omit_family_on_non_windows(load_tokens) -> None:
    t = load_tokens("linux").Tokens()
    btn = C.primary_button("更换", None, t)
    assert btn.style.text_style.font_family is None
    assert btn.style.text_style.font_family_fallback is None
    field = C.text_field(t, value="x")
    assert field.text_style.font_family is None
    assert field.text_style.font_family_fallback is None
    sel = C.Select(t, value="a", options=[{"key": "a", "text": "甲"}])
    assert sel._value_text.font_family is None
    assert sel._value_text.font_family_fallback is None
    assert sel.items[0].content.content.font_family is None


# ── 静态闸：新增字体构造点必须走出口或显式带族 ──────────────────────────────
def _enclosing_factory(lines: list[str], index: int) -> str:
    """向上找 module 级 def；text()/tstyle() 内部允许构造 ft.Text/TextStyle。"""
    for line in reversed(lines[: index + 1]):
        match = re.match(r"def\s+(\w+)\s*\(", line)
        if match:
            return match.group(1)
    return ""


def _call_source(lines: list[str], index: int) -> str:
    """从构造行读到括号闭合（最多 40 行），用于判断是否带 font_family。"""
    depth = 0
    for end in range(index, min(index + 40, len(lines))):
        code = lines[end].split("#", 1)[0]
        depth += code.count("(") - code.count(")")
        if depth <= 0:
            return "\n".join(lines[index:end + 1])
    return "\n".join(lines[index:index + 40])


def test_every_font_construction_is_covered() -> None:
    missing: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            for kind, factory in (("Text", "text"), ("TextStyle", "tstyle")):
                if f"ft.{kind}(" not in line.split("#", 1)[0]:
                    continue
                if _enclosing_factory(lines, index) == factory:
                    continue
                if "font_family" not in _call_source(lines, index):
                    missing.append(f"{path.relative_to(SOURCE)}:{index + 1}")
    assert not missing, "显式字体构造漏了 font_family: " + ", ".join(missing)
