"""批 115 B0 契约测试：共享壳（bib_shell）+ 表格（bib_table）。

测试只碰控件树和状态，不启动主 App / 不开窗口——这样在 CI 无桌面环境也能跑。
每个用例对应设计稿 §4/§9 的一条硬线，失败信息里写清“哪条线断了”。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from ui import components as C
from ui import bib_shell as S
from ui import bib_table as T
from ui.tokens import Tokens

APP = Path(__file__).resolve().parents[1] / "app" / "ui"
SHELL_SRC = (APP / "bib_shell.py").read_text(encoding="utf-8")
TABLE_SRC = (APP / "bib_table.py").read_text(encoding="utf-8")


class _FakePage:
    """最小 Page 替身：只实现本包用到的对话框栈、服务和热键字段。"""

    def __init__(self) -> None:
        self.on_keyboard_event = None
        self.dialogs = []
        self.services = []
        self.tasks = []

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)


class _FakeField:
    """可记录 focus 的输入框替身（⌘F 测试用）。"""

    def __init__(self) -> None:
        self.focused = False
        self.updated = False

    def focus(self) -> None:
        self.focused = True

    def update(self) -> None:
        self.updated = True


def _texts(control) -> list[str]:
    """递归收集控件树里的字符串，用来断言说明文字/影响条数确实渲染出来。"""
    out: list[str] = []
    if control is None:
        return out
    if isinstance(control, str):
        return [control]
    value = getattr(control, "value", None)
    if isinstance(value, str):
        out.append(value)
    content = getattr(control, "content", None)
    out.extend(_texts(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_texts(child))
    for child in getattr(control, "actions", None) or ():
        out.extend(_texts(child))
    return out


# ── 静态硬线 ───────────────────────────────────────────────────────────────
def test_sources_have_no_hex_or_raw_material_colors() -> None:
    """颜色只经 palette：源码里不许出现 #RRGGBB，也不许 ft.Colors。"""
    for name, src in (("bib_shell", SHELL_SRC), ("bib_table", TABLE_SRC)):
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", src), f"{name} 里出现十六进制色"
        assert "ft.Colors" not in src, f"{name} 绕过了 palette"


def test_sources_do_not_build_bare_inputs_or_buttons() -> None:
    """禁止裸 ft.TextField/Dropdown/*Button：几何/主题重染只能走 C 封装。"""
    forbidden = {
        "TextField", "Dropdown", "PopupMenuButton", "FilledButton", "OutlinedButton",
        "TextButton", "IconButton", "ElevatedButton", "CupertinoButton",
    }
    for name, src in (("bib_shell", SHELL_SRC), ("bib_table", TABLE_SRC)):
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "ft":
                assert node.func.attr not in forbidden, (
                    f"{name}:{node.lineno} 直接构造了 ft.{node.func.attr}")
            if isinstance(owner, ast.Name) and owner.id == "C":
                assert node.func.attr != "card", (
                    f"{name}:{node.lineno} 页面主体用了 C.card")


def test_every_helper_has_a_why_docstring() -> None:
    """每个 helper 的 docstring 都要解释“为什么这样规定”，不是复述函数名。"""
    for name, src in (("bib_shell", SHELL_SRC), ("bib_table", TABLE_SRC)):
        tree = ast.parse(src)
        assert ast.get_docstring(tree) and len(ast.get_docstring(tree)) > 200
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node) or ""
                assert len(doc) >= 8, f"{name}:{node.lineno} {node.name} 缺 docstring"


def test_geometry_constants_match_design() -> None:
    """1160/1000 共用的几何只允许这一组值；tokens 的 ROW_H 不能被绕开。"""
    t = Tokens()
    assert (S.PAGE_HEADER_H, S.FILTER_ROW_H, S.TABLE_HEADER_H) == (72, 44, 30)
    assert (S.TABLE_ROW_H, S.DETAIL_W, S.PARAMS_W) == (32, 360, 356)
    assert (S.SUMMARY_H, S.CONFIRM_H, S.BANNER_H, S.BATCH_H) == (58, 56, 44, 44)
    assert (S.DOCK_H, S.DOCK_H_EXPANDED, S.NARROW_W) == (40, 200, 1000)
    assert T.TABLE_HEADER_H == S.TABLE_HEADER_H == t.row_h("table") - 2
    assert T.TABLE_ROW_H == S.TABLE_ROW_H == t.row_h("table")
    assert t.control_h() == 28


# ── 页面壳 ─────────────────────────────────────────────────────────────────
def test_page_shell_header_body_dock_do_not_touch_status_bar() -> None:
    """白面板 + 72 页头 + 24 主体 + 40/200 dock；状态栏 26 不归本壳。"""
    t = Tokens()
    shell = S.page_shell(t, title="引用库", meta="只读视图 · 3,883 条",
                         role_chip="readonly", body=C.text("body", t))
    assert shell.bgcolor == t.palette.surface
    assert shell.border_radius.top_left == t.px(10)
    assert shell._bib_header.height == 72
    assert shell._bib_body.padding.left == 24
    assert shell._bib_body.padding.right == 24
    assert shell._bib_title.value == "引用库"
    assert shell._bib_title.size == 22
    assert shell._bib_meta.size == 11
    assert shell._bib_dock is None
    assert "StatusBar" not in SHELL_SRC

    docked = S.page_shell(t, title="引用库", meta="m", body=C.text("b", t),
                          dock=C.text("log", t), dock_height=S.DOCK_H_EXPANDED)
    assert docked._bib_dock.height == 200


def test_page_shell_notice_action_gear_and_single_action_rule() -> None:
    """通知条 28 槽位；页头 ≤1 动作；齿轮无回调时禁用而不是假按钮。"""
    t = Tokens()
    shell = S.page_shell(t, title="引用库", meta="m", role_chip="write",
                         action=C.text_button("导出", None, t, height=28),
                         body=C.text("b", t), notice="已按 2024 年筛选")
    title_row = shell._bib_header.content.controls[0].content
    gear = title_row.controls[-1]
    assert isinstance(gear, ft.IconButton) and gear.disabled is True
    assert shell._bib_notice.height == 36  # 8 顶距 + 28 通知条 = 164..192
    assert shell._bib_chip.data == "bib-role:write"
    assert "写库" in _texts(shell._bib_chip)

    enabled = S.page_shell(t, title="引用库", meta="m", body=C.text("b", t),
                           on_gear=lambda e: None)
    assert enabled._bib_header.content.controls[0].content.controls[-1].disabled is False

    with pytest.raises(ValueError, match="最多一个动作"):
        S.page_shell(t, title="引用库", meta="m", body=C.text("b", t),
                     action=[C.text_button("A", None, t), C.text_button("B", None, t)])


def test_role_chip_three_modes_and_unknown_rejected() -> None:
    """权限 chip 固定 readonly/write/stale 三态；未知语气直接报错。"""
    t = Tokens()
    assert S.role_chip(t, "readonly").data == "bib-role:readonly"
    assert S.role_chip(t, "write").data == "bib-role:write"
    assert S.role_chip(t, "stale").data == "bib-role:stale"
    assert "过期" in _texts(S.role_chip(t, "stale"))
    with pytest.raises(ValueError):
        S.role_chip(t, "admin")


# ── 只读骨架 ───────────────────────────────────────────────────────────────
def test_filter_row_height_and_cmd_f_chaining() -> None:
    """筛选行 44 / 控件 28；⌘F 只拦 F，其余键必须转交主壳热键。"""
    t = Tokens()
    row = S.filter_row(t, count="10 / 3,883")
    assert row.height == 44
    assert row._bib_filter_field.height == 28
    assert row._bib_filter_field.prefix_icon is not None

    page = _FakePage()
    prev_calls = []
    page.on_keyboard_event = lambda e: prev_calls.append(e.key)
    field = _FakeField()
    handler = S.bind_filter_shortcut(page, field)
    handler(SimpleNamespace(key="f", meta=True, ctrl=False, alt=False))
    assert field.focused is True
    handler(SimpleNamespace(key="k", meta=True, ctrl=False, alt=False))
    assert prev_calls == ["k"]

    second = _FakeField()
    assert S.bind_filter_shortcut(page, second) is handler
    handler(SimpleNamespace(key="f", meta=False, ctrl=True, alt=False))
    assert second.focused is True


def test_table_header_and_row_contract() -> None:
    """表头 30 / 行 32 / 选中 accent_soft / 行底 0.6 / 无竖线 / 箭头 8px。"""
    t = Tokens()
    picked = []
    header = S.table_header_row(t, ["题名", "年"], weights=[3.2, 0.8],
                                sort_index=0, on_sort=picked.append)
    assert header.height == 30
    assert header._bib_sorted == "asc"
    arrow = header.content.controls[0].content.controls[-1]
    assert isinstance(arrow, ft.Icon) and arrow.size == 8
    header.content.controls[0].on_click(None)
    assert picked == [0]

    clicks = []
    row = S.table_row(t, ["一篇稿件", "2024"], weights=[3.2, 0.8],
                      selected=True, on_click=lambda e: clicks.append(1))
    assert row.height == 32
    assert row.bgcolor == t.palette.accent_soft
    assert row.border.bottom.width == 0.6
    # 无竖线：左右边是 0 宽 NONE，而不是 None（Flet Border.only 的表示法）。
    assert row.border.left.width == 0 and row.border.right.width == 0
    row.on_click(None)
    assert clicks == [1]

    row.on_hover(SimpleNamespace(data=True))
    assert row.bgcolor == t.palette.accent_soft  # 选中态不被 hover 冲掉


def test_hover_only_on_unselected_row() -> None:
    """hover 只给未选中行铺 surface_2；鼠标移出恢复透明。"""
    t = Tokens()
    row = S.table_row(t, ["题名", "2024"], on_click=lambda e: None)
    row.on_hover(SimpleNamespace(data=True))
    assert row.bgcolor == t.palette.surface_2
    row.on_hover(SimpleNamespace(data=False))
    assert row.bgcolor is None


def test_detail_panel_empty_row_and_batch_bar() -> None:
    """详情 360；空态只占一行 + 动作；批量条仅多选时出现。"""
    t = Tokens()
    detail = S.detail_panel(t, title="详情", body=[C.text("x", t)],
                            actions=[C.text_button("关闭", None, t)])
    assert detail.width == 360
    assert detail.border.left.width >= 1
    # ft.Border.only 把未指定的边建成 0 宽（不是 None），锁 0 才算“无右边线”。
    assert detail.border.right.width == 0

    empty = S.empty_row(t, "没有匹配记录", action=C.text_button("清除筛选", None, t))
    assert empty.height == 32
    assert empty._bib_empty_row is True
    assert len(empty.content.controls) == 2

    assert S.batch_bar(t, count=0) is None
    bar = S.batch_bar(t, count=3, actions=[C.text_button("加入待处理", None, t)],
                      on_clear=lambda: None)
    assert bar.height == 44
    assert bar._bib_batch_bar is True
    assert "已选 3 条" in _texts(bar)


# ── 写库骨架 ───────────────────────────────────────────────────────────────
def test_write_layout_two_columns_and_1000_single_column() -> None:
    """写库左 356 / 右 flex；1000 宽退单列，确认条不被参数区压住。"""
    t = Tokens()
    confirm = S.confirm_bar(t, consequence="将新增 3 条", on_confirm=lambda: None,
                            on_repreview=lambda: None)
    wide = S.write_layout(t, params=C.text("参数", t), preview=C.text("预览", t),
                          confirm=confirm)
    assert isinstance(wide, ft.Row)
    assert wide.controls[0].width == 356
    assert isinstance(wide.controls[1], ft.Container)
    assert wide._bib_confirm is confirm

    narrow = S.write_layout(t, params=C.text("参数", t), preview=C.text("预览", t),
                            confirm=confirm, narrow=True)
    assert isinstance(narrow, ft.Column)
    assert len(narrow.controls) == 3
    assert narrow._bib_narrow is True


def test_summary_band_and_confirm_bar_states() -> None:
    """摘要带 58/52；确认条 56，左侧后果+sha，stale 时主按钮换成重新预览。"""
    t = Tokens()
    band = S.summary_band(t, [
        {"label": "新增", "value": "12"},
        {"label": "更新", "value": "8", "tone": "warn"},
        {"label": "冲突", "value": "3", "tone": "error"},
    ])
    assert band.height == 58
    assert band._bib_band is True
    assert S.summary_band(t, [{"label": "a", "value": "1"}], dense=True).height == 52

    bar = S.confirm_bar(t, consequence="将新增 3 条、更新 2 条", sha="a1b2c3d4",
                        on_confirm=lambda: None, on_repreview=lambda: None)
    assert bar.height == 56
    assert isinstance(bar._bib_confirm_btn, ft.FilledButton)
    assert isinstance(bar._bib_repreview_btn, ft.OutlinedButton)
    assert "a1b2c3d4" in " ".join(_texts(bar))

    stale = S.confirm_bar(t, consequence="写入已禁用 · 未落盘", sha="a1b2c3d4",
                          stale=True, on_confirm=lambda: None,
                          on_repreview=lambda: None)
    assert isinstance(stale._bib_confirm_btn, ft.OutlinedButton)
    assert stale._bib_confirm_btn.disabled is True
    assert isinstance(stale._bib_repreview_btn, ft.FilledButton)


def test_result_banner_and_stale_notice() -> None:
    """成功横幅 44 带“去待处理/回退”；过期说明并排两个 sha。"""
    t = Tokens()
    banner = S.result_banner(t, detail="新增 3 条 · 已写 bibliography.jsonl",
                             on_review=lambda: None, on_revert=lambda: None)
    assert banner.height == 44
    assert banner._bib_review_btn.content == "去待处理"
    assert banner._bib_revert_btn.content == "回退本次写入"

    stale = S.stale_notice(t, expected_sha="a1b2c3d4", actual_sha="c3d4e5f6",
                           on_repreview=lambda: None)
    assert stale._bib_stale is True
    joined = " ".join(_texts(stale))
    assert "a1b2c3d4" in joined and "c3d4e5f6" in joined


# ── 七态 / 危险确认 ─────────────────────────────────────────────────────────
def test_state_banner_covers_all_seven_states() -> None:
    """七态统一出口：空/加载/运行/成功/失败/取消/过期，一个都不许漏。"""
    t = Tokens()
    empty = S.state_banner(t, "empty", message="没有结果",
                           action=C.text_button("清除筛选", None, t))
    assert empty._bib_state == "empty"
    assert "没有结果" in _texts(empty)

    loading = S.state_banner(t, "loading")
    assert loading._bib_state == "loading" and loading._bib_skeletons == 3

    cancel = []
    running = S.state_banner(t, "running", stage="正在写入…", on_cancel=lambda: cancel.append(1))
    assert running._bib_state == "running" and running._bib_cancel is not None

    success = S.state_banner(t, "success", message="写入完成", on_next=lambda: None)
    assert success._bib_state == "success"

    page = _FakePage()
    failure = S.state_banner(t, "failure", message="写入失败", stderr="Traceback: boom",
                             page=page, on_next=lambda: None)
    assert failure._bib_state == "failure"
    assert failure._bib_stderr is not None
    banner = failure.controls[0]
    copy_btn = banner.content.controls[2]
    assert copy_btn.content == "复制 stderr"
    copy_btn.on_click(None)
    assert len(page.tasks) == 1  # Clipboard 协程交给 page.run_task，不阻塞 UI

    cancelled = S.state_banner(t, "cancelled", message="已取消")
    assert cancelled._bib_state == "cancelled"

    stale = S.state_banner(t, "stale", expected_sha="a1b2", actual_sha="c3d4")
    assert stale._bib_state == "stale"

    with pytest.raises(ValueError):
        S.state_banner(t, "exploded")


def test_danger_confirm_requires_impact_and_cleans_dialog_stack() -> None:
    """危险确认必须写影响条数；确认后要出栈，Esc 才不会关错框。"""
    t = Tokens()
    C._open_dialogs.clear()
    page = _FakePage()
    with pytest.raises(ValueError, match="impact"):
        S.danger_confirm(page, t, title="删除", message="不可撤销", impact=None)

    calls = []
    dialog = S.danger_confirm(page, t, title="删除 3 条记录",
                              message="删除后不可恢复", impact=3,
                              confirm_label="删除", on_confirm=lambda: calls.append(1))
    try:
        assert dialog.modal is True
        assert dialog._bib_impact == "将影响 3 条记录"
        assert dialog.actions[0].content == "取消"
        assert dialog.actions[1].content == "删除"
        assert dialog in C._open_dialogs
        dialog.actions[1].on_click(None)
        assert calls == [1]
        assert page.dialogs == []
        assert dialog not in C._open_dialogs
    finally:
        C._open_dialogs.clear()


# ── 表格：排序 / 选中 / 空态 ───────────────────────────────────────────────
def _cols() -> list[T.BibColumn]:
    return [
        T.BibColumn("title", "题名", flex=3.2),
        T.BibColumn("year", "年", flex=0.8, align="right"),
        T.BibColumn("key", "Key", flex=1.9, mono=True),
    ]


def _rows(n: int = 5) -> list[dict]:
    return [{"title": f"稿件 {i}", "year": 2000 + i, "key": f"k{i}"} for i in range(n)]


def test_table_sorting_toggle_stable_and_none_last() -> None:
    """同列点两次翻转；换列回升序；空值永远沉底；未知列报错。"""
    t = Tokens()
    seen = []
    table = T.BibTable(t, _cols(), [
        {"title": "B", "year": 2001, "key": "b"},
        {"title": "A", "year": None, "key": "a"},
        {"title": "C", "year": 2000, "key": "c"},
    ], on_sort=lambda key, desc: seen.append((key, desc)))

    table.sort_by("year")
    assert [r["year"] for r in table.visible_rows()] == [2000, 2001, None]
    table.sort_by("year")
    assert [r["year"] for r in table.visible_rows()] == [2001, 2000, None]
    assert seen == [("year", False), ("year", True)]
    table.sort_by("title")
    assert [r["title"] for r in table.visible_rows()] == ["A", "B", "C"]
    with pytest.raises(KeyError):
        table.sort_by("nope")


def test_table_selection_click_callback_and_rebuild() -> None:
    """点整行 → 回调 + accent_soft；重建后选中仍在；可按 key/下标/清空。"""
    t = Tokens()
    clicked, changed = [], []
    table = T.BibTable(t, _cols(), _rows(), row_key="key",
                       on_row_click=clicked.append, on_change=lambda: changed.append(1))
    tree = table.build()
    first_row = tree.controls[1].controls[0]
    first_row.on_click(None)
    assert clicked and clicked[0]["key"] == "k0"
    assert changed == [1]
    rebuilt = table.build()
    assert rebuilt.controls[1].controls[0]._bib_selected is True
    assert rebuilt.controls[1].controls[0].bgcolor == t.palette.accent_soft

    table.select("k2")
    assert table.is_selected(table.rows[2])
    table.select(0)
    assert table.is_selected(table.visible_rows()[0])
    table.clear_selection()
    assert not table.is_selected(table.rows[0])


def test_table_empty_state_keeps_header_and_action() -> None:
    """空态保留表头，只给一行话 + 一个动作，不能退回 180 高卡片。"""
    t = Tokens()
    action = C.text_button("清除筛选", None, t)
    table = T.BibTable(t, _cols(), [], empty_message="没有匹配记录", empty_action=action)
    tree = table.build()
    assert tree.controls[0].height == 30
    empty = tree.controls[1].controls[0]
    assert empty._bib_empty is True
    assert "没有匹配记录" in _texts(empty)
    assert action in empty.content.controls


# ── 表格：limit / 虚拟化 / scroll_x fallback ────────────────────────────────
def test_table_limit_load_more_and_footer() -> None:
    """首屏 100；载入更多 +100；到底后 footer 消失，不占位。"""
    t = Tokens()
    noticed = []
    table = T.BibTable(t, _cols(), _rows(250), limit=100,
                       on_load_more=noticed.append)
    assert (table.total, table.visible_count, table.remaining) == (250, 100, 150)
    tree = table.build()
    footer = tree.controls[-1]
    assert footer._bib_load_more is True
    footer.content.on_click(None)
    assert table.visible_count == 200
    assert table.remaining == 50
    assert noticed == [200]
    table.load_more()
    assert table.visible_count == 250 and table.has_more is False
    final = table.build()
    assert len(final.controls) == 2  # header + body，footer 已消失


def test_table_300_rows_column_and_301_rows_listview_on_scroll() -> None:
    """≤300 全量 Column；>300 才上 ListView + on_scroll 自动载入更多。"""
    t = Tokens()
    small = T.BibTable(t, _cols(), _rows(300))
    small_tree = small.build()
    assert isinstance(small_tree.controls[1], ft.Column)
    assert getattr(small_tree.controls[1], "on_scroll", None) is None

    big = T.BibTable(t, _cols(), _rows(301))
    big_tree = big.build()
    body = big_tree.controls[1]
    assert isinstance(body, ft.ListView)
    assert body.on_scroll is not None
    big._scrolled(SimpleNamespace(pixels=1000, viewport_dimension=400,
                                  max_scroll_extent=1400))
    assert big.visible_count == 200


def test_table_scroll_x_fixed_width_fallback_is_documented() -> None:
    """无原生 sticky：scroll_x 整表横滚、首列固定宽；模块文档写明退化原因。"""
    t = Tokens()
    table = T.BibTable(t, _cols(), _rows(2), scroll_x=True)
    tree = table.build()
    # scroll_x 只保留一个 body（表头在 body 的横向 grid 里）；否则会出现两条表头。
    assert len(tree.controls) == 1
    body = tree.controls[0]
    assert isinstance(body, ft.ListView) and body._bib_scroll_x is True
    scroller = body.controls[0]
    assert isinstance(scroller, ft.Row) and scroller.scroll is not None
    grid = scroller.controls[0]
    assert grid.width == table._total_width()
    assert "sticky" in T.STICKY_FALLBACK_NOTE
    assert "首列" in T.STICKY_FALLBACK_NOTE


def test_table_set_rows_keeps_selection_by_business_key() -> None:
    """刷新换新 dict 时按 row_key 保留选中；空结果清空选中。"""
    t = Tokens()
    table = T.BibTable(t, _cols(), _rows(3), row_key="key")
    table.select("k1")
    table.set_rows([{"title": "新稿", "year": 2025, "key": "k1"},
                    {"title": "另一篇", "year": 2024, "key": "k9"}])
    assert table.is_selected(table.rows[0])
    table.set_rows([])
    assert table.total == 0 and table.visible_count == 0
    assert not table.has_more


def test_table_column_dict_format_and_cell_styles() -> None:
    """列可用 dict 定义；format 负责显示值；mono 列走等宽字体，数字右对齐。"""
    t = Tokens()
    table = T.BibTable(t, [
        {"key": "title", "label": "题名", "flex": 3.2},
        {"key": "year", "label": "年", "flex": 0.8, "align": "right",
         "format": lambda v: f"{v} 年"},
        {"key": "doi", "label": "DOI", "flex": 1.6, "mono": True},
    ], [{"title": "甲", "year": 2024, "doi": "10.1234/x"}])
    tree = table.build()
    row = tree.controls[1].controls[0]
    cells = row.content.controls
    assert cells[1].alignment == ft.Alignment.CENTER_RIGHT
    assert cells[1].content.value == "2024 年"
    assert cells[2].content.font_family == t.mono_family
