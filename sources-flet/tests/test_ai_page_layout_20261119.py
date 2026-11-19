"""初排完整流程和 AI 页宿主：离线布局/状态契约，不作为原生窗口验收。"""
from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from ui import bib_ai_assist as A
from ui import pages as P
from test_b114_polish import _FakeApp, _walk


class _App(_FakeApp):
    def set_option(self, key, value):
        self.store.set(f"options.{key}", value)

    def copy_text(self, text):
        pass


class _TaskView:
    def __init__(self, host, *, purpose):
        assert purpose == "initial"
        self.page = host
        self.phase = "idle"
        self.task_id = ""
        self.running = False
        self.delivery_ok = False
        self.error = ""
        self.render_flags = []

    def activate(self):
        pass

    def body_only(self, *, compact=False, pinned_actions=False):
        self.render_flags.append((compact, pinned_actions))
        return ft.Column(expand=True, controls=[ft.Text("当前 AI 任务")])


def _build(monkeypatch, *, inputs=True):
    monkeypatch.setattr(A, "BibAiAssistView", _TaskView)
    page = P.SinglePage(_App())
    root = page.build()
    if inputs:
        page.set_field("single_source", "/tmp/anonymous/article.tex")
        page.set_field("single_out", "/tmp/anonymous/delivery")
    return page, root


def _visible(root):
    if root is None or getattr(root, "visible", True) is False:
        return
    yield root
    for control in getattr(root, "controls", []) or []:
        yield from _visible(control)
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        yield from _visible(content)


def test_plain_conversion_has_one_local_action_after_inputs_and_optional_settings(monkeypatch):
    page, root = _build(monkeypatch)
    assert page._header_run_actions.controls == []
    visible = list(_visible(root))
    assert visible.index(page.fields["single_source"][0]) < visible.index(page._run_btn)
    assert visible.index(page.fields["single_out"][0]) < visible.index(page._run_btn)
    assert sum(control is page._run_btn for control in visible) == 1
    assert page._run_btn.content == "开始转换"
    assert page._stop_btn.visible is False
    assert page._convert_supplement.expanded is False
    assert page._convert_more_output.expanded is False
    assert page.fields["convert_vol"][0] not in visible
    assert page.fields["convert_bib"][0] not in visible
    assert page._single_scroll_body.scroll is not None


def test_output_checks_keep_same_engine_option_keys_and_defaults(monkeypatch):
    page, root = _build(monkeypatch)
    checks = {control._convert_option_key: control
              for control in _walk(root) if hasattr(control, "_convert_option_key")}
    assert set(checks) == {"convert_compile", "convert_clean", "convert_source_pdf",
                           "convert_force", "convert_dry_run"}
    assert checks["convert_compile"].value is True
    assert all(not checks[key].value for key in checks if key != "convert_compile")
    checks["convert_dry_run"].on_change(SimpleNamespace(control=SimpleNamespace(value=True)))
    checks["convert_compile"].on_change(SimpleNamespace(control=SimpleNamespace(value=False)))
    assert page.app.store.get("options.convert_dry_run") is True
    assert page.app.store.get("options.convert_compile") is False


def test_ai_workspace_reuses_parent_fields_and_has_only_inner_task_scroll(monkeypatch):
    page, root = _build(monkeypatch)
    refs = {key: ref[0] for key, ref in page.fields.items()}
    page._toggle_ai_workflow()
    assert page._aiw_open is True
    assert page._aiw_parameters_open is False
    assert page._aiw_view.render_flags[-1] == (True, True)
    assert page._single_scroll_body.scroll is None
    assert page._single_inner_host.expand is True
    assert page._single_inner.expand is True
    assert page._flow_box.expand is True
    assert page._convert_root.expand is True
    assert page._aiw_box.expand is True
    assert page._ordinary_results.visible is False
    assert page._norm_row.visible is False
    assert page._run_btn.visible is False
    page._aiw_view.page._render()
    assert all(page.fields[key][0] is value for key, value in refs.items())
    assert page.field("single_source") == "/tmp/anonymous/article.tex"
    assert page.field("single_out") == "/tmp/anonymous/delivery"
    visible_text = [control.value for control in _visible(root) if isinstance(control, ft.Text)]
    assert "当前 AI 任务" in visible_text
    assert page.fields["single_source"][0] not in list(_visible(root))


def test_editing_conversion_inputs_temporarily_owns_the_single_outer_scroll(monkeypatch):
    page, root = _build(monkeypatch)
    page._toggle_ai_workflow()
    page._toggle_ai_parameters()
    assert page._aiw_parameters_open is True
    assert page._single_scroll_body.scroll is not None
    visible = list(_visible(root))
    assert page.fields["single_source"][0] in visible
    assert "当前 AI 任务" not in [control.value for control in visible if isinstance(control, ft.Text)]
    page.set_field("convert_refs", "/tmp/anonymous/extra.bib")
    ref = page.fields["convert_refs"][0]
    page._toggle_ai_parameters()
    assert page._single_scroll_body.scroll is None
    assert page.fields["convert_refs"][0] is ref
    assert page.field("convert_refs") == "/tmp/anonymous/extra.bib"
    page._toggle_ai_workflow()
    assert page._single_scroll_body.scroll is not None
    assert page._ordinary_results.visible is True
    assert page._run_btn.visible is True
    assert page.fields["convert_refs"][0] is ref


def test_missing_required_inputs_open_editor_and_task_creation_collapses_it_once(monkeypatch):
    page, root = _build(monkeypatch, inputs=False)
    page._toggle_ai_workflow()
    assert page._aiw_parameters_open is True
    assert page.fields["single_source"][0] in list(_visible(root))
    page.set_field("single_source", "/tmp/anonymous/article.tex")
    page.set_field("single_out", "/tmp/anonymous/delivery")
    page._toggle_ai_parameters()
    page._aiw_view.task_id = "ANON-1"
    page._aiw_view.phase = "waiting"
    page._rerender_flow()
    assert page._aiw_parameters_open is False
    page._toggle_ai_parameters()
    page._rerender_flow()
    assert page._aiw_parameters_open is True, "普通刷新不得覆盖用户主动展开参数"


def test_log_collapses_only_on_first_entry_and_real_delivery_boundary(monkeypatch):
    page, _ = _build(monkeypatch)
    page._log.expand()
    page._toggle_ai_workflow()
    assert page._log.is_expanded is False
    page._log.expand()
    page._rerender_flow()
    assert page._log.is_expanded is True
    page._toggle_ai_workflow()
    page._toggle_ai_workflow()
    assert page._log.is_expanded is True, "重进同一任务不覆盖手动日志偏好"
    page._aiw_view.phase = "applied"
    page._aiw_view.delivery_ok = False
    page._rerender_flow()
    assert page._log.is_expanded is True, "只有阶段字样不证明交付成功"
    page._aiw_view.phase = "preview"
    page._rerender_flow()
    page._aiw_view.phase = "applied"
    page._aiw_view.delivery_ok = True
    page._rerender_flow()
    assert page._log.is_expanded is False
    page._log.expand()
    page._rerender_flow()
    assert page._log.is_expanded is True


def test_proofcheck_restores_original_header_actions_and_scroll(monkeypatch):
    page, _ = _build(monkeypatch)
    page._toggle_ai_workflow()
    page.activate("proofcheck")
    assert page._header_run_actions.controls == [page._stop_btn, page._run_btn]
    assert page._run_btn.content == "运行"
    assert page._run_btn.visible is True
    assert page._single_scroll_body.scroll is not None
    assert page._ordinary_results.visible is True


def test_ai_task_navigation_does_not_focus_hidden_parent_form(monkeypatch):
    page, _ = _build(monkeypatch)
    source = page.fields["single_source"][0]
    assert page.nav_focus_target() is source
    page._toggle_ai_workflow()
    assert page.nav_focus_target() is None
    page._toggle_ai_parameters()
    assert page.nav_focus_target() is source


def test_real_ai_view_keeps_action_bar_outside_its_only_scroll_area():
    page = P.SinglePage(_App())
    root = page.build()
    page.set_field("single_source", "/tmp/anonymous/article.tex")
    page.set_field("single_out", "/tmp/anonymous/delivery")
    page._toggle_ai_workflow()
    visible = list(_visible(root))
    scrolls = [control for control in visible
               if getattr(control, "_bib_ai_workspace_scroll", False)]
    assert len(scrolls) == 1
    assert page._single_scroll_body.scroll is None
    action_bar = page._aiw_view._workspace_action_bar
    assert action_bar is not None
    assert action_bar in visible
    assert action_bar not in list(_walk(scrolls[0]))
