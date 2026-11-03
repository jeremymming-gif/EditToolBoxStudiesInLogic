"""批 112 S1 + 批 114 导航：状态保持（GUI-05 / 族 B / P2_* 钩子）契约测试。

二级视图迁入左侧导航后（用户 m03256），本文件守住「旧入口仍兼容」与状态持久化：
1. BibPage 10 视图顺序、`set_view`/`_switch` 兼容入口、组记忆；
2. SinglePage 流程恢复、`_switch_flow` 兼容入口；
3. rebuild_shell/on_show、P2_BIB_VIEW/P2_SINGLE_FLOW 钩子（现在走 navigate）；
4. 页内 tab（chip/组条）已删除，导航树本身由 test_b114_nav_tree.py 覆盖。
"""
from __future__ import annotations

import inspect
import sys

import flet as ft
import pytest

import main as M
from ui import pages as P
from ui.tokens import Tokens


# ── 假壳（只实现页面构造与切换用得到的接口）───────────────────────────────
class FakeStore:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})

    def get(self, key: str, default=None):
        current = self.data
        for part in str(key).split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set(self, key: str, value) -> None:
        parts = str(key).split(".")
        current = self.data
        for part in parts[:-1]:
            nxt = current.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                current[part] = nxt
            current = nxt
        current[parts[-1]] = value


class FakePage:
    def __init__(self, app) -> None:
        self.app = app
        self.tasks: list = []

    def run_task(self, task, *args, **kwargs) -> None:
        # 页面切换会调度 ensure_loaded 协程；单测里不跑事件循环，安全关闭即可。
        if inspect.iscoroutine(task):
            task.close()
        self.tasks.append(task)


class FakeApp:
    def __init__(self, data: dict | None = None) -> None:
        self.store = FakeStore(data)
        self.t = Tokens()
        self.running = False
        self.marks: list[tuple[str, dict]] = []
        self.page = FakePage(self)

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))

    def toast(self, message: str) -> None:
        pass


def _make_bib(data: dict | None = None) -> tuple[P.BibPage, FakeApp]:
    app = FakeApp(data)
    page = P.BibPage(app)
    # 不构建真实子视图：只测视图状态 + 持久化。
    page._render = lambda: None
    page._safe_update = lambda *controls: None
    page._ensure_loaded = lambda view: None
    page._sync_header = lambda: None
    page._notice_slot = ft.Container()
    page._status_slot = ft.Row(spacing=4, controls=[])
    page.view_slot = ft.Container()
    page.mark = lambda event, **fields: app.marks.append((event, fields))
    for name in ("review_view", "settings_view", "dupes_view", "fieldfix_view", "health_view"):
        getattr(page, name).ensure_loaded = lambda *args, **kwargs: None
    return page, app


# ── 静态契约 ────────────────────────────────────────────────────────────────
def test_bib_views_are_ten_in_ia_order() -> None:
    keys = [key for key, _ in P.BibPage.VIEWS]
    assert len(keys) == 10
    assert keys == ["overview", "query", "normalize", "import", "review",
                    "edit", "dupes", "fieldfix", "health", "settings"]
    assert P.BibPage.view_keys() == set(keys)


def test_set_view_routes_through_navigate_and_keeps_memory() -> None:
    assert "navigate(f\"bib:{view}\", source=source)" in inspect.getsource(P.BibPage.set_view)
    activate = inspect.getsource(P.BibPage.activate)
    assert 'store.set("bib_view", view)' in activate
    assert 'store.set("bib_group_memory", memory)' in activate
    assert "self._sync_header()" in activate
    assert "self._render()" in activate
    assert "bib_view_invalid" in activate


def test_switch_is_legacy_alias_for_set_view() -> None:
    source = inspect.getsource(P.BibPage._switch)
    assert "self.set_view(" in source


def test_single_switch_persists_flow() -> None:
    source = inspect.getsource(P.SinglePage._switch_flow)
    assert "navigate(f\"single:{key}\"" in source
    assert "self.activate(key, source=" in source
    activate = inspect.getsource(P.SinglePage.activate)
    assert 'store.set("single_flow"' in activate
    assert "_flow_builder(view)" in activate


def test_single_build_restores_flow_not_hardcoded_convert() -> None:
    source = inspect.getsource(P.SinglePage.build)
    assert "self._flow_builder(self.flow_key)()" in source
    assert "[self._build_convert()]" not in source
    assert "C.chip(" not in source


def test_rebuild_shell_calls_on_show_on_new_instance() -> None:
    source = inspect.getsource(M.App.rebuild_shell)
    assert "self.pages[key].on_show()" in source
    # 导航版：恢复上次路由并在构建后 activate 子视图。
    assert "IA.normalize_route(self._restore_route())" in source
    assert 'activate(view_key, source="restore")' in source


def test_after_boot_has_p2_bib_view_hook() -> None:
    source = inspect.getsource(M.App.after_boot)
    assert "P2_BIB_VIEW" in source
    assert "bib_view in bib_page.view_keys()" in source
    assert 'navigate(f"bib:{bib_view}", source="env")' in source


def test_pages_do_not_build_chips_inside_build_anymore() -> None:
    bib_build = inspect.getsource(P.BibPage.build)
    assert "_sync_header()" in bib_build
    assert "C.chip(" not in bib_build
    assert "_chips_row" not in bib_build
    single_build = inspect.getsource(P.SinglePage.build)
    assert "C.chip(" not in single_build
    issue_build = inspect.getsource(P.IssuePage.build)
    assert "_step_row(" not in issue_build  # 步骤卡列表已删，改导航子页


# ── 运行时：BibPage 视图状态 ────────────────────────────────────────────────
def test_bib_view_restored_from_store() -> None:
    page, _ = _make_bib({"bib_view": "query"})
    assert page.view == "query"
    assert page.group == "read"


@pytest.mark.parametrize("bad", ["", "nope", "overview2", None])
def test_illegal_bib_view_falls_back_to_overview(bad) -> None:
    page, _ = _make_bib({"bib_view": bad})
    assert page.view == "overview"


def test_activate_switches_views_and_persists() -> None:
    page, app = _make_bib()
    for key in P.BibPage.view_keys():
        P.BibPage.activate(page, key, source="test")
        assert page.view == key
        assert app.store.get("bib_view") == key
        assert any(event == "bib_view" and fields.get("view") == key
                   for event, fields in app.marks)


def test_illegal_activate_falls_back_to_overview() -> None:
    page, app = _make_bib({"bib_view": "query"})
    P.BibPage.activate(page, "bogus", source="test")
    # 非法视图不再静默忽略：回落 overview 并留痕（便于 P2 冒烟发现坏参数）。
    assert page.view == "overview"
    assert app.store.get("bib_view") == "overview"
    assert any(event == "bib_view_invalid" and fields.get("view") == "bogus"
               for event, fields in app.marks)


def test_activate_records_group_memory() -> None:
    page, app = _make_bib()
    P.BibPage.activate(page, "edit", source="test")
    assert page.group == "treat"
    assert app.store.get("bib_group_memory") == {"treat": "edit"}


def test_switch_marks_source_ui_and_env_override() -> None:
    page, app = _make_bib()
    P.BibPage.activate(page, "health", source="ui")
    assert any(event == "bib_view" and fields.get("view") == "health"
               and fields.get("source") == "ui"
               for event, fields in app.marks)
    P.BibPage.activate(page, "dupes", source="env")
    assert any(event == "bib_view" and fields.get("view") == "dupes"
               and fields.get("source") == "env"
               for event, fields in app.marks)


# ── 运行时：SinglePage 流程状态 ─────────────────────────────────────────────
def _make_single(data: dict | None = None) -> tuple[P.SinglePage, FakeApp]:
    app = FakeApp(data)
    page = P.SinglePage(app)
    page._flow_box = ft.Column()
    page._crumb = ft.Text("单篇稿件")
    page._child_title = ft.Text("初排转换")
    page._flow_desc = ft.Text("")
    page._result = type("_Result", (), {"reset": lambda self: None})()
    page._sync_header = lambda: None
    page._safe_update = lambda *controls: None
    page.mark = lambda event, **fields: app.marks.append((event, fields))
    return page, app


def test_single_flow_restored_from_store() -> None:
    page, _ = _make_single({"single_flow": "proofcheck"})
    assert page.flow_key == "proofcheck"


@pytest.mark.parametrize("bad", ["", "nope", None, "CONVERT"])
def test_illegal_single_flow_falls_back_to_convert(bad) -> None:
    page, _ = _make_single({"single_flow": bad})
    assert page.flow_key == "convert"


def test_switch_flow_persists_and_builds_selected(monkeypatch) -> None:
    page, app = _make_single()
    monkeypatch.setattr(P.CK, "on_flow_switch", lambda page: None)
    monkeypatch.setattr(page, "_flow_builder", lambda key: (lambda: ft.Text(key)))
    page._switch_flow("proofcheck")
    assert page.flow_key == "proofcheck"
    assert app.store.get("single_flow") == "proofcheck"
    assert page._flow_box.controls[0].value == "proofcheck"
    page._switch_flow("convert")
    assert app.store.get("single_flow") == "convert"


def test_flow_builder_maps_every_flow() -> None:
    page, _ = _make_single()
    for key in P.SinglePage.FLOWS:
        builder = page._flow_builder(key)
        assert callable(builder)
        assert "convert" in builder.__name__ or key in builder.__name__


def test_rebuild_restores_single_flow_through_constructor() -> None:
    """族 B 链路：store 里是 proofcheck → 新实例的 __init__ 必须直接恢复。"""
    page, _ = _make_single({"single_flow": "proofcheck"})
    assert page.flow_key == "proofcheck"
    assert P.SinglePage.FLOWS[page.flow_key]["label"] == "一校核查"


def test_tokens_are_importable_without_window() -> None:
    # 守住「测试不启动窗口」：本文件只构造 Tokens/控件，不碰 page.run_task 的真实实现。
    assert sys.modules.get("flet") is ft
