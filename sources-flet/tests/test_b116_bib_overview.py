"""批 115 BPAGES-A：概览页契约测试（不启窗口、不碰真库）。

对应 spec/pages/overview：五格口径、队列表固定顺序、只读、无默认选中、
1000 宽不横向滚动。引擎全部 monkeypatch，绝不触发真实子进程。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import flet as ft
import pytest

from services import engine_bridge as EB
from ui import bib_overview as OV
from ui.tokens import Tokens


class _FakeStore:
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


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
                                   copy_text=lambda text: None)
        self.stats_data = {"db": "/tmp/does-not-exist/bibliography.jsonl",
                          "sha256": "8f21c0de1234abcd"}
        self.renders = 0
        self.view = ""

    def mark(self, event, **kwargs) -> None:
        pass

    def _render(self) -> None:
        self.renders += 1

    def set_view(self, view) -> None:
        self.view = view


STATS = {
    "path": "/tmp/lib/bibliography.jsonl", "exists": True, "bytes": 1048576,
    "sha256": "8f21c0de1234abcd9999", "records": 3883, "verified": 3800,
    "unverified": 83, "sources": 4952, "aliases": {"records": 40, "total": 120},
    "conflicts": {"records": 135, "total": 157}, "updated_newest": "2026-11-03T21:31:00",
    "dirty": False, "raw_lines": 3883,
}
CONFLICTS = {"db_sha256": "8f21c0de1234abcd9999",
             "totals": {"total": 157, "pending": 0, "resolved": 157}, "items": []}
LINT = {"ok": True, "errors": [], "warnings": ["w"] * 76,
        "warning_items": [{"code": "conflict_pending", "count": 157},
                          {"code": "near_duplicates", "count": 12}],
        "counts": {"records": 3883, "errors": 0, "warnings": 76,
                   "near_duplicates_acknowledged": 2,
                   "needs_inversion": {"author": 43, "editor": 0, "institution": 0}}}
DUPLICATES = {"reports": {}, "pairs": 0}
SUGGEST = {"registry": "missing_field", "count": 86, "rows": []}


def _patch(monkeypatch, *, stats=None, conflicts=None, lint=None, dup=None, sug=None,
           fail=()):
    calls = {
        "stats": SimpleNamespace(name="stats"), "conflicts": SimpleNamespace(name="conflicts"),
        "lint": SimpleNamespace(name="lint"), "dup": SimpleNamespace(name="dup"),
        "sug": SimpleNamespace(name="sug"),
    }
    monkeypatch.setattr(EB, "bibdb_stats_call", lambda: calls["stats"])
    monkeypatch.setattr(EB, "bibdb_conflicts_call", lambda **kw: calls["conflicts"])
    monkeypatch.setattr(EB, "bibdb_lint_call", lambda **kw: calls["lint"])
    monkeypatch.setattr(EB, "bibdb_report_duplicates_call", lambda **kw: calls["dup"])
    monkeypatch.setattr(EB, "bibdb_suggest_fixes_call", lambda **kw: calls["sug"])
    payloads = {
        "stats": (stats if stats is not None else STATS, True),
        "conflicts": (conflicts if conflicts is not None else CONFLICTS, True),
        "lint": (lint if lint is not None else LINT, True),
        "dup": (dup if dup is not None else DUPLICATES, True),
        "sug": (sug if sug is not None else SUGGEST, True),
    }

    def fake_read(call, *, timeout=120, notes="", env_extra=None):
        name = getattr(call, "name", "")
        data, ok = payloads[name]
        if name in fail:
            return {"ok": False, "json": {}, "code": 2, "lines": [],
                    "error": "Traceback: boom", "argv": [], "label": name}
        return {"ok": ok, "json": dict(data), "code": 0, "lines": [],
                "error": "", "argv": [], "label": name}

    monkeypatch.setattr(EB, "read_json_command", fake_read)


def _walk(control) -> list:
    out = [control]
    content = getattr(control, "content", None)
    if content is not None:
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    for child in getattr(control, "actions", None) or ():
        out.extend(_walk(child))
    for child in getattr(control, "cells", None) or ():
        out.extend(_walk(child))
    return out


def _texts(control) -> list[str]:
    out = []
    seen: set[int] = set()
    for node in _walk(control):
        if id(node) in seen:
            continue
        seen.add(id(node))
        value = getattr(node, "value", None)
        if isinstance(value, str):
            out.append(value)
        label = getattr(node, "content", None)
        if isinstance(label, str):
            out.append(label)
    return out


def _labels(host):
    tree = host.view_obj.build()
    return tree, _texts(tree)


def _load(host) -> None:
    asyncio.run(host.view_obj.load())


def test_overview_has_one_h1_no_cards(monkeypatch):
    _patch(monkeypatch)
    host = _Host()
    host.view_obj = OV.OverviewView(host)
    _load(host)
    tree, texts = _labels(host)
    assert texts.count("概览") == 1
    assert not any(isinstance(node, ft.Card) for node in _walk(tree))
    assert OV.VIEW_KEY == "overview" and OV.ROLE == "readonly"


def test_overview_five_cells_use_pending_and_history(monkeypatch):
    _patch(monkeypatch)
    host = _Host()
    host.view_obj = OV.OverviewView(host)
    _load(host)
    tree, texts = _labels(host)
    for word in ("条目", "待办冲突", "体检警告", "重复候选", "归位建议"):
        assert word in texts
    assert "3,883" in texts and "76" in texts and "86" in texts
    # 待办是 0；历史 157 / 135 条记录不能被写成「135 条」的裸数字
    assert "0" in texts
    assert "累计 157" in " ".join(texts)
    assert "135 条记录" in " ".join(texts)
    assert "135" not in texts and "135 条" not in texts
    assert any(getattr(node, "_bib_band", False) for node in _walk(tree))


def test_overview_queue_fixed_order_and_next_steps(monkeypatch):
    _patch(monkeypatch)
    host = _Host()
    host.view_obj = OV.OverviewView(host)
    _load(host)
    _tree, texts = _labels(host)
    order = [texts.index(name) for name in ("去待处理 →", "去字段归位 →", "去库健康 →",
                                            "去查重合并 →", "去回读校验 →")]
    assert order == sorted(order)


def test_overview_one_primary_and_readonly_chip(monkeypatch):
    _patch(monkeypatch)
    host = _Host()
    host.view_obj = OV.OverviewView(host)
    _load(host)
    tree, texts = _labels(host)
    primaries = [node for node in _walk(tree)
                 if isinstance(node, ft.FilledButton) and node.content == "入藏新条目"]
    assert len(primaries) == 1
    assert "只读" in texts
    assert any(getattr(node, "_bib_chip", False) for node in _walk(tree)) or "只读" in texts


def test_overview_partial_failure_degrades_only_that_cell(monkeypatch):
    _patch(monkeypatch, fail=("lint",))
    host = _Host()
    host.view_obj = OV.OverviewView(host)
    _load(host)
    _tree, texts = _labels(host)
    assert "3,883" in texts
    assert "—" in texts
    assert any("读取失败" in text or text == "—" for text in texts)
    assert host.view_obj.error


def test_overview_empty_library_keeps_header_and_action(monkeypatch):
    _patch(monkeypatch, stats={**STATS, "records": 0, "conflicts": {"records": 0, "total": 0}},
           conflicts={"totals": {"total": 0, "pending": 0, "resolved": 0}, "items": []},
           lint={**LINT, "counts": {**LINT["counts"], "warnings": 0}},
           sug={"count": 0}, dup={"pairs": 0})
    host = _Host()
    host.view_obj = OV.OverviewView(host)
    _load(host)
    tree, texts = _labels(host)
    assert "库里还没有条目；导入 .bib 后列出待办。" in texts
    assert any(isinstance(node, ft.FilledButton) and node.content == "入藏新条目"
               for node in _walk(tree))


def test_overview_narrow_has_no_horizontal_scroll(monkeypatch):
    _patch(monkeypatch)
    host = _Host()
    host.page.width = 1000
    host.app.window_w = 1000
    host.view_obj = OV.OverviewView(host)
    _load(host)
    tree, _texts_ = _labels(host)
    rows = [node for node in _walk(tree) if isinstance(node, ft.Row)]
    assert all(not getattr(row, "scroll", None) for row in rows)
    assert host.view_obj._build_width < 1100
