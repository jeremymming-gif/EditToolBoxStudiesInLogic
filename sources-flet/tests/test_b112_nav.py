"""批 112 S1：导航状态保持（GUI-05 / 族 B / P2_BIB_VIEW）契约测试。

三类断言：
1. 纯静态契约（函数源码里的持久化 / on_show / 环境钩子）；
2. 假 store + 假 page 的运行时单测（不启动 Flet 窗口）；
3. 非法输入回退（bib_view / single_flow / 未知子视图）。
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
    # 不构建真实子视图：只测两级导航 + 状态持久化。
    page._render = lambda: None
    page._safe_update = lambda *controls: None
    page._ensure_loaded = lambda view: None
    page._group_row = ft.Row(spacing=4, controls=[])
    page._chips_row = ft.Row(spacing=4, wrap=True, controls=[])
    page._notice_slot = ft.Container()
    page._status_slot = ft.Row(spacing=4, controls=[])
    page._render_nav()
    # 子视图的 ensure_loaded 是协程；单测不跑事件循环，替换成同步空桩。
    for name in ("review_view", "settings_view", "dupes_view", "fieldfix_view", "health_view"):
        getattr(page, name).ensure_loaded = lambda *args, **kwargs: None
    return page, app


def _active_chips(page: P.BibPage) -> list:
    accent = page.t.palette.accent_soft
    return [chip for chip in page._chips_row.controls if chip.bgcolor == accent]


# ── 静态契约 ────────────────────────────────────────────────────────────────
def test_bib_views_are_ten_in_ia_order() -> None:
    keys = [key for key, _ in P.BibPage.VIEWS]
    assert len(keys) == 10
    assert keys == ["overview", "query", "normalize", "import", "review",
                    "edit", "dupes", "fieldfix", "health", "settings"]
    assert P.BibPage.view_keys() == set(keys)


def test_set_view_persists_view_and_rerenders_nav() -> None:
    source = inspect.getsource(P.BibPage.set_view)
    assert 'store.set("bib_view", view)' in source
    assert 'store.set("bib_group_memory", memory)' in source
    assert "_render_nav()" in source
    assert "_render()" in source
    assert "source=source" in source
    # 兼容入口仍在：main.py 的 P2_BIB_VIEW / P2_BIB_QUERY_UI 走 _switch。
    assert "self.set_view(view, source=source)" in inspect.getsource(P.BibPage._switch)


def test_single_switch_persists_flow() -> None:
    source = inspect.getsource(P.SinglePage._switch_flow)
    assert 'store.set("single_flow", key)' in source
    assert "_flow_builder(key)" in source


def test_single_build_restores_flow_not_hardcoded_convert() -> None:
    source = inspect.getsource(P.SinglePage.build)
    assert "self._flow_builder(self.flow_key)()" in source
    assert "[self._build_convert()]" not in source


def test_rebuild_shell_calls_on_show_on_new_instance() -> None:
    source = inspect.getsource(M.App.rebuild_shell)
    assert "self.pages[key].on_show()" in source


def test_after_boot_has_p2_bib_view_hook() -> None:
    source = inspect.getsource(M.App.after_boot)
    assert "P2_BIB_VIEW" in source
    assert "bib_view in bib_page.view_keys()" in source
    assert '_switch(bib_view, source="env")' in source


def test_pages_do_not_build_chips_inside_build_anymore() -> None:
    source = inspect.getsource(P.BibPage.build)
    assert "self._chips_row" in source
    assert "self._render_nav()" in source
    # build 里不应再出现内联的 chip（或旧的一次性 _render_chips）列表推导。
    assert "C.chip(" not in source
    assert "_render_chips()" not in source


# ── 运行时：BibPage 视图状态 ────────────────────────────────────────────────
def test_bib_view_restored_from_store() -> None:
    page, _ = _make_bib({"bib_view": "query"})
    assert page.view == "query"
    assert [chip.bgcolor is not None for chip in _active_chips(page)] == [True]
    labels = [chip.content.value for chip in page._chips_row.controls]
    assert labels[1] == "查库"
    assert _active_chips(page)[0].content.value == "查库"


@pytest.mark.parametrize("bad", ["", "nope", "overview2", None])
def test_illegal_bib_view_falls_back_to_overview(bad) -> None:
    page, _ = _make_bib({"bib_view": bad})
    assert page.view == "overview"
    assert _active_chips(page)[0].content.value == "概况"


def test_each_view_switch_highlights_exactly_one_chip() -> None:
    page, app = _make_bib()
    for key in P.BibPage.view_keys():
        page._switch(key)
        assert page.view == key
        assert app.store.get("bib_view") == key
        active = _active_chips(page)
        assert len(active) == 1, key
        assert active[0].content.value == dict(P.BibPage.VIEWS)[key]
        assert active[0].content.weight == ft.FontWeight.W_500


def test_illegal_switch_falls_back_to_overview() -> None:
    page, app = _make_bib({"bib_view": "query"})
    page._switch("bogus")
    # S3a1 起非法视图不再静默忽略：回落 overview 并留痕（便于 P2 冒烟发现坏参数）。
    assert page.view == "overview"
    assert app.store.get("bib_view") == "overview"
    assert any(event == "bib_view_invalid" and fields.get("view") == "bogus"
               for event, fields in app.marks)


def test_chip_click_forwards_to_set_view() -> None:
    page, app = _make_bib()
    # 两级导航：先点「治理」组条（无记忆 → 组内首项 review），再点组内「编辑」胶囊。
    page._group_row.controls[2].on_click(None)
    assert page.view == "review"
    labels = [chip.content.value for chip in page._chips_row.controls]
    assert labels == ["待处理", "编辑", "查重合并", "字段归位"]
    page._chips_row.controls[labels.index("编辑")].on_click(None)
    assert page.view == "edit"
    assert app.store.get("bib_view") == "edit"
    assert app.store.get("bib_group_memory") == {"treat": "edit"}


def test_switch_marks_source_ui_and_env_override() -> None:
    page, app = _make_bib()
    page._switch("health")
    assert any(event == "bib_view" and fields.get("page") == "bib"
               and fields.get("view") == "health" and fields.get("source") == "ui"
               for event, fields in app.marks)
    page._switch("dupes", source="env")
    assert any(event == "bib_view" and fields.get("page") == "bib"
               and fields.get("view") == "dupes" and fields.get("source") == "env"
               for event, fields in app.marks)


# ── 运行时：SinglePage 流程状态 ─────────────────────────────────────────────
def _make_single(data: dict | None = None) -> tuple[P.SinglePage, FakeApp]:
    app = FakeApp(data)
    page = P.SinglePage(app)
    page._render_chips = lambda: None
    page._flow_box = ft.Column()
    page._flow_desc = ft.Text("")
    page._result = type("_Result", (), {"reset": lambda self: None})()
    page._safe_update = lambda *controls: None
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
    # build 用恢复后的 key 选构建器（静态契约另有断言）。
    assert P.SinglePage.FLOWS[page.flow_key]["label"] == "一校核查"


def test_tokens_are_importable_without_window() -> None:
    # 守住「测试不启动窗口」：本文件只构造 Tokens/控件，不碰 page.run_task 的真实实现。
    assert sys.modules.get("flet") is ft
