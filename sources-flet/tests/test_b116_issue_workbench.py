"""批 115：整期装配工作台壳（D1-115-26/28）静态 + 运行期契约。"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import flet as ft

from ui import assembly as ASM
from ui import pages as P
from test_b112_post_b import _FakeApp, _build_issue, _walk


def _src() -> str:
    return inspect.getsource(P.IssuePage)


# ── 1. 壳布局：页头 / 上下文条 / 左栏或横条 / 详情 / 日志 dock ──────────
def test_workbench_shell_regions_and_no_card_glue():
    page, root = _build_issue()
    assert page._shell is not None and page._rail_box is not None
    assert page._detail_box is not None
    assert root is not None and page._log.dock is True
    assert page._log.is_expanded is False
    assert page._log_spacer.height == page.t.px(40)
    src = _src()
    for banned in ("C.card(", "C.section_title(", "C.status_chip("):
        assert banned not in src, f"工作台主体不许再出现 {banned}"
    assert "运行整期" in src and "issue_state" in src


def test_narrow_content_gets_horizontal_strip():
    page, _root = _build_issue()
    page.app._design_width = lambda: 1000  # 内容 780 < 880
    page._refresh_layout()
    # D1-115-26：窄窗横条只出现一次，由详情页顶部自带（外壳不再重复）。
    assert page._shell.controls == [page._detail_box]
    assert page._progress_band().height == page.t.px(54)
    page.app._design_width = lambda: 1160  # 内容 940 ≥ 880
    page._refresh_layout()
    row = page._shell.controls[0]
    assert isinstance(row, ft.Row) and len(row.controls) == 2
    assert row.controls[0].width == page.t.px(216)
    assert row.controls[1] is page._detail_box
    texts = [ctl.value for ctl in _walk(page._rail_box)
             if isinstance(ctl, ft.Text)]
    assert "生产 4 步" in texts and "收尾（可选）" in texts


# ── 2. 运行整期对话框：默认 1–4、05 danger 行、跳过已完成 ──────────────
def test_runall_dialog_defaults_and_danger_row():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    shown: list = []
    page.app.page.show_dialog = lambda dialog: shown.append(dialog)
    page._open_runall()
    assert len(shown) == 1
    dialog = shown[0]
    assert dialog.modal is True
    boxes = [ctl for ctl in _walk(dialog.content) if isinstance(ctl, ft.Checkbox)]
    assert len(boxes) == 7, "01–06 + 跳过已完成"
    assert [box.value for box in boxes[:4]] == [True] * 4
    assert boxes[4].value is False and boxes[5].value is False
    assert boxes[6].value is True, "跳过已完成默认勾选"
    assert page._runall_danger.visible is False
    boxes[4].value = True
    boxes[4].on_change(SimpleNamespace(control=boxes[4]))
    assert page._runall_danger.visible is True, "勾 05 必须出现真实删除 danger 行"


def test_runall_disabled_without_issue_dir_and_tooltip():
    page, _root = _build_issue()
    page._sync_run_actions()
    assert page._run_all_btn.disabled is True
    assert page._run_all_btn.tooltip == "先选择期目录"
    assert page._header_actions.controls == [page._run_all_btn]


# ── 3. 运行中页头改「停止」；失败后出「从失败处继续…」─────────────────
def test_header_actions_running_and_failed_states():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.app.running = True
    page._sync_run_actions()
    assert page._header_actions.controls == [page._stop_btn]
    page.app.running = False
    page._status["compileall"] = ASM.StepStatus("error", exit_code=2)
    page._sync_run_actions()
    assert page._header_actions.controls == [page._continue_btn, page._run_all_btn]


# ── 4. 键盘：⌘1–6 / ↑↓ / Esc ──────────────────────────────────────────
def test_keyboard_digit_jumps_and_escape_stops():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("infocheck")
    page._on_key(SimpleNamespace(key="3", ctrl=True, meta=False))
    assert page.selected == "compileall"
    page._on_key(SimpleNamespace(key="ArrowDown", ctrl=False, meta=False))
    assert page.selected == "merge"

    shown: list = []
    page.app.page.show_dialog = lambda dialog: shown.append(dialog)
    page.app.running = True
    page._on_key(SimpleNamespace(key="Escape", ctrl=False, meta=False))
    assert shown and "停止当前步骤" in str(shown[0].title.value)


# ── 5. 状态持久化：issue_state schema 1 + 落地 last_step/statuses ──────
def test_issue_state_persisted_and_restored():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.selected = "toc"
    page._status["toc"] = ASM.StepStatus("done", exit_code=0, duration_ms=1200,
                                         artifact="/tmp/报告.txt")
    page._status["compileall"] = ASM.StepStatus("error", exit_code=2,
                                                error="引擎失败")
    page._persist_issue_state()
    data = page.app.store.get("issue_state")
    entry = data["/tmp/期目录"]
    assert entry["schema"] == 1
    assert entry["last_step"] == "toc"
    assert entry["statuses"]["toc"]["state"] == "done"
    assert entry["statuses"]["compileall"]["exit_code"] == 2

    page2 = P.IssuePage(page.app)  # 同一 store，模拟切换页面后重进
    page2.build()
    page2.set_field("issue_dir", "/tmp/期目录")
    page2._restore_issue_state()
    assert page2._state_of("toc") == "done"
    assert page2._state_of("compileall") == "error"
    assert page2.selected == "toc"


def test_landing_step_prefers_last_then_first_unfinished():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/新期")
    page._status["infocheck"] = ASM.StepStatus("done")
    assert page._default_view() == "toc"  # 第一个未完成
    page.app.store.set("issue_state", {"/tmp/新期": {"schema": 1, "last_step": "merge",
                                                    "statuses": {}}})
    assert page._default_view() == "merge"


# ── 6. 日志：不清空 / 段头切分 / 本步过滤 / 2000 行上限 ────────────────
def test_log_keeps_history_and_filters_by_step():
    page, _root = _build_issue()
    page._log_filter = "step"
    page.selected = "toc"
    page._on_line("▶ 01/06 检查 tex 信息 · infocheck · 10:00:00")
    page._on_line("扫描中 1/2")
    shown = len(page._log._lines)
    page._on_line("▶ 02/06 生成目录 · toc · 10:00:03")
    page._on_line("目录生成中")
    assert len(page._log._lines) == shown + 2, "切段后本步日志继续累积，不 per-step 清空"
    page._on_line("▶ 03/06 编译全部 · compileall · 10:00:05")
    before = len(page._log._lines)
    page._on_line("xelatex 第一篇")  # 当前步是 toc → 本步过滤隐藏
    assert len(page._log._lines) == before
    page._log_filter = "all"
    page._on_line("整期可见")
    assert len(page._log._lines) == before + 1


def test_log_buffer_capped_at_2000():
    page, _root = _build_issue()
    page._log_buffer = ["旧"] * 1999
    page._on_line("新")
    assert len(page._log_buffer) == 2000
    page._on_line("再新")
    assert len(page._log_buffer) == 2000
    assert page._log_buffer[-1] == "再新"


# ── 11. 六步详情都能真实构建（GUI 冒烟：曾因 ft.border / 字体角色错整页回退） ──
def test_all_six_detail_pages_build_without_error():
    page, _root = _build_issue()
    page.app._design_width = lambda: 940    # 窄窗分支也要走一遍
    page.set_field("issue_dir", "/tmp/期目录")
    for step in P.IssuePage.STEPS:
        key = step["key"]
        page.activate(key)
        assert page.selected == key
        # 详情头标题必须与步骤一致，且唯一主按钮在详情头内。
        detail = page._content_box.controls[0]
        assert detail is not None
        assert page._detail_primary is not None
        assert str(page._detail_primary.content) == page._primary_label(key)
