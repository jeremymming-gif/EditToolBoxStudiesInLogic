"""批 116 ISSUE4-C/D：rail 打磨 + 视图内手风琴（替代 flet 1.0.5 无效的 scroll_to）。

锁住的不变量（纯壳层，不建窗口、不跑引擎）：
1. rail 不再画 `01–02` 序号列（ISSUE4-D 用户拍板）；四行同高 64，
   标题 + 状态点同行、状态点右对齐；
2. 去掉序号列后 caption 放大可用宽、完整显示：不设 max_lines、不用省略号，
   也不以序号开头/复述标题；
3. 同一视图同一时刻只展开一个区块，默认首块（01/03/05/06），chevron ▸/▾；
4. 点区块头切换、点主按钮不切换；收起区块内容 visible=False，但头部状态/主按钮仍刷新；
5. 深链 4 条展开目标区块；切视图保留各视图最近展开的区块；
6. 03 编译成功 → 04 展开、03 收起（不自动运行 04）；其他步骤完成不切换；
7. 窄窗 54 横条 4 段都用短标题显示，同样不显示序号。
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import flet as ft

from ui import assembly as ASM
from ui import ia_map as IA
from test_b112_post_b import _build_issue, _walk


def _issue_page(issue: str = "/tmp/期目录"):
    page, _root = _build_issue()
    page.set_field("issue_dir", issue)
    return page


def _texts(control) -> list[str]:
    return [str(ctl.value) for ctl in _walk(control) if isinstance(ctl, ft.Text)]


def _click(control) -> None:
    control.on_click(SimpleNamespace(control=control))


def _approx_text_width(value: str, size: float) -> float:
    """保守估宽：CJK/全角 1em，其余 0.62em（对空格偏宽，安全）。"""
    wide = sum(1 for ch in value if ord(ch) > 0x2E7F)
    return wide * size + (len(value) - wide) * size * 0.62


# ── 1. 页内 chip：无序号 / 完整标题 / 28 高 ─────────────────────────
def test_nav_chips_have_no_number_column_and_full_titles():
    page = _issue_page()
    t = page.t
    for view in page.VIEWS:
        assert "range" not in view, "ISSUE_VIEWS 不再保留没人读的 range 字段"
        chip = page._nav_chip(view, False)
        texts = _texts(chip)
        assert texts == [view["title"]], "chip 只显示完整标题（无序号、无 caption）"
        assert not any(re.fullmatch(r"\d{2}(–\d{2})?", value) for value in texts), \
            f"{view['key']} chip 里仍有序号文本"
        assert chip.height == t.px(28), "chip 28 高（与引用库一致）"
        title, dot = chip.content.controls
        assert isinstance(title, ft.Text) and isinstance(dot, ft.Container)
        assert title.max_lines is None, "标题不截断"
        assert chip.tooltip.startswith(view["title"] + "："), "状态词进 chip tooltip"


def test_nav_chips_not_numbered_and_no_caption():
    page = _issue_page()
    for view in page.VIEWS:
        chip = page._nav_chip(view, False)
        texts = _texts(chip)
        assert texts == [view["title"]]
        assert not texts[0].startswith(("01", "02", "03", "04", "05", "06"))
        assert texts[0] != view["caption"]
        assert len(chip.content.controls) == 2, "chip = 标题 + 状态点（文案进 tooltip）"


def test_nav_chips_uniform_height_and_status_inline():
    page = _issue_page()
    page.selected = ""
    t = page.t
    chips = [page._nav_chip(view, False) for view in page.VIEWS]
    assert {chip.height for chip in chips} == {t.px(28)}, "四个 chip 必须同高"
    for chip in chips:
        title, dot = chip.content.controls
        assert isinstance(title, ft.Text)
        assert isinstance(dot, ft.Container), "右侧状态点"
        assert dot.width == dot.height, "状态点是圆点"
        assert chip.tooltip, "状态文案进 chip tooltip"
    assert all(chip.bgcolor is None for chip in chips), "未选中不染色"


def test_nav_selected_tint_and_hover_surface():
    page = _issue_page()
    page.activate("toc")
    catalog = page._nav_chip(page.VIEWS[0], True)
    assert catalog.bgcolor == page.t.palette.nav_sel
    produce = page._nav_chip(page.VIEWS[1], False)
    assert produce.bgcolor is None
    page._nav_hover(SimpleNamespace(data="true"), produce, False)
    assert produce.bgcolor == page.t.palette.surface_2, "hover 用 surface_2"
    page._nav_hover(SimpleNamespace(data="false"), produce, False)
    assert produce.bgcolor is None


# ── 2. 手风琴：默认 / 切换 / 按钮不切换 ──────────────────────────────
def test_accordion_default_expands_first_section_of_each_view():
    page = _issue_page()
    page.activate("infocheck")
    for view_key, first in (("catalog", "infocheck"), ("produce", "compileall"),
                            ("clean", "cleantemp"), ("push", "dirpush")):
        assert page.expanded_step(view_key) == first
    # 当前视图（catalog）两个区块都建好，但只有首块可见。
    assert list(page._section_bodies) == ["infocheck", "toc"]
    assert page._section_bodies["infocheck"].visible is True
    assert page._section_bodies["toc"].visible is False
    assert page._chevrons["infocheck"].value == "▾"
    assert page._chevrons["toc"].value == "▸"


def test_accordion_click_header_toggles_and_keeps_one_open():
    page = _issue_page()
    page.activate("infocheck")
    _click(page._section_heads["toc"])
    assert page.selected == "infocheck", "点区块头不改变路由/选中步"
    assert page.expanded_step("catalog") == "toc"
    assert page._section_bodies["toc"].visible is True
    assert page._section_bodies["infocheck"].visible is False, "同视图只展开一个"
    assert page._chevrons["toc"].value == "▾"
    assert page._chevrons["infocheck"].value == "▸"

    _click(page._section_heads["toc"])
    assert page.expanded_step("catalog") == "", "再点同一区块 = 全部收起"
    assert all(not body.visible for body in page._section_bodies.values())
    assert page._chevrons["toc"].value == "▸"

    _click(page._section_heads["toc"])
    assert page.expanded_step("catalog") == "toc", "第三次点 = 重新展开"


def test_accordion_primary_button_does_not_toggle():
    page = _issue_page()
    page.activate("compileall")
    ran: list = []
    page._primary_action = lambda key: ran.append(key)
    _click(page._primary_btns["compileall"])
    assert ran == ["compileall"], "主按钮仍运行该步"
    assert page.expanded_step("produce") == "compileall", "点主按钮不切换展开"
    _click(page._primary_btns["merge"])
    assert ran == ["compileall", "merge"]
    assert page.expanded_step("produce") == "compileall", "收起区块的按钮也不展开它"


def test_collapsed_section_head_status_and_button_still_refresh():
    page = _issue_page()
    page.activate("merge")
    assert page._section_bodies["compileall"].visible is False
    page._status["compileall"] = ASM.StepStatus("done")
    page._sync_detail_status()
    assert page._detail_statuses["merge"].value == page._status_text("merge")
    assert page._detail_statuses["compileall"].value == page._status_text("compileall")
    assert str(page._primary_btns["compileall"].content) == page._primary_label("compileall")


# ── 3. 深链 / 视图切换记忆 ────────────────────────────────────────────
def test_four_view_deep_links_expand_target_section():
    page = _issue_page()
    cases = {
        "infocheck": ("catalog", "toc"),
        "toc": ("catalog", "infocheck"),
        "compileall": ("produce", "merge"),
        "merge": ("produce", "compileall"),
    }
    for step, (view_key, sibling) in cases.items():
        page.activate(step)
        assert page.expanded_step(view_key) == step
        assert page._section_bodies[sibling].visible is False, "同视图另一块收起"
        assert page._section_bodies[step].visible is True
        assert page._chevrons[step].value == "▾"
        assert page._chevrons[sibling].value == "▸"


def test_view_switch_remembers_expanded_section_per_view():
    page = _issue_page()
    page.activate("merge")
    page._select_view("catalog")
    assert page.selected == "infocheck", "catalog 还没展开过 → 默认首块 01"
    page._select_view("produce")
    assert page.selected == "merge", "回到视图 → 最近展开的 04，不是默认 03"
    assert page.expanded_step("produce") == "merge"
    page.activate("toc")
    page._select_view("produce")
    assert page.selected == "merge", "catalog 的展开不影响 produce 的记忆"
    page._select_view("catalog")
    assert page.selected == "toc"


# ── 4. 03 成功自动切 04（不运行 04）──────────────────────────────────
def test_compile_success_switches_to_merge_without_running_it():
    page = _issue_page()
    page.activate("compileall")
    ran: list = []
    page._primary_action = lambda key: ran.append(key)
    page.on_step_finished("compileall", 0)
    assert page.selected == "merge"
    assert page.expanded_step("produce") == "merge"
    assert page._section_bodies["merge"].visible is True
    assert page._section_bodies["compileall"].visible is False
    assert page._chevrons["merge"].value == "▾"
    assert page._chevrons["compileall"].value == "▸"
    assert ran == [], "04 不自动运行（保留独立确认点）"
    assert page._state_of("merge") == "idle"

    page.on_step_finished("toc", 0)       # 其他区块完成不自动切换
    assert page.selected == "merge"
    page.on_step_finished("compileall", 2)  # 失败不切换
    assert page.selected == "merge"


def test_compile_success_remembered_even_when_other_view_active():
    page = _issue_page()
    page.activate("toc")
    page.on_step_finished("compileall", 0)
    assert page.selected == "toc", "不在编译视图时不被强行拉走"
    assert page.expanded_step("produce") == "merge"
    page._select_view("produce")
    assert page.selected == "merge", "下次进「编译并合并」直接见 04"


# ── 5. 窄窗：导航折两行，仍是 4 个完整标题 ───────────────────────────
def test_narrow_nav_two_rows_full_titles():
    page = _issue_page()
    page.app._design_width = lambda: 1000  # <1100 → 两行
    page._refresh_layout()
    assert page._nav_box.content.height == page.t.px(72)
    labels = _texts(page._nav_box)
    assert labels == [view["title"] for view in page.VIEWS], \
        "两行导航仍显示 4 个完整标题（无序号、不截断）"
    for view in page.VIEWS:
        assert view["title"] in labels
    assert not any(re.fullmatch(r"\d{2}(–\d{2})?", value) for value in labels), \
        "导航不画序号"
    for chip in page._nav_chips.values():
        label = chip.content.controls[0]
        assert label.max_lines is None, "标题不截断"


def test_rail_does_not_regress_routes_or_tree():
    assert len(IA.all_routes()) == 22
    assert IA.issue_step_keys() == ["infocheck", "toc", "compileall",
                                    "merge", "cleantemp", "dirpush"]
    assert [view["key"] for view in IA.issue_views()] == ["catalog", "produce",
                                                          "clean", "push"]
