"""R3 Task5（第 3 层）：BibAiAssistView 只读任务书内容桥复制 + 剪贴板诚实性。

TDD / RED：针对 `ui.bib_ai_assist.BibAiAssistView` 的锁定契约写失败测试。
当前产品源码是骨架（copy_task_text 仍是同步读 self.task_text、_copy 吞异常返回
None、无 request_seq / set_work_dir / set_task_id / paste_reply_text，
BRIDGE_NAMES 无 taskbook_call / deliver_call），本文件应当**导入成功但 FAIL**。

只用假宿主 + 假桥 + monkeypatch 掉 services.engine_bridge 的两个纯函数；
不 import Engine/**，不跑真实引擎，不碰真实剪贴板/用户配置/稿件。
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

# 视图最终会经 services.engine_bridge 调这两个纯函数；monkeypatch 模块属性即可接管。
from services import engine_bridge as EB
from ui import bib_ai_assist as A
from ui.tokens import Tokens


# ── 测试数据 ──────────────────────────────────────────────────────────────
DECOY_LINE = "诱饵日志行 LINE_ONLY_SENTINEL：任务书：/tmp/decoy.md"
FULL_TEXT = (
    "标题：Studies in Logic 卷 12\n\n"
    "@article{zhang2026, author={张三}, title={真实任务书}, year={2026}}\n\n"
    "规则 1：引用必须逐条核实。\n"
    "规则 2：源正文永不回写。\n"
    "规则 3：交付只经只读内容桥取回全文。\n"
)
LATE_TEXT = (
    "【新任务 T-NEW 的全文】\n"
    "@book{new2026, author={李四}, title={切换后的任务书}, year={2026}}\n"
    "新规则：只认新代次，旧结果一律丢弃。\n"
)
# LATE_TEXT 刻意与 FULL_TEXT 不互为子串：迟到结果必须清晰可辨、不许混入旧全文。
assert not (LATE_TEXT in FULL_TEXT or FULL_TEXT in LATE_TEXT)
STALE_TEXT = "陈旧 self.task_text：不应被复制"
FAKE_PATH = "/tmp/real/taskbook.md"


# ── 假宿主（照抄 tests/test_bib_ai_assist_c_20261117.py 的 _FakeStore/_FakePage/_Host）
class _FakeStore:
    def __init__(self) -> None:
        self.data: dict = {}

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


class _FakePage:
    def __init__(self, *, clipboard_mode: str = "ok",
                 with_get_clipboard: bool = True) -> None:
        self.on_keyboard_event = None
        self.dialogs: list = []
        self.tasks: list = []
        self.width = 1160
        self.clipboard = ""
        self._clipboard_mode = clipboard_mode
        self.get_clipboard_text = ""
        self.get_clipboard = (self._get_clipboard if with_get_clipboard else None)

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)

    def set_clipboard(self, text) -> None:
        if self._clipboard_mode == "raise":
            raise OSError("flet clipboard unavailable")
        self.clipboard = str(text)

    def _get_clipboard(self) -> str:
        if self._clipboard_mode == "raise":
            raise OSError("flet clipboard unavailable")
        return str(self.get_clipboard_text)


class _Host:
    """`copy_mode`: ok / false / raise / missing；paste_text 为可调用或 None。"""

    def __init__(self, *, copy_mode: str = "ok", paste_text=None,
                 clipboard_mode: str = "ok", with_get_clipboard: bool = True) -> None:
        self.t = Tokens("light")
        self.page = _FakePage(clipboard_mode=clipboard_mode,
                              with_get_clipboard=with_get_clipboard)
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl"}
        self.view = ""
        self.marks: list = []
        self.notes: list = []
        self.copied: list = []
        self._copy_mode = copy_mode
        app_fields = {
            "store": _FakeStore(), "notes_repo": "", "window_w": 1160,
        }
        if copy_mode != "missing":
            app_fields["copy_text"] = self._copy_dispatch
        if paste_text is not None:
            app_fields["paste_text"] = paste_text
        self.app = SimpleNamespace(**app_fields)

    def _copy_dispatch(self, text):
        if self._copy_mode == "false":
            return False
        if self._copy_mode == "raise":
            raise OSError("pbcopy missing")
        self.copied.append(str(text))
        self.page.clipboard = str(text)
        return True

    def mark(self, event, **kwargs) -> None:
        self.marks.append((event, kwargs))

    def notify(self, text) -> None:
        self.notes.append(str(text))

    def _render(self) -> None:
        return None

    def set_view(self, view) -> None:
        self.view = view


class _Bridge:
    """假引擎桥：提供 taskbook_call / import_call / prepare_call 等，记账所有调用。"""

    def __init__(self, *, code: int = 0, task_text: str = "TASK") -> None:
        self.code = code
        self.task_text = task_text
        self.calls: list = []
        self.ran: list = []

    def _call(self, name, kwargs):
        self.calls.append((name, dict(kwargs)))
        return SimpleNamespace(
            entry="convert.py", argv=["fake-engine", name], label=name,
            display=f"convert.py {name}", task_text=self.task_text,
            task_id="T-42", preview_id="pv-1", blocked=[], keys=7,
            entries=3, backup="b.bak")

    def prepare_call(self, **kwargs):
        return self._call("prepare", kwargs)

    def import_call(self, **kwargs):
        return self._call("import", kwargs)

    def preview_call(self, **kwargs):
        return self._call("preview", kwargs)

    def apply_call(self, **kwargs):
        return self._call("apply", kwargs)

    def undo_call(self, **kwargs):
        return self._call("undo", kwargs)

    def status_call(self, **kwargs):
        return self._call("status", kwargs)

    def request_call(self, **kwargs):
        return self._call("request", kwargs)

    def taskbook_call(self, **kwargs):
        return self._call("taskbook", kwargs)

    def deliver_call(self, **kwargs):
        return self._call("deliver", kwargs)

    async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                       stdin_text=None, **kwargs):
        self.ran.append(call)
        if on_line is not None:
            on_line("phase=ok")
        if on_proc is not None:
            on_proc(object())
        return self.code


def _view(host=None, bridge=None):
    host = host if host is not None else _Host()
    bridge = bridge if bridge is not None else _Bridge()
    return A.BibAiAssistView(host, bridge=bridge), host, bridge


def _ready_view(**host_kwargs):
    """构造已定位工作目录/任务的视图；skeleton 无 set_work_dir 时此处即 RED。"""
    host = _Host(**host_kwargs)
    view, _, bridge = _view(host=host)
    view.work_dir = "/tmp/wd"
    view.set_work_dir("/tmp/wd")
    view.task_id = "T-7"
    view.task_text = STALE_TEXT
    return view, host, bridge


def _payload(text: str, *, ok: bool = True, path: str = FAKE_PATH) -> dict:
    """read_json_command 的假返回值：JSON 才是 payload，lines/stderr 全是诱饵。"""
    return {
        "ok": True,
        "json": {
            "ok": ok, "task_id": "T-7", "template_version": "v3",
            "path": path, "sha256": "abc123", "text": text, "chars": len(text),
        },
        "lines": [f"任务书：{path}", DECOY_LINE],
        "stderr": DECOY_LINE,
    }


def _fields(result: dict) -> dict:
    """taskbook_result_fields 的假实现：只从 json 取字段，绝不读 lines/stderr。"""
    payload = result.get("json") or {}
    text = str(payload.get("text", "") or "")
    return {
        "ok": bool(payload.get("ok")),
        "task_id": str(payload.get("task_id", "") or ""),
        "template_version": str(payload.get("template_version", "") or ""),
        "path": str(payload.get("path", "") or ""),
        "sha256": str(payload.get("sha256", "") or ""),
        "text": text,
        "chars": len(text),
    }


def _patch_reader(monkeypatch, reader) -> None:
    monkeypatch.setattr(EB, "read_json_command", reader)
    monkeypatch.setattr(EB, "taskbook_result_fields", _fields)


def _no_success(host) -> None:
    joined = "\n".join(host.notes)
    assert "已复制任务书全文" not in joined, f"失败态出现成功文案：{host.notes!r}"
    assert "已复制" not in joined, f"失败态出现复制成功文案：{host.notes!r}"


def _explains_failure(view, host) -> None:
    assert (str(getattr(view, "error", "") or "").strip()
            or any(any(word in note for word in ("失败", "无法", "未能", "未变"))
                   for note in host.notes)), (
        f"失败必须给出解释（error 或失败 notify），实际 error={view.error!r} notes={host.notes!r}")


# ── 1. 桥名契约 ───────────────────────────────────────────────────────────
def test_bridge_names_expose_taskbook_and_deliver_call():
    assert A.BRIDGE_NAMES["taskbook_call"] == "ai_taskbook_call"
    assert A.BRIDGE_NAMES["deliver_call"] == "ai_deliver_call"


# ── 2. 请求代次与缓存初值 ─────────────────────────────────────────────────
def test_view_initializes_request_seq_and_taskbook_cache():
    view, _, _ = _view()
    assert view.request_seq == 0
    assert view._taskbook_cache is None


def test_setters_bump_request_seq_and_clear_cache():
    view, _, _ = _view()
    view._taskbook_cache = {"stale": True}
    view.set_work_dir("/tmp/new-wd")
    assert view.request_seq == 1
    assert view._taskbook_cache is None

    view._taskbook_cache = {"stale": True}
    view.set_task_id("T-99")
    assert view.request_seq == 2
    assert view._taskbook_cache is None


def test_prepare_reopen_and_task_switch_bump_request_seq():
    """prepare（重开任务）与切 task_id 都必须让旧异步结果失效（代次递增 + 清缓存）。"""
    view, host, bridge = _view()
    view.target_bib = "/tmp/lib/target.bib"
    asyncio.run(view.prepare())
    seq_after_first = view.request_seq
    assert seq_after_first > 0, "prepare 后 request_seq 必须递增（旧异步结果作废）"
    assert view._taskbook_cache is None

    view._taskbook_cache = {"stale": True}
    asyncio.run(view.prepare())  # 重开任务
    assert view.request_seq > seq_after_first
    assert view._taskbook_cache is None

    view._taskbook_cache = {"stale": True}
    seq_before_switch = view.request_seq
    view.set_task_id("T-100")
    assert view.request_seq > seq_before_switch
    assert view._taskbook_cache is None


# ── 3. _copy 诚实性 ───────────────────────────────────────────────────────
def test_copy_host_success_returns_true():
    host = _Host(copy_mode="ok")
    view, _, _ = _view(host=host)
    assert view._copy(FULL_TEXT) is True
    assert host.copied == [FULL_TEXT]
    _no_success(host)  # _copy 本身不该擅自报成功


def test_copy_host_raises_without_flet_fallback_returns_false():
    """host.copy_text 抛异常，且 Flet 剪贴板不可用 → 必须返回 False。"""
    host = _Host(copy_mode="raise", clipboard_mode="raise")
    view, _, _ = _view(host=host)
    assert view._copy(FULL_TEXT) is False
    assert host.copied == []
    assert host.page.clipboard == ""
    _no_success(host)


def test_copy_flet_set_clipboard_only_returns_true():
    host = _Host(copy_mode="missing", clipboard_mode="ok")
    view, _, _ = _view(host=host)
    assert view._copy(FULL_TEXT) is True
    assert host.page.clipboard == FULL_TEXT
    assert host.copied == []


def test_copy_both_clipboard_paths_fail_returns_false():
    host = _Host(copy_mode="raise", clipboard_mode="raise")
    view, _, _ = _view(host=host)
    assert view._copy(FULL_TEXT) is False
    assert host.copied == []
    assert host.page.clipboard == ""
    _no_success(host)


# ── 4. copy_task_text：内容来自引擎真实全文 ────────────────────────────────
def test_copy_task_text_copies_engine_full_text_not_stale_or_path(monkeypatch):
    view, host, bridge = _ready_view()
    _patch_reader(monkeypatch, lambda call: _payload(FULL_TEXT))

    assert asyncio.run(view.copy_task_text()) is True
    assert host.copied == [FULL_TEXT]
    assert host.page.clipboard == FULL_TEXT
    assert DECOY_LINE not in "".join(host.copied)
    assert FAKE_PATH not in host.copied
    assert STALE_TEXT not in host.copied
    assert any("已复制任务书全文" in note for note in host.notes), host.notes
    # 只读调用：经 taskbook_call 构造，带工作目录/任务 id，不写稿件。
    assert any(name == "taskbook" for name, _ in bridge.calls), bridge.calls
    taskbook_kwargs = [kw for name, kw in bridge.calls if name == "taskbook"][-1]
    # 原断言："/tmp/wd" in str(work_dir)。Windows 下产品经 abspath 补盘符 -> D:\tmp\wd。
    # 改为按路径分量（去盘符锚点）精确比较：既跨平台，又比原来的子串判断更严格——
    # 要求 work_dir 恰好指向 tmp/wd 这个目录，而不是仅包含该片段。
    work_dir = Path(str(taskbook_kwargs.get("work_dir", "")))
    assert (work_dir.parts[1:] if work_dir.anchor else work_dir.parts) == ("tmp", "wd")
    assert str(taskbook_kwargs.get("task_id", "")) == "T-7"


def test_copy_task_text_ok_false_does_not_copy(monkeypatch):
    view, host, _ = _ready_view()
    _patch_reader(monkeypatch, lambda call: _payload("不该复制的正文", ok=False, path=""))

    assert asyncio.run(view.copy_task_text()) is False
    assert host.copied == []
    assert host.page.clipboard == ""
    _no_success(host)
    _explains_failure(view, host)


def test_copy_task_text_empty_text_does_not_copy(monkeypatch):
    view, host, _ = _ready_view()
    _patch_reader(monkeypatch, lambda call: _payload("   \n  "))

    assert asyncio.run(view.copy_task_text()) is False
    assert host.copied == []
    assert host.page.clipboard == ""
    _no_success(host)
    _explains_failure(view, host)


def test_copy_task_text_copy_failure_does_not_notify_success(monkeypatch):
    view, host, _ = _ready_view(copy_mode="raise", clipboard_mode="raise")
    _patch_reader(monkeypatch, lambda call: _payload(FULL_TEXT))

    assert asyncio.run(view.copy_task_text()) is False
    assert host.copied == []
    _no_success(host)
    _explains_failure(view, host)


# ── 5. 竞态：旧代次的异步结果不得复制 ─────────────────────────────────────
def test_copy_task_text_stale_request_during_call_does_not_copy(monkeypatch):
    view, host, _ = _ready_view()

    def reader(call):
        view.set_work_dir("/tmp/other")  # 调用期间工作目录变了 → 本次结果作废
        return _payload(FULL_TEXT)

    _patch_reader(monkeypatch, reader)

    assert asyncio.run(view.copy_task_text()) is False
    assert host.copied == []
    assert host.page.clipboard == ""
    _no_success(host)
    # 竞态作废只需“不复制、不报成功”：是否附带说明文案由实现自定，不强求。


def test_copy_task_text_rapid_reclick_late_first_does_not_copy(monkeypatch):
    """第一次点击的引擎结果迟到时，不得覆盖第二次（新代次）的真实全文。"""
    view, host, _ = _ready_view()
    started = threading.Event()
    release = threading.Event()

    def reader(call):
        if not started.is_set():
            started.set()          # 标记第一次调用已进入 reader
            release.wait(3)        # 故意卡住，直到第二次调用完成才返回
            return _payload(FULL_TEXT)
        return _payload(LATE_TEXT)

    _patch_reader(monkeypatch, reader)

    async def scenario():
        first = asyncio.create_task(view.copy_task_text())
        await asyncio.to_thread(started.wait, 3)
        view.set_task_id("T-NEW")  # 第二次点击前任务已切换 → 两次捕获不同 seq
        second = asyncio.create_task(view.copy_task_text())
        second_result = await second
        release.set()
        first_result = await first
        return first_result, second_result

    first_result, second_result = asyncio.run(scenario())
    assert first_result is False
    assert second_result is True
    assert host.copied == [LATE_TEXT]
    assert host.page.clipboard == LATE_TEXT
    assert FULL_TEXT not in "".join(host.copied)


# ── 6. 粘贴接真实剪贴板；导入仍走引擎 ─────────────────────────────────────
def test_paste_reply_text_reads_host_clipboard():
    pasted = "AI 的真实回复正文\n@article{ok2026, ...}"
    host = _Host(paste_text=lambda: pasted)
    view, _, _ = _view(host=host)
    assert view.paste_reply_text() == pasted


def test_paste_reply_text_falls_back_to_flet_clipboard():
    pasted = "来自 Flet 剪贴板的回复"
    host = _Host()
    host.page.get_clipboard_text = pasted
    view, _, _ = _view(host=host)
    assert view.paste_reply_text() == pasted


def test_paste_reply_text_no_service_returns_empty():
    host = _Host(with_get_clipboard=False)
    view, _, _ = _view(host=host)
    assert view.paste_reply_text() == ""


def test_import_uses_pasted_body_text_and_still_calls_import_call():
    pasted = "@article{new2026, title={模型回的正文}}"
    host = _Host(paste_text=lambda: pasted)
    view, _, bridge = _view(host=host)
    view.body_text = view.paste_reply_text()
    assert view.body_text == pasted

    asyncio.run(view.import_result())

    assert any(name == "import" for name, _ in bridge.calls), bridge.calls
    import_kwargs = [kw for name, kw in bridge.calls if name == "import"][-1]
    assert import_kwargs.get("body_text") == pasted
    assert import_kwargs.get("result_text") == pasted
    assert bridge.ran, "粘贴正文后仍必须经 run_call 走引擎校验"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_copy_host_false_gates_success(monkeypatch):
    view, host, _ = _ready_view(copy_mode="false", clipboard_mode="raise")
    _patch_reader(monkeypatch, lambda call: _payload(FULL_TEXT))
    assert asyncio.run(view.copy_task_text()) is False
    assert host.copied == []
    assert host.page.clipboard == ""
    _no_success(host)


def test_app_exact_copy_preserves_bytes_and_default_still_trims(monkeypatch):
    import main

    body = "\n  任务书\r\n\n"
    sent = []
    monkeypatch.setattr(main.subprocess, "run",
                        lambda argv, **kwargs: sent.append(kwargs["input"]) or
                        SimpleNamespace(returncode=0))
    host = SimpleNamespace(toast=lambda text: None, mark=lambda *args, **kwargs: None)
    assert main.App.copy_text(host, body, preserve_whitespace=True)
    assert main.App.copy_text(host, body)
    encoding = "utf-16le" if main.os.name == "nt" else "utf-8"
    assert sent == [body.encode(encoding), body.strip().encode(encoding)]
