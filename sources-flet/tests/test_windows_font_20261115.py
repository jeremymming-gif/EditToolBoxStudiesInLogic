"""B-Windows：Windows 正文族与回退（2026-11-15）。

tokens 在 import 期按 sys.platform 定值；这里用「独立加载一份 tokens 模块」的方式
在无 GUI 下验证三个平台分支，以及 components.text / tstyle 的注入规则：

- win32 正文族 = "Microsoft YaHei UI"，显式回退 = ("Microsoft YaHei",)；
- macOS / Linux 正文族仍为 None，不注入回退；
- 调用方显式 font_family / font_family_fallback 优先；
- 等宽 / 日志角色只带等宽回退，不混入正文字体。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from ui import components as C
from ui import tokens as T


@pytest.fixture
def load_tokens(monkeypatch):
    """按平台独立加载 tokens，不 reload ui.tokens，避免换掉全局单例污染其它测试。"""
    def _load(platform: str):
        monkeypatch.setattr(sys, "platform", platform)
        name = f"_tokens_platform_{platform}"
        spec = importlib.util.spec_from_file_location(name, Path(T.__file__))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    return _load


def test_windows_body_font_and_fallback(load_tokens) -> None:
    tokens = load_tokens("win32")
    t = tokens.Tokens()
    assert t.font_family == "Microsoft YaHei UI"
    assert t.font_fallback == ("Microsoft YaHei",)
    # 单族名：Flet 的 font_family 不认逗号串（批 112 GUI-07）。
    assert "," not in t.font_family


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_non_windows_keeps_system_default(load_tokens, platform) -> None:
    tokens = load_tokens(platform)
    t = tokens.Tokens()
    assert t.font_family is None
    assert t.font_fallback == ()
    body = C.text("正文", t)
    assert body.font_family is None
    assert body.font_family_fallback is None


def test_text_injects_windows_body_family_and_fallback(load_tokens) -> None:
    tokens = load_tokens("win32")
    t = tokens.Tokens()
    body = C.text("正文", t)
    # C.text 也显式带族：控件样式不继承 Theme.font_family 时仍走同一字体。
    assert body.font_family == "Microsoft YaHei UI"
    assert body.font_family_fallback == ["Microsoft YaHei"]


def test_explicit_family_and_fallback_win(load_tokens) -> None:
    tokens = load_tokens("win32")
    t = tokens.Tokens()
    custom = C.text(
        "正文", t, font_family="Arial", font_family_fallback=["Arial Fallback"],
    )
    assert custom.font_family == "Arial"
    assert custom.font_family_fallback == ["Arial Fallback"]
    explicit = C.text("正文", t, font_family_fallback=["Explicit"])
    assert explicit.font_family_fallback == ["Explicit"]


def test_mono_role_does_not_take_body_fallback(load_tokens) -> None:
    tokens = load_tokens("win32")
    t = tokens.Tokens()
    log = C.text("日志", t, role="log")
    assert log.font_family == t.mono_family
    assert log.font_family_fallback == list(t.mono_fallback)
    assert "Microsoft YaHei" not in (log.font_family_fallback or [])


def test_tstyle_covers_explicit_styles(load_tokens) -> None:
    tokens = load_tokens("win32")
    t = tokens.Tokens()
    style = C.tstyle(t, size=t.font("body"))
    assert style.font_family == "Microsoft YaHei UI"
    assert style.font_family_fallback == ["Microsoft YaHei"]
    mono = C.tstyle(t, size=t.font("body"), font_family=t.mono_family,
                    font_family_fallback=list(t.mono_fallback))
    assert mono.font_family == t.mono_family
    assert mono.font_family_fallback == list(t.mono_fallback)


def test_tstyle_explicit_family_wins(load_tokens) -> None:
    tokens = load_tokens("win32")
    t = tokens.Tokens()
    custom = C.tstyle(t, font_family="Arial",
                      font_family_fallback=["Arial Fallback"])
    assert custom.font_family == "Arial"
    assert custom.font_family_fallback == ["Arial Fallback"]


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_tstyle_non_windows_keeps_system_default(load_tokens, platform) -> None:
    tokens = load_tokens(platform)
    t = tokens.Tokens()
    style = C.tstyle(t, size=t.font("body"))
    assert style.font_family is None
    assert style.font_family_fallback is None
