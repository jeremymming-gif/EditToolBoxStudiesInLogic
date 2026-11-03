"""批 114（用户 m02990）：全壳按钮语义统一契约。

规则（lead 2026-11-03 下发）：
- 主操作 = primary（每屏 1–2 个）；次操作 = secondary；破坏性 = danger；
- 纯导航/链接 = text_button；图标按钮 = icon_button；
- 整期流水线的 6 个步骤「运行」必须是描边按钮、带图标、运行中/引擎未就绪时 disabled 且
  tooltip 说明原因；全屏主色只留给「运行整期流水线」。
"""
from __future__ import annotations

import ast
from pathlib import Path

import flet as ft

from ui import components as C
from ui import pages as P
from ui.tokens import Tokens
from test_b112_post_b import _FakeApp, _build_issue, _build_single, _walk

APP_UI = Path(__file__).resolve().parents[1] / "app" / "ui"


def _of(root, kind):
    return [ctl for ctl in _walk(root) if isinstance(ctl, kind)]


# ── 1. 步骤卡「运行」：描边按钮 + 图标 + 等高 + 明确 disabled 态 ───────────
def test_issue_step_run_buttons_are_secondary_play_buttons():
    page, _root = _build_issue()
    t = page.t
    keys = {step["key"] for step in page.STEPS}
    assert set(page._step_run_btns) == keys, "6 个步骤都要有自己的运行按钮"
    for btn in page._step_run_btns.values():
        assert isinstance(btn, ft.OutlinedButton), "次操作应是描边按钮，不是文字链"
        assert str(btn.content) == "运行"
        assert btn.icon == C.ic("PLAY_ARROW"), "动作按钮应带播放图标"
        assert btn.height == t.control_h(), "按钮高度跟统一控制高一致"
        assert btn.disabled is False and btn.tooltip is None


def test_issue_step_run_buttons_disabled_with_reason():
    app = _FakeApp()
    app.engine_status.ok = False
    page = P.IssuePage(app)
    page.build()
    for btn in page._step_run_btns.values():
        assert btn.disabled is True, "引擎未就绪时步骤运行必须禁用"
        assert "引擎未就绪" in (btn.tooltip or ""), "禁用必须说明原因"

    app.engine_status.ok = True
    app.running = True
    page.refresh_running()
    for btn in page._step_run_btns.values():
        assert btn.disabled is True, "运行中不能重复点步骤运行"
        assert "运行中" in (btn.tooltip or "")

    app.running = False
    page.refresh_running()
    for btn in page._step_run_btns.values():
        assert btn.disabled is False and btn.tooltip is None


# ── 2. 主操作唯一性：整期页只有页面级「运行整期流水线」是主色实心按钮 ───────
def test_issue_primary_actions_are_limited_and_step_rows_are_not_filled():
    page, root = _build_issue()
    filled = _of(root, ft.FilledButton)
    contents = [str(ctl.content) for ctl in filled]
    # 用户 m03093：整期父页全屏唯一主色 = 「运行整期流水线」；6 步概览行上的
    # 「运行」与子页的「运行这一步」都降为描边次操作（批 114 改导航子页）。
    assert len(filled) == 1, f"整期页主操作必须唯一：{contents}"
    assert contents.count("运行整期流水线") == 1
    overview_runs = [ctl for ctl in _of(root, ft.OutlinedButton)
                     if str(ctl.content) == "运行"]
    assert len(overview_runs) == len(page.STEPS), "6 步概览行都要能直接运行"
    assert all(ctl.icon == C.ic("PLAY_ARROW") for ctl in overview_runs)
    assert filled[0] is page._run_all_btn or page._run_all_btn in filled
    # 步骤概览/子页上的运行按钮一律不是主色实心。
    assert not _of([*page._step_run_btns.values()], ft.FilledButton)
    step_page = page._build_step_page("toc")
    step_runs = [ctl for ctl in _of(step_page, ft.OutlinedButton)
                 if str(ctl.content) == "运行这一步"]
    assert step_runs, "运行这一步应是描边按钮（次操作）"
    assert step_runs[0].icon == C.ic("PLAY_ARROW")
    # 子页里的步骤专属主操作（如目录面板的「生成并编译」）不受影响；
    # 守的是「运行这一步」自身绝不是实心主色。
    assert step_runs[0] not in _of(step_page, ft.FilledButton)


def test_single_page_primary_actions_within_limit_and_preview_is_button():
    _page, root = _build_single()
    filled = _of(root, ft.FilledButton)
    assert len(filled) <= 2, "每屏主操作不超过 2 个"
    previews = [ctl for ctl in _of(root, ft.OutlinedButton)
                if str(ctl.content) == "预览元数据"]
    assert previews, "预览元数据是明确动作 → 描边按钮"
    assert previews[0].icon == C.ic("PREVIEW")


# ── 3. 危险动作 = danger 按钮（tint 式），不再是无样式文字链 ───────────────
def test_secondary_button_disabled_state_is_visibly_muted():
    t = Tokens("light", 1.0, 96)
    btn = C.secondary_button("取消", None, t, disabled=True)
    state = ft.ControlState
    assert btn.style.color[state.DEFAULT] == t.palette.text_muted
    assert btn.style.color[state.HOVERED] == t.palette.text
    assert btn.style.color[state.DISABLED] == t.palette.text_faint, \
        "次操作禁用态必须明显变浅，否则用户会误点"


def test_danger_button_is_tinted_not_plain_link():
    t = Tokens("light", 1.0, 96)
    btn = C.danger_button("删除", None, t)
    assert isinstance(btn, ft.TextButton)
    state = ft.ControlState.DEFAULT
    assert btn.style.bgcolor[state] == t.buttons.danger_soft
    assert btn.style.color == t.buttons.danger


def test_destructive_sites_use_danger_button_static():
    expected = {
        "ai_panel.py": {"删除"},
        "metadata_form.py": {"删除", "删"},
        "toc.py": {"删除"},
    }
    seen: dict[str, set[str]] = {name: set() for name in expected}
    for name in expected:
        tree = ast.parse((APP_UI / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            fn = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if fn != "danger_button" or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                seen[name].add(arg.value)
    for name, labels in expected.items():
        assert labels <= seen[name], f"{name} 的破坏性动作缺 danger 按钮: {labels - seen[name]}"


# ── 4. 静态契约：执行动作不许再用纯文字链（防回退） ───────────────────────
FORBIDDEN_TEXT_BUTTON_LABELS = {"运行", "重试", "预览元数据", "删除", "删", "导出诊断包"}


def test_execution_actions_are_not_text_links_static():
    offenders: list[str] = []
    for path in sorted(APP_UI.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            fn = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if fn != "text_button" or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and arg.value in FORBIDDEN_TEXT_BUTTON_LABELS:
                offenders.append(f"{path.name}:{node.lineno} text_button({arg.value!r})")
    assert not offenders, "执行动作仍是纯文字链: " + "; ".join(offenders)
