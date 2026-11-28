"""整期排序控件、换期保护及引擎 JSON 传输契约。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from test_b112_post_b import _build_issue, _walk
from services import engine_bridge as EB


def button(root, label, *, index=0):
    return [control for control in _walk(root)
            if isinstance(control, (ft.FilledButton, ft.OutlinedButton, ft.TextButton))
            and control.content == label][index]


def text_values(root):
    return [control.value for control in _walk(root) if isinstance(control, ft.Text)]


def click(root, label, *, index=0):
    control = button(root, label, index=index)
    assert not control.disabled
    control.on_click(SimpleNamespace(control=control))


def draft(issue, *, status="preview", docx=None, error=None):
    payload = {
        "schema": 1, "operation": "order_draft", "status": status, "issue": str(issue),
        "warnings": [], "docx": str(docx) if docx else None, "rows": [
            {"folder": "Alpha", "title": "甲的题名", "authors": ["甲", "乙"], "state": "ok"},
            {"folder": "Beta", "title": "乙的题名", "authors": [], "state": "missing_field"},
            {"folder": "Extras", "title": "", "authors": [], "state": "missing_tex"},
        ],
    }
    if error:
        payload["error"] = error
    return payload


def rename(issue, *, status="preview", viable=True, **fields):
    return {
        "schema": 1, "operation": "renamefolders", "status": status,
        "issue": {"path": str(issue), "device": 1, "inode": 1}, "warnings": [],
        "plan_id": "plan-1" if viable else None, "can_apply": viable,
        "renames": [
            {"old": "Beta", "new": "01 Beta", "main_tex": "Beta.tex"},
            {"old": "Alpha", "new": "02 Alpha", "main_tex": "Alpha.tex"},
        ],
        "excluded": ["Extras"], "conflicts": [], **fields,
    }


def build_flow(issue):
    page, _ = _build_issue()
    page.set_field("issue_dir", str(issue))
    page._on_issue_changed()
    page._nav_chips["push"].on_click(None)
    assert page.selected == "dirpush"
    root = page._content_box
    queued, calls, dialogs, opened = [], [], [], []
    replies = []
    page.page.run_task = lambda coro, *args: queued.append((coro, args))
    page.page.show_dialog = dialogs.append
    page.page.pop_dialog = dialogs.pop
    page.app.open_path = opened.append

    def begin_run(call):
        assert not page.app.running
        page.app.running = True
        page.app.cancel_requested = False
        page.refresh_running()
        return True

    def finish_run(call, code):
        page.app.running = False
        page.refresh_running()

    page.app.begin_run = begin_run
    page.app.finish_run = finish_run
    tracked = []
    page.app.track_proc = tracked.append

    def drain():
        while queued:
            coro, args = queued.pop(0)
            asyncio.run(coro(*args))

    return SimpleNamespace(page=page, root=root, issue=issue, replies=replies,
                           calls=calls, dialogs=dialogs, opened=opened, drain=drain,
                           tracked=tracked)


@pytest.fixture
def flow(tmp_path, monkeypatch):
    issue = tmp_path / "issue A"
    issue.mkdir()
    flow = build_flow(issue)
    monkeypatch.setattr(EB, "base_cmd", lambda entry: [entry])

    async def engine(call, **kwargs):
        flow.calls.append(call)
        return flow.replies.pop(0)

    monkeypatch.setattr(EB, "run_json_call", engine)
    return flow


def load_candidates(flow):
    flow.replies.append((0, draft(flow.issue)))
    click(flow.root, "读取排序候选")
    flow.drain()


def test_order_draft_move_exclude_preview_confirm_and_open(flow, tmp_path):
    load_candidates(flow)
    assert flow.calls[-1].argv == ["dirpush.py", "--order-draft", "--issue", str(flow.issue),
                                   "--out", str(tmp_path), "--dry-run", "--json"]
    assert "甲的题名" in text_values(flow.root)
    assert "甲、乙" in text_values(flow.root)
    assert "缺少题名或作者" in text_values(flow.root)
    click(flow.root, "上移", index=1)
    click(flow.root, "排除", index=2)

    out = tmp_path / "outside"
    field = next(c for c in _walk(flow.root) if isinstance(c, ft.TextField)
                 and getattr(c, "_form_label", "") == "排序稿输出文件夹")
    docx = out / "排序决策稿.docx"
    out.mkdir()
    docx.write_bytes(b"current output")
    field.value = str(out)
    flow.replies.append((0, draft(flow.issue, status="completed", docx=docx)))
    click(flow.root, "生成排序决策稿")
    flow.drain()
    assert flow.calls[-1].argv == ["dirpush.py", "--order-draft", "--issue", str(flow.issue),
                                   "--out", str(out), "--json"]
    click(flow.root, "打开排序决策稿")
    assert flow.opened == [str(docx)]

    flow.replies.append((0, rename(flow.issue)))
    click(flow.root, "预览改名")
    flow.drain()
    preview = flow.calls[-1]
    assert preview.argv == ["renamefolders.py", "--issue", str(flow.issue), "--order", "-", "--json"]
    assert json.loads(preview.stdin_text) == {
        "schema": 1, "ordered": ["Beta", "Alpha"], "excluded": ["Extras"],
    }
    assert "Beta → 01 Beta" in text_values(flow.root)
    click(flow.root, "应用改名")
    cancel = next(c for c in flow.dialogs[-1].actions if c.content == "取消")
    cancel.on_click(None)
    flow.drain()
    assert flow.calls[-1] is preview
    click(flow.root, "应用改名")
    assert len(flow.calls) == 3
    assert "只更改" in flow.dialogs[-1].content.content.value
    flow.replies.append((0, rename(flow.issue, status="completed", viable=False)))
    confirm = next(c for c in flow.dialogs[-1].actions if c.content == "应用改名")
    confirm.on_click(None)
    flow.drain()
    assert flow.calls[-1].argv == [*preview.argv[:-1], "--apply", "--plan-id", "plan-1", "--json"]
    assert flow.calls[-1].stdin_text == preview.stdin_text
    assert "Beta → 01 Beta" in text_values(flow.root)
    click(flow.root, "打开期目录")
    assert flow.opened[-1] == str(flow.issue)


@pytest.mark.parametrize("switch_back", [False, True])
def test_switching_issue_revokes_old_apply_confirmation(flow, tmp_path, switch_back):
    load_candidates(flow)
    flow.replies.append((0, rename(flow.issue)))
    click(flow.root, "预览改名")
    flow.drain()
    click(flow.root, "应用改名")
    confirm = next(c for c in flow.dialogs[-1].actions if c.content == "应用改名")
    issue_b = tmp_path / "issue B"
    issue_b.mkdir()
    field = flow.page.fields["issue_dir"][0]
    field.value = str(issue_b)
    field.on_blur(SimpleNamespace(control=field))
    if switch_back:
        field.value = str(flow.issue)
        field.on_blur(SimpleNamespace(control=field))
    confirm.on_click(None)
    flow.drain()
    assert len(flow.calls) == 2
    assert "Beta → 01 Beta" not in text_values(flow.root)


def test_reload_without_candidates_clears_stale_order(flow):
    load_candidates(flow)
    empty = draft(flow.issue, status="blocked", error="本期找不到可读取的精确同名初排 TeX")
    empty["rows"] = []
    flow.replies.append((2, empty))
    click(flow.root, "读取排序候选")
    flow.drain()
    assert "甲的题名" not in text_values(flow.root)
    assert button(flow.root, "预览改名").disabled


def test_stop_before_spawn_cancels_the_registered_process(flow, monkeypatch):
    proc = object()
    killed = []

    async def engine(call, *, on_line, on_proc):
        assert button(flow.root, "读取排序候选").disabled
        flow.page.app.cancel_requested = True
        on_proc(proc)
        return -15, None

    monkeypatch.setattr(EB, "run_json_call", engine)
    monkeypatch.setattr(EB, "kill_tree", killed.append)
    click(flow.root, "读取排序候选")
    flow.drain()
    assert flow.tracked == killed == [proc]
    assert "已取消" in text_values(flow.root)
    assert not button(flow.root, "读取排序候选").disabled


def test_failed_docx_keeps_output_and_reordered_candidates(flow, tmp_path):
    load_candidates(flow)
    click(flow.root, "上移", index=1)
    click(flow.root, "排除", index=2)
    field = next(c for c in _walk(flow.root) if isinstance(c, ft.TextField)
                 and getattr(c, "_form_label", "") == "排序稿输出文件夹")
    field.value = str(tmp_path / "unwritable")
    flow.replies.append((2, draft(flow.issue, status="blocked", error="测试写入失败")))
    click(flow.root, "生成排序决策稿")
    flow.drain()
    assert field.value == str(tmp_path / "unwritable")
    assert not any(c.content == "打开排序决策稿" for c in _walk(flow.root)
                   if isinstance(c, ft.TextButton))
    assert any("测试写入失败" in (value or "") for value in text_values(flow.root))
    flow.replies.append((0, rename(flow.issue)))
    click(flow.root, "预览改名")
    flow.drain()
    assert json.loads(flow.calls[-1].stdin_text)["ordered"] == ["Beta", "Alpha"]


def test_plan_conflict_shows_reason_and_disables_apply(flow):
    load_candidates(flow)
    result = rename(flow.issue, status="blocked", viable=False)
    result["conflicts"] = ["目标 01 Beta 已被文件占用"]
    flow.replies.append((2, result))
    click(flow.root, "预览改名")
    flow.drain()
    assert "目标 01 Beta 已被文件占用" in text_values(flow.root)
    disabled = button(flow.root, "应用改名")
    assert disabled.disabled


def test_recovery_diagnostics_stay_visible_and_use_recovery_only_argv(flow, tmp_path):
    load_candidates(flow)
    recovery_path = str(tmp_path / "state" / "recover.json")
    flow.replies.append((1, rename(flow.issue, status="recovery_required", viable=False,
                                   recovery_id="recover-1", recovery_path=recovery_path,
                                   recovery_operation="renamefolders")))
    click(flow.root, "预览改名")
    flow.drain()
    assert recovery_path in text_values(flow.root)
    assert button(flow.root, "应用改名").disabled
    click(flow.root, "恢复本期改名")
    flow.replies.append((0, rename(flow.issue, status="rolled_back", viable=False)))
    confirm = next(c for c in flow.dialogs[-1].actions if c.content == "恢复本期改名")
    confirm.on_click(None)
    flow.drain()
    assert flow.calls[-1].argv == [
        "renamefolders.py", "--issue", str(flow.issue), "--recover", "recover-1", "--json",
    ]
    assert flow.calls[-1].stdin_text is None


def test_browse_output_reuses_picker_and_guards_cancel_switch_late(flow, tmp_path):
    """§5：复用目录 picker；取消不改值、换期不回填、迟到来访不改新期输出值。"""
    load_candidates(flow)
    panel = flow.page._issue_order_panel
    field = panel._output
    clicks, guards, on_results = [], [], []

    def fake_browse(*args, **kwargs):
        clicks.append(kwargs)
        guards.append(kwargs["result_guard"])
        on_results.append(kwargs["on_result"])
        assert kwargs["ref"] == [field]
        assert kwargs["kind"] == "dir"
        # 模拟真实 picker：确认写值前先查 guard，写后调 on_result。
        if guards[-1]():
            field.value = str(tmp_path / "chosen")
        on_results[-1](str(tmp_path / "chosen"))

    flow.page.app.browse_field = fake_browse
    click(panel._root, "浏览…")
    assert len(clicks) == 1 and field.value == str(tmp_path / "chosen")

    # 取消不改值：on_result(None) 不写字段（真实 picker 取消路径）。
    field.value = str(tmp_path / "manual")
    before = field.value
    on_results[-1](None)
    assert field.value == before

    # 从 A 切 B 后不回填：模拟 panel 已换期，旧 guard 必失效。
    panel.issue = str(tmp_path / "issue B")
    panel.binding = panel.binding + 1
    assert not guards[-1]()
    old_value = field.value
    on_results[-1](str(tmp_path / "late"))
    assert field.value == old_value

    # 手填与实际保存一致：生成排序决策稿用字段现值。
    panel.issue = str(flow.issue)
    panel.binding = flow.page.current_artifact_binding()
    field.value = str(tmp_path / "typed")
    flow.replies.append((0, draft(flow.issue, status="completed", docx=tmp_path / "d.docx")))
    click(panel._root, "生成排序决策稿")
    flow.drain()
    assert flow.calls[-1].argv[-2] == str(tmp_path / "typed")


def test_receipt_only_on_success_confirm_and_move_exclude_keep_last(flow):
    """§2：只有成功确认产出原始回执；上移/排除不清除上次有效回执。"""
    load_candidates(flow)
    panel = flow.page._issue_order_panel
    # 先预览（viable），再上移/排除 → 应清除预览但不动回执。
    flow.replies.append((0, rename(flow.issue)))
    click(flow.root, "预览改名")
    flow.drain()
    click(flow.root, "上移", index=1)
    click(flow.root, "排除", index=2)
    assert panel.refresh_order_result is None  # 尚无成功确认
    # 重新预览并确认成功
    flow.replies.append((0, rename(flow.issue)))
    click(flow.root, "预览改名")
    flow.drain()
    click(flow.root, "应用改名")
    confirm = next(c for c in flow.dialogs[-1].actions if c.content == "应用改名")
    flow.replies.append((0, rename(flow.issue, status="completed", viable=False)))
    confirm.on_click(None)
    flow.drain()
    receipt = panel.refresh_order_result
    assert receipt and receipt["status"] == "completed"
    assert "已应用改名" in flow.page.app.toasts
    assert "已应用改名" in flow.page._log_buffer
    # 读候选 + 上移/排除不清除回执
    flow.replies.append((0, draft(flow.issue)))
    click(flow.root, "读取排序候选")
    flow.drain()
    click(flow.root, "上移", index=1)
    assert panel.refresh_order_result == receipt
    # 未提交编辑标记待确认，但有效回执保留（D2 优先级：显式 JSON > 有效回执 > 编辑）。
    assert panel.refresh_order_needs_confirmation


def test_rename_unchanged_neutral_and_no_reexpanded_mapping(flow):
    """§5：unchanged 中性状态，不重复展开同名映射，不发成功 Toast。"""
    load_candidates(flow)
    preview = rename(flow.issue)
    preview["renames"] = [{"old": "Alpha", "new": "Alpha"}]
    flow.replies.append((0, preview))
    click(flow.root, "预览改名")
    flow.drain()
    assert "Alpha → Alpha" in text_values(flow.root)
    click(flow.root, "应用改名")
    confirm = next(c for c in flow.dialogs[-1].actions if c.content == "应用改名")
    result = rename(flow.issue, status="unchanged", viable=False)
    result["renames"] = [{"old": "Alpha", "new": "Alpha"}]
    flow.replies.append((0, result))
    confirm.on_click(None)
    flow.drain()
    assert "顺序已确认，目录无需改名" in text_values(flow.root)
    assert "Alpha → Alpha" not in text_values(flow.root)
    assert "已应用改名" not in flow.page.app.toasts
    panel = flow.page._issue_order_panel
    assert panel.refresh_order_result["status"] == "unchanged"


def test_recovery_success_clears_old_receipt_first(flow):
    """§2：改名恢复成功先清旧回执，并发一次失效通知。"""
    load_candidates(flow)
    flow.replies.append((0, rename(flow.issue)))
    click(flow.root, "预览改名")
    flow.drain()
    # 先进入恢复态（recovery_required 不清除候选），保留候选供后续。
    flow.replies.append((1, rename(flow.issue, status="recovery_required", viable=False,
                                   recovery_id="rec-1")))
    click(flow.root, "预览改名")
    flow.drain()
    assert button(flow.root, "恢复本期改名").disabled is False
    # 造一个旧回执（模拟此前成功确认留下的回执）。
    panel = flow.page._issue_order_panel
    panel.refresh_order_result = {"status": "completed", "issue": {"path": str(flow.issue)}}
    panel.refresh_order_needs_confirmation = False
    invalidations = []
    flow.page._issue_paging_panel = SimpleNamespace(
        invalidate_order=lambda: invalidations.append(1), refresh_running=lambda: None)
    click(flow.root, "恢复本期改名")
    confirm = next(c for c in flow.dialogs[-1].actions if c.content == "恢复本期改名")
    flow.replies.append((0, rename(flow.issue, status="rolled_back", viable=False)))
    confirm.on_click(None)
    flow.drain()
    assert panel.refresh_order_result is None
    assert panel.refresh_order_needs_confirmation
    assert len(invalidations) == 1


def test_json_engine_transport_keeps_stdin_and_separates_diagnostics():
    script = ("import json, sys; "
              "print(json.dumps({'schema': 1, 'received': sys.stdin.read()})); "
              "print('{\"schema\": 99}', file=sys.stderr); sys.exit(2)")
    call = EB.EngineCall("renamefolders.py", [sys.executable, "-c", script],
                         stdin_text="已确认顺序")
    diagnostics = []
    code, payload = asyncio.run(EB.run_json_call(call, on_line=diagnostics.append))
    assert code == 2
    assert payload == {"schema": 1, "received": "已确认顺序"}
    assert diagnostics == ['{"schema": 99}']
