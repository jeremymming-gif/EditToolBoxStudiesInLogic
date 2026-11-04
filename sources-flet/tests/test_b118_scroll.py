"""批 118 · D1 壳滚动修复静态契约。

锁：
1. `C.scrollbar(t)` 是唯一的常显细条配置（`thumb_visibility=True`、可拖拽、
   设计像素宽度），`SCROLLBAR_THICKNESS` 只有一处常量。
2. `page_body` 默认即常显条，滚动内容右缘留 6px；`tail` 仍留在滚动列顶层末尾
   （日志动态 reserve 契约不变）。
3. Home 用 `page_body(panel=True)`，不再是不可滚的 `C.panel`。
4. 帮助页滚动宿主 / 侧栏导航列 / 日志浮层 / `build_info_table` 横向条都走 helper。
5. 五个壳文件里不再残留 `ft.ScrollMode.AUTO` 滚动容器。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import flet as ft

import main as M
from ui import checks as CK
from ui import components as C
from ui import help_page as HP
from ui import pages as P
from ui.tokens import Tokens

APP = Path(__file__).resolve().parents[1] / "app"


def _t() -> Tokens:
    return Tokens("light", 1.0, 96)


def _source(rel: str) -> str:
    return (APP / rel).read_text(encoding="utf-8")


# ── 1. helper 本身 ─────────────────────────────────────────────────────
def test_scrollbar_helper_is_always_visible_thin_and_interactive():
    t = _t()
    bar = C.scrollbar(t)
    assert isinstance(bar, ft.Scrollbar)
    assert bar.thumb_visibility is True      # 静止也画；无溢出时 Flet 不画
    assert bar.interactive is True
    assert bar.thickness == t.px(C.SCROLLBAR_THICKNESS) == t.px(6)
    assert C.scrollbar(t, thickness=4).thickness == t.px(4)
    # 批 120 B-352：radius / orientation 也收敛到唯一实现（横向条贴底）。
    assert bar.radius == t.px(3)
    assert bar.orientation == ft.ScrollbarOrientation.RIGHT
    assert C.scrollbar(t, horizontal=True).orientation == ft.ScrollbarOrientation.BOTTOM
    # 只有一个宽度常量，避免各页各写一个数字。
    assert C.SCROLLBAR_THICKNESS == 6


# ── 2. page_body 默认滚动配置 / 右缘留白 / tail ────────────────────────
def test_page_body_default_uses_always_scrollbar_and_six_px_right_gutter():
    t = _t()
    box = C.page_body("内容", t=t)
    assert isinstance(box, ft.Container)
    assert isinstance(box.content.scroll, ft.Scrollbar)
    assert box.content.scroll.thumb_visibility is True
    inset = box.content.controls[0]
    assert isinstance(inset, ft.Container)
    assert inset.padding.right == t.px(6)
    assert inset.content.controls[0] == "内容"
    # 外层内容盒 padding 不受影响（b114_cards 契约）。
    assert (box.padding.left, box.padding.top, box.padding.right,
            box.padding.bottom) == (t.px(24), t.px(12), t.px(24), t.px(18))


def test_page_body_tail_stays_top_level_last_for_log_reserve():
    t = _t()
    tail = ft.Container(height=t.px(52))
    dock = ft.Container()
    stack = C.page_body("内容", t=t, overlay=dock, overlay_reserve=0, tail=tail)
    assert isinstance(stack, ft.Stack)
    body = stack.controls[0]
    assert body.content.controls[-1] is tail
    assert body.content.scroll.thumb_visibility is True
    # 无 tail 时滚动列只有内容（overlay_reserve=0 不再多塞占位）。
    plain = C.page_body("内容", t=t, overlay=dock, overlay_reserve=0)
    assert len(plain.controls[0].content.controls) == 1


# ── 3. Home：从 C.panel 改为可滚 page_body ─────────────────────────────
def test_home_builds_scrollable_white_sheet():
    t = _t()
    app = SimpleNamespace(
        t=t,
        engine_status=SimpleNamespace(ok=True),
        app_version="1.0.0",
        pages={},
        store=SimpleNamespace(
            get=lambda key, default=None: {} if key == "recent" else default),
        page=SimpleNamespace(run_task=lambda *a, **k: None),
        goto=lambda *a, **k: None,
        mark=lambda *a, **k: None,
    )
    page = P.HomePage(app)
    root = page.build()
    assert isinstance(root, ft.Container)
    assert root.bgcolor == t.palette.surface
    assert isinstance(root.content.scroll, ft.Scrollbar)
    assert root.content.scroll.thumb_visibility is True
    assert root.padding.bottom == t.px(10)
    src = inspect.getsource(P.HomePage.build)
    assert "C.page_body(" in src and "panel=True" in src
    assert "C.panel(" not in src


# ── 4. 帮助页滚动宿主 ──────────────────────────────────────────────────
def test_help_scroll_host_uses_shared_scrollbar():
    from test_b115_help import _app_stub

    page = HP.HelpPage(_app_stub())
    box = page.build()
    assert page._scroll is box.content
    assert isinstance(box.content.scroll, ft.Scrollbar)
    assert box.content.scroll.thumb_visibility is True


# ── 5. 侧栏 / 日志 / 横向表：源码与运行时双证 ─────────────────────────
def test_sidebar_log_and_checks_call_the_shared_helper():
    nav_src = inspect.getsource(M.App._sidebar_content)
    assert "C.scrollbar(t)" in nav_src
    assert "ft.ScrollMode.AUTO" not in nav_src
    log_src = inspect.getsource(C.LogConsole.__init__)
    assert "scroll=scrollbar(t, thickness=4)" in log_src
    checks_src = inspect.getsource(CK.build_info_table)
    assert "C.scrollbar(t)" in checks_src
    assert "C._pad(t, 0, 0, 0, 6)" in checks_src


def test_log_console_list_has_4px_always_scrollbar_and_right_pad():
    t = _t()
    log = C.LogConsole(t, dock=True)
    assert isinstance(log.list.scroll, ft.Scrollbar)
    assert log.list.scroll.thumb_visibility is True
    assert log.list.scroll.thickness == t.px(4)
    assert log.list.padding.right == t.px(4)
    # 日志正文必须有紧高度，否则 ListView 按内容铺开、没有可滚视口。
    assert log._selection_host.expand is True
    assert log._selection_host.content is log._selection
    assert log._log_box.content.controls[-1] is log._selection_host


def test_checks_wide_row_has_horizontal_always_bar_and_bottom_gap():
    t = _t()
    article = dict(folder="F1", volume="1", issue="1", year="2024",
                   span="1-2", pages="3", actual_text="3", font_line="—",
                   author="A", title="T", mismatch=False)
    row = CK.build_info_table([article], t)
    assert isinstance(row, ft.Row)
    assert isinstance(row.scroll, ft.Scrollbar)
    assert row.scroll.thumb_visibility is True
    holder = row.controls[0]
    assert isinstance(holder.content, ft.DataTable)
    assert holder.padding.bottom == t.px(6)


# ── 6. 壳里不再残留 AUTO 清单容器 ──────────────────────────────────────
def test_no_scrollmode_auto_left_in_shell_scroll_containers():
    for rel in ("ui/components.py", "ui/pages.py", "ui/help_page.py",
                "ui/checks.py", "main.py"):
        assert "ScrollMode.AUTO" not in _source(rel), rel
