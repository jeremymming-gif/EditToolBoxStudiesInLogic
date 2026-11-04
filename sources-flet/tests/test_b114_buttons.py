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


# ── 1. 无期目录空态：没有旧的 6 行「运行」，只留选目录 CTA ─────────────────
def test_issue_empty_state_has_no_step_run_rows():
    # 批 117：旧总览删除后，无期目录不构建步骤区块，也不该有 6 个「运行」。
    page, root = _build_issue()
    assert page._step_run_btns == {}
    assert page._step_run_primary is None
    runs = [ctl for ctl in _of(root, ft.OutlinedButton) if str(ctl.content) == "运行"]
    assert runs == []
    cta = [ctl for ctl in _of(root, ft.FilledButton) if str(ctl.content) == "选择期目录"]
    assert len(cta) == 1


def test_issue_step_run_buttons_disabled_with_reason():
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("infocheck")
    assert set(page._step_run_btns) == {"infocheck", "toc"}
    app.engine_status.ok = False
    page.refresh_running()
    for btn in page._step_run_btns.values():
        assert btn.disabled is True, "引擎未就绪时步骤运行必须禁用"
        assert "引擎未就绪" in (btn.tooltip or ""), "禁用必须说明原因"

    app.engine_status.ok = True
    app.running = True
    page.refresh_running()
    for key, btn in page._step_run_btns.items():
        if key == "infocheck":
            # 运行中的 01 主按钮是停止入口，必须保持可点（标签随状态变「取消」）。
            assert btn.disabled is False
        else:
            assert btn.disabled is True, "运行中不能重复点其他步骤"
            assert "运行中" in (btn.tooltip or "")

    app.running = False
    page.refresh_running()
    # 批 117：目录页缺信息检查报告时主按钮禁用并说明补法（不再只弹 toast）；
    # 手选一份报告后即恢复可点。
    assert page._step_run_btns["infocheck"].disabled is False
    assert page._step_run_btns["toc"].disabled is True
    assert "信息检查报告" in (page._step_run_btns["toc"].tooltip or "")
    page.set_field("toc_report", __file__)
    page.refresh_running()
    for btn in page._step_run_btns.values():
        assert btn.disabled is False and btn.tooltip is None


# ── 2. 主操作唯一性：页头两按钮已删（批 120 B-354），主操作只在详情/空态 ────
def test_issue_header_buttons_removed_and_detail_owns_primary():
    page, root = _build_issue()
    # 批 120 B-354：页头「运行整期…」「从失败处继续…」已删除，运行整期入口只剩
    # 键盘与空态 CTA；页头只在运行中出现「停止」。
    assert not hasattr(page, ("_run_" + "all_btn"))
    assert not hasattr(page, ("_continue_" + "btn"))
    assert not hasattr(page, ("_run_" + "all_secondary"))
    assert not hasattr(page, ("_header_" + "actions"))
    assert page._step_run_primary is None
    assert page._stop_btn.visible is False, "停止只在运行中出现"
    header_filled = _of(root, ft.FilledButton)
    assert not any(str(ctl.content) == "运行整期…" for ctl in header_filled)
    assert not any(str(ctl.content) == "从失败处继续…" for ctl in header_filled)
    # 无期目录时详情是选目录空态（CTA 在 root 里），页头无实心按钮。
    cta = [ctl for ctl in header_filled if str(ctl.content) == "选择期目录"]
    assert len(cta) == 1

    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("toc")
    assert not any(str(ctl.content) == "运行整期…"
                   for ctl in _of(page._content_box, ft.FilledButton))
    assert page._stop_btn.visible is False
    detail_filled = _of(page._content_box, ft.FilledButton)
    # 批 116 决策（I-2 E′）：「生成目录」视图含 01/02 两个区块，逐区块各一个
    # 主按钮（01 运行检查、02 生成目录）；选中步的主按钮仍是 _step_run_primary。
    contents = [str(ctl.content) for ctl in detail_filled]
    assert contents.count("生成目录") == 1 and contents.count("运行检查") == 1, \
        "每个区块的详情头必须有且只有一个主按钮"
    toc_btn = next(ctl for ctl in detail_filled if str(ctl.content) == "生成目录")
    assert toc_btn is page._step_run_primary
    assert set(page._step_run_btns) == {"infocheck", "toc"}
    step_page = page._build_step_page("toc")
    legacy = [ctl for ctl in _of(step_page, ft.OutlinedButton)
              if str(ctl.content) == "运行这一步"]
    assert not legacy, "P1-3 后本步运行只在详情头，参数区旧副按钮应已移除"


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
    assert btn.style.color[state] == t.buttons.danger
    assert btn.style.color[ft.ControlState.DISABLED] == t.buttons.off_fg, \
        "禁用态必须灰字，不能仍亮红"


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
