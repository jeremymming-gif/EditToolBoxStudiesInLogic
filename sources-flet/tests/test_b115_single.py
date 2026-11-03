"""批 115 · P3-C1 单篇页契约（白面板 / 去卡 / 日志避让）。

锁：
1. `C.page_body(panel=True)`：surface 底 + 顶部圆角 10；默认 False 灰底零变化；
   padding / 滚动 / overlay / overlay_reserve 行为不变。
2. `LogConsole.on_layout_change`：默认 None 零行为变化；收展、拖拽、双击复位后
   各触发一次；回调异常被吞掉，不影响日志控件。
3. SinglePage build：根是 Stack、内容盒是白面板、底部 spacer 是最后一个 control。
4. SinglePage 页面级不再用 C.card / C.section_title（AST 检查方法体）。
5. 日志 reserve 两档：收起 40（截图实测 dock 26.0 逻辑px ≈ 设计 39，留 1px）；
   展开 46 + `_log_height`（拖拽跟随，复位回 200）。
6. `checks.proofcheck_section` 去卡：section_label + hairline + 4 行 inline_note。
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace

import flet as ft

from app.ui import checks as CK
from app.ui import components as C
from app.ui import pages as P
from app.ui.tokens import Tokens
from test_b114_polish import _build_single, _walk


def _t() -> Tokens:
    return Tokens("light", 1.0, 96)


def _calls(func) -> set[str]:
    """函数体里出现的 `<attr>(...)` 调用属性名集合（注释/字符串不参与）。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}


# ── 1. page_body(panel=) ────────────────────────────────────────────────
def test_page_body_panel_default_keeps_gray_background():
    t = _t()
    sig = inspect.signature(C.page_body)
    assert sig.parameters["panel"].default is False
    box = C.page_body("内容", t=t)
    assert isinstance(box, ft.Container)
    assert box.bgcolor == t.palette.bg
    assert box.border_radius is None
    assert box.content.scroll == ft.ScrollMode.AUTO


def test_page_body_panel_true_is_white_sheet_with_top_radius():
    t = _t()
    box = C.page_body("内容", t=t, panel=True)
    assert isinstance(box, ft.Container)
    assert box.bgcolor == t.palette.surface
    assert isinstance(box.border_radius, ft.BorderRadius)
    assert box.border_radius.top_left == t.radius("panel")
    assert box.border_radius.top_right == t.radius("panel")
    assert box.border_radius.bottom_left == 0
    assert box.border_radius.bottom_right == 0
    # padding / 滚动 / overlay_reserve 与默认路径一致。
    plain = C.page_body("内容", t=t)
    assert box.padding == plain.padding
    assert box.content.scroll == plain.content.scroll


def test_page_body_overlay_and_reserve_unchanged_by_panel():
    t = _t()
    overlay = ft.Container(content=ft.Text("浮层"))
    stack = C.page_body("内容", t=t, overlay=overlay, overlay_reserve=64, panel=True)
    assert isinstance(stack, ft.Stack)
    assert len(stack.controls) == 2
    box, dock = stack.controls
    assert dock is overlay
    assert isinstance(box, ft.Container)
    assert box.bgcolor == t.palette.surface
    last = box.content.controls[-1]
    assert isinstance(last, ft.Container)
    assert last.height == t.px(64)
    # overlay_reserve=0 时不再追加占位。
    stack2 = C.page_body("内容", t=t, overlay=overlay, overlay_reserve=0, panel=True)
    assert len(stack2.controls[0].content.controls) == 1


# ── 2. LogConsole.on_layout_change ──────────────────────────────────────
def test_log_console_on_layout_change_defaults_none():
    log = C.LogConsole(_t(), dock=True)
    assert log.on_layout_change is None
    log.expand()
    log.collapse()
    log._on_resize_update(SimpleNamespace(primary_delta=-10))
    log._on_resize_reset()
    assert log.is_expanded is False


def test_log_console_notifies_on_expand_collapse_and_resize():
    log = C.LogConsole(_t(), dock=True)
    calls: list[int] = []
    log.on_layout_change = lambda: calls.append(1)
    log.expand()
    assert log.is_expanded is True
    assert len(calls) == 1
    log.collapse()
    assert log.is_expanded is False
    assert len(calls) == 2
    before = log._log_height
    log._on_resize_update(SimpleNamespace(primary_delta=-_t().px(10)))
    assert log._log_height == before + _t().px(10)
    assert len(calls) == 3
    log._on_resize_reset()
    assert log._log_height == log._base_height
    assert len(calls) == 4
    # delta=0 早退，不重复通知。
    log._on_resize_update(SimpleNamespace(primary_delta=0))
    assert len(calls) == 4


def test_log_console_swallows_callback_errors():
    log = C.LogConsole(_t(), dock=True)
    def boom() -> None:
        raise RuntimeError("宿主重排失败")
    log.on_layout_change = boom
    log.expand()  # 不应抛出
    assert log.is_expanded is True


def test_log_console_non_dock_does_not_notify():
    log = C.LogConsole(_t(), dock=False)
    calls: list[int] = []
    log.on_layout_change = lambda: calls.append(1)
    log.expand()
    log.collapse()
    assert calls == []
    assert log.is_expanded is True


# ── 3/4. SinglePage 白面板 + 去卡 + spacer ──────────────────────────────
def test_single_page_builds_white_sheet_with_spacer_last():
    page, root = _build_single()
    t = page.t
    assert isinstance(root, ft.Stack)
    assert len(root.controls) == 2
    body, dock = root.controls
    assert dock is page._log.dock_root
    assert isinstance(body, ft.Container)
    assert body.bgcolor == t.palette.surface
    assert isinstance(body.border_radius, ft.BorderRadius)
    assert body.border_radius.top_left == t.radius("panel")
    last = body.content.controls[-1]
    assert isinstance(last, ft.Container)
    assert last is page._log_spacer
    assert last.height == t.px(40)
    assert page._log.on_layout_change == page._sync_log_reserve


def test_single_page_methods_have_no_page_level_cards():
    for func in (P.SinglePage.build, P.SinglePage._build_convert,
                 P.SinglePage._advanced_params, P.SinglePage._advanced_title):
        calls = _calls(func)
        assert "card" not in calls, f"{func.__name__} 仍在建页面级 C.card"
        assert "section_title" not in calls, f"{func.__name__} 仍在用 C.section_title"
    assert "section_label" in _calls(P.SinglePage._build_convert)
    assert "hairline" in _calls(P.SinglePage._build_convert)
    assert "section_label" in _calls(P.SinglePage._advanced_title)
    # 保持批 114 契约：build 仍用 page_body 且传 panel=True。
    src = inspect.getsource(P.SinglePage.build)
    assert "page_body(" in src
    assert "panel=True" in src
    assert "overlay_reserve=0" in src


# ── 5. 日志 reserve 两档 ────────────────────────────────────────────────
def test_single_page_log_reserve_two_levels_and_drag_follow():
    page, root = _build_single()
    t = page.t
    spacer = root.controls[0].content.controls[-1]
    assert spacer.height == t.px(40)
    page._log.expand()
    assert spacer.height == t.px(46) + page._log._log_height
    assert spacer.height == t.px(246)
    assert spacer.height > t.px(40)
    page._log._on_resize_update(SimpleNamespace(primary_delta=-t.px(30)))
    assert spacer.height == t.px(46) + page._log._log_height
    assert spacer.height == t.px(276)
    page._log._on_resize_reset()
    assert spacer.height == t.px(246)
    page._log.collapse()
    assert spacer.height == t.px(40)


# ── 6. 一校核查去卡 ─────────────────────────────────────────────────────
def test_proofcheck_section_has_no_card_and_uses_section_label_hairline():
    page, _ = _build_single()
    calls = _calls(CK.proofcheck_section)
    assert "card" not in calls
    assert "section_title" not in calls
    assert "section_label" in calls
    assert "hairline" in calls
    assert "inline_note" in calls
    section = CK.proofcheck_section(page)
    assert isinstance(section, ft.Column)
    notes = section.controls[3:]
    assert len(notes) == 4
    assert all(isinstance(note, ft.Row) for note in notes)

# ── 7. 一校核查结果区去卡（批 115 验收返工）──────────────────────────────
def test_proof_check_card_root_is_borderless_column_with_inline_stats():
    card = CK.ProofCheckCard(_t())
    assert isinstance(card.root, ft.Column)
    assert not isinstance(card.root, ft.Container)
    calls = _calls(CK.ProofCheckCard.__init__)
    assert "card" not in calls
    assert "section_label" in calls
    assert "hairline" in calls
    # 第一行是 C.section_label（Row），四项计数是单行 inline（Row），不是灰底 tile。
    assert isinstance(card.root.controls[0], ft.Row)
    assert isinstance(card.stats_row, ft.Row)
    assert len(card.stats_row.controls) == 4
    for item in card.stats_row.controls:
        assert isinstance(item, ft.Row)
        assert not isinstance(item, ft.Container)
        assert all(isinstance(ctl, ft.Text) for ctl in item.controls)
    assert card.root.visible is False  # reset 的空态仍按原行为隐藏


def test_proof_check_card_result_state_stays_borderless():
    card = CK.ProofCheckCard(_t())
    report = CK.ProofReport(
        manual=2, fixable=3, total=5, rule_count=2, ok=True,
        rules=(CK.ProofRule("TY-01", "标点", 2),),
        conclusion="有 2 处需要人工处理",
    )
    card.show(report, report_path="/tmp/b115-proof.md")
    assert card.root.visible is True
    assert not isinstance(card.root, ft.Container)
    values = [row.controls[-1].value for row in card.stats_row.controls]
    assert values == ["2", "3", "5", "2"]
    assert card.open_btn.disabled is False
    assert len(card.rules_box.controls) == 1


def test_single_page_proofcheck_has_no_card_empty_and_result_states(monkeypatch):
    made: list[ft.Control] = []
    real_card = C.card

    def spy(*args, **kwargs):
        ctl = real_card(*args, **kwargs)
        made.append(ctl)
        return ctl

    monkeypatch.setattr(C, "card", spy)
    page, root = _build_single()
    page.activate("proofcheck")  # 空态：还没有核查结果
    assert page._proof.root.visible is True
    ids = {id(ctl) for ctl in _walk(root)}
    assert not any(id(ctl) in ids for ctl in made), "一校核查空态仍有 C.card 控件"
    page._proof.show(CK.ProofReport(
        manual=1, fixable=0, total=1, rule_count=1, ok=True,
        conclusion="有 1 处需要人工处理",
    ))
    ids = {id(ctl) for ctl in _walk(root)}
    assert not any(id(ctl) in ids for ctl in made), "一校核查结果态仍有 C.card 控件"
