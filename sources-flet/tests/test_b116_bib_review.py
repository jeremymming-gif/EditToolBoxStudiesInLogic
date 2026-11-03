"""批 115 BPAGES-A：待处理（冲突裁决）页契约测试（EB 全 mock）。

红线口径：157 条冲突 / 135 条记录必须分开写；默认不自动选中首行；
写入先 dry-run 再 apply，单槽意图。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft

from services import engine_bridge as EB
from ui import bib_review as RV
from ui.tokens import Tokens


class _FakeStore:
    def set(self, key, value) -> None:
        pass


class _FakePage:
    def __init__(self, width: int = 1160) -> None:
        self.on_keyboard_event = None
        self.dialogs = []
        self.tasks = []
        self.width = width

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)


class _Host:
    def __init__(self, width: int = 1160) -> None:
        self.t = Tokens("light")
        self.page = _FakePage(width)
        self.app = SimpleNamespace(store=_FakeStore(), notes_repo="", window_w=width,
                                   copy_text=lambda text: None)
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl",
                           "conflicts": {"records": 135, "total": 157}}
        self.view = ""

    def mark(self, event, **kwargs) -> None:
        pass

    def _render(self) -> None:
        pass

    def set_view(self, view) -> None:
        self.view = view


def _walk(control) -> list:
    out = [control]
    content = getattr(control, "content", None)
    if content is not None:
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    for child in getattr(control, "actions", None) or ():
        out.extend(_walk(child))
    return out


def _texts(control) -> list[str]:
    out = []
    for node in _walk(control):
        for attr in ("value", "content", "label"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                out.append(value)
    return out


CONFLICTS = {
    "db": "/tmp/lib/bibliography.jsonl",
    "db_sha256": "8f21" + "a" * 60,
    "totals": {"total": 157, "pending": 2, "resolved": 155},
    "items": [
        {"record_key": "li2024analysis", "title": "Analyticity revisited",
         "field": "title", "issue": "SL", "slug": "li2024",
         "diff": {"title": {"old": "Analyticity", "new": "Analyticity revisited"}},
         "incoming_sha256": "abc123", "pending": True, "verified": False},
        {"record_key": "li2024analysis", "title": "Analyticity revisited",
         "field": "author", "issue": "SL", "slug": "li2024",
         "diff": {"author": {"old": "Li", "new": "Li, W."}},
         "incoming_sha256": "abc123", "pending": True, "verified": True},
    ],
}


def _loaded(monkeypatch, data=None, width: int = 1160):
    payload = data or CONFLICTS
    monkeypatch.setattr(EB, "read_json_command",
                        lambda call, **kw: {"ok": True, "json": payload, "code": 0,
                                            "lines": [], "argv": call.argv})
    host = _Host(width)
    view = RV.ReviewView(host)
    asyncio.run(view._load())
    return host, view


def test_review_load_counts_and_no_bare_record_number(monkeypatch):
    host, view = _loaded(monkeypatch)
    texts = _texts(view.build())
    joined = " ".join(texts)
    assert "157 条冲突 · 涉及 135 条记录" in joined
    assert "135 条冲突" not in joined
    assert view.total_conflicts == 157 and view.total_records == 135
    assert view.selected_key is None  # 默认不自动选中首行
    assert "未选择冲突" in joined
    filled = [n for n in _walk(view.build()) if isinstance(n, ft.FilledButton)]
    assert len(filled) == 1 and filled[0].content == "确认写入"
    assert filled[0].disabled is True


def test_review_severity_mapping():
    assert RV.severity_of({"diff": {"title": {"old": "a", "new": "b"}}}) == "高"
    assert RV.severity_of({"diff": {"pages": {"old": "1", "new": "2"}}}) == "中"
    assert RV.severity_of({"diff": {"doi": {"old": "x", "new": ""}}}) == "低"
    assert RV.severity_of({}) == "低"


def test_review_queue_geometry_and_rows(monkeypatch):
    host, view = _loaded(monkeypatch)
    view.build()
    assert len(view.rows) == 2
    assert view.rows[0]["key"] == "li2024analysis"
    view.selected_key = view.rows[0]["no"]
    assert "记录共 2 条冲突" in " ".join(_texts(view._detail()))
    header30 = [n for n in _walk(view.build())
                if isinstance(n, ft.Container) and n.height == host.t.px(30)]
    row32 = [n for n in _walk(view.build())
             if isinstance(n, ft.Container) and n.height == host.t.px(32)]
    assert header30 and row32


def test_review_stage_detail_then_dry_run_then_apply(monkeypatch):
    host, view = _loaded(monkeypatch)
    calls = []

    def fake_read(call, **kw):
        calls.append(call)
        return {"ok": True, "json": {}, "code": 0, "lines": [], "argv": call.argv}

    monkeypatch.setattr(EB, "read_json_command", fake_read)
    view.selected_key = "C001"
    view.adopt = {"title": True}
    view._stage_detail(False)
    assert view.staged and view.staged["fields"] == ["title"]
    assert view._confirm_disabled() is False
    asyncio.run(view._write())
    assert len(calls) == 2
    assert "--dry-run" in calls[0].argv
    assert "--dry-run" not in calls[1].argv
    assert "写入成功 · 1 条记录 · 1 个字段" in view.success


def test_review_keep_action(monkeypatch):
    host, view = _loaded(monkeypatch)
    monkeypatch.setattr(EB, "read_json_command",
                        lambda call, **kw: {"ok": True, "json": {}, "code": 0,
                                            "lines": [], "argv": call.argv})
    view.selected_key = "C002"
    view._stage_keep()
    assert view.staged["kind"] == "keep"
    asyncio.run(view._write())
    assert "1 条记录 · 0 个字段" in view.success


def test_review_batch_bar_and_stage(monkeypatch):
    host, view = _loaded(monkeypatch)
    view.selected = {"C001", "C002"}
    tree = view.build()
    texts = " ".join(_texts(tree))
    assert "已选 2 条" in texts
    view._stage_batch()
    assert view.staged["kind"] == "batch"
    assert view.staged["rows"][0]["key"] == "li2024analysis"


def test_review_empty_and_narrow(monkeypatch):
    empty = {"db_sha256": "8f21", "totals": {"total": 0, "pending": 0, "resolved": 0},
             "items": []}
    host, view = _loaded(monkeypatch, data=empty, width=1000)
    texts = " ".join(_texts(view.build()))
    assert "没有待处理冲突" in texts
    assert "冲突详情" in texts
    assert "已取消，未写入" not in texts
    assert view._build_width < 1100
    assert not any(getattr(n, "scroll", None) == ft.ScrollMode.ALWAYS
                   for n in _walk(view.build()))
