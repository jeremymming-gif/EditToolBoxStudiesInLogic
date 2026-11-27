"""批 127 GUI 回归：版本与同步页 body 必须可滚动，底部操作卡片不得被裁切。

TDD 红→绿：先让本文件红（body Column 没有 scroll/expand），再给
``bib_version_sync.build()`` 的 body Column 加 ``scroll=C.scrollbar(t), expand=True``。
定位 body 用语义查找（直接子节点分别承载三张卡片），不依赖脆弱索引。
"""
from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from ui import bib_version_sync as VS
from ui import components as C
from ui.tokens import Tokens


class _FakePage:
    """只满足 build() 的只读契约；loaded=True 时不触发异步加载。"""

    def __init__(self) -> None:
        self.t = Tokens()
        self.app = SimpleNamespace(notes_repo="")
        self.stats_data = {"db": "/tmp/version_sync_scroll/bibliography.jsonl"}
        self.page = SimpleNamespace()
        self.renders = 0
        self.routes: list[str] = []

    def mark(self, event, **fields) -> None:
        pass

    def _render(self) -> None:
        self.renders += 1

    def navigate_route(self, route: str) -> None:
        self.routes.append(route)


def _walk(control):
    yield control
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)
    for name in ("controls", "actions"):
        children = getattr(control, name, None) or ()
        if isinstance(children, ft.Control):
            children = [children]
        for child in children:
            if isinstance(child, ft.Control):
                yield from _walk(child)


def _texts(root) -> list[str]:
    out: list[str] = []
    for node in _walk(root):
        for attr in ("value", "content", "label"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                out.append(value)
    return out


_SECTIONS = (("target", "目标库"), ("status", "Git 状态"),
             ("actions", "本地版本操作"))


def _section_cards(column):
    """把 Column 的直接子节点按三张卡片的章节标题归类。"""
    found: dict[str, list] = {}
    for child in column.controls:
        joined = " ".join(_texts(child))
        for key, title in _SECTIONS:
            if title in joined:
                found.setdefault(key, []).append(child)
    return found


def _body_column(root):
    """语义定位 body：三个标题落在三个不同直接子节点上的那个 Column。

    页壳 Column 的三个标题都藏在同一个 body_box 里（只占一个直接子节点），
    会被 ``len(ids) == 3`` 排除；卡片内部 Column 只含一个标题，同样排除。
    """
    candidates = []
    for node in _walk(root):
        if not isinstance(node, ft.Column):
            continue
        cards = _section_cards(node)
        ids = {id(ctl) for group in cards.values() for ctl in group}
        if all(cards.get(key) for key, _ in _SECTIONS) and len(ids) == 3:
            candidates.append((node, cards))
    assert len(candidates) == 1, (
        "expected exactly one body Column carrying the three cards, "
        f"got {len(candidates)}")
    return candidates[0]


def _view():
    page = _FakePage()
    view = VS.BibVersionSyncView(page)
    view.loaded = True
    return view, view.build()


def test_version_sync_body_scrolls_and_keeps_all_cards():
    view, root = _view()
    body, cards = _body_column(root)

    # C.scrollbar(t) 在 flet 1.0.5 返回 Scrollbar 配置对象（不是包裹控件），
    # thumb_visibility=True 即 ALWAYS 语义（components.py:341-358）。
    assert isinstance(body.scroll, ft.Scrollbar), (
        f"body Column must be scrollable via C.scrollbar(t), got {body.scroll!r}")
    assert body.scroll.thumb_visibility is True
    assert body.scroll.interactive is True
    assert body.scroll.thickness == view.t.px(C.SCROLLBAR_THICKNESS)
    assert body.scroll.orientation == ft.ScrollbarOrientation.RIGHT
    assert body.expand is True

    # 回归守卫：三张卡片仍在 body.controls 里，且互不丢失。
    assert len(cards["target"]) == 1
    assert len(cards["status"]) == 1
    assert len(cards["actions"]) == 1


def test_version_sync_refresh_updates_controls_without_losing_scroll():
    view, _root = _view()
    view._notice("info", "刷新后新提示 20261127")

    # _refresh_view() 走 page._render() 整页重建；重建后新提示条应进入 body，
    # 且滚动配置与三张卡片都不丢。
    refreshed = view.build()
    new_body, cards = _body_column(refreshed)

    assert "刷新后新提示 20261127" in " ".join(_texts(new_body))
    assert isinstance(new_body.scroll, ft.Scrollbar)
    assert new_body.scroll.thumb_visibility is True
    assert new_body.expand is True
    assert all(cards.get(key) for key, _ in _SECTIONS)
