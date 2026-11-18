"""GUI 包（两段式初排合批）：冲突裁决 / VS Code 对比 / 完成态日志 / .DS_Store 的最小匿名回归。

这份文件只钉 GUI 侧合同（CONTRACT.md v1 §3 与执行卡 §4.1–§4.4），不验收引擎：

* (a) 冲突行渲染三选一、默认「保留原值」、无冲突时整块不出现；
* (b) 库不可用显示具体原因，**不得**显示成「零冲突」；
* (c) 对比入口只在阶段③（有效 preview）可用，等待回件 / 有阻断 / 裁决刚改 /
      预览失效时一律禁用，且等待回件块里没有第二条入口；
* (d) 对比副本落 `<work_dir>/compare/`，`stage/` 内不新增任何 `.bib`；
* (e) 左侧是冻结稿件版 Bib、右侧是候选，两侧路径与 sha256 逐字节对应；
* (f) 完成态日志含任务号 / 编译状态 / 最终路径，且与 status 同源；失败也写日志；
* (g) 成功卡主操作是「打开交付目录」，opener 收到的是 final。

夹具全部匿名自造（临时目录 + 假宿主 + 假桥），不读真实稿件、不建 Flet 窗口、
不调用真实编辑器。`.DS_Store` 一节依赖 A 包在引擎侧落地，按能力探测 `skipif`，
**不为了变绿放宽断言**。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import flet as ft

from services import editor_bridge as ED
from ui import bib_ai_assist as A
from ui.tokens import Tokens


# ── 匿名夹具 ────────────────────────────────────────────────────────────────
class _Store:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value


class _App:
    """最小 app 服务面：store / copy_text / open_path（记录收到什么）。"""

    def __init__(self) -> None:
        self.store = _Store()
        self.notes_repo = ""
        self.window_w = 1160
        self.toasts: list[str] = []
        self.opened: list[str] = []

    def copy_text(self, text) -> None:
        self.toasts.append(f"copied:{text}")

    def open_path(self, path) -> None:
        self.opened.append(str(path))

    def record_line(self, line) -> None:
        pass


class _Page:
    def __init__(self) -> None:
        self.width = 1160
        self.run_task = lambda fn: None
        self.logged: list[str] = []

    def log_line(self, text) -> None:
        self.logged.append(str(text))


class _Host:
    """满足 BibAiAssistView 宿主协议（t / app / mark / notify / _render / log_line）。

    `log_line` 与真实 `_SingleAiHost.log_line` 同形：宿主适配器把行转给页面
    `_on_line`；这里只记录，供「日志 == 状态」断言用。
    """

    def __init__(self) -> None:
        self.t = Tokens("light")
        self.page = _Page()
        self.app = _App()
        self.marks: list[tuple[str, dict]] = []
        self.notes: list[str] = []
        self.logged: list[str] = []

    def log_line(self, text) -> None:
        self.logged.append(str(text))
        self.page.log_line(text)

    def mark(self, event, **fields) -> None:
        self.marks.append((event, dict(fields)))

    def notify(self, text) -> None:
        self.notes.append(str(text))

    def set_view(self, view) -> None:
        pass

    def _render(self) -> None:
        pass

    def browse_field(self, *args, **kwargs) -> None:
        pass


class _Bridge:
    """假桥：工厂返回带 argv 的假 call，`run_call` 按预置 JSON 行回放。"""

    def __init__(self, lines: list[str] | None = None, code: int = 0) -> None:
        self.lines = list(lines or [])
        self.code = code
        self.ran: list = []

    def _call(self, name, kwargs):
        return SimpleNamespace(entry="engine.py", argv=[name], label=name,
                               display=f"engine.py {name}", task_text="TASK",
                               task_id="T-1", preview_id="pv-1", blocked=[],
                               keys=3, entries=2, backup="b.bak")

    def __getattr__(self, name):
        if name.endswith("_call") or name in ("prepare", "import", "preview", "apply",
                                              "undo", "request", "status"):
            def factory(**kwargs):
                return self._call(name, kwargs)
            return factory
        raise AttributeError(name)

    async def run_call(self, call, *, on_line=None, on_proc=None, cwd=None,
                       stdin_text=None):
        self.ran.append(call)
        for line in self.lines:
            if on_line is not None:
                on_line(line)
        return self.code


def _walk(control):
    """深度遍历 Flet 控件树（content / controls / actions）。"""
    if control is None:
        return
    yield control
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)
    for child in getattr(control, "controls", None) or ():
        yield from _walk(child)
    for child in getattr(control, "actions", None) or ():
        yield from _walk(child)


def _walk_visible(control):
    """只走**看得见**的子树：折叠掉的阶段块不算「界面上有这条入口」。

    四个阶段块同时存在于控件树里（`visible=False` 折叠），所以「同一时刻只有
    一条对比入口 / 一条冲突行」必须按可见性判断，而不是按节点总数。
    """
    if control is None:
        return
    if getattr(control, "visible", True) is False:
        return
    yield control
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk_visible(content)
    for child in getattr(control, "controls", None) or ():
        yield from _walk_visible(child)
    for child in getattr(control, "actions", None) or ():
        yield from _walk_visible(child)


def _blob(control) -> str:
    parts = []
    for node in _walk(control):
        for attr in ("value", "label", "content", "text"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def _view(*, bridge=None, purpose="library") -> tuple[A.BibAiAssistView, _Host]:
    host = _Host()
    view = A.BibAiAssistView(host, bridge=bridge or _Bridge(), purpose=purpose)
    return view, host


def _bib(path: Path, title: str) -> Path:
    path.write_text(
        "@article{a,\n  title = {%s},\n  year = {2020},\n}\n" % title,
        encoding="utf-8",
    )
    return path


def _preview_ready(tmp_path: Path, *, with_source: bool = True,
                   with_frozen: bool = True, frozen_sha: str | None = None):
    """造一份「阶段③ 已出有效 preview」的最小视图。

    `with_frozen` 决定引擎是否给了 `status.frozen_bib`（合同 v1.1）：
    给了就是「冻结快照可核」，没给就是旧 /1 任务（只能退化 + 明示）。
    `frozen_sha` 可覆盖，用来造「sha 对不上」的反例。
    """
    view, host = _view()
    work = tmp_path / "work"
    (work / "stage").mkdir(parents=True)
    (work / "stage" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    frozen = _bib(tmp_path / "frozen.bib", "Original Title")
    candidate = _bib(tmp_path / "candidate.bib", "Library Title")
    view.work_dir = str(work)
    view.target_bib = str(frozen)
    view._source_bib = str(frozen) if with_source else ""
    if with_frozen:
        view._frozen_bib_path = str(frozen)
        view._frozen_bib_sha = (frozen_sha if frozen_sha is not None
                                else A.file_sha256(str(frozen)))
    view.candidate_bib = str(candidate)
    view.preview_id = "pv-abc"
    view.phase = "preview"
    view.blocked = []
    view.task_id = "T-1"
    return view, host, work, frozen, candidate


_CONFLICTS = [
    {
        "conflict_id": "cf-0123456789ab",
        "key": "zhang2020",
        "field": "title",
        "original": "原条目标题",
        "library": "库里的标题",
        "reply": "回件里的标题",
        "choices": ["library", "reply", "original"],
        "default": "original",
        "group": "title",
    }
]


# ── (a) 冲突裁决行 ──────────────────────────────────────────────────────────
def test_conflict_row_offers_three_choices_and_defaults_to_original(tmp_path):
    view, _host, _work, _frozen, _candidate = _preview_ready(tmp_path)
    view.conflicts = A.parse_conflicts({"conflicts": _CONFLICTS})
    assert view.conflicts, "夹具冲突没被解析出来"

    view.body_only(compact=True)
    groups = [n for n in _walk_visible(view.build()) if isinstance(n, ft.RadioGroup)]
    # 每个冲突行一颗独立的 RadioGroup（可见区里只应出现这一条）。
    conflict_groups = [g for g in groups if getattr(g, "_bib_ai_conflict", "")]
    assert len(conflict_groups) == 1
    group = conflict_groups[0]
    assert group.value == "original", "缺省裁决必须是「保留原值」"
    assert [r.value for r in group.content.controls] == ["library", "reply", "original"]

    text = _blob(view.build())
    assert A.CONFLICT_TITLE in text
    for needle in ("zhang2020", "title", "原条目标题", "库里的标题", "回件里的标题"):
        assert needle in text, f"冲突行缺内容：{needle}"
    for _key, label in A.DECISION_CHOICES:
        assert label in text


def test_conflict_section_absent_when_no_conflicts():
    view, _host = _view()
    assert view.conflicts == []
    view.body_only(compact=True)
    text = _blob(view.build())
    assert A.CONFLICT_TITLE not in text
    assert not [g for g in _walk(view.build())
                if isinstance(g, ft.RadioGroup) and getattr(g, "_bib_ai_conflict", "")]


def test_decision_change_invalidates_preview_and_keeps_scope():
    view, _host = _view()
    view.conflicts = A.parse_conflicts({"conflicts": _CONFLICTS})
    view.preview_id = "pv-1"
    view.phase = "preview"

    cid = view.conflicts[0]["conflict_id"]
    assert view.set_decision(cid, "library") is True
    assert view.decisions[cid] == "library"
    assert view.decision_scope() == {cid: "library"}
    # 裁决变了 → 旧预览必须失效（合同 §2：apply/deliver 用旧 preview_id 要拒）。
    assert view.preview_id == ""
    assert view.phase == "waiting"
    assert "重新生成拟应用结果" in view.notice
    # 重复设同一个值不算变更。
    assert view.set_decision(cid, "library") is False
    # 认不出的值回落到 original，不悄悄变成「用库值」。
    assert view.set_decision(cid, "bogus") is True
    assert view.decisions[cid] == "original"


# ── (b) 库不可用必须显示原因 ────────────────────────────────────────────────
def test_unavailable_library_shows_reason_not_zero_conflicts():
    view, _host = _view()
    view.phase = "waiting"
    view.imported = True
    view._receive_library({"library": {
        "checked": False,
        "state": "missing",
        "path": "/nonexistent/shared.bib",
        "sha256": "",
        "matched": 0,
        "conflicts": 0,
        "skipped_reason": "共享库路径不存在",
    }})
    assert view.library["checked"] is False
    view.body_only(compact=True)
    text = _blob(view.build())
    assert "共享库不存在或路径不对" in text
    assert "原因：共享库路径不存在" in text
    # 绝不能把「没比对」显示成「比过了、零冲突」。
    assert "库冲突" not in text
    assert "库匹配" not in text


def test_missing_library_block_is_not_treated_as_checked():
    library = A.parse_library({})
    assert library["checked"] is False
    assert library["state"] == "skipped"
    lines = A.library_lines(library)
    assert lines == ["本次跳过了库比对"]
    assert all("0" not in line for line in lines)


def test_library_checked_reports_counts():
    library = A.parse_library({"library": {
        "checked": True, "state": "ok", "path": "/tmp/lib.bib",
        "sha256": "a" * 64, "matched": 4, "conflicts": 2, "skipped_reason": "",
    }})
    lines = A.library_lines(library)
    assert lines[0] == "库已比对"
    assert "库匹配 4 条" in lines
    assert "库冲突 2 条" in lines


# ── (c) 对比入口只在阶段③可用，且只有一条 ──────────────────────────────────
def _diff_buttons(view) -> list:
    return [n for n in _walk(view.build())
            if getattr(n, "_bib_ai_action", "") == "diff"]


def test_diff_button_enabled_only_after_valid_preview(tmp_path):
    view, _host, _work, _frozen, _candidate = _preview_ready(tmp_path)
    view.body_only(compact=True)
    buttons = _diff_buttons(view)
    assert len(buttons) == 1
    assert buttons[0].disabled is False
    assert view._can_diff() is True


@pytest.mark.parametrize("phase,blocked,expected", [
    ("idle", [], 1),
    ("preparing", [], 1),
    ("waiting", [], 1),
    ("blocked", ["回件缺条目：zhang2020"], 1),
    # 阶段④（已应用 / 已失效）：对比入口整个不出现——合同 §4.2 只允许它在阶段③。
    ("stale", [], 0),
    ("applied", [], 0),
])
def test_diff_button_disabled_outside_valid_preview(tmp_path, phase, blocked, expected):
    view, _host, _work, _frozen, _candidate = _preview_ready(tmp_path)
    view.phase = phase
    view.blocked = list(blocked)
    if phase != "preview":
        view.preview_id = ""
    view.body_only(compact=True)
    buttons = _diff_buttons(view)
    assert len(buttons) == expected
    if expected:
        assert buttons[0].disabled is True
    assert view._can_diff() is False


def test_diff_disabled_after_decision_change(tmp_path):
    view, _host, _work, _frozen, _candidate = _preview_ready(tmp_path)
    view.conflicts = A.parse_conflicts({"conflicts": _CONFLICTS})
    view.set_decision(view.conflicts[0]["conflict_id"], "reply")
    view.body_only(compact=True)
    assert view._can_diff() is False
    assert _diff_buttons(view)[0].disabled is True


def test_waiting_block_has_no_second_diff_entry(tmp_path):
    view, _host, _work, _frozen, _candidate = _preview_ready(tmp_path)
    view.phase = "waiting"
    view.preview_id = ""
    view.imported = False
    view.task_id = "T-1"
    view.taskbook_path = str(tmp_path / "taskbook.md")
    Path(view.taskbook_path).write_text("# 任务书\n", encoding="utf-8")
    view.body_only(compact=True)
    view.build()
    waiting = view._stage_boxes["waiting"]
    assert not [n for n in _walk(waiting) if getattr(n, "_bib_ai_action", "") == "diff"], \
        "等待回件块里不能再有对比入口（合同 §4.2：只留阶段③那一条）"


# ── (d)(e) 对比副本落 compare/，左冻结右候选 ────────────────────────────────
def _missing_editor(monkeypatch) -> list:
    spawns: list = []
    monkeypatch.setattr(ED, "find_editor", lambda: "")
    monkeypatch.setattr(ED, "CODE_PATHS", ())
    monkeypatch.setattr(ED, "subprocess", SimpleNamespace(
        run=lambda *a, **k: spawns.append((a, k)),
        CREATE_NO_WINDOW=0,
    ))
    return spawns


def test_compare_copies_land_in_compare_and_stage_untouched(tmp_path, monkeypatch):
    spawns = _missing_editor(monkeypatch)
    view, _host, work, frozen, candidate = _preview_ready(tmp_path)
    stage_before = sorted(p.name for p in (work / "stage").iterdir())

    view.open_diff()

    assert spawns == [], "找不到编辑器时绝不能真的起进程"
    compare = Path(ED.preview_dir(str(work), ED.COMPARE_DIRNAME))
    assert compare.is_dir(), "对比副本必须落在 <work_dir>/compare/"
    assert compare.name == ED.COMPARE_DIRNAME == "compare"
    assert ".diff" not in str(compare)

    folders = [p for p in compare.iterdir() if p.is_dir()]
    assert len(folders) == 1
    old = folders[0] / "reference.bib"
    new = folders[0] / "reference_proposed.bib"
    assert old.is_file() and new.is_file()

    # (e) 左 = 冻结稿件版 Bib，右 = 当前候选，两侧逐字节对应。
    assert old.read_bytes() == frozen.read_bytes()
    assert new.read_bytes() == candidate.read_bytes()
    assert ED.file_sha256(str(old)) == ED.file_sha256(str(frozen))
    assert ED.file_sha256(str(new)) == ED.file_sha256(str(candidate))
    ok, message = ED.verify_preview(folders[0].name, str(work), ED.COMPARE_DIRNAME)
    assert ok, message

    # (d) stage/ 内不新增任何 .bib，也没有被写进副本。
    stage_after = sorted(p.name for p in (work / "stage").iterdir())
    assert stage_after == stage_before
    assert not [p for p in (work / "stage").rglob("*.bib")]


def test_compare_reports_two_real_paths_when_editor_missing(tmp_path, monkeypatch):
    _missing_editor(monkeypatch)
    view, _host, work, frozen, candidate = _preview_ready(tmp_path)
    view.open_diff()
    assert "已打开" not in view.notice
    assert "旧版：" in view.notice and "新版：" in view.notice
    assert ED.preview_dir(str(work), ED.COMPARE_DIRNAME) in view.notice
    # 两条路径必须是真实存在、可手工对比的副本，而不是原始输入路径。
    folders = [p for p in (work / "compare").iterdir() if p.is_dir()]
    old = folders[0] / "reference.bib"
    new = folders[0] / "reference_proposed.bib"
    assert str(old) in view.notice and str(new) in view.notice
    assert old.is_file() and new.is_file()
    assert old.read_bytes() == frozen.read_bytes()
    assert new.read_bytes() == candidate.read_bytes()


def test_compare_mark_records_opened_false(tmp_path, monkeypatch):
    _missing_editor(monkeypatch)
    view, host, _work, _frozen, _candidate = _preview_ready(tmp_path)
    view.open_diff()
    events = [fields for event, fields in host.marks if event == "bib_ai_diff"]
    assert events and events[-1]["opened"] is False


def test_compare_uses_frozen_source_bib_not_target(tmp_path, monkeypatch):
    _missing_editor(monkeypatch)
    view, _host, work, frozen, candidate = _preview_ready(tmp_path)
    other = _bib(tmp_path / "other.bib", "Something Else")
    view.target_bib = str(other)  # 目标被换过：左侧仍必须是冻结快照
    view.open_diff()
    folders = [p for p in (work / "compare").iterdir() if p.is_dir()]
    assert (folders[0] / "reference.bib").read_bytes() == frozen.read_bytes()
    assert (folders[0] / "reference.bib").read_bytes() != other.read_bytes()


# ── 红线2-⑦ 冻结 Bib 身份（合同 v1.1 四判据）─────────────────────────────
def test_frozen_bib_sha_match_opens_normally(tmp_path, monkeypatch):
    """判据 1：frozen_bib 的 sha 对得上 → 左侧就是冻结快照，文案是正常成功。"""
    spawns = _missing_editor(monkeypatch)
    view, _host, work, frozen, _candidate = _preview_ready(tmp_path)
    assert view._can_diff() is True

    view.open_diff()

    assert spawns == []
    assert view._frozen_identity()[1] == A.FROZEN_OK
    folders = [p for p in (work / "compare").iterdir() if p.is_dir()]
    assert (folders[0] / "reference.bib").read_bytes() == frozen.read_bytes()
    # 身份核得出来 → 不许有降级说明混进 notice。
    assert "旧任务没有冻结快照" not in view.notice
    assert "已变动" not in view.notice


def test_frozen_bib_sha_mismatch_disables_diff_and_names_both_shas(
        tmp_path, monkeypatch):
    """判据 2：sha 不符 → 禁用对比、提示期望/实际 sha、**绝不**起编辑器、不冒充。"""
    spawns = _missing_editor(monkeypatch)
    view, host, work, frozen, _candidate = _preview_ready(tmp_path)
    stale_sha = "0" * 64
    view._frozen_bib_sha = stale_sha
    actual = A.file_sha256(str(frozen))
    other = _bib(tmp_path / "other.bib", "Something Else")
    view._source_bib = str(other)   # 有可退化的 source_bib，也不许拿它顶替
    view.target_bib = str(other)

    assert view._can_diff() is False, "sha 不符时对比入口必须禁用"
    view.open_diff()

    assert spawns == [], "sha 不符时绝不能起编辑器进程"
    assert not (work / "compare").exists(), "sha 不符时不许落对比副本"
    assert "已变动" in view.error
    assert stale_sha[:16] in view.error and actual[:16] in view.error
    events = [fields for event, fields in host.marks if event == "bib_ai_diff"]
    assert events and events[-1]["opened"] is False
    assert events[-1]["frozen_kind"] == A.FROZEN_MISSING


def test_frozen_bib_absent_degrades_loudly_and_never_writes(tmp_path, monkeypatch):
    """判据 3：旧任务没 frozen_bib → 退化 source_bib，但 notice 必须写明「未冻结」。"""
    spawns = _missing_editor(monkeypatch)
    view, host, work, frozen, _candidate = _preview_ready(tmp_path, with_frozen=False)
    before = A.file_sha256(str(frozen))
    assert view._can_diff() is True

    view.open_diff()

    assert spawns == []
    assert view._frozen_identity()[1] == A.FROZEN_LEGACY
    assert "旧任务没有冻结快照" in view.notice
    assert str(frozen) in view.notice
    folders = [p for p in (work / "compare").iterdir() if p.is_dir()]
    assert (folders[0] / "reference.bib").read_bytes() == frozen.read_bytes()
    # 只读：退化用的是源文件本身，绝不能被写动。
    assert A.file_sha256(str(frozen)) == before
    events = [fields for event, fields in host.marks if event == "bib_ai_diff"]
    assert events and events[-1]["frozen_kind"] == A.FROZEN_LEGACY
    assert events[-1]["degraded"] is True


def test_no_frozen_and_no_fallback_disables_diff_with_reason(tmp_path):
    """判据 4：三条都取不到 → 禁用 + 说清原因。"""
    view, _host, work, frozen, _candidate = _preview_ready(
        tmp_path, with_source=False, with_frozen=False)
    frozen.unlink()
    view.target_bib = ""

    assert view._can_diff() is False
    view.open_diff()

    assert "找不到冻结的稿件版 Bib" in view.error
    assert not (work / "compare").exists()
    assert view.phase == "preview", "对比失败不能改阶段"


def test_compare_requires_valid_preview(tmp_path):
    view, _host, _work, _frozen, _candidate = _preview_ready(tmp_path)
    view.phase = "waiting"
    view.preview_id = ""
    view.open_diff()
    assert "先「生成拟应用结果」" in view.error
    assert view.phase == "waiting", "对比失败不能改阶段"


# ── (f) 完成态日志 ──────────────────────────────────────────────────────────
def test_delivery_log_records_task_compile_and_final(tmp_path):
    view, host, work, _frozen, _candidate = _preview_ready(tmp_path)
    final = work / "delivered"
    final.mkdir()
    payload = {
        "op": "deliver", "ok": True, "delivered": True,
        "task_id": "T-1", "preview_id": "pv-abc",
        "final": str(final), "compile_status": "编译成功",
    }
    asyncio.run(view._finish_delivery(payload, use_original=False))

    assert view.phase == "applied"
    assert view.delivery_ok is True
    assert view._final_dir == str(final)
    text = "\n".join(view.delivery_log)
    assert "任务号：T-1" in text
    assert "编译状态：成功" in text
    assert f"交付目录：{final}" in text
    # 日志与状态同源：宿主收到的日志行里也有同一个 final。
    assert any(str(final) in line for line in host.page.logged)
    assert view.notice == "初排已交付"


def test_delivery_failure_also_writes_log_and_keeps_phase():
    view, host = _view()
    view.task_id = "T-fail"
    view.work_dir = "/tmp/work"
    view.phase = "waiting"
    bridge = _Bridge(lines=[json.dumps({"op": "deliver", "ok": False,
                                       "error": "编译失败"})], code=0)
    view._bridge = bridge
    asyncio.run(view._deliver(use_original=True))

    assert view.phase == "waiting", "交付失败不能把阶段推到 applied"
    assert view.delivery_ok is False
    text = "\n".join(view.delivery_log)
    assert "任务号：T-fail" in text
    assert "编译状态：" in text
    assert "失败原因：" in text and "编译失败" in text
    assert "交付目录：（交付失败，引擎未返回最终路径）" in text
    assert not any("初排已交付" in note for note in host.notes)


def test_delivery_without_final_never_reports_success():
    view, host = _view()
    view.task_id = "T-nofinal"
    view.phase = "waiting"
    bridge = _Bridge(lines=[json.dumps({"op": "deliver", "ok": True,
                                       "delivered": True, "task_id": "T-nofinal",
                                       "compile_status": "编译成功"})], code=0)
    view._bridge = bridge
    asyncio.run(view._deliver(use_original=True))

    assert view.delivery_ok is False
    assert view.phase != "applied"
    assert "引擎没有返回最终交付路径" in view.error
    assert not any("初排已交付" in note for note in host.notes)
    assert any("引擎没有返回最终交付路径" in line for line in view.delivery_log)


def test_status_delivery_block_is_the_single_authority():
    view, _host = _view()
    view._receive_status({
        "phase": "delivered", "task_id": "T-1", "preview_id": "pv-1",
        "target_bib": "/tmp/t.bib", "source_bib": "/tmp/s.bib",
        "delivery": {"delivered": True, "final": "/tmp/out/final",
                     "compile_status": "编译成功"},
    })
    assert view.phase == "applied"
    assert view.delivery_ok is True
    assert view._final_dir == "/tmp/out/final"
    assert view.delivery_compile == "成功"


def test_status_frozen_bib_drives_left_side_identity(tmp_path):
    """红线2-⑦ 的真实入口：身份来自 `status.frozen_bib`，不是 `source_bib`。

    验收那次 FAIL 走的就是这条路：status 里 `source_bib` 与 `target_bib` 都在，
    但冻结快照才是左侧唯一合法的身份来源。
    """
    view, _host = _view()
    work = tmp_path / "work"
    work.mkdir()
    snapshot = _bib(tmp_path / "snapshot.bib", "Frozen Title")
    source = _bib(tmp_path / "source.bib", "Source Title")
    target = _bib(tmp_path / "target.bib", "Target Title")
    view.work_dir = str(work)

    view._receive_status({
        "phase": "previewed", "task_id": "T-1", "preview_id": "pv-1",
        "target_bib": str(target), "source_bib": str(source),
        "frozen_bib": {"path": str(snapshot),
                       "sha256": A.file_sha256(str(snapshot))},
    })

    path, kind, message = view._frozen_identity()
    assert kind == A.FROZEN_OK and message == ""
    assert path == str(snapshot), "左侧必须是冻结快照，不是 source/target"

    # 快照被改动 → 立刻降级为「核不出来」，且不会退回 source/target 冒充。
    snapshot.write_text("@article{z,\n  title = {Edited},\n}\n", encoding="utf-8")
    path, kind, message = view._frozen_identity()
    assert path == "" and kind == A.FROZEN_MISSING
    assert "已变动" in message


def test_status_without_frozen_bib_is_treated_as_legacy(tmp_path):
    """B 还没落地 `frozen_bib` 时（旧 /1 任务）不能崩，也不能静默退化。"""
    view, _host = _view()
    work = tmp_path / "work"
    work.mkdir()
    source = _bib(tmp_path / "source.bib", "Source Title")
    view.work_dir = str(work)

    view._receive_status({
        "phase": "previewed", "task_id": "T-1", "preview_id": "pv-1",
        "target_bib": str(source), "source_bib": str(source),
    })

    assert view._frozen_bib_path == "" and view._frozen_bib_sha == ""
    path, kind, message = view._frozen_identity()
    assert kind == A.FROZEN_LEGACY
    assert path == str(source)
    assert "旧任务没有冻结快照" in message


# ── (g) 成功卡主操作 = 打开交付目录 ─────────────────────────────────────────
def test_success_card_main_action_opens_final(tmp_path):
    view, host, work, _frozen, _candidate = _preview_ready(tmp_path)
    final = work / "delivered"
    final.mkdir()
    view.phase = "applied"
    view.delivery_ok = True
    view.delivery_compile = "成功"
    view._final_dir = str(final)

    view.body_only(compact=True)
    tree = view.build()
    text = _blob(tree)
    assert "打开交付目录" in text

    opener = [n for n in _walk(tree) if getattr(n, "content", None) == "打开交付目录"]
    assert len(opener) == 1
    opener[0].on_click(None)
    assert host.app.opened == [str(final)], "opener 必须收到引擎返回的 final"
    events = [fields for event, fields in host.marks if event == "bib_ai_open_final"]
    assert events and events[-1]["path"] == str(final)


def test_open_delivery_without_final_fails_loudly():
    view, host = _view()
    view._final_dir = ""
    view._open_delivery_dir()
    assert "引擎没有返回最终交付路径" in view.error
    assert host.app.opened == []


# ── (h) .DS_Store 边界（GUI 侧断言；引擎实现依赖 A 包）────────────────────
_ENGINE = Path(__file__).resolve().parents[2] / "Engine"


def _load_convert():
    """惰性 import 引擎模块；不污染全局 sys.path。"""
    added = str(_ENGINE) not in sys.path
    if added:
        sys.path.insert(0, str(_ENGINE))
    try:
        from slcn import ai_workflow_convert as mod
    finally:
        if added:
            try:
                sys.path.remove(str(_ENGINE))
            except ValueError:
                pass
    return mod


def _ds_store_ignored() -> bool:
    """能力探测：引擎是否已经把 `.DS_Store` 当噪音忽略。"""
    try:
        mod = _load_convert()
        return ".DS_Store" in mod._publication_ignore(".", [".DS_Store", "a.tex"])
    except Exception:  # noqa: BLE001 - 引擎未就绪就跳过，不伪装通过
        return False


_DS_READY = _ds_store_ignored()
_DS_WHY = ("引擎侧 .DS_Store 忽略规则由 A 包实现（Engine/slcn/ai_workflow_convert.py"
           " 属 A owner）；GUI 侧断言先写好，等 A 冻结后自动生效，不放宽断言。")


@pytest.mark.skipif(not _DS_READY, reason=_DS_WHY)
def test_ds_store_is_not_copied_into_delivery():
    mod = _load_convert()
    ignored = mod._publication_ignore(".", [".DS_Store", "a.tex", ".aiw"])
    assert ".DS_Store" in ignored
    assert "a.tex" not in ignored


@pytest.mark.skipif(not _DS_READY, reason=_DS_WHY)
def test_ds_store_does_not_change_directory_fingerprint(tmp_path):
    mod = _load_convert()
    root = tmp_path / "delivered"
    root.mkdir()
    (root / "main.tex").write_text("x\n", encoding="utf-8")
    before = mod._tree_shas(str(root))
    (root / ".DS_Store").write_bytes(b"\x00\x01junk")
    after = mod._tree_shas(str(root))
    assert after == before, ".DS_Store 是系统噪音，不该让交付目录指纹变化"


def test_real_changes_still_change_fingerprint(tmp_path):
    """反例：真实新增/改动仍要改变指纹（这条与 A 包无关，现在就成立）。"""
    mod = _load_convert()
    root = tmp_path / "delivered"
    root.mkdir()
    (root / "main.tex").write_text("x\n", encoding="utf-8")
    before = mod._tree_shas(str(root))
    (root / "extra.tex").write_text("y\n", encoding="utf-8")
    added = mod._tree_shas(str(root))
    assert added != before
    (root / "main.tex").write_text("z\n", encoding="utf-8")
    assert mod._tree_shas(str(root)) != added


def test_symlink_in_delivery_still_blocks(tmp_path):
    mod = _load_convert()
    root = tmp_path / "delivered"
    root.mkdir()
    (root / "main.tex").write_text("x\n", encoding="utf-8")
    try:
        os.symlink(str(root / "main.tex"), str(root / "link.tex"))
    except (OSError, NotImplementedError):  # pragma: no cover - 平台不支持
        pytest.skip("该平台不支持符号链接")
    with pytest.raises(ValueError):
        mod._tree_shas(str(root))
