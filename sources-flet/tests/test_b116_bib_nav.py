"""批 116 BIB-NAV2：引用库侧栏单叶（IA 方案 A）与 10 视图深链。

锁定的契约：
- 侧栏引用库 section 只有一个叶子「引用库」（图标 + 待处理徽标），
  无组 caption、无 9 子行、无 chevron；3 组 9 视图移入 Bib 页内 chip 导航。
- 设置只在页头齿轮/⌘K，不进侧栏，但 `bib:settings` 仍可深链。
- normalize 显示名「回读校验」贯穿 IA / 搜索索引 / 工具栏标题。
- `set_bib_badge(n)` 暴露给 BibPage owner：待办冲突数，0/None 隐藏。
"""

from __future__ import annotations

import inspect

import flet as ft

import main as M
from ui import components as C
from ui import ia_map as IA
from ui import search_index as SI
from ui.tokens import Tokens

BIB_LEAVES = [
    ("overview", "概览"), ("query", "查库"),
    ("import", "入藏"), ("normalize", "回读校验"),
    ("review", "待处理"), ("edit", "编辑"), ("dupes", "查重合并"),
    ("fieldfix", "字段归位"), ("health", "库健康"),
]


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
    def __init__(self) -> None:
        self.updated = 0
        self.activated: list[tuple[str, str]] = []
        self.shown = 0
        self.root = ft.Column()

    def ensure_built(self):
        return self.root

    def activate(self, view, *, source="nav"):
        self.activated.append((view, source))

    def on_show(self) -> None:
        self.shown += 1

    def update(self) -> None:
        self.updated += 1


def _nav_app() -> M.App:
    """只建侧栏/徽标需要的 App 骨架，不跑 Flet 页面。"""
    app = object.__new__(M.App)
    app.t = Tokens()
    app.bib_badge = None
    app._bib_nav_marked = False
    app.store = FakeStore()
    app.marks = []
    app.calls = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.navigate = lambda route, source="nav": app.calls.append((route, source))
    return app


def _route_app(store_data: dict | None = None) -> tuple[M.App, FakePage]:
    app = object.__new__(M.App)
    app.store = FakeStore(store_data)
    app.page = FakePage()
    app.pages = {"bib": FakePage()}
    app.current_page_key = ""
    app.current_route = ""
    app.nav_open = {"page": "", "group": ""}
    app.page_switches = []
    app.running = False
    app.marks = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app._sync_toolbar = lambda: None
    app.sidebar = None
    app.content = ft.Container()
    app._sidebar_content = lambda: ft.Column()
    return app, app.pages["bib"]


def test_ia_contract_three_groups_nine_leaves_and_settings_route_only() -> None:
    groups = IA.group_views("bib")
    assert [group["label"] for group in groups] == ["查阅", "采录", "治理"]
    leaves = [(view["key"], view["label"])
              for group in groups for view in group["views"]]
    assert leaves == BIB_LEAVES
    assert len(leaves) == 9
    views = IA.nav_views("bib")
    assert len(views) == 10
    assert [(view["key"], view["label"]) for view in views] == BIB_LEAVES + [
        ("settings", "设置")]
    assert views[-1]["route_only"] is True
    assert IA.group_of_view("bib", "settings") == ""
    assert IA.route_labels("bib:normalize")["view_label"] == "回读校验"


def test_sidebar_has_one_bib_leaf_no_captions_no_children() -> None:
    # 批 116 决策：引用库在侧栏只留一个叶子；3 组 9 视图移入页内 chip 导航。
    app = _nav_app()
    t = app.t
    leaf = M.App._bib_sidebar_leaf(app, t, False)
    assert leaf.height == t.px(28)
    assert leaf.bgcolor is None
    assert leaf.padding.left == t.px(8)
    assert callable(leaf.on_click)
    controls = leaf.content.controls
    assert len(controls) == 2  # 图标 + 文本；0 徽标隐藏
    assert controls[0].icon == C.ic("LIBRARY_BOOKS_OUTLINED")
    assert controls[1].value == "引用库"
    assert controls[1].expand is True
    # 无 chevron/子行：控件里没有「展开/收起」热区，也没有二级缩进行。
    assert not any(getattr(control, "tooltip", None) in ("展开", "收起")
                   for control in controls)

    leaf.on_click(None)
    assert app.calls == [("bib:overview", "nav")]
    assert "C.nav_child" not in inspect.getsource(M.App._bib_sidebar_leaf)
    assert "has_children=False" in inspect.getsource(M.App._bib_sidebar_leaf)


def test_sidebar_bib_leaf_is_active_on_every_bib_route() -> None:
    app = _nav_app()
    t = app.t
    active = M.App._bib_sidebar_leaf(app, t, True)
    assert active.bgcolor == t.palette.nav_sel
    assert active.content.controls[1].color == t.palette.text
    inactive = M.App._bib_sidebar_leaf(app, t, False)
    assert inactive.content.controls[1].color == t.palette.text_muted


def test_bib_nav_marker_records_single_leaf_and_is_emitted_once() -> None:
    app = _nav_app()
    app.bib_badge = 157
    M.App._bib_sidebar_leaf(app, app.t, True)
    M.App._bib_sidebar_leaf(app, app.t, True)
    markers = [fields for event, fields in app.marks if event == "bib_nav_section"]
    assert len(markers) == 1
    marker = markers[0]
    assert marker["section"] == "引用库"
    assert marker["leaf"] == "bib"
    assert marker["label"] == "引用库"
    assert marker["icon"] == "LIBRARY_BOOKS_OUTLINED"
    assert marker["groups"] == ["read", "intake", "treat"]
    assert marker["leaves"] == [key for key, _ in BIB_LEAVES]
    assert marker["route_only"] == ["settings"]
    assert marker["badge"] == 157


def test_sidebar_leaf_badge_only_for_positive_count() -> None:
    app = _nav_app()
    t = app.t
    app.bib_badge = 0
    leaf = M.App._bib_sidebar_leaf(app, t, False)
    assert len(leaf.content.controls) == 2  # 0 隐藏

    app.bib_badge = 3883
    leaf = M.App._bib_sidebar_leaf(app, t, False)
    assert len(leaf.content.controls) == 3
    badge = leaf.content.controls[2]
    assert badge.content.value == "3,883"
    assert badge.bgcolor == t.palette.surface_2  # neutral，不抢主行


def test_set_bib_badge_clamps_hides_and_rebuilds_sidebar() -> None:
    app = _nav_app()
    rebuilt: list[int] = []
    app.sidebar = type("_Sidebar", (), {"content": None})()
    app.page = FakePage()
    app._sidebar_content = lambda: rebuilt.append(1) or ft.Column()

    M.App.set_bib_badge(app, 157)
    assert app.bib_badge == 157
    assert rebuilt == [1] and app.page.updated == 1
    assert app.marks[-1] == ("bib_badge", {"count": 157, "visible": True})

    M.App.set_bib_badge(app, -3)       # 负数归 0，隐藏
    assert app.bib_badge == 0
    assert app.marks[-1] == ("bib_badge", {"count": 0, "visible": False})
    M.App.set_bib_badge(app, None)     # None 隐藏
    assert app.bib_badge is None
    M.App.set_bib_badge(app, "oops")   # 非法值不炸壳
    assert app.bib_badge is None
    assert len(rebuilt) == 4
    assert M.App._bib_badge_count(object.__new__(M.App)) == 0


def test_toolbar_title_names_bib_leaf_even_for_default_overview() -> None:
    app = object.__new__(M.App)
    page = type("_Bib", (), {"TOOLBAR_TITLE": "引用库"})()
    app.pages = {"bib": page}
    app.current_page_key = "bib"
    app.engine_status = type("_Status", (), {"ok": True})()
    app.mark = lambda event, **fields: None
    for route, expected in [
        ("bib", "引用库 · 概览"),  # 裸 bib 归一成默认叶，标题同样带叶名
        ("bib:overview", "引用库 · 概览"),
        ("bib:normalize", "引用库 · 回读校验"),
        ("bib:settings", "引用库 · 设置"),
    ]:
        app.current_route = route
        assert M.App._toolbar_spec(app)[0] == expected


def test_all_bib_routes_stay_deep_linkable_and_navigate() -> None:
    routes = [f"bib:{key}" for key, _ in BIB_LEAVES] + ["bib:settings"]
    assert set(routes) <= set(IA.all_routes())
    assert len(IA.all_routes()) == 22  # 不可因侧栏改版减少深链路由

    for route in routes:
        app, page = _route_app()
        M.App.navigate(app, route, source="deep")
        assert app.current_route == route
        assert page.activated[-1] == (route.split(":", 1)[1], "deep")
        assert app.store.get("last_route") == route


def test_bare_bib_lands_default_or_last_view_and_settings_is_not_sidebar() -> None:
    app, _page = _route_app()
    assert IA.normalize_route("bib") == ("bib", "overview")
    assert M.App._nav_default_route(app, "bib") == "bib:overview"
    app.store.set("bib_view", "health")
    assert M.App._nav_default_route(app, "bib") == "bib:health"
    app.store.set("bib_view", "removed-view")
    assert M.App._nav_default_route(app, "bib") == "bib:overview"

    sidebar_source = inspect.getsource(M.App._sidebar_content)
    leaf_source = inspect.getsource(M.App._bib_sidebar_leaf)
    assert 'key == "bib"' in sidebar_source
    assert "C.nav_group(" not in sidebar_source + leaf_source
    assert "C.nav_child" not in sidebar_source + leaf_source
    # 侧栏叶子不列 settings；settings 仅在 marker 里以 route_only 记录。
    assert 'bib:settings' not in leaf_source
    assert "C.badge" in leaf_source
    assert "has_children=False" in leaf_source


def test_search_index_lists_all_ten_bib_routes_with_new_labels() -> None:
    bib_views = [(view["key"], view["label"]) for view in IA.route_views("bib")]
    entries = SI.build_index(nav_tree=IA.NAV_TREE, bib_views=bib_views)
    pages = [entry for entry in entries
             if entry.kind == "page" and entry.action.startswith("goto:bib:")]
    assert {entry.action for entry in pages} == {
        "goto:bib:" + key for key, _ in BIB_LEAVES} | {"goto:bib:settings"}
    labels = {entry.label for entry in pages}
    assert "引用库 · 回读校验" in labels
    assert "引用库 · 设置" in labels  # 可搜索直达，只是不列侧栏
    assert not any("规范化" in label for label in labels)
