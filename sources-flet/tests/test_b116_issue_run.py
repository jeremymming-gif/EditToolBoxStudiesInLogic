"""批 116 整期装配运行语义（D1-115-26）：默认步、rc 门、日志、状态、取消。

不启动 GUI、不跑真引擎：run_call 用桩，引擎目录用临时桩目录（同 test_b109_w3）。
"""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from services import engine_bridge as EB
from ui import assembly as ASM
from ui.tokens import Tokens

STEP_KEYS = ("infocheck", "toc", "compileall", "merge", "cleantemp", "dirpush")


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

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))
        asyncio.run(handler(*args))

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


def test_run_all_default_is_first_four_steps(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [0, 0, 0, 0])
    assert inspect.signature(ASM.run_all).parameters["keys"].default is None

    ASM.run_all(page)  # 旧调用形式：run_all(page)

    assert [call.entry for call in calls] == [
        "infocheck.py", "toc.py", "compileall.py", "mergepdf.py",
    ]
    assert [item["key"] for item in page._run_results] == [
        "infocheck", "toc", "compileall", "merge",
    ]
    assert states(page) == ["done"] * 4
    assert page._status["cleantemp"] == "idle" and page._status["dirpush"] == "idle"
    assert ("run_all_submit", {"steps": ["infocheck", "toc", "compileall", "merge"]}) in page.app.marks


def test_run_all_explicit_keys_keep_normal_step_semantics(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path, report=False)
    page.set_field("dirpush_out", str(tmp_path / "out"))
    page.app.store.set("form.cleantemp_mode", "delivery")
    page.app.store.update_options(cleantemp_dry_run=False, dirpush_dry_run=True)
    calls = install_run(monkeypatch, page, [0, 0])

    ASM.run_all(page, ["cleantemp", "dirpush"])

    assert [call.entry for call in calls] == ["cleantemp.py", "dirpush.py"]
    clean = calls[0].argv
    assert "--mode" in clean and "delivery" in clean
    assert "--dry-run" not in clean  # 不再被 run_all 强制只预演
    assert page._status["cleantemp"] == "done"
    assert page._status["dirpush"] == "done"

    # 单字符串也当作单步（方便确认框单传）；顺序保留。
    page2 = make_page(tmp_path)
    only_merge = install_run(monkeypatch, page2, [0])
    ASM.run_all(page2, "merge")
    assert [call.entry for call in only_merge] == ["mergepdf.py"]
    assert states(page2) == ["done"]


def test_run_all_stops_on_rc2_and_marks_rest_skipped(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [0, 2])

    ASM.run_all(page)

    assert len(calls) == 2  # compileall 失败后不再启动 merge
    assert page._status["toc"].exit_code == 2
    assert page._status["toc"] == "error"
    assert page._status["compileall"] == "skipped"
    assert page._status["merge"] == "skipped"
    assert states(page) == ["done", "error", "skipped", "skipped"]
    assert [item["rc"] for item in page._run_results] == [0, 2, None, None]


def test_run_all_rc1_warns_and_continues(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [1, 0, 0, 0])

    ASM.run_all(page)

    assert len(calls) == 4
    assert states(page) == ["warn", "done", "done", "done"]
    assert page._status["infocheck"] == "warn" and page._status["infocheck"].exit_code == 1
    assert page._result.level == "ok"  # 最后一步的结果条


def test_log_is_never_cleared_and_gets_section_headers(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    page._log.add_line("旧日志", "info")
    install_run(monkeypatch, page, [0, 1, 0, 0])

    ASM.run_all(page)

    assert page._log.clear_calls == 0
    assert page._log.lines[0] == ("旧日志", "info")
    texts = [text for text, _ in page._log.lines]
    assert any(text.startswith("▶ 01/06") and "infocheck" in text for text in texts)
    assert any(text.startswith("▶ 04/06") and "merge" in text for text in texts)
    assert any(text.startswith("■ 02/06") and "rc=1" in text for text in texts)
    assert len([text for text in texts if text.startswith("▶")]) == 4


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

    def spy(page_, key, *args):
        seen.append(key)
        return original(page_, key, *args)

    monkeypatch.setattr(ASM, "after_step", spy)

    ASM.run_all(page)

    status = page._status["merge"]
    assert isinstance(status, ASM.StepStatus) and status == "done"
    assert status.exit_code == 0
    assert isinstance(status.duration_ms, (int, float)) and status.duration_ms >= 0
    assert status.started_at and status.finished_at
    assert status.artifact == str(artifact)
    assert status.as_dict()["state"] == "done"
    assert json.loads(json.dumps(status.as_dict()))["exit_code"] == 0
    assert seen == ["infocheck", "toc", "compileall", "merge"]
    assert page._refresh_count >= 8
    assert page._engine_lines == ["引擎输出"] * 4


def test_cancel_marks_current_and_remaining_cancelled(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [0, "cancel", 0, 0])

    ASM.run_all(page)

    assert len(calls) == 2  # 取消后不启动 compileall/merge
    assert page._status["infocheck"] == "done"
    assert page._status["toc"] == "cancelled"
    assert page._status["compileall"] == "cancelled"
    assert page._status["merge"] == "cancelled"
    assert states(page) == ["done", "cancelled", "cancelled", "cancelled"]
    assert page.app.running is False


def test_cancel_before_start_launches_nothing(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    page.app.cancel_requested = True
    calls = install_run(monkeypatch, page, [0, 0, 0, 0])

    ASM.run_all(page)

    assert calls == [] and page.page.tasks
    assert states(page) == ["cancelled"] * 4
    assert all(page._status[key] == "cancelled" for key in STEP_KEYS[:4])


def test_run_all_requires_issue_dir(tmp_path, engine, monkeypatch):
    page = _Page()
    calls = install_run(monkeypatch, page, [0])

    ASM.run_all(page)

    assert calls == [] and page.page.tasks == []
    assert page.app.toasts == ["请先选择期目录"]
    assert page.app.marks[-1] == ("validation_error",
                                  {"step": "all", "reason": "missing issue dir"})


def test_run_all_rejects_unknown_or_empty_keys(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    install_run(monkeypatch, page, [0])

    ASM.run_all(page, ["nope"])
    assert page.app.toasts[-1] == "未知步骤：nope"
    assert page.app.marks[-1][0] == "validation_error"

    ASM.run_all(page, [])
    assert page.app.toasts[-1] == "请至少选择一个步骤"
    assert page.page.tasks == []


def test_run_all_ignored_while_running(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    page.app.running = True
    calls = install_run(monkeypatch, page, [0, 0, 0, 0])

    ASM.run_all(page)
    ASM.run_step(page, "merge")

    assert calls == [] and page.page.tasks == []


def test_dirpush_repo_guard_only_when_selected(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    page.set_field("dirpush_out", str(tmp_path / "out"))
    install_run(monkeypatch, page, [0, 0, 0, 0])
    monkeypatch.setattr(ASM.DIRPUSH, "repo_warning", lambda *a, **k: "输出根在代码仓内")

    # 默认 1–4 不含 dirpush：不受输出根影响。
    ASM.run_all(page)
    assert len(page._run_results) == 4

    # 显式勾选 dirpush 且非 dry-run：整期拒绝启动（由 pages.py 确认框负责例外）。
    marker = len(page.page.tasks)
    ASM.run_all(page, ["dirpush"])
    assert len(page.page.tasks) == marker
    assert page.app.toasts[-1] == "输出根在代码仓内，整期流水线已停止；请改输出根或勾选只预演"
    assert page.app.marks[-1] == ("validation_error", {"step": "all", "reason": "repo out root"})


def test_exception_stops_remaining_and_records_error(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path)
    calls = install_run(monkeypatch, page, [RuntimeError("boom")])

    ASM.run_all(page)

    assert len(calls) == 1
    status = page._status["infocheck"]
    assert status == "error" and status.exit_code == -1 and "boom" in status.error
    assert states(page) == ["error", "skipped", "skipped", "skipped"]
    assert "任务启动失败：boom" in page.app.toasts[-1]
    assert ("run_start_error", {"step": "infocheck", "error": "boom"}) in page.app.marks


def test_build_failure_after_infocheck_skips_downstream(tmp_path, engine, monkeypatch):
    page = make_page(tmp_path, report=False)  # 没有报告：toc 构建失败
    calls = install_run(monkeypatch, page, [0])

    ASM.run_all(page)

    assert [call.entry for call in calls] == ["infocheck.py"]
    assert page._status["toc"] == "error" and page._status["toc"].exit_code is None
    assert "信息检查报告" in page._status["toc"].error
    assert states(page) == ["done", "error", "skipped", "skipped"]
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
