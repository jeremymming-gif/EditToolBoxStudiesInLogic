"""回归：引用库编辑页字段列首行浮动标签被上边缘裁切。

结构性断言：字段列的第一个 cell 必须为上浮标签留出顶部空间；
其余 cell 与 66px 行高保持不变（最小、局部修复）。
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


def _cells(view: BE.BibEditView) -> list[ft.Container]:
    return [cell for pane in view._field_panes for cell in pane.controls]


def _view() -> BE.BibEditView:
    view = BE.BibEditView(_Host())
    view.build()
    return view


def test_first_field_cell_reserves_top_space_for_floating_label():
    cells = _cells(_view())
    assert cells, "字段列应至少有一个 cell"
    first = cells[0]
    assert isinstance(first, ft.Container)
    assert first.padding is not None and first.padding.top > 0, (
        "首行 padding.top 必须 > 0，给上浮 label 让出空间，避免被滚动容器上边缘裁切"
    )


def test_only_first_cell_has_top_padding_and_geometry_is_unchanged():
    cells = _cells(_view())
    assert len(cells) > 1, "需要多行才能验证只有首行改变"
    first, *rest = cells
    assert first.height == 66
    # 两列各有自己的首行（左列「题名」、右列「丛书」），都需留顶部空间；
    # 其余行不变，保持原有对齐/间距。
    assert sum(1 for cell in cells if cell.padding and cell.padding.top > 0) == 2
    assert first.padding.top > 0 and rest[0].padding.top == 0
    assert all(cell.height == 66 for cell in cells), "行高必须保持 66px"
