"""批 114 导航树（用户 m03256）：IA 三层 / 路由 / 展开状态 / 页头 / 搜索直达契约。

背景：用户拍板「引用库三层、整期 6 步全部上导航、直接删页内 tab」。本文件守住：
1. `ia_map.NAV_TREE` 的层级、默认视图、全部 route；
2. `components.nav_parent/nav_child/nav_group` 的行高/缩进（30/28/28，0/28/52）；
3. `main.navigate` 是唯一路由入口（激活视图 + 持久化 last_route/nav_open + mark）；
4. `settings_store` 的 last_route/nav_open 默认值、坏值归一、旧键迁移；
5. 页面侧：单篇 activate / 整期 overview+步骤子页+54 高进度带 / 引用库 activate；
6. 搜索索引「父项 + 子项」且 action 用 `goto:<route>`。
"""
from __future__ import annotations

import inspect
import json

import flet as ft
import pytest

import main as M
from services import settings_store as SS
from ui import components as C
from ui import ia_map as IA
from ui import pages as P
from ui import search_index as SI
from ui.tokens import Tokens


# ── 假壳 ────────────────────────────────────────────────────────────────────
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
        self.updated = 0

    def run_task(self, task, *args, **kwargs) -> None:
        if inspect.iscoroutine(task):
            task.close()
        self.tasks.append(task)

    def update(self) -> None:
        self.updated += 1


class FakeApp:
    def __init__(self, data: dict | None = None) -> None:
        self.store = FakeStore(data)
        self.t = Tokens()
        self.running = False
        self.marks: list[tuple[str, dict]] = []
        self.page = FakePage(self)
        self.navigations: list[tuple[str, str]] = []

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))

    def toast(self, message: str) -> None:
        pass

    def navigate(self, route: str, source: str = "nav") -> None:
        self.navigations.append((route, source))


# ── A. IA 树与 route ────────────────────────────────────────────────────────
def test_nav_tree_has_three_sections_and_six_parents() -> None:
    # 批 115 D1-115-31（IA-B）：引用库升为顶层 section，原「资料」不再作为导航分组。
    groups = [section["group"] for section in IA.NAV_TREE]
    assert groups == ["工作区", "引用库", "系统"]
    parents = [(entry["key"], entry["label"])
               for section in IA.NAV_TREE for entry in section["entries"]]
    assert parents == [("home", "工作台"), ("single", "单篇稿件"), ("issue", "整期装配"),
                       ("bib", "引用库"), ("settings", "设置"), ("help", "帮助与关于")]


def test_nav_tree_views_and_bib_groups_are_three_level() -> None:
    assert [view["key"] for view in IA.nav_views("single")] == ["convert", "proofcheck"]
    # 批 115 D1-115-26：整期装配侧栏叶子化，6 步只在页内步骤栏（不再是子项）。
    assert IA.nav_views("issue") == []
    assert IA.issue_step_keys() == [
        "infocheck", "toc", "compileall", "merge", "cleantemp", "dirpush"]
    # 批 115 D1-115-31/32：3 个不可点击组标签、9 叶常驻；健康并入治理，
    # 设置降为仅路由（页头齿轮/⌘K），不再占第 4 组。
    bib_groups = IA.group_views("bib")
    assert [group["key"] for group in bib_groups] == ["read", "intake", "treat"]
    assert [group["label"] for group in bib_groups] == ["查阅", "采录", "治理"]
    assert [view["key"] for view in bib_groups[1]["views"]] == ["import", "normalize"]
    assert [view["label"] for view in bib_groups[1]["views"]] == ["入藏", "回读校验"]
    assert [view["key"] for view in bib_groups[2]["views"]] == [
        "review", "edit", "dupes", "fieldfix", "health"]
    views = IA.nav_views("bib")
    assert len(views) == 10
    assert [view["key"] for view in views][-1] == "settings"
    assert [view["label"] for view in views][:2] == ["概览", "查库"]
    assert "回读校验" in {view["label"] for view in views}
    assert "规范化" not in {view["label"] for view in views}
    assert IA.group_of_view("bib", "edit") == "treat"
    assert IA.group_of_view("bib", "health") == "treat"
    assert IA.group_of_view("bib", "settings") == ""   # 仅路由：侧栏不列，无组


def test_all_routes_cover_parent_defaults_and_children() -> None:
    routes = IA.all_routes()
    assert len(routes) == 22
    assert set(routes) >= {"home", "settings", "help", "single:convert", "single:proofcheck",
                           "issue:overview", "issue:toc", "bib:overview", "bib:edit",
                           "bib:settings"}   # 设置仅从侧栏移除，深链路由不减少
    assert len(set(routes)) == len(routes)


@pytest.mark.parametrize("route, expected", [
    ("single:proofcheck", ("single", "proofcheck")),
    ("issue:toc", ("issue", "toc")),
    ("bib:edit", ("bib", "edit")),
    # 批 115 D1-115-26：裸 issue 与旧 issue:overview 静态回落到默认步。
    ("issue", ("issue", "infocheck")),
    ("issue:overview", ("issue", "infocheck")),
    ("single", ("single", "convert")),
    ("bib:nope", ("bib", "overview")),
    ("nope", ("home", "")),
    ("", ("home", "")),
    (None, ("home", "")),
])
def test_normalize_route_falls_back_safely(route, expected) -> None:
    assert IA.normalize_route(route) == expected


def test_route_labels_give_breadcrumb_and_child_title() -> None:
    labels = IA.route_labels("bib:edit")
    assert labels["crumb"] == "引用库 / 治理 / 编辑"
    assert labels["title"] == "编辑"
    assert labels["group"] == "treat"
    # 批 115 D1-115-31/32：normalize 全 UI 改名「回读校验」；设置无组但可深链。
    normalize = IA.route_labels("bib:normalize")
    assert normalize["view_label"] == "回读校验"
    assert normalize["crumb"] == "引用库 / 采录 / 回读校验"
    settings = IA.route_labels("bib:settings")
    assert settings["crumb"] == "引用库 / 设置" and settings["group"] == ""
    single = IA.route_labels("single:proofcheck")
    assert single["crumb"] == "单篇稿件 / 一校核查"
    assert single["title"] == "一校核查"
    # 批 115 D1-115-26：整期没有总览屏，静态默认步也要带步骤名（不再只显示父页名）。
    issue = IA.route_labels("issue")
    assert issue["view"] == "infocheck"
    assert issue["title"] == "检查tex信息"
    assert issue["crumb"] == "整期装配 / 检查tex信息"


# ── B. 导航组件行高 / 缩进 ─────────────────────────────────────────────────
def _field(control, name):
    return getattr(control, name)


def test_nav_parent_row_is_30_high_with_20px_toggle_area() -> None:
    t = Tokens()
    expanded: list = []
    clicked: list = []
    row = C.nav_parent("DESCRIPTION_OUTLINED", "单篇稿件", t, active=True, expanded=True,
                       on_click=lambda e: clicked.append(1),
                       on_toggle=lambda e: expanded.append(1))
    assert row.height == t.px(30)
    assert row.bgcolor == t.palette.nav_sel
    assert len(row.content.controls) == 3
    chevron = row.content.controls[2]
    assert chevron.width == t.px(20) and chevron.height == t.px(20)
    chevron.on_click(None)
    assert expanded == [1]
    assert clicked == []  # 箭头点击区只展开、不导航


def test_nav_child_and_group_rows_are_28_high_with_indent() -> None:
    t = Tokens()
    child = C.nav_child("编辑", t, active=True, on_click=lambda e: None, indent=52)
    assert child.height == t.px(28)
    assert child.bgcolor == t.palette.nav_sel
    assert child.padding.left == t.px(52)
    group = C.nav_group("治理", t, expanded=False, on_click=lambda e: None, indent=28)
    assert group.height == t.px(28)
    assert group.padding.left == t.px(28)


def test_sidebar_is_accordion_with_internal_scroll() -> None:
    source = inspect.getsource(M.App._sidebar_content)
    bib_source = inspect.getsource(M.App._bib_sidebar_leaf)
    assert "C.nav_parent(" in source
    # 批 116 决策：引用库侧栏只留一个叶子；9 视图移入 Bib 页内 chip 导航。
    assert "C.nav_child(" not in source + bib_source
    assert "C.nav_group(" not in source + bib_source
    assert "_bib_sidebar_leaf(" in source
    assert "_bib_section_header(" not in source
    assert "indent=48" not in bib_source
    assert "has_children=False" in bib_source
    assert "C.scrollbar(t)" in source          # 批 118：导航超高内部滚动、常显细条
    assert "C.nav_item(" in source             # <720 的 56 图标条兜底仍在
    assert "compact=True" in source


# ── C. 路由入口（对象注入，不建真实 App）────────────────────────────────────
class _RoutePage:
    key = "fake"

    def __init__(self) -> None:
        self.activated: list[tuple[str, str]] = []
        self.shown = 0
        self.root = ft.Column()

    def ensure_built(self):
        return self.root

    def activate(self, view, *, source="nav"):
        self.activated.append((view, source))

    def on_show(self) -> None:
        self.shown += 1


def _bare_app(**pages) -> M.App:
    app = object.__new__(M.App)
    app.store = FakeStore()
    app.page = FakePage(app)
    app.pages = dict(pages)
    app.current_page_key = ""
    app.current_route = ""
    app.nav_open = {"page": "", "group": ""}
    app.page_switches = []
    app.running = False
    app.marks = []
    app.calls = []

    def mark(event, **fields):
        app.marks.append((event, fields))

    app.mark = mark
    app._sync_toolbar = lambda: None
    app.sidebar = None
    app.content = ft.Container()
    app._sidebar_content = lambda: ft.Column()
    return app


def test_navigate_activates_view_and_persists_route() -> None:
    page = _RoutePage()
    app = _bare_app(bib=page)
    M.App.navigate(app, "bib:edit", source="test")
    assert page.activated == [("edit", "test")]
    assert app.current_page_key == "bib"
    assert app.current_route == "bib:edit"
    assert app.store.get("last_route") == "bib:edit"
    assert app.store.get("last_page") == "bib"
    assert app.store.get("nav_open") == {"page": "bib", "group": "treat", "collapsed": False}
    assert app.content.content is page.root
    events = [event for event, _ in app.marks]
    assert "nav_change" in events
    change = next(fields for event, fields in app.marks if event == "nav_change")
    assert change["route"] == "bib:edit" and change["page"] == "bib" and change["view"] == "edit"


def test_navigate_marks_invalid_route_instead_of_crashing() -> None:
    page = _RoutePage()
    app = _bare_app(home=page)
    M.App.navigate(app, "totally-unknown", source="test")
    assert app.current_page_key == "home"
    assert app.current_route == "home"
    assert page.activated == [("", "test")]


def test_goto_delegates_to_navigate_with_default_route() -> None:
    page = _RoutePage()
    app = _bare_app(bib=page)
    app.current_page_key = "home"
    app.navigate = (lambda route, source="nav": app.calls.append((route, source)))
    M.App.goto(app, "bib")
    assert app.calls == [("bib:overview", "goto")]  # 无记忆 → 页面默认视图
    app.current_page_key = "bib"
    M.App.goto(app, "bib")  # 同页早退，不重复导航
    assert len(app.calls) == 1


def test_nav_toggle_persists_open_state() -> None:
    app = _bare_app(home=_RoutePage(), single=_RoutePage())
    app.current_page_key = "home"
    app.nav_open = {"page": "home", "group": "", "collapsed": False}
    M.App._toggle_nav_page(app, "single")
    assert app.nav_open["page"] == "single"
    assert app.store.get("nav_open")["page"] == "single"
    assert any(event == "nav_toggle" for event, _ in app.marks)


# ── D. 持久化：默认值 / 旧键迁移 ───────────────────────────────────────────
def test_settings_defaults_include_route_and_open_state() -> None:
    data = SS._defaults()
    assert data["last_route"] == "home"
    assert data["nav_open"] == {"page": "home", "group": ""}


def test_settings_migrates_legacy_keys_to_route(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"flet": {"last_page": "bib", "bib_view": "edit"}}),
                    encoding="utf-8")
    data, _ = SS.SettingsStore(path).load()
    assert data["last_route"] == "bib:edit"
    assert data["nav_open"] == {"page": "bib", "group": "treat"}
    # 旧键保留（其它模块/回退仍可读）。
    assert data["last_page"] == "bib"
    assert data["bib_view"] == "edit"


def test_settings_bad_route_migrates_once(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"flet": {"last_route": 3, "last_page": "single",
                                         "single_flow": "proofcheck"}}),
                    encoding="utf-8")
    data, _ = SS.SettingsStore(path).load()
    assert data["last_route"] == "single:proofcheck"


# ── E. 页面侧：activate / 页头 / 进度带 ────────────────────────────────────
def test_single_page_activate_switches_flow_with_page_chips() -> None:
    page = object.__new__(P.SinglePage)
    app = FakeApp()
    page.app = app
    page.flow_key = "convert"
    page._flow_box = ft.Column()
    page._result = type("_Result", (), {"reset": lambda self: None})()
    page._ai = None
    page._crumb = ft.Text("单篇稿件")
    page._child_title = ft.Text("单篇稿件")
    page._flow_desc = ft.Text("")
    page._chip_row = ft.Container()
    page._flow_builder = lambda key: (lambda: ft.Text(key))
    page._sync_header = lambda: None
    page._safe_update = lambda *controls: None
    page.mark = lambda event, **fields: app.marks.append((event, fields))
    P.SinglePage.activate(page, "proofcheck", source="test")
    assert page.flow_key == "proofcheck"
    assert app.store.get("single_flow") == "proofcheck"
    assert page._flow_box.controls[0].value == "proofcheck"
    # 批 119：单篇不再有侧栏子项，convert/proofcheck 变成页内 28 高 chip，
    # 与整期/引用库共用 C.page_nav（选中态统一 nav_sel）。
    assert [chip.content.value for chip in page._flow_chips] == [
        "初排转换", "一校核查"]
    assert page._flow_chips[0].bgcolor is None
    assert page._flow_chips[1].bgcolor == app.t.palette.nav_sel
    build_src = inspect.getsource(P.SinglePage.build)
    assert "_build_flow_chips()" in build_src
    assert "C.chip(" not in build_src  # nav chip 只在共享 helper 里构建
    assert "C.page_nav(" in inspect.getsource(P.SinglePage._build_flow_chips)
    assert "_sync_header()" in build_src
    assert "_child_title" in build_src and "_flow_desc" in build_src
    assert "_crumb" not in build_src


def test_issue_page_builds_empty_state_or_step_subpage() -> None:
    # 批 117：旧「流水线总览」删除；build 只挂当前视图（无期目录时 = 空态）。
    build_src = inspect.getsource(P.IssuePage.build)
    assert "_build_overview" not in build_src
    assert "_build_step_page(landing)" in build_src
    assert "_content_box" in build_src
    step_src = inspect.getsource(P.IssuePage._build_step_page)
    body_src = inspect.getsource(P.IssuePage._step_body)
    assert "_build_empty_detail()" in body_src
    assert "_build_issue_row()" in step_src
    assert "_run_step" not in step_src and "_run_step" not in body_src
    # 批 117：54 高横条/216 左栏删除，导航改为页内 4 chip。
    assert "_progress_band" not in step_src
    nav_src = inspect.getsource(P.IssuePage._nav_bar)
    assert "self.VIEWS" in nav_src
    assert "_nav_narrow()" in nav_src
    empty_src = inspect.getsource(P.IssuePage._build_empty_detail)
    assert "还没有选择期目录" in empty_src and "_browse_issue" in empty_src
    assert "self.STEPS" not in inspect.getsource(P.IssuePage.build)


def test_issue_activate_switches_overview_alias_to_step_pages() -> None:
    page = object.__new__(P.IssuePage)
    app = FakeApp()
    page.app = app
    page.selected = ""
    page._content_box = ft.Column()
    page._step_pages = []
    page._default_view = lambda: "infocheck"
    page._build_step_page = lambda key: (page._step_pages.append(key)
                                         or ft.Column([ft.Text(key)]))
    page._crumb = ft.Text("整期装配")
    page._child_title = ft.Text("整期装配")
    page._flow_desc = ft.Text("")
    page._nav_box = ft.Container()
    page._sync_header = lambda: None
    page._sync_stop_action = lambda: None
    page._sync_nav = lambda: None
    page._set_expanded = lambda key: None
    page._persist_issue_state = lambda *args, **kwargs: None
    page.refresh_running = lambda: None
    page._safe_update = lambda *controls: None
    page.mark = lambda event, **fields: app.marks.append((event, fields))
    P.IssuePage.activate(page, "toc", source="test")
    assert page.selected == "toc"
    assert page._step_pages == ["toc"]
    assert page._content_box.controls[0].controls[0].value == "toc"
    # `issue:overview` 只保留别名兼容：落到默认首视图，不再有 overview 屏。
    P.IssuePage.activate(page, "overview", source="test")
    assert page.selected == "infocheck"
    assert page._step_pages[-1] == "infocheck"
    assert page._content_box.controls[0].controls[0].value == "infocheck"


def test_bib_page_activate_keeps_group_memory() -> None:
    page = object.__new__(P.BibPage)
    app = FakeApp()
    page.app = app
    page.view = "overview"
    page.group = "read"
    page._render = lambda: None
    page._ensure_loaded = lambda view: None
    page._sync_header = lambda: None
    page._safe_update = lambda *controls: None
    page.mark = lambda event, **fields: app.marks.append((event, fields))
    P.BibPage.activate(page, "edit", source="test")
    assert page.view == "edit" and page.group == "treat"
    assert app.store.get("bib_view") == "edit"
    assert app.store.get("bib_group_memory") == {"treat": "edit"}
    P.BibPage.activate(page, "bogus", source="test")
    assert page.view == "overview"
    assert any(event == "bib_view_invalid" for event, _ in app.marks)


# ── F. 搜索索引：父项 + 子项，action=goto:<route> ─────────────────────────
def test_search_index_lists_parents_and_children_with_routes() -> None:
    entries = SI.build_index(nav_tree=IA.NAV_TREE)
    pages = [entry for entry in entries if entry.kind == "page"]
    # 批 115 D1-115-31/32：父项 6 + 单篇 2 + 整期 0 + 引用库 10
    # （9 叶 + 设置仅路由；设置仍可经 ⌘K/搜索直达，只是不在侧栏）。
    assert len(pages) == 6 + 2 + 0 + 10
    actions = {entry.action for entry in pages}
    assert "goto:single" in actions
    assert "goto:single:proofcheck" in actions
    assert "goto:issue" in actions
    assert "goto:issue:toc" not in actions  # 步骤不再是页面子项
    assert "goto:issue:overview" not in actions  # 也没有「流水线总览」第二入口
    assert "goto:bib:edit" in actions
    assert "goto:bib:settings" in actions
    bib_labels = {entry.label for entry in pages if entry.action.startswith("goto:bib:")}
    assert "引用库 · 回读校验" in bib_labels
    assert "引用库 · 概览" in bib_labels
    assert "引用库 · 设置" in bib_labels
    assert not any("规范化" in label for label in bib_labels)
    assert all(entry.action.startswith("goto:") for entry in pages)


def test_search_commands_route_through_goto() -> None:
    bib_views = [(view["key"], view["label"]) for view in IA.route_views("bib")]
    entries = SI.build_index(nav_tree=IA.NAV_TREE,
                             bib_views=bib_views,
                             issue_steps=[(step["key"], step["title"])
                                          for step in P.IssuePage.STEPS])
    commands = [entry for entry in entries if entry.kind == "command"]
    bib_actions = [entry.action for entry in commands if "bib" in entry.action]
    issue_actions = [entry.action for entry in commands if "issue" in entry.action]
    assert "goto:bib:overview" in bib_actions
    assert "goto:bib:settings" in bib_actions
    assert "goto:issue:infocheck" in issue_actions
    # 批 115 D1-115-26：6 步仍可从搜索/命令面板直达（只是不再占侧栏子项）。
    assert "goto:issue:toc" in issue_actions
    # 批 115 D1-115-31/32：命令面板同样用 IA 新标签（回读校验），不再读 pages 旧名。
    bib_labels = [entry.label for entry in commands if entry.action.startswith("goto:bib:")]
    assert "引用库 · 回读校验" in bib_labels
    assert not any("规范化" in label for label in bib_labels)
    # 10 个真命令（主题/缩放/引擎/运行…）保持 id 直呼，不改成 goto。
    assert any(entry.action == "theme.toggle" for entry in commands)


def test_search_entry_runs_through_navigate() -> None:
    source = inspect.getsource(M.App._run_search_entry)
    assert "self.navigate(route, source=\"search\")" in source
    assert "IA.route_labels(route)" in source


def test_rebuild_shell_restores_route_before_on_show() -> None:
    source = inspect.getsource(M.App.rebuild_shell)
    assert "IA.normalize_route(self._restore_route())" in source
    assert 'activate(view_key, source="restore")' in source
    assert "self._nav_open_for(self.current_route)" in source
