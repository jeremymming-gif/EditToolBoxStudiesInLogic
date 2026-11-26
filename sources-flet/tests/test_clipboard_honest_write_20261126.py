"""剪贴板诚实写入：Finder/AppTranslocation 下 `pbcopy` 假成功修复（2026-11-26）。

用户报告（m03042 / m03130 / m03217）：从 Finder 双击启动打包版 macOS app 后点
「复制任务书全文」，看到「已复制任务书全文」，但系统剪贴板是空的（`pbpaste`
0 字节）。已证实的根因：

* `main.App.copy_text` 只看 `pbcopy` 的 `returncode == 0` 就报成功；在
  `com.apple.quarantine` → AppTranslocation 启动环境里 `pbcopy` 退出 0 却
  没有真正写系统 pasteboard。
* `bib_ai_assist._copy` 的 Flet 兜底依赖 `page.set_clipboard`，而 flet 1.0.5
  的 `ft.Page` **没有**该属性——死代码。真正可靠的是客户端 `ft.Clipboard`
  服务的 `set` + `get` 回读（对照 `ui/bib_advanced.py:612-637`）。

本文件是 TDD 的 RED 锁：实现前 `App.copy_text` 不做回读（假成功），
`BibAiAssistView` 也没有 `_acopy`（复制任务书走同步 `_copy`）。

只 monkeypatch 进程内的 `subprocess.run` 与 `ft.Clipboard` 工厂：
不启动 GUI、不碰真实剪贴板、不调引擎、不写用户文件。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import main
from ui import bib_ai_assist as A

# 复用既有 R3 任务书复制测试的假宿主/假桥（只 import，不改那个文件）。
from test_taskbook_copy_delivery_20261118 import (
    STALE_TEXT,
    _Bridge,
    _Host,
    _payload,
    _patch_reader,
    _view,
)

#: 写入命令按平台（Windows 是 `clip`，macOS 是 `pbcopy`）。
COPY_EXE = "clip" if main.os.name == "nt" else "pbcopy"

R3_TEXT = (
    "标题：R3 剪贴板回读任务书\n\n"
    "## 文献核实\n"
    "- 可用材料：待核条目 3 条，正文引用缺条 0 条，必要依赖缺条 0 条。\n\n"
    "## 回传格式与自检\n"
)
assert R3_TEXT != STALE_TEXT


# ── 假壳：只记 toast / marker（与 test_bib_shell_label 的 _clip_app 同款）──────
def _clip_app():
    app = main.App.__new__(main.App)
    app.toasts: list[str] = []
    app.marked: list = []
    app.toast = lambda message: app.toasts.append(message)
    app.mark = lambda event, **fields: app.marked.append((event, fields))
    return app


def _run_stub(execs: list, *, paste_stdout: bytes = b"", paste_exc=None):
    """按 argv 分派的假 `subprocess.run`：pbcopy/clip 成功，pbpaste 由测试指定。

    真实 `capture_output=True` 的完成对象一定带 bytes `stdout`；这里如实建模，
    这样产品侧的回读逻辑在生产形态下一定被触发。
    """

    def run(argv, **kwargs):
        execs.append((list(argv), kwargs.get("input")))
        if argv[0] == COPY_EXE:
            return SimpleNamespace(returncode=0, stdout=b"")
        if argv[0] == "pbpaste":
            if paste_exc is not None:
                raise paste_exc
            return SimpleNamespace(returncode=0, stdout=paste_stdout)
        raise AssertionError(f"未预期的子进程命令：{argv!r}")

    return run


def _marks(app, name: str) -> list:
    return [fields for event, fields in app.marked if event == name]


# ── A. App.copy_text：rc==0 不够，必须 pbpaste 回读 ─────────────────────────
@pytest.mark.skipif(main.os.name == "nt", reason="Windows 保留 clip rc 判据（无 pbpaste）")
def test_copy_text_rc0_but_empty_pasteboard_is_failure(monkeypatch):
    """pbcopy rc==0 但剪贴板实际为空（Finder/AppTranslocation 假成功）→ 必须 False。"""
    app = _clip_app()
    execs: list = []
    monkeypatch.setattr(main.subprocess, "run",
                        _run_stub(execs, paste_stdout=b""))

    assert app.copy_text("任务书全文") is False
    assert app.toasts == ["复制失败"], app.toasts
    assert _marks(app, "copy_path") == [], "失败时不得写成功 marker"
    # 写命令确实执行过（证明失败点在回读，不是提前 return）。
    assert [argv[0] for argv, _ in execs] == [COPY_EXE, "pbpaste"]


@pytest.mark.skipif(main.os.name == "nt", reason="Windows 保留 clip rc 判据（无 pbpaste）")
def test_copy_text_rc0_and_matching_pasteboard_is_success(monkeypatch):
    app = _clip_app()
    body = "任务书全文\n第二行：@article{ok2026}"
    execs: list = []
    monkeypatch.setattr(main.subprocess, "run",
                        _run_stub(execs, paste_stdout=body.encode("utf-8")))

    assert app.copy_text(body) is True
    assert app.toasts == ["已复制"], app.toasts
    assert _marks(app, "copy_path")[0]["chars"] == len(body)
    # pbcopy 收到的必须正是提交的字节（默认去首尾空白后的文本）。
    pbcopy_input = [payload for argv, payload in execs if argv[0] == COPY_EXE]
    assert pbcopy_input == [body.encode("utf-8")]


@pytest.mark.skipif(main.os.name == "nt", reason="Windows 保留 clip rc 判据（无 pbpaste）")
def test_copy_text_preserve_whitespace_submits_and_verifies_exact_bytes(monkeypatch):
    app = _clip_app()
    body = "\n  任务书\r\n\n"
    execs: list = []
    monkeypatch.setattr(main.subprocess, "run",
                        _run_stub(execs, paste_stdout=body.encode("utf-8")))

    assert app.copy_text(body, preserve_whitespace=True) is True
    pbcopy_input = [payload for argv, payload in execs if argv[0] == COPY_EXE]
    # preserve_whitespace=True：一个字节都不许动（不去空白、不归一换行）。
    assert pbcopy_input == [body.encode("utf-8")]


@pytest.mark.skipif(main.os.name == "nt", reason="Windows 保留 clip rc 判据（无 pbpaste）")
def test_copy_text_rejects_readback_with_extra_newline(monkeypatch):
    """回读必须与提交字节**完全相等**：多一个换行也算没写对。"""
    app = _clip_app()
    body = "任务书全文"
    monkeypatch.setattr(main.subprocess, "run",
                        _run_stub([], paste_stdout=body.encode("utf-8") + b"\n"))

    assert app.copy_text(body) is False
    assert app.toasts == ["复制失败"]
    assert _marks(app, "copy_path") == []


@pytest.mark.skipif(main.os.name == "nt", reason="Windows 保留 clip rc 判据（无 pbpaste）")
def test_copy_text_readback_error_is_failure(monkeypatch):
    """pbpaste 起不来/超时 → 拿不到证据，必须如实报失败。"""
    app = _clip_app()
    monkeypatch.setattr(main.subprocess, "run",
                        _run_stub([], paste_exc=OSError("pbpaste missing")))

    assert app.copy_text("任务书全文") is False
    assert app.toasts == ["复制失败"]
    assert _marks(app, "copy_path") == []


# ── B. BibAiAssistView._acopy：Flet Clipboard 回读优先 ──────────────────────
class _FakeClipboard:
    """假 `ft.Clipboard` 服务：`set` 记录、`get` 回读；可注入失败模式。"""

    _MISSING = object()

    def __init__(self, *, set_exc=None, get_exc=None, echo_value=_MISSING):
        self.set_exc = set_exc
        self.get_exc = get_exc
        self.echo_value = echo_value
        self.written = ""
        self.set_calls: list[str] = []
        self.get_calls = 0

    async def set(self, value):
        if self.set_exc is not None:
            raise self.set_exc
        self.set_calls.append(str(value))
        self.written = str(value)

    async def get(self):
        self.get_calls += 1
        if self.get_exc is not None:
            raise self.get_exc
        if self.echo_value is not self._MISSING:
            return self.echo_value
        return self.written


def _patch_clipboard(monkeypatch, service: _FakeClipboard) -> None:
    """让 `A.ft.Clipboard()` 返回给定假服务（仅本测试生效）。"""
    monkeypatch.setattr(A.ft, "Clipboard", lambda *args, **kwargs: service)


def test_acopy_uses_verified_flet_clipboard_when_host_lies(monkeypatch):
    """宿主 `copy_text` 返回 True 却什么都没写：Flet 回读成功就返回 True 且不碰宿主。"""
    host = _Host(copy_mode="false")
    host.page.services = []
    lied: list = []
    host.app.copy_text = lambda *args, **kwargs: lied.append(args) or True
    service = _FakeClipboard()
    _patch_clipboard(monkeypatch, service)
    view, _, _ = _view(host=host)

    assert asyncio.run(view._acopy("任务书全文", exact=True)) is True
    assert service.set_calls == ["任务书全文"]
    assert service.written == "任务书全文"
    assert lied == [], "Flet 回读成功时不该调用（可能说谎的）宿主 copy_text"


def test_acopy_honest_failure_when_flet_raises_and_host_returns_false(monkeypatch):
    """Flet 服务抛异常 + 宿主 copy_text 返回 False → 必须 False。"""
    host = _Host(copy_mode="false")
    host.page.services = []
    service = _FakeClipboard(set_exc=OSError("flet clipboard unavailable"))
    _patch_clipboard(monkeypatch, service)
    view, _, _ = _view(host=host)

    assert asyncio.run(view._acopy("任务书全文", exact=True)) is False


def test_acopy_without_flet_services_falls_back_to_sync_copy(monkeypatch):
    """没有 `page.services`（旧测试桩/受限宿主）→ 优雅回落同步 `_copy`，不崩。"""
    host = _Host(copy_mode="ok")
    view, _, _ = _view(host=host)

    assert asyncio.run(view._acopy("任务书全文", exact=True)) is True
    assert host.copied == ["任务书全文"]


# ── C. copy_task_text 端到端：假成功环境里也必须真复制 ─────────────────────
def _r3_view(host: _Host):
    view = A.BibAiAssistView(host, bridge=_Bridge(), purpose="library",
                             taskbook_version=3)
    view.work_dir = "/tmp/wd"
    view.set_work_dir("/tmp/wd")
    view.task_id = "T-7"
    view.task_text = STALE_TEXT
    return view


def test_copy_task_text_uses_verified_flet_clipboard_end_to_end(monkeypatch):
    """pbcopy 撒谎的环境：Flet 回读成功 → 必须发「已复制任务书全文」。"""
    host = _Host(copy_mode="false")
    host.page.services = []
    lied: list = []
    host.app.copy_text = lambda *args, **kwargs: lied.append(args) or True
    service = _FakeClipboard()
    _patch_clipboard(monkeypatch, service)
    view = _r3_view(host)
    _patch_reader(monkeypatch, lambda call: _payload(R3_TEXT))

    assert asyncio.run(view.copy_task_text()) is True
    assert service.written == R3_TEXT
    assert lied == []
    assert any("已复制任务书全文" in note for note in host.notes), host.notes
    assert any(event == "bib_ai_copy_task" for event, _ in host.marks), host.marks


def test_copy_task_text_reports_unavailable_when_host_lies_and_flet_fails(monkeypatch):
    """宿主 copy_text 谎报 True 且 Flet 写失败（回读仍是旧值）→ 报「剪贴板不可用」。"""
    host = _Host(copy_mode="false")
    host.page.services = []
    lied: list = []
    host.app.copy_text = lambda *args, **kwargs: lied.append(args) or True
    service = _FakeClipboard(set_exc=OSError("flet clipboard unavailable"),
                             echo_value="")
    _patch_clipboard(monkeypatch, service)
    view = _r3_view(host)
    _patch_reader(monkeypatch, lambda call: _payload(R3_TEXT))

    assert asyncio.run(view.copy_task_text()) is False
    assert "剪贴板不可用" in str(view.error or ""), view.error
    assert lied, "宿主 copy_text 曾被调用（它返回 True 但没写）"
    joined = "\n".join(host.notes)
    assert "已复制任务书全文" not in joined, f"失败态出现成功文案：{host.notes!r}"
    assert not any(event == "bib_ai_copy_task" for event, _ in host.marks), host.marks


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
