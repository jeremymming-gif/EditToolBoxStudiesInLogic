"""批 112 S0 · P0 回归：GUI-04 / GUI-04b（不启动 GUI、不碰真实库）。

GUI-04：`App.__init__` 曾把 `_browse_task` 存成 `None`，遮蔽类上同名 async 方法；
`browse_field` 调 `page.run_task(None, …)`，flet 1.0.5 抛
`TypeError: handler must be a coroutine function`，18 个「浏览…」入口全中。

GUI-04b：bib 三页的 `_run(task)` 原样把「协程对象」交给 `run_task`（31 处调用点都是
`self._run(self._load())` 这种写法），同一 TypeError；修复是在 3 个 helper 内包 async 函数。

这里的验证分三层：源码静态契约（AST，不需要 import main）、假 page 复刻 flet 契约、
以及 `browse_field` 的运行时调度（用 `App.__new__` 造壳，不跑窗口）。
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
MAIN = APP / "main.py"


# ── 源码静态契约（GUI-04） ─────────────────────────────────────────────
def _app_class() -> ast.ClassDef:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    return next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == "App")


def _app_methods() -> dict[str, ast.AST]:
    return {node.name: node for node in _app_class().body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _self_attrs_assigned(node: ast.AST) -> set[str]:
    """node 内所有 `self.X = …` / `self.X: … = …` 的 X。"""
    names: set[str] = set()
    for sub in ast.walk(node):
        targets: list[ast.expr] = []
        if isinstance(sub, ast.Assign):
            targets = list(sub.targets)
        elif isinstance(sub, ast.AnnAssign):
            targets = [sub.target]
        for target in targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                names.add(target.attr)
    return names


def test_gui04_app_has_async_browse_task_and_never_shadows_it():
    methods = _app_methods()
    assert "_browse_task" in methods, "App 上必须还有 async `_browse_task` 方法"
    assert isinstance(methods["_browse_task"], ast.AsyncFunctionDef), (
        "`_browse_task` 必须是 async def（flet 1.0.5 的 run_task 只收 coroutine function）")
    shadowed = _self_attrs_assigned(_app_class())
    assert "_browse_task" not in shadowed, (
        "App 内不得再给 self._browse_task 赋值（实例属性会遮蔽 async 方法，GUI-04）")


def test_gui04_browse_field_hands_run_task_the_async_method():
    field = _app_methods()["browse_field"]
    handlers = [
        call.args[0] for call in ast.walk(field)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute) and call.func.attr == "run_task"
        and call.args
    ]
    assert handlers, "browse_field 必须通过 page.run_task 调度"
    assert any(isinstance(handler, ast.Attribute) and handler.attr == "_browse_task"
               for handler in handlers), "browse_field 传给 run_task 的必须是 `_browse_task` 方法"


# ── 假 page：复刻 flet 1.0.5 Page.run_task 的契约 ─────────────────────
class _FakeFletPage:
    """run_task 只收 coroutine function；同步把协程跑完，测试不依赖事件循环。"""

    def __init__(self) -> None:
        self.handlers: list = []

    def run_task(self, handler, *args, **kwargs):
        assert inspect.iscoroutinefunction(handler), (
            f"run_task 只收 coroutine function，收到 {handler!r}（GUI-04b 回归）")
        self.handlers.append(handler)
        return asyncio.run(handler(*args, **kwargs))


class _HostPage:
    """假 pages.BibPage：三个 bib 视图的构造与 `_run` 只用到这些成员。"""

    def __init__(self) -> None:
        self.page = _FakeFletPage()
        self.app = SimpleNamespace(notes_repo="", store=SimpleNamespace(data={}))
        self.stats_data = None
        self.renders = 0

    def mark(self, event: str, **fields) -> None:
        pass

    def _render(self) -> None:
        self.renders += 1


@pytest.mark.parametrize(("module_name", "class_name"), [
    ("ui.bib_advanced", "BibQueryView"),
    ("ui.bib_settings", "BibSettingsView"),
    ("ui.bib_ops", "BibEditorView"),
])
def test_gui04b_run_wraps_coroutine_object(module_name: str, class_name: str):
    view = getattr(importlib.import_module(module_name), class_name)(_HostPage())
    ran: list[str] = []

    async def job() -> None:
        await asyncio.sleep(0)
        ran.append(class_name)

    view._run(job())  # 调用点写法不变：传的是协程对象
    assert ran == [class_name], f"{class_name}._run 没有 await 传进来的协程对象"
    flet_page = view.page.page
    assert len(flet_page.handlers) == 1
    assert inspect.iscoroutinefunction(flet_page.handlers[0])


# ── browse_field 运行时调度（GUI-04 + 加固的失败提示） ────────────────
class _EmptyPicker:
    """模拟用户取消：pick_files 返回空列表 → browse_field 走 picker_cancel。"""

    async def pick_files(self, **_kwargs):
        return []


class _StrictPage:
    """像 flet 1.0.5 一样在参数校验阶段就拒绝；用来验证 browse_field 的兜底提示。"""

    def run_task(self, handler, *args, **kwargs):
        raise TypeError("handler must be a coroutine function")


def _app_shim(flet_page) -> SimpleNamespace:
    app = SimpleNamespace(running=False, page=flet_page, _picker=_EmptyPicker())
    app.marks: list[tuple[str, dict]] = []
    app.errors: list[tuple[str, bool]] = []
    app.toasts: list[str] = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app._picker_error = lambda error, cancel=False: app.errors.append((error, cancel))
    app.toast = lambda message: app.toasts.append(message)
    return app


def test_gui04_browse_field_schedules_and_reaches_cancel_path():
    main = importlib.import_module("main")
    flet_page = _FakeFletPage()
    app = _app_shim(flet_page)
    app._browse_task = main.App._browse_task.__get__(app)  # 绑到假壳上
    ref = [SimpleNamespace(value="")]

    main.App.browse_field(app, ref, "选择源稿件", "file", "single_source")

    assert [event for event, _ in app.marks] == ["picker_open_begin", "picker_result"]
    assert app.errors == [("", True)], "空返回 = 用户取消，应走 _picker_error(..., cancel=True)"
    assert app.toasts == []
    assert len(flet_page.handlers) == 1
    assert inspect.iscoroutinefunction(flet_page.handlers[0])


def test_gui04_browse_field_reports_dispatch_failure_without_raising():
    main = importlib.import_module("main")
    app = _app_shim(_StrictPage())
    app._browse_task = main.App._browse_task.__get__(app)
    ref = [SimpleNamespace(value="")]

    main.App.browse_field(app, ref, "选择源稿件", "file", "single_source")  # 不得抛

    assert len(app.errors) == 1
    error, cancel = app.errors[0]
    assert error.startswith("run_task: TypeError:") and cancel is False
