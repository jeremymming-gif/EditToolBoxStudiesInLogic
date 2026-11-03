"""批 115 BPAGES-A：库健康页契约测试（EB 全 mock）。

数字口径：76 = 警告句数，12 = 规则类数，冲突行 135 条记录 / 157 处差异。
确认非重复只写 sidecar，不写库；先 dry-run 再 apply。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft

from services import engine_bridge as EB
from ui import bib_health as HV
from ui.tokens import Tokens


class _FakeStore:
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value


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
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl", "sha256": "9c02" + "b" * 60}
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
        for attr in ("value", "content", "label", "hint_text"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                out.append(value)
    return out


ITEMS = [
    {"code": "conflict_pending", "count": 135, "pending_records": 135,
     "pending_total": 157, "records": ["li2024analysis · title", "chen2023logic · year"]},
    {"code": "near_duplicates", "count": 12, "records": ["wang2020a ≈ wang2020b"] * 12},
    {"code": "duplicate_content", "count": 2, "records": ["g1a|g1b|g1c", "g2a|g2b"]},
    {"code": "missing_title", "count": 5, "records": ["k1", "k2"]},
    {"code": "missing_author", "count": 4, "records": ["k3"]},
    {"code": "missing_year", "count": 3, "records": ["k4"]},
    {"code": "bad_doi", "count": 2, "records": ["k5"]},
    {"code": "unknown_entry_type", "count": 1, "records": ["k6"]},
    {"code": "alias_duplicate", "count": 1, "records": ["k7"]},
    {"code": "unverified", "count": 7, "records": ["k8"]},
    {"code": "needs_inversion", "count": 4, "records": ["k9"]},
    {"code": "health_ignore", "count": 1, "records": ["k10"]},
]

LINT = {
    "db": "/tmp/lib/bibliography.jsonl",
    "ok": True,
    "errors": [],
    "warnings": ["w"] * 76,
    "warning_items": ITEMS,
    "counts": {"records": 3883, "errors": 0, "warnings": 76,
               "near_duplicates": 12, "near_duplicates_acknowledged": 9,
               "conflicts_pending": {"records": 135, "total": 157}},
}


def _loaded(monkeypatch, data=None, width: int = 1160):
    payload = data or LINT
    monkeypatch.setattr(EB, "read_json_command",
                        lambda call, **kw: {"ok": True, "json": payload, "code": 1,
                                            "lines": [], "argv": call.argv})
    host = _Host(width)
    view = HV.HealthView(host)
    asyncio.run(view._load())
    return host, view


def test_health_unit_counts_and_keys():
    assert HV.rule_label("conflict_pending") == "冲突待裁决"
    assert HV.count_text(ITEMS[0]) == "135 条"
    assert HV.count_hint(ITEMS[0]) == "157 处差异；去待处理"
    assert HV.count_text(ITEMS[1]) == "12 对"
    assert HV.count_text(ITEMS[2]) == "2 组"
    assert HV.record_keys("a ≈ b") == ["a", "b"]
    assert HV.record_keys("g1a|g1b|g1c") == ["g1a", "g1b", "g1c"]
    assert HV.severity_of("unverified") == "提示"
    assert HV.severity_of("conflict_pending") == "警告"


def test_health_band_and_conflict_units(monkeypatch):
    host, view = _loaded(monkeypatch)
    texts = _texts(view.build())
    joined = " ".join(texts)
    assert texts.count("库健康") == 1
    assert not any(isinstance(n, ft.Card) for n in _walk(view.build()))
    for value in ("76", "0", "12", "9"):
        assert value in texts
    assert "警告句数，不是记录数" in joined
    assert "135 条" in texts and "157 处差异；去待处理" in joined
    assert "135 条冲突" not in joined


def test_health_table_paging_and_header(monkeypatch):
    host, view = _loaded(monkeypatch)
    tree = view.build()
    assert len(view.rows) == 9
    joined = " ".join(_texts(tree))
    assert "显示 9 / 12 类" in joined
    assert "载入更多（3）" in joined
    assert "示例记录" in joined  # 1160 宽显示
    header30 = [n for n in _walk(tree)
                if isinstance(n, ft.Container) and n.height == host.t.px(30)]
    row32 = [n for n in _walk(tree)
             if isinstance(n, ft.Container) and n.height == host.t.px(32)]
    assert header30 and len(row32) >= 9
    assert "重新体检" in joined


def test_health_expand_shows_records(monkeypatch):
    host, view = _loaded(monkeypatch)
    view._toggle_expand("conflict_pending")
    texts = " ".join(_texts(view._table()))
    assert "li2024analysis · title" in texts
    assert "共 135 项" in texts
    assert "查看全部" in texts


def test_health_narrow_hides_sample_column(monkeypatch):
    host, view = _loaded(monkeypatch, width=1000)
    texts = " ".join(_texts(view.build()))
    assert "示例记录" not in texts
    assert "规则" in texts and "严重度" in texts and "记录数" in texts
    assert not any(getattr(n, "scroll", None) == ft.ScrollMode.ALWAYS
                   for n in _walk(view.build()))


def test_health_ack_dry_run_then_apply(monkeypatch):
    host, view = _loaded(monkeypatch)
    calls = []

    def fake_read(call, **kw):
        calls.append(call)
        added = 0 if "--dry-run" in call.argv else 2
        return {"ok": True, "json": {"added": added}, "code": 0, "lines": [],
                "argv": call.argv}

    monkeypatch.setattr(EB, "read_json_command", fake_read)
    view.items[1]["records"] = ["wang2020a ≈ wang2020b"]
    view.selected = {"near_duplicates"}
    view.stage_ack()
    assert view.staged is True
    asyncio.run(view._apply_ack())
    assert len(calls) == 2
    assert "--dry-run" in calls[0].argv and "--dry-run" not in calls[1].argv
    assert "已写 2 项" in view.success and "库未改" in view.success
    assert view.selected == set()
    assert view.staged is False


def test_health_empty_state(monkeypatch):
    empty = {"warning_items": [], "counts": {"errors": 0, "warnings": 0,
                                             "near_duplicates_acknowledged": 0}}
    host, view = _loaded(monkeypatch, data=empty)
    texts = " ".join(_texts(view.build()))
    assert "没有待处理警告" in texts
    assert "重新体检" in texts
    assert "没有待处理警告" in texts  # 空态保留表头之下的一行
    header30 = [n for n in _walk(view.build())
                if isinstance(n, ft.Container) and n.height == host.t.px(30)]
    assert header30
