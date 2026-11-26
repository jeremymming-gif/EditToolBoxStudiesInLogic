"""编译修复 L2 入口行 UX：终态出口、因果说明、错误语气、诚实文案（严格 TDD）。

测试对象：``ui.bib_ai_assist.BibAiAssistView._compile_fix_controls`` 的渲染结果。
只断言显示与文案；不触碰引擎调用、身份绑定、fail-closed 或轮次判定。
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
from ui import components as C
from ui.tokens import Tokens


class _Page:
    def __init__(self):
        self.dialogs = []

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)


@pytest.fixture
def ui_harness(monkeypatch):
    """复用 test_compilefix_r3.py 的稀疏宿主风格：只给渲染所需的宿主协议。"""
    page = _Page()
    view = object.__new__(BAA.BibAiAssistView)
    tasks = []
    settings = {"ai_enabled": True, "compile_fix_enabled": True, "key": "synthetic-key",
                "base": "https://example.invalid", "model": "synthetic", "protocol": "chat"}
    view.page = SimpleNamespace(page=page, t=Tokens(), app=SimpleNamespace(open_path=lambda path: True),
                                ai_initial_paths=lambda: ("/synthetic/source", "/synthetic/final"))
    view._initial_call = lambda: None
    view._r3, view.purpose = True, "typeset"
    view.running = False
    view.task_id, view.preview_id, view.work_dir, view.phase = "T1", "P1", "/synthetic/work", "preview"
    view.request_seq, view._reply_revision, view.preview_revision = 7, 9, 3
    view.candidate_bib, view.backup = "candidate.bib", ""
    view._original_final_dir = view._final_dir = ""
    view._initial_open_stage = "compare"
    view.compile_fix_eligible, view.compile_fix_notice = {}, ""
    view._compile_fix_prepared = view._compile_fix_pdf_opened = None
    view._compile_fix_row = ft.Column()
    view._compile_fix_settings = lambda: settings.copy()
    view.can_apply = lambda: True
    view._task = tasks.append
    monkeypatch.setattr(C, "_open_dialogs", [])
    return view, page, tasks, settings


def _result(status, **extra):
    return {"status": status, "task_id": "T1", "preview_id": "P1", "evidence_id": "E1",
            "candidate_sha256": "a" * 64, "evidence": [{"evidence_id": "D1"}], **extra}


def _texts(view):
    """收集行内所有可见文字（说明 + 按钮标签），用于文案断言。"""
    out = []

    def walk(control):
        value = getattr(control, "value", None)
        if isinstance(value, str) and value:
            out.append(value)
        for child in getattr(control, "controls", None) or []:
            walk(child)
        content = getattr(control, "content", None)
        if isinstance(content, str) and content:
            out.append(content)
        elif content is not None and hasattr(content, "controls"):
            walk(content)

    for control in view._compile_fix_row.controls:
        walk(control)
    return out


def _buttons(view):
    return [control.content for row in view._compile_fix_row.controls
            if hasattr(row, "controls") for control in row.controls
            if isinstance(getattr(control, "content", None), str)]


def _button(view, label):
    return next(control for row in view._compile_fix_row.controls if hasattr(row, "controls")
                for control in row.controls if getattr(control, "content", None) == label)


def _click(button):
    result = button.on_click(SimpleNamespace(control=button))
    if inspect.isawaitable(result):
        return __import__("asyncio").run(result)
    return result


@pytest.mark.parametrize("status, hint", [
    ("expired", "已过期"),
    ("deleted", "已删除"),
    ("delivered", "已完成"),
])
def test_terminal_status_renders_explanation_and_exit(ui_harness, status, hint):
    """终态不能是没按钮没说明的点不动空壳。"""
    view, _, _, _ = ui_harness
    view._set_compile_fix(_result(status))
    texts = _texts(view)
    assert any(hint in text for text in texts), texts
    labels = _buttons(view)
    assert labels, "终态渲染出无出口的空壳行"
    assert any("刷新状态" in label or "隐藏此行" in label for label in labels), labels


def test_terminal_status_exit_is_actionable_without_new_round(ui_harness):
    """出口必须可点：刷新只排队状态检查；隐藏只改显示，均不重发请求。"""
    view, _, tasks, _ = ui_harness
    view._set_compile_fix(_result("delivered"))
    _click(_button(view, "刷新状态"))
    assert len(tasks) == 1
    _click(_button(view, "隐藏此行"))
    assert view._compile_fix_row.controls == []


@pytest.mark.parametrize("status", ["eligible", "compile_failed"])
def test_failed_local_fix_row_explains_evidence_and_explicit_authorization(ui_harness, status):
    """失败后的行要讲清：本地修复失败、已生成安全证据、发送需逐稿显式授权。"""
    view, _, _, _ = ui_harness
    view._set_compile_fix(_result(status, eligible=True, diagnostics=[{"message": "synthetic"}]))
    texts = _texts(view)
    assert any("本地编译修复已失败" in text for text in texts), texts
    assert any("安全证据" in text for text in texts), texts
    assert any("显式授权" in text for text in texts), texts


def test_unknown_branch_shows_engine_message_and_no_auto_resend(ui_harness):
    """unknown 分支必须显示引擎 message；不会自动重发的提示不能因刷新而丢失。"""
    view, _, _, _ = ui_harness
    message = "引擎报告：请求等待超时，本轮没有收到可用回件。"
    view._set_compile_fix(_result("unknown", message=message, log_path="/synthetic/request.log",
                                  actions=["view_log", "retry", "cancel"]))
    texts = _texts(view)
    assert any(message in text for text in texts), texts
    assert any("不会自动重发" in text for text in texts), texts


def test_error_notice_uses_prominent_error_tone(ui_harness):
    """错误提示不能一律 info 灰；走 components 现成的 error 语气。"""
    view, _, _, _ = ui_harness
    notice = "未能确认请求结果；请先查看本地状态，不会自动重发。"
    view._set_compile_fix(_result("check_status"), notice)
    note_rows = [row for row in view._compile_fix_row.controls
                 if isinstance(row, ft.Row)
                 and any(getattr(ctl, "value", None) == notice for ctl in row.controls)]
    assert note_rows, _texts(view)
    note = next(ctl for ctl in note_rows[0].controls if getattr(ctl, "value", None) == notice)
    assert note.color == view.t.palette.error


@pytest.mark.parametrize("status", ["compile_failed", "cancelled"])
def test_failure_labels_do_not_promise_immediate_resend(ui_harness, status):
    """“新轮重试…”误导为立即重发；实际是选范围→冻结→重新授权。"""
    view, _, _, _ = ui_harness
    view._set_compile_fix(_result(status))
    labels = _buttons(view)
    assert any("重新选择范围" in label for label in labels), labels
    assert not any("新轮重试" in label for label in labels), labels
