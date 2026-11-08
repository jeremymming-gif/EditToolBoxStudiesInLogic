"""P02 2.2-G：AI 两段式「界面文案 / 参数」与引擎真实语义一致性（离线，零真实 API）。

背景（P02 R03/R05/R09）：
- 引擎第二段**默认**把文献核实结果并入 `reference.bib`（改前备份 `reference.bib.ai前`）；
  `--coop-apply` 只控制低置信 BIB/META/FN 建议，`--no-ai-merge` 才关闭文献并入。
- 失败/取消必须显示「未完成 / 最终交付目录未写 / 暂存保留可重跑」，不能显示成「第二段结束」。
- context 档在没有引用/脚注锚点时会退回整篇正文，界面必须如实标注。
"""
from __future__ import annotations

import json
from pathlib import Path

import flet as ft
import pytest

from services import ai_prefs as AI
from services import settings_store as SS
from ui import ai_panel as AIP
from ui.tokens import Tokens


class FakeStore:
    def __init__(self) -> None:
        self.data = {"ai": AI.defaults(), "options": {}, "recent": {}, "paths": {}}

    def get(self, dotted, default=None):
        node = self.data
        for part in str(dotted).split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted, value) -> None:
        parts = str(dotted).split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def update_options(self, **kwargs) -> None:
        self.data.setdefault("options", {}).update(kwargs)

    def save(self) -> None:
        pass


class FakeApp:
    def __init__(self, store) -> None:
        self.store = store

    def toast(self, message) -> None:
        pass

    def browse_field(self, *args, **kwargs) -> None:
        return None

    def goto(self, key) -> None:
        pass


class FakePage:
    def __init__(self, store) -> None:
        self.app = FakeApp(store)
        self.t = Tokens()
        self.fields = {"single_source": "", "single_out": ""}

    def field(self, key):
        return self.fields.get(key, "")


class _Field:
    def __init__(self, value: str = "") -> None:
        self.value = value


def _args(call) -> list[str]:
    for i, item in enumerate(call.argv):
        if str(item).endswith(call.entry):
            return [str(v) for v in call.argv[i + 1:]]
    return [str(v) for v in call.argv]


def _walk(control):
    yield control
    for name in ("controls", "content"):
        child = getattr(control, name, None)
        if isinstance(child, list):
            for item in child:
                yield from _walk(item)
        elif isinstance(child, ft.Control):
            yield from _walk(child)


def _visible_text(view) -> str:
    """面板上用户能读到的文案（Text.value / Checkbox.label / 按钮 text）。"""
    parts: list[str] = []
    for control in _walk(view):
        for name in ("value", "label", "text"):
            value = getattr(control, name, None)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


@pytest.fixture
def store(tmp_path, monkeypatch) -> SS.SettingsStore:
    monkeypatch.setenv("P2_SETTINGS", str(tmp_path / "settings.json"))
    instance = SS.SettingsStore()
    instance.load()
    return instance


@pytest.fixture
def enabled(store) -> SS.SettingsStore:
    AI.save(store, {"enabled": True})
    return store


def _ph2_panel(enabled) -> AIP.AiPanel:
    panel = AIP.AiPanel(FakePage(enabled))
    panel.build()
    panel._coop = True
    panel._stage_field = _Field(r"C:\out\Zhang_待核")
    panel._stage_user = True
    panel._reply_fields = [_Field(r"C:\r\回复.md")]
    return panel


# ── ① 第二段默认行为：文献默认并回，低置信默认不应用 ───────────────────
def test_ph2_default_does_not_apply_low_confidence_and_does_not_disable_merge(enabled):
    args = _args(_ph2_panel(enabled).build_call(source=r"C:\m\main.tex", out=r"C:\out"))
    assert "--coop-replies" in args
    assert "--coop-apply" not in args      # 低置信建议默认只记录
    assert "--no-ai-merge" not in args     # 文献默认并回（引擎默认）


def test_ph2_apply_low_confidence_flag(enabled):
    panel = _ph2_panel(enabled)
    panel._apply = True
    args = _args(panel.build_call(source=r"C:\m\main.tex", out=r"C:\out"))
    assert "--coop-apply" in args
    assert "--no-ai-merge" not in args


def test_ph2_coop_no_merge_flag(enabled):
    panel = _ph2_panel(enabled)
    panel._coop_no_ai_merge = True
    args = _args(panel.build_call(source=r"C:\m\main.tex", out=r"C:\out"))
    assert "--no-ai-merge" in args
    assert "--coop-apply" not in args


# ── ② 文案不得再声称「产物不动」；两个开关的语义分开写 ─────────────────
def test_coop_copy_does_not_claim_products_untouched(enabled):
    text = _visible_text(AIP.AiPanel(FakePage(enabled)).build())
    assert "产物不动" not in text
    assert "只记录建议" not in text
    assert "应用低置信建议（--coop-apply）" in text
    assert "不并回文献（--no-ai-merge）" in text
    assert "文献核实结果的并回见上一行" in text


def test_ph2_mode_line_distinguishes_low_confidence_from_merge(enabled):
    panel = _ph2_panel(enabled)
    panel._refresh_mode()
    assert "低置信建议只记录" in panel._mode_ctl.value
    assert "文献默认并回" in panel._mode_ctl.value
    panel._apply = True
    panel._refresh_mode()
    assert "应用低置信建议" in panel._mode_ctl.value
    panel._coop_no_ai_merge = True
    panel._refresh_mode()
    assert "文献不并回" in panel._mode_ctl.value


# ── ③ context 档退回整篇必须标注 ───────────────────────────────────────
def test_fulltext_context_label_discloses_fallback():
    assert "无锚点" in AI.FULLTEXT_LABELS[AI.FULLTEXT_CONTEXT]
    assert "退回整篇" in AI.FULLTEXT_LABELS[AI.FULLTEXT_CONTEXT]


def test_fulltext_summary_marks_estimate():
    state = {"fulltext": {"mode": "context", "chars": 211, "tokens_est": 71}}
    text = AI.fulltext_summary(state)
    assert "附三正文" in text and "211 字" in text
    assert "估算" in text
    assert "元" not in text           # 不换算成费用
    assert AI.fulltext_summary({}) == ""


# ── ④ 失败/取消不再伪装成「第二段结束」；成功提示人工核对 ────────────────
def test_ph2_failure_summary_states_incomplete(enabled):
    page = FakePage(enabled)
    panel = AIP.AiPanel(page)
    panel.build()
    panel._last_mode = AI.MODE_PH2
    panel.after_run(None, 2)
    text = panel._summary_ctl.value
    assert "第二段未完成" in text
    assert "最终交付目录未写" in text
    assert "可重跑第二段" in text
    assert "第二段结束" not in text


def test_ph2_success_summary_separates_technical_delivery(enabled):
    page = FakePage(enabled)
    panel = AIP.AiPanel(page)
    panel.build()
    panel._last_mode = AI.MODE_PH2
    panel.after_run(None, 0)
    text = panel._summary_ctl.value
    assert "技术交付完成" in text
    assert "人工核对" in text


def test_ph1_failure_summary_mentions_stage_not_delivered(enabled):
    page = FakePage(enabled)
    panel = AIP.AiPanel(page)
    panel.build()
    panel._last_mode = AI.MODE_PH1
    panel.after_run(None, 2)
    text = panel._summary_ctl.value
    assert "第一段未完成" in text
    assert "不会进最终目录" in text


def test_ph1_success_appends_fulltext_estimate(tmp_path, enabled):
    out = tmp_path / "out"
    stage = out / "Zhang_待核"
    stage.mkdir(parents=True)
    (stage / AI.STATE_NAME).write_text(json.dumps({
        "schema": 1, "phase": "ph1", "params": {"stem": "Zhang"},
        "source": {"path": r"C:\m\main.tex"},
        "taskbook": "任务书.md",
        "fulltext": {"mode": "context", "chars": 211, "tokens_est": 71},
    }, ensure_ascii=False), encoding="utf-8")
    page = FakePage(enabled)
    page.fields["single_out"] = str(out)
    page.fields["single_source"] = r"C:\m\main.tex"
    panel = AIP.AiPanel(page)
    panel.build()
    panel._last_mode = AI.MODE_PH1
    panel.after_run(None, 0)
    text = panel._summary_ctl.value
    assert "第一段完成（未交付）" in text
    assert "附三正文" in text and "估算" in text


# ── ⑤ 引用库「只算账（verify_plan）」不得穿透两段式 ─────────────────────
def test_coop_ph1_ignores_verify_plan_and_always_verifies_refs():
    """G-6：页面把引用库设置的 verify_plan 并进 kwargs 时，第一段仍必须走
    `--verify-refs`（否则引擎只算账提前 return，任务书/状态都不生成）。"""
    from services import engine_bridge as EB

    args = _args(EB.coop_ph1_call(source=r"C:\m\main.tex", out=r"C:\out",
                                  verify_plan=True))
    assert "--verify-refs" in args
    assert "--verify-plan" not in args


def test_coop_ph2_ignores_verify_plan():
    """G-6：第二段不重新核实，也不接受 `--verify-plan`（避免引擎误判）。"""
    from services import engine_bridge as EB

    args = _args(EB.coop_ph2_call(source=r"C:\m\main.tex", out=r"C:\out",
                                  replies=[r"C:\r\回复.md"], verify_plan=True))
    assert "--verify-plan" not in args
    assert "--verify-refs" not in args
