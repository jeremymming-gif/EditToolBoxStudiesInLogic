"""批 116 NAV-B（用户拍板方案 B）：侧栏全扁平 + 整期进度徽标 + 单篇页内 chip。

契约（不建窗口、不跑引擎）：
1. 工作区三页（工作台/单篇稿件/整期装配）全是叶子：28 高、无 chevron、无子行；
   单篇点击沿用上次视图，整期点击走上次步骤。
2. 引用库（批 116 BIB-NAV2 决策）：侧栏只留 1 个叶「引用库」；3 组 9 视图
   改由 Bib 白面板顶部的页内导航承载（见 test_b116_bib_inpage_nav.py）。
3. 22 条路由与 issue 深链 alias 口径不变。
4. SinglePage h1 固定「单篇稿件」，convert/proofcheck 改为 28 高页内 chip。
5. 整期行右侧不再有「完成/4」进度徽标（批 120 B-353 删除）；行高仍 28。
6. 主题切换后深色模式开关的 thumb/track 四态颜色跟随新 Tokens（控件原位重建）。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from ui import ia_map as IA
from ui import pages as P
from ui.tokens import DARK, LIGHT, Tokens


class _Page:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.updated = 0

    def update(self) -> None:
        self.updated += 1


class _Store:
    """点号路径的小 store；整期目录用绝对路径（含 /，不会被 split）。"""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}

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
            current = current.setdefault(part, {})
        current[parts[-1]] = value


def _app(*, theme: str = "light", width: int = 1160, height: int = 780,
         data: dict | None = None) -> M.App:
    app = object.__new__(M.App)
    app.t = Tokens(theme, 1.0, 96)
    app.page = _Page(width, height)
    app.window_w, app.window_h = width, height
    app.current_page_key = "single"
    app.current_route = "single:convert"
    app.nav_open = {"page": "single", "group": "", "collapsed": False}
    app.sidebar_open = True
    app.sidebar = None
    app.bib_badge = None
    app._bib_nav_marked = False
    app.theme = theme
    app.engine_status = SimpleNamespace(ok=True)
    app._version_info = {"engine_version": "9.9.9"}
    app.app_version = "v9.9.9"
    app.marks = []
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.calls = []
    app.navigate = lambda route, source="nav": app.calls.append((route, source))
    app.on_theme_switch = lambda e: None
    app.store = _Store(data)
    return app


def _nav(app: M.App) -> ft.Column:
    root = app._sidebar_content()
    assert isinstance(root, ft.Column)
    nav_box = root.controls[1]
    assert isinstance(nav_box.content, ft.Column)
    return nav_box.content


def _walk(control) -> list:
    found: list = []

    def visit(node) -> None:
        if node is None:
            return
        found.append(node)
        for attr in ("controls", "content"):
            child = getattr(node, attr, None)
            if isinstance(child, (list, tuple)):
                for item in child:
                    visit(item)
            elif child is not None and not isinstance(child, str):
                visit(child)

    visit(control)
    return found


def _labels(control) -> list[str]:
    return [str(node.value or "") for node in _walk(control)
            if isinstance(node, ft.Text)]


def _row(nav: ft.Column, label: str):
    for row in nav.controls:
        if label in _labels(row):
            return row
    raise AssertionError(f"侧栏找不到行：{label}")


# ── 1. 工作区全叶子 ─────────────────────────────────────────────────────────
def test_workspace_has_three_flat_leaves_without_chevron_or_children() -> None:
    app = _app()
    nav = _nav(app)
    # 批 116 BIB-NAV2：2 段标签 + 3 工作区叶 + 1 引用库叶 + 2 系统叶 = 8。
    assert len(nav.controls) == 8

    for label in ("工作台", "单篇稿件", "整期装配"):
        row = _row(nav, label)
        assert row.height == app.t.px(28)
        # 只有 icon + label；第三个 20×20 chevron 热区不存在。
        assert len(row.content.controls) == 2
        assert isinstance(row.content.controls[0], ft.Icon)
        assert isinstance(row.content.controls[1], ft.Text)

    labels = _labels(nav)
    for gone in ("初排转换", "一校核查", "检查tex信息", "生成目录", "合并文件"):
        assert gone not in labels, f"扁平侧栏不应再出现二级子项：{gone}"

    chevrons = [node for node in _walk(nav)
                if isinstance(node, ft.Icon)
                and str(getattr(node, "name", "")) in ("expand_more", "chevron_right")]
    assert chevrons == [], "方案 B 侧栏不应有任何展开/收起箭头"


# ── 2. 引用库：批 116 BIB-NAV2 决策 = 侧栏单叶 ─────────────────────────────
def test_bib_has_one_sidebar_leaf_and_no_group_captions_or_children() -> None:
    app = _app()
    app.current_page_key = "bib"
    nav = _nav(app)
    leaf = nav.controls[4]
    assert leaf.content.controls[0].icon == ft.Icons.LIBRARY_BOOKS_OUTLINED
    assert leaf.content.controls[1].value == "引用库"
    assert leaf.height == app.t.px(28)
    assert leaf.bgcolor == app.t.palette.nav_sel, "bib:* 路由下该叶选中"
    assert callable(leaf.on_click)

    labels = _labels(nav)
    for gone in ("查阅", "采录", "治理", "概览", "查库", "入藏", "回读校验",
                 "待处理", "编辑", "查重合并", "字段归位", "库健康"):
        assert gone not in labels, f"引用库子项应在页内导航，不在侧栏：{gone}"
    assert labels.count("引用库") == 1
    assert "设置" in labels and labels.index("设置") > labels.index("引用库")


def test_bib_leaf_stays_visible_when_other_page_collapsed() -> None:
    app = _app()
    app.current_page_key = "home"
    app.nav_open = {"page": "single", "group": "", "collapsed": True}
    labels = _labels(_nav(app))
    assert "引用库" in labels
    assert "初排转换" not in labels


# ── 3. 路由口径不变 ────────────────────────────────────────────────────────
def test_all_routes_and_issue_alias_unchanged() -> None:
    assert len(IA.all_routes()) == 22
    assert "single:convert" in IA.all_routes()
    assert "single:proofcheck" in IA.all_routes()
    assert "issue:overview" in IA.all_routes()
    # 深链 alias：裸 issue / issue:overview 都静态落到第一步（运行期再读 last_step）。
    assert IA.is_issue_landing_route("issue")
    assert IA.is_issue_landing_route("issue:overview")
    assert not IA.is_issue_landing_route("issue:toc")
    assert IA.normalize_route("issue:overview") == ("issue", "infocheck")
    assert IA.normalize_route("issue") == ("issue", "infocheck")
    # 侧栏扁平不改数据：单篇仍有两个 views，整期仍无 views（6 步仅路由）。
    assert [v["key"] for v in IA.nav_views("single")] == ["convert", "proofcheck"]
    assert IA.nav_views("issue") == []


# ── 4. 点击叶子：单篇记上次视图，整期走默认步 ──────────────────────────────
def test_single_leaf_click_opens_last_view() -> None:
    app = _app(data={"single_flow": "proofcheck"})
    row = _row(_nav(app), "单篇稿件")
    row.on_click(None)
    assert app.calls == [("single:proofcheck", "nav")]


def test_issue_leaf_click_navigates_page_default_route() -> None:
    app = _app()
    app._nav_default_route = lambda key: {"issue": "issue:toc"}.get(key, key)
    row = _row(_nav(app), "整期装配")
    row.on_click(None)
    assert app.calls == [("issue:toc", "nav")]


# ── 5. SinglePage：h1 固定 + 页内 chip ────────────────────────────────────
def _single_page():
    from test_b115_titleflow import _build
    return _build(P.SinglePage)


def test_single_page_has_two_28px_flow_chips_with_selected_state() -> None:
    page, _root = _single_page()
    chips = page._flow_chips
    assert [chip.content.value for chip in chips] == ["初排转换", "一校核查"]
    assert all(chip.height == page.t.px(28) for chip in chips)
    assert all(chip.alignment == ft.Alignment.CENTER for chip in chips)
    # 批 119：选中态与整期/引用库统一为 palette 的 nav_sel，不反白、不铺主色。
    assert chips[0].bgcolor == page.t.palette.nav_sel
    assert chips[1].bgcolor is None
    assert chips[0].bgcolor != page.t.palette.accent


def test_single_chip_click_switches_flow_and_keeps_h1() -> None:
    page, _root = _single_page()
    page._flow_chips[1].on_click(None)
    assert page.flow_key == "proofcheck"
    chips = page._flow_chips
    assert chips[0].bgcolor is None
    assert chips[1].bgcolor == page.t.palette.nav_sel
    assert page._child_title.value == "单篇稿件"
    assert "一校核查" in page._flow_desc.value


# ── 6. 整期行：进度徽标已删（批 120 B-353）─────────────────────────────────
def _issue_data(statuses: dict) -> dict:
    return {
        "recent": {"issue_dir": "/tmp/一期"},
        "issue_state": {"/tmp/一期": {"schema": 1, "statuses": statuses}},
    }


def test_issue_row_has_no_done_over_four_progress_badge() -> None:
    data = _issue_data({
        "infocheck": {"state": "done"},
        "toc": {"state": "warn"},       # 旧口径算「完成」，现已不显示
        "merge": {"state": "error"},    # 失败不计
        "cleantemp": {"state": "running"},
    })
    app = _app(data=data)
    row = _row(_nav(app), "整期装配")
    assert row.height == app.t.px(28), "整期行仍为 28 高"
    assert len(row.content.controls) == 2, "整期行不应再带进度徽标控件"
    assert not any("/" in str(node.value or "")
                   for node in _walk(row) if isinstance(node, ft.Text))


def test_issue_badge_helper_construction_removed() -> None:
    src = inspect.getsource(M.App)
    assert ("_issue_" + "progress") not in src
    assert ("done}/{" + "total}") not in src
    assert ("_issue_" + "progress") not in inspect.getsource(M.App._sidebar_content)


def test_issue_row_stays_plain_without_dir_or_state() -> None:
    cases = [
        {},                                              # 无任何状态
        {"issue_state": {"/tmp/x": {"statuses": {"toc": {"state": "done"}}}}},
        {"recent": {"issue_dir": "/tmp/一期"}, "issue_state": {}},
        {"recent": {"issue_dir": "/tmp/一期"},
         "issue_state": {"/tmp/一期": {"schema": 1}}},   # 有记录但没有 statuses
    ]
    for data in cases:
        app = _app(data=data)
        row = _row(_nav(app), "整期装配")
        assert len(row.content.controls) == 2, f"不应显示徽标：{data}"
        assert row.height == app.t.px(28)


# ── 7. 主题开关重染（批 116 附带瑕疵）──────────────────────────────────────
def _theme_shell():
    from test_b116_theme_roundtrip import _CachedPage, _app as shell, _mount
    app = shell()
    home = _CachedPage(app)
    app.pages = {"home": home}
    home.ensure_built()
    _mount(app, home)
    app.theme_switch = ft.Switch(value=False, active_color=LIGHT.accent)
    app._theme_row = ft.Row(controls=[
        ft.Icon("dark_mode", size=16), ft.Text("深色模式"),
        ft.Container(expand=True), app.theme_switch])
    return app


def test_theme_switch_thumb_and_track_follow_light_dark_roundtrip() -> None:
    app = _theme_shell()
    old = app.theme_switch
    assert 3 == app._theme_row.controls.index(old)

    app.set_theme("dark")
    dark = app.theme_switch
    assert dark is not old, "ControlStateValue 颜色无法靠改 str 属性重染，必须重建"
    assert app._theme_row.controls[3] is dark, "原位替换，尺寸/位置不变"
    assert dark.value is True
    assert dark.on_change == app.on_theme_switch
    assert dark.thumb_color == {
        ft.ControlState.DEFAULT: DARK.surface,
        ft.ControlState.SELECTED: DARK.on_accent,
        ft.ControlState.DISABLED: DARK.surface_3,
        ft.ControlState.HOVERED: DARK.surface,
    }
    assert dark.track_color == {
        ft.ControlState.DEFAULT: DARK.text_muted,
        ft.ControlState.SELECTED: DARK.accent,
        ft.ControlState.DISABLED: DARK.border,
        ft.ControlState.HOVERED: DARK.text_muted,
    }

    app.set_theme("light")
    light = app.theme_switch
    assert light is not dark
    assert app._theme_row.controls[3] is light
    assert light.value is False
    assert light.thumb_color[ft.ControlState.DEFAULT] == LIGHT.surface
    assert light.thumb_color[ft.ControlState.SELECTED] == LIGHT.on_accent
    assert light.track_color[ft.ControlState.SELECTED] == LIGHT.accent
    assert light.track_color[ft.ControlState.DEFAULT] == LIGHT.text_muted
