"""批 115 BPAGES-A：回读校验页契约测试（不启动真引擎）。

覆盖：一页一个主按钮、空态保留表头、Markdown 解析（错误优先）、
0 错误“去入藏”、失败保留旧报告、1000 宽不横滚。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from services import engine_bridge as EB
from ui import bib_normalize as NV
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
                                   copy_text=lambda text: None)
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl"}
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
        value = getattr(node, "value", None)
        if isinstance(value, str):
            out.append(value)
        content = getattr(node, "content", None)
        if isinstance(content, str):
            out.append(content)
        label = getattr(node, "label", None)
        if isinstance(label, str):
            out.append(label)
    return out


REPORT = """# 回读校验报告

**结论：5 处问题 / 7 处提示** —— 交回稿与原件不一致。

- 退回文件：`/tmp/a.bib`（解析出 9 条条目）
- 原件：`/tmp/b.bib`（解析出 9 条条目）

## 问题（5）

- 键 `k1`：题名不一致
- 键 `k2`：年份不一致

## 提示（7）

- 键 `k9`：作者顺序可能不同
- 键 `k10`：页码缺失
- 键 `k11`：DOI 大小写
- 键 `k12`：期号缺失

## 附录：逐条状态

| 键 | 类型 | 问题 | 提示 | 与原件 |
|---|---|---|---|---|
| `k1` | @article | 1 | 0 | 题名不同 |
| `k2` | @book | 1 | 0 | 年份不同 |
| `k3` | @article | 0 | 0 | 一致 |
| `k4` | @article | 0 | 1 | 作者顺序 |
| `k5` | @article | 0 | 0 | 一致 |
| `k6` | @article | 0 | 0 | 一致 |
"""


def test_parse_report_counts_and_error_first():
    data = NV.parse_report(REPORT)
    assert (data["errors"], data["warnings"], data["records"]) == (5, 7, 9)
    assert data["keys"] == 6
    assert data["ok"] is False
    assert data["rows"][0]["kind"] == "error"
    assert "题名不一致" in data["rows"][0]["text"]


def test_default_report_path_matches_engine():
    assert NV.default_report_path("/tmp/a/Foo.bib").endswith("Foo_回读校验报告.md")


def test_normalize_initial_state_keeps_header_one_primary():
    host = _Host()
    view = NV.NormalizeView(host)
    tree = view.build()
    texts = _texts(tree)
    assert NV.VIEW_KEY == "normalize" and NV.ROLE == "readonly"
    assert texts.count("回读校验") == 1
    assert "还没有报告；先选 .bib 再生成。" in texts
    assert "级别" in texts and "问题" in texts  # 空态保留表头
    primaries = [n for n in _walk(tree) if isinstance(n, ft.FilledButton)]
    assert len(primaries) == 1 and primaries[0].content == "生成报告"
    assert not any(isinstance(n, ft.Card) for n in _walk(tree))
    for label in ("交回稿（.bib）", "原件（.bib", "正文（.tex"):
        assert any(label in text for text in texts), label


def test_normalize_requires_returned_path():
    host = _Host()
    view = NV.NormalizeView(host)
    view.generate()
    texts = _texts(view.build())
    assert "先选交回的 .bib 文件。" in texts


def _fake_engine(monkeypatch, report: str, code: int = 1, captured=None):
    def fake_checkbib(**kwargs):
        out = kwargs.get("out") or "/tmp/report.md"
        return SimpleNamespace(out=out, kwargs=kwargs)

    async def fake_run(call, *, on_line=None, on_proc=None, cwd=None, api=None,
                       notes="", stdin_text=None):
        if captured is not None:
            captured.update(call.kwargs)
        Path(call.out).write_text(report, encoding="utf-8")
        if on_line:
            on_line("checkbib running")
        return code

    monkeypatch.setattr(EB, "checkbib_call", fake_checkbib)
    monkeypatch.setattr(EB, "run_call", fake_run)


def test_normalize_success_renders_counts_and_remaining(monkeypatch, tmp_path):
    _fake_engine(monkeypatch, REPORT)
    host = _Host()
    view = NV.NormalizeView(host)
    view.returned = str(tmp_path / "returned.bib")
    view.original = str(tmp_path / "original.bib")
    view.compare = True
    view.report_path = str(tmp_path / "report.md")
    asyncio.run(view._run_check())
    texts = _texts(view.build())
    assert view.success.startswith("体检未通过：5 错误、7 警告")
    assert "体检未通过" in texts
    assert "5 错误 · 7 警告 · 9 记录 · 6 键" in texts
    assert "还有 2 条 · 完整内容见报告" in texts
    assert "报告未更新" not in " ".join(texts)


def test_normalize_pass_offers_import(monkeypatch, tmp_path):
    report = REPORT.replace("**结论：5 处问题 / 7 处提示**", "**结论：0 处问题 / 2 处提示**")
    _fake_engine(monkeypatch, report, code=0)
    host = _Host()
    view = NV.NormalizeView(host)
    view.returned = str(tmp_path / "returned.bib")
    view.report_path = str(tmp_path / "report.md")
    asyncio.run(view._run_check())
    texts = _texts(view.build())
    assert view.data["errors"] == 0
    assert "体检通过：0 错误、2 警告。" in texts
    assert "去入藏" in texts


def test_normalize_failure_keeps_old_report(monkeypatch, tmp_path):
    async def fake_run(call, **kwargs):
        raise RuntimeError("checkbib exploded")

    monkeypatch.setattr(EB, "checkbib_call", lambda **kw: SimpleNamespace(out=kw["out"]))
    monkeypatch.setattr(EB, "run_call", fake_run)
    host = _Host()
    view = NV.NormalizeView(host)
    view.returned = str(tmp_path / "returned.bib")
    view.report_path = str(tmp_path / "report.md")
    view.loaded = True
    asyncio.run(view._run_check())
    texts = _texts(view.build())
    assert "生成失败：checkbib exploded" in " ".join(texts)
    assert "旧报告未更新" in " ".join(texts)


def test_normalize_narrow_no_horizontal_scroll():
    host = _Host()
    host.page.width = 1000
    host.app.window_w = 1000
    view = NV.NormalizeView(host)
    tree = view.build()
    assert view._build_width < 1100
    assert not any(getattr(node, "scroll", None) == ft.ScrollMode.ALWAYS
                   for node in _walk(tree))
