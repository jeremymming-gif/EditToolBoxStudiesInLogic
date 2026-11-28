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
    assert page._log_spacer.height == page.t.px(52)
    src = _src()
    for banned in ("C.card(", "C.section_title(", "C.status_chip("):
        assert banned not in src, f"工作台主体不许再出现 {banned}"
    assert "运行整期" in src and "issue_state" in src


def test_narrow_content_nav_wraps_two_rows():
    page, _root = _build_issue()
    page.app._design_width = lambda: 1000  # <1100 → 两行
    page._refresh_layout()
    # 批 117：左栏/横条已删除，外壳恒为「导航条 + 正文」。
    assert page._shell.controls == [page._nav_box, page._content_box]
    assert page._nav_box.content.height == page.t.px(72)
    page.app._design_width = lambda: 1160  # ≥1100 → 单行
    page._refresh_layout()
    assert page._nav_box.content.height == page.t.px(36)
    texts = [ctl.value for ctl in _walk(page._nav_box)
             if isinstance(ctl, ft.Text)]
    assert "正文流水线" not in texts and "收尾（可选）" not in texts
    assert all(view["title"] in texts for view in page.VIEWS)


# ── 2. D3：整期串跑入口已下线（只保留单步 + 「刷新页码并编译」）────────
def test_runall_dialog_is_gone_after_d3():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    # D3：GUI 不再有「运行整期」对话框——批量串跑由 CLI/引擎侧负责，
    # 界面单动作只留「刷新页码并编译」（refresh 页签的预览+确认）。
    assert not hasattr(page, "_open_runall")
    assert not hasattr(page, "_run_all")
    assert not hasattr(page, "_runall_danger")
    assert not hasattr(page, "_plan_prefixes")
    assert not hasattr(page, "_done_prefixes")


def test_runall_without_issue_dir_has_no_header_buttons():
    page, _root = _build_issue()
    page._sync_stop_action()
    assert page._stop_btn.visible is False
    assert not hasattr(page, ("_run_" + "all_btn"))
    assert not hasattr(page, ("_header_" + "actions"))
    # 无期目录时页头无整期入口；运行整期仍可从键盘/空态 CTA 打开。
    assert not page.field("issue_dir")


# ── 3. 运行中页头只显示「停止」；失败后不再有「从失败处继续…」──────────
def test_header_stop_visible_only_when_running():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.app.running = True
    page._sync_stop_action()
    assert page._stop_btn.visible is True
    page.app.running = False
    page._status["refresh"] = ASM.StepStatus("error", exit_code=2)
    page._sync_stop_action()
    assert page._stop_btn.visible is False
    assert not hasattr(page, ("_continue_" + "btn"))
    assert not hasattr(page, ("_run_" + "all_btn"))


# ── 4. 键盘：⌘1–6 / ↑↓ / Esc ──────────────────────────────────────────
def test_keyboard_digit_jumps_and_escape_stops():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("infocheck")
    # D7：⌘1–6 = 六页签（catalog/order/refresh/merge/clean/push），⌘3 落「刷新页码」。
    page._on_key(SimpleNamespace(key="3", ctrl=True, meta=False))
    assert page.selected == "refresh"
    page.activate("infocheck")
    page._on_key(SimpleNamespace(key="ArrowDown", ctrl=False, meta=False))
    assert page.selected == "toc"

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
    page._status["refresh"] = ASM.StepStatus("error", exit_code=2,
                                             error="引擎失败")
    page._persist_issue_state()
    data = page.app.store.get("issue_state")
    entry = data["/tmp/期目录"]
    assert entry["schema"] == 1
    assert entry["last_step"] == "toc"
    assert entry["statuses"]["toc"]["state"] == "done"
    assert entry["statuses"]["refresh"]["exit_code"] == 2

    page2 = P.IssuePage(page.app)  # 同一 store，模拟切换页面后重进
    page2.build()
    page2.set_field("issue_dir", "/tmp/期目录")
    page2._restore_issue_state()
    assert page2._state_of("toc") == "done"
    assert page2._state_of("refresh") == "error"
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
    page._on_line("▶ 03/07 排序与改名 · order · 10:00:05")
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


# ── B-344：期目录全文 tooltip 与编辑回调契约 ─────────────────────────────
def test_issue_dir_tooltip_tracks_typing_blur_and_picker(monkeypatch):
    page, _root = _build_issue()
    field = page.fields["issue_dir"][0]
    assert field._value_tooltip is True
    assert field.tooltip is None

    field.value = "/tmp/期目录/" + ("很长的路径/" * 12)
    field.on_change(SimpleNamespace(control=field))
    assert field.tooltip == field.value
    assert page.app.store.recent.get("issue_dir") == field.value
    assert ("path_change", {"page": "issue", "field": "issue_dir", "value": field.value}) in page.app.marked

    # 模拟文件选择器直接赋值（不触发 on_change），watcher 负责同步全文提示。
    picked = "/tmp/选择器回填/" + ("更长的路径/" * 10)
    field.value = picked
    async def _no_wait(_seconds):
        return None
    monkeypatch.setattr(P.asyncio, "sleep", _no_wait)
    import asyncio
    asyncio.run(page._watch_issue_pick(page.fields["issue_dir"], "/tmp/旧值"))
    assert field.tooltip == picked
    assert page.app.store.recent.get("issue_dir") == picked


def test_issue_dir_tooltip_initial_value_and_blur_commit_mark():
    page, _root = _build_issue()
    path = "/tmp/期目录/" + ("完整路径段/" * 8)
    page.app.store.set("recent.issue_dir", path)
    page2 = P.IssuePage(page.app)
    page2.build()
    field = page2.fields["issue_dir"][0]
    assert field._value_tooltip is True
    assert field.tooltip == path

    updated = path + "/下一段"
    field.value = updated
    field.on_change(SimpleNamespace(control=field))
    field.on_blur(SimpleNamespace(control=field))
    assert field.tooltip == updated
    assert page.app.store.recent.get("issue_dir") == updated
    assert ("issue_changed", {"page": "issue", "issue": updated}) in page.app.marked
    assert updated in page.app.store.get("issue_state")


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
