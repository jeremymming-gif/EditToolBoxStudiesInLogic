"""AIW 任务身份必须纳入语种/样式/骨架（2026-11-27 缺陷 b）。

背景：初排 AI 辅助（AIW）用 store 里的 ``bib_ai.active_initial`` 恢复上次任务，
旧身份只比 ``(source, out)``。同一稿件改了语种后重新进入，旧冻结 stage 仍被
恢复（stage 的 SLCN-MIX.sty 与新语种 en 不符 → No counter 'theorem' defined）。

本文件先用 TDD RED 钉住缺陷：同稿同目录、只改语种 → 旧任务不得恢复；
再 GREEN：语种不变仍恢复；身份三元组稳定可 JSON 序列化；library 分支不变。
全部离线，只用桩，不联网、不写稿件仓；临时路径只用 /tmp/slcn-aiw-lang-*。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ui import bib_ai_assist as A
from ui.tokens import Tokens


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict = {}

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


class _Host:
    """最小初排宿主：暴露 ai_initial_paths / ai_initial_style_identity /
    build_ai_initial_call / run_task_ui，正好覆盖 BibAiAssistView 的宿主协议。"""

    def __init__(self, source: str, out: str, *, lang: str = "en",
                 sty: str = "", skeleton: str = "", initial: bool = True) -> None:
        self.t = Tokens("light")
        self.tasks: list = []
        self.marks: list = []
        self.notes: list = []
        self.app = SimpleNamespace(store=_FakeStore(), notes_repo="",
                                   window_w=1160, copy_text=lambda text: None)
        self.store = self.app.store
        self.page = SimpleNamespace(run_task=lambda fn: None)
        self.source, self.out = source, out
        self.lang, self.sty, self.skeleton = lang, sty, skeleton
        if initial:
            self.build_ai_initial_call = lambda view, work_dir: None
        self.target_bib = ""
        self.tex_path = ""

    def mark(self, event, **kwargs) -> None:
        self.marks.append((event, kwargs))

    def notify(self, text) -> None:
        self.notes.append(str(text))

    def _render(self) -> None:
        return None

    def set_view(self, view) -> None:
        return None

    def ai_initial_paths(self) -> tuple[str, str]:
        return self.source, self.out

    def ai_initial_style_identity(self) -> tuple[str, str, str]:
        return self.lang, self.sty, self.skeleton

    def finish_ai_initial_call(self) -> None:
        return None

    def run_task_ui(self, fn) -> None:
        self.tasks.append(fn)


def _seed(host: _Host, *, lang: str = "en", task_id: str = "AIW-TEST0001") -> None:
    """模拟上次 prepare 成功写下的 store 记录（含本次修复新增的身份字段）。"""
    host.store.set("bib_ai.active_initial", {
        "inputs": [host.source, host.out],
        "work_dir": "/tmp/slcn-aiw-lang-work",
        "task_id": task_id,
        "style_identity": [lang, "", ""],
    })


def test_language_change_does_not_resume_stale_frozen_stage():
    """核心 RED：同稿同目录只把语种从 en 改成 zh，旧任务不得被恢复。"""
    host = _Host("/tmp/slcn-aiw-lang-src/main.tex", "/tmp/slcn-aiw-lang-out",
                 lang="zh")
    _seed(host, lang="en")
    view = A.BibAiAssistView(host, purpose="initial")

    view.activate()

    assert view.task_id == "", "改语种后仍恢复了旧冻结 stage（AIW 会再次交付 SLCN-MIX）"
    assert not host.tasks, "不该调度旧任务的 refresh_status"
    assert not view.work_dir


def test_same_language_still_resumes_task():
    """反例：语种等身份不变时，旧任务必须照常恢复（不能因修复而失去可用性）。"""
    host = _Host("/tmp/slcn-aiw-lang-src/main.tex", "/tmp/slcn-aiw-lang-out",
                 lang="en")
    _seed(host, lang="en")
    view = A.BibAiAssistView(host, purpose="initial")

    view.activate()

    assert view.task_id == "AIW-TEST0001"
    assert len(host.tasks) == 1


def test_style_identity_is_stable_json_serializable_tuple():
    """身份三元组必须可稳定 JSON 序列化且可比较（store 可能落 JSON）。"""
    host = _Host("/tmp/slcn-aiw-lang-src/main.tex", "/tmp/slcn-aiw-lang-out",
                 lang="en", sty="SLEN.sty", skeleton="/tmp/slcn-aiw-lang-skel")
    view = A.BibAiAssistView(host, purpose="initial")

    ident = view._task_style_identity()

    assert ident == ("en", "SLEN.sty", "/tmp/slcn-aiw-lang-skel")
    assert json.loads(json.dumps(list(ident))) == list(ident)
    assert tuple(json.loads(json.dumps(ident))) == ident


def test_library_task_identity_and_store_shape_unchanged():
    """library 分支行为不变：_task_inputs 仍两项，store 记录不增新字段。"""
    host = _Host("/tmp/slcn-aiw-lang-src/main.tex", "/tmp/slcn-aiw-lang-out",
                 initial=False)
    view = A.BibAiAssistView(host, purpose="library", taskbook_version=3)
    view.target_bib = "/tmp/slcn-aiw-lang-refs.bib"

    assert view._task_inputs() == [view.target_bib, ""]
    assert view._task_style_identity() == ("", "", "")

    view.work_dir = "/tmp/slcn-aiw-lang-work-lib"
    view.task_id = "AIW-LIB00001"
    view._remember_task()
    assert host.store.get("bib_ai.active_library") == {
        "inputs": [view.target_bib, ""],
        "work_dir": "/tmp/slcn-aiw-lang-work-lib",
        "task_id": "AIW-LIB00001",
    }


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
