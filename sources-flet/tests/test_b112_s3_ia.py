"""批 112 S3a1：引用库两级导航（10 视图 / 4 组 IA）契约测试。

覆盖 impl-plan §2.7 的 1–6、11：
1. VIEWS 10 项、IA 序、键唯一；
2. GROUPS 并集 = 10 且互斥、GROUP_ORDER 四组；
3. group_of 覆盖 10 个视图；
4. set_view 非法值回落 overview 并 mark("bib_view_invalid")；
5. set_view 写 bib_view 与 bib_group_memory[group]；
6. _render_nav 生成 4 组条 + 当前组胶囊，切组后胶囊集合与 active 迁移；
11. settings_store.pick 三态（缺键 / 非法 / 合法）。

另按 T6 加两条组件契约：group_tab 的 active 视觉、action_row 的点击转发。
"""
from __future__ import annotations

import flet as ft
import pytest

from services import settings_store as SS
from ui import components as C
from ui import pages as P
from ui.tokens import Tokens

IA_ORDER = [
    "overview", "query", "normalize", "import", "review",
    "edit", "dupes", "fieldfix", "health", "settings",
]


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
    def __init__(self) -> None:
        self.tasks: list = []

    def run_task(self, task, *args, **kwargs) -> None:
        if hasattr(task, "close"):
            task.close()
        self.tasks.append(task)


class FakeApp:
    def __init__(self, data: dict | None = None) -> None:
        self.store = FakeStore(data)
        self.t = Tokens()
        self.page = FakePage()
        self.marks: list[tuple[str, dict]] = []

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))

    def toast(self, message: str) -> None:
        pass


def _make_page(data: dict | None = None) -> tuple[P.BibPage, FakeApp]:
    """构造 BibPage，但不构建真实子视图、不碰 Flet 事件循环。"""
    app = FakeApp(data)
    page = P.BibPage(app)
    page._render = lambda: None
    page._safe_update = lambda *controls: None
    page._ensure_loaded = lambda view: None
    page._group_row = ft.Row(spacing=4, controls=[])
    page._chips_row = ft.Row(spacing=4, wrap=True, controls=[])
    page._notice_slot = ft.Container()
    page._status_slot = ft.Row(spacing=4, controls=[])
    for name in ("review_view", "settings_view", "dupes_view", "fieldfix_view", "health_view"):
        getattr(page, name).ensure_loaded = lambda *args, **kwargs: None
    page._render_nav()
    return page, app


def _tabs(page: P.BibPage) -> list[tuple[str, bool]]:
    """组条 → [(标签, 是否 active)]；active 由下划线颜色判定。"""
    out = []
    accent = page.t.palette.accent
    for tab in page._group_row.controls:
        col = tab.content
        out.append((col.controls[0].controls[0].value, col.controls[1].bgcolor == accent))
    return out


def _chips(page: P.BibPage) -> list[str]:
    return [chip.content.value for chip in page._chips_row.controls]


def _active_chips(page: P.BibPage) -> list[str]:
    soft = page.t.palette.accent_soft
    return [chip.content.value for chip in page._chips_row.controls if chip.bgcolor == soft]


# ── 1. 视图表 ──────────────────────────────────────────────────────────────
def test_views_are_ten_in_ia_order() -> None:
    keys = [key for key, _ in P.BibPage.VIEWS]
    assert len(keys) == 10
    assert len(set(keys)) == 10
    assert keys == IA_ORDER
    assert P.BibPage.view_keys() == set(IA_ORDER)
    # 规范化是新增项，插在入藏之前（采集 → 入藏的顺序不能反）。
    assert keys.index("normalize") < keys.index("import")
    assert keys.index("normalize") == keys.index("import") - 1
    # 设置退到末位（维护组）。
    assert keys[-1] == "settings"


# ── 2. 分组 ────────────────────────────────────────────────────────────────
def test_groups_are_four_and_partition_all_views() -> None:
    assert P.BibPage.GROUP_ORDER == ("read", "intake", "treat", "maint")
    assert len(P.BibPage.GROUP_ORDER) == 4
    flat = [key for group in P.BibPage.GROUP_ORDER for key in P.BibPage.GROUPS[group][1]]
    assert sorted(flat) == sorted(IA_ORDER)
    assert len(flat) == len(set(flat))  # 互斥
    for group in P.BibPage.GROUP_ORDER:
        label, keys = P.BibPage.GROUPS[group]
        assert label and keys


# ── 3. group_of ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("view,group", [
    ("overview", "read"), ("query", "read"),
    ("normalize", "intake"), ("import", "intake"),
    ("review", "treat"), ("edit", "treat"), ("dupes", "treat"), ("fieldfix", "treat"),
    ("health", "maint"), ("settings", "maint"),
])
def test_group_of_maps_every_view(view: str, group: str) -> None:
    assert P.BibPage.group_of(view) == group
    assert P.group_of(view) == group


def test_group_of_unknown_view_falls_back_to_first_group() -> None:
    assert P.BibPage.group_of("bogus") == P.BibPage.GROUP_ORDER[0]


# ── 4. 非法视图回落 ────────────────────────────────────────────────────────
def test_set_view_rejects_illegal_and_falls_back_to_overview() -> None:
    page, app = _make_page({"bib_view": "dupes"})
    assert page.view == "dupes"

    page.set_view("bogus")

    assert page.view == "overview"
    assert page.group == "read"
    assert app.store.get("bib_view") == "overview"
    invalid = [fields for event, fields in app.marks if event == "bib_view_invalid"]
    assert invalid == [{"page": "bib", "view": "bogus", "source": "legacy"}]


# ── 5. 持久化 ──────────────────────────────────────────────────────────────
def test_set_view_persists_view_and_group_memory() -> None:
    page, app = _make_page({"bib_view": "overview"})

    page.set_view("dupes")
    assert page.view == "dupes" and page.group == "treat"
    assert app.store.get("bib_view") == "dupes"
    assert app.store.get("bib_group_memory") == {"treat": "dupes"}

    page.set_view("query")
    assert app.store.get("bib_group_memory") == {"treat": "dupes", "read": "query"}

    # 点组条回到该组上次视图，而不是组内首项。
    page.set_group("treat")
    assert page.view == "dupes" and page.group == "treat"
    assert app.store.get("bib_view") == "dupes"

    # 无记忆的组 → 组内首项。
    page.set_group("intake")
    assert page.view == "normalize"


def test_set_view_marks_group_and_source() -> None:
    page, app = _make_page()
    page.set_view("fieldfix", source="group")
    assert any(event == "bib_view" and fields.get("view") == "fieldfix"
               and fields.get("group") == "treat" and fields.get("source") == "group"
               for event, fields in app.marks)
    # 兼容别名：_switch 透传 source（main.py 的 P2 钩子仍走它）。
    page._switch("health", source="env")
    assert any(event == "bib_view" and fields.get("view") == "health"
               and fields.get("source") == "env" for event, fields in app.marks)


# ── 6. 两级导航渲染 ────────────────────────────────────────────────────────
def test_render_nav_builds_group_bar_and_current_group_chips() -> None:
    page, _ = _make_page({"bib_view": "import"})

    assert [label for label, _ in _tabs(page)] == ["查阅", "采录", "治理", "维护"]
    assert [label for label, active in _tabs(page) if active] == ["采录"]
    assert _chips(page) == ["规范化", "入藏"]
    assert _active_chips(page) == ["入藏"]


def test_switch_group_changes_chips_and_moves_active_tab() -> None:
    page, app = _make_page({"bib_view": "import"})

    page._group_row.controls[3].on_click(None)  # 维护

    assert page.view == "health"
    assert page.group == "maint"
    assert app.store.get("bib_view") == "health"
    assert [label for label, active in _tabs(page) if active] == ["维护"]
    assert _chips(page) == ["库健康", "设置"]
    assert _active_chips(page) == ["库健康"]

    # 回「采录」：该组尚无记忆 → 组内首项（design §7 的默认：intake → normalize）。
    page._group_row.controls[1].on_click(None)
    assert page.view == "normalize"
    labels = _chips(page)
    assert labels == ["规范化", "入藏"]
    page._chips_row.controls[labels.index("入藏")].on_click(None)
    assert page.view == "import"
    assert _active_chips(page) == ["入藏"]

    # 离开再回来：沿用组内记忆 import，而不是组内首项。
    page._group_row.controls[0].on_click(None)
    assert page.view == "overview"
    page._group_row.controls[1].on_click(None)
    assert page.view == "import"


def test_only_write_views_carry_a_one_line_notice() -> None:
    page, _ = _make_page()
    seen = {}
    for key in P.BibPage.view_keys():
        page.set_view(key)
        seen[key] = page._notice_slot.content
    for key in ("import", "review", "edit", "dupes", "fieldfix"):
        assert seen[key] is not None, key
        assert seen[key].tooltip is not None, key  # 详情压到 tooltip
    for key in ("overview", "query", "normalize", "health", "settings"):
        assert seen[key] is None, key
    # 只读 / 写库权限上浮到页头状态芯片。
    page.set_view("query")
    assert [chip.content.value for chip in page._status_slot.controls] == ["只读"]
    page.set_view("edit")
    assert [chip.content.value for chip in page._status_slot.controls] == ["写库"]


# ── 11. settings_store.pick ────────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("query", "query"),
    ("overview", "overview"),
    ("bogus", "overview"),
    ("", "overview"),
    (None, "overview"),
    (123, "overview"),
    (["query"], "overview"),
    ({"v": "query"}, "overview"),
])
def test_pick_missing_invalid_and_valid(value, expected) -> None:
    store = FakeStore({"bib_view": value})
    assert SS.pick(store, "bib_view", P.BibPage.view_keys(), "overview") == expected


def test_pick_returns_default_on_missing_key() -> None:
    store = FakeStore({})
    assert SS.pick(store, "bib_view", P.BibPage.view_keys(), "overview") == "overview"
    assert SS.pick(store, "bib_group_memory", {"x": 1}, {}) == {}


def test_store_method_and_defaults(tmp_path) -> None:
    store = SS.SettingsStore(tmp_path / "settings.json")
    assert store.get("bib_view") == "overview"
    assert store.get("bib_group_memory") == {}
    store.set("bib_view", "dupes")
    assert store.pick("bib_view", P.BibPage.view_keys(), "overview") == "dupes"
    assert store.pick("bib_view", {"overview"}, "overview") == "overview"
    assert not (tmp_path / "settings.json").exists()  # 纯读取不写盘


# ── T6 组件契约 ────────────────────────────────────────────────────────────
def test_group_tab_active_state_is_color_and_underline() -> None:
    t = Tokens()
    on = C.group_tab("治理", t, active=True)
    off = C.group_tab("维护", t, active=False)
    assert on.content.controls[1].bgcolor == t.palette.accent
    assert off.content.controls[1].bgcolor is None
    assert on.content.controls[0].controls[0].color == t.palette.accent
    assert off.content.controls[0].controls[0].color == t.palette.text_muted
    # 比胶囊矮：组条无背景色。
    assert on.bgcolor is None


def test_action_row_forwards_click_and_keeps_one_line_caption() -> None:
    t = Tokens()
    hits: list = []
    row = C.action_row("GAVEL", "处理 3 条冲突", "逐条裁决", t,
                       on_click=lambda e: hits.append(e))
    row.on_click(None)
    assert hits == [None]
    assert row.ink is True
    middle = row.content.controls[1]
    assert middle.controls[0].value == "处理 3 条冲突"
    caption = middle.controls[1]
    assert caption.value == "逐条裁决"
    assert caption.max_lines == 1
    # 不可点时不出 ink 涟漪，也不留箭头以外的尾控件。
    passive = C.action_row("RULE", "只出报告", "", t)
    assert passive.ink is False
    assert len(passive.content.controls[1].controls) == 1
