from __future__ import annotations

import json
from types import SimpleNamespace

import flet as ft
import pytest

from services import engine_bridge as EB
from test_b112_post_b import _walk
from test_b116_issue_run import _Page


class PagingPage(_Page):
    def __init__(self, issue):
        super().__init__()
        self.set_field("issue_dir", str(issue))
        self.generation = 0
        self.opened = []
        self.dialogs = []
        self.page.show_dialog = self.dialogs.append
        self.page.pop_dialog = self.dialogs.pop
        self.app.save_settings = lambda **kw: None

    def _safe_update(self, *controls):
        pass

    def _ensure_issue_identity(self):
        pass

    def current_artifact_binding(self):
        return self.generation

    def open_artifact(self, path, *, binding):
        if binding == self.generation:
            self.opened.append(path)

    def switch(self, issue):
        self.set_field("issue_dir", str(issue))
        self.generation += 1


def _button(control, label):
    items = [*_walk(control), *getattr(control, "actions", ())]
    return next(item for item in items if getattr(item, "content", None) == label)


def _has_button(control, label):
    return any(getattr(item, "content", None) == label for item in _walk(control))


def _texts(control):
    return "\n".join(item.value for item in _walk(control) if isinstance(item, ft.Text))


def _result(issue, status, **fields):
    return {
        "schema": 1, "operation": "refreshpages", "status": status,
        "issue": {"path": str(issue), "device": 1, "inode": 8},
        "warnings": [], "can_apply": status == "preview", **fields,
    }


def _summary(issue, *, numbered, excluded=0, source="implicit_all"):
    return {
        "schema": 1, "operation": "refreshpages", "status": "selection_summary",
        "issue": {"path": str(issue), "device": 1, "inode": 8},
        "order_source": source, "numbered_count": numbered,
        "excluded_numbered_count": excluded, "article_count": numbered - excluded,
    }


def _article(*, folder="01 稿件甲", new=None):
    return {
        "folder": folder, "main_tex": f"{folder}/{folder[3:]}.tex",
        "pdf": f"{folder}/{folder[3:]}.pdf", "old": {"first": 2, "last": 4, "count": 3},
        "new": new, "physical_pages": new and new["count"], "recompiled": bool(new),
    }


@pytest.fixture()
def paging(tmp_path, monkeypatch):
    from ui import issue_paging

    issue = tmp_path / "合成一期"
    issue.mkdir()
    page = PagingPage(issue)
    page._issue_order_panel = SimpleNamespace(
        refresh_order_result={"status": "completed", "excluded": []},
        refresh_order_needs_confirmation=False,
    )
    panel = issue_paging.PagingPanel(page)
    calls, replies = [], []
    summary_calls, summary_replies = [], []

    async def run(call, *, on_line, on_proc):
        if "--selection-summary" in call.argv:
            summary_calls.append(call)
            reply = summary_replies.pop(0) if summary_replies else (0, _summary(issue, numbered=1))
            if on_line:
                on_line("合成摘要日志")
            return reply() if callable(reply) else reply
        calls.append(call)
        on_line("合成日志")
        reply = replies.pop(0)
        return reply() if callable(reply) else reply

    monkeypatch.setattr(EB, "base_cmd", lambda entry: [])
    monkeypatch.setattr(EB, "run_json_call", run, raising=False)
    return (page, panel, panel.controls(), issue, calls, replies,
            summary_calls, summary_replies)


def _paging(paging):
    page, panel, view, issue, calls, replies, summary_calls, summary_replies = paging
    return page, panel, view, issue, calls, replies, summary_calls, summary_replies


def test_preview_cancel_apply_and_open_only_this_runs_outputs(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    pdf = issue / "01 稿件甲/稿件甲.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"from this compile")
    toc = issue / "000/目录.pdf"
    toc.parent.mkdir()
    toc.write_bytes(b"from this compile")
    relative_pdf, relative_toc = "01 稿件甲/稿件甲.pdf", "000/目录.pdf"
    article = _article()
    replies.append((0, _result(
        issue, "preview", plan_id="plan-a", order_source="provided",
        toc_strategy="updated",
        articles=[article], writes=[article["main_tex"], relative_pdf, relative_toc],
        toc_outputs=[relative_toc],
    )))

    _button(view, "编译并刷新页码").on_click(None)
    assert calls[0].argv == ["--issue", str(issue), "--json", "--order-result", "-"]
    assert json.loads(calls[0].stdin_text) == page._issue_order_panel.refresh_order_result
    assert "2–4" in _texts(view) and relative_toc in _texts(view)
    assert not _has_button(view, "打开 01 稿件甲 正文 PDF")
    _button(view, "应用刷新计划").on_click(None)
    _button(page.dialogs[-1], "取消").on_click(None)
    assert len(calls) == 1

    def completed():
        assert "正在编译并刷新页码" in _texts(view)
        return 0, _result(
            issue, "completed", scope="issue", rounds=2,
            articles=[_article(new={"first": 1, "last": 3, "count": 3})],
            writes=[relative_pdf, relative_toc], toc_outputs=[relative_toc],
        )

    replies.append(completed)
    _button(view, "应用刷新计划").on_click(None)
    _button(page.dialogs[-1], "开始编译并刷新").on_click(None)
    assert calls[1].argv == ["--issue", str(issue), "--json", "--apply", "--plan-id", "plan-a",
                             "--order-result", "-"]
    assert calls[0].stdin_text == calls[1].stdin_text
    assert "1–3" in _texts(view) and "刷新完成" in _texts(view)
    _button(view, "打开 01 稿件甲 正文 PDF").on_click(None)
    _button(view, "打开 目录.pdf").on_click(None)
    assert page.opened == [str(pdf), str(toc)]
    assert page._engine_lines == ["合成日志", "合成日志"]
    old_button = _button(view, "打开 01 稿件甲 正文 PDF")
    page.switch(issue.parent)
    page.switch(issue)
    old_button.on_click(None)
    assert page.opened == [str(pdf), str(toc)]


def test_switch_invalidates_preview_dialog(paging, tmp_path):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    replies.append((0, _result(issue, "preview", plan_id="old-a",
                               order_source="provided", articles=[_article()])))
    _button(view, "编译并刷新页码").on_click(None)
    _button(view, "应用刷新计划").on_click(None)
    apply_dialog = page.dialogs[-1]
    page.switch(tmp_path / "合成二期")
    second_view = panel.controls()
    _button(apply_dialog, "开始编译并刷新").on_click(None)
    assert len(calls) == 1
    assert not _has_button(second_view, "应用刷新计划")
    page.switch(issue)
    assert not _has_button(panel.controls(), "应用刷新计划")


@pytest.mark.parametrize(("status", "label", "recovery"), [
    ("rolled_back", "已回滚", ""),
    ("recovery_required", "需恢复", "recover-a"),
])
def test_apply_failure_shows_transaction_state_and_recovery(paging, status, label, recovery):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    diagnostic = issue.parent / "诊断"
    diagnostic.mkdir()
    replies.extend([
        (0, _result(issue, "preview", plan_id="plan-a", order_source="provided",
                    articles=[_article()])),
        (1, _result(issue, status, recovery_id=recovery, diagnostic_dir=str(diagnostic),
                    pending_operation="refreshpages", warnings=["注入故障"])),
    ])
    _button(view, "编译并刷新页码").on_click(None)
    _button(view, "应用刷新计划").on_click(None)
    _button(page.dialogs[-1], "开始编译并刷新").on_click(None)
    assert label in _texts(view) and "注入故障" in _texts(view)
    _button(view, "打开诊断目录").on_click(None)
    assert page.opened == [str(diagnostic)]
    assert not _has_button(view, "打开 01 稿件甲 正文 PDF")
    if recovery:
        replies.append((0, _result(issue, "rolled_back")))
        _button(view, "恢复上一轮").on_click(None)
        _button(page.dialogs[-1], "开始恢复").on_click(None)
        assert calls[-1].argv == ["--issue", str(issue), "--json", "--recover", recovery]
        assert "已恢复本期文件" in _texts(view)


def test_regenerating_toc_rebinds_preview(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    replies.append((0, _result(issue, "preview", plan_id="no-regen",
                               order_source="provided", articles=[_article()])))
    _button(view, "编译并刷新页码").on_click(None)
    checkbox = next(c for c in _walk(view) if isinstance(c, ft.Checkbox) and c.label == "重生成目录")
    checkbox.value = True
    checkbox.on_change(SimpleNamespace(control=checkbox))
    assert not _has_button(view, "应用刷新计划")
    replies.extend([
        (0, _result(issue, "preview", plan_id="with-regen", order_source="provided",
                    toc_strategy="regenerated", articles=[_article()])),
        (0, _result(issue, "completed")),
    ])
    _button(view, "编译并刷新页码").on_click(None)
    _button(view, "应用刷新计划").on_click(None)
    _button(page.dialogs[-1], "开始编译并刷新").on_click(None)
    assert calls[1].argv == ["--issue", str(issue), "--json", "--regenerate-toc",
                             "--order-result", "-"]
    assert calls[2].argv == ["--issue", str(issue), "--json", "--apply", "--plan-id", "with-regen",
                             "--regenerate-toc", "--order-result", "-"]


def test_current_order_handoff_is_bound_to_preview_and_apply(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    result = {"status": "completed", "excluded": ["09 附件"], "schema": 1}
    page._issue_order_panel = SimpleNamespace(
        refresh_order_result=result, refresh_order_needs_confirmation=False,
    )
    replies.extend([(0, _result(issue, "preview", plan_id="bound",
                                order_source="provided")),
                    (0, _result(issue, "unchanged"))])
    _button(view, "编译并刷新页码").on_click(None)
    _button(view, "应用刷新计划").on_click(None)
    _button(page.dialogs[-1], "开始编译并刷新").on_click(None)
    assert all(call.argv[-2:] == ["--order-result", "-"] for call in calls)
    assert calls[0].stdin_text == calls[1].stdin_text
    assert json.loads(calls[0].stdin_text) == result
    assert "文件无需更新" in _texts(view)


def test_unconfirmed_order_edit_does_not_block_direct_refresh(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    page._issue_order_panel = SimpleNamespace(
        refresh_order_result=None, refresh_order_needs_confirmation=True,
    )
    replies.extend([(0, _result(issue, "preview", plan_id="direct",
                                order_source="implicit_all", articles=[_article()])),
                    (0, _result(issue, "unchanged"))])
    _button(view, "编译并刷新页码").on_click(None)
    assert _has_button(view, "应用刷新计划")
    _button(view, "应用刷新计划").on_click(None)
    _button(page.dialogs[-1], "开始编译并刷新").on_click(None)
    assert "--order-result" not in calls[0].argv
    assert "所有识别到的篇目" not in _texts(view) or "刷新完成" in _texts(view)


def test_initial_order_state_without_result_shows_summary_but_no_gate(paging):
    page, panel, _, issue, _, _, _, summary_replies = _paging(paging)
    page._issue_order_panel = SimpleNamespace(
        refresh_order_result=None, refresh_order_needs_confirmation=False,
    )
    summary_replies.append((0, _summary(issue, numbered=2)))
    panel.refresh_selection()
    view = panel.view
    assert "本期按当前 2 个编号目录刷新" in _texts(view)
    assert not _has_button(view, "应用刷新计划")


def test_cancelled_apply_and_foreign_pending_transaction(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)

    def stop():
        page.app.stop_run()
        return -15, None

    replies.extend([(0, _result(issue, "preview", plan_id="plan-a",
                                 order_source="provided")), stop,
                    (1, _result(issue, "recovery_required", recovery_id="rename-a",
                                 pending_operation="renamefolders"))])
    _button(view, "编译并刷新页码").on_click(None)
    _button(view, "应用刷新计划").on_click(None)
    _button(page.dialogs[-1], "开始编译并刷新").on_click(None)
    assert "请重新预览核对本期文件" in _texts(view)
    _button(view, "编译并刷新页码").on_click(None)
    assert "到对应步骤恢复" in _texts(view)
    assert not _has_button(view, "恢复上一轮")


def test_restart_uses_implicit_or_saved_without_shell_gate(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    page._issue_order_panel = None
    replies.extend([
        (0, _result(issue, "preview", plan_id="implicit", order_source="implicit_all",
                    excluded=[], articles=[_article()])),
        (0, _result(issue, "preview", plan_id="saved", order_source="saved",
                    excluded=["09 附件"], articles=[_article()])),
    ])
    _button(view, "编译并刷新页码").on_click(None)
    assert _has_button(view, "应用刷新计划")
    panel.order_input.value = str(issue.parent / "排序结果.json")
    panel.order_input.on_change(SimpleNamespace(control=panel.order_input))
    _button(view, "编译并刷新页码").on_click(None)
    assert calls[-1].argv[-2:] == ["--order-result", panel.order_input.value]


def test_stop_before_child_is_tracked(paging, monkeypatch):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    proc = object()
    killed = []

    async def run(call, *, on_line, on_proc):
        if "--selection-summary" in call.argv:
            return 0, _summary(issue, numbered=1)
        page.app.stop_run()
        on_proc(proc)
        return -15, None

    monkeypatch.setattr(EB, "run_json_call", run)
    monkeypatch.setattr(EB, "kill_tree", killed.append)
    _button(view, "编译并刷新页码").on_click(None)
    assert page.app.proc is proc and killed == [proc]
    assert "已取消预览" in _texts(view)


# ── T1 新语义：选择摘要、子集、顺序优先级、异步安全、错误生命周期 ──

def test_selection_summary_is_background_and_binds_issue_generation(paging):
    page, panel, view, issue, calls, replies, summary_calls, summary_replies = _paging(paging)
    summary_replies.append((0, _summary(issue, numbered=3, excluded=1, source="saved")))
    assert "正在读取编号目录" not in _texts(view)
    panel.refresh_selection()
    assert "正在读取编号目录" not in _texts(view)  # 桩同步跑完
    assert summary_calls[-1].argv[:4] == ["--issue", str(issue), "--json", "--selection-summary"]
    assert page.app.proc is None  # 摘要不拿前台槽
    assert "本期按当前 2 个编号目录刷新" in _texts(view)
    assert "已排除 1 个编号目录" in _texts(view)

    # 过期代次回执不更新
    def stale():
        page.switch(issue.parent)
        return 0, _summary(issue, numbered=99)

    summary_replies.append(stale)
    panel.refresh_selection()
    assert "99" not in _texts(view)


def test_selection_summary_empty_and_failure_do_not_reuse_old_numbers(paging):
    page, panel, view, issue, calls, replies, summary_calls, summary_replies = _paging(paging)
    summary_replies.append((0, _summary(issue, numbered=3)))
    panel.refresh_selection()
    assert "本期按当前 3 个编号目录刷新" in _texts(view)
    summary_replies.append((0, _summary(issue, numbered=0)))
    panel.refresh_selection()
    assert "未找到可刷新的编号目录" in _texts(view)
    assert "3 个编号目录" not in _texts(view)
    summary_replies.append((1, {"status": "blocked", "error": "期目录不存在"}))
    panel.refresh_selection()
    assert "期目录不存在" in _texts(view)
    assert "未找到" not in _texts(view)


def test_selection_summary_is_idempotent_while_in_flight(paging, monkeypatch):
    page, panel, view, issue, calls, replies, summary_calls, summary_replies = _paging(paging)
    pending = []
    monkeypatch.setattr(page.page, "run_task", lambda coro, *a: pending.append((coro, a)))
    panel.refresh_selection()
    panel.refresh_selection()
    assert len(summary_calls) == 0 and len(pending) == 1
    assert "正在读取编号目录" in _texts(view)
    for coro, args in pending:
        import asyncio
        asyncio.run(coro(*args))
    assert len(summary_calls) == 1
    assert "正在读取编号目录" not in _texts(view)


def test_summary_uses_explicit_json_then_bound_receipt_then_saved(paging):
    page, panel, view, issue, calls, replies, summary_calls, summary_replies = _paging(paging)
    panel.refresh_selection()
    # 本期有已确认回执：允许带 --order-result - （stdin 绑定该回执）。
    assert "--selection-summary" in summary_calls[-1].argv
    path = str(issue.parent / "order.json")
    panel.order_input.value = path
    panel.order_input.on_change(SimpleNamespace(control=panel.order_input))
    panel.refresh_selection()
    assert summary_calls[-1].argv[-2:] == ["--order-result", path]
    assert "排序结果 JSON" in _texts(view)

    panel.order_input.value = ""
    panel.order_input.on_change(SimpleNamespace(control=panel.order_input))
    panel.refresh_selection()
    assert summary_calls[-1].argv[-2:] == ["--order-result", "-"]
    assert json.loads(summary_calls[-1].stdin_text) == page._issue_order_panel.refresh_order_result

    page._issue_order_panel = SimpleNamespace(refresh_order_result=None,
                                              refresh_order_needs_confirmation=False)
    panel.refresh_selection()
    assert "--order-result" not in summary_calls[-1].argv


def test_stale_explicit_receipt_can_be_cleared_and_retried(paging):
    page, panel, view, issue, calls, replies, summary_calls, summary_replies = _paging(paging)
    panel.order_input.value = str(issue.parent / "过期.json")
    panel.order_input.on_change(SimpleNamespace(control=panel.order_input))
    replies.append((2, _result(issue, "blocked", error="排序结果不是本期已完成的改名记录")))
    _button(view, "编译并刷新页码").on_click(None)
    assert "排序结果不是本期" in _texts(view)
    panel.order_input.value = ""
    panel.order_input.on_change(SimpleNamespace(control=panel.order_input))
    assert "排序结果不是本期" not in _texts(view)
    replies.append((0, _result(issue, "preview", plan_id="fresh", order_source="implicit_all",
                               articles=[_article()])))
    _button(view, "编译并刷新页码").on_click(None)
    assert _has_button(view, "应用刷新计划")


def test_failed_saved_exclusion_prompts_recheck(paging):
    page, panel, view, issue, calls, replies, summary_calls, summary_replies = _paging(paging)
    summary_replies.append((2, _result(issue, "blocked",
                                       error="已确认的编号目录发生变化，请重新预览整期刷新并核准篇目")))
    panel.refresh_selection()
    text = _texts(view)
    assert "已确认的编号目录发生变化" in text


def test_subset_only_uses_list_positions_and_excludes_regenerate(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    input_field = next(c for c in _walk(view)
                       if isinstance(c, ft.TextField) and c.hint_text == "如 01,03")
    assert input_field.label == "只编这几篇"
    replies.append((0, _result(issue, "preview", plan_id="subset", order_source="provided",
                               articles=[_article(folder="01 甲"), _article(folder="03 丙")])))
    input_field.value = "01,03"
    input_field.on_change(SimpleNamespace(control=input_field))
    _button(view, "编译并刷新页码").on_click(None)
    assert calls[0].argv[-4:] == ["--on-change", "--only", "01,03"][-4:] or \
        ("--on-change" in calls[0].argv and calls[0].argv[calls[0].argv.index("--only") + 1] == "01,03")
    checkbox = next(c for c in _walk(view) if isinstance(c, ft.Checkbox) and c.label == "重生成目录")
    assert checkbox.disabled is True
    # 预览逐行显示位置
    assert "01 01 甲" in _texts(view) and "02 03 丙" in _texts(view)
    # 子集确认文案
    _button(view, "应用刷新计划").on_click(None)
    assert "先编选定篇目" in _texts(page.dialogs[-1])
    replies.append((0, _result(issue, "completed", scope="selected_pdfs",
                               articles=[_article(folder="01 甲")], writes=["01 甲/甲.pdf"])))
    _button(page.dialogs[-1], "开始编译并刷新").on_click(None)
    assert "--apply" in calls[1].argv and "--plan-id" in calls[1].argv
    assert calls[1].argv[calls[1].argv.index("--plan-id") + 1] == "subset"
    assert calls[0].stdin_text == calls[1].stdin_text


def test_regenerate_blocks_subset_and_only_change_invalidates_apply(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    replies.append((0, _result(issue, "preview", plan_id="plan-a", order_source="provided",
                               articles=[_article()])))
    _button(view, "编译并刷新页码").on_click(None)
    assert _has_button(view, "应用刷新计划")
    input_field = next(c for c in _walk(view)
                       if isinstance(c, ft.TextField) and c.hint_text == "如 01,03")
    checkbox = next(c for c in _walk(view) if isinstance(c, ft.Checkbox) and c.label == "重生成目录")
    input_field.value = "02"
    input_field.on_change(SimpleNamespace(control=input_field))
    assert not _has_button(view, "应用刷新计划")
    assert checkbox.disabled is True  # 子集与重生成互斥
    input_field.value = ""
    input_field.on_change(SimpleNamespace(control=input_field))
    checkbox.value = True
    checkbox.on_change(SimpleNamespace(control=checkbox))
    assert input_field.disabled is True


def test_preview_success_updates_count_from_engine_articles(paging):
    page, panel, view, issue, calls, replies, summary_calls, summary_replies = _paging(paging)
    summary_replies.append((0, _summary(issue, numbered=5)))
    panel.refresh_selection()
    assert "本期按当前 5 个编号目录刷新" in _texts(view)
    replies.append((0, _result(issue, "preview", plan_id="p", order_source="implicit_all",
                               articles=[_article(folder="01 甲"), _article(folder="02 乙")])))
    _button(view, "编译并刷新页码").on_click(None)
    assert "本期按当前 2 个编号目录刷新" in _texts(view)


def test_error_lifecycle_clears_red_on_fix_and_keeps_recovery(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    replies.extend([
        (0, _result(issue, "preview", plan_id="p", order_source="provided", articles=[_article()])),
        (1, _result(issue, "recovery_required", recovery_id="r1",
                    pending_operation="refreshpages", warnings=["写入失败"])),
    ])
    _button(view, "编译并刷新页码").on_click(None)
    _button(view, "应用刷新计划").on_click(None)
    _button(page.dialogs[-1], "开始编译并刷新").on_click(None)
    assert "写入失败" in _texts(view) and _has_button(view, "恢复上一轮")
    input_field = next(c for c in _walk(view)
                       if isinstance(c, ft.TextField) and c.hint_text == "如 01,03")
    input_field.value = "01"
    input_field.on_change(SimpleNamespace(control=input_field))
    assert "写入失败" not in _texts(view)
    assert _has_button(view, "恢复上一轮")
    page.switch(issue.parent)
    assert not _has_button(panel.controls(), "恢复上一轮")


def test_stale_async_preview_reply_never_updates(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)

    def stale():
        page.switch(issue.parent)
        return 0, _result(issue, "preview", plan_id="old", order_source="provided",
                          articles=[_article(folder="99 旧")])

    replies.append(stale)
    _button(view, "编译并刷新页码").on_click(None)
    assert "99 旧" not in _texts(view)
    assert not _has_button(view, "应用刷新计划")


def test_invalidate_order_cancels_preview_links_and_refetches(paging):
    page, panel, view, issue, calls, replies, summary_calls, summary_replies = _paging(paging)
    replies.append((0, _result(issue, "preview", plan_id="p", order_source="provided",
                               articles=[_article()], writes=["01 甲/甲.pdf"])))
    _button(view, "编译并刷新页码").on_click(None)
    assert _has_button(view, "应用刷新计划")
    panel.invalidate_order()
    assert not _has_button(panel.view, "应用刷新计划")
    assert not _has_button(panel.view, "打开 01 稿件甲 正文 PDF")
    assert len(summary_calls) == 1  # 已挂载时重新取摘要


def test_automatic_entry_points_are_removed():
    from ui import issue_paging

    assert not hasattr(issue_paging, "AutomaticCompile")
    assert not hasattr(issue_paging.PagingPanel, "automatic_request")
    assert not hasattr(issue_paging.PagingPanel, "compile_automatically")
    assert hasattr(issue_paging.PagingPanel, "refresh_selection")
    assert hasattr(issue_paging.PagingPanel, "invalidate_order")


def test_auto_checkbox_and_shell_sort_gate_are_gone(paging):
    page, panel, view, issue, calls, replies, _, _ = _paging(paging)
    assert not any(isinstance(c, ft.Checkbox) and c.label == "编译后自动刷新页码"
                   for c in _walk(view))
    replies.append((0, _result(issue, "preview", plan_id="implicit",
                               order_source="implicit_all", articles=[_article()])))
    _button(view, "编译并刷新页码").on_click(None)
    apply_button = _button(view, "应用刷新计划")
    assert "请先回到排序步骤" not in _texts(view)
    # 关闭确认对话框，避免污染后续测试
    if page.dialogs:
        page.dialogs.pop()


def test_recovery_omits_toc_and_order_options():
    args = dict(issue="/issue", recovery_id="recover-a", regenerate_toc=True,
                order_result="/file.json", order_json="unused")
    from unittest.mock import patch

    with patch.object(EB, "base_cmd", return_value=["engine"]):
        call = EB.refreshpages_call(**args)
    assert call.argv == ["engine", "--issue", "/issue", "--json", "--recover", "recover-a"]
    assert call.stdin_text is None
    with patch.object(EB, "base_cmd", return_value=["engine"]):
        summary = EB.refreshpages_call(issue="/issue", selection_summary=True,
                                       order_result="/file.json")
    assert summary.argv == ["engine", "--issue", "/issue", "--json", "--selection-summary",
                            "--order-result", "/file.json"]
