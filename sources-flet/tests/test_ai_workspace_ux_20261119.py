"""共享AI工作区：状态、回执、只读完成与有界滚动的回归。

全部匿名夹具/假宿主。render_view()可由Flet渲染harness复用，属于状态注入辅助覆盖，
不证明真实引擎、macOS原生选择器或安装包流程。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from ui import bib_ai_assist as A
from ui.tokens import Tokens
from test_bib_ai_assist_c_20261117 import _Bridge, _walk
from test_taskbook_copy_delivery_20261118 import _Host


def render_view(root: Path, *, state: str = "prepare", purpose: str = "library", host=None):
    """准备关键状态；传真实GUI host可复用同样匿名数据做实际渲染。"""
    root.mkdir(parents=True, exist_ok=True)
    host = host or _Host()
    bib = root / "anonymous.bib"
    bib.write_text("@article{K01,title={Anonymous example},number={3}}\n", encoding="utf-8")
    if purpose == "initial":
        host.build_ai_initial_call = lambda **kwargs: None
        host.ai_initial_paths = lambda: (str(root / "anonymous.tex"), str(root / "output"))
    view = A.BibAiAssistView(host, purpose=purpose, bridge=_Bridge())
    view.target_bib = str(bib)
    if state == "prepare":
        return view
    view.work_dir = str(root / "task")
    view.task_id = "AIW-ANONYMOUS"
    view.scope_frozen = True
    view.phase = "waiting"
    view.task_text = "Anonymous taskbook"
    if state == "api":
        view.channel = "api"
    if state in ("review", "ready", "stale", "done", "blocked"):
        view.imported = True
        view._has_active_reply = True
        view.conflicts = [{"conflict_id": "cf-number", "key": "K01", "field": "number",
                           "original": "3", "reply": "7", "library": "4"}]
        view.library = {"checked": True, "matched": 1, "conflicts": 1, "state": "ok"}
    if state in ("ready", "done"):
        candidate = root / "candidate.bib"
        candidate.write_text("@article{K01,title={Anonymous example},number={4}}\n", encoding="utf-8")
        view.candidate_bib = str(candidate)
        view._candidate_bib_sha = A.file_sha256(str(candidate))
        view._frozen_bib_path = str(bib)
        view._frozen_bib_sha = A.file_sha256(str(bib))
        view.preview_id = "pv-anonymous"
        view.phase = "preview"
    if state == "stale":
        view._preview_dirty = True
    if state == "blocked":
        view.blocked = ["匿名夹具：缺少必要定位信息"]
        view.phase = "blocked"
    if state == "done":
        view.phase = "applied"
        if purpose == "initial":
            final = root / "output" / "Anonymous"
            final.mkdir(parents=True, exist_ok=True)
            view._final_dir = str(final)
            view.delivery_ok = True
            view.delivery_compile = "未执行编译"
            view._operation_receipt = {"delivered": True, "n_applied": 1,
                                       "adopted": [{"item_id": "K01", "action": "bib_verify"}],
                                       "final": str(final)}
        else:
            view._operation_receipt = {"wrote": 1, "target": str(bib),
                                       "writes": [{"kind": "bib", "target": str(bib)}]}
    return view


@pytest.mark.parametrize("purpose", ["initial", "library"])
def test_completed_workspace_has_no_mutable_process_controls(tmp_path, purpose):
    view = render_view(tmp_path, state="done", purpose=purpose)
    view.decisions["cf-number"] = "library"
    before = (view.phase, dict(view.decisions), view.preview_id)
    tree = view.body_only(compact=True)
    assert not any(isinstance(n, (ft.RadioGroup, ft.Checkbox, ft.TextField)) for n in _walk(tree))
    assert not view._conflict_groups and not view._accept_boxes and view._paste_field is None
    assert view._primary_slot == ("done", "open_final")
    assert (view.phase, view.decisions, view.preview_id) == before
    assert len([n for n in _walk(tree) if isinstance(n, ft.FilledButton)]) == 1


@pytest.mark.parametrize("state", ["prepare", "chat", "api", "review", "ready", "stale", "done", "blocked"])
def test_pinned_action_is_outside_the_only_workspace_scroll(tmp_path, state):
    view = render_view(tmp_path, state=state)
    tree = view.body_only(compact=True, pinned_actions=True)
    scrolling = [n for n in _walk(tree) if getattr(n, "scroll", None)]
    assert len(scrolling) == 1
    actions = [n for n in _walk(tree) if getattr(n, "_bib_ai_action_bar", False)]
    assert len(actions) == 1 and actions[0] not in list(_walk(scrolling[0]))
    assert len([n for n in _walk(tree) if isinstance(n, ft.FilledButton)]) == 1


@pytest.mark.parametrize("with_copy", [False, True])
@pytest.mark.parametrize("zoom", [0.8, 1.0, 2.0])
@pytest.mark.parametrize("width", [340, 760, 1400])
def test_initial_ready_actions_have_equal_scaled_button_borders(tmp_path, monkeypatch,
                                                                width, zoom, with_copy):
    view = render_view(tmp_path, state="ready", purpose="initial")
    view.page.t = Tokens("light", zoom=zoom)
    monkeypatch.setattr(view, "_design_width", lambda: width)
    monkeypatch.setattr(view, "_can_diff", lambda: with_copy)
    if with_copy:
        view._compare_paths = (view._frozen_bib_path, view.candidate_bib)
    view.body_only(compact=True, pinned_actions=True)
    row = view._workspace_action_bar.controls[0]
    expected = [view._action_buttons["apply"], view._action_buttons["diff"]]
    if with_copy:
        expected.append(view._action_buttons["copy_compare"])
    assert row.controls == expected
    assert {(button.width, button.height) for button in expected} == {
        (view.t.px(184), view.t.px(36))}
    assert isinstance(expected[0], ft.FilledButton)
    assert all(isinstance(button, ft.OutlinedButton) for button in expected[1:])
    assert all(button.style.visual_density == ft.VisualDensity.STANDARD for button in expected)
    assert row.wrap and row.run_spacing == view.t.sp("1")
    assert all(button.expand is None and callable(button.on_click) for button in expected)
    assert expected[0].disabled is False and expected[1].disabled is (not with_copy)


def test_api_workspace_does_not_render_chat_inputs(tmp_path):
    view = render_view(tmp_path, state="api")
    tree = view.body_only(compact=True)
    assert "request" in view._action_buttons and "import" not in view._action_buttons
    assert not any(isinstance(n, ft.TextField) for n in _walk(tree))
    assert "copy_task" not in view._action_buttons


def test_text_item_is_created_once_and_unexecuted_categories_are_text(tmp_path):
    view = render_view(tmp_path, state="review", purpose="initial")
    view.execute["textfix"] = True
    view.accept["textfix"] = True
    view.tex_path = str(tmp_path / "body.tex")
    view.tex_out = str(tmp_path / "body-copy.tex")
    view._text_items = {"T1": "mispelled → misspelled"}
    view._category_reports = {"typeset": {"status": "not_run"}}
    tree = view.body_only(compact=True)
    text = [n.value for n in _walk(tree) if isinstance(n, ft.Text)]
    assert text.count("mispelled → misspelled") == 1
    assert "proofcheck" not in view._accept_boxes
    assert "初排检查 · 本次未执行" in text


def test_library_scope_ignores_legacy_body_paths_and_extra_categories(tmp_path):
    view = render_view(tmp_path, state="review")
    view.tex_path = str(tmp_path / "legacy-associated.tex")
    view.tex_out = str(tmp_path / "legacy-body-copy.tex")
    view.execute["textfix"] = True
    view.execute["proofcheck"] = True
    view.accept["textfix"] = True
    view.accept["proofcheck"] = True
    view._category_reports = {"typeset": {"status": "completed"},
                              "text": {"status": "completed"}}
    tree = view.body_only(compact=True)
    text = [n.value for n in _walk(tree) if isinstance(n, ft.Text)]
    assert view.execute_scope() == ["bib_verify"]
    assert view.accept_scope() == ["bib_verify"]
    assert set(view._accept_boxes) == {"verify"}
    assert not any("初排检查" in line or "文字纠错" in line for line in text)
    kwargs = view._call_kwargs()
    assert kwargs["execute"] == kwargs["adopt"] == ["bib_verify"]
    assert kwargs["tex"] == kwargs["tex_path"] == kwargs["tex_out"] == ""


def test_legacy_library_tex_task_is_preserved_but_not_resumed(tmp_path):
    host = _Host()
    bib = tmp_path / "legacy.bib"
    tex = tmp_path / "legacy.tex"
    bib.write_text("@article{K01,title={Example}}\n", encoding="utf-8")
    tex.write_text("\\cite{K01}\n", encoding="utf-8")
    stored = {"inputs": [str(bib), str(tex)], "work_dir": str(tmp_path / "old-task"),
              "task_id": "AIW-LEGACY"}
    host.app.store.set("bib_ai.target_bib", str(bib))
    host.app.store.set("bib_ai.tex_path", str(tex))
    host.app.store.set("bib_ai.active_library", stored)
    scheduled = []
    host.run_task_ui = scheduled.append
    bridge = _Bridge()
    view = A.BibAiAssistView(host, bridge=bridge, purpose="library", taskbook_version=3)

    view.activate()

    assert view.target_bib == str(bib)
    assert view.tex_path == ""
    assert not view.task_id and not view.work_dir
    assert not scheduled and not bridge.ran
    assert host.app.store.get("bib_ai.active_library") == stored
    assert host.app.store.get("bib_ai.tex_path") == str(tex)
    assert "重新" in view.notice and "任务" in view.notice


@pytest.mark.parametrize("status", [
    {"entry": "bib", "tex": "", "text_target": "",
     "execute": ["bib_verify", "typeset"], "adopt": ["bib_verify"]},
    {"entry": "bib", "tex": "", "text_target": "",
     "execute": ["bib_verify"], "adopt": ["bib_verify", "text"]},
    {"entry": "bib", "tex": "", "text_target": "",
     "execute": [], "adopt": ["bib_verify"]},
    {"entry": "bib", "tex": "", "text_target": "",
     "adopt": ["bib_verify"]},
    {"tex": "", "text_target": "", "execute": ["bib_verify"],
     "adopt": ["bib_verify"]},
    {"entry": "bib", "phase": "prepared", "execute": ["bib_verify"], "adopt": []},
    {"entry": "bib", "phase": "previewed", "execute": ["bib_verify"], "adopt": []},
    {"entry": "bib", "phase": "previewed", "preview_id": "pv-anon", "tex": "anon.tex",
     "execute": ["bib_verify"], "adopt": []},
    {"entry": "bib", "phase": "previewed", "preview_id": "pv-anon",
     "execute": ["bib_verify"], "adopt": [], "scope": {"adopt": ["text"]}},
])
def test_library_rejects_legacy_or_incomplete_task_scope(tmp_path, status):
    view = render_view(tmp_path, state="ready")
    assert view._library_status_issue(status)


def test_library_status_deactivates_extra_scope_without_deleting_saved_task(tmp_path):
    view = render_view(tmp_path, state="ready")
    view.task_id = "AIW-LEGACY-SCOPE"
    view.work_dir = str(tmp_path / "legacy-task")
    saved = {"inputs": [view.target_bib, ""], "work_dir": view.work_dir,
             "task_id": view.task_id}
    view._store_set(view._task_store_key(), saved)
    status = {"entry": "bib", "tex": "", "text_target": "",
              "execute": ["bib_verify", "typeset"], "adopt": ["bib_verify"]}

    assert view._receive_status(status) is False

    assert view.task_id == "" and view.work_dir == ""
    assert "旧引用库任务已停用" in view.notice
    assert view._store_get(view._task_store_key()) == saved


def test_library_accepts_only_explicit_bibliography_scope(tmp_path):
    view = render_view(tmp_path, state="ready")
    status = {"entry": "bib", "tex": "", "text_target": "",
              "execute": ["bib_verify"], "adopt": ["bib_verify"]}
    assert view._library_status_issue(status) == ""


def test_no_library_value_cannot_be_selected(tmp_path):
    view = render_view(tmp_path, state="ready", purpose="initial")
    view.library.update(matched=0, conflicts=0)
    view.conflicts[0].update(library="", choices=["original", "reply"], default="original")
    tree = view.body_only(compact=True)
    radios = {n.value: n for n in _walk(tree) if isinstance(n, ft.Radio)}
    assert radios["library"].disabled
    assert not radios["original"].disabled and not radios["reply"].disabled
    assert view._conflict_groups["cf-number"].value == "original"
    assert any(isinstance(n, ft.Text) and n.value == "无库值" for n in _walk(tree))
    assert view._initial_library_summary() == "匹配 0 条文献 · 1 项字段差异"
    assert not view.set_decision("cf-number", "library")
    assert view.set_decision("cf-number", "reply") and not view.can_apply()


def test_conflict_expressions_round_trip_and_render_without_changing_choices(tmp_path):
    view = render_view(tmp_path, state="ready", purpose="initial")
    logical = [
        {"conflict_id": "cf-macro", "key": "K01", "field": "month",
         "original": "logical original month", "library": "library month",
         "reply": "logical reply month", "choices": ["original", "reply"],
         "default": "reply", "group": "date",
         "expressions": {"original": "jan", "reply": "{jan}", "ignored": "extra"}},
        {"conflict_id": "cf-concat", "key": "K02", "field": "prefix",
         "original": "logical original prefix", "library": "library prefix",
         "reply": "logical reply prefix", "choices": ["original", "reply", "library"],
         "default": "original", "group": "title",
         "expressions": {"original": "prefix # {Suffix}", "reply": "{prefixSuffix}"}},
    ]

    view._receive_library({"conflicts": logical})
    rows = view.conflicts

    assert [row["conflict_id"] for row in rows] == ["cf-macro", "cf-concat"]
    assert [row["expressions"] for row in rows] == [
        {"original": "jan", "reply": "{jan}"},
        {"original": "prefix # {Suffix}", "reply": "{prefixSuffix}"},
    ]
    for row, source in zip(rows, logical):
        for key in ("conflict_id", "key", "field", "original", "library", "reply", "default", "group"):
            assert row[key] == source[key]
        assert set(row["choices"]) == set(source["choices"])
    assert view.decision_scope() == {"cf-macro": "reply", "cf-concat": "original"}

    tree = view.body_only(compact=True)
    visible = {node.value for node in _walk(tree) if isinstance(node, ft.Text)}
    assert {"jan", "{jan}", "prefix # {Suffix}", "{prefixSuffix}"} <= visible
    assert {"library month", "library prefix"} <= visible
    assert "logical original month" not in visible and "logical reply month" not in visible
    assert "logical original prefix" not in visible and "logical reply prefix" not in visible


@pytest.mark.parametrize("metadata, expected_expressions", [
    (None, None),
    (["not", "a", "mapping"], None),
    ({"original": "  {exact original}  ", "reply": 17, "extra": "ignored"},
     {"original": "  {exact original}  "}),
    ({"original": 42, "reply": None, "extra": "ignored"}, None),
])
def test_conflict_expression_metadata_is_optional_and_validated_by_type(
        tmp_path, metadata, expected_expressions):
    view = render_view(tmp_path, state="ready", purpose="initial")
    conflict = {"conflict_id": "cf-fallback", "key": "K03", "field": "title",
                "original": "logical original", "library": "logical library",
                "reply": "logical reply", "choices": ["original", "reply"],
                "default": "original", "group": "title"}
    if metadata is not None:
        conflict["expressions"] = metadata

    view._receive_library({"conflicts": [conflict]})
    row = view.conflicts[0]
    assert row["original"] == "logical original" and row["reply"] == "logical reply"
    if expected_expressions is None:
        assert "expressions" not in row
    else:
        assert row["expressions"] == expected_expressions

    tree = view.body_only(compact=True)
    visible = {node.value for node in _walk(tree) if isinstance(node, ft.Text)}
    assert ("  {exact original}  " if expected_expressions else "logical original") in visible
    assert "logical reply" in visible
    assert "logical library" in visible


def test_decision_change_refreshes_workspace_in_place_not_via_full_rerender(tmp_path):
    """B-393 #1: 字段差异三选一必须就地刷新；裁决/横幅正确且宿主不整树重排。"""
    view = render_view(tmp_path, state="ready", purpose="initial")
    view._receive_library({"conflicts": [{
        "conflict_id": "cf-b393", "key": "K01", "field": "number",
        "original": "3", "library": "4", "reply": "7",
    }]})
    scroll = view.body_only(compact=True, pinned_actions=True)
    rendered = []

    real_host_render = view.page._render

    def fake_page_render():
        rendered.append(True)
        real_host_render()  # _Host._render() 无副作用；真实宿主走 _SingleAiHost

    view.page._render = fake_page_render
    group = view._conflict_groups["cf-b393"]
    group.on_change(SimpleNamespace(control=SimpleNamespace(value="library")))

    assert view.decisions["cf-b393"] == "library"
    assert view._preview_dirty is True
    assert len(rendered) == 1, "视图只应请求一次宿主刷新（就地刷新由宿主实现）"
    refreshed = view.body_only(compact=True, pinned_actions=True, previous=scroll)
    assert refreshed is scroll, "就地刷新必须复用同一滚动容器（offset 不归零）"
    assert any("已改选" in n.value for n in _walk(refreshed)
               if isinstance(n, ft.Text)), "横幅必须出现"


def test_result_path_on_change_and_clear_never_rerender_full_flow(tmp_path):
    """B-393 #2: 路径每字输入/✕清空只刷新视图内存状态，不触发整树重排。"""
    view = render_view(tmp_path, state="waiting", purpose="initial")
    view.body_only(compact=True)
    renders = []
    view.page._render = lambda: renders.append(True)
    field = view._refs["result_path"][0]

    for ch in "/anonymous/very/long/path/to/result.bib":
        field.value = field.value + ch
        field.on_change(SimpleNamespace(control=field))
    assert view.result_path == "/anonymous/very/long/path/to/result.bib"
    assert renders == [], "结果路径输入不得触发任何整树重排（含就地重绘），以保住焦点与滚动"

    before_clear = len(renders)
    field._clear_button.on_click(SimpleNamespace(control=field._clear_button))
    assert view.result_path == ""
    assert renders == [], "✕ 清空同样不得触发重排"
    assert field._clear_button is not None
    assert field._clear_button.visible is False, "清空后 ✕ 应隐藏"  # sync_clearable 就地生效


def test_conflict_selection_invalidates_candidate_and_sends_only_decisions(tmp_path):
    view = render_view(tmp_path, state="ready", purpose="initial")
    view._receive_library({"conflicts": [{
        "conflict_id": "cf-macro", "key": "K01", "field": "month",
        "original": "jan", "library": "lib-month", "reply": "jan",
        "choices": ["original", "reply"], "default": "original",
        "expressions": {"original": "jan", "reply": "{jan}"},
    }]})
    tree = view.body_only(compact=True)
    group = view._conflict_groups["cf-macro"]
    group.on_change(SimpleNamespace(control=SimpleNamespace(value="reply")))

    assert view.phase == "waiting"
    assert view.preview_id == "" and view.candidate_bib == ""
    assert view.decisions == {"cf-macro": "reply"}
    kwargs = view._call_kwargs()
    assert kwargs["decisions"] == kwargs["decide"] == {"cf-macro": "reply"}
    assert all("jan" not in str(key) and "{jan}" not in str(value)
               for key, value in kwargs["decisions"].items())
    assert tree is not None


def test_initial_typeset_remains_selectable_for_adoption(tmp_path):
    view = render_view(tmp_path, state="review", purpose="initial")
    view.execute["textfix"] = True
    view.accept["textfix"] = True
    view._category_reports = {"typeset": {"status": "completed"}}
    tree = view.body_only(compact=True)
    assert "typeset" in view.execute_scope() and "typeset" in view.accept_scope()
    assert "text" in view.execute_scope() and "text" in view.accept_scope()
    assert "proofcheck" in view._accept_boxes
    assert "textfix" in view._accept_boxes
    assert not any(isinstance(n, ft.Text) and n.value == "初排检查 · 仅报告" for n in _walk(tree))


@pytest.mark.parametrize("wrote", [0, 1])
def test_library_apply_requires_real_write_receipt(tmp_path, monkeypatch, wrote):
    view = render_view(tmp_path, state="ready")
    async def run(op, call, label, **kwargs):
        return {"ok": True, "op": "apply", "wrote": wrote, "note": "没有可写类别。",
                "target": view.target_bib}
    monkeypatch.setattr(view, "_run_r3", run)
    before = Path(view.target_bib).read_bytes()
    asyncio.run(view.apply())
    assert Path(view.target_bib).read_bytes() == before, "假桥从不写文件"
    assert (view.phase == "applied") is bool(wrote)
    assert view.can_undo() is bool(wrote)
    tree = view.body_only(compact=True)
    if not wrote:
        assert "open_final" not in view._action_buttons and "undo" not in view._action_buttons
        assert "本次未写入文件" in view.notice
        assert not any(getattr(n, "_bib_ai_disclosure", "") == "adoption_history" for n in _walk(tree))


def test_undo_waits_for_confirmation_and_rechecks_task_identity(tmp_path, monkeypatch):
    view = render_view(tmp_path, state="done")
    confirmations, tasks = [], []
    monkeypatch.setattr(view, "_confirm", lambda **kwargs: confirmations.append(kwargs))
    monkeypatch.setattr(view, "_task", tasks.append)
    view.body_only(compact=True)
    view._action_buttons["undo"].on_click(None)
    assert len(confirmations) == 1 and not tasks and view.phase == "applied"
    view.task_id = "AIW-OTHER"
    confirmations.pop()["callback"]()
    assert not tasks and view.phase == "applied"


def test_read_only_history_respects_disabled_bibliography_adoption(tmp_path):
    view = render_view(tmp_path, state="done")
    view.decisions["cf-number"] = "library"
    view.accept["verify"] = False
    text = [n.value for n in _walk(ft.Column(controls=view._adoption_history())) if isinstance(n, ft.Text)]
    assert any("保留原值：3" in line for line in text)
    assert not any("用库值：4" in line for line in text)


def test_disclosure_keeps_user_expansion_across_rerender(tmp_path):
    view = render_view(tmp_path, state="review")
    tile = view._disclosure("task_details", "任务详情", [])
    tile.on_change(SimpleNamespace(data=True, control=tile))
    assert view._disclosure("task_details", "任务详情", []).expanded
    tile.on_change(SimpleNamespace(data=False, control=tile))
    assert not view._disclosure("task_details", "任务详情", []).expanded


@pytest.mark.parametrize("case", ["same_applied", "same_preview", "foreign", "missing", "failure",
                                  "switch_during_apply", "switch_during_status", "edit_during_status"])
def test_missing_write_receipt_is_bound_to_current_task(tmp_path, monkeypatch, case):
    view = render_view(tmp_path, state="ready")
    original = (view.task_id, view.target_bib, view.candidate_bib, view.preview_id)
    async def apply_receipt(*args, **kwargs):
        if case == "switch_during_apply":
            view.set_work_dir(str(tmp_path / "different-work"))
            view.error = "new-task-message"
            return {"ok": True, "op": "apply", "wrote": 1}
        return {"ok": True, "op": "apply"}
    async def status_receipt(*args, **kwargs):
        if case == "failure":
            return None
        if case == "missing":
            return {"ok": True, "phase": "applied"}
        if case == "switch_during_status":
            view.set_task_id("AIW-NEW")
            view.error = "new-task-message"
        if case == "edit_during_status":
            view._invalidate_preview()
        return {"ok": True, "entry": "bib", "tex": "", "text_target": "",
                "execute": ["bib_verify"], "adopt": ["bib_verify"],
                "task_id": "AIW-FOREIGN" if case == "foreign" else original[0],
                "phase": "previewed" if case == "same_preview" else "applied",
                "source_bib": str(tmp_path / "foreign.bib") if case == "foreign" else original[1],
                "target_bib": str(tmp_path / "foreign.bib") if case == "foreign" else original[1],
                "preview_id": original[3], "candidate_bib": original[2]}
    monkeypatch.setattr(view, "_run_r3", apply_receipt)
    monkeypatch.setattr(view, "_read_status", status_receipt)
    asyncio.run(view.apply())
    assert view.target_bib == original[1]
    assert not view.running
    assert view.can_undo() is (case == "same_applied")
    if case in ("foreign", "missing", "failure"):
        assert view.error
    if case == "failure":
        assert (view.task_id, view.candidate_bib, view.preview_id) == (original[0], original[2], original[3])
    if case.startswith("switch_"):
        assert view.error == "new-task-message" and not view._operation_receipt
    if case == "same_applied":
        assert view.phase == "applied" and view._library_completion_title() == "本次处理已完成"
    elif case == "same_preview":
        assert view.phase == "preview"
    else:
        assert view.phase != "applied"


@pytest.mark.parametrize("kinds,heading", [(["bib"], "已更新 Bib"),
                                         ([], "本次处理已完成")])
def test_library_completion_uses_actual_written_file_kinds(tmp_path, monkeypatch, kinds, heading):
    view = render_view(tmp_path, state="ready")
    paths = {"text": str(tmp_path / "body-copy.tex"), "bib": str(tmp_path / "output.bib")}
    receipt = {"ok": True, "op": "apply", "wrote": max(1, len(kinds)),
               "writes": [{"kind": kind, "target": paths[kind]} for kind in kinds]}
    if kinds:
        receipt["target"] = paths[kinds[0]]
    async def run(*args, **kwargs):
        return receipt
    monkeypatch.setattr(view, "_run_r3", run)
    asyncio.run(view.apply())
    assert view.notice == heading
    tree = view.body_only(compact=True)
    assert [n.value for n in _walk(tree) if getattr(n, "_bib_ai_status", False)] == [heading]


@pytest.mark.parametrize("state", ["ready", "api"])
def test_external_bib_requires_fresh_visible_source_and_cancel_restores_draft(tmp_path, monkeypatch, state):
    view = render_view(tmp_path, state=state)
    old = ("/tmp/prior-reply.md", "@article{K01,title={Prior AI subset}}")
    view.result_path, view.body_text = old
    if state == "ready":
        view.error = "prior-review-message"
    previous = (view.preview_id, view._has_active_reply, view.error)
    confirmations = []
    monkeypatch.setattr(view, "_confirm_import", lambda kind: confirmations.append((kind, view.result_path, view.body_text)))
    tree = view.body_only(compact=True)
    menu = next(n for n in _walk(tree) if getattr(n, "content", None) == "采用完整外部 Bib…")
    menu.on_click(None)
    assert not confirmations and not view.result_path and not view.body_text
    assert view._initial_stage() == "waiting"
    assert (view.preview_id, view._has_active_reply) == previous[:2]
    tree = view.body_only(compact=True)
    assert any(isinstance(n, ft.TextField) for n in _walk(tree)), "完整Bib来源必须可见"
    view._action_buttons["import_external"].on_click(None)
    assert not confirmations, "空来源不得消费隐藏旧回复"
    view.body_text = "@article{K01,title={Explicit complete Bib}}"
    view._action_buttons["import_external"].on_click(None)
    assert confirmations == [("complete_external", "", view.body_text)]
    view._action_buttons["cancel_external_import"].on_click(None)
    assert (view.result_path, view.body_text) == old
    assert (view.preview_id, view._has_active_reply, view.error) == previous


@pytest.mark.parametrize("purpose", ["initial", "library"])
@pytest.mark.parametrize("width", [340, 760, 1100, 1400])
def test_stepper_never_expands_flex_children_under_wrapping_row(tmp_path, monkeypatch, width, purpose):
    view = render_view(tmp_path, state="ready", purpose=purpose)
    monkeypatch.setattr(view, "_design_width", lambda: width)
    tree = view.body_only(compact=True, pinned_actions=True)
    invalid = [n for n in _walk(tree) if isinstance(n, ft.Row) and n.wrap
               and any(getattr(child, "expand", None) is not None for child in n.controls)]
    assert not invalid, "Flet maps expand=False to Expanded(0); Wrap children must omit expand"


# ── B-394 字段差异区批量选择按钮（全选库值 / 全选 AI 值）────────────────────
def _conflicts_with_choices():
    """显式给 choices：ready fixture 无 choices 键会被当全支持，测不出跳过。"""
    return A.parse_conflicts({"conflicts": [
        {"conflict_id": "cf-lib", "key": "K1", "field": "number",
         "original": "3", "library": "4", "reply": "7",
         "choices": ["library", "reply", "original"], "default": "original"},
        {"conflict_id": "cf-nolib", "key": "K2", "field": "title",
         "original": "A", "library": "", "reply": "B",
         "choices": ["reply", "original"], "default": "original"},
        {"conflict_id": "cf-third", "key": "K3", "field": "volume",
         "original": "1", "library": "2", "reply": "3",
         "choices": ["library", "reply", "original"], "default": "original"},
    ]})


def _batch_ready_view(tmp_path, conflicts=None):
    view = render_view(tmp_path, state="ready")
    view._receive_library({"conflicts": conflicts or _conflicts_with_choices()})
    return view


def _batch_button(tree, label):
    buttons = [n for n in _walk(tree)
               if isinstance(n, ft.OutlinedButton) and getattr(n, "content", "") == label]
    assert len(buttons) == 1, f"批量按钮「{label}」应恰好出现一次，实际 {len(buttons)} 次"
    return buttons[0]


def test_batch_select_library_changes_supported_skips_others(tmp_path):
    view = _batch_ready_view(tmp_path)
    assert view.set_decisions_for_value("library") == (2, 1, 0)
    assert view.decisions == {"cf-lib": "library", "cf-third": "library"}
    assert view.decisions.get("cf-nolib") is None, "无库值条目不得写入裁决"
    assert view.decision_scope()["cf-nolib"] == "original", "跳过项保留默认 original"
    assert view._preview_dirty is True and view.preview_id == ""


def test_batch_select_reply_maps_to_reply_and_skips_unsupported(tmp_path):
    conflicts = _conflicts_with_choices()
    # 让 cf-third 不支持 reply，测跳过；其余两条支持 reply。
    conflicts[2]["choices"] = ["library", "original"]
    view = _batch_ready_view(tmp_path, conflicts)
    assert view.set_decisions_for_value("reply") == (2, 1, 0)
    assert view.decisions == {"cf-lib": "reply", "cf-nolib": "reply"}
    assert view.decision_scope()["cf-third"] == "original"
    assert ("bib_ai_batch_decision", {"value": "reply", "changed": 2,
                                      "skipped": 1, "already": 0, "total": 3}) in view.page.marks


def test_batch_decision_mark_and_visible_skip_notice(tmp_path):
    view = _batch_ready_view(tmp_path)
    tree = view.body_only(compact=True, pinned_actions=True)
    _batch_button(tree, "全选库值").on_click(None)
    assert ("bib_ai_batch_decision", {"value": "library", "changed": 2,
                                      "skipped": 1, "already": 0, "total": 3}) in view.page.marks
    refreshed = view.body_only(compact=True)
    assert any(isinstance(n, ft.Text) and "1 条无库值已跳过" in n.value
               for n in _walk(refreshed)), "跳过提示必须是冲突区内可见控件（A1）"
    assert "无库值已跳过" not in view.notice, "跳过提示不得只写通用 notice（R3 不渲染）"


def test_batch_buttons_disabled_when_running_or_no_support(tmp_path):
    conflicts = _conflicts_with_choices()
    for item in conflicts:
        item["choices"] = ["reply", "original"]  # 全部无库值
    view = _batch_ready_view(tmp_path, conflicts)
    tree = view.body_only(compact=True, pinned_actions=True)
    lib_btn = _batch_button(tree, "全选库值")
    assert lib_btn.disabled and lib_btn.tooltip
    assert not _batch_button(tree, "全选 AI 值").disabled
    view.running = True
    tree = view.body_only(compact=True, pinned_actions=True)
    for label in ("全选库值", "全选 AI 值", "全部保留原值"):
        btn = _batch_button(tree, label)
        assert btn.disabled, f"running 时「{label}」必须置灰"
        if label != "全部保留原值":  # 既有按钮保持原样，不补 tooltip
            assert btn.tooltip, f"批量按钮禁用时须给 tooltip 说明（A6）"


def test_batch_decision_no_op_does_not_invalidate(tmp_path):
    conflicts = _conflicts_with_choices()
    for item in conflicts:
        item["default"] = "library"  # 所有支持 library 的条目已选中
    view = _batch_ready_view(tmp_path, conflicts)
    before_dirty = view._preview_dirty
    marks_before = len(view.page.marks)
    assert view.set_decisions_for_value("library") == (0, 1, 2)
    assert view._preview_dirty == before_dirty, "空操作不得失效预览（A8）"
    assert len(view.page.marks) == marks_before, "空操作不得打点（A8）"


def test_batch_decision_refreshes_in_place_not_full_tree(tmp_path):
    view = _batch_ready_view(tmp_path)
    scroll = view.body_only(compact=True, pinned_actions=True)
    rendered = []
    real_host_render = view.page._render

    def fake_page_render():
        rendered.append(True)
        real_host_render()

    view.page._render = fake_page_render
    _batch_button(scroll, "全选库值").on_click(None)

    assert view.decisions == {"cf-lib": "library", "cf-third": "library"}
    assert len(rendered) == 1, "批量点击只应请求一次宿主就地刷新"
    refreshed = view.body_only(compact=True, pinned_actions=True, previous=scroll)
    assert refreshed is scroll, "就地刷新必须复用同一滚动容器（offset 不归零）"
    assert {"cf-lib", "cf-nolib", "cf-third"} <= set(view._conflict_groups), \
        "折叠区条目（cf-third）仍须在冲突组里重建"


def test_batch_unknown_value_never_falls_back_to_original(tmp_path):
    view = _batch_ready_view(tmp_path)
    assert view.set_decisions_for_value("ai") == (0, 0, 0), "入口只认 library/reply（A4）"
    assert view.decisions == {}, "未知值不得静默改稿成 original"
    assert not view.page.marks
