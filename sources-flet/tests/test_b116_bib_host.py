"""批 115 BHOST：引用库薄宿主契约测试（懒加载 / 10 路由 / 降级 / 齿轮 / 转发）。

宿主只做路由与壳，页面模块全部被 fake 掉；真实模块由 `test_b116_bib_*.py`
各 owner 的用例覆盖。这里禁止启动窗口（FakePage.run_task 只记录，不跑事件循环）。
"""
from __future__ import annotations

import asyncio
import inspect

import flet as ft
import pytest

from services import engine_bridge as EB
from ui import bib_host as BH
from ui import bib_shell as S
from ui import pages as P
from ui.tokens import Tokens


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
    width = 1160

    def __init__(self) -> None:
        self.tasks: list = []
        self.updates = 0

    def run_task(self, task, *args, **kwargs) -> None:
        if inspect.iscoroutine(task):
            task.close()
        self.tasks.append(task)

    def update(self) -> None:
        self.updates += 1


class FakeApp:
    def __init__(self, data: dict | None = None) -> None:
        self.store = FakeStore(data)
        self.t = Tokens()
        self.page = FakePage()
        self.marks: list[tuple[str, dict]] = []
        self.toasts: list[str] = []
        self.navigations: list[tuple[str, str]] = []
        self.badges: list[int] = []
        self.toolbar = None

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))

    def toast(self, message: str) -> None:
        self.toasts.append(message)

    def navigate(self, route: str, *, source: str = "nav") -> None:
        self.navigations.append((route, source))

    def set_bib_badge(self, count) -> None:
        self.badges.append(int(count))

    def _sync_toolbar(self) -> None:
        pass


class FakeView:
    """10 个页面模块的最小替身：build 可换成 page_shell（测齿轮）。"""

    instances: list["FakeView"] = []

    def __init__(self, page) -> None:
        self.page = page
        self.built = 0
        self.activated = 0
        self.refreshed = 0
        self.shell = getattr(type(self), "shell", False)
        FakeView.instances.append(self)

    def build(self):
        self.built += 1
        if self.shell:
            return S.page_shell(
                self.page.t, title="叶", meta="meta", body=ft.Container(expand=True),
                gear=True)
        return ft.Container(content=ft.Text("leaf"), expand=True)

    def activate(self) -> None:
        self.activated += 1

    def refresh_running(self) -> None:
        self.refreshed += 1


@pytest.fixture(autouse=True)
def _reset_instances():
    FakeView.instances = []
    yield
    FakeView.instances = []


def _loader(monkeypatch, missing: set[str] | None = None):
    calls: list[tuple[str, str]] = []
    missing = missing or set()

    def load(module: str, cls: str):
        calls.append((module, cls))
        if module.rsplit(".", 1)[-1] in missing:
            raise ImportError(f"{module} 尚未落盘")
        return FakeView

    monkeypatch.setattr(BH, "load_view_class", load)
    return calls


def _host(monkeypatch, data: dict | None = None, missing: set[str] | None = None):
    calls = _loader(monkeypatch, missing)
    app = FakeApp(data)
    page = BH.BibHost(app)
    page._sync_toolbar_meta = lambda: None
    return page, app, calls


def test_bibpage_is_thin_host_and_keeps_legacy_class() -> None:
    assert issubclass(P.BibPage, BH.BibHost)
    assert P.BibPage.key == "bib"
    assert P.BibPage.view_keys() == set(BH.VIEW_SPECS)
    assert len(P.BibPage.VIEWS) == 10


def test_views_are_lazy_until_first_visit(monkeypatch) -> None:
    page, app, calls = _host(monkeypatch)
    assert calls == [] and page._views == {}
    first = page._get_view("overview")
    second = page._get_view("overview")
    assert first is second
    assert [c[1] for c in calls] == ["OverviewView"]


def test_default_view_is_overview_and_store_restores_last(monkeypatch) -> None:
    assert _host(monkeypatch)[0].view == "overview"
    assert _host(monkeypatch, {"bib_view": "health"})[0].view == "health"
    assert _host(monkeypatch, {"bib_view": ""})[0].view == "overview"
    assert _host(monkeypatch, {"bib_view": "nope"})[0].view == "overview"


@pytest.mark.parametrize("view", list(BH.VIEW_SPECS))
def test_all_ten_routes_activate_and_persist(monkeypatch, view) -> None:
    page, app, calls = _host(monkeypatch)
    page.build()
    page.activate(view, source="test")
    assert page.view == view
    assert page.group == page.group_of(view)
    assert app.store.get("bib_view") == view
    instance = page._get_view(view)
    assert instance.activated == 1
    assert instance.built >= 1
    assert any(event == "bib_view" and fields.get("view") == view
               for event, fields in app.marks)


def test_illegal_view_falls_back_to_overview(monkeypatch) -> None:
    page, app, _ = _host(monkeypatch)
    page.activate("bogus", source="test")
    assert page.view == "overview"
    assert app.store.get("bib_view") == "overview"
    assert any(event == "bib_view_invalid" and fields.get("view") == "bogus"
               for event, fields in app.marks)


def test_missing_module_degrades_only_that_view(monkeypatch) -> None:
    page, app, _ = _host(monkeypatch, missing={"bib_import"})
    page.build()
    page.activate("import", source="test")
    assert page.view == "import"
    assert page._get_view("import") is None
    assert "ImportError" in page._view_errors["import"]
    fallback = page.view_slot.content
    assert getattr(fallback, "_bib_state_banner", None) is True
    assert any(event == "bib_view_missing" and fields.get("view") == "import"
               for event, fields in app.marks)
    # import 失败不得影响其它视图：切 overview 仍能真实构建。
    page.activate("overview", source="test")
    assert page._get_view("overview") is not None
    assert page.view_slot.content is not fallback


def test_build_exception_degrades_with_banner(monkeypatch) -> None:
    page, app, _ = _host(monkeypatch)

    class Boom(FakeView):
        def build(self):
            raise ValueError("半成品")

    monkeypatch.setattr(BH, "load_view_class", lambda module, cls: Boom)
    page.build()
    page.activate("health", source="test")
    assert getattr(page.view_slot.content, "_bib_state_banner", None) is True
    assert "ValueError" in page._view_errors["health"]
    assert any(event == "bib_render_error" and fields.get("view") == "health"
               for event, fields in app.marks)


def test_write_view_gear_is_wired_to_settings(monkeypatch) -> None:
    page, app, _ = _host(monkeypatch)

    class ShellView(FakeView):
        shell = True

    monkeypatch.setattr(BH, "load_view_class", lambda module, cls: ShellView)
    page.build()

    gears = [c for c in BH._walk_controls(page.view_slot.content)
             if isinstance(c, ft.IconButton) and c.tooltip == "设置"]
    assert len(gears) == 1
    gear = gears[0]
    assert not gear.disabled and gear.on_click is not None
    gear.on_click(None)
    assert app.navigations == [("bib:settings", "ui")]


def test_refresh_running_forwards_only_to_current_leaf(monkeypatch) -> None:
    page, _, calls = _host(monkeypatch)
    page.build()
    page.activate("health", source="test")
    page.refresh_running()
    assert page._get_view("health").refreshed == 1
    # 没进过的视图不因 refresh 被提前加载。
    page.view = "edit"
    page.refresh_running()
    assert "ui.bib_edit" not in {module for module, _ in calls}


def test_set_view_delegates_route_to_main(monkeypatch) -> None:
    page, app, _ = _host(monkeypatch)
    page.set_view("edit", source="legacy")
    assert app.navigations == [("bib:edit", "legacy")]
    # 路由由 main 管：宿主自己不改 view（main 会回调 activate）。
    assert page.view == "overview"


def test_switch_alias_and_settings_gear_route(monkeypatch) -> None:
    page, app, _ = _host(monkeypatch)
    page._switch("health", source="env")
    assert app.navigations == [("bib:health", "env")]
    page.navigate_route("bib:settings")
    assert app.navigations[-1] == ("bib:settings", "ui")


def test_host_api_notify_run_task_reload(monkeypatch) -> None:
    page, app, _ = _host(monkeypatch)
    page.notify("已保存")
    assert app.toasts == ["已保存"]

    async def job():
        return 1

    page.run_task_ui(job)
    assert app.page.tasks and callable(app.page.tasks[0])

    page.build()
    before = page._get_view("overview").built
    page.reload_view()
    assert page._get_view("overview").built == before + 1


def test_toolbar_meta_matches_legacy_contract(monkeypatch) -> None:
    page, _, _ = _host(monkeypatch)
    assert page.toolbar_meta() == "统计读取中…"
    page.stats_data = {"records": 3883}
    assert page.toolbar_meta() == "3,883 条"
    page.lint_counts = {"warnings": 76}
    assert page.toolbar_meta() == "3,883 条 · 76 条警告"


def test_load_stats_updates_meta_and_badge(monkeypatch) -> None:
    page, app, _ = _host(monkeypatch)

    def fake_read(call):
        if "lint" in list(getattr(call, "argv", []) or []):
            return {"ok": True, "json": {"counts": {"warnings": 76}}}
        return {"ok": True, "json": {
            "records": 3883,
            "aliases": {"total": 12},
            "conflicts": {"totals": {"pending": 5}},
        }}

    monkeypatch.setattr(EB, "read_json_command", fake_read)
    asyncio.run(page._load_stats())
    assert page.stats_data["records"] == 3883
    assert page.lint_counts == {"warnings": 76}
    assert page.toolbar_meta() == "3,883 条 · 76 条警告"
    assert app.badges == [5]


def test_normalize_view_compat_translates_old_entrypoints(monkeypatch) -> None:
    page, _, _ = _host(monkeypatch)

    class Norm(FakeView):
        def __init__(self, host):
            super().__init__(host)
            self.returned = ""
            self.original = ""
            self.generated = 0

        def generate(self):
            self.generated += 1

    monkeypatch.setattr(BH, "load_view_class", lambda module, cls: Norm)
    compat = page.normalize_view
    assert compat.ensure_built() is not None
    compat.set_field("bib_returned", "/tmp/a.pdf")
    compat._on_run()
    real = page._get_view("normalize")
    assert real.returned == "/tmp/a.pdf"
    assert real.generated == 1


def test_page_build_has_single_content_slot(monkeypatch) -> None:
    page, _, _ = _host(monkeypatch)
    root = page.build()
    assert isinstance(root, ft.Column)
    assert root.controls == [page.view_slot]
    assert page._crumb is not None and page._crumb.visible is False
