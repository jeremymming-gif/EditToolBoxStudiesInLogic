"""Synthetic Flet/bridge contract tests for the R3 compile-fix workflow."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import flet as ft

_CANDIDATE = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(_CANDIDATE))

from services import ai_prefs as AI
from services import engine_bridge as EB


def test_compile_fix_default_is_closed_and_settings_control_exists():
    assert AI.defaults()["compile_fix_enabled"] is False
    assert AI.normalize({"enabled": True})["compile_fix_enabled"] is False
    source = (_CANDIDATE / "ui/ai_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert any(isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "option_row"
               and any(isinstance(arg, ast.Constant) and arg.value == "AI 辅助编译修复"
                       for arg in ast.walk(node)) for node in ast.walk(tree))


def test_deliver_eligibility_requires_code_payload_and_matching_identity():
    payload = {"op": "deliver", "ok": False, "delivered": False, "compile_fix": {
        "eligible": True, "task_id": "T1", "preview_id": "P1", "evidence_id": "E1",
        "candidate_sha256": "a" * 64, "diagnostics": [{"message": "synthetic"}],
    }}
    assert EB.compile_fix_result(2, payload, op="deliver", task_id="T1", preview_id="P1")
    assert EB.compile_fix_result(0, payload, op="deliver", task_id="T1", preview_id="P1") is None
    assert EB.compile_fix_result(2, {**payload, "delivered": True}, op="deliver",
                                 task_id="T1", preview_id="P1") is None
    assert EB.compile_fix_result(2, payload, op="deliver", task_id="T2", preview_id="P1") is None


def _call(monkeypatch, **kwargs):
    monkeypatch.setattr(EB, "_call", lambda entry, args, label: EB.EngineCall(entry, ["python", *args], label))
    return EB.ai_compile_fix_call(work_dir="/work", preview_id="P1", **kwargs)


def test_prepare_and_send_use_private_key_env_not_argv_or_stdin(monkeypatch):
    settings = {"ai_enabled": True, "compile_fix_enabled": True, "key": "synthetic-secret",
                "base": "https://example.invalid/v1", "model": "synthetic", "protocol": "chat",
                "article_budget_tokens": 100, "batch_budget_tokens": 200}
    prepare = _call(monkeypatch, action="prepare", settings=settings, evidence_id="E1", evidence_ids=["D1"])
    assert "--ai-enabled" in prepare.argv and "--compile-fix-enabled" in prepare.argv
    assert "synthetic-secret" not in prepare.argv
    assert "synthetic-secret" not in prepare.stdin_text
    assert prepare.explicit_api_only is True and prepare.explicit_ai_key == "synthetic-secret"
    sent = _call(monkeypatch, action="send", settings=settings, evidence_id="E1", evidence_ids=["D1"],
                 request_id="R1", request_sha256="b" * 64, authorization_id="A1", terms_ack=True)
    assert "--ai-enabled" in sent.argv and "--compile-fix-enabled" in sent.argv
    assert "--timeout" in sent.argv and sent.argv[sent.argv.index("--timeout") + 1] == "60"
    assert "--response-limit" in sent.argv and sent.argv[sent.argv.index("--response-limit") + 1] == "65536"
    assert sent.explicit_ai_key == "synthetic-secret"
    assert "synthetic-secret" not in sent.argv and "synthetic-secret" not in sent.stdin_text


def test_send_requires_every_ack_binding_and_fixed_transport_limits(monkeypatch):
    settings = {"ai_enabled": True, "compile_fix_enabled": True, "key": "secret"}
    with pytest.raises(ValueError):
        _call(monkeypatch, action="send", settings=settings, evidence_id="E1", evidence_ids=["D1"],
              request_id="R1", request_sha256="b" * 64, authorization_id="A1", terms_ack=False)
    with pytest.raises(ValueError):
        _call(monkeypatch, action="send", settings=settings, evidence_id="E1", evidence_ids=["D1"],
              request_id="R1", request_sha256="b" * 64, authorization_id="A1", terms_ack=True,
              timeout=30)


def test_explicit_only_environment_drops_inherited_ai_keys(monkeypatch):
    import asyncio

    for key in ("SLCN_LLM_KEY", "SLCN_LLM_BASE", "SLCN_COMPILE_FIX_API_KEY"):
        monkeypatch.setenv(key, "parent-secret")
    call = EB.EngineCall("convert.py", ["convert.py"], "synthetic")
    call.explicit_api_only = True
    call.explicit_ai_key = "current-secret"

    class _Proc:
        returncode = 0
        stdin = None
        stdout = None

        async def wait(self):
            return 0

        async def communicate(self):
            return b"", b""

    captured = {}

    async def spawn(*args, **kwargs):
        captured.update(kwargs)
        proc = _Proc()

        class _Stream:
            async def read(self, size):
                return b""

        proc.stdout = _Stream()
        return proc

    monkeypatch.setattr(EB.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(EB, "find_engine_dir", lambda: None)
    asyncio.run(EB.run_call(call))
    env = captured["env"]
    assert env["SLCN_COMPILE_FIX_API_KEY"] == "current-secret"
    assert not {"SLCN_LLM_KEY", "SLCN_LLM_BASE"} & env.keys()


def test_retained_and_delete_bridge_use_only_work_dir_and_stable_record_key(monkeypatch):
    monkeypatch.setattr(EB, "_call", lambda entry, args, label: EB.EngineCall(entry, ["python", *args], label))
    retained = EB.ai_compile_fix_call(action="retained", work_dir="/work", settings={})
    assert retained.argv[1:] == ["--ai-workflow", "--work-dir", "/work", "compile-fix", "retained", "--json"]
    assert retained.explicit_api_only is True and not retained.stdin_text and not retained.explicit_ai_key

    key = "a" * 64
    delete = EB.ai_compile_fix_call(action="delete", work_dir="/work", settings={}, record_key=key)
    assert delete.argv[1:] == ["--ai-workflow", "--work-dir", "/work", "compile-fix", "delete",
                               "--record-key", key, "--json"]
    assert delete.explicit_api_only is True and not delete.stdin_text and not delete.explicit_ai_key
    with pytest.raises(ValueError):
        EB.ai_compile_fix_call(action="delete", work_dir="/work", settings={}, preview_id="old-preview",
                               record_key=key)
    with pytest.raises(ValueError):
        EB.ai_compile_fix_call(action="delete", work_dir="/work", settings={}, record_key="not-a-key")

    row = {"record_key": key, "task_id": "T1", "preview_id": "P-old", "status": "delivered",
           "expires_at": "2026-12-25T00:00:00Z", "has_reply": True}
    second = {**row, "record_key": "b" * 64, "preview_id": "P-older", "status": "expired"}
    listed = {"op": "compile-fix", "ok": True, "retained": [row, second]}
    assert EB.compile_fix_retained_result(0, listed, task_id="T1") == [row, second]
    assert EB.compile_fix_retained_result(2, {"op": "compile-fix", "ok": False, "error": "rejected"},
                                          task_id="T1") is None
    assert EB.compile_fix_retained_result(0, {**listed, "retained": [{**row, "request_id": "secret"}]},
                                          task_id="T1") is None
    assert EB.compile_fix_retained_result(0, {**listed, "retained": [row, {**second, "record_key": key}]},
                                          task_id="T1") is None
    receipt = {"op": "compile-fix", "ok": True, "compile_fix": {**row, "status": "deleted"}}
    assert EB.compile_fix_retained_result(0, receipt, task_id="T1", record=row) == "deleted"
    assert EB.compile_fix_retained_result(0, {**receipt, "compile_fix": {**receipt["compile_fix"],
                                          "record_key": "b" * 64}}, task_id="T1", record=row) is None


def test_action_receipts_fail_closed_on_status_identity_and_hash_mismatch():
    previous = {"evidence_id": "E1", "candidate_sha256": "a" * 64}
    args = {"evidence_id": "E1", "request_id": "R1", "request_sha256": "b" * 64,
            "authorization_id": "A1"}
    result = {"status": "imported", "task_id": "T1", "preview_id": "P1", **previous,
              "request_id": "R1", "request_sha256": "b" * 64, "authorization_id": "A1"}
    payload = {"op": "compile-fix", "ok": True, "compile_fix": result}
    assert EB.compile_fix_action_result(0, payload, action="import", task_id="T1", preview_id="P1",
                                        previous=previous, arguments=args)
    bad = {**payload, "compile_fix": {**result, "request_id": "R2"}}
    assert EB.compile_fix_action_result(0, bad, action="import", task_id="T1", preview_id="P1",
                                        previous=previous, arguments=args) is None


def test_unknown_is_explicit_and_never_a_successful_retry_receipt():
    previous = {"evidence_id": "E1", "candidate_sha256": "a" * 64}
    result = {"status": "unknown", "task_id": "T1", "preview_id": "P1", **previous,
              "request_id": "R1", "request_sha256": "b" * 64, "authorization_id": "A1",
              "log_path": "/local/request.log", "actions": ["view_log", "retry", "cancel"]}
    payload = {"op": "compile-fix", "ok": False, "compile_fix": result}
    assert EB.compile_fix_action_result(2, payload, action="send", task_id="T1", preview_id="P1",
                                        previous=previous, arguments={"request_id": "R1",
                                        "request_sha256": "b" * 64, "authorization_id": "A1"}) == result
    assert EB.compile_fix_action_result(0, payload, action="send", task_id="T1", preview_id="P1",
                                        previous=previous, arguments={"request_id": "R1"}) is None


def test_review_and_apply_receipts_require_current_request_and_review_hash():
    previous = {"evidence_id": "E1", "candidate_sha256": "a" * 64}
    args = {"evidence_id": "E1", "request_id": "R1", "review_sha256": "c" * 64}
    common = {"task_id": "T1", "preview_id": "P1", **previous, "request_id": "R1",
              "request_sha256": "b" * 64, "authorization_id": "A1"}
    reviewed = {**common, "status": "reviewed", "review_sha256": "c" * 64,
                "diff": "synthetic diff", "actions": [], "can_adopt": True}
    review_payload = {"op": "compile-fix", "ok": True, "compile_fix": reviewed}
    assert EB.compile_fix_action_result(0, review_payload, action="review", task_id="T1", preview_id="P1",
                                        previous=previous, arguments=args)
    compiled = {**reviewed, "status": "compiled", "pdf_path": "/synthetic/result.pdf", "compile_run_id": "C1"}
    compiled_payload = {"op": "compile-fix", "ok": True, "compile_fix": compiled}
    assert EB.compile_fix_action_result(0, compiled_payload, action="apply", task_id="T1", preview_id="P1",
                                        previous=previous, arguments=args)
    bad = {"op": "compile-fix", "ok": True,
           "compile_fix": {**compiled, "review_sha256": "d" * 64}}
    assert EB.compile_fix_action_result(0, bad, action="apply", task_id="T1", preview_id="P1",
                                        previous=previous, arguments=args) is None


def test_prepared_body_is_verified_as_exact_utf8_bytes():
    body = '{"synthetic":"编译修复"}'
    raw = body.encode("utf-8")
    summary = {"key_configured": True, "body_bytes": len(raw), "body_sha256": hashlib.sha256(raw).hexdigest(),
               "base": "x", "target": "x", "model": "m", "protocol": "chat", "tools": {},
               "max_tokens": 1, "thinking": False, "article_budget_tokens": 1, "batch_budget_tokens": 1}
    prepared = {"body": body, "summary": summary, "scope": {"evidence_ids": ["D1"]},
                "evidence": [{"evidence_id": "D1"}], "evidence_id": "E1", "request_id": "R1",
                "authorization_id": "A1", "request_sha256": summary["body_sha256"]}
    assert EB.compile_fix_disclosure_valid(prepared, ["D1"])
    assert not EB.compile_fix_disclosure_valid(prepared, ["D2"])
    assert not EB.compile_fix_disclosure_valid({**prepared, "body": body + " "}, ["D1"])


def test_prepared_authorization_is_voided_by_scope_settings_or_target_identity_change():
    prepared = object()
    base = {"prepared": prepared, "current": prepared, "expected_context": ("T1", "P1"),
            "current_context": ("T1", "P1"), "expected_settings": {"base": "https://a.invalid"},
            "current_settings": {"base": "https://a.invalid"}, "expected_scope": {"evidence_ids": ["D1"]},
            "current_scope": {"evidence_ids": ["D1"]}}
    assert EB.compile_fix_authorization_matches(**base)
    assert not EB.compile_fix_authorization_matches(**{**base, "current_settings": {"base": "https://b.invalid"}})
    assert not EB.compile_fix_authorization_matches(**{**base, "current_scope": {"evidence_ids": ["D2"]}})
    assert not EB.compile_fix_authorization_matches(**{**base, "current_context": ("T2", "P1")})
    assert not EB.compile_fix_authorization_matches(**{**base, "current": object()})


def test_delivered_receipt_must_match_compiled_run_and_request():
    compiled = {"evidence_id": "E1", "request_id": "R1", "compile_run_id": "C1"}
    payload = {"op": "deliver", "ok": True, "delivered": True, "task_id": "T1", "preview_id": "P1",
               "compile_fix": {"status": "delivered", "task_id": "T1", "preview_id": "P1",
                               "evidence_id": "E1", "request_id": "R1", "run_id": "C1"}}
    assert EB.compile_fix_delivery_matches(0, payload, task_id="T1", preview_id="P1", compiled=compiled)
    payload["compile_fix"]["run_id"] = "other"
    assert not EB.compile_fix_delivery_matches(0, payload, task_id="T1", preview_id="P1", compiled=compiled)
    payload["compile_fix"]["run_id"] = "C1"
    payload["compile_fix"]["request_id"] = "other"
    assert not EB.compile_fix_delivery_matches(0, payload, task_id="T1", preview_id="P1", compiled=compiled)


class _Page:
    def __init__(self):
        self.dialogs = []

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        return self.dialogs.pop()


@pytest.fixture
def ui_harness(monkeypatch):
    import ui

    # A sparse delivery tree can use unchanged UI dependencies next to the running venv.
    base_ui = Path(sys.prefix).parent / "app/ui"
    if not (_CANDIDATE / "ui/components.py").is_file() and (base_ui / "components.py").is_file():
        monkeypatch.setattr(ui, "__path__", [str(_CANDIDATE / "ui"), str(base_ui)])
    from ui import bib_ai_assist as BA, compile_fix_dialog as CFD, components as C
    from ui.tokens import Tokens

    monkeypatch.setattr(C, "_open_dialogs", [])
    page = _Page()
    view = object.__new__(BA.BibAiAssistView)
    tasks = []
    settings = {"ai_enabled": True, "compile_fix_enabled": True, "key": "synthetic-key",
                "base": "https://example.invalid", "model": "synthetic", "protocol": "chat"}
    view.page = SimpleNamespace(page=page, t=Tokens(), app=SimpleNamespace(open_path=lambda path: True),
                                ai_initial_paths=lambda: ("/synthetic/source", "/synthetic/final"))
    view._initial_call = lambda: None
    view._r3, view.purpose = True, "typeset"
    view.running = False
    view.task_id, view.preview_id, view.work_dir, view.phase = "T1", "P1", "/synthetic/work", "preview"
    view.request_seq, view._reply_revision, view.preview_revision = 7, 9, 3
    view.candidate_bib, view.backup = "candidate.bib", ""
    view._original_final_dir = view._final_dir = ""
    view._initial_open_stage = "compare"
    view.compile_fix_eligible, view.compile_fix_notice = {}, ""
    view._compile_fix_prepared = view._compile_fix_pdf_opened = None
    view._compile_fix_row = CFD.ft.Column()
    view._compile_fix_settings = lambda: settings.copy()
    view.can_apply = lambda: True
    view._task = tasks.append
    view._refresh = lambda: pytest.fail("compile fix rebuilt the view")
    view._store_set = lambda *args: pytest.fail("compile-fix UI persisted local consent or result_path")
    monkeypatch.setattr(BA, "_refresh_workspace", lambda *a: pytest.fail("compile fix rebuilt the workspace"))
    monkeypatch.setattr(EB, "_call", lambda entry, args, label: EB.EngineCall(entry, ["python", *args], label))
    return view, page, tasks, settings


def _click(button):
    result = button.on_click(SimpleNamespace(control=button))
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _button(view, label):
    return next(button for row in view._compile_fix_row.controls if hasattr(row, "controls")
                for button in row.controls if getattr(button, "content", None) == label)


def _checks(dialog):
    """Consent checkboxes in the authorization dialog (C-lite: none)."""
    return [control for control in dialog.content.content.controls if isinstance(control, ft.Checkbox)]


def _terms_fields(dialog):
    """Manual verification fields in the authorization dialog (C-lite: none)."""
    return [control for control in dialog.content.content.controls if isinstance(control, ft.TextField)]


def _evidence():
    return {"status": "eligible", "eligible": True, "task_id": "T1", "preview_id": "P1",
            "candidate_sha256": "a" * 64, "evidence_id": "E1",
            "evidence": [{"evidence_id": "D1"}, {"evidence_id": "D2"}],
            "diagnostics": [{"message": "first"}, {"message": "second"}]}


def _prepared(request_id, evidence_ids):
    body = json.dumps({"evidence_ids": evidence_ids, "synthetic": "编译修复"}, ensure_ascii=False)
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    return {**_evidence(), "status": "prepared", "request_id": request_id,
            "authorization_id": "A-" + request_id, "request_sha256": body_sha,
            "scope": {"evidence_ids": evidence_ids}, "body": body,
            "summary": {"key_configured": True, "base": "https://api.deepseek.com/v1",
                        "target": "https://api.deepseek.com/v1/chat/completions",
                        "model": "synthetic", "protocol": "chat", "tools": {}, "max_tokens": 100,
                        "thinking": False, "article_budget_tokens": 100, "batch_budget_tokens": 200,
                        "body_bytes": len(body.encode()), "body_sha256": body_sha}}


def _fake_run(view, *receipts):
    expected = iter(receipts)
    calls = []

    async def run(call, *, private_lines=None):
        action, code, result = next(expected)
        assert call.argv[call.argv.index("compile-fix") + 1] == action
        calls.append(call)
        assert private_lines is not None
        private_lines.append(json.dumps({"op": "compile-fix", "ok": code == 0, "compile_fix": result},
                                        ensure_ascii=False))
        return code

    view._run = run
    return calls


def test_scope_selection_terms_and_unknown_require_explicit_new_round(ui_harness):
    view, page, tasks, _ = ui_harness
    prepared = _prepared("R1", ["D2"])
    unknown = {**prepared, "status": "unknown", "actions": ["view_log", "retry", "cancel"],
               "log_path": "/synthetic/request.log"}
    calls = _fake_run(view, ("prepare", 0, prepared), ("send", 2, unknown),
                      ("prepare", 0, _prepared("R2", ["D2"])))
    context = view._initial_action_context()
    view._set_compile_fix(_evidence())
    _click(_button(view, "选择范围并准备请求…"))
    scope_dialog = page.dialogs[-1]
    scope_dialog.content.content.controls[1].value = False
    _click(scope_dialog.actions[-1])
    assert len(calls) == 1 and json.loads(calls[0].stdin_text)["scope"] == {"evidence_ids": ["D2"]}

    dialog = page.dialogs[-1]
    assert any(getattr(control, "value", None) == prepared["body"] for control in dialog.content.content.controls)
    send = dialog.actions[-1]
    # C-lite: no consent checkboxes or manual verification fields; a valid target enables the single button.
    assert not _checks(dialog) and not _terms_fields(dialog)
    assert not send.disabled
    _click(dialog.actions[0])
    _click(send)
    assert len(calls) == 1

    _click(_button(view, "审阅待发送请求…"))
    asyncio.run(tasks.pop()())
    dialog = page.dialogs[-1]
    assert not _checks(dialog) and not _terms_fields(dialog)
    _click(dialog.actions[-1])
    _click(dialog.actions[-1])
    assert len(calls) == 2 and "--compile-fix-terms-ack" in calls[1].argv
    assert "terms_ack" not in calls[1].stdin_text
    assert view.compile_fix_eligible["status"] == "unknown"
    assert [button.content for button in view._compile_fix_row.controls[-1].controls] == [
        "查看本地日志", "新轮重试…", "放弃并保留原产物"]
    _click(_button(view, "新轮重试…"))
    assert len(calls) == 2
    retry_scope = page.dialogs[-1]
    retry_scope.content.content.controls[1].value = False
    _click(retry_scope.actions[-1])
    assert len(calls) == 3 and not tasks
    assert not _checks(page.dialogs[-1]) and not _terms_fields(page.dialogs[-1])
    assert view._initial_action_context() == context


@pytest.mark.parametrize("change", ["base", "key", "scope", "preview"])
def test_restored_prepared_round_requires_fresh_authorization_and_rejects_stale_changes(ui_harness, change):
    view, page, tasks, settings = ui_harness
    calls = _fake_run(view, ("status", 0, _prepared("old", ["D1"])),
                      ("prepare", 0, _prepared("new", ["D1"])))
    identity = view._initial_action_context()
    asyncio.run(view._restore_compile_fix_status(identity))
    assert view.compile_fix_eligible["request_id"] == "old" and not page.dialogs
    _click(_button(view, "审阅待发送请求…"))
    asyncio.run(tasks.pop()())
    dialog = page.dialogs[-1]
    assert view.compile_fix_eligible["request_id"] == "new"
    assert not _checks(dialog) and not _terms_fields(dialog)
    if change == "scope":
        view.compile_fix_eligible["scope"] = {"evidence_ids": ["D2"]}
    elif change == "preview":
        view.preview_id = "P2"
    else:
        settings[change] = "changed"
    _click(dialog.actions[-1])
    assert len(calls) == 2 and not page.dialogs


def test_import_review_apply_and_pdf_confirmation_are_separate(ui_harness, monkeypatch):
    view, page, tasks, _ = ui_harness
    prepared = _prepared("R1", ["D1"])
    imported = {**prepared, "status": "imported"}
    reviewed = {**imported, "status": "reviewed", "review_sha256": "c" * 64,
                "diff": "synthetic diff", "actions": [], "can_adopt": True}
    compiled = {**reviewed, "status": "compiled", "pdf_path": "/synthetic/preview.pdf", "compile_run_id": "C1"}
    opened = []
    open_results = iter([False, True])

    def open_path(path):
        opened.append(path)
        return next(open_results), ""

    monkeypatch.setattr(EB, "open_path", open_path)
    view._set_compile_fix(prepared)
    calls = _fake_run(view, ("import", 0, imported), ("review", 0, reviewed), ("apply", 0, compiled))
    delivery = []

    async def deliver(**kwargs):
        delivery.append(kwargs)

    view._deliver = deliver
    _click(_button(view, "导入本地回件…"))
    dialog = page.dialogs[-1]
    dialog.content.content.value = '{"synthetic": true}'
    _click(dialog.actions[-1])
    asyncio.run(tasks.pop()())
    assert json.loads(calls[-1].stdin_text)["reply_text"] == '{"synthetic": true}'
    assert not page.dialogs and not delivery
    _click(_button(view, "审阅回件差异…"))
    asyncio.run(tasks.pop()())
    assert len(calls) == 2 and not delivery
    _click(page.dialogs[-1].actions[-1])
    assert len(calls) == 3 and "--review-sha256" in calls[-1].argv and not delivery

    assert _button(view, "确认验版并交付…").disabled
    _click(_button(view, "确认验版并交付…"))
    assert not page.dialogs and not tasks
    monkeypatch.setattr(os.path, "isfile", lambda path: path == compiled["pdf_path"])
    _click(_button(view, "打开复编 PDF"))
    assert _button(view, "确认验版并交付…").disabled
    _click(_button(view, "打开复编 PDF"))
    assert opened == [compiled["pdf_path"], compiled["pdf_path"]]
    _click(_button(view, "确认验版并交付…"))
    cancelled = page.dialogs[-1]
    _click(cancelled.actions[0])
    _click(cancelled.actions[-1])
    assert not tasks and not delivery
    _click(_button(view, "确认验版并交付…"))
    _click(page.dialogs[-1].actions[-1])
    asyncio.run(tasks.pop()())
    assert delivery == [{"compile_fix": {"evidence_id": "E1", "request_id": "R1", "review_sha256": "c" * 64}}]


@pytest.mark.parametrize("run_id", ["C1", "stale-run"])
def test_explicit_r3_deliver_passes_bindings_and_checks_receipt(ui_harness, monkeypatch, run_id):
    from ui import bib_ai_assist as BA

    view, page, tasks, _ = ui_harness
    compiled = {**_prepared("R1", ["D1"]), "status": "compiled", "compile_run_id": "C1",
                "review_sha256": "c" * 64, "pdf_path": "/synthetic/preview.pdf"}
    view._check_parent_task = lambda: None
    view._mark = view._log_delivery = lambda *a, **kw: None
    view._build_call = lambda name, params: EB.ai_deliver_call(**params)
    view._delivered_output = lambda payload: "/synthetic/final"
    committed, builds, refreshed = [], [], []

    async def finish(payload, **kwargs):
        committed.append(payload)
        view.delivery_ok = True

    async def run(call):
        builds.append(call.argv)
        result = {"op": "deliver", "ok": True, "delivered": True, "task_id": "T1", "preview_id": "P1",
                  "compile_fix": {"status": "delivered", "task_id": "T1", "preview_id": "P1",
                                  "evidence_id": "E1", "request_id": "R1", "run_id": run_id}}
        view.log = [json.dumps(result)]
        return 0

    view._run, view._finish_delivery = run, finish
    monkeypatch.setattr(BA, "_refresh_workspace", lambda view: refreshed.append(view))
    view._set_compile_fix(compiled)
    view._compile_fix_pdf_opened = view._compile_fix_pdf_identity(compiled)
    _click(_button(view, "确认验版并交付…"))
    assert not builds
    _click(page.dialogs[-1].actions[-1])
    asyncio.run(tasks.pop()())
    assert len(builds) == 1
    argv = builds[0]
    for flag, expected in {"--work-dir": view.work_dir, "--preview-id": view.preview_id,
                           "--evidence-id": "E1", "--request-id": "R1", "--review-sha256": "c" * 64}.items():
        assert argv[argv.index(flag) + 1] == expected
    assert {"deliver", "--ai-workflow", "--ai-enabled", "--compile-fix-enabled", "--json"} <= set(argv)
    if run_id == "C1":
        assert len(committed) == 1 and refreshed == [view]
    else:
        assert not committed and not refreshed
        assert view.compile_fix_eligible["status"] == "check_status"


def test_result_path_typing_and_clear_keep_field_and_task_identity(ui_harness):
    view, _, _, _ = ui_harness
    view.result_path = ""
    view._refs = {"result_path": []}
    profile = view._initial_action_context()
    view._result_picker()
    field = view._refs["result_path"][0]
    for value in ("a", "ab", "abc"):
        field.value = value
        field.on_change(SimpleNamespace(control=field))
        assert view.result_path == value and view._refs["result_path"] == [field]
    assert field._clear_button.visible
    _click(field._clear_button)
    assert view.result_path == field.value == "" and not field._clear_button.visible
    assert view._refs["result_path"] == [field] and view._initial_action_context() == profile


def test_authorization_dialog_has_no_consent_form(ui_harness):
    """C-lite: the 4 checkboxes and 3 manual verification fields are gone, leaving a single send action."""
    from ui import compile_fix_dialog as CFD
    from ui.tokens import Tokens

    _, page, _, _ = ui_harness
    dialog = CFD.show_authorization_dialog(page, Tokens(), _prepared("R-lite", ["D1"]),
                                           lambda *args: None)
    assert not _checks(dialog)
    assert not _terms_fields(dialog)
    assert [button.content for button in dialog.actions] == ["取消", "确认并发送一次（真实付费外发）"]


@pytest.mark.parametrize("base", ["https://api.deepseek.com/v1", "https://api.deepseek.com.proxy.invalid/v1"])
def test_authorization_disclosure_is_complete_and_only_one_click_sends(ui_harness, base):
    """C-lite: full read-only disclosure stays; a valid target enables exactly one explicit send click."""
    from ui import compile_fix_dialog as CFD
    from ui.tokens import Tokens

    _, page, _, _ = ui_harness
    sent = []
    prepared = _prepared("R1", ["D1"])
    prepared["summary"].update(base=base, target=base + "/chat/completions")
    dialog = CFD.show_authorization_dialog(page, Tokens(), prepared, lambda *args: sent.append(args))
    texts = [control.value for control in dialog.content.content.controls if isinstance(control, CFD.ft.Text)]
    host = "api.deepseek.com.proxy.invalid" if "proxy.invalid" in base else "api.deepseek.com"
    assert any(text.startswith("目标主机：" + host) and base + "/chat/completions" in text for text in texts)
    assert any("需人工核验适用条款" in text and "不验证来源真实性或条款内容" in text for text in texts)
    assert any("不是整轮请求总时限" in text and "65536 字节" in text for text in texts)
    assert any("仅本机记录保留 30 天" in text and "不代表服务商留存期限" in text for text in texts)
    # base/model/protocol/tools plus the full frozen body and its SHA-256 stay visible.
    assert any('"base"' in text and '"model"' in text and '"protocol"' in text and '"tools"' in text
               for text in texts)
    assert any(prepared["body"] in text for text in texts)
    assert any("请求 SHA-256：" + prepared["request_sha256"] in text for text in texts)
    assert any("输入粗估约" in text and "不代表服务商计费" in text for text in texts)  # budget disclosure
    assert not any("工具已核验" in text or "服务商不会训练" in text for text in texts)
    assert not _checks(dialog) and not _terms_fields(dialog)
    send = dialog.actions[-1]
    assert send.disabled is False
    _click(send)
    assert sent == [(prepared, True)]
    _click(send)
    assert sent == [(prepared, True)]  # one click = one send; dismissed dialog cannot resend
    reopened = CFD.show_authorization_dialog(page, Tokens(), prepared, lambda *_: pytest.fail("sent"))
    assert not _checks(reopened) and not _terms_fields(reopened)


def test_authorization_is_bound_to_frozen_configuration(ui_harness):
    """C-lite: the single click is still rejected when the shown summary drifts after opening."""
    from ui import compile_fix_dialog as CFD
    from ui.tokens import Tokens

    _, page, _, _ = ui_harness
    prepared = _prepared("R-bind", ["D1"])
    sent = []
    dialog = CFD.show_authorization_dialog(page, Tokens(), prepared, lambda *args: sent.append(args))
    assert not dialog.actions[-1].disabled
    prepared["summary"]["model"] = "changed-model"
    _click(dialog.actions[-1])
    assert sent == []


@pytest.mark.parametrize("target", ["https://api.deepseek.com.proxy.invalid/v1/chat/completions",
                                    "https://api.deepseek.com@proxy.invalid/v1/chat/completions",
                                    "https://api.deepseek.com/v10/chat/completions",
                                    "not a valid endpoint"])
def test_wrong_or_invalid_target_fails_closed(ui_harness, target):
    from ui import compile_fix_dialog as CFD
    from ui.tokens import Tokens

    _, page, _, _ = ui_harness
    prepared = _prepared("R3", ["D1"])
    prepared["summary"]["target"] = target
    dialog = CFD.show_authorization_dialog(page, Tokens(), prepared, lambda *_: pytest.fail("sent"))
    assert dialog.actions[-1].disabled
    _click(dialog.actions[-1])


@pytest.mark.parametrize("output", [0, None, 25])
def test_budget_disclosure_uses_frozen_unicode_and_does_not_invent_total(ui_harness, output):
    from ui import compile_fix_dialog as CFD

    text = CFD._budget_disclosure({"article_budget_tokens": 0, "batch_budget_tokens": 1000,
                                   "max_tokens": output}, "合成文本abcd")
    assert "单篇 不限 (0)；批量 1,000 tokens" in text
    assert "8 字符" in text and "输入粗估约 3 tokens" in text and "不代表服务商计费" in text
    if output:
        assert "约 28 tokens" in text and "输出上限：25 tokens" in text
    elif output == 0:
        assert "输出上限：不限 (0)" in text and "总量未知" in text
    else:
        assert "输出上限未知" in text and "输出上限：不限 (0)" not in text
        assert "输入 + 输出上限" not in text


@pytest.mark.parametrize("phase,preview", [("applied", "P1"), ("preview", "P2")])
def test_retention_deletes_by_key_without_changing_workflow_or_final(ui_harness, phase, preview):
    from ui import compile_fix_dialog as CFD

    view, page, tasks, settings = ui_harness
    prepared = _prepared("R1", ["D1"])
    old_context = view._initial_action_context()
    entry = (prepared, old_context, settings.copy(), ["D1"])
    view._set_compile_fix(prepared, prepared=entry)
    view._show_prepared_compile_fix(entry)
    old_dialog = page.dialogs[-1]
    view.phase, view.preview_id = phase, preview
    view._original_final_dir = view._final_dir = "/synthetic/unchanged-final"
    view.delivery_ok = True
    record = {"record_key": "d" * 64, "task_id": view.task_id, "preview_id": "P1",
              "status": "delivered" if phase == "applied" else "prepared",
              "expires_at": "2026-12-25T00:00:00Z", "has_reply": True}
    second_record = {**record, "record_key": "e" * 64, "preview_id": "P0", "status": "expired"}
    calls = []

    async def run(call, *, private_lines):
        action = call.argv[call.argv.index("compile-fix") + 1]
        calls.append(action)
        assert call.explicit_api_only and not call.explicit_ai_key and not call.stdin_text
        envelope = {"op": "compile-fix", "ok": True}
        if action == "retained":
            assert call.argv[1:] == ["--ai-workflow", "--work-dir", view.work_dir,
                                     "compile-fix", "retained", "--json"]
            envelope["retained"] = [record, second_record]
        elif action == "delete":
            assert call.argv[1:] == ["--ai-workflow", "--work-dir", view.work_dir, "compile-fix", "delete",
                                     "--record-key", second_record["record_key"], "--json"]
            envelope["compile_fix"] = {**second_record, "status": "deleted"}
        else:
            pytest.fail(f"unexpected {action}")
        private_lines.append(json.dumps(envelope))
        return 0

    view._run = run
    before = (view.phase, view.preview_id, view.task_id, view.work_dir, view._final_dir,
              view._original_final_dir, view.delivery_ok)
    _click(old_dialog.actions[-1])
    assert calls == [] and not tasks
    _click(old_dialog.actions[-1])
    asyncio.run(view._compile_fix_action("apply", expected=old_context, evidence_id="E1", request_id="R1"))
    assert calls == []
    _click(view._compile_fix_retention_button())
    asyncio.run(tasks.pop()())
    assert [button.content for button in page.dialogs[-1].actions] == [
        "关闭", "删除记录 1 · P1", "删除记录 2 · P0"]
    rendered = "\n".join(control.value for control in page.dialogs[-1].content.content.controls
                         if isinstance(control, ft.Text))
    assert record["record_key"] in rendered and second_record["record_key"] in rendered
    assert "request_id" not in rendered and "evidence_id" not in rendered
    _click(page.dialogs[-1].actions[2])
    asyncio.run(tasks.pop()())
    after = (view.phase, view.preview_id, view.task_id, view.work_dir, view._final_dir,
             view._original_final_dir, view.delivery_ok)
    assert calls == ["retained", "delete"] and after == before
    assert view.compile_fix_eligible == prepared and view._compile_fix_prepared is entry
    assert [button.content for button in page.dialogs[-1].actions] == ["关闭"]


def test_native_result_path_browse_preserves_control_and_rejects_late_result(ui_harness):
    from ui import components as C

    view, _, _, _ = ui_harness
    view.result_path, view.error, view._picking = "", "", False
    view._refs = {"result_path": []}
    view._mark = lambda *a, **kw: None
    scroll = ft.Column(scroll=ft.ScrollMode.AUTO)
    scroll.controls.append(view._result_picker())
    field = view._refs["result_path"][0]
    context = view._initial_action_context()
    pending = []
    view.page.app.browse_field = lambda ref, *a, **kw: pending.append((ref, kw))
    browse = view._browse("result_path", "synthetic")
    browse()
    ref, callbacks = pending.pop()
    assert callbacks["result_guard"]()
    ref[0].value = "/synthetic/selected.txt"
    C.sync_clearable(ref[0], update=False)
    callbacks["on_result"](ref[0].value)
    assert view.result_path == field.value == "/synthetic/selected.txt"
    assert scroll.controls and ref[0] is field and view._initial_action_context() == context
    browse()
    pending.pop()[1]["on_result"](None)
    assert view.result_path == field.value
    browse()
    view.request_seq += 1
    ref, callbacks = pending.pop()
    assert not callbacks["result_guard"]()
    callbacks["on_result"]("/synthetic/late.txt")
    assert view.result_path == field.value == "/synthetic/selected.txt" and ref[0] is field
