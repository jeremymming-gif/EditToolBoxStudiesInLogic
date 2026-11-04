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


# ── 1. rail：无序号 / caption 完整 / 行高 ───────────────────────────
def test_rail_has_no_number_column_and_full_captions():
    page = _issue_page()
    t = page.t
    for view in page.VIEWS:
        assert "range" not in view, "ISSUE_VIEWS 不再保留没人读的 range 字段"
        row = page._rail_row(view)
        texts = _texts(row)
        assert not any(re.fullmatch(r"\d{2}(–\d{2})?", value) for value in texts), \
            f"{view['key']} 行里仍有序号文本"
        # 行 = 标题/说明列 + 选中竖条；序号列及其 Container 已删除。
        assert len(row.content.controls) == 2, "rail 行不再有独立的序号列"
        col = row.content.controls[0]
        assert isinstance(col, ft.Column) and col.expand is True
        caption = col.controls[1]
        assert caption.value == view["caption"]
        assert caption.max_lines is None, "不设 max_lines：宁可换行也不截断"
        assert caption.overflow == ft.TextOverflow.VISIBLE, "caption 不得用省略号"
        # 216 - padding(10+8) - accent 竖条 2 - Row spacing 16 = 180px。
        available = t.px(216) - t.px(10) - t.px(8) - t.px(2) - t.sp("2")
        need = _approx_text_width(caption.value, caption.size)
        assert need <= available, (
            f"{view['key']} caption {caption.value!r} 估算 {need:.1f}px > {available}px")


def test_rail_caption_not_repeating_title_or_number():
    page = _issue_page()
    for view in page.VIEWS:
        row = page._rail_row(view)
        col = row.content.controls[0]
        title_row = col.controls[0]
        caption = col.controls[1]
        assert caption.value == view["caption"]
        assert not caption.value.startswith(("01", "02", "03", "04", "05", "06"))
        assert caption.value != view["title"]
        assert len(caption.value) <= 16, "216 栏里 caption 必须短"
        assert len(title_row.controls) == 2, "标题行 = 标题 + 状态点（文案进 tooltip）"


def test_rail_rows_uniform_height_and_status_inline():
    page = _issue_page()
    t = page.t
    rows = [page._rail_row(view) for view in page.VIEWS]
    assert {row.height for row in rows} == {t.px(64)}, "四行必须同高（原来 76/更矮混排）"
    for row in rows:
        title_row = row.content.controls[0].controls[0]
        assert isinstance(title_row, ft.Row)
        title, dot = title_row.controls
        assert isinstance(title, ft.Container) and title.expand is True, "标题吃剩余宽度"
        assert isinstance(title.content, ft.Text) and title.content.max_lines == 1
        assert isinstance(dot, ft.Container) and dot.tooltip, "状态文案进 tooltip"
        assert dot.content.width == dot.content.height, "状态点是圆点"
    active = rows[0]
    assert active.bgcolor is None, "未选中不染色"


def test_rail_selected_tint_and_accent_bar_unchanged():
    page = _issue_page()
    page.activate("toc")
    catalog = page._rail_row(page.VIEWS[0])
    assert catalog.bgcolor == page.t.palette.sel_tint
    accent = catalog.content.controls[-1]
    assert isinstance(accent, ft.Container)
    assert accent.bgcolor == page.t.palette.accent
    assert accent.width == page.t.px(2) and accent.height == page.t.px(34)
    produce = page._rail_row(page.VIEWS[1])
    assert produce.bgcolor is None
    assert produce.content.controls[-1].bgcolor is None


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


# ── 5. 窄窗 54 横条：4 段短标题 ──────────────────────────────────────
def test_narrow_band_four_short_titles_fit():
    page = _issue_page()
    page.app._design_width = lambda: 1000  # 内容 780 < 880 → 横条
    page._refresh_layout()
    band = page._progress_band()
    assert band.height == page.t.px(54)
    segs = band.content.controls
    assert len(segs) == 4
    labels = _texts(band)
    assert labels == [view["short"] for view in page.VIEWS], \
        "54 横条只显示 4 个短标题（无序号、无长标题）"
    for view in page.VIEWS:
        assert view["short"] in labels, f"窄窗要显示缩短标题：{view['short']}"
    assert not any(re.fullmatch(r"\d{2}(–\d{2})?", value) for value in labels), \
        "横条不画序号"
    assert "清理临时文件" not in labels and "生成推送文案" not in labels
    for seg in segs:
        label = seg.content.controls[0].controls[1]
        assert label.max_lines == 1 and label.overflow == ft.TextOverflow.ELLIPSIS


def test_rail_does_not_regress_routes_or_tree():
    assert len(IA.all_routes()) == 22
    assert IA.issue_step_keys() == ["infocheck", "toc", "compileall",
                                    "merge", "cleantemp", "dirpush"]
    assert [view["key"] for view in IA.issue_views()] == ["catalog", "produce",
                                                          "clean", "push"]
