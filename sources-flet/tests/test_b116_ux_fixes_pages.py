"""批 116 UX FIX-PAGES 契约：断点 / 日志避让 / 清理确认 / 单篇选项 / 文案。

只做静态与运行期结构断言，不启动真 GUI。
"""
from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from ui import assembly as ASM
from ui import checks as CK
from ui import pages as P
from ui.components import danger_button

from test_b112_post_b import _FakeApp, _build_issue, _build_single, _walk


def _text(value) -> str:
    inner = getattr(value, "content", value)
    return str(getattr(inner, "value", inner))


def _dialog_controls(dialog) -> list:
    return [*(dialog.actions or []), dialog.content]


def _texts(control) -> list[str]:
    return [str(c.value) for c in _walk(control) if isinstance(c, ft.Text)]


# ── 1. Issue 宽态断点：1160 出 rail，1000 走横条 ──────────────────────
def test_issue_content_breakpoint_is_880():
    page, _root = _build_issue()
    page.app._design_width = lambda: 1160  # 内容 1160 - 220 = 940
    assert page._narrow() is False
    page.app._design_width = lambda: 1000  # 内容 780
    assert page._narrow() is True
    page.app._design_width = lambda: 1100  # 内容 880，边界仍宽态
    assert page._narrow() is False
    page.app._design_width = lambda: 1099
    assert page._narrow() is True


def test_issue_nav_chips_at_1160_without_group_headers():
    page, _root = _build_issue()
    page.app._design_width = lambda: 1160
    page._refresh_layout()
    nav_texts = _texts(page._nav_box)
    # 批 117：216 左栏与分组标题删除，改成页内 4 chip（1160 单行 / 1000 两行）。
    assert "正文流水线" not in nav_texts
    assert "收尾（可选）" not in nav_texts
    assert all(view["title"] in nav_texts for view in page.VIEWS)
    assert page._nav_box.content.height == page.t.px(36)

    page.app._design_width = lambda: 1000
    page._refresh_layout()
    assert page._shell.controls == [page._nav_box, page._content_box]
    assert page._nav_box.content.height == page.t.px(72)


# ── 2. 日志 dock 避让：两档 + 拖拽跟随 ────────────────────────────────
def test_issue_log_reserve_follows_dock_and_drag():
    page, root = _build_issue()
    t = page.t
    spacer = root.controls[0].content.controls[-1]
    assert spacer.height == t.px(52)
    page._log.expand()
    assert spacer.height == t.px(54) + page._log._log_height
    before = spacer.height
    page._log._on_resize_update(SimpleNamespace(primary_delta=-t.px(30)))
    assert spacer.height == before + t.px(30)
    page._log.collapse()
    assert spacer.height == t.px(52)


# ── 3. 清理确认：完整路径 + 灰底灰字禁用 + 520 宽 ─────────────────────
def test_cleanup_single_entry_has_no_confirm_ui():
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    page.set_field("issue_dir", "/tmp/期目录")
    app.store.set("form.cleantemp_mode", "workspace")
    page._ct_preview = {"ok": True, "issue": "/tmp/期目录", "mode": "aux",
                        "count": 18, "ts": "2026-11-04 11:04:00"}
    shown: list = []
    app.page.show_dialog = lambda d: shown.append(d)
    assert page._primary_label("cleantemp") == "清理临时文件"
    page._primary_action("cleantemp")
    assert shown == [], "不再有 520 危险确认框/输入「清理」"
    assert page._cleanup.phase == "scanning"

    def _all_texts(controls):
        out: list[str] = []
        for item in controls:
            for ctl in [item, *_walk(item)]:
                if isinstance(ctl, ft.Text):
                    out.append(str(ctl.value))
        return out

    texts = _all_texts(page._cleanup.params())
    assert any("自动校验 + 回收" in value for value in texts), texts
    assert any("不再影响本入口" in value for value in texts), texts


def test_danger_button_disabled_style_is_gray_not_red():
    t = _FakeApp().t
    btn = danger_button("危险", lambda e: None, t, disabled=True)
    assert btn.style.color[ft.ControlState.DISABLED] == t.buttons.off_fg
    assert btn.style.bgcolor[ft.ControlState.DISABLED] == t.buttons.off_bg
    assert btn.style.color[ft.ControlState.DEFAULT] == t.buttons.danger


# ── 4. 单篇处理选项：1000 保持 2 列（5 张卡）──────────────────────────
def test_single_options_two_columns_at_1000():
    page, root = _build_single()
    page.app._design_width = lambda: 1000
    grid = page._option_tiles({})
    assert isinstance(grid, ft.Column)
    assert [len(row.controls) for row in grid.controls] == [2, 2, 2]
    assert sum(1 for c in _walk(grid) if isinstance(c, ft.Checkbox)) == 5

    page.app._design_width = lambda: 899
    narrow = page._option_tiles({})
    assert isinstance(narrow, ft.Column)
    assert all(len(row.controls) == 1 for row in narrow.controls)
    assert len(narrow.controls) == 5


# ── 5. 文案 / 页头 / 只读步 / 空态 CTA / 运行整期 ─────────────────────
def test_single_proofcheck_cta_says_readback_check():
    page, _root = _build_single()
    assert _text(page._norm_jump_btn) == "去引用库 → 回读校验"


def test_issue_header_keeps_fixed_product_line():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("toc")
    assert page._flow_desc.value == "一期稿件，从检查到合刊。"
    assert any("取最新信息检查报告" in value
               for value in _texts(page._content_box)), "步骤 desc 只留在详情"


def test_infocheck_readonly_hides_save_note():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page.activate("infocheck")
    labels = _texts(page._content_box)
    assert "参数" in labels
    # 批 116 决策（I-2 E′）：01 与 02 同屏两个区块，01 只读、02 才写参数；
    # 「改动即时保存」只属于 02 区块，断言按区块范围收窄。
    info_labels = _texts(page._section_refs["infocheck"])
    assert "改动即时保存" not in info_labels
    assert "改动即时保存" in _texts(page._section_refs["toc"])
    page.activate("toc")
    assert "改动即时保存" in _texts(page._content_box)


def test_issue_empty_info_without_issue_dir_offers_picker():
    app = _FakeApp()
    page = P.IssuePage(app)
    page.build()
    assert not page.field("issue_dir")
    calls: list = []
    app.browse_field = lambda *args, **kwargs: calls.append((args, kwargs))
    result = page._info_result()
    labels = _texts(result)
    assert "还没有选择期目录" in labels
    btn = [c for c in _walk(result) if isinstance(c, ft.FilledButton)
           and "选择期目录" in _text(c)][0]
    btn.on_click(None)
    assert calls and calls[0][0][2] == "dir"
    assert calls[0][0][3] == "issue_dir"


def test_issue_info_zero_article_result_keeps_report_entry():
    """空报告修复 A1：0 篇完成态要给出报告入口与判据，不再显示「还没检查结果」。"""
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page._info_payload = {"articles": []}
    page._info_articles = []
    page._info_state = "empty"
    page._info_report = "/tmp/期目录/信息检查2026_11_15.txt"

    result = page._info_result()
    labels = _texts(result)

    assert any("检查完成 · 0 篇" in value for value in labels), labels
    assert any("报告已生成并保留" in value for value in labels), labels
    assert not any("还没有检查结果" in value for value in labels), "0 篇完成不得退回未运行"
    assert any("2026_11_15" in value for value in labels), "必须显示报告路径"
    opened = [c for c in _walk(result) if isinstance(c, ft.OutlinedButton)
              and "打开报告" in _text(c)]
    assert opened and opened[0].disabled is False


def test_issue_info_failed_running_unreadable_states_override_stale_result():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    page._info_payload = {"articles": []}  # 旧完成态不得盖住失败/运行说明
    page._info_state = "failed"
    page._info_code = 2
    labels = _texts(page._info_result())
    assert any("本次检查未完成（退出码 2）" in value for value in labels), labels
    assert not any("检查完成 · 0 篇" in value for value in labels)

    page._info_state = "running"
    labels = _texts(page._info_result())
    assert any("正在检查…" in value for value in labels), labels

    page._info_state = "unreadable"
    page._info_report = "/tmp/期目录/信息检查.txt"
    result = page._info_result()
    labels = _texts(result)
    assert any(CK.UNREADABLE_INFO in value for value in labels), labels
    assert any("打开报告" in _text(c) for c in _walk(result)
               if isinstance(c, ft.OutlinedButton))


def test_issue_switch_clears_info_completion_state():
    """A1：换期不得沿用旧完成态（含 0 篇完成、失败码与报告路径）。"""
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/旧期")
    page._info_payload = {"articles": []}
    page._info_articles = []
    page._info_notes = ["旧项"]
    page._info_state = "empty"
    page._info_code = 2
    page._info_report = "/tmp/旧期/信息检查.txt"

    page._reset_issue_runtime()

    assert page._info_state == "idle" and page._info_code is None
    assert page._info_payload is None and page._info_articles == []
    assert page._info_report == "" and page._info_notes == []
    assert page._info.state == "idle", "卡片自身也要回到未运行"


def test_runall_dialog_tight_and_no_unchecked_05_warning():
    page, _root = _build_issue()
    page.set_field("issue_dir", "/tmp/期目录")
    shown: list = []
    page.app.page.show_dialog = lambda dialog: shown.append(dialog)
    page._open_runall()
    assert shown
    dialog = shown[0]
    assert dialog.content.width == page.t.px(600)
    assert dialog.content.height == page.t.px(470)
    assert page._runall_danger.visible is False
    labels = [str(c.value) for ctl in _dialog_controls(dialog)
              for c in _walk(ctl) if isinstance(c, ft.Text)]
    assert not any("05 默认只预演" in value for value in labels)

    boxes = [c for ctl in _dialog_controls(dialog) for c in _walk(ctl)
             if isinstance(c, ft.Checkbox)]
    boxes[4].value = True
    boxes[4].on_change(SimpleNamespace(control=boxes[4]))
    assert page._runall_danger.visible is True
