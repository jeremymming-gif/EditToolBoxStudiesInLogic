"""库入口的工作区集成：动作可见性、单滚动及重绘保留视图身份。

离线控件树证据，不启动引擎、浏览器或原生窗口。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

from services import engine_bridge as EB
from ui import bib_ai_assist as AI
from ui.bib_host import BibHost
from ui import bib_normalize as N
from test_bib_ai_assist_c_wiring_20261117 import _Host, _texts, _walk
from test_b116_bib_host import FakeApp


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


def _scroll_owner(root):
    owners = [c for c in _walk(root) if getattr(c, "_bib_ai_workspace_scroll", False)]
    assert len(owners) == 1
    return owners[0]


@pytest.fixture
def library_workspace():
    host = BibHost(FakeApp({"bib_view": "normalize"}))
    host.build()
    view = host._get_view("normalize")
    shell = host.view_slot.content
    assert shell is view._shell
    ai = view._ai_view
    ai.target_bib = "/tmp/anonymous-library/selected.bib"
    view._render()
    return host, view, shell, ai


def _change(control, value):
    control.value = value
    control.on_change(SimpleNamespace(control=control))


def _start_task(view, ai, *, phase="waiting"):
    ai.task_id = "AIW-ANONYMOUS"
    ai.work_dir = "/tmp/anonymous-library/task"
    ai.phase = phase
    view._render()


def test_library_prepare_scope_and_channel_keep_mounted_scroll(library_workspace, monkeypatch):
    host, _, shell, ai = library_workspace
    scroll = _scroll_owner(shell)
    updates = []
    monkeypatch.setattr(ft.Container, "update", lambda control: updates.append(control))

    channel = next(c for c in _walk(scroll) if getattr(c, "_bib_ai_field", "") == "channel")
    _change(channel, "api")
    assert ai.channel == "api"
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is scroll
    assert updates == [shell._bib_body]

    execute = ai._execute_boxes["verify"]
    _change(execute, False)
    assert not ai.execute["verify"]
    assert ai._execute_boxes["verify"].value is False
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is scroll
    assert updates == [shell._bib_body] * 2


def test_library_waiting_channel_button_keeps_mounted_scroll(library_workspace):
    host, view, shell, ai = library_workspace
    _start_task(view, ai)
    scroll = _scroll_owner(shell)

    ai._action_buttons["use_api"].on_click(None)
    assert ai.channel == "api"
    assert not ai._action_buttons.get("copy_task")
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is scroll

    ai._action_buttons["use_chat"].on_click(None)
    assert ai.channel == "chat"
    assert any(c is ai._action_buttons["copy_task"] for c in _walk(scroll))
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is scroll


def test_library_review_decision_reset_and_scope_keep_mounted_scroll(library_workspace):
    host, view, shell, ai = library_workspace
    ai.conflicts = [{"conflict_id": f"cf-{i}", "key": f"anon{i}", "field": "title",
                     "choices": ["original", "reply", "library"],
                     "original": "old", "reply": "new", "library": "lib"}
                    for i in range(4)]
    ai._has_active_reply = True
    ai.preview_id = "synthetic-preview"
    ai._disclosure_open["more_differences"] = True
    _start_task(view, ai, phase="preview")
    scroll = _scroll_owner(shell)

    _change(ai._conflict_groups["cf-3"], "reply")
    assert ai._conflict_groups["cf-3"].value == "reply"
    assert not ai.preview_id
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is scroll
    more = next(c for c in _walk(scroll) if getattr(c, "_bib_ai_disclosure", "") == "more_differences")
    assert more.expanded

    reset = next(c for c in _walk(scroll) if getattr(c, "content", None) == "全部保留原值")
    reset.on_click(None)
    assert ai._conflict_groups["cf-3"].value == "original"
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is scroll

    _change(ai._accept_boxes["verify"], False)
    assert not ai.accept["verify"]
    assert ai._accept_boxes["verify"].value is False
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is scroll


@pytest.mark.parametrize("phase", ["waiting", "imported"])
def test_library_status_updates_loading_result_and_prefetch(library_workspace, monkeypatch, phase):
    host, view, shell, ai = library_workspace
    _start_task(view, ai)
    scroll = _scroll_owner(shell)

    async def read_status(work_dir):
        assert work_dir == ai.work_dir
        assert host.view_slot.content is shell
        assert _scroll_owner(shell) is scroll
        assert "正在刷新任务状态…" in _texts(scroll)
        return {"phase": phase, "task_id": ai.task_id, "entry": "bib",
                "execute": ["bib_verify"], "adopt": ["bib_verify"]}

    monkeypatch.setattr(ai, "_read_status", read_status)
    ai._action_buttons["refresh"].on_click(None)
    asyncio.run(host.page.tasks.pop()())
    assert host.view_slot.content is shell
    result_scroll = _scroll_owner(shell)
    assert (result_scroll is scroll) == (phase == "waiting")
    assert "正在刷新任务状态…" not in _texts(result_scroll)

    monkeypatch.setattr(ai, "_fn", lambda name: lambda **kwargs: None)
    monkeypatch.setattr(EB, "read_json_command", lambda call: {
        "ok": True, "json": {"task_id": ai.task_id, "text": "匿名任务书", "path": "/tmp/book.md"}})
    asyncio.run(host.page.tasks.pop()())
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is result_scroll
    assert ai.taskbook_path == "/tmp/book.md"


@pytest.mark.parametrize("clipboard_ok", [True, False])
def test_library_copy_taskbook_keeps_mounted_scroll(library_workspace, monkeypatch, clipboard_ok):
    host, view, shell, ai = library_workspace
    _start_task(view, ai)
    scroll = _scroll_owner(shell)
    book = "- 可用材料：待核条目 1 条。　\n"
    received = []

    def copy_text(text, *, preserve_whitespace):
        received.append((text, preserve_whitespace))
        return clipboard_ok

    monkeypatch.setattr(ai, "_fn", lambda name: lambda **kwargs: object())
    monkeypatch.setattr(EB, "read_json_command", lambda call: {
        "ok": True, "json": {"task_id": ai.task_id, "text": book}})
    host.app.copy_text = copy_text

    ai._action_buttons["copy_task"].on_click(None)
    assert asyncio.run(host.page.tasks.pop()()) is clipboard_ok
    assert received == [(book, True)]
    assert ("已复制任务书全文" in host.app.toasts) == clipboard_ok
    assert ("复制任务书全文失败：剪贴板不可用" in _texts(shell)) != clipboard_ok
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is scroll


def test_library_copy_compare_paths_keeps_review_scroll(library_workspace, monkeypatch):
    host, view, shell, ai = library_workspace
    ai.preview_id = "preview-anonymous"
    ai._compare_paths = ("/tmp/anonymous-library/before.bib", "/tmp/anonymous-library/after.bib")
    monkeypatch.setattr(ai, "_can_diff", lambda: True)
    copied = []

    def copy_text(text, *, preserve_whitespace):
        copied.append((text, preserve_whitespace))
        return True

    host.app.copy_text = copy_text
    _start_task(view, ai, phase="preview")
    scroll = _scroll_owner(shell)

    ai._action_buttons["copy_compare"].on_click(None)
    assert copied == [("\n".join(ai._compare_paths), True)]
    assert "已复制两份对比副本的路径。" in _texts(scroll)
    assert host.view_slot.content is shell
    assert _scroll_owner(shell) is scroll


def test_library_background_refresh_leaves_other_route_untouched(library_workspace):
    host, view, shell, ai = library_workspace
    _start_task(view, ai)
    scroll = _scroll_owner(shell)
    old_parts = scroll.controls
    host.view = "dupes"
    host._render()
    dupes = host.view_slot.content
    assert dupes is not shell

    ai._action_buttons["use_api"].on_click(None)
    assert ai.channel == "api"
    assert host.view_slot.content is dupes
    assert scroll.controls is old_parts

    host.view = "normalize"
    host._render()
    assert host.view_slot.content is not shell
    assert "开始 API 核实" in _texts(host.view_slot.content)
