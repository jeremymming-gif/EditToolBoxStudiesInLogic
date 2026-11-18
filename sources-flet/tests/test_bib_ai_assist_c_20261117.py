"""Bib-AI 视图（`app/ui/bib_ai_assist.py`）的视图层测试。

全部用假宿主 + 假引擎桥：**不碰真实稿件、不跑引擎、不写用户文件**。
断言口径来自 DESIGN §3.2 / §4 / §9 / §10：

* 执行范围与采纳范围是两组**不同控件对象**，文字纠错在两组的默认值都是 False；
* 切用途只改文案，切通道才改控件（Chat 有「复制任务书全文」，API 有费用提示）；
* 引擎桥缺函数 → 「引擎桥未就绪」横幅，且**没有任何成功文案**；
* 引擎返回非 0 → 失败文案，且**不通知成功**；
* 「粘贴正文」与「粘贴路径」是两组不同控件，互不冒充；
* 有阻断项 → 阻断横幅，且「应用」保持禁用。
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from ui import bib_ai_assist as A
from ui.tokens import Tokens


# ── 假宿主（照抄 tests/test_b116_bib_normalize.py 的写法）──────────────────
class _FakeStore:
    def __init__(self) -> None:
        self.data: dict = {}

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


class _FakePage:
    def __init__(self) -> None:
        self.on_keyboard_event = None
        self.dialogs: list = []
        self.tasks: list = []
        self.width = 1160
        self.clipboard = ""

    def show_dialog(self, dialog) -> None:
        self.dialogs.append(dialog)

    def pop_dialog(self) -> None:
        self.dialogs.pop()

    def run_task(self, fn) -> None:
        self.tasks.append(fn)

    def set_clipboard(self, text) -> None:
        self.clipboard = text


class _Host:
    def __init__(self) -> None:
        self.t = Tokens("light")
        self.page = _FakePage()
        self.app = SimpleNamespace(
            store=_FakeStore(), notes_repo="", window_w=1160,
            copy_text=self._copy_text)
        self.stats_data = {"db": "/tmp/lib/bibliography.jsonl"}
        self.view = ""
        self.marks: list = []
        self.notes: list = []
        self.copied: list = []

    def _copy_text(self, text) -> None:
        self.copied.append(text)

    def mark(self, event, **kwargs) -> None:
        self.marks.append((event, kwargs))

    def notify(self, text) -> None:
        self.notes.append(str(text))

    def _render(self) -> None:
        return None

    def set_view(self, view) -> None:
        self.view = view


class _Bridge:
    """假引擎桥：7 个语义齐全，`code` 控制退出码，可选择性缺函数。"""

    def __init__(self, *, code: int = 0, missing=(), preview_id="pv-1",
                 blocked=(), keys=7, entries=3, task_text="TASK", backup="b.bak"):
        self.code = code
        self.preview_id = preview_id
        self.blocked = list(blocked)
        self.keys = keys
        self.entries = entries
        self.task_text = task_text
        self.backup = backup
        self.calls: list = []
        self.ran: list = []
        for name in missing:
            object.__setattr__(self, name, None)

    def _call(self, name, kwargs):
        self.calls.append((name, dict(kwargs)))
        return SimpleNamespace(
            entry="engine.py", argv=[name], label=name, display=f"engine.py {name}",
            task_text=self.task_text, task_id="T-42", preview_id=self.preview_id,
            blocked=list(self.blocked), keys=self.keys, entries=self.entries,
            backup=self.backup)

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

    def request_call(self, **kwargs):
        # `request` 只在 API 通道用到，因此不在 BRIDGE_FUNCS 的必备集里；
        # 假桥照样提供它，才能测「API 通道成功/失败」两条路。
        return self._call("request", kwargs)

    def status_call(self, work_dir=None):
        return self._call("status", {"work_dir": work_dir})

    async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                       stdin_text=None):
        self.ran.append(call)
        for line in ("phase=preview", "task_id=T-42", "keys=7", "entries=3"):
            if on_line is not None:
                on_line(line)
        if on_proc is not None:
            on_proc(object())
        return self.code


def _walk(control) -> list:
    """递归收集控件：content / controls / actions。"""
    out = [control]
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        out.extend(_walk(content))
    for child in getattr(control, "controls", None) or ():
        out.extend(_walk(child))
    for child in getattr(control, "actions", None) or ():
        out.extend(_walk(child))
    return out


def _texts(control) -> list[str]:
    """收集可见文案：.value / .label / .content(字符串)。"""
    out: list[str] = []
    for node in _walk(control):
        for attr in ("value", "label"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                out.append(value)
        content = getattr(node, "content", None)
        if isinstance(content, str):
            out.append(content)
        text = getattr(node, "text", None)
        if isinstance(text, str):
            out.append(text)
    return out


def _blob(control) -> str:
    return "\n".join(_texts(control))


def _view(bridge=None) -> tuple[A.BibAiAssistView, _Host]:
    host = _Host()
    return A.BibAiAssistView(host, bridge=bridge if bridge is not None else _Bridge()), host


SUCCESS_WORDS = ("成功", "已应用", "全部核实", "全绿", "已写入")


def _no_success(text: str) -> None:
    for word in SUCCESS_WORDS:
        assert word not in text, f"失败/未就绪态不该出现成功文案：{word!r} in {text!r}"


# ── 1. 两组勾选的默认值与独立性（DESIGN §4）──────────────────────────────
def test_scope_defaults_match_design():
    view, _ = _view()
    view.build()
    assert view.execute == {"verify": True, "proofcheck": True, "textfix": False}
    assert view.accept == {"verify": True, "proofcheck": True, "textfix": False}
    # 内部键 → 引擎类别名（`bib_verify/typeset/text`，见 CATEGORY_MAP / 问题 4 M2）
    assert view.execute_scope() == ["bib_verify", "typeset"]
    assert view.accept_scope() == ["bib_verify", "typeset"]
    assert view.accept["textfix"] is False


def test_scope_groups_are_distinct_controls():
    view, _ = _view()
    view.build()
    assert set(view._execute_boxes) == {"verify", "proofcheck", "textfix"}
    assert set(view._accept_boxes) == {"verify", "proofcheck", "textfix"}
    for key in ("verify", "proofcheck", "textfix"):
        assert view._execute_boxes[key] is not view._accept_boxes[key]
        assert isinstance(view._execute_boxes[key], ft.Checkbox)
        assert isinstance(view._accept_boxes[key], ft.Checkbox)
    # 默认值也要分别落在控件上
    assert view._execute_boxes["textfix"].value is False
    assert view._accept_boxes["textfix"].value is False


def test_execute_change_does_not_touch_accept():
    view, _ = _view()
    view.build()
    before = dict(view.accept)
    view.set_execute("verify", False)
    view.set_execute("textfix", True)
    assert view.accept == before, "改执行范围不该动采纳范围"
    view.build()
    assert view._accept_boxes["verify"].value is True
    assert view._accept_boxes["textfix"].value is False


def test_accept_change_does_not_touch_execute():
    view, _ = _view()
    view.build()
    before = dict(view.execute)
    view.set_accept("verify", False)
    assert view.execute == before
    assert view.accept_scope() == ["typeset"]


def test_accept_textfix_default_stays_false_after_reset():
    view, _ = _view()
    view.build()
    view.set_accept("textfix", True)
    assert view.accept["textfix"] is True
    view.reset_accept()
    assert view.accept["textfix"] is False
    assert view.accept_scope() == ["bib_verify", "typeset"]


def test_two_sections_are_rendered_separately():
    view, _ = _view()
    tree = view.build()
    blob = _blob(tree)
    assert "执行范围" in blob
    assert "采纳范围" in blob
    # 两组不是同一行拼在一起：文案出现在不同容器里
    assert blob.count("文献核实") >= 2, "两组各要有一个「文献核实」勾选"


# ── 2. 切用途 / 切通道（DESIGN §3.2 / §10）───────────────────────────────
def test_purpose_switch_changes_copy_not_flow():
    view, _ = _view()
    chat = _blob(view.build())
    assert "初排稿" in chat
    view.set_purpose("library")
    library = _blob(view.build())
    assert "初排稿" not in library
    assert "源 Bib" in library
    # 流程不变：六个核心动作仍在（`request` 只在 API 通道出现，见下一条）
    core = ("prepare", "import", "preview", "apply", "undo", "refresh")
    for key in core:
        assert A.ACTION_LABELS[key] in library, A.ACTION_LABELS[key]
    assert A.ACTION_LABELS["request"] not in library, "Chat 通道没有「直接请求（API）」"


def test_channel_api_has_request_entry_point():
    """API 通道只写「会产生费用」而不给按钮，等于这条路走不通（DESIGN §3.1）。"""
    view, _ = _view()
    view.set_channel("api")
    tree = view.build()
    buttons = [n for n in _walk(tree) if getattr(n, "_bib_ai_action", "") == "request"]
    assert len(buttons) == 1, "API 通道要有「直接请求（API）」按钮"
    assert buttons[0].content == "直接请求（API）"
    assert buttons[0].disabled is False
    # Chat 通道不摆这个按钮（避免与「复制任务书」混淆）
    view.set_channel("chat")
    chat_tree = view.build()
    assert not [n for n in _walk(chat_tree)
                if getattr(n, "_bib_ai_action", "") == "request"]


def test_request_api_fails_honestly_on_wrong_channel():
    """非 API 通道点它 → 明确失败文案，不发请求、不进任何成功态。"""
    bridge = _Bridge()
    view, _host = _view(bridge)
    view.set_channel("chat")
    asyncio.run(view.request_api())
    assert "先切到「直接调 API」通道" in view.error
    _no_success(view.notice or "")
    assert [name for name, _kw in bridge.calls] == [], "通道不对时不该发任何请求"


def test_request_api_requires_work_dir():
    """API 通道、既没工作目录也没目标 Bib → 如实拒绝（不猜路径、不静默跳过）。

    注意口径变化（问题 4 M1）：只要选了目标 Bib 就能派生工作区，这时不该再拦；
    拦的只是「两样都没有、真的没处落」的情形。
    """
    bridge = _Bridge()
    view, _host = _view(bridge)
    view.set_channel("api")
    view.work_dir = ""
    assert view.target_bib == "", "前置：目标 Bib 也没选"
    asyncio.run(view.request_api())
    assert "先选工作目录" in view.error
    assert [name for name, _kw in bridge.calls] == []


def test_request_api_accepts_derived_work_dir(tmp_path):
    """只选了目标 Bib（工作目录空）→ 用派生工作区直接请求，不再拦。"""
    bridge = _Bridge()
    view, _host = _view(bridge)
    view.set_channel("api")
    view.work_dir = ""
    view.target_bib = str(tmp_path / "2026年第5期(90)" / "reference.bib")
    asyncio.run(view.request_api())
    assert view.error == ""
    name, kwargs = bridge.calls[-1]
    assert name == "request"
    assert kwargs["work_dir"] == str(tmp_path / "2026年第5期(90)" / ".aiw")


def test_request_api_success_lands_on_waiting_not_preview():
    """成功只到「结果已落盘」，仍要 import/preview —— 不自动采纳（DESIGN §8）。"""
    bridge = _Bridge()
    view, _host = _view(bridge)
    view.set_channel("api")
    view.work_dir = "/tmp/ws"
    asyncio.run(view.request_api())
    assert view.error == ""
    assert view.phase == "waiting"
    assert view.can_apply() is False
    assert [name for name, _kw in bridge.calls] == ["request"]


def test_request_call_maps_to_real_bridge():
    """真桥的函数叫 `ai_request_call`；映射漏了就等于 API 通道永远「缺少 request_call」。"""
    assert A.BRIDGE_NAMES["request_call"] == "ai_request_call"


def test_channel_chat_shows_copy_button():
    view, _ = _view()
    view.build()
    assert view.channel == "chat"
    buttons = [n for n in _walk(view.build()) if getattr(n, "_bib_ai_copy_task", False)]
    assert buttons, "Chat 通道要有「复制任务书全文」"
    blob = _blob(view.build())
    assert "复制任务书全文" in blob
    assert "导入结果" in blob
    assert "费用" not in blob


def test_channel_api_shows_cost_notice():
    view, _ = _view()
    view.build()
    view.set_channel("api")
    tree = view.build()
    blob = _blob(tree)
    assert "可能产生费用" in blob
    assert not [n for n in _walk(tree) if getattr(n, "_bib_ai_copy_task", False)]
    assert view.channel == "api"


def test_purpose_switch_keeps_scopes():
    view, _ = _view()
    view.build()
    view.set_execute("proofcheck", False)
    view.set_accept("proofcheck", False)
    view.set_purpose("library")
    view.build()
    assert view.execute["proofcheck"] is False
    assert view.accept["proofcheck"] is False


# ── 3. 桥缺函数 → 如实报错，没有成功态 ───────────────────────────────────
def test_missing_bridge_functions_reported():
    bridge = _Bridge(missing=("prepare_call", "apply_call"))
    view, _ = _view(bridge)
    tree = view.build()
    blob = _blob(tree)
    assert "引擎桥未就绪" in blob
    assert "prepare_call" in blob
    _no_success(blob)


def test_missing_bridge_blocks_action_and_reports():
    bridge = _Bridge(missing=("prepare_call",))
    view, host = _view(bridge)
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.prepare())
    assert view._bridge_missing == ["prepare_call"]
    tree = view.build()
    blob = _blob(tree)
    assert "引擎桥未就绪" in blob
    assert "prepare_call" in blob
    _no_success(blob)
    assert not [note for note in host.notes if any(w in note for w in SUCCESS_WORDS)]
    assert not [event for event, _ in host.marks if event.endswith("_ok")]


def test_missing_run_call_reported():
    bridge = _Bridge(missing=("run_call",))
    view, _ = _view(bridge)
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.prepare())
    assert "run_call" in view._bridge_missing
    assert "引擎桥未就绪" in _blob(view.build())


def test_no_bridge_at_all_reports_every_missing(monkeypatch):
    # 真实引擎桥已经接齐 7 个 `ai_*` 函数，所以「取不到桥」必须显式模拟：
    # 让惰性加载返回 None（未安装/未接线时就是这个分支），而不是靠真桥缺失。
    monkeypatch.setattr(A, "_load_bridge", lambda: None)
    host = _Host()
    view = A.BibAiAssistView(host, bridge=None)
    view._bridge = None  # 强制走「取不到桥」的分支
    blob = _blob(view.build())
    assert "引擎桥未就绪" in blob
    _no_success(blob)


# ── 4. 引擎非 0 → 失败横幅，无成功 toast ────────────────────────────────
def test_nonzero_exit_shows_failure_without_success():
    bridge = _Bridge(code=3)
    view, host = _view(bridge)
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.prepare())
    assert view.error
    assert "失败" in view.error
    assert "3" in view.error
    tree = view.build()
    blob = _blob(tree)
    assert "失败" in blob
    _no_success(blob)
    assert view.phase not in ("applied", "preview")
    assert view.can_apply() is False
    assert view.can_undo() is False
    assert not [note for note in host.notes if any(w in note for w in SUCCESS_WORDS)]
    assert not [event for event, _ in host.marks if event.endswith("_ok")]


def test_nonzero_exit_on_preview_keeps_apply_disabled():
    bridge = _Bridge(code=1)
    view, _ = _view(bridge)
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.preview())
    assert "失败" in view.error
    assert view.preview_id == ""
    assert view.can_apply() is False
    assert view._action_buttons["apply"].disabled is True


def test_engine_exception_reported_as_failure():
    class _Boom(_Bridge):
        async def run_call(self, call, **kwargs):
            raise RuntimeError("engine crashed")

    view, host = _view(_Boom())
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.prepare())
    assert "失败" in view.error
    assert "engine crashed" in view.error
    _no_success(_blob(view.build()))
    assert not host.notes


# ── 5. 正文粘贴 vs 路径粘贴是两个不同控件（DESIGN §10 / N04）──────────────
def test_body_paste_is_not_path_paste():
    view, _ = _view()
    tree = view.build()
    field = view._paste_field
    assert isinstance(field, ft.TextField)
    assert field.multiline is True, "正文粘贴必须是多行输入"
    assert view._paste_button is not None
    assert view._paste_button._paste_field is field

    pickers = [n for n in _walk(tree)
               if isinstance(n, ft.TextField) and getattr(n, "_value_tooltip", False)]
    assert pickers, "路径输入要走 path_picker（带路径提示）"
    assert all(p is not field for p in pickers), "正文输入框不能同时充当路径输入"
    assert any("不是文件路径" in str(getattr(n, "hint_text", "") or "")
               for n in _walk(tree) if isinstance(n, ft.TextField))


def test_body_text_and_result_path_are_separate_state():
    view, _ = _view()
    view.build()
    view._on_body_change(SimpleNamespace(control=SimpleNamespace(value="AI 正文")))
    assert view.body_text == "AI 正文"
    assert view.result_path == ""
    view._on_path("result_path")(SimpleNamespace(
        control=SimpleNamespace(value="/tmp/ai.bib")))
    assert view.result_path == "/tmp/ai.bib"
    assert view.body_text == "AI 正文", "路径不能覆盖正文"


def test_path_picker_fields_registered():
    view, _ = _view()
    view.build()
    for key in ("source_path", "target_bib", "result_path", "work_dir"):
        assert view._refs[key], f"{key} 的输入框没登记进 _refs"
    assert len(view._refs["target_bib"]) == 1


# ── 6. 阻断项 → 阻断 banner + 应用禁用（DESIGN §9 / §10）──────────────────
def test_blocked_items_banner_and_apply_disabled():
    bridge = _Bridge(blocked=["缺 3 个 DOI", "2 条只报告未修"])
    view, _ = _view(bridge)
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.preview())
    assert view.blocked == ["缺 3 个 DOI", "2 条只报告未修"]
    tree = view.build()
    blob = _blob(tree)
    assert A.BLOCKED_TITLE in blob
    assert "缺 3 个 DOI" in blob
    assert "2 条只报告未修" in blob
    assert view.can_apply() is False
    assert view._action_buttons["apply"].disabled is True
    assert view.phase == "blocked"
    _no_success(blob)


def test_apply_refused_when_blocked():
    bridge = _Bridge(blocked=["x"])
    view, host = _view(bridge)
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.preview())
    asyncio.run(view.apply())
    assert "失败" in view.error
    assert not [call for call in bridge.ran if "apply" in str(getattr(call, "argv", ""))]
    assert view.phase != "applied"
    assert not [note for note in host.notes if any(w in note for w in SUCCESS_WORDS)]


# ── 正向流程（对照组：证明上面的「禁用」不是永远禁用）────────────────────
def test_happy_path_preview_then_apply_then_undo():
    bridge = _Bridge()
    view, host = _view(bridge)
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.prepare())
    assert view.phase == "waiting"
    assert view.scope_frozen is True
    assert view.task_text == "TASK"

    asyncio.run(view.preview())
    assert view.preview_id == "pv-1"
    assert view.phase == "preview"
    assert view.can_apply() is True
    view.build()  # 重画一次：按钮的 disabled 是画的时候定的
    assert view._action_buttons["apply"].disabled is False

    asyncio.run(view.apply())
    assert view.phase == "applied"
    assert view.backup == "b.bak"
    assert view.can_undo() is True

    asyncio.run(view.undo())
    assert view.phase == "stale"
    assert view.undone is True
    assert view.can_apply() is False


def test_accept_change_after_apply_requires_new_preview():
    view, _ = _view()
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.preview())
    asyncio.run(view.apply())
    assert view.phase == "applied"
    view.set_accept("proofcheck", False)
    assert view.phase == "stale"
    assert view.preview_id == ""
    assert view.can_apply() is False


def test_execute_scope_frozen_after_task_sheet():
    view, _ = _view()
    view.build()
    view.target_bib = "/tmp/target.bib"
    asyncio.run(view.prepare())
    view.build()
    assert view._execute_boxes["verify"].disabled is True
    assert view._accept_boxes["verify"].disabled is False


# ── 状态区 / 阶段（DESIGN §9）───────────────────────────────────────────
def test_status_block_shows_phase_task_and_counts():
    view, _ = _view()
    view.build()
    view.phase = "preview"
    view.task_id = "T-42"
    view.key_count = 7
    view.entry_count = 3
    blob = _blob(view.build())
    assert "可预览" in blob
    assert "T-42" in blob
    assert "引用键数" in blob and "条目数" in blob
    assert "7" in blob and "3" in blob


def test_phase_labels_cover_design_stages():
    assert A.phase_label("preparing") == "准备中"
    assert A.phase_label("waiting") == "等待 AI 结果"
    assert A.phase_label("preview") == "可预览"
    assert A.phase_label("applied") == "已应用"
    assert A.phase_label("blocked") == "阻断"
    assert A.phase_label("stale") == "已失效"


def test_status_refresh_parses_engine_output():
    bridge = _Bridge()
    view, _ = _view(bridge)
    view.build()
    asyncio.run(view.refresh_status())
    assert view.phase == "preview"
    assert view.task_id == "T-42"
    assert view.key_count == 7
    assert view.entry_count == 3


def test_parse_status_ignores_unknown_and_parses_blocked():
    out = A.parse_status(["phase=preview", "keys=9", "nonsense", "blocked=a；b; c"])
    assert out["phase"] == "preview"
    assert out["keys"] == 9
    assert out["blocked"] == ["a", "b", "c"]
    assert "nonsense" not in str(out)


# ── 模块级接口（供宿主接线）──────────────────────────────────────────────
def test_module_exports_for_host_wiring():
    assert A.VIEW_KEY == "bib_ai"
    assert A.TITLE == "AI 处理 Bib"
    assert A.ROLE == "readwrite"
    assert A.ROLE_CHIP in ("readonly", "write", "stale")
    # `request` = API 通道的入口（DESIGN §3.1「Chat/API 两条路都要能跑完」）
    assert set(A.ACTION_LABELS) == {
        "prepare", "request", "import", "preview", "apply", "undo", "refresh"}
    assert A.ACTION_LABELS["request"] == "直接请求（API）"
    assert set(A.BRIDGE_FUNCS) == {
        "prepare_call", "import_call", "preview_call", "apply_call",
        "undo_call", "status_call", "run_call"}


def test_view_exposes_host_hooks():
    view, _ = _view()
    for name in ("build", "activate", "refresh_running", "on_resize"):
        assert callable(getattr(view, name, None)), name
    assert view.header_actions() == []
    view.activate()
    view.refresh_running()
    view.on_resize()


def test_shell_metadata_present():
    view, _ = _view()
    tree = view.build()
    assert getattr(tree, "_bib_shell", None) is not None
    assert getattr(tree, "_bib_body", None) is not None
    assert getattr(tree, "_bib_dock", None) is not None


def test_page_does_not_render_cards_or_tables_in_body():
    view, _ = _view()
    tree = view.build()
    body = getattr(tree, "_bib_body", None)
    assert body is not None
    assert not any(isinstance(node, ft.Card) for node in _walk(body))


def test_bridge_names_map_to_prefixed_real_functions():
    """视图内部名与真实桥名是两套：`prepare_call` → `ai_prepare_call`。

    真实 `services.engine_bridge` 里已有 `ai_*_call`；同时 `ui/normalize.py`
    占用了裸名 `preview_call`，所以映射不是可选优化而是必须。
    """
    assert A.BRIDGE_NAMES["prepare_call"] == "ai_prepare_call"
    assert A.BRIDGE_NAMES["run_call"] == "run_call"  # run_call 没有前缀
    real = SimpleNamespace(ai_prepare_call=lambda **k: "p", run_call=lambda **k: 0)
    assert A.missing_bridge(real) == [
        "import_call", "preview_call", "apply_call", "undo_call", "status_call"]
    resolved = A._bridge_fn(real, "prepare_call")
    assert callable(resolved) and resolved() == "p"
    # 裸名仍兼容（假桥/旧桥）
    legacy = SimpleNamespace(prepare_call=lambda **k: "legacy")
    assert callable(A._bridge_fn(legacy, "prepare_call"))


def test_missing_bridge_helper():
    assert A.missing_bridge(None) == list(A.BRIDGE_FUNCS)
    partial = SimpleNamespace(run_call=lambda *a, **k: 0)
    assert A.missing_bridge(partial) == [
        "prepare_call", "import_call", "preview_call", "apply_call",
        "undo_call", "status_call"]
    full = _Bridge()
    assert A.missing_bridge(full) == []


def test_bridge_error_text_lists_names():
    text = A.bridge_error_text(["a", "b"])
    assert "引擎桥未就绪" in text
    assert "a" in text and "b" in text
    assert A.bridge_error_text([]) == ""


def test_call_kwargs_carry_scopes_and_paths():
    bridge = _Bridge()
    view, _ = _view(bridge)
    view.build()
    view.target_bib = "/tmp/target.bib"
    view.source_path = "/tmp/paper.tex"
    asyncio.run(view.prepare())
    name, kwargs = bridge.calls[0]
    assert name == "prepare"
    assert kwargs["target_bib"] == "/tmp/target.bib"
    assert kwargs["execute"] == ["bib_verify", "typeset"]
    assert kwargs["accept"] == ["bib_verify", "typeset"]
    assert "textfix" not in kwargs["execute"]
    assert "text" not in kwargs["execute"]


# ── 5. 问题 4 的修复钉子（M1 派生工作区 / M2 类别词表 / M3 引擎 stderr）────


def test_category_map_matches_engine_cats():
    """M2：视图内部键与引擎 `CATS` 是**两套**词表，必须逐项翻译。

    缺陷原状：视图把 `verify/proofcheck` 直接当 `--execute/--adopt` 的值，
    引擎 `_scope_of` 认不出这两串 → 「不认识的类别」整条退出 2 —— 修好 M1 之后
    这一层立刻暴露成第二个阻断。
    """
    assert set(A.CATEGORY_MAP) == {key for key, _label in A.CATEGORIES}
    assert A.CATEGORY_MAP == {"verify": "bib_verify", "proofcheck": "typeset",
                              "textfix": "text"}
    # 引擎是唯一真源：读源码里的 `CAT_* = "…"` 比对（不 import 引擎、不起子进程）。
    engine = (Path(__file__).resolve().parents[2] / "Engine" / "slcn" /
              "ai_workflow.py")
    cats = re.findall(r'^CAT_[A-Z_]+ = "([^"]+)"', engine.read_text(encoding="utf-8"),
                      re.M)
    assert set(A.CATEGORY_MAP.values()) == set(cats), (
        f"视图词表与引擎 CATS 不一致：{sorted(A.CATEGORY_MAP.values())} vs {cats}")


def test_work_dir_derived_from_target_bib_dir(tmp_path):
    """M1：界面从没给过工作目录 → 从目标 Bib 所在目录派生隐藏 `.aiw`。"""
    folder = tmp_path / "2026年第5期(90)" / "07 匿名作者"
    folder.mkdir(parents=True)
    target = folder / "reference.bib"
    target.write_text("@article{a, title={A}}\n", encoding="utf-8")
    before = target.read_bytes()

    bridge = _Bridge()
    view, _host = _view(bridge)
    view.build()
    assert view.work_dir == "", "派生不许凭空改写用户字段"
    view.target_bib = str(target)
    asyncio.run(view.prepare())

    name, kwargs = bridge.calls[0]
    assert name == "prepare"
    assert kwargs["work_dir"] == os.path.join(str(folder), ".aiw")
    assert os.path.isabs(kwargs["work_dir"]), "必须给绝对路径（cwd 不保证是哪里）"
    assert " ".join(kwargs["execute"]) == "bib_verify typeset"
    assert view.error == ""
    assert target.read_bytes() == before, "出任务书不许动目标 Bib 一个字节"
    assert view.work_dir == "", "跑完也不把派生值写回字段"


def test_work_dir_path_prefers_explicit_choice(tmp_path):
    """显式选了工作目录就以它为准（派生只填空）。"""
    view, _host = _view()
    view.work_dir = str(tmp_path / "ws")
    view.target_bib = str(tmp_path / "lib" / "reference.bib")
    assert view._work_dir_path() == str(tmp_path / "ws")
    view.work_dir = ""
    assert view._work_dir_path() == str(tmp_path / "lib" / ".aiw")
    view.target_bib = ""
    assert view._work_dir_path() == "", "两样都没选 → 交给动作给红字"


class _ErrTailBridge(_Bridge):
    """引擎失败时的形状：逐行输出（stderr 已合流）后返回非 0。"""

    def __init__(self, lines, *, code: int = 2) -> None:
        super().__init__(code=code)
        self.lines = list(lines)

    async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                       stdin_text=None):
        self.ran.append(call)
        for line in self.lines:
            if on_line is not None:
                on_line(line)
        return self.code


_ENGINE_STDERR = (
    "usage: convert.py --ai-workflow --work-dir DIR {prepare,import,...} ...",
    "convert.py: error: the following arguments are required: --work-dir",
)

_OPS = (("prepare", "prepare"), ("import", "import_result"), ("preview", "preview"),
        ("apply", "apply"), ("undo", "undo"), ("status", "refresh_status"))


@pytest.mark.parametrize("op,method", _OPS)
def test_exit_code_failures_quote_engine_stderr(op, method):
    """M3：6 条失败分支都要把引擎最后一行带进红字，不许只剩一个退出码。"""
    view, _host = _view(_ErrTailBridge(_ENGINE_STDERR))
    view.build()
    view.target_bib = "/tmp/ws/target.bib"
    view.result_path = "/tmp/ws/reply.md"
    view.preview_id = "pv-1"
    view.phase = "applied" if op == "undo" else "preview"
    asyncio.run(getattr(view, method)())
    assert "引擎返回退出码 2" in view.error, view.error
    assert "the following arguments are required" in view.error, (
        f"{op} 的红字丢掉了引擎 stderr：{view.error!r}")


# ── 10. 方案 B（问题 5）：阶段分块折叠 + 主操作随阶段切换 ──────────────────
def _stage_boxes(tree) -> dict:
    """按渲染顺序取四块（每块上打了 `_bib_ai_stage`）。"""
    return {getattr(n, "_bib_ai_stage"): n for n in _walk(tree)
            if getattr(n, "_bib_ai_stage", None)}


def _primaries(tree) -> list:
    return [n for n in _walk(tree) if isinstance(n, ft.FilledButton)]


def _visible_stages(tree) -> list[str]:
    boxes = _stage_boxes(tree)
    return [key for key in A.STAGE_ORDER if boxes[key].visible]


def test_stage_blocks_fold_to_current_phase():
    """四块每次全建，非当前阶段 `visible=False`；唯一 FilledButton 随阶段换。

    用户要求「按钮紧挨输入框、按操作先后排」。这里钉两条：同一时刻只展开一块 +
    主操作就是该阶段的真主动作——两条合起来才不会退回「7 个按钮叠页尾」。
    """
    view, _host = _view()
    cases = (
        ("idle", False, ["prepare"], A.ACTION_LABELS["prepare"]),
        ("preparing", False, ["prepare"], A.ACTION_LABELS["prepare"]),
        ("waiting", False, ["waiting"], A.ACTION_LABELS["import"]),
        # 结果已导入 → 阶段③，主操作变成「生成拟应用结果」
        ("waiting", True, ["review"], A.ACTION_LABELS["preview"]),
        ("preview", True, ["review"], A.ACTION_LABELS["apply"]),
        ("blocked", True, ["review"], A.ACTION_LABELS["apply"]),
        ("applied", True, ["done"], A.ACTION_LABELS["undo"]),
        # stale：撤销已不可用（can_undo()=False），主操作改为「重新生成拟应用结果」
        ("stale", True, ["done"], A.ACTION_LABELS["preview"]),
    )
    for phase, imported, open_stages, label in cases:
        view.phase = phase
        view.imported = imported
        tree = ft.Column(controls=view._parts())
        assert set(_stage_boxes(tree)) == set(A.STAGE_ORDER), phase
        assert _visible_stages(tree) == open_stages, (phase, imported)
        primaries = _primaries(tree)
        assert len(primaries) == 1, f"{phase}/{imported}: 任何时刻恰好 1 个 FilledButton"
        assert primaries[0].content == label, (phase, imported)


def test_stage_blocks_keep_inputs_next_to_their_buttons():
    """各阶段块的输入与按钮在同一块里；执行/采纳范围按阶段拆开。"""
    view, _host = _view()
    boxes = _stage_boxes(ft.Column(controls=view._parts()))
    prepare = _blob(boxes["prepare"])
    waiting = _blob(boxes["waiting"])
    review = _blob(boxes["review"])
    done = _blob(boxes["done"])
    # 真机缺陷 A：复制任务书属于②等待块——出任务书成功后①折叠，按钮必须还在。
    for word in ("目标 Bib", "关联正文", "执行范围", A.ACTION_LABELS["prepare"]):
        assert word in prepare, word
    for word in ("AI 结果正文", A.ACTION_LABELS["import"], "复制任务书全文"):
        assert word in waiting, word
    for word in ("采纳范围", A.ACTION_LABELS["preview"], "在 VS Code 里对比…",
                 A.ACTION_LABELS["apply"]):
        assert word in review, word
    assert A.ACTION_LABELS["undo"] in done
    # 两组范围各归其位（不再由 `_scope_block()` 一起平铺）
    assert "执行范围" not in waiting + review + done
    assert "采纳范围" not in prepare + waiting + done
    # 复制任务书只在②，①/③/④都没有
    assert "复制任务书全文" not in prepare + review + done
    assert "AI 结果正文" not in prepare + review + done


class _TaskbookBridge(_Bridge):
    """prepare 成功时像真引擎那样打印「任务书：<path>」行。"""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.taskbook = path

    async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                       stdin_text=None):
        self.ran.append(call)
        for line in ("phase=waiting", f"任务书：{self.taskbook}", "keys=7"):
            if on_line is not None:
                on_line(line)
        return 0


def test_copy_task_button_moves_to_waiting_after_prepare():
    """真机缺陷 A：出任务书成功后①折叠，复制按钮必须已在②且只建一份。"""
    view, _host = _view()
    view.build()
    view.target_bib = "/tmp/ws/target.bib"
    asyncio.run(view.prepare())
    assert view.phase == "waiting", "前置：出任务书成功应停在等待阶段"

    tree = ft.Column(controls=view._parts())
    boxes = _stage_boxes(tree)
    assert boxes["waiting"].visible and not boxes["prepare"].visible
    copies = [n for n in _walk(tree) if getattr(n, "_bib_ai_copy_task", False)]
    assert len(copies) == 1, "复制按钮只许建一个：不能在①和②各建一份"
    assert copies[0] is view._action_buttons["copy_task"]
    assert any(n is copies[0] for n in _walk(boxes["waiting"])), "复制按钮必须在②"
    assert not any(n is copies[0] for n in _walk(boxes["prepare"])), "①不该再有复制按钮"
    assert copies[0].disabled is False, "任务书已生成：复制按钮要可点"


def test_taskbook_path_visible_and_matches_engine_log():
    """真机缺陷 B：出任务书后状态区显示实际路径，且与日志行一致。"""
    path = "/tmp/ws/10 匿名作者/.aiw/reference_AI任务书.md"
    view, _host = _view(_TaskbookBridge(path))
    view.build()
    view.target_bib = "/tmp/ws/10 匿名作者/reference.bib"

    asyncio.run(view.prepare())

    assert view.taskbook_path == path
    assert A.parse_taskbook_path(view.log) == path, "路径必须来自引擎日志原文"
    blob = _blob(ft.Column(controls=view._parts()))
    assert "任务书路径" in blob
    assert path in blob, f"状态区没显示真实任务书路径：{blob!r}"


def test_taskbook_path_fallback_is_honest_without_log_line():
    """日志没有路径行 → 如实写「路径见引擎日志」，绝不拼一个猜测路径。"""
    view, _host = _view()  # _Bridge.run_call 不发「任务书：…」
    view.build()
    view.target_bib = "/tmp/ws/target.bib"

    asyncio.run(view.prepare())

    assert view.taskbook_path == ""
    blob = _blob(ft.Column(controls=view._parts()))
    assert "任务书路径" in blob
    assert "任务书已生成（路径见引擎日志）" in blob


def test_api_channel_primary_is_request_inside_waiting_block():
    """API 通道：请求按钮落在阶段②（不是页尾），准备阶段同时展开②才可达。"""
    view, _host = _view()
    view.set_channel("api")
    tree = ft.Column(controls=view._parts())
    boxes = _stage_boxes(tree)
    assert _visible_stages(tree) == ["prepare", "waiting"], "API 准备阶段也要开②"
    primaries = _primaries(tree)
    assert len(primaries) == 1
    assert primaries[0].content == A.ACTION_LABELS["request"]
    request_btn = view._action_buttons["request"]
    assert any(n is request_btn for n in _walk(boxes["waiting"])), \
        "「直接请求（API）」必须紧挨在阶段②里"
    assert not any(n is view._action_buttons.get("prepare") for n in _walk(tree)), \
        "API 通道不摆「出任务书」"


def test_single_scroll_and_all_stage_blocks_in_built_shell():
    """护栏不因分块而破：整页仍只有一个滚动容器，四块都在壳里。"""
    view, _host = _view()
    tree = view.build()
    scrollers = [n for n in _walk(tree) if getattr(n, "scroll", None)]
    assert len(scrollers) == 1, "单一页面纵向滚动"
    assert set(_stage_boxes(tree)) == set(A.STAGE_ORDER)
    # 页尾不再有旧的「动作」整块标题：动作已经并进各阶段块
    assert "动作" not in _texts(tree)


def test_execute_section_has_no_redundant_explainer():
    """用户在真机上要求删掉「执行 = 引擎去核实/检查什么…」这行无用小字。"""
    view, _host = _view()
    blob = _blob(ft.Column(controls=view._parts()))
    assert "执行范围" in blob
    assert "不等于同意修改" not in blob, "出任务书按钮上方的说明小字应已删除"
