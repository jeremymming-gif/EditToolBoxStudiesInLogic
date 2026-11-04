"""批 115 UX 复评 P1-3/4/5 回归：bib:review 单一计数 + bib:settings 标签/底条。

只打页面契约，不启 GUI：review 用 EB.read_json_command stub，settings 用
GIT.status / EB.read_json_command stub。旧测试保留，本文件只补新口径。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import flet as ft

import services.engine_bridge as EB
from ui import bib_prefs as BP
from ui import bib_review as RV
from ui.tokens import Tokens


# ── 共用 stub / walker ────────────────────────────────────────────────
class _Store:
    def __init__(self, data=None) -> None:
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


class _FletPage:
    def __init__(self, width: int = 1160, height: int = 780) -> None:
        self.width = width
        self.height = height
        self.dialogs: list = []
        self.tasks: list = []
        self.clipboard: list[str] = []
        self.on_keyboard_event = None

    def update(self) -> None:
        pass

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)

    def set_clipboard(self, value) -> None:
        self.clipboard.append(value)


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
    out = []
    for node in _walk(root):
        for attr in ("value", "content", "label"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                out.append(value)
    return out


def _find(root, marker: str):
    return [node for node in _walk(root) if getattr(node, marker, False)]


# ── bib:review：单一计数投影 ─────────────────────────────────────────
class _ReviewPage(_FletPage):
    pass


class _ReviewHost:
    def __init__(self, width: int = 1160) -> None:
        self.t = Tokens("light")
        self.page = _ReviewPage(width)
        self.app = SimpleNamespace(store=_Store(), notes_repo="", window_w=width,
                                   copy_text=lambda text: None)
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl",
                           "conflicts": {"records": 135, "total": 157}}
        self.events: list[tuple] = []

    def mark(self, event, **kwargs) -> None:
        self.events.append((event, kwargs))

    def _render(self) -> None:
        pass


def _review(monkeypatch, payload: dict, *, stats=None, width: int = 1160):
    monkeypatch.setattr(
        EB, "read_json_command",
        lambda call, **kw: {"ok": True, "json": payload, "code": 0, "lines": [],
                            "argv": call.argv})
    host = _ReviewHost(width)
    if stats is not None:
        host.stats_data = stats
    view = RV.ReviewView(host)
    asyncio.run(view._load())
    return host, view, view.build()


def _item(key: str, field: str) -> dict:
    return {"record_key": key, "title": "T", "field": field, "issue": "SL",
            "slug": key, "diff": {field: {"old": "a", "new": "b"}},
            "pending": True, "verified": False}


def test_review_pending_zero_has_one_projection(monkeypatch):
    payload = {"db_sha256": "8f21" + "a" * 60,
               "totals": {"total": 157, "pending": 0, "resolved": 157},
               "items": []}
    host, view, root = _review(monkeypatch, payload)
    texts = _texts(root)

    # header、筛选计数、dock 都是同一句 0 条待处理/累计 157。
    assert root._bib_meta.value == "0 条待处理 · 累计 157 条已裁决"
    assert "0 条待处理 · 累计 157 条已裁决" in texts
    assert "涉及 0 条记录" not in " ".join(texts)
    assert "157 条冲突" not in " ".join(texts)  # pending=0 不再并列 157 冲突
    assert "显示 0 / 0 条待处理" in texts
    assert "没有待处理冲突" in texts

    # 没有写入对象：确认条整条隐藏，不再出现硬编码的 157/135。
    assert not _find(root, "_bib_confirm_bar")
    assert "157 条冲突 / 135 条记录" not in " ".join(texts)

    # 写库页的权限 chip 必须是 write，而不是只读。
    chips = [node for node in _walk(root) if getattr(node, "_bib_role", None)]
    assert len(chips) == 1 and chips[0]._bib_role == "write"
    assert "只读" not in texts


def test_review_pending_positive_projection_is_consistent(monkeypatch):
    payload = {"db_sha256": "8f21" + "a" * 60,
               "totals": {"total": 11, "pending": 3, "resolved": 8, "records": 4},
               "items": [_item("a", "title"), _item("b", "author"),
                         _item("c", "year")]}
    host, view, root = _review(monkeypatch, payload)
    texts = _texts(root)
    line = "3 条待处理 · 11 条冲突 · 涉及 4 条记录"

    assert root._bib_meta.value == line
    assert texts.count(line) == 2            # header + 筛选计数
    assert any(text.startswith("待处理 3 条冲突 · 涉及 4 条记录")
               for text in texts)           # dock
    assert "显示 3 / 3 条待处理" in texts
    joined = " ".join(texts)
    assert "157" not in joined and "135" not in joined

    bar = _find(root, "_bib_confirm_bar")
    assert len(bar) == 1
    assert any(text.endswith("3 条待处理 · 11 条冲突 · 涉及 4 条记录 · 本页只暂存意图")
               for text in _texts(bar[0]))
    assert bar[0]._bib_confirm_btn.disabled is True   # 未暂存 → 不可写


def test_review_missing_records_never_prints_zero(monkeypatch):
    payload = {"db_sha256": "8f21" + "a" * 60,
               "totals": {"total": 5, "pending": 2, "resolved": 3},
               "items": [_item("a", "title"), _item("b", "author")]}
    host, view, root = _review(monkeypatch, payload, stats={"db": "/tmp/x.jsonl"})
    texts = _texts(root)
    line = "2 条待处理 · 5 条冲突"

    assert root._bib_meta.value == line
    assert "涉及 0 条记录" not in " ".join(texts)
    assert any(text.startswith("待处理 2 条冲突") for text in texts)
    assert _find(root, "_bib_confirm_bar")


# ── bib:settings：开关标签 + 底部影响条 ──────────────────────────────
STATS = {"sha256": "a" * 64, "records": 3883,
         "aliases": {"records": 2081, "total": 4102},
         "bytes": 2202009, "engine_version": "1.6.1"}


class _PrefsHost:
    def __init__(self, root: Path) -> None:
        self.app = SimpleNamespace(toasts=[], notes_repo="")
        self.store = _Store({"bibdb.lookup_enabled": True})
        self.t = Tokens()
        self.page = _FletPage()
        self.stats_data = {"db": str(root / "bib_db" / "bibliography.jsonl")}
        self.events: list[tuple] = []

    def mark(self, event, **fields) -> None:
        self.events.append((event, fields))

    def notify(self, message: str) -> None:
        self.app.toasts.append(message)

    def run_task_ui(self, fn) -> None:
        pass


def _prefs(tmp_path, monkeypatch):
    monkeypatch.setattr(BP.EB, "read_json_command",
                        lambda call, **kw: {"ok": True, "json": STATS, "code": 0,
                                            "stderr": ""})
    status = {"ok": True, "git": True, "branch": "main", "head": "4f2a9c1abc",
              "ahead": 0, "dirty_db": False, "dirty_other": False,
              "commits": [{"sha": "4f2a9c1abc", "subject": "引用库：手动提交",
                           "date": "2026-11-03 22:41"}], "warnings": []}
    monkeypatch.setattr(BP.GIT, "status",
                        lambda db="", *, notes_repo="": dict(status))
    db = Path(tmp_path) / "bib_db" / "bibliography.jsonl"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("x", encoding="utf-8")
    host = _PrefsHost(Path(tmp_path))
    view = BP.BibPrefsView(host)
    asyncio.run(view._load())
    return host, view, view.build()


def test_settings_switches_have_visible_labels_and_semantics(tmp_path, monkeypatch):
    host, view, root = _prefs(tmp_path, monkeypatch)
    rows = _find(root, "_bib_switch_row")
    assert [row._bib_switch_row for row in rows] == [key for key, _label in BP.SWITCHES]

    by_key = {row._bib_switch_row: row for row in rows}
    for key, label in BP.SWITCHES:
        row = by_key[key]
        texts = _texts(row)
        assert label in texts
        assert BP.SWITCH_HINTS[key] in texts
        switches = [node for node in _walk(row) if isinstance(node, ft.Switch)]
        assert len(switches) == 1
        assert switches[0].tooltip == key   # 真实 settings 键可悬停查看

    plan_texts = _texts(by_key["bibdb.verify_plan"])
    assert any("不检索" in text for text in plan_texts)
    assert any("120" in text for text in _texts(by_key["bibdb.verify_all_refs"]))
    assert any("跳过检索" in text for text in _texts(by_key["bibdb.lookup_enabled"]))


def test_settings_bottom_impact_bar_is_persistent_and_full(tmp_path, monkeypatch):
    host, view, root = _prefs(tmp_path, monkeypatch)
    scroll = _find(root, "_bib_scroll_body")
    bars = _find(root, "_bib_impact_bar")
    assert len(scroll) == 1 and len(bars) == 1
    bar = bars[0]

    expected = "1 个文件 · 1 条新提交 · 不推送"
    assert expected in _texts(bar)
    assert any("推送 / 回退只在设置页" in text for text in _texts(bar))
    assert not _find(scroll[0], "_bib_impact_bar")   # 不进滚动区，不会被裁
    assert scroll[0].expand is True
    # 批 118 D2：设置页长表单改常显条（Scrollbar 配置对象，不是 AUTO）。
    assert isinstance(scroll[0].scroll, ft.Scrollbar)
    assert scroll[0].scroll.thumb_visibility is True
    assert getattr(scroll[0].controls[-1], "_bib_bottom_pad", False)
    assert scroll[0].controls[-1].height == 12
    for node in _walk(bar):
        if isinstance(node, ft.Text):
            assert node.overflow != ft.TextOverflow.ELLIPSIS
            assert node.max_lines is None or node.max_lines >= 2


def test_settings_git_heading_and_commit_tooltip(tmp_path, monkeypatch):
    host, view, root = _prefs(tmp_path, monkeypatch)
    texts = _texts(root)
    assert "03 Git" in texts
    assert "只提交本地" in texts
    # 批 119：设置页正文铺满页壳（旧 720 限宽已删），不再留右侧空栏。
    assert not any(node.width == 720 for node in _walk(root)
                   if isinstance(node, ft.Container))

    # 最后提交 subject 被 32 高单行截断时，tooltip 必须是全文。
    values = [node for node in _walk(root)
              if isinstance(node, ft.Text) and "引用库：手动提交" in (node.value or "")]
    assert values and all(node.tooltip == node.value for node in values)
