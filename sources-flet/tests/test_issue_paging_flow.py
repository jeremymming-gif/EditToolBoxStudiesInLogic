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


def _article(*, new=None):
    return {
        "folder": "01 稿件甲", "main_tex": "01 稿件甲/稿件甲.tex",
        "pdf": "01 稿件甲/稿件甲.pdf", "old": {"first": 2, "last": 4, "count": 3},
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

    async def run(call, *, on_line, on_proc):
        calls.append(call)
        on_line("合成日志")
        reply = replies.pop(0)
        return reply() if callable(reply) else reply

    monkeypatch.setattr(EB, "base_cmd", lambda entry: [])
    monkeypatch.setattr(EB, "run_json_call", run, raising=False)
    return page, panel, panel.controls(), issue, calls, replies


def test_preview_cancel_apply_and_open_only_this_runs_outputs(paging):
    page, panel, view, issue, calls, replies = paging
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
    page, panel, view, issue, calls, replies = paging
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
    page, panel, view, issue, calls, replies = paging
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
    page, panel, view, issue, calls, replies = paging
    replies.append((0, _result(issue, "preview", plan_id="no-regen",
                               order_source="provided", articles=[_article()])))
    _button(view, "编译并刷新页码").on_click(None)
    checkbox = next(c for c in _walk(view) if isinstance(c, ft.Checkbox) and c.label == "重生成目录")
    checkbox.value = True
    checkbox.on_change(SimpleNamespace(control=checkbox))
    assert not _has_button(view, "应用刷新计划")
    replies.extend([
        (0, _result(issue, "preview", plan_id="with-regen", order_source="provided",
                    toc_strategy="regenerated",
                    articles=[_article()])),
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
    page, panel, view, issue, calls, replies = paging
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


def test_unconfirmed_edited_order_requires_reconfirmation_or_saved_result(paging):
    page, panel, view, issue, calls, replies = paging
    page._issue_order_panel = SimpleNamespace(
        refresh_order_result=None, refresh_order_needs_confirmation=True,
    )
    replies.extend([(0, _result(issue, "preview", plan_id="unconfirmed",
                                order_source="provided")),
                    (0, _result(issue, "preview", plan_id="from-file",
                                order_source="provided"))])
    _button(view, "编译并刷新页码").on_click(None)
    assert not _has_button(view, "应用刷新计划")
    assert "确认本期的排序" in _texts(view)
    panel.order_input.value = str(issue.parent / "排序结果.json")
    panel.order_input.on_change(SimpleNamespace(control=panel.order_input))
    _button(view, "编译并刷新页码").on_click(None)
    assert calls[-1].argv[-2:] == ["--order-result", panel.order_input.value]
    assert calls[-1].stdin_text is None
    assert _has_button(view, "应用刷新计划")


def test_initial_order_state_without_result_renders_confirmation_hint(paging):
    page, panel, _, _, _, _ = paging
    page._issue_order_panel = SimpleNamespace(
        refresh_order_result=None, refresh_order_needs_confirmation=False,
    )

    view = panel.controls()

    assert panel.options().order_needs_confirmation is True
    assert "确认改名" in _texts(view)


def test_cancelled_apply_and_foreign_pending_transaction(paging):
    page, panel, view, issue, calls, replies = paging

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


def test_restart_requires_order_file_or_saved_selection(paging):
    page, panel, view, issue, calls, replies = paging
    page._issue_order_panel = None
    replies.extend([
        (0, _result(issue, "preview", plan_id="implicit", order_source="implicit_all",
                    excluded=[], articles=[_article()])),
        (0, _result(issue, "preview", plan_id="saved", order_source="saved",
                    excluded=["09 附件"], articles=[_article()])),
    ])
    _button(view, "编译并刷新页码").on_click(None)
    assert not _has_button(view, "应用刷新计划")
    assert "所有识别到的篇目" in _texts(view)
    panel.order_input.value = str(issue.parent / "排序结果.json")
    panel.order_input.on_change(SimpleNamespace(control=panel.order_input))
    _button(view, "编译并刷新页码").on_click(None)
    assert _has_button(view, "应用刷新计划")


def test_stop_before_child_is_tracked(paging, monkeypatch):
    page, panel, view, issue, calls, replies = paging
    proc = object()
    killed = []

    async def run(call, *, on_line, on_proc):
        page.app.stop_run()
        on_proc(proc)
        return -15, None

    monkeypatch.setattr(EB, "run_json_call", run)
    monkeypatch.setattr(EB, "kill_tree", killed.append)
    _button(view, "编译并刷新页码").on_click(None)
    assert page.app.proc is proc and killed == [proc]
    assert "已取消预览" in _texts(view)


def test_recovery_omits_toc_and_order_options():
    args = dict(issue="/issue", recovery_id="recover-a", regenerate_toc=True,
                order_result="/file.json", order_json="unused")
    from unittest.mock import patch

    with patch.object(EB, "base_cmd", return_value=["engine"]):
        call = EB.refreshpages_call(**args)
    assert call.argv == ["engine", "--issue", "/issue", "--json", "--recover", "recover-a"]
    assert call.stdin_text is None


def _toggle_auto(page, view, *, confirm=True):
    checkbox = next(c for c in _walk(view)
                    if isinstance(c, ft.Checkbox) and c.label == "编译后自动刷新页码")
    checkbox.value = not checkbox.value
    checkbox.on_change(SimpleNamespace(control=checkbox))
    if confirm:
        _button(page.dialogs[-1], "启用自动刷新").on_click(None)
    return checkbox


@pytest.mark.parametrize(("only", "scope", "message"), [
    ("02", "selected_pdfs", "只更新本次编译的正文 PDF"),
    ("", "issue", "已刷新整期页码"),
])
def test_normal_compile_uses_one_bound_refresh_flow(paging, only, scope, message):
    from ui import assembly as ASM

    page, panel, view, issue, calls, replies = paging
    page._issue_paging_panel = panel
    page.set_field("compile_only", only)
    assert _toggle_auto(page, view).value is True
    page._issue_order_panel.refresh_order_result["excluded"] = ["09 附件"]
    article = _article()
    replies.extend([
        (0, _result(issue, "preview", plan_id="chosen", order_source="provided", articles=[article])),
        (0, _result(issue, "completed", scope=scope, articles=[article], writes=[article["pdf"]])),
    ])

    ASM.run_step(page, "compileall")

    assert len(calls) == 2 and all(c.entry == "refreshpages.py" for c in calls)
    assert calls[0].argv[-2:] == calls[1].argv[-2:] == ["--order-result", "-"]
    assert calls[0].stdin_text == calls[1].stdin_text
    assert json.loads(calls[1].stdin_text)["excluded"] == ["09 附件"]
    for call in calls:
        assert "--on-change" in call.argv
        if only:
            assert call.argv[call.argv.index("--only") + 1] == only
        else:
            assert "--only" not in call.argv
    assert "--apply" not in calls[0].argv
    assert calls[1].argv[calls[1].argv.index("--plan-id") + 1] == "chosen"
    assert page._run_results[0]["state"] == "done"
    assert message in _texts(view)
    assert _has_button(view, "打开 01 稿件甲 正文 PDF")


def test_auto_opt_in_is_confirmed_and_bound_to_current_issue(paging, tmp_path):
    from ui import issue_paging

    page, panel, view, issue, _, _ = paging
    checkbox = _toggle_auto(page, view, confirm=False)
    assert checkbox.value is False
    _button(page.dialogs[-1], "取消").on_click(None)
    assert checkbox.value is False
    _toggle_auto(page, view, confirm=False)
    confirm = page.dialogs[-1]
    page.switch(tmp_path / "合成二期")
    _button(confirm, "启用自动刷新").on_click(None)
    assert not panel.auto_enabled
    new = issue_paging.PagingPanel(page)
    assert not new.auto_enabled

    assert _toggle_auto(page, new.controls()).value is True
    rebuilt_checkbox = next(c for c in _walk(new.controls())
                            if isinstance(c, ft.Checkbox) and c.label == "编译后自动刷新页码")
    assert rebuilt_checkbox.value is True
    page.switch(issue)
    assert not issue_paging.PagingPanel(page).auto_enabled


def test_legacy_compile_still_works_when_disabled_or_dry_run(paging, monkeypatch):
    from ui import assembly as ASM
    from test_b116_issue_run import install_run

    page, panel, view, _, calls, _ = paging
    page._issue_paging_panel = panel
    legacy_calls = install_run(monkeypatch, page, [0, 0, 0])
    ASM.run_step(page, "compileall")
    _toggle_auto(page, view)
    page.app.store.update_options(compileall_dry_run=True)
    ASM.run_step(page, "compileall")
    page.app.store.update_options(compileall_dry_run=False)
    assert _toggle_auto(page, view, confirm=False).value is False
    ASM.run_step(page, "compileall")
    assert [c.entry for c in legacy_calls] == ["compileall.py"] * 3
    assert "--dry-run" in legacy_calls[1].argv
    assert calls == []


@pytest.mark.parametrize("problem", ["unconfirmed", "edited", "no articles", "cancelled", "changed", "switched"])
def test_auto_preview_never_applies_if_it_cannot_be_used(paging, problem, tmp_path):
    from ui import assembly as ASM

    page, panel, view, issue, calls, replies = paging
    page._issue_paging_panel = panel
    _toggle_auto(page, view)
    if problem == "edited":
        page._issue_order_panel.refresh_order_result = None
        page._issue_order_panel.refresh_order_needs_confirmation = True

    def preview():
        if problem == "cancelled":
            page.app.stop_run()
        elif problem == "changed":
            page.set_field("compile_only", "02")
        elif problem == "switched":
            page.switch(tmp_path / "合成二期")
        source = {"unconfirmed": "implicit_all", "edited": "saved"}.get(problem, "provided")
        return 0, _result(issue, "preview", plan_id="old", order_source=source,
                          articles=[] if problem == "no articles" else [_article()])

    replies.append(preview)
    ASM.run_all(page, ["compileall", "merge"])

    assert len(calls) == 1
    assert "--apply" not in calls[0].argv
    assert page._run_results[1]["state"] == ("cancelled" if problem == "cancelled" else "skipped")


def test_automatic_full_refresh_leaves_toc_decision_visible_and_stops_sequence(paging):
    from ui import assembly as ASM

    page, panel, view, issue, calls, replies = paging
    page._issue_paging_panel = panel
    _toggle_auto(page, view)
    replies.extend([
        (0, _result(issue, "preview", plan_id="changed-pages", order_source="provided",
                    articles=[_article()], warnings=["如果需要刷新整期，必须先处理目录"])),
        (1, _result(issue, "blocked", error="目录不匹配，请核对目录或重生成目录。")),
    ])

    ASM.run_all(page, ["compileall", "merge"])

    assert len(calls) == 2 and all("--regenerate-toc" not in c.argv for c in calls)
    assert page._run_results[0]["rc"] == 1
    assert page._run_results[1]["state"] == "skipped"
    assert "重生成目录" in panel.message
