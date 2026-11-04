"""批 118 D2：引用库滚动/布局契约（静态 + 控件树，不启窗口）。

锁四条：
1. 常显条只经 ``C.scrollbar(t)``（6px / thumb_visibility=True / 可拖 / 圆角 / 横条贴底），
   页面级滚动列不再用静止即隐的 ``ScrollMode.AUTO``；
2. 内层短表 shrink-wrap（health 样例、dupes 两表），页体是唯一滚动者；
3. 宽表横向可滚且带常显横条（BibTable.scroll_x）；
4. 日志 dock 仍是固定高单行，不叠第二条滚动。
"""

from __future__ import annotations

from pathlib import Path

import flet as ft

from ui import components as C
from ui import bib_table as T
from ui.tokens import Tokens

APP = Path(__file__).resolve().parents[1] / "app" / "ui"


def _src(name: str) -> str:
    return (APP / name).read_text(encoding="utf-8")


def test_scrollbar_helper_is_always_on_config() -> None:
    """常显配置：不是包裹控件，thumb 恒定、6px、可拖、圆角、方向随参数。"""
    t = Tokens()
    bar = C.scrollbar(t)
    assert isinstance(bar, ft.Scrollbar)
    assert bar.thumb_visibility is True
    assert bar.thickness == t.px(C.SCROLLBAR_THICKNESS) == t.px(6)
    assert bar.radius == t.px(3)
    assert bar.interactive is True
    assert bar.orientation == ft.ScrollbarOrientation.RIGHT
    hbar = C.scrollbar(t, horizontal=True)
    assert hbar.orientation == ft.ScrollbarOrientation.BOTTOM


def test_shell_leaves_six_px_for_the_thumb() -> None:
    """页面主体右内边距 = 24 + 6；详情栏右 6px，条不压最后一列。"""
    src = _src("bib_shell.py")
    assert "PAGE_H_PAD + C.SCROLLBAR_THICKNESS" in src
    assert "padding=_pad(t, 16, 0, C.SCROLLBAR_THICKNESS, 0)" in src
    assert "scroll=C.scrollbar(t)" in src
    # 批 120 B-352：bib 本地重复实现已删，宽度常量只留 components 一处。
    assert "SCROLLBAR_T = 6" not in src
    assert "def scrollbar(" not in src


def test_page_scroll_columns_are_always_on() -> None:
    """六视图页体 + 详情栏不再有静止即隐的 AUTO 滚动列。"""
    for name in ("bib_overview.py", "bib_normalize.py", "bib_health.py",
                 "bib_review.py", "bib_query.py", "bib_prefs.py",
                 "bib_fieldfix.py"):
        src = _src(name)
        assert "C.scrollbar(t)" in src, f"{name} 未接常显条"
        assert "S.scrollbar(" not in src, f"{name} 仍走 bib 本地旧 helper"
        assert "scroll=ft.ScrollMode.AUTO" not in src, f"{name} 仍有 AUTO 滚动列"


def test_health_sample_and_dupes_tables_shrink_wrap() -> None:
    """短列表去掉固定高/内滚：滚轮归页体，末尾不被内层吞掉。"""
    health = _src("bib_health.py")
    assert "height=t.px(112)" not in health
    assert "box._bib_shrink = True" in health
    dupes = _src("bib_dupes.py")
    assert "height=t.px(158)" not in dupes
    assert "height=t.px(128)" not in dupes
    assert dupes.count("_bib_shrink = True") >= 2


def test_import_table_shrinks_when_short_and_scrolls_when_long() -> None:
    """入藏变化 ≤8 行 shrink-wrap；长列表才保留独立滚动（常显条）。"""
    src = _src("bib_import.py")
    assert "scrolling = len(rows) > 8" in src
    assert "scroll=C.scrollbar(t) if scrolling else None" in src
    assert "table._bib_shrink = not scrolling" in src


def test_wide_table_horizontal_bar_is_always_visible() -> None:
    """scroll_x：横条常显在视口底缘，内层竖向条也常显；表头随行横移。"""
    t = Tokens()
    cols = [T.BibColumn("title", "题名", flex=3.2),
            T.BibColumn("author", "作者", flex=1.8)]
    rows = [{"title": "题名一", "author": "作者一"},
            {"title": "题名二", "author": "作者二"}]
    table = T.BibTable(t, cols, rows, scroll_x=True)
    tree = table.build()
    body = tree.controls[0]
    assert isinstance(body, ft.Container) and body._bib_scroll_x is True
    scroller = body.content
    assert isinstance(scroller, ft.Row)
    bar = scroller.scroll
    assert isinstance(bar, ft.Scrollbar) and bar.thumb_visibility is True
    assert bar.orientation == ft.ScrollbarOrientation.BOTTOM
    grid = scroller.controls[0]
    assert isinstance(grid, ft.Column) and grid.width == t.px(table._total_width())
    inner = grid.controls[1]
    assert isinstance(inner, ft.Column)
    assert isinstance(inner.scroll, ft.Scrollbar) and inner.scroll.thumb_visibility is True


def test_query_turns_on_scroll_x_only_when_wide() -> None:
    """1160 六列超宽 → 横滚；1000 三列仍用 flex，不无谓加横条。"""
    src = _src("bib_query.py")
    assert "scroll_x=(self._build_width >= 1100)" in src
    assert "scroll_x=False" not in src


def test_log_dock_stays_single_line_fixed_height() -> None:
    """日志 dock 固定高、单行截断，不引入第二条滚动条。"""
    shell = _src("bib_shell.py")
    assert "height=t.px(dock_height)" in shell
    for name in ("bib_health.py", "bib_edit.py", "bib_dupes.py",
                 "bib_import.py", "bib_prefs.py"):
        src = _src(name)
        assert "max_lines=1" in src, f"{name} 的 dock 文案未单行截断"
