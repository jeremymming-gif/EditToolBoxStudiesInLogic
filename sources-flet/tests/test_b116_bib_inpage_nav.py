"""批 116 BIB-NAV2：引用库页内二级导航（方案 A，顶部 chip）契约测试。

锁定的契约：
- 侧栏引用库 section 只有一个叶子（图标 + 待处理徽标），无组 caption/子行；
- Bib 白面板顶部一条 36 高导航条：9 个 28 高 chip + 2 条组间发丝分隔（无可见 caption）；
- chip 点击走 `navigate("bib:<key>")`，选中色 = 侧栏 nav_sel 灰 tint；
- bib:settings 仍显示导航条，但无 chip 选中；
- <1100 设计宽自动两行分组换行，9 项全部可见可点；
- 22 条路由 / 10 个视图 / 搜索索引 / P2_BIB_VIEW 语义不变。

批 116 BIB-NAV3：删掉 3 个可见组标题（查阅/采录/治理），组语义改用 1×16 发丝线；
组名放到每组第一个 chip 的 tooltip，不新增可见文字。

这里不启动窗口：FakePage 只记录 update，页面模块用 FakeView 包一层 page_shell。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import flet as ft
import pytest

import main as M
from ui import bib_host as BH
from ui import bib_shell as S
from ui import components as C
from ui import ia_map as IA
from ui import search_index as SI
from ui.tokens import DARK, LIGHT, Tokens, palette_remap

BIB_LEAVES = [
    ("overview", "概览"), ("query", "查库"),
    ("import", "入藏"), ("normalize", "回读校验"),
    ("review", "待处理"), ("edit", "编辑"), ("dupes", "查重合并"),
    ("fieldfix", "字段归位"), ("health", "库健康"),
]
CAPTIONS = ["查阅", "采录", "治理"]


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
    def __init__(self, width: int = 1160) -> None:
        self.width = width
        self.updates = 0
        self.tasks: list = []

    def update(self) -> None:
        self.updates += 1

    def run_task(self, task, *args, **kwargs) -> None:
        if inspect.iscoroutine(task):
            task.close()
        self.tasks.append(task)


class FakeApp:
    def __init__(self, data: dict | None = None, width: int = 1160) -> None:
        self.store = FakeStore(data)
        self.t = Tokens()
        self.page = FakePage(width)
        self.marks: list[tuple[str, dict]] = []
        self.navigations: list[tuple[str, str]] = []
        self.toolbar = None
        self.bib_badge = None

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))

    def navigate(self, route: str, *, source: str = "nav") -> None:
        self.navigations.append((route, source))

    def _sync_toolbar(self) -> None:
        pass


class FakeView:
    shell = True

    def __init__(self, page) -> None:
        self.page = page

    def build(self):
        return S.page_shell(self.page.t, title="叶", meta="meta",
                            body=ft.Container(expand=True), gear=True)

    def activate(self) -> None:
        pass


def _host(monkeypatch, *, width: int = 1160, data: dict | None = None):
    monkeypatch.setattr(BH, "load_view_class",
                        lambda module, cls: FakeView)
    app = FakeApp(data, width)
    host = BH.BibHost(app)
    host._sync_toolbar_meta = lambda: None
    return host, app


def _walk(control):
    yield control
    content = getattr(control, "content", None)
    if isinstance(content, (list, tuple)):
        for child in content:
            yield from _walk(child)
    elif isinstance(content, ft.Control):
        yield from _walk(content)
    controls = getattr(control, "controls", None)
    if isinstance(controls, (list, tuple)):
        for child in controls:
            yield from _walk(child)


def _texts(control) -> list[str]:
    return [str(c.value) for c in _walk(control)
            if isinstance(c, ft.Text) and c.value is not None]


def _panel(host) -> ft.Container:
    panel = host._shell_panel(host.view_slot.content)
    assert panel is not None and getattr(panel, "_bib_shell", False) is True
    return panel


def _nav(host) -> ft.Container:
    nav = _panel(host).content.controls[0]
    assert getattr(nav, "_bib_inpage_nav", False) is True
    return nav


def _chips(nav) -> list[ft.Container]:
    return [c for c in _walk(nav) if getattr(c, "_bib_nav_chip", False)]


def _chip_keys(nav) -> list[str]:
    return [str(chip._bib_nav_key) for chip in _chips(nav)]


def _dividers(nav) -> list[ft.Container]:
    return [c for c in _walk(nav) if getattr(c, "_bib_nav_divider", False)]


def _sidebar_app(badge=None):
    app = object.__new__(M.App)
    app.t = Tokens()
    app.store = FakeStore()
    app.bib_badge = badge
    app._bib_nav_marked = False
    app.sidebar_open = True
    app.current_page_key = "bib"
    app.theme = "light"
    app.engine_status = SimpleNamespace(ok=True)
    app._version_info = {}
    app.app_version = "test"
    app.marks = []
    app.calls = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.navigate = lambda route, source="nav": app.calls.append((route, source))
    app._sidebar_compact = lambda: False
    app._sidebar_short = lambda: True
    app._theme_row = None
    app._sidebar_compact_cache = False
    app._issue_progress = lambda: None
    app.page = FakePage()
    return app


def _nav_column(content) -> ft.Column:
    for control in _walk(content):
        if isinstance(control, ft.Column) and control.scroll is not None:
            return control
    raise AssertionError("侧栏导航列不存在")


# ── A. 侧栏单叶 ────────────────────────────────────────────────────────────
def test_sidebar_bib_section_is_one_leaf_with_badge_and_no_captions() -> None:
    app = _sidebar_app(157)
    content = M.App._sidebar_content(app)
    column = _nav_column(content)
    labels = _texts(column)
    assert labels.count("引用库") == 1
    for caption in CAPTIONS:
        assert caption not in labels
    assert labels[:5] == ["工作区", "工作台", "单篇稿件", "整期装配", "引用库"]
    assert labels.index("系统") > labels.index("引用库")
    assert "157" in labels

    leaf = next(c for c in _walk(column)
                if isinstance(c, ft.Container) and c.bgcolor == app.t.palette.nav_sel
                and "引用库" in _texts(c))
    assert leaf.content.controls[0].icon == C.ic("LIBRARY_BOOKS_OUTLINED")
    assert len(leaf.content.controls) == 3  # 图标 + 文本 + 徽标


def test_sidebar_bib_leaf_badge_passthrough_via_set_bib_badge() -> None:
    app = _sidebar_app(0)
    app.sidebar = SimpleNamespace(content=None)
    M.App.set_bib_badge(app, 3883)
    assert app.bib_badge == 3883
    assert "3,883" in _texts(app.sidebar.content)
    M.App.set_bib_badge(app, 0)
    assert "0" not in _texts(app.sidebar.content)


# ── B. 页内导航结构 / 选中 / 点击 ──────────────────────────────────────────
def test_inpage_nav_nine_chips_two_dividers_and_single_row(monkeypatch) -> None:
    host, _app = _host(monkeypatch)
    host.build()
    nav = _nav(host)
    assert nav.height == host.t.px(36)
    assert nav._bib_nav_narrow is False
    # 白底 + 左右 24 + 底部 1px 分界线保持 BIB-NAV2 口径。
    assert nav.bgcolor == host.t.palette.surface
    assert (nav.padding.left, nav.padding.right) == (host.t.px(24), host.t.px(24))
    assert nav.border.bottom.width == host.t.px(1)
    assert isinstance(nav.content, ft.Container)
    assert nav.content.height == host.t.px(36)
    assert nav.content.content.wrap is False  # 单行不换行

    # 批 116 BIB-NAV3：3 个可见组标题已删，只剩 9 chip + 2 条组间发丝线。
    assert not [c for c in _walk(nav)
                if isinstance(c, ft.Text) and c.value in CAPTIONS]
    assert _chip_keys(nav) == [key for key, _ in BIB_LEAVES]
    assert [chip.content.value for chip in _chips(nav)] == [
        label for _, label in BIB_LEAVES]
    assert all(chip.height == host.t.px(28) for chip in _chips(nav))

    dividers = _dividers(nav)
    assert len(dividers) == 2
    assert all(d.width == host.t.px(21) for d in dividers)  # 10 + 1 + 10
    assert all(d.height == host.t.px(36) for d in dividers)
    lines = [d.content for d in dividers]
    assert all(line.width == host.t.px(1) for line in lines)
    assert all(line.height == host.t.px(16) for line in lines)
    assert all(line.bgcolor == host.t.palette.border for line in lines)
    # 组内 chip 间距 4；组间容器宽 21（线左右各 10），外行不再叠 4。
    rows = [c for c in nav.content.content.controls if isinstance(c, ft.Row)]
    assert [row.spacing for row in rows] == [host.t.px(4)] * 3

    # 语义补偿：每组第一个 chip 的 tooltip = 组名；其它 chip 无 tooltip。
    groups = IA.group_views("bib")
    first_keys = {str(group["views"][0]["key"]) for group in groups}
    for group in groups:
        first = next(chip for chip in _chips(nav)
                     if chip._bib_nav_key == group["views"][0]["key"])
        assert first.tooltip == group["label"]
    assert all(chip.tooltip is None for chip in _chips(nav)
               if chip._bib_nav_key not in first_keys)


def test_inpage_nav_marker_has_empty_captions_and_rendered_divider_count(
        monkeypatch) -> None:
    host, app = _host(monkeypatch)
    host.build()
    marks = [fields for event, fields in app.marks if event == "bib_inpage_nav"]
    assert marks and marks[-1]["captions"] == []
    assert marks[-1]["dividers"] == 2
    assert marks[-1]["narrow"] is False
    assert marks[-1]["chips"] == [key for key, _ in BIB_LEAVES]

    narrow_host, narrow_app = _host(monkeypatch, width=1000)
    narrow_host.build()
    narrow_marks = [fields for event, fields in narrow_app.marks
                    if event == "bib_inpage_nav"]
    assert narrow_marks and narrow_marks[-1]["captions"] == []
    # read|intake 在行 1；intake|treat 落在行间（不画多余线）→ 实际渲染 1。
    assert narrow_marks[-1]["dividers"] == 1
    assert narrow_marks[-1]["narrow"] is True


def test_inpage_nav_active_chip_uses_sidebar_nav_sel(monkeypatch) -> None:
    host, _app = _host(monkeypatch, data={"bib_view": "review"})
    host.build()
    nav = _nav(host)
    active = [chip for chip in _chips(nav) if chip.bgcolor == host.t.palette.nav_sel]
    assert len(active) == 1
    assert active[0]._bib_nav_key == "review"
    assert active[0].content.color == host.t.palette.text
    assert active[0].content.weight == ft.FontWeight.W_600
    inactive = [chip for chip in _chips(nav) if chip is not active[0]]
    assert all(chip.bgcolor is None for chip in inactive)
    assert all(chip.content.color == host.t.palette.text_muted for chip in inactive)


def test_inpage_nav_chip_click_navigates_bib_route(monkeypatch) -> None:
    host, app = _host(monkeypatch)
    host.build()
    chip = next(c for c in _chips(_nav(host)) if c._bib_nav_key == "edit")
    chip.on_click(None)
    assert app.navigations == [("bib:edit", "ui")]


def test_deep_link_selection_and_bib_view_persistence(monkeypatch) -> None:
    host, app = _host(monkeypatch)
    host.build()
    host.activate("dupes", source="deep")
    nav = _nav(host)
    assert nav._bib_nav_active == "dupes"
    assert [chip._bib_nav_key for chip in _chips(nav)
            if chip.bgcolor == host.t.palette.nav_sel] == ["dupes"]
    assert app.store.get("bib_view") == "dupes"


def test_settings_shows_nav_without_any_selected_chip(monkeypatch) -> None:
    host, _app = _host(monkeypatch)
    host.build()
    host.activate("settings", source="deep")
    nav = _nav(host)
    assert nav._bib_nav_active == ""
    assert all(chip.bgcolor is None for chip in _chips(nav))
    assert nav._bib_nav_keys == [key for key, _ in BIB_LEAVES]


# ── C. 1000 宽两行 / 无裁切 / resize 原位换行 ─────────────────────────────
def test_narrow_window_wraps_to_two_rows_with_all_nine_chips(monkeypatch) -> None:
    host, _app = _host(monkeypatch, width=1000)
    host.build()
    nav = _nav(host)
    assert nav._bib_nav_narrow is True
    assert nav.height == host.t.px(72)
    assert isinstance(nav.content, ft.Column)
    rows = nav.content.controls
    assert len(rows) == 2
    assert [row.height for row in rows] == [host.t.px(36), host.t.px(36)]
    keys = _chip_keys(nav)
    assert keys == [key for key, _ in BIB_LEAVES]
    assert all(callable(chip.on_click) for chip in _chips(nav))
    assert all(chip.width is None for chip in _chips(nav))
    # 组行不换行、不开省略；两行按 查阅+采录 / 治理 拆开。
    assert all(row.content.wrap is False for row in rows)
    assert not [c for c in _walk(nav)
                if isinstance(c, ft.Text) and c.value in CAPTIONS]
    assert [chip.content.value for chip in _chips(rows[0])] == [
        "概览", "查库", "入藏", "回读校验"]
    assert [chip.content.value for chip in _chips(rows[1])] == [
        "待处理", "编辑", "查重合并", "字段归位", "库健康"]
    # 行 1 有 read|intake 一条线；行 2 只有 treat 组，行间不画多余分隔符。
    assert len(_dividers(rows[0])) == 1
    assert _dividers(rows[1]) == []
    # 每组第一个 chip 的 tooltip 在各自行内保留。
    assert _chips(rows[0])[0].tooltip == "查阅"
    assert _chips(rows[0])[2].tooltip == "采录"
    assert _chips(rows[1])[0].tooltip == "治理"


def test_nav_attaches_when_view_wraps_page_shell_in_root_container(monkeypatch) -> None:
    """edit/dupes 等写库页 build 返回 Container(content=page_shell)，也要挂上。"""

    class WrappedView(FakeView):
        def build(self):
            return ft.Container(content=super().build(), expand=True)

    monkeypatch.setattr(BH, "load_view_class", lambda module, cls: WrappedView)
    app = FakeApp(data={"bib_view": "edit"})
    host = BH.BibHost(app)
    host._sync_toolbar_meta = lambda: None
    host.build()
    root = host.view_slot.content
    assert isinstance(root, ft.Container) and not getattr(root, "_bib_shell", False)
    nav = _nav(host)
    assert nav._bib_nav_active == "edit"
    assert [chip._bib_nav_key for chip in _chips(nav)
            if chip.bgcolor == app.t.palette.nav_sel] == ["edit"]


def test_resize_across_breakpoint_refreshes_nav_in_place(monkeypatch) -> None:
    host, app = _host(monkeypatch)
    host.build()
    first = _nav(host)
    assert len(_dividers(first)) == 2
    app.page.width = 1000
    host.on_resize()
    second = _nav(host)
    assert second is not first
    assert second.height == host.t.px(72)
    assert len(second.content.controls) == 2
    assert len(_dividers(second)) == 1
    app.page.width = 1160
    host.on_resize()
    third = _nav(host)
    assert third.height == host.t.px(36)
    assert isinstance(third.content, ft.Container)
    assert len(_dividers(third)) == 2
    # marker 跟随最终布局：宽→窄→宽 各记一次，最后一次仍是单行 2 条线。
    marks = [fields for event, fields in app.marks if event == "bib_inpage_nav"]
    assert [(m["narrow"], m["dividers"]) for m in marks] == [
        (False, 2), (True, 1), (False, 2)]


# ── D. 主题 / 路由 / 搜索 ──────────────────────────────────────────────────
def test_chip_colors_follow_theme_rebuild(monkeypatch) -> None:
    host, app = _host(monkeypatch)
    host.build()
    assert palette_remap("dark")[LIGHT.nav_sel] == DARK.nav_sel
    app.t = Tokens("dark")
    nav = host._bib_nav_control()
    dark = app.t.palette
    assert nav.bgcolor == dark.surface
    assert nav.border.bottom.color == dark.border
    active = [chip for chip in _chips(nav) if chip.bgcolor == dark.nav_sel]
    assert len(active) == 1
    assert active[0].content.color == dark.text
    assert all(chip.content.color == dark.text_muted
               for chip in _chips(nav) if chip is not active[0])


def test_ia_routes_views_and_inpage_catalog_stay_ten_views_twenty_two_routes() -> None:
    assert len(IA.all_routes()) == 22
    assert [view["key"] for view in IA.nav_views("bib")] == [
        key for key, _ in BIB_LEAVES] + ["settings"]
    assert IA.nav_views("bib")[-1]["route_only"] is True
    groups = IA.group_views("bib")
    assert [group["label"] for group in groups] == CAPTIONS
    assert [str(view["key"]) for group in groups for view in group["views"]] == [
        key for key, _ in BIB_LEAVES]
    assert IA.group_of_view("bib", "health") == "treat"
    assert IA.group_of_view("bib", "settings") == ""
    for key, _ in BIB_LEAVES:
        assert IA.normalize_route(f"bib:{key}") == ("bib", key)
    assert IA.normalize_route("bib:settings") == ("bib", "settings")


def test_search_index_still_lists_all_ten_bib_views() -> None:
    bib_views = [(view["key"], view["label"]) for view in IA.route_views("bib")]
    entries = SI.build_index(nav_tree=IA.NAV_TREE, bib_views=bib_views)
    pages = [entry for entry in entries
             if entry.kind == "page" and entry.action.startswith("goto:bib:")]
    assert len(pages) == 10
    labels = {entry.label for entry in pages}
    for _, label in BIB_LEAVES:
        assert f"引用库 · {label}" in labels
    assert "引用库 · 设置" in labels


def test_p2_bib_view_hook_still_routes_ten_views_to_bib() -> None:
    source = inspect.getsource(M.App.after_boot)
    assert 'P2_BIB_VIEW' in source
    assert 'self.navigate(f"bib:{bib_view}", source="env")' in source
    # 钩子白名单 = 宿主的 10 个视图键，BIB-NAV2 没有缩表。
    assert BH.BibHost.view_keys() == set(BH.VIEW_SPECS)
    assert len(BH.BibHost.view_keys()) == 10


def test_replant_nav_after_view_rerender_and_idempotent(monkeypatch) -> None:
    """批 116 验收回归（真行为）：视图异步重绘换掉 page_shell 后，宿主补插导航。

    复现路径：`navigate("bib:settings")` / 点击 dupes chip → 视图异步 load 完成走
    `_render()`，把 `_root.content` 整块换成新 `_body()`（新 page_shell，无导航），
    宿主 `_attach_nav` 插在旧面板里的导航随之丢失。修复：`BibHost._replant_nav()`
    幂等补插；五个视图的 `_render()` 在换内容后调用它（回调目标是 `self.page`
    = BibHost，不是顶层 App）。
    """
    host, app = _host(monkeypatch, data={"bib_view": "settings"})
    host.build()
    assert _nav(host)._bib_nav_active == ""  # settings 视图无选中 chip

    # 模拟视图 `_render`：整块换成新 page_shell（不带导航）。
    fresh = S.page_shell(host.t, title="叶", meta="meta",
                         body=ft.Container(expand=True), gear=True)
    root = host.view_slot.content
    if isinstance(root, ft.Container) and not getattr(root, "_bib_shell", False):
        root.content = fresh
    else:
        host.view_slot.content = fresh
    host._replant_nav()
    nav = _nav(host)
    assert len(_chips(nav)) == 9

    # 幂等：重复调用不再插入第二份导航。
    host._replant_nav()
    assert _nav(host) is nav
    controls = _panel(host).content.controls
    assert sum(1 for c in controls if getattr(c, "_bib_inpage_nav", False)) == 1


def test_five_write_views_replant_after_root_content_swap() -> None:
    """契约：五个异步重绘视图的回调对象必须是宿主（self.page），非顶层 App。"""
    from ui import bib_dupes, bib_edit, bib_fieldfix, bib_import, bib_prefs

    for module in (bib_dupes, bib_edit, bib_fieldfix, bib_import, bib_prefs):
        view_cls = next(getattr(module, name) for name in dir(module)
                        if name.endswith("View") and isinstance(getattr(module, name), type))
        render_source = inspect.getsource(view_cls._render)
        assert "_root.content = self._body()" in render_source
        tail = render_source[render_source.index("_root.content = self._body()"):]
        # 批 116 验收：回调目标修正为宿主 self.page（self.app 是顶层 App，恒无该方法）。
        assert 'getattr(self.page, "_replant_nav", None)' in tail, module.__name__

    host_source = inspect.getsource(BH.BibHost._replant_nav)
    assert "inner.controls.insert(0, nav)" in host_source
