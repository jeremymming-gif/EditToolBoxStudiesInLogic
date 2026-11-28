"""批 116 ISSUE4-C/D + 批 118（D7）：rail 打磨 + 视图内手风琴（替代 flet 1.0.5 无效的 scroll_to）。

锁住的不变量（纯壳层，不建窗口、不跑引擎）：
1. rail 不再画 `01–02` 序号列（ISSUE4-D 用户拍板）；行同高 64，
   标题 + 状态点同行、状态点右对齐；
2. 去掉序号列后 caption 放大可用宽、完整显示：不设 max_lines、不用省略号，
   也不以序号开头/复述标题；
3. 同一视图同一时刻只展开一个区块，默认首块（catalog 01 / order 03 / refresh 04 /
   merge 05 / clean 06 / push 07），chevron ▸/▾；
4. 点区块头切换、点主按钮不切换；展开主按钮 Filled、收起 Outlined，头部状态/主按钮仍刷新；
5. 深链展开目标区块；切视图保留各视图最近展开的区块；
6. 批 118（D3）：任何步骤完成都**不自动切换视图、不自动展开别的区块、不自动运行下一步**；
7. 窄窗 54 横条 6 段都用短标题显示，同样不显示序号。
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
    for view_key, first in (("catalog", "infocheck"), ("order", "order"),
                            ("refresh", "refresh"), ("merge", "merge"),
                            ("clean", "cleantemp"), ("push", "dirpush")):
        assert page.expanded_step(view_key) == first
    # 当前视图（catalog）两个区块都建好，但只有首块可见。
    assert list(page._section_bodies) == ["infocheck", "toc"]
    assert page._section_bodies["infocheck"].visible is True
    assert page._section_bodies["toc"].visible is False
    assert page._chevrons["infocheck"].value == "▾"
    assert page._chevrons["toc"].value == "▸"
    assert isinstance(page._primary_btns["infocheck"], ft.FilledButton)
    assert isinstance(page._primary_btns["toc"], ft.OutlinedButton)


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
    assert isinstance(page._primary_btns["toc"], ft.FilledButton)
    assert isinstance(page._primary_btns["infocheck"], ft.OutlinedButton)

    _click(page._section_heads["toc"])
    assert page.expanded_step("catalog") == "", "再点同一区块 = 全部收起"
    assert all(not body.visible for body in page._section_bodies.values())
    assert page._chevrons["toc"].value == "▸"
    assert all(isinstance(btn, ft.OutlinedButton)
               for btn in page._primary_btns.values())

    _click(page._section_heads["toc"])
    assert page.expanded_step("catalog") == "toc", "第三次点 = 重新展开"
    assert isinstance(page._primary_btns["toc"], ft.FilledButton)
    assert isinstance(page._primary_btns["infocheck"], ft.OutlinedButton)


def test_accordion_primary_button_does_not_toggle():
    page = _issue_page()
    page.activate("toc")     # catalog 有两块：01 / 02
    ran: list = []
    page._primary_action = lambda key: ran.append(key)
    _click(page._primary_btns["toc"])
    assert ran == ["toc"], "主按钮仍运行该步"
    assert page.expanded_step("catalog") == "toc", "点主按钮不切换展开"
    _click(page._primary_btns["infocheck"])
    assert ran == ["toc", "infocheck"]
    assert page.expanded_step("catalog") == "toc", "收起区块的按钮也不展开它"

    _click(page._section_heads["infocheck"])
    assert isinstance(page._primary_btns["toc"], ft.OutlinedButton)
    assert isinstance(page._primary_btns["infocheck"], ft.FilledButton)
    assert page._step_run_btns["infocheck"] is page._primary_btns["infocheck"]
    # selected 仍是 02（点区块头不改选中步）→ _step_run_primary 指 02 的按钮
    assert page._step_run_primary is page._primary_btns["toc"]
    _click(page._primary_btns["toc"])
    _click(page._primary_btns["infocheck"])
    assert ran == ["toc", "infocheck", "toc", "infocheck"]
    assert page.expanded_step("catalog") == "infocheck", "重建后的按钮也只运行原区块"

    _click(page._section_heads["infocheck"])
    _click(page._primary_btns["toc"])
    _click(page._primary_btns["infocheck"])
    assert ran == ["toc", "infocheck", "toc", "infocheck", "toc", "infocheck"]
    assert page.expanded_step("catalog") == "", "全收起时按钮仍不切换展开"


def test_single_section_button_follows_expansion(tmp_path):
    page = _issue_page(str(tmp_path))
    page.activate("cleantemp")
    assert list(page._primary_btns) == ["cleantemp"]
    assert isinstance(page._primary_btns["cleantemp"], ft.FilledButton)

    _click(page._section_heads["cleantemp"])
    assert page.expanded_step("clean") == ""
    assert isinstance(page._primary_btns["cleantemp"], ft.OutlinedButton)

    _click(page._section_heads["cleantemp"])
    assert page.expanded_step("clean") == "cleantemp"
    assert isinstance(page._primary_btns["cleantemp"], ft.FilledButton)


def test_collapsed_section_head_status_and_button_still_refresh():
    page = _issue_page()
    page.activate("toc")     # catalog：02 展开，01 收起
    assert page._section_bodies["infocheck"].visible is False
    page._status["infocheck"] = ASM.StepStatus("done")
    page._sync_detail_status()
    assert page._detail_statuses["toc"].value == page._status_text("toc")
    assert page._detail_statuses["infocheck"].value == page._status_text("infocheck")
    assert str(page._primary_btns["infocheck"].content) == page._primary_label("infocheck")


# ── 3. 深链 / 视图切换记忆 ────────────────────────────────────────────
def test_deep_links_expand_target_section():
    page = _issue_page()
    cases = {
        "infocheck": ("catalog", "toc"),
        "toc": ("catalog", "infocheck"),
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
    page.activate("toc")            # catalog 最近展开 02
    page._select_view("merge")
    assert page.selected == "merge"
    page._select_view("catalog")
    assert page.selected == "toc", "回到视图 → 最近展开的 02，不是默认 01"
    assert page.expanded_step("catalog") == "toc"
    page.activate("infocheck")      # catalog 改成 01 展开
    page._select_view("merge")
    assert page.selected == "merge", "catalog 的展开不影响 merge 的记忆"
    page._select_view("catalog")
    assert page.selected == "infocheck"


# ── 4. D3：步骤完成不自动切页签（单动作，各自确认）───────────────────
def test_step_finish_does_not_switch_view_or_run_next():
    page = _issue_page()
    page.activate("refresh")
    ran: list = []
    page._primary_action = lambda key: ran.append(key)
    page.on_step_finished("refresh", 0)
    assert page.selected == "refresh", "成功后停在原页签"
    assert page.expanded_step("refresh") == "refresh"
    assert ran == [], "任何步骤都不自动运行下一步"

    page.on_step_finished("toc", 0)          # 别的步骤完成也不切
    assert page.selected == "refresh"
    page.on_step_finished("refresh", 2)      # 失败同样不切
    assert page.selected == "refresh"
    assert page._state_of("merge") == "idle"


# ── 5. 窄窗：导航折两行，仍是 6 个完整标题 ───────────────────────────
def test_narrow_nav_two_rows_full_titles():
    page = _issue_page()
    page.app._design_width = lambda: 1000  # <1100 → 两行
    page._refresh_layout()
    assert page._nav_box.content.height == page.t.px(72)
    labels = _texts(page._nav_box)
    assert labels == [view["title"] for view in page.VIEWS], \
        "两行导航仍显示 6 个完整标题（无序号、不截断）"
    for view in page.VIEWS:
        assert view["title"] in labels
    assert not any(re.fullmatch(r"\d{2}(–\d{2})?", value) for value in labels), \
        "导航不画序号"
    for chip in page._nav_chips.values():
        label = chip.content.controls[0]
        assert label.max_lines is None, "标题不截断"


def test_rail_does_not_regress_routes_or_tree():
    assert len(IA.all_routes()) == 32
    assert IA.issue_step_keys() == ["infocheck", "toc", "order", "refresh", "merge", "cleantemp", "dirpush"]
    assert [view["key"] for view in IA.issue_views()] == ["catalog", "order", "refresh", "merge", "clean", "push"]
