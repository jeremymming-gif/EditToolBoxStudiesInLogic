"""legacy-idea-8 R3 initial-AI scope and budget guard regressions."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services import ai_prefs as AI
from services import engine_bridge as EB
from ui import bib_ai_assist as BAA


@pytest.fixture(autouse=True)
def _synthetic_manuscripts_root(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-manuscripts"
    root.mkdir()
    (root / "2026年第1期").mkdir()
    monkeypatch.setenv("SLCN_MANUSCRIPTS", str(root))
    monkeypatch.setenv("SLCN_TEST_DATASET_ID", "legacy-idea-8-synthetic-v1")
    monkeypatch.delenv("SLCN_LLM_MAX_TOKENS", raising=False)
    return root


def _args(call):
    entry_index = next(i for i, value in enumerate(call.argv)
                       if Path(value).name == "convert.py")
    return call.argv[entry_index + 1:]


def _flag_value(args, flag):
    return args[args.index(flag) + 1]


def test_r3_convert_ignores_legacy_global_verify_preferences():
    args = _args(EB.convert_call(
        source="/tmp/synthetic.tex", out="/tmp/synthetic-out",
        ai_taskbook_version=3, ai_execute="bib_verify,typeset",
        verify_refs=True, verify_plan=True, verify_max=7,
        verify_all_refs=True,
    ))

    assert "--verify-plan" not in args
    assert "--verify-all-refs" not in args
    assert "--verify-refs" not in args
    assert _flag_value(args, "--verify-max") == "120"


def test_r3_prepare_preserves_existing_article_and_batch_budgets():
    args = EB.r3_ai_budget_args({
        "article_budget_tokens": 12345,
        "batch_budget_tokens": 54321,
    })

    assert _flag_value(args, "--ai-budget-tokens") == "12345"
    assert _flag_value(args, "--ai-batch-budget-tokens") == "54321"


def test_taskbook_scope_counts_pending_and_explicitly_fillable_items():
    taskbook = """## 文献核实
- 可用材料：待核条目 2 条，正文引用缺条 1 条，必要依赖缺条 1 条。
#### M1 · 确认为同一来源后可按原键交回完整条目
#### M2 · 确认为同一来源后可按原键交回完整条目
"""

    assert AI.r3_task_scope(taskbook) == {
        "known": True, "count": 4, "uncertain": False, "warning": "",
    }


@pytest.mark.parametrize("pending,expected_known,expected_count", [
    (120, True, 120),
    (121, True, 121),
])
def test_taskbook_authoritative_count_boundary(pending, expected_known, expected_count):
    taskbook = ("## 文献核实\n"
                f"- 可用材料：待核条目 {pending} 条，正文引用缺条 0 条，必要依赖缺条 0 条。\n")

    scope = AI.r3_task_scope(taskbook)

    assert scope["known"] is expected_known
    assert scope["count"] == expected_count


def test_taskbook_contradictory_pending_counts_are_unknown():
    taskbook = ("## 文献核实\n- 可用材料：待核条目 121 条，正文引用缺条 0 条。\n"
                "另有待核条目 1 条。\n")

    scope = AI.r3_task_scope(taskbook)

    assert scope["known"] is False
    assert scope["count"] is None


def test_chat_and_api_use_the_same_scoped_taskbook_payload(monkeypatch):
    from Engine.slcn.ai_workflow_taskbook import build_context, render_taskbook

    bib = ("@article{Cited, author={A}, title={Cited Article}, journal={J}, year={2020}}\n"
           "@book{UncitedSecret, author={Secret}, "
           "title={NEVER_DISCLOSE_UNCITED_SENTINEL}, publisher={P}, year={2020}}")
    context = build_context(
        state={"task_id": "T-SYNTHETIC-1", "entry": "convert",
               "scope": {"exec": ["bib_verify"], "adopt": []},
               "safe_hit_keys": [], "allowed_keys": ["Fillable"], "cite_keys": []},
        bib_text=bib,
        tex_text=r"\cite{Cited,Fillable,Unauthorized}",
        source_evidence={"refs_by_key": {
            "Fillable": "Known source for Fillable",
            "Unauthorized": "Known source for Unauthorized",
        }},
        rule_sections=[],
    )
    taskbook = render_taskbook(context=context)
    scope = AI.r3_task_scope(taskbook)

    assert "待核条目 1 条" in taskbook
    assert "Fillable · 确认为同一来源后可按原键交回完整条目" in taskbook
    assert "Unauthorized · 待人工确认，本次未授权补条" in taskbook
    assert scope["known"] and scope["count"] == 2
    assert "NEVER_DISCLOSE_UNCITED_SENTINEL" not in taskbook

    copied = []
    chat = SimpleNamespace(
        _r3=True, task_id="T-SYNTHETIC-1", request_seq=0, _taskbook_cache=None,
        _fn=lambda _name: lambda **_kwargs: object(),
        _check_parent_task=lambda: None,
        _work_dir_path=lambda: "/tmp/synthetic-work",
        _bump_request=lambda: setattr(chat, "request_seq", chat.request_seq + 1),
        _copy=lambda text, exact=False: (copied.append((text, exact)) or True),
        _notify=lambda _message: None, _mark=lambda *_args, **_kwargs: None,
        _refresh=lambda: None,
    )
    monkeypatch.setattr(EB, "read_json_command", lambda _call: {
        "ok": True,
        "json": {"ok": True, "task_id": "T-SYNTHETIC-1", "text": taskbook},
    })
    import asyncio

    assert asyncio.run(BAA.BibAiAssistView.copy_task_text(chat)) is True
    assert copied == [(taskbook, True)]

    api = _request_view(taskbook)
    estimated_texts = []
    estimate = AI.r3_budget_estimate

    def capture_estimate(text, settings, **kwargs):
        estimated_texts.append(text)
        return estimate(text, settings, **kwargs)

    monkeypatch.setattr(AI, "r3_budget_estimate", capture_estimate)
    assert asyncio.run(BAA.BibAiAssistView.request_api(api)) is None
    assert estimated_texts == [taskbook]
    assert taskbook == copied[0][0]
    assert len(api.dialogs) == 1 and "预计委派 2 条" in api.dialogs[0]["message"]


@pytest.mark.parametrize("pending,expected_copy", [(120, True), (121, False)])
def test_chat_real_copy_path_enforces_fixed_scope_limit(monkeypatch, pending, expected_copy):
    import asyncio

    taskbook = ("## 文献核实\n"
                f"- 可用材料：待核条目 {pending} 条，正文引用缺条 0 条。\n")
    copied = []
    view = _copy_view(monkeypatch, taskbook, copied)

    result = asyncio.run(BAA.BibAiAssistView.copy_task_text(view))

    assert result is expected_copy
    assert bool(copied) is expected_copy
    if expected_copy:
        assert copied == [(taskbook, True)]
    else:
        assert "超过固定上限 120 条" in view.error
        assert view._taskbook_cache["text"] == taskbook


def test_chat_real_copy_path_blocks_unknown_count_without_hiding_taskbook(monkeypatch):
    import asyncio

    taskbook = "## 文献核实\n待核范围缺少可靠计数。\n"
    copied = []
    view = _copy_view(monkeypatch, taskbook, copied)

    result = asyncio.run(BAA.BibAiAssistView.copy_task_text(view))

    assert result is False
    assert not copied
    assert "待核条数存在缺失或矛盾" in view.error
    assert view._taskbook_cache["text"] == taskbook


def _copy_view(monkeypatch, taskbook, copied):
    fields = {"ok": True, "task_id": "T-SYNTHETIC-1", "path": "/tmp/taskbook.md",
              "text": taskbook}
    view = SimpleNamespace(
        _r3=True, task_id="T-SYNTHETIC-1", request_seq=0, _taskbook_cache=None,
        errors=[], _work_dir_path=lambda: "/tmp/synthetic-work",
        _fn=lambda _name: lambda **_kwargs: object(),
        _check_parent_task=lambda: None,
        _bump_request=lambda: setattr(view, "request_seq", view.request_seq + 1),
        _copy=lambda text, exact=False: (copied.append((text, exact)) or True),
        _notify=lambda _message: None, _mark=lambda *_args, **_kwargs: None,
        _refresh=lambda: None,
    )
    from services import engine_bridge as EB

    monkeypatch.setattr(EB, "read_json_command", lambda _call: {
        "ok": True,
        "json": {"ok": True, "task_id": "T-SYNTHETIC-1", "path": fields["path"],
                 "text": taskbook},
    })
    return view


@pytest.mark.parametrize("taskbook", ["", "## 文献核实\n范围未知"])
def test_unknown_taskbook_count_fails_closed(taskbook):
    scope = AI.r3_task_scope(taskbook)

    assert scope["known"] is False
    assert scope["count"] is None
    assert "已阻止请求" in scope["warning"]


def test_unresolved_body_scope_is_disclosed_without_expansion():
    taskbook = ("## 文献核实\n按正文实际引用的 2 个键裁剪范围；未引用条目原文不在此披露。\n"
                "- 可用材料：待核条目 2 条，正文引用缺条 0 条，必要依赖缺条 0 条。\n"
                "正文里有编号标记或脚注引用，但判不出对应的条目键：按已识别的引用键裁剪。")

    scope = AI.r3_task_scope(taskbook)
    caption = BAA.BibAiAssistView._r3_scope_caption(
        type("View", (), {"_taskbook_cache": {"text": taskbook}})()
    )

    assert scope["known"] and scope["count"] == 2 and scope["uncertain"]
    assert "未引用条目不纳入范围" in caption
    assert "未识别部分" in caption


def test_ai_settings_save_preserves_legacy_and_unknown_values():
    class Store:
        def __init__(self):
            self.data = {
                "ai": {"enabled": True, "verify_max": -7,
                       "future_setting": {"keep": True},
                       "article_budget_tokens": 30000,
                       "batch_budget_tokens": 1000000},
                "bibdb": {"verify_plan": True, "verify_all_refs": True},
            }

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value):
            self.data[key] = value

    store = Store()

    AI.save(store, {"model": "synthetic-model"})

    assert store.data["ai"]["verify_max"] == -7
    assert store.data["ai"]["future_setting"] == {"keep": True}
    assert store.data["bibdb"] == {"verify_plan": True, "verify_all_refs": True}
    assert "verify_max" not in AI.r3_request_settings(AI.raw_from_store(store))


def _request_view(taskbook):
    fields = {"ok": True, "task_id": "T-SYNTHETIC-1", "path": "/tmp/taskbook.md",
              "text": taskbook}
    settings = {
        "enabled": True,
        "key": "synthetic-key-not-for-argv",
        "base": "https://example.invalid/v1",
        "model": "synthetic-model",
        "protocol": "chat",
        "thinking": False,
        "article_budget_tokens": 30000,
        "batch_budget_tokens": 1000000,
    }
    view = SimpleNamespace(
        _initial_call=lambda *_: None,
        _r3=True,
        running=False,
        channel="api",
        task_id="T-SYNTHETIC-1",
        page=SimpleNamespace(store=SimpleNamespace(get=lambda _key, default=None: settings)),
        errors=[],
        dialogs=[],
        scheduled=[],
        dispatched=[],
        _taskbook_cache=None,
        _read_responses=[fields],
    )
    view._require = lambda *_args: True
    view._work_dir_path = lambda: "/tmp/synthetic-work"
    view._call_kwargs = lambda: {}
    view._api_env = lambda: {}
    view._build_call = lambda *_args: object()
    view._initial_identity = lambda: (view.request_seq, view.task_id, view.work_dir,
                                      "/tmp/synthetic-work", ("/tmp/synthetic.tex",))
    view.request_seq = 1
    view.work_dir = "/tmp/synthetic-work"
    async def read_current(*, force=False):
        current = view._read_responses.pop(0) if view._read_responses else fields
        view._taskbook_cache = current
        return current["text"]
    view._read_current_taskbook = read_current
    view._fail = lambda *args: view.errors.append(args)
    view._refresh = lambda: None
    view._confirm = lambda **kwargs: view.dialogs.append(kwargs)
    view._do = lambda callback: view.scheduled.append(callback())
    view._build_call = lambda _name, kwargs: SimpleNamespace(argv=(
        ["convert.py", "--taskbook", kwargs.get("taskbook", ""),
         "--taskbook-sha256", kwargs.get("taskbook_sha256", "")]
    ), kwargs=kwargs)

    async def run_request(call, initial, identity, request_identity=None):
        view.dispatched.append((call, initial, identity, request_identity))

    view._run_api_request = run_request
    view._send_confirmed_r3_api = lambda *args: (
        BAA.BibAiAssistView._send_confirmed_r3_api(view, *args)
    )
    view._r3_api_request_identity = lambda: BAA.BibAiAssistView._r3_api_request_identity(view)
    view._app = lambda: None
    return view


async def _async_value(value):
    return value


def test_api_preflight_confirms_only_after_exact_scope_and_budget_check(monkeypatch):
    import asyncio

    monkeypatch.setenv("SLCN_LLM_MAX_TOKENS", "4096")
    taskbook = ("## 文献核实\n按正文实际引用的 2 个键裁剪范围。\n"
                "- 可用材料：待核条目 2 条，正文引用缺条 0 条，必要依赖缺条 0 条。")
    view = _request_view(taskbook)

    asyncio.run(BAA.BibAiAssistView.request_api(view))

    assert not view.errors
    assert len(view.dialogs) == 1
    dialog = view.dialogs[0]
    assert "预计委派 2 条" in dialog["message"]
    assert "token 用量估算" in dialog["impact"]
    assert "输出上限 4096" in dialog["impact"]
    assert "不是账单" in dialog["impact"]
    assert callable(dialog["callback"])


def test_api_confirmation_binds_exact_text_path_and_is_one_use():
    import asyncio
    import hashlib

    taskbook = ("## 文献核实\n按正文实际引用的 2 个键裁剪范围。\n"
                "- 可用材料：待核条目 2 条，正文引用缺条 0 条，必要依赖缺条 0 条。")
    view = _request_view(taskbook)
    asyncio.run(BAA.BibAiAssistView.request_api(view))
    callback = view.dialogs[0]["callback"]

    callback()
    callback()
    assert len(view.scheduled) == 2
    asyncio.run(view.scheduled[0])
    asyncio.run(view.scheduled[1])

    assert len(view.dispatched) == 1
    call = view.dispatched[0][0]
    assert call.argv[call.argv.index("--taskbook") + 1] == "/tmp/taskbook.md"
    assert call.argv[call.argv.index("--taskbook-sha256") + 1] == hashlib.sha256(
        taskbook.encode("utf-8")).hexdigest()


def test_api_confirmation_blocks_changed_taskbook_before_dispatch():
    import asyncio

    reviewed = ("## 文献核实\n- 可用材料：待核条目 1 条，正文引用缺条 0 条。\n")
    changed = ("## 文献核实\n- 可用材料：待核条目 2 条，正文引用缺条 0 条。\n")
    view = _request_view(reviewed)
    view._read_responses.append({
        "ok": True, "task_id": "T-SYNTHETIC-1", "path": "/tmp/taskbook.md",
        "text": changed,
    })
    asyncio.run(BAA.BibAiAssistView.request_api(view))
    view.dialogs[0]["callback"]()
    asyncio.run(view.scheduled[0])

    assert not view.dispatched
    assert any("旧确认已失效" in message for _title, message in view.errors)


@pytest.mark.parametrize("change", ["channel", "budget"])
def test_api_confirmation_blocks_changed_channel_or_budget(change):
    import asyncio

    taskbook = ("## 文献核实\n- 可用材料：待核条目 1 条，正文引用缺条 0 条。\n")
    view = _request_view(taskbook)
    asyncio.run(BAA.BibAiAssistView.request_api(view))
    if change == "channel":
        view.channel = "chat"
    else:
        view.page.store.get = lambda _key, default=None: {
            "enabled": True, "key": "synthetic-key-not-for-argv",
            "base": "https://example.invalid/v1", "model": "synthetic-model",
            "protocol": "chat", "thinking": False,
            "article_budget_tokens": -1, "batch_budget_tokens": 1000000,
        }
    view.dialogs[0]["callback"]()
    asyncio.run(view.scheduled[0])

    assert not view.dispatched
    assert view.errors


def test_api_preflight_blocks_unknown_scope_before_confirmation():
    import asyncio

    view = _request_view("## 文献核实\n范围无法判定")

    asyncio.run(BAA.BibAiAssistView.request_api(view))

    assert not view.dialogs
    assert view.errors and "已阻止请求" in view.errors[0][1]


@pytest.mark.parametrize("budget", [-1, "invalid"])
def test_malformed_or_negative_api_budget_blocks_before_confirmation(budget):
    import asyncio

    taskbook = ("## 文献核实\n- 可用材料：待核条目 1 条，正文引用缺条 0 条。\n")
    view = _request_view(taskbook)
    view.page.store.get = lambda _key, default=None: {
        "enabled": True, "key": "synthetic-key-not-for-argv",
        "base": "https://example.invalid/v1", "model": "synthetic-model",
        "protocol": "chat", "thinking": False,
        "article_budget_tokens": budget, "batch_budget_tokens": 1000000,
    }

    asyncio.run(BAA.BibAiAssistView.request_api(view))

    assert not view.dialogs
    assert not view.dispatched
    assert view.errors


def test_zero_budget_keeps_explicit_unlimited_semantics():
    values = AI.r3_budget_limits({"article_budget_tokens": 0,
                                  "batch_budget_tokens": 0})

    assert values == (0, 0)


def test_budget_estimate_uses_existing_token_caps_and_never_claims_price():
    estimate = AI.r3_budget_estimate(
        "任务书正文", {"article_budget_tokens": 3000, "batch_budget_tokens": 9000},
        request_env={},
    )

    assert estimate["total_tokens"] == (
        estimate["prompt_tokens"] + estimate["completion_tokens"]
    )
    assert estimate["article_budget_tokens"] == 3000
    assert estimate["batch_budget_tokens"] == 9000
    assert estimate["over_budget"] is False
    over = AI.r3_budget_estimate("x" * 10000, {"article_budget_tokens": 1})
    assert over["over_budget"] is True


@pytest.mark.parametrize(("request_env", "expected", "source"), [
    ({}, 2048, "引擎默认上限（未设置）"),
    ({"SLCN_LLM_MAX_TOKENS": "4096"}, 4096, "环境覆盖上限"),
    ({"SLCN_LLM_MAX_TOKENS": ""}, None, "环境显式不封顶（空值）"),
    ({"SLCN_LLM_MAX_TOKENS": "0"}, None, "环境显式不封顶（≤ 0）"),
    ({"SLCN_LLM_MAX_TOKENS": "-5"}, None, "环境显式不封顶（≤ 0）"),
    ({"SLCN_LLM_MAX_TOKENS": "bad"}, 2048, "坏值按引擎默认上限处理"),
])
def test_r3_output_estimate_matches_engine_environment_semantics(
        monkeypatch, request_env, expected, source):
    from slcn import llm

    if "SLCN_LLM_MAX_TOKENS" in request_env:
        monkeypatch.setenv("SLCN_LLM_MAX_TOKENS", request_env["SLCN_LLM_MAX_TOKENS"])
    else:
        monkeypatch.delenv("SLCN_LLM_MAX_TOKENS", raising=False)
    engine_limit = llm.request_params_from_env()[1]
    estimate = AI.r3_budget_estimate(
        "合成任务书", {"article_budget_tokens": 0, "batch_budget_tokens": 0},
        request_env=request_env,
    )

    assert engine_limit == expected
    assert estimate["completion_tokens"] == expected
    assert estimate["output_limit_source"] == source
    if expected is None:
        assert estimate["total_tokens"] is None
        assert estimate["budget_verifiable"] is False
        assert estimate["unbounded_with_finite_budget"] is False
    else:
        assert estimate["total_tokens"] == estimate["prompt_tokens"] + expected
        assert estimate["budget_verifiable"] is True


@pytest.mark.parametrize("raw_limit", ["", "0", "-1"])
def test_unbounded_output_fails_closed_when_any_budget_is_finite(monkeypatch, raw_limit):
    import asyncio

    monkeypatch.setenv("SLCN_LLM_MAX_TOKENS", raw_limit)
    taskbook = "## 文献核实\n- 可用材料：待核条目 1 条，正文引用缺条 0 条。\n"
    view = _request_view(taskbook)

    asyncio.run(BAA.BibAiAssistView.request_api(view))

    assert not view.dialogs
    assert not view.dispatched
    assert view.errors and "输出不封顶" in view.errors[0][1]


def test_unbounded_output_confirmation_never_claims_a_finite_total(monkeypatch):
    import asyncio

    monkeypatch.setenv("SLCN_LLM_MAX_TOKENS", "0")
    taskbook = "## 文献核实\n- 可用材料：待核条目 1 条，正文引用缺条 0 条。\n"
    view = _request_view(taskbook)
    view.page.store.get = lambda _key, default=None: {
        "enabled": True, "key": "synthetic-key-not-for-argv",
        "base": "https://example.invalid/v1", "model": "synthetic-model",
        "protocol": "chat", "thinking": False,
        "article_budget_tokens": 0, "batch_budget_tokens": 0,
    }

    asyncio.run(BAA.BibAiAssistView.request_api(view))

    assert not view.errors
    assert len(view.dialogs) == 1
    impact = view.dialogs[0]["impact"]
    assert "输出不封顶" in impact
    assert "无法计算 token 总量上界" in impact
    assert "输出额度 2048" not in impact
    assert "约 None tokens" not in impact
