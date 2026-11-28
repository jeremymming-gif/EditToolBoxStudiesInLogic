"""批 116 运行语义（D3 改版后：无整期 run_all，只有单步 run_step + 串行 _run_sequence）。

不启动 GUI、不跑真引擎：run_call 用桩，引擎目录用临时桩目录（同 test_b109_w3）。

D3 语义变更（本文件同步）：
- `ASM.run_all` / `DEFAULT_RUN_ALL_KEYS` / `_automatic_compile` / `compile_plan`
  全部下线：GUI 只留「刷新页码并编译」单动作，不再有一键整期入口；
- 唯一执行器是 `ASM._run_sequence(page, keys, calls)`：不清日志、每步段头/段尾、
  rc>=2 停后续、rc1 警告继续、取消标 cancelled；
- 用户点击走 `ASM.run_step(page, key)`：校验 → dirpush 仓库根确认 → `_start`。
"""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from services import engine_bridge as EB
from ui import assembly as ASM
from ui import cleanup as CLEANUP
from ui import components as C
from ui.tokens import Tokens

STEP_KEYS = ("infocheck", "toc", "order", "refresh", "merge", "cleantemp", "dirpush")


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    root = tmp_path / "Engine"
    root.mkdir()
    for entry in EB.ENTRIES:
        (root / entry).write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("P2_ENGINE", str(root))
    for name in ("P2_ENGINE_EXE", "SLCN_ENGINE_EXE", "P2_ENGINE_PYTHON", "SLCN_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    return root


class _Store:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def update_options(self, **kwargs):
        self.data.setdefault("options", {}).update(kwargs)

    def set_recent(self, key, value):
        if value:
            self.data.setdefault("recent", {})[key] = value


class _App:
    def __init__(self):
        self.running = False
        self.cancel_requested = False
        self.auto_confirm = True
        self.notes_repo = ""
        self.last_line = ""
        self.store = _Store()
        self.toasts: list[str] = []
        self.marks: list[tuple] = []
        self.proc = None
        self.engine_status = SimpleNamespace(ok=True, version="v", mode="dev",
                                             engine_dir="", describe=lambda: "stub",
                                             problems=[])

    def toast(self, message):
        self.toasts.append(message)

    def mark(self, event, **fields):
        self.marks.append((event, fields))

    def begin_run(self, call):
        self.running = True
        self.cancel_requested = False
        self.last_line = ""

    def finish_run(self, call, code):
        self.running = False

    def track_proc(self, proc):
        self.proc = proc

    def stop_run(self):
        self.cancel_requested = True


class _Log:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []
        self.clear_calls = 0

    def add_line(self, value, level="info", summary=True):
        self.lines.append((str(value), level))

    def clear(self):
        self.clear_calls += 1
        self.lines.clear()


class _Result:
    def __init__(self):
        self.visible = False
        self.text = ""
        self.level = ""
        self.artifact = ""

    def reset(self):
        self.visible = False
        self.text = self.level = self.artifact = ""

    def show(self, result, detail="", level="info", artifact=""):
        self.visible = True
        self.text = result
        self.level = level
        self.artifact = artifact


class _FletPage:
    def __init__(self):
        self.tasks: list[tuple] = []
        self.dialogs: list = []

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))
        asyncio.run(handler(*args))

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        return self.dialogs.pop() if self.dialogs else None

    def update(self):
        pass


class _Page:
    STEPS = [{"key": key, "desc": key, "title": key, "icon": ""} for key in STEP_KEYS]

    def __init__(self):
        self.app = _App()
        self.t = Tokens()
        self._fields: dict[str, str] = {}
        self._status: dict[str, str] = {key: "idle" for key in STEP_KEYS}
        self._log = _Log()
        self._result = _Result()
        self._refresh_count = 0
        self.page = _FletPage()
        self._engine_lines: list[str] = []

    def field(self, key):
        return self._fields.get(key, "")

    def set_field(self, key, value):
        self._fields[key] = value

    def mark(self, event, **fields):
        self.app.marks.append((event, fields))

    def _refresh_steps(self):
        self._refresh_count += 1

    def _on_line(self, line):
        self._engine_lines.append(line)

    def _issue_dir(self):
        return self.field("issue_dir")


def make_page(tmp_path, *, report=True):
    page = _Page()
    page.set_field("issue_dir", str(tmp_path))
    if report:
        (tmp_path / "信息检查.txt").write_text("stub", encoding="utf-8")
    return page


def install_run(monkeypatch, page, codes):
    """桩掉 EB.run_call：按 codes 顺序返回 rc；"cancel"/异常 走特殊路径。"""
    calls: list[EB.EngineCall] = []

    async def fake_run_call(call, *, on_line=None, on_proc=None, notes="", **kwargs):
        calls.append(call)
        index = len(calls) - 1
        code = codes[index] if index < len(codes) else 0
        if code == "cancel":
            page.app.stop_run()
            return 0
        if isinstance(code, BaseException):
            raise code
        return int(code)

    monkeypatch.setattr(EB, "run_call", fake_run_call)
    return calls


def states(page):
    return [item["state"] for item in page._run_results]


def run(page, keys, calls=None):
    return asyncio.run(ASM._run_sequence(page, list(keys), calls))


# ── D3：一键整期入口已下线 ───────────────────────────────────────────────
def test_run_all_entry_is_gone_after_d3():
    assert not hasattr(ASM, "run_all")
    assert not hasattr(ASM, "run_all_demo")
    assert not hasattr(ASM, "DEFAULT_RUN_ALL_KEYS")
    assert not hasattr(ASM, "_run_all_keys")
    assert not hasattr(ASM, "_automatic_compile")
    assert not hasattr(ASM, "compile_plan")
    assert not hasattr(ASM, "compile_event")
    # 唯一执行器是 async 的 _run_sequence（不是 run_all 的同步外壳）。
    assert inspect.iscoroutinefunction(ASM._run_sequence)


def test_sequence_runs_given_keys_in_order(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [0, 0, 0])

    run(page, ["infocheck", "toc", "merge"])

    assert [call.entry for call in calls] == [
        "infocheck.py", "toc.py", "mergepdf.py",
    ]
    assert [item["key"] for item in page._run_results] == [
        "infocheck", "toc", "merge",
    ]
    assert states(page) == ["done"] * 3
    assert page._status["cleantemp"] == "idle" and page._status["dirpush"] == "idle"


def test_sequence_explicit_steps_keep_normal_step_semantics(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path, report=False)
    page._cleanup = CLEANUP.CleanupPanel(page)
    page.set_field("dirpush_out", str(tmp_path / "out"))
    page.app.store.set("form.cleantemp_mode", "delivery")
    page.app.store.update_options(cleantemp_dry_run=False, dirpush_dry_run=True)
    calls = install_run(monkeypatch, page, [0, 0])

    run(page, ["cleantemp", "dirpush"])

    # T3/C14：显式勾选 cleantemp 也只走一次 --mode daily；不再串 tocwork、
    # 不带旧档位，也不被旧 dry_run 设置改成只预演。
    assert [call.entry for call in calls] == ["cleantemp.py", "dirpush.py"]
    clean = calls[0].argv
    assert "--mode" in clean and "daily" in clean
    assert "--dry-run" not in clean and "--trash" not in clean
    # D1 删掉三个只预演开关后，旧 options.dirpush_dry_run 也不再影响 argv。
    assert "--dry-run" not in calls[1].argv
    assert page._status["cleantemp"] == "done"
    assert page._status["dirpush"] == "done"

    # 单步走 run_step（唯一 GUI 入口）：同样保留单步语义。
    page2 = make_page(tmp_path)
    only_merge = install_run(monkeypatch, page2, [0])
    ASM.run_step(page2, "merge")
    assert [call.entry for call in only_merge] == ["mergepdf.py"]
    assert states(page2) == ["done"]


def test_run_sequence_stops_on_rc2_and_marks_rest_skipped(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [0, 2])

    run(page, ["infocheck", "toc", "merge"])

    assert len(calls) == 2  # toc 失败后不再启动 merge
    assert page._status["toc"].exit_code == 2
    assert page._status["toc"] == "error"
    assert page._status["merge"] == "skipped"
    assert states(page) == ["done", "error", "skipped"]
    assert [item["rc"] for item in page._run_results] == [0, 2, None]


def test_run_sequence_rc1_warns_and_continues(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [1, 0, 0])

    run(page, ["infocheck", "toc", "merge"])

    assert len(calls) == 3
    assert states(page) == ["warn", "done", "done"]
    assert page._status["infocheck"] == "warn" and page._status["infocheck"].exit_code == 1
    assert page._result.level == "ok"  # 最后一步的结果条


def test_log_is_never_cleared_and_gets_section_headers(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    page._log.add_line("旧日志", "info")
    install_run(monkeypatch, page, [0, 1, 0])

    run(page, ["infocheck", "toc", "merge"])

    assert page._log.clear_calls == 0
    assert page._log.lines[0] == ("旧日志", "info")
    texts = [text for text, _ in page._log.lines]
    # D7：7 个底层步骤 → 段头分母是 07（不是旧的 06）。
    assert any(text.startswith("▶ 01/07") and "infocheck" in text for text in texts)
    assert any(text.startswith("▶ 05/07") and "merge" in text for text in texts)
    assert any(text.startswith("■ 02/07") and "rc=1" in text for text in texts)
    assert len([text for text in texts if text.startswith("▶")]) == 3


def test_status_metadata_and_hooks_written_back(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    artifact = tmp_path / "merged.pdf"
    artifact.write_bytes(b"%PDF-1.4\n")

    async def fake_run_call(call, *, on_line=None, on_proc=None, notes="", **kwargs):
        page.app.last_line = str(artifact)
        if on_line:
            on_line("引擎输出")
        return 0

    monkeypatch.setattr(EB, "run_call", fake_run_call)
    seen: list[str] = []
    original = ASM.after_step

    def spy(page_, key, *args, **kwargs):
        seen.append(key)
        return original(page_, key, *args, **kwargs)

    monkeypatch.setattr(ASM, "after_step", spy)

    run(page, ["infocheck", "toc", "merge"])

    status = page._status["merge"]
    assert isinstance(status, ASM.StepStatus) and status == "done"
    assert status.exit_code == 0
    assert isinstance(status.duration_ms, (int, float)) and status.duration_ms >= 0
    assert status.started_at and status.finished_at
    assert status.artifact == str(artifact)
    assert status.as_dict()["state"] == "done"
    assert json.loads(json.dumps(status.as_dict()))["exit_code"] == 0
    assert seen == ["infocheck", "toc", "merge"]
    assert page._refresh_count >= 7  # 3 步 × (开始+结束) + 收尾一次
    assert page._engine_lines == ["引擎输出"] * 3


def test_cancel_marks_current_and_remaining_cancelled(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [0, "cancel", 0])

    run(page, ["infocheck", "toc", "merge"])

    assert len(calls) == 2  # 取消后不启动 merge
    assert page._status["infocheck"] == "done"
    assert page._status["toc"] == "cancelled"
    assert page._status["merge"] == "cancelled"
    assert states(page) == ["done", "cancelled", "cancelled"]
    assert page.app.running is False


def test_cancel_before_start_launches_nothing(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    page.app.cancel_requested = True
    calls = install_run(monkeypatch, page, [0, 0, 0])

    run(page, ["infocheck", "toc", "merge"])

    assert calls == []
    assert states(page) == ["cancelled"] * 3
    assert all(page._status[key] == "cancelled" for key in ("infocheck", "toc", "merge"))


# ── 单步入口 run_step ────────────────────────────────────────────────────
def test_run_step_requires_issue_dir(tmp_path, engine, monkeypatch):
    page = _Page()
    install_run(monkeypatch, page, [0])

    ASM.run_step(page, "merge")

    assert page.page.tasks == []
    assert page.app.toasts == ["请先选择期目录"]
    assert page.app.marks[-1] == ("validation_error",
                                  {"step": "merge", "reason": "请先选择期目录"})


def test_run_step_rejects_unknown_step(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    install_run(monkeypatch, page, [0])

    ASM.run_step(page, "nope")

    assert page.app.toasts[-1] == "未知步骤：nope"
    assert page.app.marks[-1][0] == "validation_error"
    assert page.page.tasks == []


def test_run_step_ignored_while_running(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    page.app.running = True
    calls = install_run(monkeypatch, page, [0])

    ASM.run_step(page, "merge")

    assert calls == [] and page.page.tasks == []


def test_dirpush_repo_guard_asks_for_confirmation(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    page.set_field("dirpush_out", str(tmp_path / "out"))
    calls = install_run(monkeypatch, page, [0])
    monkeypatch.setattr(ASM.DIRPUSH, "repo_warning", lambda *a, **k: "输出根在代码仓内")
    C._open_dialogs.clear()

    # 默认（auto_confirm）直接启动，不再有整期级别的「已停止」toast。
    page.app.auto_confirm = True
    ASM.run_step(page, "dirpush")
    assert [call.entry for call in calls] == ["dirpush.py"]

    # 交互模式：仓库内输出根必须走确认框，确认前不启动。
    page2 = make_page(tmp_path)
    page2.set_field("dirpush_out", str(tmp_path / "out"))
    page2.app.auto_confirm = False
    calls2 = install_run(monkeypatch, page2, [0])
    C._open_dialogs.clear()
    ASM.run_step(page2, "dirpush")
    assert calls2 == [] and page2.page.tasks == []
    assert len(C._open_dialogs) == 1
    dialog = C._open_dialogs[-1]
    assert "输出根在代码仓内" in str(dialog.title.value)
    assert [action.content for action in dialog.actions] == ["取消", "继续生成"]

    # 点「继续生成」→ 记 confirm_repo_out 并真正派发。
    dialog.actions[1].on_click(None)
    assert ("confirm_repo_out", {"path": str(tmp_path / "out")}) in page2.app.marks
    assert [call.entry for call in calls2] == ["dirpush.py"]
    C._open_dialogs.clear()


def test_exception_stops_remaining_and_records_error(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [RuntimeError("boom")])

    run(page, ["infocheck", "toc", "merge"])

    assert len(calls) == 1
    status = page._status["infocheck"]
    assert status == "error" and status.exit_code == -1 and "boom" in status.error
    assert states(page) == ["error", "skipped", "skipped"]
    assert "任务启动失败：boom" in page.app.toasts[-1]
    assert ("run_start_error", {"step": "infocheck", "error": "boom"}) in page.app.marks


def test_build_failure_after_infocheck_skips_downstream(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path, report=False)  # 没有报告：toc 构建失败
    calls = install_run(monkeypatch, page, [0])

    run(page, ["infocheck", "toc", "merge"])

    assert [call.entry for call in calls] == ["infocheck.py"]
    assert page._status["toc"] == "error" and page._status["toc"].exit_code is None
    assert "信息检查报告" in page._status["toc"].error
    assert states(page) == ["done", "error", "skipped"]
    assert "信息检查报告" in page.app.toasts[-1]


def test_run_step_backcompat_keeps_log_and_writes_status(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    page._log.add_line("之前", "info")
    calls = install_run(monkeypatch, page, [0])

    ASM.run_step(page, "merge")

    assert [call.entry for call in calls] == ["mergepdf.py"]
    assert page._log.clear_calls == 0 and page._log.lines[0] == ("之前", "info")
    assert page._status["merge"] == "done"
    assert page._status["infocheck"] == "idle"
    assert page._run_results[0]["key"] == "merge"

    # 单步校验错误路径保持原样。
    blank = _Page()
    ASM.run_step(blank, "merge")
    assert blank.app.toasts == ["请先选择期目录"]
    assert blank.app.marks[-1][0] == "validation_error"
