"""P10 R03：settings 日志渲染节流回归（纯 Python，无 GUI/平台依赖）。

render_logs_throttled 合并 min_interval_s 窗口内的渲染请求（置 pending），
窗口外立即渲染；flush_logs 在运行结束补渲染一次尾部日志，无 pending 时 no-op。
本文件只验证节流/合并/flush 语义与 LogBuffer 数据完整性，不实例化 Flet 控件。
"""
from __future__ import annotations

from types import SimpleNamespace

from ui import settings_shell as SHELL


def _counting_render(monkeypatch):
    calls: list[object] = []

    def _render(page) -> None:
        calls.append(page)

    monkeypatch.setattr(SHELL, "render_logs", _render)
    return calls


def test_throttled_merges_within_window(monkeypatch) -> None:
    calls = _counting_render(monkeypatch)
    page = SimpleNamespace()

    assert SHELL.render_logs_throttled(page, now=0.0) is True
    assert SHELL.render_logs_throttled(page, now=0.05) is False
    assert getattr(page, "_logs_render_pending", False) is True
    assert SHELL.render_logs_throttled(page, now=0.10) is False
    assert SHELL.render_logs_throttled(page, now=0.30) is True

    assert len(calls) == 2
    assert page._logs_render_pending is False
    assert page._logs_render_last_mono == 0.30


def test_throttle_interval_boundary(monkeypatch) -> None:
    calls = _counting_render(monkeypatch)
    page = SimpleNamespace()

    # min_interval_s 恰好命中时不合并（>= 语义）；0.5 是二进制可精确表示值。
    assert SHELL.render_logs_throttled(page, now=0.0, min_interval_s=0.5) is True
    assert SHELL.render_logs_throttled(page, now=0.25, min_interval_s=0.5) is False
    assert SHELL.render_logs_throttled(page, now=0.5, min_interval_s=0.5) is True

    assert len(calls) == 2
    assert page._logs_render_pending is False


def test_flush_renders_pending_tail(monkeypatch) -> None:
    calls = _counting_render(monkeypatch)
    page = SimpleNamespace()

    assert SHELL.render_logs_throttled(page, now=0.0) is True
    assert SHELL.render_logs_throttled(page, now=0.05) is False
    assert page._logs_render_pending is True

    assert SHELL.flush_logs(page) is True
    assert page._logs_render_pending is False
    assert len(calls) == 2

    # 已无 pending：再次 flush 不再渲染。
    assert SHELL.flush_logs(page) is False
    assert len(calls) == 2


def test_throttle_keeps_all_lines_and_tail(monkeypatch) -> None:
    calls = _counting_render(monkeypatch)
    page = SimpleNamespace()
    buffer = SHELL.LogBuffer()
    for index in range(2000):
        buffer.add(f"line {index}", "info")

    # 2000 行期间反复请求渲染：窗口内合并，窗口外渲染。
    for step in range(400):
        SHELL.render_logs_throttled(page, now=step * 0.05)
    if getattr(page, "_logs_render_pending", False):
        SHELL.flush_logs(page)

    # 数据完整：节流只影响 UI 刷新频率，不丢行、不改顺序。
    assert buffer.count() == 2000
    assert buffer.lines()[0] == "line 0"
    assert buffer.lines()[-1] == "line 1999"
    # 400 次请求被合并到约 134 次（20s / 0.15s），远少于逐次渲染。
    assert len(calls) <= 140
    assert page._logs_render_pending is False


def test_no_pending_flush_noop(monkeypatch) -> None:
    calls = _counting_render(monkeypatch)
    page = SimpleNamespace()

    assert SHELL.flush_logs(page) is False
    assert len(calls) == 0
    assert getattr(page, "_logs_render_pending", False) is False
