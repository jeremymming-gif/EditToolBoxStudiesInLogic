"""回归：引用库编辑页字段输入框下方不再渲染英文字段名小字。

结构性断言（不碰真机视觉）：
- 每个 cell 内不含以英文字段名渲染的 caption Text；
- 浮动输入框仍存在，中文浮动标签与 66px 行高不变；
- 上一轮 5c9a8df 引入的 first/padding.top 补丁已回退。
"""
from __future__ import annotations

import sys
from pathlib import Path

import flet as ft

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui import bib_edit as BE  # noqa: E402
from ui.tokens import Tokens  # noqa: E402


class _Host:
    def __init__(self) -> None:
        self.t = Tokens()


def _view() -> BE.BibEditView:
    view = BE.BibEditView(_Host())
    view.build()
    return view


def _cells_and_names(view: BE.BibEditView):
    cells = [cell for pane in view._field_panes for cell in pane.controls]
    names = [name for pane_names in view._field_columns() for name in pane_names]
    assert len(cells) == len(names), "cell 数应与字段名数一致"
    return list(zip(cells, names))


def test_no_english_caption_text_in_any_field_cell():
    pairs = _cells_and_names(_view())
    assert pairs, "字段列应至少有一个 cell"
    for cell, name in pairs:
        assert isinstance(cell, ft.Container)
        assert isinstance(cell.content, ft.Column)
        english_captions = [
            ctl for ctl in cell.content.controls
            if isinstance(ctl, ft.Text) and ctl.value == name
        ]
        assert not english_captions, (
            f"cell（字段 {name!r}）内不应有以英文字段名渲染的 caption Text"
        )


def test_floating_input_still_present_with_chinese_label_and_66px_height():
    pairs = _cells_and_names(_view())
    assert len(pairs) > 1, "需要多行才能验证结构"
    for cell, name in pairs:
        assert cell.height == 66, "行高保持 66px"
        # 上一轮 5c9a8df 的 first/padding.top 补丁已回退：所有 cell 顶部零留白。
        assert cell.padding is not None and cell.padding.top == 0, (
            "padding.top 应回退为 0，不再为首行单独留顶部空间"
        )
        # content 只保留浮动输入框（TextField），标签是中文（或字段名的中文映射）。
        assert len(cell.content.controls) == 1, "cell 内应只有一个浮动输入框控件"
        field = cell.content.controls[0]
        assert isinstance(field, ft.TextField), "浮动输入框应为 TextField"
        expected_label = BE.field_label(name)
        assert field.label == expected_label, (
            f"浮动输入框 label 应为中文映射 {expected_label!r}，实际 {field.label!r}"
        )
