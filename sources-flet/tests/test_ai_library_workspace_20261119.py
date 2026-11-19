"""库入口的工作区集成：动作可见性、单滚动及重绘保留视图身份。

离线控件树证据，不启动引擎、浏览器或原生窗口。
"""
from __future__ import annotations

import flet as ft
import pytest

from ui import bib_ai_assist as AI
from ui import bib_normalize as N
from test_bib_ai_assist_c_wiring_20261117 import _Host, _texts, _walk


def test_library_requests_shared_pinned_workspace(monkeypatch):
    calls = []
    body = ft.Column(expand=True, controls=[ft.Text("SHARED-WORKSPACE")])

    class View:
        def __init__(self, host, *, purpose):
            calls.append(("init", purpose))

        def body_only(self, *, compact=False, pinned_actions=False):
            calls.append(("body", compact, pinned_actions))
            return body

    monkeypatch.setattr(AI, "BibAiAssistView", View)
    view = N.NormalizeView(_Host())
    shell = view.build()
    assert calls == [("init", "library"), ("body", True, True)]
    assert shell._bib_body.content is body
    assert shell._bib_dock is None, "已有固定动作时不再占用一个重复底栏"
    assert _texts(shell).count("仅更新选定 Bib，不改中央库") == 1


@pytest.mark.parametrize("stage", ["prepare", "waiting", "review", "done"])
def test_library_one_scroll_keeps_current_action_outside_it(stage):
    view = N.NormalizeView(_Host())
    ai = view._ensure_ai_view()
    ai.target_bib = "/tmp/anonymous-library/selected.bib"
    assert ai.can_apply() is False, "没有有效候选时仍不能应用"
    if stage != "prepare":
        ai.task_id = "AIW-ANONYMOUS"
        ai.work_dir = "/tmp/anonymous-library/task"
    if stage == "review":
        ai.imported = True
        ai._has_active_reply = True
    elif stage == "done":
        ai.phase = "applied"
        ai._operation_receipt = {"target": ai.target_bib, "wrote": 1}
    shell = view.build()
    workspace = shell._bib_body.content
    scrolling = [c for c in _walk(workspace) if getattr(c, "scroll", None)]
    assert len(scrolling) == 1
    actions = [c for c in _walk(workspace) if getattr(c, "_bib_ai_action_bar", False)]
    assert len(actions) == 1
    assert all(c is not actions[0] for c in _walk(scrolling[0])), \
        "主动作不能跟着长审阅内容滚出视野"
    primaries = [c for c in _walk(workspace) if isinstance(c, ft.FilledButton)]
    assert len(primaries) == 1
    assert ai.purpose == "library"
    assert shell._bib_dock is None


def test_library_refresh_reuses_task_and_replaces_pinned_action():
    view = N.NormalizeView(_Host())
    shell = view.build()
    ai = view._ai_view
    ai.target_bib = "/tmp/anonymous-library/selected.bib"
    ai.task_id = "AIW-ANONYMOUS"
    ai.work_dir = "/tmp/anonymous-library/task"
    ai.body_text = "A reply that must survive rerender"
    ai.phase = "waiting"
    view._render()
    assert view._ai_view is ai
    assert view._shell is shell
    assert ai.body_text == "A reply that must survive rerender"
    assert ai.target_bib == "/tmp/anonymous-library/selected.bib"
    primaries = [c for c in _walk(shell) if isinstance(c, ft.FilledButton)]
    assert len(primaries) == 1
    assert "读取并检查结果" == primaries[0].content
    assert not any("完成初排转换" == t for t in _texts(shell))
