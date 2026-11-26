"""task-3：普通初排转换编译失败 → AI 辅助编译修复入口（离线契约）。

先证据后实现的结论：普通转换失败只回退出码 2（Engine/convert.py 741-746），
不产出 AI 工作流那套签名证据（preview_id / evidence_id / authorization_id）。
所以本入口 fail-closed：按既有 ``EB.compile_fix_settings`` 单一闸门分流——
未开开关 / 无本机 Key → 去设置；齐全 → 引导进唯一能产出真证据的
「AI 辅助初排」预览流程。绝不伪造 ID、绝不自动调用 prepare/send。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft

from services import engine_bridge as EB
from ui import help_content as HC
from ui import pages as P
from test_b114_polish import _FakeApp, _button_contents, _texts, _walk


class _App(_FakeApp):
    """在 b114 的假壳上补 `_run` 所需钩子（不启 GUI、不跑真引擎）。"""

    def __init__(self) -> None:
        super().__init__()
        self.last_line = ""
        self.cancel_requested = False
        self.notes_repo = ""
        self.begun = []
        self.finished = []
        self.gotos: list[str] = []

    def set_option(self, key: str, value) -> None:
        self.store.set(f"options.{key}", value)

    def begin_run(self, call) -> None:
        self.running = True
        self.cancel_requested = False
        self.last_line = ""
        self.begun.append(call)

    def finish_run(self, call, code) -> None:
        self.running = False
        self.finished.append((call, code))

    def track_proc(self, proc) -> None:
        pass

    def copy_text(self, value) -> None:
        pass

    def goto(self, key: str) -> None:
        self.gotos.append(key)


def _build(app=None):
    app = app or _App()
    page = P.SinglePage(app)
    root = page.build()
    page.set_field("single_source", "/tmp/anonymous/article.tex")
    page.set_field("single_out", "/tmp/anonymous/delivery")
    return page, root


def _install_run(monkeypatch, page, code):
    async def fake_run_call(call, *, on_line=None, on_proc=None, api=None,
                            notes="", stdin_text=None, **kwargs):
        page.app.last_line = ""
        return code

    monkeypatch.setattr(EB, "run_call", fake_run_call)


def _enable_ai(app):
    app.store.set("ai", {"enabled": True, "compile_fix_enabled": True,
                         "key": "sk-local-test-key"})


def _forbid_send(monkeypatch):
    """任何 prepare/send 企图都立刻失败——普通转换入口不得触发联网。"""
    seen = []

    def boom(**kwargs):
        seen.append(kwargs)
        raise AssertionError("普通转换入口不得调用 ai_compile_fix_call")

    monkeypatch.setattr(EB, "ai_compile_fix_call", boom)
    return seen


# ── RED 1：失败前没有入口（结果区下面不挂引导行） ────────────────────────
def test_plain_compile_fix_entry_absent_before_failure():
    page, _root = _build()
    row = page._plain_fix_row
    assert row is not None, "结果区应预留一个可复用的引导行槽位"
    assert row.visible is False
    assert row.controls == []


# ── RED 2：普通转换退出码 2 才出现；成功不出现 ──────────────────────────
def test_plain_convert_rc2_reveals_entry(monkeypatch):
    page, _root = _build()
    _install_run(monkeypatch, page, 2)
    asyncio.run(page._run(page._build_call()))
    assert page._plain_compile_failed is True
    assert page._plain_fix_row.visible is True
    assert page._plain_fix_row.controls


def test_plain_convert_rc0_keeps_entry_hidden(monkeypatch):
    page, _root = _build()
    _install_run(monkeypatch, page, 0)
    asyncio.run(page._run(page._build_call()))
    assert page._plain_compile_failed is False
    assert page._plain_fix_row.visible is False
    assert page._plain_fix_row.controls == []


# ── RED 3：开关未开 / 无 Key → 去设置，明确告知且绝不发送 ────────────────
def test_switches_off_shows_settings_jump_and_never_sends(monkeypatch):
    seen = _forbid_send(monkeypatch)
    page, _root = _build()
    page._plain_compile_failed = True
    page._sync_plain_compile_fix()

    texts = _texts(page._plain_fix_row)
    assert any("未开启" in value or "API Key" in value for value in texts), texts
    assert any("设置" in value for value in _button_contents(page._plain_fix_row))

    opened = []
    page.app.goto = lambda key: opened.append(key)
    btn = next(control for control in _walk(page._plain_fix_row)
               if isinstance(control, ft.TextButton) and "设置" in str(control.content))
    btn.on_click(SimpleNamespace(control=btn))
    assert opened == ["settings"]
    assert seen == []


# ── RED 4：开关齐全但普通转换无签名证据 → 引导 AI 工作流，不伪造/发送 ────
def test_ready_but_no_signed_evidence_routes_to_ai_workflow(monkeypatch):
    seen = _forbid_send(monkeypatch)
    app = _App()
    _enable_ai(app)
    page, _root = _build(app)
    opened = []
    page._toggle_ai_workflow = lambda e=None: opened.append(True)

    page._plain_compile_failed = True
    page._sync_plain_compile_fix()

    texts = _texts(page._plain_fix_row)
    assert any("不生成可发送" in value or "AI 辅助初排" in value for value in texts), texts
    assert any("AI 辅助初排" in value for value in _button_contents(page._plain_fix_row))
    btn = next(control for control in _walk(page._plain_fix_row)
               if isinstance(control, ft.TextButton) and "AI 辅助初排" in str(control.content))
    btn.on_click(SimpleNamespace(control=btn))
    assert opened == [True]
    assert seen == []


# ── RED 5：帮助文案与真实入口 / 开关位置一致 ────────────────────────────
def test_help_content_points_to_real_entry_and_settings():
    entry = next(item for item in HC.ENTRIES if item[0] == "AI 辅助编译修复")
    assert "设置 → AI 与模型" in entry[1], entry[1]
    assert "AI 辅助初排" in entry[1], entry[1]
    assert "页下方 AI 面板" not in entry[1], entry[1]
