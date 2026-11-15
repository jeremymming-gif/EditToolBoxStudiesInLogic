"""B-Windows：Windows 正文族与回退（2026-11-15）。

tokens 在 import 期按 sys.platform 定值；这里用 importlib.reload + monkeypatch
在无 GUI 下验证三个平台分支，以及 components.text 唯一出口的注入规则：

- win32 正文族 = "Microsoft YaHei UI"，显式回退 = ("Microsoft YaHei",)；
- macOS / Linux 正文族仍为 None，不注入回退；
- 调用方显式 font_family / font_family_fallback 优先；
- 等宽 / 日志角色只带等宽回退，不混入正文字体。
"""
from __future__ import annotations

import importlib
import sys

import pytest

from ui import components as C
from ui import tokens as T


@pytest.fixture
def reload_tokens(monkeypatch):
    """按需重载 tokens；teardown 恢复真实平台并重载，避免污染其它测试。"""
    original = sys.platform

    def _reload(platform: str):
        monkeypatch.setattr(sys, "platform", platform)
        return importlib.reload(T)

    yield _reload
    monkeypatch.setattr(sys, "platform", original)
    importlib.reload(T)


def test_windows_body_font_and_fallback(reload_tokens) -> None:
    reload_tokens("win32")
    t = T.Tokens()
    assert t.font_family == "Microsoft YaHei UI"
    assert t.font_fallback == ("Microsoft YaHei",)
    # 单族名：Flet 的 font_family 不认逗号串（批 112 GUI-07）。
    assert "," not in t.font_family


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_non_windows_keeps_system_default(reload_tokens, platform) -> None:
    reload_tokens(platform)
    t = T.Tokens()
    assert t.font_family is None
    assert t.font_fallback == ()
    body = C.text("正文", t)
    assert body.font_family is None
    assert body.font_family_fallback is None


def test_text_injects_windows_body_fallback(reload_tokens) -> None:
    reload_tokens("win32")
    t = T.Tokens()
    body = C.text("正文", t)
    # 正文族由主题统一给，控件不重复写；只补同类回退。
    assert body.font_family is None
    assert body.font_family_fallback == ["Microsoft YaHei"]


def test_explicit_family_and_fallback_win(reload_tokens) -> None:
    reload_tokens("win32")
    t = T.Tokens()
    custom = C.text(
        "正文", t, font_family="Arial", font_family_fallback=["Arial Fallback"],
    )
    assert custom.font_family == "Arial"
    assert custom.font_family_fallback == ["Arial Fallback"]
    explicit = C.text("正文", t, font_family_fallback=["Explicit"])
    assert explicit.font_family_fallback == ["Explicit"]


def test_mono_role_does_not_take_body_fallback(reload_tokens) -> None:
    reload_tokens("win32")
    t = T.Tokens()
    log = C.text("日志", t, role="log")
    assert log.font_family == t.mono_family
    assert log.font_family_fallback == list(t.mono_fallback)
    assert "Microsoft YaHei" not in (log.font_family_fallback or [])
