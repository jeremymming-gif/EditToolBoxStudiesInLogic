"""R3 Task5：初排父页 R3「正常转换闭环」的失败测试（TDD RED）。

针对规格 §3/§10 与执行卡 Task5 的锁定契约写断言：

* 初排父页必须暴露 ``SinglePage.initial_ai_inputs()``（与普通转换同组 kwargs）
  与 ``SinglePage._build_ai_initial_call()``（普通转换 + R3 三选项走 CLI 普通路径）；
* 桥侧 ``convert_call`` 的三个 R3 选项（ai_taskbook_version / ai_execute /
  ai_work_dir）按位置拼 argv，普通转换路径保持不变；
* 只读内容桥 / 交付桥 / 结果字段投影 / 空 adopt / 状态解析 / 失败码 / 恢复任务
  的语义按契约透传。

当前产品源码是骨架：``SinglePage.initial_ai_inputs`` /
``SinglePage._build_ai_initial_call`` 尚未出现，``BibAiAssistView.set_work_dir``
与 ``request_seq`` 也尚未出现，本文件应当**导入成功但部分 FAIL**。
不修改任何产品源码求绿；全部离线，不建窗口、不跑真引擎、不读稿件仓。
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from services import engine_bridge as EB
from ui import bib_ai_assist as A
from ui import pages as P
from ui.tokens import Tokens

from test_b114_polish import _FakeApp  # 已验证的假宿主（t/store/page/mark/toast）


# ── 通用假宿主 / 工具 ──────────────────────────────────────────────────────
class _App(_FakeApp):
    """b114 假壳补最小面：set_option 落 store、copy_text 供视图/结果卡。"""

    def __init__(self) -> None:
        super().__init__()
        self.notes_repo = ""

    def set_option(self, key: str, value) -> None:
        self.store.set(f"options.{key}", value)

    def copy_text(self, text) -> None:
        self.toasts.append(f"copied:{text}")


def _single(*, source: str = "/tmp/初排稿.tex", out: str = "/tmp/out") -> P.SinglePage:
    page = P.SinglePage(_App())
    page.build()
    page.set_field("single_source", source)
    page.set_field("single_out", out)
    return page


def _fake_head(monkeypatch) -> list[str]:
    """假宿主：base_cmd 不再找真实引擎安装，只拼固定 argv 头。"""
    head = ["/fake/engine", "convert.py"]
    monkeypatch.setattr(EB, "base_cmd", lambda entry: list(head))
    return head


def _tail(call: EB.EngineCall, head: list[str]) -> list[str]:
    assert call.argv[: len(head)] == head
    return call.argv[len(head):]


def _completed(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["fake-engine"], returncode=code, stdout=stdout, stderr=stderr
    )


# ── 桥：convert_call 三选项位置与默认值 ────────────────────────────────────
def test_convert_call_appends_r3_options_after_dry_run_before_extra(monkeypatch):
    head = _fake_head(monkeypatch)
    call = EB.convert_call(
        source="/tmp/初排稿.tex", out="/tmp/out", dry_run=True,
        extra="--custom-flag",
        ai_taskbook_version=3, ai_execute="bib_verify,typeset",
        ai_work_dir="/tmp/任务根",
    )
    tail = _tail(call, head)
    assert tail[tail.index("--dry-run") + 1: tail.index("--custom-flag")] == [
        "--ai-taskbook-version", "3",
        "--ai-execute", "bib_verify,typeset",
        "--ai-work-dir", "/tmp/任务根",
    ]


def test_convert_call_plain_path_has_no_r3_options(monkeypatch):
    """普通转换（未传三选项）argv 不得混入任何 R3 选项。"""
    head = _fake_head(monkeypatch)
    call = EB.convert_call(source="/tmp/初排稿.tex", out="/tmp/out", dry_run=False)
    tail = _tail(call, head)
    for flag in ("--ai-taskbook-version", "--ai-execute", "--ai-work-dir", "--ai-workflow"):
        assert flag not in tail


# ── 桥：真调用形态，adopt=[] 不被 or defaults 重开 ────────────────────────
def test_convert_call_empty_ai_execute_not_reopened_by_defaults(monkeypatch):
    head = _fake_head(monkeypatch)
    # ai_execute="" 明确传入：桥不得用 EXECUTE_DEFAULTS 重新拼默认值。
    call = EB.convert_call(source="/tmp/a.tex", out="/tmp/b.pdf", ai_execute="")
    assert "--ai-execute" not in _tail(call, head)


def test_ai_preview_call_empty_adopt_has_no_adopt_flag(monkeypatch):
    head = _fake_head(monkeypatch)
    with_adopt = EB.ai_preview_call(work_dir="/tmp/任务根", adopt=[])
    without_adopt = EB.ai_preview_call(work_dir="/tmp/任务根")
    for call in (with_adopt, without_adopt):
        assert "--adopt" not in _tail(call, head)
    # 明确传空列表与非空列表形态不同：非空一定带 --adopt。
    filled = EB.ai_preview_call(work_dir="/tmp/任务根", adopt=["bib_verify", "typeset"])
    assert _tail(filled, head)[_tail(filled, head).index("--adopt") + 1] == "bib_verify,typeset"


# ── 桥：只读内容桥 / 交付桥（已合流部分，作回归 guard）─────────────────────
def test_ai_taskbook_call_argv(monkeypatch):
    head = _fake_head(monkeypatch)
    tail = _tail(EB.ai_taskbook_call(work_dir="/tmp/任务根", task_id="T-42"), head)
    assert tail[:5] == ["--ai-workflow", "--work-dir", "/tmp/任务根", "taskbook", "--json"]
    assert tail[5:] == ["--task-id", "T-42"]


def test_ai_deliver_call_argv(monkeypatch):
    head = _fake_head(monkeypatch)
    tail = _tail(EB.ai_deliver_call(work_dir="/tmp/任务根", preview_id="pv-1",
                                    use_original=True), head)
    assert tail[:5] == ["--ai-workflow", "--work-dir", "/tmp/任务根", "deliver", "--json"]
    assert tail[5:] == ["--preview-id", "pv-1", "--use-original"]


def test_taskbook_result_fields_guard():
    fields = EB.taskbook_result_fields({
        "ok": True,
        "json": {"task_id": "T-42", "template_version": 3, "text": "任务书正文",
                 "chars": 4, "path": "/tmp/task.md", "sha256": "abc"},
    })
    assert fields == {
        "ok": True, "task_id": "T-42", "template_version": "3",
        "text": "任务书正文", "chars": 4, "path": "/tmp/task.md", "sha256": "abc",
    }


# ── 页面：普通路径不变（回归 guard）────────────────────────────────────────
def test_plain_build_call_unchanged_and_without_r3_options(monkeypatch):
    """普通转换仍经 AiPanel.build_call → EB.convert_call，无 R3 三选项。"""
    page = _single()
    captured: dict = {}

    def fake_convert(**kwargs):
        captured.update(kwargs)
        return EB.EngineCall(entry="convert.py", argv=["/fake/engine", "convert.py"], label="fake")

    monkeypatch.setattr(EB, "convert_call", fake_convert)
    page._build_call()
    assert captured["source"] == "/tmp/初排稿.tex"
    assert captured["out"] == "/tmp/out"
    for r3_key in ("ai_taskbook_version", "ai_execute", "ai_work_dir"):
        assert r3_key not in captured


# ── RED①：初排父页暴露 initial_ai_inputs，与普通转换同组 kwargs ────────────
def test_initial_ai_inputs_matches_plain_build_call_kwargs(monkeypatch):
    page = _single()
    captured: dict = {}

    def fake_convert(**kwargs):
        captured.update(kwargs)
        return EB.EngineCall(entry="convert.py", argv=["/fake/engine", "convert.py"], label="fake")

    monkeypatch.setattr(EB, "convert_call", fake_convert)
    page._build_call()
    plain = dict(captured)

    initial = page.initial_ai_inputs()
    assert isinstance(initial, dict)
    assert initial == plain  # 同组参数，不额外塞 R3、preview_path 以外的键
    for r3_key in ("ai_taskbook_version", "ai_execute", "ai_work_dir"):
        assert r3_key not in initial
    assert "preview_path" not in initial


# ── RED②：_build_ai_initial_call 走普通 CLI + R3 三选项 ────────────────────
def _aiw_view(*, execute: dict | None = None):
    """构造挂在假宿主上的 BibAiAssistView，并按需设置执行组。"""
    view = A.BibAiAssistView(_C(tokens=Tokens("light")), bridge=_B())
    for key, value in (execute or {}).items():
        view.set_execute(key, value)
    return view


def _build_ai_initial(page, view, work_dir: str):
    """调用初排 R3 转换入口。不钉死形参名/是否先挂载 view，覆盖合理实现形态。"""
    fn = page._build_ai_initial_call
    page._aiw_view = view  # 实现可能从页面上取视图，而非从入参取
    attempts = (
        ((), {"view": view, "work_dir": work_dir}),
        ((), {"work_dir": work_dir, "execute_scope": view.execute_scope()}),
        ((view, work_dir), {}),
        ((work_dir,), {}),
        ((), {"work_dir": work_dir}),
    )
    errors = []
    for args, kwargs in attempts:
        try:
            return fn(*args, **kwargs)
        except TypeError as exc:
            errors.append(str(exc))
    pytest.fail("无法以合理形态调用 _build_ai_initial_call：" + " | ".join(errors))


def test_build_ai_initial_call_argv(monkeypatch):
    page = _single()
    head = _fake_head(monkeypatch)
    view = _aiw_view(execute={"verify": True, "proofcheck": True, "textfix": False})
    scope = view.execute_scope()
    scope_str = ",".join(scope)
    assert scope_str == "bib_verify,typeset"  # CATEGORY_MAP 映射，内部键不下传

    tail = _tail(_build_ai_initial(page, view, "/tmp/任务根"), head)
    assert tail[tail.index("--ai-taskbook-version") + 1] == "3"
    assert tail[tail.index("--ai-execute") + 1] == scope_str
    assert tail[tail.index("--ai-work-dir") + 1] == "/tmp/任务根"
    # 必须是普通转换 CLI，不是 AI 工作流的伪装（不得出现 --ai-workflow prepare）。
    assert "--ai-workflow" not in tail
    assert "prepare" not in tail


# ── RED③：execute_scope 全关 → ai_execute="" → argv 无 --ai-execute ──────
def test_build_ai_initial_call_empty_execute_scope(monkeypatch):
    page = _single()
    head = _fake_head(monkeypatch)
    view = _aiw_view(execute={key: False for key in view_keys()})
    assert view.execute_scope() == []
    assert "--ai-execute" not in _tail(_build_ai_initial(page, view, "/tmp/任务根"), head)


def view_keys():
    """视图执行组的全部内部键（避免测试写死默认值）。"""
    return list(A.EXECUTE_DEFAULTS)


# ── 状态解析 / 失败码 / rc=0 不等于业务成功 ─────────────────────────────────
def test_parse_status_blocked_phase_task_and_preview():
    out = A.parse_status([
        "phase=preview", "task_id=T-42", "preview_id=pv-1",
        "blocked=a；b;c", "keys=7", "entries=3", "未知行",
    ])
    assert out["phase"] == "preview"
    assert out["task_id"] == "T-42"
    assert out["preview_id"] == "pv-1"
    assert out["blocked"] == ["a", "b", "c"]


def test_fail_code_nonzero_rc_writes_failure_and_not_success():
    host = _Host()
    view = A.BibAiAssistView(host, bridge=_B())
    view.log = ["开始", "不认识的类别：bad"]
    view.phase = "waiting"
    view._fail_code("预览", 2)
    assert "失败" in view.error
    assert "不认识的类别：bad" in view.error
    assert view.running is False
    assert view.phase != "preview"
    assert any(event == "bib_ai_failed" for event, _ in host.marks)


def test_rc_zero_but_business_failure_not_success(monkeypatch):
    """rc=0 不等于业务成功：命令层 ok（跑通+解出 JSON）与 payload 的业务 ok 是两回事。

    视图/页面读业务成功必须看 ``result["json"]["ok"]``，不能拿 rc==0 或
    ``result["ok"]`` 当业务成功——后者只说明命令跑通且 stdout 里有 JSON。
    """
    fake = EB.EngineCall(entry="convert.py", argv=["fake"], label="x")
    monkeypatch.setattr(EB.subprocess, "run",
                        lambda *a, **k: _completed('{"ok": false, "error": "业务失败"}', "", 0))
    result = EB.read_json_command(fake)
    # 命令层：rc=0 且解出 dict → read_json_command 的 ok 为真。
    assert result["ok"] is True
    # 业务层：成功与否只由 payload 决定，rc=0 时仍可以是业务失败。
    assert isinstance(result["json"], dict)
    assert result["json"]["ok"] is False
    assert result["json"]["error"] == "业务失败"


# ── RED④：恢复任务 set_work_dir 只回填字段 + request_seq，不重发桥调用 ──────
def test_restore_work_dir_does_not_prepare_or_send():
    host = _Host()
    bridge = _B()
    view = A.BibAiAssistView(host, bridge=bridge)
    before = len(bridge.calls)
    before_seq = getattr(view, "request_seq", None)
    assert hasattr(view, "set_work_dir"), "恢复任务需要 set_work_dir 回填引擎工作区"
    assert before_seq is not None, "恢复任务需要 request_seq 序号用于新鲜度校验"
    view.set_work_dir("/tmp/引擎返回的工作区")
    assert view.work_dir == "/tmp/引擎返回的工作区"
    assert getattr(view, "request_seq") != before_seq
    assert len(bridge.calls) == before  # 不 prepare、不 re-import、不 re-send


# ── 最小视图宿主 / 假桥（供上面构造 BibAiAssistView，抄 c 测试）────────────
class _C:
    def __init__(self, *, tokens) -> None:
        self.t = tokens
        self.page = SimpleNamespace(run_task=lambda fn: None, _render=lambda: None)
        self.app = SimpleNamespace(store=_NopStore(), notes_repo="", window_w=1160,
                                   copy_text=lambda text: None)
        self.marks: list = []

    def mark(self, event, **kwargs) -> None:
        self.marks.append((event, kwargs))

    def notify(self, text) -> None:
        pass

    def set_view(self, view) -> None:
        pass

    def _render(self) -> None:
        pass


class _NopStore:
    def get(self, key, default=None):
        return default

    def set(self, key, value) -> None:
        pass


class _B:
    """最小假桥：只记录工厂调用，run_call 按 code 控制 rc。"""

    def __init__(self, *, code: int = 0) -> None:
        self.code = code
        self.calls: list = []

    def _call(self, name, kwargs):
        self.calls.append((name, dict(kwargs)))
        return SimpleNamespace(entry="engine.py", argv=[name], label=name,
                               display=f"engine.py {name}", task_text="TASK",
                               task_id="T-42", preview_id="pv-1", blocked=[],
                               keys=7, entries=3, backup="b.bak")

    def __getattr__(self, name):
        if name.endswith("_call") or name in ("prepare", "import", "preview", "apply",
                                               "undo", "request", "status"):
            def factory(**kwargs):
                return self._call(name, kwargs)
            return factory
        raise AttributeError(name)

    async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                       stdin_text=None):
        self.calls.append(("run", {}))
        for line in ("phase=preview", "task_id=T-42", "keys=7", "entries=3"):
            if on_line is not None:
                on_line(line)
        if on_proc is not None:
            on_proc(object())
        return self.code


class _Host(_C):
    def __init__(self) -> None:
        super().__init__(tokens=Tokens("light"))


def _click_and_run(view, pending, action):
    """Run the callback registered on the actual generated Flet control."""
    import asyncio

    view.body_only(compact=True)
    button = view._action_buttons[action]
    assert not button.disabled
    button.on_click(None)
    assert pending
    return asyncio.run(pending.pop(0)())


def test_initial_parent_controls_prepare_import_preview_deliver_and_undo(monkeypatch, tmp_path):
    head = _fake_head(monkeypatch)
    source = tmp_path / "anonymous.tex"
    out = tmp_path / "out"
    page = _single(source=str(source), out=str(out))
    page.app.store.set("options", {"convert_compile": True, "convert_clean": True})
    page._rerender_flow = lambda: None
    page._toggle_ai_workflow(None)
    view = page._aiw_view
    pending = []
    view._task = pending.append
    page._rerender_flow = lambda: view.body_only(compact=True)
    calls = []
    state = {"task_id": "T-frozen", "phase": "prepared", "preview_id": "",
             "execute": ["bib_verify", "typeset"]}

    async def run(call, *, on_line, stdin_text, **kwargs):
        argv = _tail(call, head)
        calls.append((argv, stdin_text))
        if "--ai-workflow" not in argv:
            work = argv[argv.index("--ai-work-dir") + 1]
            on_line(f"task_id={state['task_id']}")
            on_line(f"work_dir={work}")
            on_line(f"taskbook={work}/task.md")
            return 0
        op = next(op for op in ("status", "import", "preview", "deliver", "undo") if op in argv)
        result = {"op": op, "ok": True}
        if op == "status":
            result.update(state)
        elif op == "import":
            state.update(phase="imported", imported=True)
        elif op == "preview":
            state.update(phase="previewed", preview_id="pv-frozen", adopt=[])
            result.update(phase="previewed", preview_id="pv-frozen")
        elif op == "deliver":
            result["final_dir"] = str(out / "Anonymous")
        on_line(json.dumps(result))
        return 0

    monkeypatch.setattr(EB, "run_call", run)
    _click_and_run(view, pending, "prepare")
    assert view.phase == "waiting"
    assert view.task_id == "T-frozen"
    assert not view._action_buttons["copy_task"].disabled
    pending.clear()  # Prefetch uses the separately tested read-only subprocess bridge.
    argv = calls[0][0]
    assert argv[argv.index("--source") + 1] == str(source)
    assert argv[argv.index("--out") + 1] == str(out)
    assert "--compile" in argv and "--clean" in argv
    assert "--ai-taskbook-version" in argv and "--target-bib" not in argv
    assert "--ai-workflow" not in argv
    assert calls[1][0][-1] == "status" and "--json" in calls[1][0]
    assert view.work_dir.startswith(str(tmp_path / ".out.aiw"))

    view.body_text = "% TASK-ID: T-frozen\nfirst reply\n"
    _click_and_run(view, pending, "import")
    argv, stdin_text = calls[-1]
    assert argv[argv.index("--task-id") + 1] == "T-frozen"
    assert argv[argv.index("--source-kind") + 1] == "ai_subset"
    assert stdin_text == view.body_text
    view.set_accept("verify", False)
    view.set_accept("proofcheck", False)
    _click_and_run(view, pending, "preview")
    argv = calls[-2][0]
    assert argv[argv.index("--adopt") + 1] == ""
    assert "--reply" not in argv and view.can_apply()

    view.set_accept("verify", True)
    assert not view.can_apply() and view.preview_id == ""
    _click_and_run(view, pending, "refresh")
    pending.clear()
    assert not view.can_apply() and view.accept["verify"]
    _click_and_run(view, pending, "preview")
    _click_and_run(view, pending, "apply")
    assert "deliver" in calls[-1][0] and "apply" not in calls[-1][0]
    assert calls[-1][0][calls[-1][0].index("--preview-id") + 1] == "pv-frozen"
    assert view.phase == "applied"
    _click_and_run(view, pending, "undo")
    assert "undo" in calls[-1][0]
    assert view.phase == "stale"
    assert sum("--ai-taskbook-version" in argv for argv, _ in calls) == 1


def test_initial_original_delivery_button_reads_business_result(monkeypatch):
    _fake_head(monkeypatch)
    page = _single()
    page._rerender_flow = lambda: None
    page._toggle_ai_workflow(None)
    view = page._aiw_view
    view.work_dir = "/tmp/work"
    view.task_id = "T-original"
    view.scope_frozen = True
    view.phase = "waiting"
    pending = []
    view._task = pending.append

    async def run(call, *, on_line, **kwargs):
        assert call.argv[-4:] == [view.work_dir, "deliver", "--json", "--use-original"]
        on_line(json.dumps({"op": "deliver", "ok": False, "error": "编译失败"}))
        return 0

    monkeypatch.setattr(EB, "run_call", run)
    _click_and_run(view, pending, "deliver_original")
    assert view.phase == "waiting"
    assert "编译失败" in view.error
    assert not any("初排已交付" in toast for toast in page.app.toasts)


def test_initial_actual_cli_from_controls_and_read_only_resume(monkeypatch, tmp_path):
    """Anonymous source runs through actual CLI, with original output then undo."""
    import sys
    from pathlib import Path

    engine = Path(__file__).resolve().parents[2] / "Engine"
    monkeypatch.setattr(EB, "base_cmd",
                        lambda entry: [sys.executable, "-B", str(engine / entry)])
    monkeypatch.setenv("SLCN_MANUSCRIPTS", str(tmp_path / "unavailable"))
    source = tmp_path / "anonymous.tex"
    source_bytes = br"""\documentclass{article}
\title{An Anonymous Example}
\author{A. Example}
\begin{document}
\maketitle
A short anonymous example.
\end{document}
"""
    source.write_bytes(source_bytes)
    out = tmp_path / "out"
    page = _single(source=str(source), out=str(out))
    page.app.store.set("options", {"convert_compile": False, "convert_force": True})
    page.app.store.set("form.convert_lang", "en")
    page.set_field("convert_name", "Anonymous")
    page._rerender_flow = lambda: None
    page._toggle_ai_workflow(None)
    view = page._aiw_view
    pending = []
    view._task = pending.append
    _click_and_run(view, pending, "prepare")
    assert not view.error, view.error
    assert view.phase == "waiting" and view.task_id
    assert not (out / "Anonymous").exists()
    assert Path(view.taskbook_path).is_file()
    pending.clear()

    restored = A.BibAiAssistView(P._SingleAiHost(page), purpose="initial")
    restored._task = pending.append
    restored.activate()
    assert restored.task_id == view.task_id
    assert len(pending) == 1
    import asyncio
    asyncio.run(pending.pop(0)())
    assert restored.phase == "waiting"
    pending.clear()
    _click_and_run(restored, pending, "deliver_original")
    assert not restored.error, restored.error
    assert restored.phase == "applied"
    final = Path(restored._final_dir)
    assert final.is_dir() and list(final.glob("*.tex"))
    assert source.read_bytes() == source_bytes
    _click_and_run(restored, pending, "undo")
    assert restored.phase == "stale", restored.error
    assert not final.exists()
    assert source.read_bytes() == source_bytes


def test_library_actual_cli_defaults_to_subset_and_registers_explicit_copies(monkeypatch, tmp_path):
    import sys
    from pathlib import Path

    from test_taskbook_copy_delivery_20261118 import _Host

    engine = Path(__file__).resolve().parents[2] / "Engine"
    monkeypatch.setattr(EB, "base_cmd",
                        lambda entry: [sys.executable, "-B", str(engine / entry)])
    monkeypatch.setenv("SLCN_MANUSCRIPTS", str(tmp_path / "unavailable"))
    bib = tmp_path / "input.bib"
    tex = tmp_path / "associated.tex"
    old_bib = "@book{example, author={Anon}, title={Old}, year={2020}}\n"
    bib.write_text(old_bib, encoding="utf-8")
    old_tex = "An anonymous body \\cite{example}.\n"
    tex.write_text(old_tex, encoding="utf-8")
    host = _Host()
    view = A.BibAiAssistView(host, purpose="library")
    view.target_bib = str(bib)
    view.body_only(compact=True)
    assert view._execute_boxes["proofcheck"].disabled
    assert view._accept_boxes["textfix"].disabled
    view.tex_path = str(tex)
    view.body_only(compact=True)
    assert not view._execute_boxes["proofcheck"].disabled
    assert view._accept_boxes["textfix"].disabled

    dialogs = []
    monkeypatch.setattr(view, "_confirm", lambda **kwargs: dialogs.append(kwargs))
    view._register_copy("bib_out")
    assert not view.bib_out
    dialogs.pop()["callback"]()
    view._register_copy("tex_out")
    assert not view.tex_out
    dialogs.pop()["callback"]()
    destination = Path(view.bib_out)
    assert destination != bib and not destination.exists()

    pending = []
    view._task = pending.append
    _click_and_run(view, pending, "prepare")
    assert not view.error and view.task_id, view.error
    assert not destination.exists() and not Path(view.tex_out).exists()
    pending.clear()
    view.body_text = (f"% TASK-ID: {view.task_id}\n"
                      "```bibtex\n@book{example, author={Anon}, title={New}, year={2020}}\n```\n")
    _click_and_run(view, pending, "import")
    _click_and_run(view, pending, "preview")
    assert view.can_apply(), (view.error, view.blocked)
    _click_and_run(view, pending, "apply")
    assert view.phase == "applied", view.error
    assert "New" in destination.read_text(encoding="utf-8")
    assert bib.read_text(encoding="utf-8") == old_bib
    assert tex.read_text(encoding="utf-8") == old_tex
    _click_and_run(view, pending, "undo")
    assert view.phase == "stale" and not destination.exists(), view.error


@pytest.mark.parametrize("purpose", ["library", "initial"])
def test_replace_reply_opens_once_and_imports_only_on_confirm(monkeypatch, tmp_path, purpose):
    import asyncio

    import flet as ft

    from test_bib_ai_assist_c_wiring_20261117 import _Host
    from ui.bib_normalize import NormalizeView

    head = _fake_head(monkeypatch)
    if purpose == "initial":
        parent = _single()
        parent._toggle_ai_workflow()
        view = parent._aiw_view
        page = parent.page
    else:
        host = _Host()
        parent = NormalizeView(host)
        parent.build()
        view = parent._ai_view
        page = host.page

    dialogs = []
    pending = []
    calls = []
    guard = SimpleNamespace(
        _dialogs=SimpleNamespace(controls=dialogs, update=lambda: None),
        _prepare_dialog=lambda dialog: None,
    )
    monkeypatch.setattr(page, "show_dialog", lambda dialog: ft.Page.show_dialog(guard, dialog))
    monkeypatch.setattr(page, "pop_dialog", dialogs.pop)
    monkeypatch.setattr(page, "run_task", pending.append)
    if purpose == "library":
        monkeypatch.setattr(host, "run_task_ui", pending.append)
    monkeypatch.setattr(A.C, "_open_dialogs", [])

    async def run(call, *, on_line, stdin_text, **kwargs):
        calls.append((_tail(call, head), stdin_text))
        on_line(json.dumps({"op": "import", "ok": True}))
        return 0

    monkeypatch.setattr(EB, "run_call", run)
    view.work_dir = str(tmp_path)
    view.task_id = "T-active"
    view.phase = "waiting"
    view._has_active_reply = True
    view.body_text = "replacement reply"

    _click_and_run(view, pending, "import")
    assert len(dialogs) == 1
    assert not calls and not pending
    dialogs[0].actions[0].on_click(None)
    assert not dialogs and not calls and not pending
    assert not A.C._open_dialogs

    _click_and_run(view, pending, "import")
    assert len(dialogs) == 1
    dialogs[0].actions[1].on_click(None)
    assert not dialogs and not calls
    assert not A.C._open_dialogs
    assert len(pending) == 1
    asyncio.run(pending.pop()())
    assert len(calls) == 1 and not pending
    argv, reply = calls[0]
    assert "import" in argv and "--replace-active" in argv
    assert reply == "replacement reply"
