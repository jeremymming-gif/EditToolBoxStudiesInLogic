"""批 115 BPAGES-A：查库页契约测试（不启窗口、不碰真库）。

覆盖：只读、首屏空态、空输入报错、total_matched 口径、首查 100 / 上限
200、默认不选中、1000 宽只留三列、详情 360。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft

from services import engine_bridge as EB
from ui import bib_query as QV
from ui import bib_shell as S
from ui.tokens import Tokens


class _FakeStore:
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value


class _FakePage:
    def __init__(self) -> None:
        self.on_keyboard_event = None
        self.dialogs = []
        self.tasks = []
        self.width = 1160

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)


class _Host:
    def __init__(self) -> None:
        self.t = Tokens("light")
        self.page = _FakePage()
        self.app = SimpleNamespace(store=_FakeStore(), notes_repo="", window_w=1160,
                                   copy_text=lambda text: None,
                                   open_path=lambda path: None)
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl", "records": 3883,
                          "sha256": "8f21"}
        self.view = ""

    def mark(self, event, **kwargs) -> None:
        pass

    def _render(self) -> None:
        pass

    def set_view(self, view) -> None:
        self.view = view


HITS = [
    {"key": "Fine1975", "score": 9.0, "verified": True,
     "title": "Some Logic", "author": "Kit Fine", "year": "1975",
     "record": {"key": "Fine1975", "entry_type": "article", "verified": True,
                "fields": {"title": "Some Logic", "author": "Kit Fine", "year": "1975",
                           "doi": "10.1000/x"},
                "sources": [{"issue": "2024年第1期", "slug": "fine"}]}},
    {"key": "li2024analysis", "score": 1.0, "verified": False,
     "record": {"key": "li2024analysis", "entry_type": "incollection",
                "fields": {"title": "分析哲学", "author": "李", "year": "2024"}}},
]
PAYLOAD = {"query": {"limit": 100}, "hits": HITS, "count": 2,
           "total_matched": 3883, "truncated": True}


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
        value = getattr(node, "value", None)
        if isinstance(value, str):
            out.append(value)
        content = getattr(node, "content", None)
        if isinstance(content, str):
            out.append(content)
    return out


def test_query_initial_state_readonly_and_one_primary():
    host = _Host()
    view = QV.QueryView(host)
    view.activate()
    tree = view.build()
    texts = _texts(tree)
    assert QV.VIEW_KEY == "query" and QV.ROLE == "readonly"
    assert texts.count("查库") == 1
    assert "还没有查询" in texts and "输入 key、DOI 或题名；⌘F 聚焦。" in texts
    assert "未选择记录" in texts
    assert "只读" in texts
    primaries = [n for n in _walk(tree) if isinstance(n, ft.FilledButton)]
    assert len(primaries) == 1
    assert not any(isinstance(n, ft.Card) for n in _walk(tree))


def test_query_empty_input_reports_error_without_disabling():
    host = _Host()
    view = QV.QueryView(host)
    view.activate()
    tree = view.build()
    buttons = [n for n in _walk(tree) if isinstance(n, ft.FilledButton)]
    assert buttons and not buttons[0].disabled
    view.text = ""
    view.query()
    texts = _texts(view.build())
    assert QV.EMPTY_INPUT in texts


def test_query_uses_total_matched_and_limit_100(monkeypatch):
    captured = {}

    def fake_read(call, *, timeout=120, notes="", env_extra=None):
        captured["argv"] = list(getattr(call, "argv", []))
        captured["label"] = getattr(call, "label", "")
        return {"ok": True, "json": dict(PAYLOAD), "code": 0, "lines": [], "error": ""}

    monkeypatch.setattr(EB, "read_json_command", fake_read)
    host = _Host()
    view = QV.QueryView(host)
    view.activate()
    view.text = "Fine1975"
    asyncio.run(view._run())
    texts = _texts(view.build())
    assert view.total_matched == 3883 and view.count == 2
    assert any("3,883" in text for text in texts)
    assert "1–2 / 3,883" in " ".join(texts)
    assert view.selected_key is None  # 默认不自动选中首行
    argv = captured["argv"]
    assert "--limit" in argv and argv[argv.index("--limit") + 1] == "100"
    assert "--key" in argv


def test_query_cap_200_disables_load_more(monkeypatch):
    monkeypatch.setattr(EB, "read_json_command",
                        lambda *a, **k: {"ok": True, "json": dict(PAYLOAD),
                                         "code": 0, "lines": [], "error": ""})
    host = _Host()
    view = QV.QueryView(host)
    view.activate()
    view.loaded = True
    view.count = 200
    view.limit = 200
    view.total_matched = 3883
    texts = _texts(view.build())
    assert "最多 200 条；请加条件" in texts
    assert "载入更多" not in texts


def test_query_narrow_keeps_three_columns_and_no_hscroll(monkeypatch):
    monkeypatch.setattr(EB, "read_json_command",
                        lambda *a, **k: {"ok": True, "json": dict(PAYLOAD),
                                         "code": 0, "lines": [], "error": ""})
    host = _Host()
    host.page.width = 1000
    host.app.window_w = 1000
    view = QV.QueryView(host)
    view.activate()
    view.text = "Fine1975"
    asyncio.run(view._run())
    tree = view.build()
    texts = _texts(tree)
    assert view._build_width < 1100
    assert "题名" in texts and "年" in texts and "状态" in texts
    assert "作者" not in texts and "来源" not in texts
    assert not any(getattr(node, "scroll", None) for node in _walk(tree)
                   if isinstance(node, ft.Row))


def test_query_filter_row_and_detail_geometry(monkeypatch):
    monkeypatch.setattr(EB, "read_json_command",
                        lambda *a, **k: {"ok": True, "json": dict(PAYLOAD),
                                         "code": 0, "lines": [], "error": ""})
    host = _Host()
    view = QV.QueryView(host)
    view.activate()
    tree = view.build()
    filters = [n for n in _walk(tree) if getattr(n, "_bib_filter_row", False)]
    assert filters and filters[0].height == host.t.px(S.FILTER_ROW_H)
    details = [n for n in _walk(tree) if getattr(n, "_bib_detail_panel", False)]
    assert details and details[0].width == host.t.px(QV.DETAIL_W)
