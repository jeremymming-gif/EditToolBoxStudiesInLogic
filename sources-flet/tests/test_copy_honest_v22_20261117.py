"""V22（验收 §3.1-8）：复制按钮**不许假成功**。

以前 `bib_advanced._set_clipboard` 无条件 `return True`、`cleanup._copy` 写死
`copied = True`：pbcopy 失败时界面照样 toast「已复制…」，把 `copy_text` 已经弹出的
「复制失败」覆盖掉 —— 用户看到的是假成功。

本文件只钉住「成功/失败如实上报」这一层：假 host，不启窗口、不碰真剪贴板。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from ui import bib_advanced as BA  # noqa: E402
from ui import cleanup as CL  # noqa: E402
from ui.tokens import Tokens  # noqa: E402

BIBTEX = "@article{K1,\n  title = {Some Logic}\n}"


class _FakeFletPage:
    """Flet Page 替身：默认**没有** services（= 未连接会话，剪贴板不可用）。"""

    def __init__(self) -> None:
        self.width = 1160
        self.tasks: list = []

    def run_task(self, handler, *args) -> None:
        self.tasks.append((handler, args))


class _Host:
    """页面替身：`mark` / `_render` 记账，`app` 可带或不带 copy_text。"""

    def __init__(self, copy_text=None, *, with_copy=True) -> None:
        self.t = Tokens("light")
        self.page = _FakeFletPage()
        self.marks: list[tuple[str, dict]] = []
        self.toasts: list[str] = []
        app_fields = {"toast": self.toasts.append, "window_w": 1160,
                      "notes_repo": "", "store": SimpleNamespace(
                          get=lambda *a, **k: "", set=lambda *a, **k: None)}
        if with_copy:
            app_fields["copy_text"] = copy_text if copy_text is not None else (lambda text: None)
        self.app = SimpleNamespace(**app_fields)

    def mark(self, event: str, **fields) -> None:
        self.marks.append((event, fields))

    def _render(self) -> None:
        pass


def _copy_bibtex(host) -> list[str]:
    view = BA.BibQueryView(host)
    view.hits = [{"key": "K1", "bibtex": BIBTEX,
                  "record": {"key": "K1", "fields": {"title": "Some Logic"}}}]
    view.selected = 0
    asyncio.run(view._copy_bibtex())
    return host.toasts


def _cleanup_copy(host, text: str = "a.aux\nb.aux") -> list[str]:
    panel = CL.CleanupPanel(host)
    panel._copy(text, "全部明细")
    return host.toasts


def _assert_no_success(toasts: list[str], what: str) -> None:
    joined = "\n".join(toasts)
    assert "已复制" not in joined, f"{what}：失败时不许说「已复制」，实际 {toasts!r}"


# ── ① copy_text 返回 False → 不报成功 ────────────────────────────────────
def test_bib_advanced_false_does_not_claim_success() -> None:
    _assert_no_success(_copy_bibtex(_Host(copy_text=lambda text: False)), "bib_advanced")


def test_cleanup_false_does_not_claim_success() -> None:
    _assert_no_success(_cleanup_copy(_Host(copy_text=lambda text: False)), "cleanup")


def test_cleanup_false_toast_says_failure() -> None:
    assert _cleanup_copy(_Host(copy_text=lambda text: False)) == [
        "复制失败：剪贴板不可用（全部明细）"]


# ── ② copy_text 返回 True → 报成功 ──────────────────────────────────────
def test_bib_advanced_true_reports_success() -> None:
    toasts = _copy_bibtex(_Host(copy_text=lambda text: True))
    assert toasts == [BA.copy_done_message("K1")]


def test_cleanup_true_reports_success() -> None:
    assert _cleanup_copy(_Host(copy_text=lambda text: True)) == ["已复制全部明细"]


# ── ③ copy_text 抛异常 → 失败，且不被成功文案覆盖 ────────────────────────
def test_bib_advanced_raises_reports_failure_not_overwritten() -> None:
    def boom(text):
        raise OSError("pbcopy missing")

    toasts = _copy_bibtex(_Host(copy_text=boom))
    _assert_no_success(toasts, "bib_advanced 抛异常")
    assert toasts == []  # 失败走内联提示，不 toast 成功覆盖「复制失败」


def test_cleanup_raises_reports_failure() -> None:
    def boom(text):
        raise OSError("pbcopy missing")

    toasts = _cleanup_copy(_Host(copy_text=boom))
    _assert_no_success(toasts, "cleanup 抛异常")
    assert "复制失败" in toasts[-1]


# ── ④ host 没有 copy_text → 报失败，绝不报成功 ───────────────────────────
def test_bib_advanced_without_copy_text_reports_failure() -> None:
    _assert_no_success(_copy_bibtex(_Host(with_copy=False)), "bib_advanced 无 copy_text")


def test_cleanup_without_copy_text_reports_failure() -> None:
    toasts = _cleanup_copy(_Host(with_copy=False))
    _assert_no_success(toasts, "cleanup 无 copy_text")
    assert "复制失败" in toasts[-1]


# ── ⑤ marker / 日志里不许出现被复制的正文 ────────────────────────────────
def test_cleanup_marker_records_lengths_not_text() -> None:
    host = _Host(copy_text=lambda text: True)
    _cleanup_copy(host)
    events = [fields for event, fields in host.marks if event == "cleanup_copy"]
    assert events, host.marks
    fields = events[-1]
    assert fields["ok"] is True
    assert fields["chars"] == len("a.aux\nb.aux")
    assert fields["lines"] == 2
    assert "a.aux" not in repr(fields)


def test_bib_advanced_marker_records_lengths_not_text() -> None:
    host = _Host(copy_text=lambda text: True)
    _copy_bibtex(host)
    events = [fields for event, fields in host.marks if event == "bib_query_copy"]
    assert events, host.marks
    fields = events[-1]
    assert fields["key"] == "K1" and fields["ok"] is True
    assert fields["chars"] == len(BIBTEX)
    assert "Some Logic" not in repr(fields)


# ── ⑥ 空文本：不写剪贴板、不说「已复制」 ─────────────────────────────────
def test_cleanup_empty_text_is_not_success() -> None:
    host = _Host(copy_text=lambda text: True)
    panel = CL.CleanupPanel(host)
    panel._copy("   ", "全部明细")
    assert host.toasts == ["全部明细为空，没有可复制的内容"]
    assert host.marks == []
