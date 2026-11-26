"""换肤后新打开的对话框必须用当前 Tokens，不能用构建时的旧快照（严格 TDD 回归测试）。

真机复现顺序：关闭对话框 → 切深色 → 重新打开「编译修复」对话框 → 文字深底深色不可读。
根因两处串联：
  1. ``_SingleAiHost`` 在 __init__ 里 ``self.t = single.t`` 存了快照，切主题后视图
     的 ``self.t``（BibAiAssistView.t → host.t）仍旧指向浅色 Tokens；
  2. ``_compile_fix_controls`` 里 ``t = self.t`` 是构建期局部变量，延迟回调
     ``choose_scope`` 用它打开对话框，点按钮时已换肤仍是旧 Tokens。

本文件只用内存假宿主，不启真 GUI。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

_CANDIDATE = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(_CANDIDATE))

from ui import bib_ai_assist as BAA
from ui import compile_fix_dialog as CFD
from ui import pages as P
from ui.tokens import DARK, LIGHT, Tokens


# ── Test A：host 是 Tokens 的实时视图 ──────────────────────────────────────

def _fake_single(tokens: Tokens):
    """满足 _SingleAiHost.__init__ 校验的最小 single（app/store/mark/page/...）。"""
    app = SimpleNamespace(
        store=SimpleNamespace(set=lambda *a, **k: None),
        toast=lambda *a, **k: None,
        open_path=lambda path: True,
    )
    calls: list[tuple] = []

    def mark(event, **fields):
        calls.append((event, fields))

    def field(name):
        return f"/synthetic/{name}"

    def build_ai_initial_call(*, view, work_dir):
        return None

    return SimpleNamespace(
        app=app,
        t=tokens,
        mark=mark,
        field=field,
        _build_ai_initial_call=build_ai_initial_call,
        page=SimpleNamespace(
            theme_mode=None, controls=[],
            show_dialog=lambda dialog: None,
            updates=[],
        ),
        _calls=calls,
    )


def test_single_ai_host_t_is_live_after_theme_switch():
    """host.t 必须随 single.t 变化（切主题后视图读到新 Tokens）。"""
    old = Tokens("light", 1.0, 96)
    single = _fake_single(old)
    host = P._SingleAiHost(single)
    assert host.t is old

    single.t = Tokens("dark", 1.0, 96)

    assert host.t is single.t, "host.t 是构造期快照，切主题后没有跟随 single.t"


# ── Test B/C：延迟回调打开对话框时用 click 时刻的 Tokens ──────────────────

class _Page:
    def __init__(self):
        self.dialogs = []

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)


def _view_with_host(tokens: Tokens):
    """用真实 _SingleAiHost 包 fake single 构造 BibAiAssistView。"""
    single = _fake_single(tokens)
    host = P._SingleAiHost(single)
    view = BAA.BibAiAssistView(host, purpose="library", taskbook_version=3)
    settings = {"ai_enabled": True, "compile_fix_enabled": True, "key": "synthetic-key",
                "base": "https://example.invalid", "model": "synthetic", "protocol": "chat"}
    view._compile_fix_settings = lambda: settings.copy()
    view.can_apply = lambda: True
    view._task = lambda fn: None
    view._compile_fix_row = ft.Column()
    view.compile_fix_eligible = {}
    return view, single


def _result(status, **extra):
    return {"status": status, "task_id": "T1", "preview_id": "P1", "evidence_id": "E1",
            "candidate_sha256": "a" * 64,
            "evidence": [{"evidence_id": "D1"}], **extra}


def _click(view, label):
    def walk(control):
        if getattr(control, "content", None) == label and callable(getattr(control, "on_click", None)):
            return control
        for child in getattr(control, "controls", None) or []:
            found = walk(child)
            if found is not None:
                return found
        content = getattr(control, "content", None)
        if content is not None and content is not control and hasattr(content, "controls"):
            return walk(content)
        return None

    for row in view._compile_fix_row.controls:
        button = walk(row)
        if button is not None:
            result = button.on_click(SimpleNamespace(control=button))
            if inspect.isawaitable(result):
                __import__("asyncio").run(result)
            return
    raise AssertionError(f"按钮 {label!r} 未找到")


def test_scope_dialog_uses_current_tokens_at_click_time(monkeypatch):
    """范围对话框在点击时必须使用当前 Tokens，不是构建控件时的快照。"""
    old = Tokens("light", 1.0, 96)
    view, single = _view_with_host(old)
    view.task_id, view.preview_id, view.work_dir, view.phase = "T1", "P1", "/synthetic/work", "preview"
    view.request_seq, view._reply_revision, view.preview_revision = 7, 9, 3
    view.candidate_bib, view.backup = "candidate.bib", ""
    view._original_final_dir = view._final_dir = ""
    view._initial_open_stage = "compare"
    view.running = False
    view._set_compile_fix(_result("compile_failed", eligible=True))

    captured: dict = {}

    def fake_show_scope(page, t, *args, **kwargs):
        captured["t"] = t

    monkeypatch.setattr(CFD, "show_scope_dialog", fake_show_scope)

    # 构建控件后切主题（真实场景：关闭对话框 → 换肤 → 再点按钮）。
    single.t = new = Tokens("dark", 1.0, 96)
    assert view.t is new

    _click(view, "重新选择范围并授权…")

    assert captured.get("t") is new, (
        "choose_scope 打开对话框时用的是构建期捕获的旧 Tokens，而不是当前 self.t")


def test_reply_dialog_uses_current_tokens_at_click_time(monkeypatch):
    """导入回件对话框的延迟回调同样必须在点击时用当前 Tokens。"""
    old = Tokens("light", 1.0, 96)
    view, single = _view_with_host(old)
    view.task_id, view.preview_id, view.work_dir, view.phase = "T1", "P1", "/synthetic/work", "preview"
    view.request_seq, view._reply_revision, view.preview_revision = 7, 9, 3
    view.candidate_bib, view.backup = "candidate.bib", ""
    view._original_final_dir = view._final_dir = ""
    view._initial_open_stage = "compare"
    view.running = False
    view._set_compile_fix(_result("prepared"))

    captured: dict = {}

    def fake_show_reply(page, t, *args, **kwargs):
        captured["t"] = t

    monkeypatch.setattr(CFD, "show_reply_dialog", fake_show_reply)

    single.t = new = Tokens("dark", 1.0, 96)
    assert view.t is new

    _click(view, "导入本地回件…")

    assert captured.get("t") is new, (
        "导入回件回调打开对话框时用的是构建期捕获的旧 Tokens，而不是当前 self.t")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
