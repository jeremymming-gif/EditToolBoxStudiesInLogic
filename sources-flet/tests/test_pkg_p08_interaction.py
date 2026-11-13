"""P08（外壳交互与任务状态）验收测试：CONTRACT v1.1 的 I-1..I-5。

锁的是**用户可见结论**，不复述实现：
- I-1 环境检查跳过态不得冒充成功；fail/warn 保留原因与修复提示；诊断文本同样标明跳过。
- I-2 「未保存改动」「设置文件」两行能原地刷新（环境检查持久化后 / on_show / 保存后）。
- I-3 引擎 ⚠️ 文本开头的变体选择符（U+FE0F 等）不得漏进结果卡。
- I-4 picker 取消不覆旧值、失败说清是哪个输入且有下一步、选中路径逐字进字段。
- I-5 运行中入口可见拒绝（不排队/并发）、取消/失败绝不写成完成、焦点钩子安全接线。
"""
from __future__ import annotations

import asyncio
import inspect
import weakref
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

import main as M
from services import engine_bridge as EB
from test_b112_post_b import (  # noqa: F401  复用既有假壳与构建助手
    _FakeApp,
    _Store,
    _build_issue,
    _build_single,
    _walk,
)
from test_b115_issue_settings import _SettingsApp, _build_settings
from ui import components as C
from ui import pages as P
from ui import settings_shell as SHELL
from ui import task_feedback as TF
from ui.base_page import BasePage
from ui.tokens import Tokens


def _fresh_tokens() -> Tokens:
    return Tokens("light", 1.0, 96)


def _texts(control) -> list[str]:
    """收集控件树里所有可见文本（含 Text/Button 的标签）。"""
    out: list[str] = []
    for node in _walk(control):
        value = getattr(node, "value", None)
        if isinstance(value, str) and value:
            out.append(value)
        text = getattr(node, "text", None)
        if isinstance(text, str) and text:
            out.append(text)
    return out


def _find_row_with(control, needle: str):
    """返回第一个包含 needle 文本的 Row（用于把徽章/圆点归属到具体检查行）。"""
    for node in _walk(control):
        if isinstance(node, ft.Row) and any(needle in text for text in _texts(node)):
            return node
    return None


# ───────────────────────── I-1：跳过态不冒充成功 ─────────────────────────
_SKIP_CHECK = {
    "id": "output_writable",
    "title": "输出目录可写",
    "level": "info",
    "status": "ok",
    "detail": "未指定 --out-dir，跳过写入检查（不参与结论）。",
}
_FAIL_CHECK = {
    "id": "xelatex",
    "title": "XeLaTeX",
    "level": "required",
    "status": "fail",
    "detail": "引擎跑不起来。",
    "hint": "在设置里填写正确的引擎目录。",
}


def _payload(*checks: dict) -> dict:
    return {"ok": False, "checks": list(checks)}


def test_envcheck_skip_row_is_not_success_and_keeps_detail():
    rows = SHELL.envcheck_rows(_payload(_SKIP_CHECK))
    assert len(rows) == 1
    row = rows[0]
    assert row["skipped"] is True
    assert row["ok"] is False, "跳过态不得算成功"
    assert row["tone"] != "success"
    assert row["badge"] == "跳过"
    assert row["detail"] == _SKIP_CHECK["detail"]
    assert row["fix"] == ""


def test_envcheck_skip_row_renders_neutral_dot_and_says_not_counted():
    t = _fresh_tokens()
    page = SimpleNamespace(t=t, envcheck_error="", envcheck_data=_payload(_SKIP_CHECK))
    page.envcheck_slot = ft.Container()
    SHELL.render_envcheck(page)
    root = page.envcheck_slot.content

    row = _find_row_with(root, "输出目录可写")
    assert row is not None
    dot = row.controls[0]
    assert dot.bgcolor == t.palette.text_faint, "跳过行的圆点必须是中性灰，不能是成功绿"
    assert "跳过" in _texts(row)

    detail_texts = [text for text in _texts(root) if "未参与" in text]
    assert detail_texts, "跳过行下方必须说明未参与结论"
    assert "跳过写入检查" in " ".join(detail_texts), "原始 detail 必须保真显示"


def test_envcheck_fail_row_keeps_reason_and_fix():
    rows = SHELL.envcheck_rows(_payload(_FAIL_CHECK))
    row = rows[0]
    assert row["skipped"] is False and row["ok"] is False
    assert row["tone"] == "error"
    assert row["badge"] == "fail", "失败行徽章保留引擎原始状态"
    text, tone = SHELL._envcheck_row_detail(row)
    assert "引擎跑不起来" in text and "正确的引擎目录" in text
    assert tone == "error"


def test_envcheck_skip_does_not_turn_summary_green():
    """诊断/汇总层面同样不得把跳过写成功（Lead 指出的第二消费方）。"""
    payload = _payload(_SKIP_CHECK, _FAIL_CHECK)
    ok_count = sum(1 for row in SHELL.envcheck_rows(payload) if row["ok"])
    assert ok_count == 0
    assert SHELL.check_ok(_SKIP_CHECK) is False
    assert SHELL.check_ok({"status": "ok", "level": "required"}) is True


# ─────────────────── I-2：设置页状态行原地刷新 ───────────────────
class _EnvSettingsApp(_SettingsApp):
    """真实模拟 set_check_done：写盘成功即 dirty=False（P01-4.1-④ 的现场）。"""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.store.dirty = True
        self.check_done_calls: list[bool] = []
        self.run_tasks: list = []

    def set_check_done(self, value) -> None:
        self.check_done_calls.append(bool(value))
        self.store.set("env_check_done", bool(value))
        self.store.dirty = False  # 真实实现 save() 后 dirty 清零

    def persist(self) -> bool:
        self.store.dirty = False
        return True


def _build_env_settings(tmp_path: Path):
    app = _EnvSettingsApp(tmp_path)
    page = P.SettingsPage(app)
    root = page.build()
    return app, page, root


def test_status_rows_refresh_in_place_when_dirty_clears(tmp_path):
    app, page, _root = _build_env_settings(tmp_path)
    ctl = page._status_value_ctls["未保存改动"]
    assert "有" in ctl.value
    app.store.dirty = False
    page.refresh_status_rows()
    assert ctl.value == "无"


def test_status_rows_follow_store_changes_on_show(tmp_path):
    app, page, _root = _build_env_settings(tmp_path)
    ctl = page._status_value_ctls["未保存改动"]
    app.store.dirty = False
    page.refresh_status_rows()
    assert ctl.value == "无"
    app.store.dirty = True  # 用户改了引擎目录但没保存
    page.on_show()
    assert "有" in ctl.value


def test_settings_file_row_tracks_current_settings_path(tmp_path):
    app, page, _root = _build_env_settings(tmp_path)
    ctl = page._status_value_ctls["设置文件"]
    assert ctl.value == str(app.store.path)
    app.store.path = tmp_path / "另一个 位置" / "settings.json"
    page.refresh_status_rows()
    assert ctl.value == str(app.store.path), "换设置文件位置后该行必须原地刷新"


def test_envcheck_persist_refreshes_unsaved_row(tmp_path, monkeypatch):
    app, page, _root = _build_env_settings(tmp_path)
    ctl = page._status_value_ctls["未保存改动"]
    assert "有" in ctl.value

    payload = _payload(_SKIP_CHECK)
    monkeypatch.setattr(EB, "envcheck_call", lambda out_dir="": "stub-call")
    monkeypatch.setattr(EB, "read_json_command", lambda call: {"ok": True, "code": 0, "json": payload})

    asyncio.run(page._envcheck_task())

    assert app.check_done_calls == [True]
    assert app.store.dirty is False
    assert ctl.value == "无", "环境检查写盘后该行必须立刻刷新，不能停在构建期快照"


# ─────────────────── I-3：U+FE0F 前导变体符 ───────────────────
def test_strip_format_chars_removes_invisible_format_marks():
    assert TF.strip_format_chars("\ufe0f 扉页.pdf 有 3 页") == "扉页.pdf 有 3 页"
    dirty = "\u200b\u200f正文\ufe0e\u2060"
    assert TF.strip_format_chars(dirty) == "正文"
    assert TF.strip_format_chars("") == ""
    assert TF.strip_format_chars(None) == ""
    # 幂等：清两遍与清一遍结果一致
    once = TF.strip_format_chars("\ufe0f  a  ")
    assert TF.strip_format_chars(once) == once


def test_merge_warning_from_engine_has_no_leading_variation_selector():
    page, _root = _build_issue()
    page._merge_info = {}
    page._feed_merge("⚠️ 扉页.pdf 有 3 页，未按前置页顺序？")

    warnings = page._merge_info.get("warnings") or []
    assert warnings, "⚠ 行必须被解析成 warning"
    assert "\ufe0f" not in warnings[0]
    assert warnings[0].startswith("扉页.pdf"), warnings[0]


def test_merge_result_card_renders_clean_warning_lines():
    page, _root = _build_issue()
    page._merge_info = {"path": "", "pages": 3,
                        "warnings": ["\ufe0f 扉页.pdf 有 3 页", "\u200b 封底.pdf 缺失"]}
    page._merge_run = {}
    texts = _texts(page._merge_result())
    joined = "\n".join(texts)
    assert "\ufe0f" not in joined and "\u200b" not in joined
    assert "· 扉页.pdf 有 3 页" in texts
    assert "· 封底.pdf 缺失" in texts


# ─────────────────── I-4：文件选择器（picker） ───────────────────
class _ScriptedPicker:
    def __init__(self, *, directory=None, files=None, error=None) -> None:
        self.directory = directory
        self.files = files
        self.error = error

    async def get_directory_path(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.directory

    async def pick_files(self, *args, **kwargs):
        if self.error:
            raise self.error
        # Flet 1.0.5 的 pick_files 返回带 .path 的对象，不是裸字符串
        return [SimpleNamespace(path=item) for item in (self.files or [])]


class _TaskPage:
    """真的把 handler 跑完（与 _FakeFletPage.run_task 同语义）。"""

    def run_task(self, handler, *args, **kwargs):
        assert inspect.iscoroutinefunction(handler), "run_task 必须调度协程"
        return asyncio.run(handler(*args, **kwargs))

    def update(self) -> None:
        pass


def _picker_app(picker, *, running: bool = False):
    app = SimpleNamespace(running=running, page=_TaskPage(), _picker=picker,
                          store=_Store(), last_line="")
    app.toasts: list[str] = []
    app.marks: list[tuple] = []
    app.toast = lambda message: app.toasts.append(message)
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.persist = lambda **kwargs: True
    app._picker_error = M.App._picker_error.__get__(app)
    app._browse_task = M.App._browse_task.__get__(app)
    return app


def _events(app) -> list[str]:
    return [event for event, _fields in app.marks]


def test_picker_cancel_keeps_old_value_and_says_cancelled():
    app = _picker_app(_ScriptedPicker(files=[]))
    field = SimpleNamespace(value="/旧/期 目录", update=lambda: None)
    ref = [field]

    M.App.browse_field(app, ref, "选择期目录", "dir", "issue_dir")

    assert field.value == "/旧/期 目录", "取消选择不得覆盖旧值"
    assert app.toasts == ["已取消选择"]
    assert "picker_cancel" in _events(app)
    assert "picker_error" not in _events(app), "取消不是错误"


def test_picker_failure_names_the_field_and_next_step():
    app = _picker_app(_ScriptedPicker(error=RuntimeError("ENTITLEMENT_NOT_FOUND")))
    field = SimpleNamespace(value="/旧/期 目录", update=lambda: None)

    M.App.browse_field(app, [field], "选择期目录", "dir", "issue_dir")

    assert field.value == "/旧/期 目录"
    assert "picker_error" in _events(app)
    assert len(app.toasts) == 1
    message = app.toasts[0]
    assert "期目录" in message, f"失败提示要点名受影响的输入：{message}"
    assert "粘贴路径" in message or "重试" in message, f"失败提示要给出下一步：{message}"
    label = dict(app.marks)["picker_error"].get("label")
    assert label == "期目录"


def test_picker_selection_is_verbatim_for_paths_with_spaces():
    chosen = "/tmp/我的 期/源 稿件.tex"
    app = _picker_app(_ScriptedPicker(files=[chosen]))
    field = SimpleNamespace(value="", update=lambda: None)

    M.App.browse_field(app, [field], "选择源稿件", "file", "single_source",
                       store_prefix="paths")

    assert field.value == chosen, "选中值必须逐字一致（含空格），不得 strip 变形"
    assert app.store.get("paths.single_source") == chosen
    assert "picker_result" in _events(app)


def test_browse_field_rejected_visibly_while_running():
    app = _picker_app(_ScriptedPicker(), running=True)
    field = SimpleNamespace(value="/旧", update=lambda: None)

    M.App.browse_field(app, [field], "选择期目录", "dir", "issue_dir")

    assert field.value == "/旧"
    assert len(app.toasts) == 1 and "运行中" in app.toasts[0]
    assert "picker_open_begin" not in _events(app), "运行中不得真的打开对话框"


# ─────────────────── I-5：运行入口可见拒绝 + 状态不串 ───────────────────
def test_single_page_run_entry_rejected_visibly_while_running():
    page, _root = _build_single()
    app = page.app
    app.running = True
    scheduled: list = []
    app.page = SimpleNamespace(run_task=lambda *a, **k: scheduled.append(a))
    page.page = app.page

    page._on_run(None)

    assert scheduled == [], "运行中不得排队第二个任务"
    assert app.toasts and "运行中" in app.toasts[-1]
    rejected = dict(app.marked)["run_rejected"]
    assert rejected["entry"] == "single_run"


def test_issue_step_entry_rejected_visibly_while_running(monkeypatch):
    page, _root = _build_issue()
    app = page.app
    app.running = True
    started: list = []
    monkeypatch.setattr(P.ASM, "run_step", lambda *a, **k: started.append(a))

    page._run_step("compileall")

    assert started == [], "运行中不得启动新步骤"
    assert app.toasts and "运行中" in app.toasts[-1]


def _run_shim(*, running: bool):
    app = SimpleNamespace(running=running, run_seen=False, cancel_requested=True,
                          proc=object(), current_call=None, last_line="旧日志",
                          line_count=3, first_line_ms=1.0, run_start_mono=1.0,
                          pages={}, toasts=[], marks=[])
    app.toast = lambda message: app.toasts.append(message)
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    app.status_bar = SimpleNamespace()
    app.status_calls: list[str] = []
    app.status_bar.set_status = lambda text: app.status_calls.append(text)
    return app


_CALL = SimpleNamespace(entry="envcheck", label="环境检查", argv=["envcheck.py"])


def test_begin_run_refuses_second_run_without_touching_state():
    app = _run_shim(running=True)
    proc_before = app.proc

    accepted = M.App.begin_run(app, _CALL)

    assert accepted is False
    assert app.toasts and "运行中" in app.toasts[0]
    assert app.marks and app.marks[0][0] == "run_rejected"
    # 关键：拒绝时不得重置正在跑的那一轮现场
    assert app.proc is proc_before
    assert app.last_line == "旧日志"
    assert app.cancel_requested is True
    assert app.status_calls == []


def test_begin_run_accepts_first_run_and_resets_state():
    app = _run_shim(running=False)

    accepted = M.App.begin_run(app, _CALL)

    assert accepted is True
    assert app.running is True
    assert app.cancel_requested is False
    assert app.last_line == ""
    assert app.status_calls and "环境检查" in app.status_calls[0]


def test_cancelled_step_status_is_never_written_as_done():
    page, _root = _build_issue()
    page._apply_status("compileall", 0, True)   # 用户取消，但子进程 rc=0（迟到回调）
    assert page._status["compileall"].state == "cancelled"

    page._apply_status("infocheck", 1, False)
    assert page._status["infocheck"].state == "warn"

    page._apply_status("dirpush", 0, False)
    assert page._status["dirpush"].state == "done"


def test_merge_result_cancelled_and_failed_never_say_complete():
    page, _root = _build_issue()

    page._merge_run = {"state": "cancelled"}
    page._merge_info = {}
    cancelled_text = "\n".join(_texts(page._merge_result()))
    assert "已取消" in cancelled_text
    assert "合并完成" not in cancelled_text

    page._merge_run = {"state": "error"}
    page._merge_info = {"error": "mergepdf 退出码 2"}
    failed_text = "\n".join(_texts(page._merge_result()))
    assert "失败" in failed_text
    assert "合并完成" not in failed_text


def test_open_only_real_existing_artifact():
    app = _run_shim(running=False)

    M.App.open_path(app, "")
    assert app.toasts and "没有可打开的产物" in app.toasts[-1]
    assert app.marks[-1][0] == "open_artifact_missing"

    M.App.open_path(app, "/tmp/绝对不存在的产物.pdf")
    assert "不在原位" in app.toasts[-1]
    assert app.marks[-1][1].get("reason") == "missing"
    assert [event for event, _ in app.marks] == ["open_artifact_missing", "open_artifact_missing"]


# ─────────────────── R03：导航后焦点钩子接线 ───────────────────
def test_navigate_focus_hook_schedules_async_focus():
    scheduled: list = []
    app = SimpleNamespace(toasts=[], marks=[],
                          page=SimpleNamespace(run_task=lambda fn: scheduled.append(fn)))
    app.mark = lambda event, **fields: app.marks.append((event, fields))
    focused: list[bool] = []

    class _Ctl:
        async def focus(self):
            focused.append(True)

    page = SimpleNamespace(key="home", focus_after_nav=lambda: _Ctl())
    M.App._focus_after_nav(app, page)

    assert len(scheduled) == 1
    asyncio.run(scheduled[0]())
    assert focused == [True]
    assert app.marks and app.marks[0][0] == "nav_focus"


def test_navigate_focus_hook_none_and_error_are_safe():
    scheduled: list = []
    app = SimpleNamespace(marks=[],
                          page=SimpleNamespace(run_task=lambda fn: scheduled.append(fn)))
    app.mark = lambda event, **fields: app.marks.append((event, fields))

    M.App._focus_after_nav(app, SimpleNamespace(key="home", focus_after_nav=lambda: None))
    assert scheduled == []
    assert app.marks == [], "None 不动焦点也不写遥测"

    def _boom():
        raise RuntimeError("no focus")

    M.App._focus_after_nav(app, SimpleNamespace(key="issue", focus_after_nav=_boom))
    assert scheduled == []
    assert app.marks and app.marks[0][0] == "focus_after_nav_error"

# ══════════ 组件契约（R01 / R03 / I-2）：GB 提供，合入保留 [GB] 标记 ══════════
# 这些用例锁的是共享组件层的对外契约（kv_row 句柄、path_picker tooltip、status_dot 语气、
# focus_after_nav 与键盘安装语义、最小窗口尺寸），与上面页面级用例互不替代。
# 片段源：$EVID/team/GB/tests-snippet.py（GB 已在侧自行跑通 10 passed）。
class _GBPage(BasePage):
    key = "fake"

    def build(self):
        return ft.Column(controls=[ft.Text("x")])


class _GBFocusedPage(_GBPage):
    """按需覆盖焦点策略的页面：返回输入框，等价 GA 在设置页保存的首个可编辑控件。"""

    def focus_after_nav(self):
        return self.fields.get("first", [None])[0]


# ── I-2：kv_row 的 value 句柄（原地刷新状态行） ────────────────────────────
def test_gb_kv_row_exposes_value_ctl_without_changing_signature():
    # [GB] kv_row 形参列表被 tests/test_b115_shared.py 冻结，I-2 走返回容器的属性。
    assert list(inspect.signature(C.kv_row).parameters) == ["t", "label", "value", "mono"]
    row = C.kv_row(Tokens("light", 1.0, 96), "设置文件", "/tmp/设置.json", mono=True)
    assert isinstance(row, ft.Container)
    # 结构不变：label / spacer / 右对齐槽（b115 既有断言同样覆盖，这里只锁句柄指向）
    inner = row.content
    assert isinstance(inner, ft.Row) and len(inner.controls) == 3
    label_ctl, spacer, slot = inner.controls
    assert isinstance(label_ctl, ft.Text) and label_ctl.value == "设置文件"
    assert isinstance(spacer, ft.Container) and spacer.expand is True
    assert slot.expand is True and slot.alignment == ft.Alignment.CENTER_RIGHT
    value_ctl = row.value_ctl
    assert value_ctl is slot.content and isinstance(value_ctl, ft.Text)
    assert value_ctl.value == "/tmp/设置.json" and value_ctl.tooltip == "/tmp/设置.json"
    # GA refresh_status_rows() 的用法：改值 + 改 tooltip 即可（不需要重建整页）
    value_ctl.value = "无"
    value_ctl.tooltip = None
    assert value_ctl.value == "无" and value_ctl.tooltip is None


def test_gb_kv_row_empty_value_has_no_tooltip():
    # [GB] 空值不给空气泡（与 b115 既有契约一致）
    row = C.kv_row(Tokens("light", 1.0, 96), "未保存改动", None)
    assert row.value_ctl.value == ""
    assert row.value_ctl.tooltip is None


# ── R01：path_picker 的完整路径 tooltip（两条链路都要同步） ────────────────
def test_gb_path_picker_tooltip_tracks_full_value():
    # [GB] R01：长路径被截断时悬停看全文；初值 / 用户输入 / 清空三条链路都同步。
    t = Tokens("light", 1.0, 96)
    field_ref: list = []
    picker = C.path_picker("源稿件", "/Users/x/很长很长的路径/原稿.docx", "hint", t,
                           field_ref=field_ref)
    assert isinstance(picker, ft.Column)
    field = field_ref[0]
    assert isinstance(field, ft.TextField)
    assert field.tooltip == "/Users/x/很长很长的路径/原稿.docx"
    assert getattr(field, "_value_tooltip", False) is True
    # 用户输入链路：text_field(clearable=True) 把 on_change 包成 _wrapped → sync_clearable
    field.value = "  /tmp/新路径.tex  "
    assert field.on_change is not None
    field.on_change(SimpleNamespace(control=field))
    assert field.tooltip == "/tmp/新路径.tex"        # strip 后全文，不带空白
    # 程序化回填链路：base_page.set_field 也是直接调 sync_clearable
    field.value = "/tmp/另一个.docx"
    C.sync_clearable(field)
    assert field.tooltip == "/tmp/另一个.docx"
    # 清空后不留空气泡
    field.value = "   "
    C.sync_clearable(field)
    assert field.tooltip is None
    # 布局契约不被改：仍是 48 高、expand 的浮动标签输入框
    assert field.height == t.px(48) and field.expand is True


# ── I-1：status_dot 的 tone 映射（envcheck_rows 直接喂 tone） ──────────────
def test_gb_status_dot_tone_map():
    # [GB] 新增 success/info/neutral；既有 ok/done/warn/error/run/idle 语义不变。
    t = Tokens("light", 1.0, 96)
    p = t.palette
    expect = {
        "ok": p.success, "done": p.success, "success": p.success,
        "warn": p.warn, "error": p.error, "run": p.accent,
        "idle": p.text_faint, "info": p.text_muted, "neutral": p.text_muted,
    }
    for tone, color in expect.items():
        dot = C.status_dot(tone, t)
        assert isinstance(dot, ft.Container)
        assert dot.bgcolor == color, f"tone={tone}"
        assert dot.width == t.px(6) and dot.height == t.px(6)
    # 未知 tone 退 idle 灰点（不抛错、不冒充成功绿）
    assert C.status_dot("bogus", t).bgcolor == p.text_faint


# ── R03：BasePage.focus_after_nav 默认不动焦点，语义链不变 ────────────────
def test_gb_base_page_focus_after_nav_default_none():
    # [GB] R03：空 fields 的纯展示页 → None（不抢焦点）；GA 在 navigate 接线。
    app = _FakeApp()
    page = _GBPage(app)
    assert page.focus_after_nav() is None
    # 覆盖后返回控件由调用方 page.run_task(ctrl.focus) 调度（Flet 1.0.5 focus 是 async）
    focused = _GBFocusedPage(app)
    target = ft.TextField(value="x")
    focused.fields["first"] = [target]
    assert focused.focus_after_nav() is target
    assert inspect.iscoroutinefunction(target.focus)


def test_gb_base_page_focus_after_nav_prefers_mounted_field():
    # [GB] R03 默认实现（round-2 更新）：有已挂载字段 → 插入序第一个字段控件；
    # 未挂载的字段读 `.page` 会抛 RuntimeError，不能当落点（否则导航会炸）。
    app = _FakeApp()
    page = _GBPage(app)
    first, second = ft.TextField(value="a"), ft.TextField(value="b")
    page.fields["first"] = [first]
    page.fields["second"] = [second]
    assert page.focus_after_nav() is None
    _mount_fields(page)
    assert page.focus_after_nav() is first


def test_gb_base_page_lifecycle_semantics_unchanged():
    # [GB] R03：加 focus_after_nav 不得改 ensure_built / theme_roots / on_show 语义。
    app = _FakeApp()
    page = _GBPage(app)
    assert page.root is None and page.theme_roots() == []
    root = page.ensure_built()
    assert page.root is root and page.ensure_built() is root   # 只 build 一次
    assert page.theme_roots() == [root]
    page.on_show()          # BasePage 默认 no-op：不抛错、不动焦点
    page.refresh_running()
    assert page.focus_after_nav() is None


# ── R03：图标按钮都有可理解名称 + 死代码键盘安装器不覆盖已有处理器 ─────────
def test_gb_icon_button_requires_tooltip():
    # [GB] R03：icon_button 的 tooltip 是必填位参 → 新调用点不可能漏名字。
    params = inspect.signature(C.icon_button).parameters
    assert "tooltip" in params
    assert params["tooltip"].default is inspect.Parameter.empty
    assert params["tooltip"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_gb_log_resize_handle_has_tooltip():
    # [GB] R03：日志拖拽把手只有一条 3px 灰条，没有文字 → 悬停提示是唯一可理解名称。
    console = C.LogConsole(Tokens("light", 1.0, 96), dock=True)
    assert console._resize_handle.tooltip == "拖拽调整日志高度（双击复位）"


def test_gb_issue_page_keyboard_installer_keeps_existing_handler():
    # [GB] R03：`IssuePage._install_keyboard`（pages.py:3556）现状是「只填空白、不覆盖」
    # ——这就是它被 settings_shell.install_hotkeys 先占 page.on_keyboard_event 后永不
    # 生效的机制（app/main.py:646-649 顺序：rebuild_shell 后 install_hotkeys）。锁行为，
    # 防止有人改成强行覆盖（那会在输入框里抢方向键/Esc）。
    page_obj = SimpleNamespace(on_keyboard_event=None)
    sp = object.__new__(P.IssuePage)           # 不跑 __init__（依赖真外壳）
    sp.app = SimpleNamespace(page=page_obj)
    sp._keyboard_installed = False
    sp._install_keyboard()
    assert page_obj.on_keyboard_event.__func__ is P.IssuePage._on_key
    assert page_obj.on_keyboard_event.__self__ is sp
    # 已有全局热键处理器时保持不动（install_hotkeys 先占就是这种情况）
    existing = lambda event: None             # noqa: E731
    page_obj.on_keyboard_event = existing
    sp2 = object.__new__(P.IssuePage)
    sp2.app = SimpleNamespace(page=page_obj)
    sp2._keyboard_installed = False
    sp2._install_keyboard()
    assert page_obj.on_keyboard_event is existing


# ── R05：最小窗口尺寸（1000×700 设计像素，随 k 缩放） ─────────────────────
def test_gb_min_window_size_is_1000x700_at_k1():
    # [GB] R05：原生窗口在 1000×700 下实测截图证据见 team/GB/shots/；这里锁常量。
    assert Tokens("light", 1.0, 96).min_window_size == (1000, 700)
    assert Tokens("dark", 1.5, 96).min_window_size == (1500, 1050)

# ══════════ 2.8-V 复验补修：D1 产物打开 / D2 导航焦点 / D3 状态点语气 ══════════
def _mount_fields(page):
    """把页面已注册字段挂到一页假 Flet Page 上（Flet 1.0.5 的 `.page` 靠 parent 链，
    未挂载会抛 RuntimeError；焦点钩子只认「已挂载」的输入框）。"""
    class _Sess:  # ft.Page 要 weakref 到 session 对象
        pass

    flet_page = ft.Page(_Sess())
    for ref in page.fields.values():
        control = ref[0] if ref else None
        if control is not None:
            try:
                control._parent = weakref.ref(flet_page)
            except Exception:  # noqa: BLE001
                pass
    # 假 Page 只被控件的 weakref 引用，必须在页面对象上保活，否则马上被回收
    page._test_flet_page = flet_page
    return flet_page


# ── D1：不存在的产物不得给「打开」 ─────────────────────────────────────────
def test_missing_artifact_line_never_offers_open(tmp_path, monkeypatch):
    # [P08 D1 / V 复现] 引擎末行报告不存在的文件：结果卡「完成」，但没有可打开的产物
    # ——不能退到已存在的父目录（V 实测 detail=/tmp/、open_visible=True）。
    from test_b116_single_function import _App, _install_run, _make_page

    (tmp_path / "out").mkdir()
    page, _root = _make_page(tmp_path, _App())
    missing = tmp_path / "out" / "p08 不存在 的产物.pdf"
    _install_run(monkeypatch, page, 0, f"完成。生成文件：{missing}（共 3 页）")

    asyncio.run(page._run(page._build_call()))

    assert "完成" in str(page._result.title.value)
    assert page._result.open_btn.visible is False
    assert page._result.copy_btn.visible is False
    # detail 不能落成父目录（旧行为是 /tmp/ 这类目录）
    assert page._result.detail.value == ""
    assert page._artifact_row.visible is False


def test_existing_artifact_line_still_offers_open(tmp_path, monkeypatch):
    # [P08 D1 正例] 真实存在的文件仍给「打开」，且 detail 指向该文件。
    from test_b116_single_function import _App, _install_run, _make_page

    page, _root = _make_page(tmp_path, _App())
    real = tmp_path / "out" / "Jingru_Kou.tex"
    real.parent.mkdir()
    real.write_text("stub", encoding="utf-8")
    _install_run(monkeypatch, page, 0, f"完成。产物文件：{real}")

    asyncio.run(page._run(page._build_call()))

    assert page._result.detail.value == str(real)
    assert page._result.open_btn.visible is True


# ── D2：导航后焦点落在首个已注册输入框 ────────────────────────────────────
def test_focus_after_nav_returns_first_field_for_single_page():
    # [P08 D2] SinglePage 构建并挂载字段后，钩子给出落点（不是 None）。
    page, _root = _build_single()
    assert page.focus_after_nav() is None, "未挂载的字段不能当落点（会炸导航）"

    _mount_fields(page)
    target = page.focus_after_nav()

    assert target is not None
    assert isinstance(target, ft.TextField)
    assert target is page.fields["convert_vol"][0]   # 插入序第一个已注册字段


def test_focus_after_nav_returns_issue_dir_field_for_issue_page():
    # [P08 D2] IssuePage 的期目录输入框同样可作落点。
    page, _root = _build_issue()
    _mount_fields(page)
    target = page.focus_after_nav()
    assert target is page.fields["issue_dir"][0]


def test_focus_after_nav_is_none_and_safe_for_pages_without_fields():
    # [P08 D2] 纯展示页没有输入框：返回 None、不抛错（导航不会被炸）。
    class _NoFieldPage(BasePage):
        key = "nofield"

        def build(self):
            return ft.Column(controls=[ft.Text("说明")])

    page = _NoFieldPage(_FakeApp())
    assert page.focus_after_nav() is None


# ── D3：状态栏状态点语气（成功/警告/失败/取消不混用） ──────────────────────
def test_status_bar_dot_tone_matches_failure_and_cancel():
    # [P08 D3 / V 复现] 「结束（退出码 2）」曾用 success 绿点。
    bar = C.StatusBar(_fresh_tokens())
    palette = bar.t.palette
    cases = {
        "结束（退出码 2）": palette.error,
        "失败（退出码 3）": palette.error,
        "结束（退出码 1）": palette.warn,
        "已取消": palette.warn,
        "就绪": palette.success,
        "运行中：编译": palette.accent,
    }
    for value, expected in cases.items():
        bar.set_status(value)
        assert bar.dot.bgcolor == expected, f"status={value}"


# ── MC-03：缺失产物不得误认父目录（GUI 级，D1 口径延伸） ────────────────────
def test_missing_hidden_artifact_line_never_offers_open(tmp_path, monkeypatch):
    # [MC-03] 隐藏段 .hidden/ 存在、其中的文件不存在：结果卡「完成」，
    # 但不得把已存在的 .hidden/（或任何祖先目录）当成产物给「打开」。
    from test_b116_single_function import _App, _install_run, _make_page

    (tmp_path / ".hidden").mkdir()
    page, _root = _make_page(tmp_path, _App())
    missing = tmp_path / ".hidden" / "missing.pdf"
    _install_run(monkeypatch, page, 0, f"完成。生成文件：{missing}（共 3 页）")

    asyncio.run(page._run(page._build_call()))

    assert "完成" in str(page._result.title.value)
    assert page._result.open_btn.visible is False
    assert page._result.copy_btn.visible is False
    assert page._result.detail.value == ""
    assert page._artifact_row.visible is False


def test_missing_artifact_under_paren_dir_never_offers_open(tmp_path, monkeypatch):
    # [MC-03] 期号目录名含半角括号且真实存在、其中的文件缺失：
    # 不得把期号目录误认成产物（半角括号是路径的一部分，不是收尾边界）。
    from test_b116_single_function import _App, _install_run, _make_page

    issue = tmp_path / "2026年第1期(总第100期)"
    issue.mkdir()
    page, _root = _make_page(tmp_path, _App())
    missing = issue / "缺失.pdf"
    _install_run(monkeypatch, page, 0, f"完成。生成文件：{missing}（共 3 页）")

    asyncio.run(page._run(page._build_call()))

    assert "完成" in str(page._result.title.value)
    assert page._result.open_btn.visible is False
    assert page._result.detail.value == ""
    assert page._artifact_row.visible is False


def test_existing_directory_artifact_line_offers_open(tmp_path, monkeypatch):
    # [MC-03 正例] convert 末行契约「完成。产物目录：{out_dir}」：
    # 显式交付目录是真实产物，detail 指向该目录且给「打开」，目录动作行显示。
    from test_b116_single_function import _App, _install_run, _make_page

    out = tmp_path / "out" / "作者名"
    out.mkdir(parents=True)
    page, _root = _make_page(tmp_path, _App())
    _install_run(monkeypatch, page, 0, f"完成。产物目录：{out}")

    asyncio.run(page._run(page._build_call()))

    assert page._result.detail.value == str(out)
    assert page._result.open_btn.visible is True
    assert page._result.copy_btn.visible is True
    assert page._artifact_row.visible is True


# ── MC-03 返工：日志中其他现存目录不得当成声明产物（GUI 级） ────────────────
def test_logdir_line_with_missing_report_never_offers_open(tmp_path, monkeypatch):
    # [MC-03 返工 / ACCEPT H2+S4] 日志目录存在、声明报告缺失：结果卡「完成」，
    # 但不得给出指向日志目录的「打开」。
    from test_b116_single_function import _App, _install_run, _make_page

    logdir = tmp_path / "日志 目录C"
    logdir.mkdir()
    report = tmp_path / "卷 四" / "缺失 报告.txt"
    report.parent.mkdir(parents=True)
    page, _root = _make_page(tmp_path, _App())
    line = (f"完成。检查 12 篇（成功 11 / 失败 1；状态 0）"
            f"（日志目录：{logdir}）（报告：{report}）")
    _install_run(monkeypatch, page, 0, line)

    asyncio.run(page._run(page._build_call()))

    assert "完成" in str(page._result.title.value)
    assert page._result.open_btn.visible is False
    assert page._result.copy_btn.visible is False
    assert page._result.detail.value == ""
    assert page._artifact_row.visible is False


def test_logdir_line_with_real_report_opens_declared_report(tmp_path, monkeypatch):
    # [MC-03 返工 / ACCEPT H3+S4] 日志目录与声明报告都真实存在：detail/「打开」
    # 必须指向声明报告，而不是靠前的日志目录。
    from test_b116_single_function import _App, _install_run, _make_page

    logdir = tmp_path / "日志 目录C"
    logdir.mkdir()
    report = tmp_path / "卷 四" / "合并 第8,9卷 v4.0(定).pdf"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"%PDF-1.4")
    page, _root = _make_page(tmp_path, _App())
    line = (f"完成。检查 12 篇（状态 0）"
            f"（日志目录：{logdir}）（报告：{report}）")
    _install_run(monkeypatch, page, 0, line)

    asyncio.run(page._run(page._build_call()))

    assert page._result.detail.value == str(report)
    assert page._result.open_btn.visible is True
    assert page._result.copy_btn.visible is True
    assert page._artifact_row.visible is True


# ── MC-03 返工第 3 轮：一行两个标签时「打开」必须指向声明报告（GUI 级） ──────
def test_two_label_line_opens_declared_report_not_dir(tmp_path, monkeypatch):
    # [MC-03 返工3 / wb02 B3 GUI] 报告标签 + 目录标签同行：detail/「打开」指向
    # 声明报告，而不是目录标签的日志目录。
    from test_b116_single_function import _App, _install_run, _make_page

    report = tmp_path / "报告 终.pdf"
    report.write_bytes(b"%PDF-1.4")
    logdir = tmp_path / "日志 目录"
    logdir.mkdir()
    page, _root = _make_page(tmp_path, _App())
    _install_run(monkeypatch, page, 0, f"完成。报告：{report}（目录：{logdir}）")

    asyncio.run(page._run(page._build_call()))

    assert page._result.detail.value == str(report)
    assert page._result.open_btn.visible is True
    assert page._result.copy_btn.visible is True
