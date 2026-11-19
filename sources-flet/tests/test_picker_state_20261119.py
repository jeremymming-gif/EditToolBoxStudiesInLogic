"""原生选择耗时与重绘不得丢 AI 回件/高级附加文件的业务输入。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import flet as ft
import pytest

import main as M
from test_b114_polish import _Store, _build_single, _walk
from ui import bib_ai_assist as BAA
from ui.tokens import Tokens


class _ScriptedPicker:
    def __init__(self, *, directory=None, files=None, error=None):
        self.directory = directory
        self.files = files or []
        self.error = error

    async def get_directory_path(self, **kwargs):
        if self.error:
            raise self.error
        return self.directory

    async def pick_files(self, **kwargs):
        if self.error:
            raise self.error
        return [SimpleNamespace(path=value) for value in self.files]


class _SyncPage:
    def run_task(self, handler, *args):
        return asyncio.run(handler(*args))


def _picker_app(picker):
    app = SimpleNamespace(running=False, page=_SyncPage(), _picker=picker,
                          store=_Store(), toasts=[], marks=[], persisted=[])
    app.toast = app.toasts.append
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.persist = lambda: app.persisted.append(deepcopy(app.store.data))
    app._picker_error = M.App._picker_error.__get__(app)
    app._browse_task = M.App._browse_task.__get__(app)
    return app


class _QueuedPage:
    def __init__(self):
        self.tasks = []

    def run_task(self, handler, *args):
        self.tasks.append(asyncio.create_task(handler(*args)))

    def update(self):
        pass


class _DelayedPicker:
    def __init__(self):
        self.opened = asyncio.Event()
        self.reply = asyncio.get_running_loop().create_future()

    async def pick_files(self, **kwargs):
        self.opened.set()
        return await self.reply


def _ai_picker(picker):
    app = _picker_app(picker)
    app.browse_field = M.App.browse_field.__get__(app)
    host = SimpleNamespace(t=Tokens("light"), app=app, store=app.store,
                           page=app.page, _render=lambda: None, mark=app.mark)
    view = BAA.BibAiAssistView(host, purpose="initial")
    view.result_path = "/tmp/previous.md"
    view._refs["result_path"] = [ft.TextField(value=view.result_path)]
    return view, app, host


def test_slow_picker_completion_updates_current_ai_state_after_repaint(monkeypatch):
    """把旧 watcher 的每次 0.25s 等待变成一轮事件循环，避免真等一分钟。"""
    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(BAA.asyncio, "sleep", fast_sleep)

    async def scenario():
        picker = _DelayedPicker()
        view, app, host = _ai_picker(picker)
        app.page = host.page = _QueuedPage()
        view._browse("result_path", "选择 AI 结果文件")()
        await picker.opened.wait()
        # 对话框仍未作答；旧 240 次 watcher 此时已耗尽，新服务只等待选择完成。
        for _ in range(300):
            await real_sleep(0)
        assert view._picking is True
        assert view.result_path == "/tmp/previous.md"
        # 同时覆盖原生选择期间发生重绘、宿主仍持有旧控件引用的情况。
        view._refs["result_path"] = [ft.TextField(value=view.result_path)]
        chosen = "/tmp/回包 含空格.md"
        picker.reply.set_result([SimpleNamespace(path=chosen)])
        await asyncio.gather(*app.page.tasks)
        assert view.result_path == chosen
        assert view._picking is False
        assert ("bib_ai_browse", {"field": "result_path", "picked": True}) in app.marks

    asyncio.run(scenario())


@pytest.mark.parametrize("picker", [_ScriptedPicker(files=[]),
                                    _ScriptedPicker(error=RuntimeError("picker failed"))])
def test_cancel_or_failure_releases_ai_picker_without_changing_old_input(picker):
    view, app, _host = _ai_picker(picker)
    view._browse("result_path", "选择 AI 结果文件")()
    assert view._picking is False
    assert view.result_path == "/tmp/previous.md"
    assert app._picker_state == "idle"
    assert not any(fields.get("picked") for event, fields in app.marks
                   if event == "bib_ai_browse")


@pytest.mark.parametrize("change", ["task", "input", "running"])
def test_late_picker_result_cannot_overwrite_new_task_or_input(change):
    async def scenario():
        picker = _DelayedPicker()
        view, app, host = _ai_picker(picker)
        app.page = host.page = _QueuedPage()
        view.task_id = "AIW-old"
        view._browse("result_path", "选择 AI 结果文件")()
        await picker.opened.wait()
        if change == "task":
            view.set_task_id("AIW-new")
        elif change == "input":
            view._set_path("result_path", "/tmp/typed.md")
        else:
            view.running = True
        expected = view.result_path
        old_control = view._refs["result_path"][0]
        old_control.value = expected
        # 重绘后宿主持有旧控件；新旧控件及最近值都不得被迟到结果污染。
        view._refs["result_path"] = [ft.TextField(value=expected)]
        app.store.set_recent("bib_ai_result_path", "/tmp/current-recent.md")
        expected_store = deepcopy(app.store.data)
        picker.reply.set_result([SimpleNamespace(path="/tmp/late.md")])
        await asyncio.gather(*app.page.tasks)
        assert view.result_path == expected
        assert old_control.value == expected
        assert view._refs["result_path"][0].value == expected
        assert app.store.data == expected_store
        assert app.persisted == []
        assert app._picker_state == "idle"
        assert view._picking is False
        assert ("bib_ai_browse", {"field": "result_path", "picked": False,
                                  "stale": True}) in app.marks

    asyncio.run(scenario())


def test_picker_dispatch_failure_releases_ai_picker():
    view, app, _host = _ai_picker(_ScriptedPicker())

    def fail_dispatch(*args):
        raise RuntimeError("dispatch failed")

    app.page.run_task = fail_dispatch
    view._browse("result_path", "选择 AI 结果文件")()
    assert view._picking is False
    assert app._picker_state == "idle"
    assert view.result_path == "/tmp/previous.md"
    assert "粘贴路径" in app.toasts[0]
    assert "dispatch failed" in next(fields["error"] for event, fields in app.marks
                                     if event == "picker_error")


def test_running_host_rejects_ai_picker_without_leaving_it_locked():
    view, app, _host = _ai_picker(_ScriptedPicker(files=["/tmp/new.md"]))
    app.running = True
    view._browse("result_path", "选择 AI 结果文件")()
    assert view._picking is False
    assert view.result_path == "/tmp/previous.md"
    assert "运行中" in app.toasts[0]


def test_picker_completion_is_once_and_callback_failure_is_visible():
    app = _picker_app(_ScriptedPicker(files=["/tmp/new.md"]))
    seen = []

    def fail_sync(value):
        seen.append((value, app._picker_state))
        raise RuntimeError("sync failed")

    M.App.browse_field(app, [ft.TextField(value="")], "选择 AI 结果文件", "file",
                       "bib_ai_result_path", on_result=fail_sync)
    assert seen == [("/tmp/new.md", "idle")]
    assert "粘贴路径" in app.toasts[0]
    error = next(fields["error"] for event, fields in app.marks if event == "picker_error")
    assert "结果同步" in error and "sync failed" in error


@pytest.mark.parametrize("key,kind,flag", [
    ("convert_bib", "file", "bib"), ("convert_refs", "file", "refs"),
    ("convert_sty", "file", "sty"), ("convert_skeleton", "dir", "skeleton"),
])
def test_advanced_picker_value_survives_ai_repaint_and_clear(key, kind, flag):
    page, _root = _build_single()
    chosen = "/tmp/附加 文件." + flag
    app = page.app
    picker_app = _picker_app(_ScriptedPicker(directory=chosen, files=[chosen]))
    for name in ("_picker", "_picker_error", "_browse_task", "page", "mark", "persist"):
        setattr(app, name, getattr(picker_app, name))
    # 回调必须使用同一个 app 与 store；绑定真实宿主服务，不用手工给 form 造值。
    app._picker_error = M.App._picker_error.__get__(app)
    app._browse_task = M.App._browse_task.__get__(app)
    app.browse_field = M.App.browse_field.__get__(app)
    row = page._adv_path_input(key, "附加文件", "", kind=kind)
    button = next(control for control in _walk(row)
                  if isinstance(control, ft.OutlinedButton) and control.content == "浏览…")
    button.on_click(None)
    assert page.field(key) == chosen
    assert app.store.get(f"form.{key}") == chosen
    page._rerender_flow()
    assert page.field(key) == chosen
    page.fields["single_source"][0].value = "/tmp/input.tex"
    page.fields["single_out"][0].value = "/tmp/output"
    assert page._convert_kwargs()[flag] == chosen
    page._clear_adv_field(key)
    page._rerender_flow()
    assert page.field(key) == ""
    assert app.store.get(f"form.{key}") == ""


@pytest.mark.parametrize("store_prefix", ["recent", "form", "paths"])
@pytest.mark.parametrize("kind", ["file", "dir"])
def test_result_guard_rejects_before_any_control_or_settings_write(
        monkeypatch, store_prefix, kind):
    app = _picker_app(_ScriptedPicker(directory="/tmp/late", files=["/tmp/late"]))
    control = SimpleNamespace(value="/tmp/typed", update=lambda: updates.append(True))
    updates, clear_syncs, completions, guards = [], [], [], []
    app.store.set(f"{store_prefix}.chosen", control.value)
    before_store = deepcopy(app.store.data)
    monkeypatch.setattr(M.C, "sync_clearable", clear_syncs.append)

    def guard():
        guards.append((control.value, deepcopy(app.store.data), list(app.persisted)))
        return False

    M.App.browse_field(
        app, [control], "选择文件", kind, "chosen", store_prefix=store_prefix,
        result_guard=guard,
        on_result=lambda value: completions.append((value, app._picker_state)))

    assert guards == [("/tmp/typed", before_store, [])]
    assert control.value == "/tmp/typed"
    assert app.store.data == before_store
    assert app.persisted == []
    assert updates == [] and clear_syncs == []
    assert completions == [(None, "idle")]
    assert ("picker_stale", {"kind": kind, "key": "chosen"}) in app.marks
    assert app.toasts == []


@pytest.mark.parametrize("store_prefix,kind", [
    ("recent", "file"), ("form", "file"), ("form", "dir"), ("paths", "dir"),
])
@pytest.mark.parametrize("guarded", [False, True])
def test_current_or_ordinary_picker_persists_then_notifies(
        store_prefix, kind, guarded):
    chosen = "/tmp/选中 含空格"
    app = _picker_app(_ScriptedPicker(directory=chosen, files=[chosen]))
    control = ft.TextField(value="/tmp/before")
    app.store.set(f"{store_prefix}.chosen", control.value)
    before_store = deepcopy(app.store.data)
    guards, completions = [], []

    def guard():
        guards.append((control.value, deepcopy(app.store.data), list(app.persisted)))
        return True

    def complete(value):
        completions.append((value, app._picker_state, control.value,
                            app.store.get(f"{store_prefix}.chosen"), len(app.persisted)))

    M.App.browse_field(
        app, [control], "选择文件", kind, "chosen", store_prefix=store_prefix,
        result_guard=guard if guarded else None, on_result=complete)

    assert guards == ([("/tmp/before", before_store, [])] if guarded else [])
    assert control.value == chosen
    assert app.store.get(f"{store_prefix}.chosen") == chosen
    assert app.persisted == [app.store.data]
    assert completions == [(chosen, "idle", chosen, chosen, 1)]
    assert app.toasts == []


def test_result_guard_failure_preserves_input_and_releases_picker(monkeypatch):
    app = _picker_app(_ScriptedPicker(files=["/tmp/late.md"]))
    control = ft.TextField(value="/tmp/typed.md")
    app.store.set_recent("chosen", control.value)
    before_store = deepcopy(app.store.data)
    clear_syncs, completions = [], []
    monkeypatch.setattr(M.C, "sync_clearable", clear_syncs.append)

    def guard():
        raise RuntimeError("request guard failed")

    M.App.browse_field(
        app, [control], "选择文件", "file", "chosen", result_guard=guard,
        on_result=lambda value: completions.append((value, app._picker_state)))

    assert control.value == "/tmp/typed.md"
    assert app.store.data == before_store
    assert app.persisted == [] and clear_syncs == []
    assert completions == [(None, "idle")]
    error = next(fields["error"] for event, fields in app.marks if event == "picker_error")
    assert "request guard failed" in error
    assert "粘贴路径" in app.toasts[0]


@pytest.mark.parametrize("failed", [False, True])
def test_picker_cancel_or_error_does_not_evaluate_result_guard(failed):
    app = _picker_app(_ScriptedPicker(error=RuntimeError("picker failed") if failed else None))
    control = ft.TextField(value="/tmp/previous.md")
    guards, completions = [], []
    M.App.browse_field(
        app, [control], "选择文件", "file", "chosen",
        result_guard=lambda: guards.append(True),
        on_result=lambda value: completions.append((value, app._picker_state)))
    assert control.value == "/tmp/previous.md"
    assert guards == [] and app.store.data == {} and app.persisted == []
    assert completions == [(None, "idle")]
