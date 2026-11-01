"""批 112 收尾修复 A：查库提示去重 + 查重合并左右并排对比。

用户两条反馈各配回归：
① 基本检索的示例只在输入框 hint 出现一次（不再有下方「例：…」同文案重复）；
② 查重合并逐字段左/右值同时成列（缺值「（空）」），行内单选决定 field_choice，
   不再用「只有单个下拉、点开才知道另一侧」的旧形态；写库入口（dry-run 预览）保留。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import flet as ft
import pytest

from ui import bib_advanced as BA
from ui import bib_ops_dupes as BD
from ui.tokens import Tokens


class _FakePage:
    """只给控件构造/重绘用到的口子，不启动 Flet 事件循环。"""

    def __init__(self) -> None:
        self.t = Tokens()
        self.renders = 0
        self.marks: list[tuple[str, dict]] = []
        self.query_field = None

    def _render(self) -> None:
        self.renders += 1

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))


def _walk(control):
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from _walk(child)
    content = getattr(control, "content", None)
    if content is not None:
        yield from _walk(content)


def _texts(control) -> list[str]:
    return [c.value for c in _walk(control) if isinstance(c, ft.Text)]


def _views(control, cls) -> list:
    return [c for c in _walk(control) if isinstance(c, cls)]


def _basic_view(mode: str = "key") -> BA.BibQueryView:
    view = BA.BibQueryView(_FakePage())
    view.mode = mode
    return view


def _merge_view() -> BD.BibDuplicatesView:
    view = BD.BibDuplicatesView(_FakePage())
    view.left = {"key": "hu2006a", "fields": {
        "title": "逻辑研究", "author": "胡塞尔", "year": "2006"}}
    view.right = {"key": "hu2006b", "fields": {
        "title": "逻辑研究（修订）", "author": "", "year": "2007", "note": "两卷本"}}
    view.pair = {"pair_no": "1", "key_a": "hu2006a", "key_b": "hu2006b"}
    return view


# ── ① 查库提示只出现一次 ──────────────────────────────────────────────────
def test_basic_panel_source_has_no_duplicate_hint() -> None:
    source = inspect.getsource(BA.BibQueryView._basic_panel)
    assert "hint_text=QUERY_HINTS[self.mode]" in source
    assert "例：" not in source  # 下方 caption 已删，示例只留输入框一处


@pytest.mark.parametrize("mode", sorted(BA.QUERY_HINTS))
def test_basic_query_hint_rendered_once(mode: str) -> None:
    panel = _basic_view(mode)._basic_panel()
    assert [field.hint_text for field in _views(panel, ft.TextField)] == [
        BA.QUERY_HINTS[mode]]
    # 同一句文案不再以普通文本形式出现在面板里（只在输入框 hint 中）
    assert all(BA.QUERY_HINTS[mode] not in value for value in _texts(panel))


# ── ② 查重对比：左右值同列 + 行内单选 ────────────────────────────────────
def test_merge_field_rows_show_both_sides() -> None:
    left = {"fields": {"title": "逻辑研究", "author": "胡塞尔"}}
    right = {"fields": {"title": "逻辑研究（修订）", "note": "两卷本"}}
    rows = BD.merge_field_rows(left, right)
    assert [name for name, _left, _right in rows] == ["title", "author", "note"]
    by_name = {name: (left_value, right_value) for name, left_value, right_value in rows}
    assert by_name["title"] == ("逻辑研究", "逻辑研究（修订）")
    assert by_name["author"] == ("胡塞尔", BD.EMPTY_VALUE)
    assert by_name["note"] == (BD.EMPTY_VALUE, "两卷本")


def test_merge_field_rows_empty_records() -> None:
    assert BD.merge_field_rows({}, None) == []


def test_merge_card_renders_left_right_columns() -> None:
    card = _merge_view()._merge_card()
    values = _texts(card)
    assert "左：hu2006a" in values and "右：hu2006b" in values
    assert "逻辑研究" in values and "逻辑研究（修订）" in values
    assert "胡塞尔" in values and "两卷本" in values
    assert values.count(BD.EMPTY_VALUE) == 2  # author 右空、note 左空
    # 旧形态「每字段单个下拉」不再出现
    assert not _views(card, ft.Dropdown)
    # 每个字段一行单选（左/右），默认左
    groups = _views(card, ft.RadioGroup)
    assert len(groups) == 4
    assert [group.value for group in groups] == ["left"] * 4
    for group in groups:
        assert [radio.value for radio in _views(group, ft.Radio)] == ["left", "right"]
    # 写库入口（dry-run 预览）仍在（flet 1.0.5 的按钮标签在 content）
    assert any(isinstance(control, ft.FilledButton)
               and control.content == "预览合并（dry-run）"
               for control in _walk(card))


def test_merge_row_radio_sets_field_choice_and_refreshes() -> None:
    view = _merge_view()
    assert view.field_choice == {}
    assert view._field_source("title") == "hu2006a"  # 默认左

    groups = _views(view._merge_card(), ft.RadioGroup)
    assert view.page.renders == 0  # 只构造不重绘
    groups[0].on_change(SimpleNamespace(control=SimpleNamespace(value="right")))

    assert view.field_choice == {"title": "right"}
    assert view.page.renders == 1  # 行内单选触发重绘
    assert view._field_source("title") == "hu2006b"
    assert view._field_sources()["title"] == "hu2006b"
    # 重建后选中侧保持（RadioGroup value 跟着 field_choice 走）
    assert _views(view._merge_card(), ft.RadioGroup)[0].value == "right"


def test_merge_long_value_wraps_with_tooltip() -> None:
    long_value = "很长的字段值" * 30
    view = _merge_view()
    view.right["fields"]["title"] = long_value
    cells = [text for text in _views(view._merge_card(), ft.Text) if text.value == long_value]
    assert len(cells) == 1
    assert cells[0].max_lines == 3
    assert cells[0].overflow == ft.TextOverflow.ELLIPSIS
    assert cells[0].tooltip == long_value
