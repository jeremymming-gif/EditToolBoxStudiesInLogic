"""批 116 / IM-A：整期装配导航壳（D1-115-26 / D1-115-28 / D1-115-29）。

守住批 115 夜间重设计的导航契约：
1. 侧栏「整期装配」是叶子：`NAV_TREE` 无 views，6 步在页内步骤栏；
2. `issue` / `issue:overview` 是兼容别名，运行期落到 issue_state.last_step（无记录 → 第 1 步）；
3. 显式 `issue:<step>` 与 `P2_ISSUE_STEP` 不被历史记忆覆盖；
4. 22 条路由口径不变；搜索/命令面板仍保留 6 个步骤入口；
5. 标题/面包屑永远是「整期装配 · <步骤名>」，没有任何「流水线总览」入口。
"""
from __future__ import annotations

import asyncio
import inspect

import flet as ft
import pytest

import main as M
from ui import ia_map as IA
from ui import search_index as SI


# ── 假件 ────────────────────────────────────────────────────────────────────
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


class FakeFletPage:
    def __init__(self) -> None:
        self.updated = 0
        self.width = 1160
        self.on_error = None
        self.waited = False
        self.tasks: list = []

    async def wait_until_visible(self) -> None:
        self.waited = True

    def update(self) -> None:
        self.updated += 1

    def run_task(self, task, *args, **kwargs) -> None:
        if inspect.iscoroutine(task):
            task.close()
        self.tasks.append(task)


class FakeIssuePage:
    key = "issue"
    STEPS = IA.ISSUE_ROUTE_STEPS

    def __init__(self) -> None:
        self.built = 0
        self.activated: list[tuple[str, str]] = []
        self.shown = 0

    def ensure_built(self):
        self.built += 1
        return ft.Container()

    def activate(self, view: str, *, source: str = "nav") -> None:
        self.activated.append((view, source))

    def on_show(self) -> None:
        self.shown += 1


class FakeHomePage(FakeIssuePage):
    key = "home"


def _nav_app(*, issue_state: dict | None = None) -> tuple[object, FakeIssuePage]:
    app = object.__new__(M.App)
    app.store = FakeStore({"issue_state": issue_state} if issue_state else {})
    app.page = FakeFletPage()
    app.current_page_key = "home"
    app.current_route = "home"
    app.pages = {"home": FakeHomePage(), "issue": FakeIssuePage()}
    app.content = ft.Container()
    app.sidebar = None
    app.page_switches = []
    app.marks: list[tuple[str, dict]] = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app._sync_toolbar = lambda: None
    return app, app.pages["issue"]


# ── A. 静态结构：侧栏叶子 / 默认步 / 别名 / 路由计数 ───────────────────────
def test_issue_nav_entry_is_leaf_with_static_default() -> None:
    entry = next(item for section in IA.NAV_TREE for item in section["entries"]
                 if item["key"] == "issue")
    assert entry == {"key": "issue", "label": "整期装配",
                     "icon": "VIEW_AGENDA_OUTLINED", "default": "infocheck"}
    assert "views" not in entry and "groups" not in entry
    assert IA.nav_views("issue") == []
    assert IA.group_views("issue") == []
    assert IA.page_defaults()["issue"] == "infocheck"


def test_issue_steps_are_route_only_not_sidebar_children() -> None:
    assert [step["key"] for step in IA.ISSUE_ROUTE_STEPS] == [
        "infocheck", "toc", "compileall", "merge", "cleantemp", "dirpush"]
    assert [step["label"] for step in IA.ISSUE_ROUTE_STEPS] == [
        "检查tex信息", "生成目录", "编译全部", "合并文件", "删除临时文件", "目录摘要推送"]
    assert [step["key"] for step in IA.route_views("issue")] == IA.issue_step_keys()
    assert IA.issue_step_label("toc") == "生成目录"
    assert IA.issue_step_label("overview") == ""


def test_issue_landing_routes_and_alias_predicate() -> None:
    assert IA.is_issue_landing_route("issue")
    assert IA.is_issue_landing_route("issue:overview")
    assert IA.is_issue_landing_route("issue:")
    assert not IA.is_issue_landing_route("issue:toc")
    assert not IA.is_issue_landing_route("single")
    assert IA.normalize_route("issue") == ("issue", "infocheck")
    assert IA.normalize_route("issue:overview") == ("issue", "infocheck")
    assert IA.normalize_route("issue:toc") == ("issue", "toc")


def test_route_count_stays_22_and_keeps_alias() -> None:
    routes = IA.all_routes()
    assert len(routes) == 22
    assert len(routes) == len(set(routes))
    issue_routes = [route for route in routes if route.startswith("issue:")]
    assert issue_routes == ["issue:overview", "issue:infocheck", "issue:toc",
                            "issue:compileall", "issue:merge", "issue:cleantemp",
                            "issue:dirpush"]


def test_main_has_alias_resolution_hooks() -> None:
    restore_src = inspect.getsource(M.App._restore_route)
    default_src = inspect.getsource(M.App._nav_default_route)
    nav_src = inspect.getsource(M.App.navigate)
    toolbar_src = inspect.getsource(M.App._toolbar_spec)
    assert "IA.is_issue_landing_route(raw)" in restore_src
    assert "self._issue_landing_step()" in restore_src
    assert 'elif page_key == "issue"' in default_src
    assert "self._issue_landing_step()" in default_src
    assert "IA.is_issue_landing_route(raw_route)" in nav_src
    assert 'self.mark("issue_alias"' in nav_src
    assert 'labels["page"] == "issue"' in toolbar_src


def test_no_overview_entry_in_nav_or_search() -> None:
    labels = [str(entry["label"]) for section in IA.NAV_TREE
              for entry in section["entries"]]
    labels += [IA.view_label("issue", key) for key in IA.issue_step_keys()]
    assert "流水线总览" not in labels
    entries = SI.build_index(
        nav_tree=IA.NAV_TREE,
        issue_steps=[(step["key"], step["label"]) for step in IA.ISSUE_ROUTE_STEPS])
    assert all("流水线总览" not in entry.label for entry in entries)
    commands = [entry for entry in entries if entry.kind == "command"]
    issue_actions = {entry.action for entry in commands if entry.action.startswith("goto:issue")}
    assert issue_actions == {f"goto:issue:{key}" for key in IA.issue_step_keys()}
    page_actions = {entry.action for entry in entries if entry.kind == "page"}
    assert "goto:issue" in page_actions
    assert "goto:issue:overview" not in page_actions


# ── B. 运行期：landing step 解析 ────────────────────────────────────────────
def test_landing_step_prefers_issue_state_last_step() -> None:
    app, _ = _nav_app(issue_state={"last_step": "toc"})
    assert app._issue_landing_step() == "toc"


def test_landing_step_falls_back_to_first_step_without_record() -> None:
    app, _ = _nav_app()
    assert app._issue_landing_step() == "infocheck"


def test_landing_step_ignores_unknown_state_and_uses_page_resolver() -> None:
    app, issue_page = _nav_app(issue_state={"last_step": "bogus"})
    issue_page.landing_step = lambda: "merge"
    assert app._issue_landing_step() == "merge"


def test_landing_step_uses_per_path_record_when_recent_dir_matches() -> None:
    app, issue_page = _nav_app()
    app.store.set("recent.issue_dir", "/tmp/issue-42")
    issue_page._issue_dir = lambda: "/tmp/issue-42"
    app.store.set("issue_state", {"by_path": {
        "/tmp/issue-42": {"last_step": "cleantemp"},
        "/tmp/issue-41": {"last_step": "toc"},
    }})
    assert app._issue_landing_step() == "cleantemp"


def test_landing_step_reads_schema_v1_flat_path_map() -> None:
    """D1-115-28 schema v1：issue_state 直接以期目录为键（非 by_path 包装）。"""
    app, issue_page = _nav_app()
    app.store.set("recent.issue_dir", "/tmp/issue-42")
    issue_page._issue_dir = lambda: "/tmp/issue-42"
    app.store.set("issue_state", {
        "/tmp/issue-42": {"schema": 1, "last_step": "toc"},
        "/tmp/issue-41": {"schema": 1, "last_step": "cleantemp"},
    })
    assert app._issue_landing_step() == "toc"


def test_landing_step_uses_page_default_view_contract() -> None:
    """页内判定（上次步 > 首个未完成 > 第 1 步）优先于静态兜底。"""
    app, issue_page = _nav_app(issue_state={"last_step": "bogus"})
    issue_page._default_view = lambda: "dirpush"
    assert app._issue_landing_step() == "dirpush"


def test_landing_step_never_returns_page_overview() -> None:
    """无期目录时页面兼容总览，但导航壳必须落第 1 步（D1-115-26）。"""
    app, issue_page = _nav_app(issue_state={"last_step": "bogus"})
    issue_page._default_view = lambda: "overview"
    assert app._issue_landing_step() == "infocheck"


def test_landing_step_survives_page_resolver_failure() -> None:
    app, issue_page = _nav_app()

    def boom() -> str:
        raise RuntimeError("no page yet")

    issue_page.landing_step = boom
    assert app._issue_landing_step() == "infocheck"
    assert any(event == "issue_landing_error" for event, _ in app.marks)


# ── C. 运行期：navigate / 恢复默认路由 ─────────────────────────────────────
def test_navigate_alias_resolves_to_last_step() -> None:
    app, issue_page = _nav_app(issue_state={"last_step": "toc"})
    for route in ("issue:overview", "issue"):
        app.current_page_key = "home"
        app.current_route = "home"
        issue_page.activated.clear()
        M.App.navigate(app, route, source="search")
        assert app.current_route == "issue:toc"
        assert issue_page.activated == [("toc", "search")]
        assert app.store.get("last_route") == "issue:toc"
        assert any(event == "issue_alias" and fields["step"] == "toc"
                   for event, fields in app.marks)


def test_navigate_explicit_step_is_not_overridden_by_memory() -> None:
    app, issue_page = _nav_app(issue_state={"last_step": "toc"})
    M.App.navigate(app, "issue:merge", source="search")
    assert app.current_route == "issue:merge"
    assert issue_page.activated == [("merge", "search")]
    assert not any(event == "issue_alias" for event, _ in app.marks)


def test_restore_route_resolves_legacy_overview_alias() -> None:
    app = object.__new__(M.App)
    app.current_route = ""
    app.store = FakeStore({"last_route": "issue:overview",
                           "issue_state": {"last_step": "compileall"}})
    app.pages = {}
    assert M.App._restore_route(app) == "issue:compileall"


def test_nav_default_route_for_issue_uses_landing_step() -> None:
    app, _ = _nav_app(issue_state={"last_step": "dirpush"})
    assert M.App._nav_default_route(app, "issue") == "issue:dirpush"


# ── D. 运行期：P2_PAGE / P2_ISSUE_STEP 环境钩子 ─────────────────────────────
class _BootPage(FakeFletPage):
    def __init__(self) -> None:
        super().__init__()
        self.window = type("_Window", (), {"close": self._close})()

    async def _close(self) -> None:
        pass


async def _noop(*args, **kwargs) -> None:
    pass


def _boot_app(*, settings_exists: bool = True):
    app = object.__new__(M.App)
    app.page = _BootPage()
    app.store = FakeStore({"issue_state": {"last_step": "toc"}})
    app.pages = {"issue": FakeIssuePage()}
    app.settings_exists = settings_exists
    app.engine_status = type("_Status", (), {"ok": True, "to_dict": lambda self: {}})()
    app.marks: list[tuple[str, dict]] = []
    app.navigations: list[tuple[str, str]] = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.write_state = lambda: None
    app._wake_platform = _noop
    app._platform_hooks = _noop
    app._apply_demo_hooks = _noop
    app.navigate = lambda route, source="nav": app.navigations.append((route, source))
    return app


def _clear_boot_env(monkeypatch) -> None:
    for name in ("P2_PAGE", "P2_BIB_VIEW", "P2_SINGLE_FLOW", "P2_ENVCHECK",
                 "P2_BIB_QUERY", "P2_BIB_QUERY_UI", "P2_AUTORUN", "P2_SELFTEST",
                 "P2_OPEN_PICKER", "P2_AUTO_CLOSE_MS", "P2_ISSUE_STEP"):
        monkeypatch.delenv(name, raising=False)


def test_p2_issue_step_hook_navigates_explicit_step(monkeypatch) -> None:
    _clear_boot_env(monkeypatch)
    monkeypatch.setenv("P2_ISSUE_STEP", "merge")
    app = _boot_app()
    monkeypatch.setattr(M.SLOG, "log", lambda *args, **kwargs: None)
    asyncio.run(M.App.after_boot(app))
    assert app.navigations[-1] == ("issue:merge", "env")
    assert any(event == "issue_step_hook" and fields["step"] == "merge"
               for event, fields in app.marks)


def test_p2_issue_step_hook_rejects_unknown_step(monkeypatch) -> None:
    _clear_boot_env(monkeypatch)
    monkeypatch.setenv("P2_ISSUE_STEP", "overview")
    app = _boot_app()
    monkeypatch.setattr(M.SLOG, "log", lambda *args, **kwargs: None)
    asyncio.run(M.App.after_boot(app))
    assert app.navigations == []
    assert any(event == "issue_step_invalid" and fields["step"] == "overview"
               for event, fields in app.marks)


def test_p2_page_issue_lands_last_step(monkeypatch) -> None:
    _clear_boot_env(monkeypatch)
    monkeypatch.setenv("P2_PAGE", "issue")
    app = _boot_app()
    monkeypatch.setattr(M.SLOG, "log", lambda *args, **kwargs: None)
    asyncio.run(M.App.after_boot(app))
    assert app.navigations == [("issue:toc", "env")]


# ── E. 标题：整期始终带步骤名 ───────────────────────────────────────────────
def test_toolbar_title_always_includes_issue_step() -> None:
    app = object.__new__(M.App)
    app.pages = {"issue": FakeIssuePage()}
    app.current_page_key = "issue"
    app.current_route = "issue:infocheck"
    app.engine_status = type("_Status", (), {"ok": True})()
    app.mark = lambda event, **fields: None
    title, _meta, _action = M.App._toolbar_spec(app)
    assert title == "整期装配 · 检查tex信息"
    assert "流水线总览" not in title
    app.current_route = "issue:dirpush"
    assert M.App._toolbar_spec(app)[0] == "整期装配 · 目录摘要推送"
