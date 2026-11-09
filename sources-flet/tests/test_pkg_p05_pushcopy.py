"""P05（2.5-G）目录摘要推送缺陷回归：BUG-05 / BUG-06 / BUG-07 / BUG-08。

独立断言（不镜像实现）：只调用公开/纯函数与无头页面构建，`EB.run_call` 一律用假
运行器替换，不启动真实引擎、不建窗口、不碰真实稿件/设置。

- BUG-05：期号分隔符唯一判据 `[,\\s，、]+`；非法片段整体拒绝；预览与 argv 一致；
  空列表不传 `--include-issues`。
- BUG-06：期头预检同时扫 `000 Table of Contents`（真实）与 `000`（旧夹具），
  匹配前剥 tex 注释。
- BUG-07：rc≠0 / JSON ok=false 都是失败；产物只认本次 JSON outputs；取消/异常
  不落产物；重跑成功只展示新产物。
- BUG-08：结果摘要显示「摘要取到 abstracts_ok/articles 篇」。
"""
from __future__ import annotations

import asyncio
import json

import flet as ft
import pytest

from services import engine_bridge as EB
from ui import assembly as ASM
from ui import dirpush as D
from ui import pages as P
from ui.tokens import Tokens

# ── 引擎目录桩（只为 build_call 的 base_cmd，不执行任何文件）──────────────
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


def args_of(call: EB.EngineCall) -> list[str]:
    head = EB.base_cmd(call.entry)
    assert head is not None
    return call.argv[len(head):]


def _arg_value(args: list[str], name: str) -> str:
    assert name in args, args
    return args[args.index(name) + 1]


# ── 假壳：够 build()/运行链用 ─────────────────────────────────────────────
class _Store:
    def __init__(self) -> None:
        self.data: dict = {}
        self.recent: dict = {}

    def get(self, key: str, default=None):
        current = self.data
        for part in str(key).split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set(self, key: str, value) -> None:
        parts = str(key).split(".")
        current = self.data
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    def update_options(self, **kwargs) -> None:
        self.data.setdefault("options", {}).update(kwargs)

    def set_recent(self, key: str, value) -> None:
        self.recent[key] = value


class _FletPage:
    def run_task(self, *args, **kwargs) -> None:
        pass

    def update(self) -> None:
        pass

    def show_dialog(self, *args, **kwargs) -> None:
        pass

    def pop_dialog(self) -> None:
        pass


class _FakeApp:
    def __init__(self) -> None:
        self.store = _Store()
        self.page = _FletPage()
        self.running = False
        self.cancel_requested = False
        self.notes_repo = ""
        self.auto_confirm = True
        self.last_line = ""
        self.line_count = 0
        self.finished: list = []
        self.t = Tokens("light", 1.0, 96)
        self.engine_status = type("_S", (), {
            "ok": True, "version": "1.6.0", "engine_dir": "/tmp/engine",
            "describe": lambda self: "源码引擎 · /tmp/engine"})()
        self.toasts: list[str] = []
        self.marked: list = []

    def toast(self, message: str) -> None:
        self.toasts.append(message)

    def mark(self, event: str, **fields) -> None:
        self.marked.append((event, fields))

    def record_line(self, line: str) -> None:
        self.line_count += 1
        self.last_line = str(line)

    def begin_run(self, call) -> None:
        self.running = True
        self.cancel_requested = False
        self.last_line = ""

    def finish_run(self, call, code) -> None:
        self.running = False
        self.finished.append((call, code))

    def track_proc(self, proc) -> None:
        pass

    def open_path(self, value) -> None:
        pass

    def copy_text(self, value) -> None:
        pass

    def browse_field(self, *args, **kwargs) -> None:
        pass

    def stop_run(self) -> None:
        self.running = False

    def goto(self, key: str) -> None:
        pass

    def persist(self) -> None:
        pass

    def set_option(self, key: str, value) -> None:
        pass


def _build_issue():
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    return app, page


def _children(control):
    for attr in ("controls", "content"):
        value = getattr(control, attr, None)
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, ft.Control):
                    yield item
        elif isinstance(value, ft.Control):
            yield value


def _walk(control):
    yield control
    for child in _children(control):
        yield from _walk(child)


def _blob(control) -> str:
    parts: list[str] = []
    for ctl in _walk(control):
        if isinstance(ctl, ft.Text) and isinstance(ctl.value, str):
            parts.append(ctl.value)
        elif isinstance(ctl, ft.TextButton):
            parts.append(str(ctl.content or ""))
    return "\n".join(parts)


def _open_buttons(control) -> list:
    return [ctl for ctl in _walk(control)
            if isinstance(ctl, ft.TextButton)
            and str(ctl.content or "").startswith(("打开", "复制路径"))]


def _payload(**over) -> dict:
    base = {
        "schema": 1, "ok": True, "mode": "issue", "dry_run": False,
        "outputs": [], "warnings": [],
        "counts": {"zh_entries": 2, "en_entries": 0, "articles": 2,
                   "abstracts_ok": 2},
    }
    base.update(over)
    return base


def _json_lines(payload: dict) -> list[str]:
    return json.dumps(payload, ensure_ascii=False, indent=2).splitlines()


def _fake_runner(monkeypatch, runs: list[tuple[int, list[str], bool]],
                 app: _FakeApp) -> None:
    """按顺序消费 (rc, 输出行, 结束后是否标记取消)；不启动任何子进程。"""
    async def fake_run_call(call, *, on_line=None, on_proc=None, cwd=None,
                            api=None, notes="", stdin_text=None):
        code, lines, cancel_after = runs.pop(0)
        for line in lines:
            if on_line is not None:
                on_line(line)
        if cancel_after:
            app.cancel_requested = True
        return code

    monkeypatch.setattr(EB, "run_call", fake_run_call)


# ── BUG-05：期号分隔符 / 非法整体拒绝 / 预览 == argv / 空不传 ──────────────
def test_bug05_single_separator_judgement(engine):
    assert EB.dirpush_split_issues("1 2") == ([1, 2], [])
    assert EB.dirpush_issues_csv("1 2") == "1,2"
    assert EB.dirpush_issues_csv("4,4,3，2 1") == "1,2,3,4"
    assert EB.dirpush_issues_csv("1、2， 3\t4") == "1,2,3,4"
    assert EB.dirpush_issues_csv("   ") == ""

    good, bad = D.normalize_issues("1,2,0,x")
    assert good == [1, 2] and bad == ["0", "x"]
    # 非法片段不得被静默丢弃：argv 侧整体拒绝（GUI 侧由 validation_error 报错）。
    with pytest.raises(ValueError, match="只填期号"):
        EB.dirpush_issues_csv("1,2,0,x")


def test_bug05_preview_matches_argv_and_empty_omitted(engine, tmp_path):
    text = "4,4,3，2 1"
    caption = D.issues_caption(text)
    call = D.build_call(issue=str(tmp_path), annual=True, year="2026",
                        include_issues=text)
    args = args_of(call)
    csv = _arg_value(args, "--include-issues")
    assert csv == "1,2,3,4"
    # 预览「将纳入第 … 期」的期号序列与 argv 完全一致（同判据，不分叉）。
    assert "、".join(csv.split(",")) in caption
    assert all(str(n) in caption for n in csv.split(","))

    empty = D.build_call(issue=str(tmp_path), annual=True, year="2026",
                         include_issues="   ")
    assert "--include-issues" not in args_of(empty)


# ── BUG-06：真实 000 目录 / 旧夹具 / 注释剥离 ─────────────────────────────
def test_bug06_header_parse_error_scans_real_toc_dir(tmp_path):
    issue = tmp_path / "2022年第6期"
    toc = issue / "000 Table of Contents"
    toc.mkdir(parents=True)
    header = toc / "Chinese Content.tex"
    header.write_text("第 ? 卷，第 ? 期，总第 88 期\n", encoding="utf-8")
    assert "期头" in D.header_parse_error(str(issue))

    header.write_text("第 19 卷，第 5 期，总第 88 期\n", encoding="utf-8")
    assert D.header_parse_error(str(issue)) == ""

    # 注释里残留的 `?` 不得误报（真实期头已合法）。
    header.write_text("第 19 卷，第 5 期 % 旧模板：第 ? 卷，第 ? 期\n",
                      encoding="utf-8")
    assert D.header_parse_error(str(issue)) == ""

    # 真实目录下还有子目录时同样要扫到。
    sub = toc / "sub"
    sub.mkdir()
    header.write_text("第 19 卷，第 5 期，总第 88 期\n", encoding="utf-8")
    (sub / "期头.tex").write_text("第 ？ 卷，第 ？ 期\n", encoding="utf-8")
    assert "期头" in D.header_parse_error(str(issue))


def test_bug06_header_parse_error_keeps_legacy_fixture(tmp_path):
    # tests/test_b117_issue_steps.py 的旧夹具形状：<issue>/000/*.tex。
    issue = tmp_path / "旧期"
    legacy = issue / "000"
    legacy.mkdir(parents=True)
    (legacy / "目录.tex").write_text("第 ? 卷，第 ? 期，总第 90 期\n",
                                     encoding="utf-8")
    assert "期头" in D.header_parse_error(str(issue))
    (legacy / "目录.tex").write_text("第 19 卷，第 5 期，总第 90 期\n",
                                     encoding="utf-8")
    assert D.header_parse_error(str(issue)) == ""


def test_bug06_header_parse_error_tolerates_tex_escaped_space(tmp_path):
    # 引擎 patch_header 真实输出把转义空格留在期头里：`第\ ? 卷` / `第\ 19 卷`。
    issue = tmp_path / "2022年第6期"
    toc = issue / "000 Table of Contents"
    toc.mkdir(parents=True)
    header = toc / "Chinese Content.tex"

    header.write_text(r"\heiti{第\ ? 卷，第\ ? 期，总第\ ? 期}" + "\n",
                      encoding="utf-8")
    assert "期头" in D.header_parse_error(str(issue))

    header.write_text(r"\heiti{第\ 19 卷，第\ 19 期，总第\ 18 期}" + "\n",
                      encoding="utf-8")
    assert D.header_parse_error(str(issue)) == ""

    # 注释里的反斜杠形状 `?` 期头同样不得误报。
    header.write_text("% 旧模板：" + r"第\ ? 卷，第\ ? 期" + "\n",
                      encoding="utf-8")
    assert D.header_parse_error(str(issue)) == ""


# ── BUG-08：摘要取到率 ───────────────────────────────────────────────────
def test_bug08_summary_shows_abstract_rate_and_fallback():
    normal = [line for line in D.summary_lines(
        {"counts": {"zh_entries": 3, "en_entries": 2, "articles": 3,
                    "abstracts_ok": 2}})]
    assert any("摘要取到 2/3 篇" in line for line in normal)

    # 英文 fallback 不算摘要：ok=0 / missing=N 的载荷照实显示 0/N。
    fallback = [line for line in D.summary_lines(
        {"counts": {"zh_entries": 2, "en_entries": 1, "articles": 2,
                    "abstracts_ok": 0}})]
    assert any("摘要取到 0/2 篇" in line for line in fallback)

    annual = [line for line in D.summary_lines(
        {"mode": "annual", "counts": {"zh_entries": 1, "en_entries": 0,
                                      "articles": 0}})]
    assert any("摘要取到 0/0 篇" in line for line in annual)


# ── BUG-07：纯函数判据 ───────────────────────────────────────────────────
def test_bug07_result_payload_state_and_artifact(tmp_path):
    docx = tmp_path / "目录摘要.docx"
    docx.write_bytes(b"docx")
    payload = _payload(outputs=[str(docx)])
    lines = ["日志行", *_json_lines(payload)]
    assert D.result_payload(lines) == payload
    assert D.result_payload(["不是 JSON"]) is None

    assert D.run_state(0, False, payload) == "done"
    assert D.run_state(0, False, {"ok": True}) == "done"
    assert D.run_state(0, False, {"ok": False}) == "error"
    assert D.run_state(2, False, {"ok": False}) == "error"
    assert D.run_state(0, True, payload) == "cancelled"

    assert D.result_artifact(payload) == str(docx)
    assert D.result_artifact({"ok": False, "outputs": [str(docx)]}) == ""
    assert D.result_artifact({"ok": True, "dry_run": True,
                              "outputs": [str(docx)]}) == ""
    assert D.result_artifact({"ok": True, "outputs": [str(tmp_path / "缺.docx")]}) == ""


# ── BUG-07：页面运行链（_run_calls） ─────────────────────────────────────
def test_bug07_run_calls_ok_false_is_error_without_artifact(tmp_path, engine,
                                                            monkeypatch):
    app, page = _build_issue()
    page.set_field("issue_dir", str(tmp_path / "期"))
    stale = tmp_path / "旧产物.docx"
    stale.write_bytes(b"old")
    payload = _payload(ok=False, outputs=[str(stale)],
                       warnings=["目标文件被占用，写入失败"])
    runs = [(0, _json_lines(payload), False)]
    _fake_runner(monkeypatch, runs, app)

    asyncio.run(page._run_calls([("dirpush", D.build_call(issue=str(tmp_path / "期")))]))
    status = page._status["dirpush"]
    assert status.state == "error"
    assert status.exit_code == 0
    assert status.artifact == ""
    assert page._dirpush_payload == payload

    card_text = _blob(page._dirpush_result())
    assert "推送失败" in card_text
    assert "旧产物" not in card_text
    assert _open_buttons(page._dirpush_result()) == []


def test_bug07_run_calls_nonzero_exit_and_cancel_leave_no_artifact(tmp_path, engine,
                                                                  monkeypatch):
    app, page = _build_issue()
    page.set_field("issue_dir", str(tmp_path / "期"))
    made = tmp_path / "半成品.docx"
    made.write_bytes(b"half")
    ok_payload = _payload(outputs=[str(made)])

    runs = [(1, ["Traceback (most recent call last):", "OSError: disk"],
             False)]
    _fake_runner(monkeypatch, runs, app)
    asyncio.run(page._run_calls([("dirpush", D.build_call(issue=str(tmp_path / "期")))]))
    assert page._status["dirpush"].state == "error"
    assert page._status["dirpush"].artifact == ""
    assert _open_buttons(page._dirpush_result()) == []

    # 取消：即使引擎先打了成功 JSON，也不得把产物归到本次。
    runs = [(0, _json_lines(ok_payload), True)]
    _fake_runner(monkeypatch, runs, app)
    asyncio.run(page._run_calls([("dirpush", D.build_call(issue=str(tmp_path / "期")))]))
    assert page._status["dirpush"].state == "cancelled"
    assert page._status["dirpush"].artifact == ""
    card_text = _blob(page._dirpush_result())
    assert "已取消" in card_text
    assert _open_buttons(page._dirpush_result()) == []


def test_bug07_rerun_success_shows_only_new_artifact(tmp_path, engine, monkeypatch):
    app, page = _build_issue()
    page.set_field("issue_dir", str(tmp_path / "期"))
    first = tmp_path / "第一版.docx"
    first.write_bytes(b"v1")
    second = tmp_path / "第二版.docx"
    second.write_bytes(b"v2")

    runs = [(0, _json_lines(_payload(outputs=[str(first)])), False)]
    _fake_runner(monkeypatch, runs, app)
    call = D.build_call(issue=str(tmp_path / "期"))
    asyncio.run(page._run_calls([("dirpush", call)]))
    assert page._status["dirpush"].artifact == str(first)

    runs = [(1, ["boom"], False)]
    _fake_runner(monkeypatch, runs, app)
    asyncio.run(page._run_calls([("dirpush", call)]))
    assert page._status["dirpush"].state == "error"
    assert page._status["dirpush"].artifact == ""

    runs = [(0, _json_lines(_payload(outputs=[str(second)])), False)]
    _fake_runner(monkeypatch, runs, app)
    asyncio.run(page._run_calls([("dirpush", call)]))
    assert page._status["dirpush"].state == "done"
    assert page._status["dirpush"].artifact == str(second)
    card_text = _blob(page._dirpush_result())
    assert second.name in card_text
    assert first.name not in card_text
    assert _open_buttons(page._dirpush_result())


def test_bug07_assembly_sequence_ok_false_banner_is_failure(tmp_path, engine,
                                                           monkeypatch):
    app, page = _build_issue()
    page.set_field("issue_dir", str(tmp_path / "期"))
    payload = _payload(ok=False, outputs=[],
                       warnings=["同名文件被占用，无法覆盖"])
    runs = [(0, _json_lines(payload), False)]
    _fake_runner(monkeypatch, runs, app)

    asyncio.run(ASM._run_sequence(
        page, ["dirpush"], {"dirpush": D.build_call(issue=str(tmp_path / "期"))}))
    assert page._status["dirpush"].state == "error"
    assert page._result.open_btn.visible is False
    assert "失败" in str(page._result.title.value or "")
    assert "完成" not in str(page._result.title.value or "")


def test_bug07_apply_status_keeps_legacy_error_tail(tmp_path, engine):
    app, page = _build_issue()
    stale = tmp_path / "旧产物.docx"
    stale.write_bytes(b"old")
    page._apply_status("dirpush", 0, False, artifact=str(stale),
                       payload=_payload(ok=False, outputs=[str(stale)]))
    status = page._status["dirpush"]
    assert status.state == "error" and status.artifact == ""
    card_text = _blob(page._dirpush_result())
    assert "推送失败" in card_text
    assert "旧产物" not in card_text
