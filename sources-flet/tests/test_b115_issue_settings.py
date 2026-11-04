"""批 115 · P3-C2 整期页 / 设置页契约（S1m-native：白面板 / 去卡 / 发丝行）。

锁：
1. IssuePage build：`C.page_body(panel=True)` + 日志 dock（贴底浮层 + 底部 spacer
   两档 reserve 40 / 200），总览与步骤页共用同一个 dock。
2. IssuePage 页面级方法不再 `C.card` / `C.section_title` / `C.status_chip`：
   总览步骤行 = 36 高无描边发丝行（序号 / 标题 / 状态点 / 运行按钮）。
3. 总览宽窗 5:6 双列、内容宽 < 980 单列（1000×700 窄窗不压扁）；跨断点 on_resize
   只重建总览一次。
4. 步骤页去重复标题与「← 流水线总览」返回键，保留 `_progress_band()` 与详情头
   的唯一主按钮；进度带仍 54 高、6 段、无 chip。
5. SettingsPage build：`page_body(panel=True)` + 4 组发丝分区（引擎与环境 / 外观 /
   窗口与托盘 / 路径与关于），组内不再有 `C.card` / `C.section_title`；引擎状态
   用 inline_note，键值行统一 `C.kv_row`（32 高）。
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

from app.ui import components as C
from app.ui import pages as P
from test_b112_post_b import _FakeApp, _build_issue, _walk


def _calls(func) -> set[str]:
    """函数体里出现的 `<attr>(...)` 调用属性名集合（注释/字符串不参与）。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}


class _SettingsApp(_FakeApp):
    """够 SettingsPage.build() 用的假壳（不建窗口、不跑引擎）。"""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__()
        self.store.path = tmp_path / "settings.json"
        self.engine_status.problems = []
        self.effective_zoom = 1.0
        self.max_zoom = 1.5
        self.window_w, self.window_h, self.dpi = 1160, 780, 96
        self.app_version = "1.6.0"
        self.flet_version = "1.0.5"
        self.python_version = "3.14.8"
        self.button_palette = "c"
        self.tray_enabled = False
        self.on_theme_switch = lambda e: None

    def save_settings(self, toast: bool = False) -> None:
        pass

    def set_zoom(self, value: float) -> None:
        self.effective_zoom = value

    def set_button_palette(self, value: str) -> None:
        self.button_palette = value

    def set_check_done(self, value: bool) -> None:
        pass

    def refresh_engine_status(self) -> None:
        pass

    def rebuild_shell(self) -> None:
        pass


def _build_settings(tmp_path: Path):
    app = _SettingsApp(tmp_path)
    page = P.SettingsPage(app)
    root = page.build()
    return page, root


# ── 1. IssuePage 白面板 + 日志 dock + spacer ────────────────────────────
def test_issue_page_white_sheet_with_log_dock():
    page, root = _build_issue()
    t = page.t
    assert isinstance(root, ft.Stack)
    assert len(root.controls) == 2
    body, dock = root.controls
    assert dock is page._log.dock_root
    assert page._log.dock is True
    assert page._log.is_expanded is False
    assert isinstance(body, ft.Container)
    assert body.bgcolor == t.palette.surface
    assert isinstance(body.border_radius, ft.BorderRadius)
    assert body.border_radius.top_left == t.radius("panel")
    # 总览与日志 dock 同时挂上；底部 spacer 是最后一项，回调已接。
    # 批 115（D1-115-26）：工作台把详情列包进 shell（左流水线 + 详情）；
    # 内容仍在同一页树里，只是外面多了一层布局容器。
    assert page._content_box in _walk(root)
    last = body.content.controls[-1]
    assert last is page._log_spacer
    assert last.height == t.px(52)
    assert page._log.on_layout_change == page._sync_log_reserve
    # 钉住批 114 路由契约：build 里重建总览。
    src = inspect.getsource(P.IssuePage.build)
    assert "_build_overview()" in src and "_content_box" in src


def test_issue_log_reserve_two_levels_and_drag_follow():
    page, root = _build_issue()
    t = page.t
    spacer = root.controls[0].content.controls[-1]
    page._log.expand()
    assert page._log.is_expanded is True
    # 批 116 P1-2：展开预留 = 46 标题条 + 日志实高；拖动/复位实时跟随。
    before = spacer.height
    page._log._on_resize_update(SimpleNamespace(primary_delta=-t.px(30)))
    assert spacer.height == before + t.px(30), "拖动 log 高度必须实时改页面预留"
    page._log._on_resize_reset()
    assert spacer.height == t.px(54) + page._log._base_height
    page._log.collapse()
    assert spacer.height == t.px(52)


# ── 2. IssuePage 去卡 / 发丝行 ─────────────────────────────────────────
def test_issue_page_methods_have_no_cards_titles_or_chips():
    for func in (P.IssuePage.build, P.IssuePage._build_overview,
                 P.IssuePage._overview_step_row, P.IssuePage._build_step_page,
                 P.IssuePage._progress_band):
        calls = _calls(func)
        assert "card" not in calls, f"{func.__name__} 仍在建页面级 C.card"
        assert "section_title" not in calls, f"{func.__name__} 仍在用 C.section_title"
        assert "status_chip" not in calls, f"{func.__name__} 仍在用 C.status_chip"
    assert "section_label" in _calls(P.IssuePage._build_overview)
    assert "hairline" in _calls(P.IssuePage._build_overview)
    assert "status_dot" in _calls(P.IssuePage._overview_step_row)


def test_issue_overview_step_row_is_36_hairline_row_with_run_button():
    page, _root = _build_issue()
    t = page.t
    row = page._overview_step_row(0, page.STEPS[0])
    assert isinstance(row, ft.Container) and isinstance(row.content, ft.Row)
    assert row.height == t.px(36)
    assert row.border is None  # 无描边卡
    first = row.content.controls[0]
    assert isinstance(first, ft.Container) and first.width == t.px(22)
    assert isinstance(first.content, ft.Text) and first.content.value == "01"
    runs = [ctl for ctl in _walk(row) if isinstance(ctl, ft.OutlinedButton)
            and str(ctl.content) == "运行"]
    assert len(runs) == 1
    dots = [ctl for ctl in _walk(row)
            if isinstance(ctl, ft.Container) and ctl.width == t.px(6)
            and ctl.height == t.px(6)]
    assert dots, "步骤行应保留状态小圆点"


def test_issue_overview_wide_two_columns_narrow_single_column():
    page, _root = _build_issue()
    page.app._design_width = lambda: 1240
    wide = page._build_overview()
    assert isinstance(wide, ft.Row)
    assert page._overview_stacked is False
    left, right = wide.controls
    assert isinstance(left, ft.Container) and left.expand == 5
    assert isinstance(right, ft.Container) and right.expand == 6
    assert right.content.controls == [page._result.root, page._info.root]

    # 回归：`_design_width()` 是整窗宽；1160 内容 940 ≥ 880 → 宽态双列。
    page.app._design_width = lambda: 1160
    assert page._narrow() is False
    assert isinstance(page._build_overview(), ft.Row)
    # 1000 内容 780 < 880 → 单列。
    page.app._design_width = lambda: 1000
    assert page._narrow() is True
    assert isinstance(page._build_overview(), ft.Column)

    page.app._design_width = lambda: 880
    narrow = page._build_overview()
    assert isinstance(narrow, ft.Column)
    assert page._overview_stacked is True
    left2, right2 = narrow.controls  # 右列下移，不再左右挤
    assert isinstance(left2, ft.Column) and isinstance(right2, ft.Column)


def test_issue_on_resize_rebuilds_only_when_crossing_breakpoint():
    page, _root = _build_issue()
    page.selected = "overview"
    page.app._design_width = lambda: 1240
    page._overview_stacked = False
    page.on_resize()  # 未跨断点：保持宽屏双列
    assert isinstance(page._content_box.controls[0], ft.Row)
    page.app._design_width = lambda: 880
    page.on_resize()
    assert page._overview_stacked is True
    assert isinstance(page._content_box.controls[0], ft.Column)
    inner = page._content_box.controls[0]
    assert isinstance(inner.controls[0], ft.Column)  # 单列：左列整宽在上


def test_issue_step_page_has_no_back_button_or_duplicate_title():
    page, _root = _build_issue()
    # 窄窗（1000 内容 780 < 880）详情上方保留 54 高横条；宽窗则由左栏承担导航。
    page.app._design_width = lambda: 1000
    t = page.t
    step = page._build_step_page("toc")
    assert isinstance(step, ft.Column)
    band = step.controls[0]
    assert isinstance(band, ft.Container) and band.height == t.px(54)
    labels = [ctl.value for ctl in _walk(step) if isinstance(ctl, ft.Text)]
    assert not any("流水线总览" in str(v) for v in labels)
    assert "参数" in labels
    # 批 115 P1-3（D1-115-24，覆盖旧 m03093）：本步运行主按钮搬到页头，
    # 参数区不再保留旧描边副本；步骤页不出现两个页面级主色按钮。
    legacy = [ctl for ctl in _walk(step) if isinstance(ctl, ft.OutlinedButton)
              and str(ctl.content) == "运行这一步"]
    assert not legacy, "参数区旧「运行这一步」应已由页头主按钮取代"
    page.activate("toc")
    # 批 115（D1-115-26）：本步主按钮在详情头，页头只留整期运行。
    detail_runs = [ctl for ctl in _walk(page._content_box)
                   if isinstance(ctl, ft.FilledButton)]
    # 批 116 决策（I-2 E′）：toc 所在「生成目录」视图同时承载 01/02 两个区块，
    # 每个区块各有自己的主按钮；本步（02）的主按钮仍是 _step_run_primary。
    contents = [str(ctl.content) for ctl in detail_runs]
    assert "生成目录" in contents and "运行检查" in contents
    toc_btn = next(ctl for ctl in detail_runs if str(ctl.content) == "生成目录")
    assert toc_btn is page._step_run_primary
    src = inspect.getsource(P.IssuePage._build_step_page)
    assert "_progress_band()" in src and "_run_step" not in src


def test_issue_progress_band_keeps_height_and_uses_bars_not_chips():
    page, _root = _build_issue()
    t = page.t
    band = page._progress_band()
    assert band.height == t.px(54)
    # 批 116 决策（I-2 E′）：横条 = 4 个视图（生成目录/编译并合并/清理/推送）。
    assert len(band.content.controls) == len(page.VIEWS) == 4
    bars = [ctl for ctl in _walk(band)
            if isinstance(ctl, ft.Container) and ctl.height == t.px(2)]
    assert len(bars) == 4
    assert "status_chip" not in _calls(P.IssuePage._progress_band)


# ── 3. SettingsPage 白面板 / 四组 / kv_row ─────────────────────────────
def test_settings_page_white_sheet_and_four_groups(tmp_path):
    page, root = _build_settings(tmp_path)
    t = page.t
    assert isinstance(root, ft.Container)
    assert root.bgcolor == t.palette.surface
    assert root.border_radius.top_left == t.radius("panel")
    body = root.content
    assert isinstance(body, ft.Column)
    groups = body.controls[1:]
    assert len(groups) == 4
    labels = [group.controls[0].controls[0].controls[0].value for group in groups]
    assert labels == ["引擎与环境", "外观", "窗口与托盘", "路径与关于"]
    # 每组都以 section_label + 发丝线开头。
    for group in groups:
        head = group.controls[0]
        assert isinstance(head, ft.Column)
        assert isinstance(head.controls[1], ft.Container)
        assert head.controls[1].height == 1  # hairline
    # 「窗口与托盘」只有一张同名的 shell 卡：标题合并到组头（note 上提），
    # 组内不得再出现第二层「窗口与托盘」小标题。
    tray_group = groups[2]
    tray_head = tray_group.controls[0].controls[0]
    assert tray_head.controls[0].value == "窗口与托盘"
    assert tray_head.controls[-1].value == "仅 Windows"
    tray_body = tray_group.controls[1]
    assert isinstance(tray_body, ft.Column)
    first = tray_body.controls[0]
    assert not (isinstance(first, ft.Row) and isinstance(first.controls[0], ft.Text)
                and first.controls[0].value == "窗口与托盘")


def test_settings_engine_rows_merge_envcheck_and_use_kv_row(tmp_path):
    page, root = _build_settings(tmp_path)
    t = page.t
    body = root.content
    engine_group = body.controls[1]
    note = engine_group.controls[1]
    assert isinstance(note, ft.Row)  # inline_note：状态点 + 状态/版本
    note_text = [str(ctl.value) for ctl in note.controls if isinstance(ctl, ft.Text)]
    assert any("引擎" in value and ("就绪" in value or "未就绪" in value)
               for value in note_text)
    # 环境检查并入组首：去壳后是 Column 且首行是 section_label。
    envcheck = engine_group.controls[2]
    assert isinstance(envcheck, ft.Column)
    assert isinstance(envcheck.controls[0], ft.Row)
    assert envcheck.controls[0].controls[0].value == "环境检查"
    saved_rows = [ctl for ctl in _walk(engine_group)
                  if isinstance(ctl, ft.Container) and ctl.height == t.row_h("table")
                  and isinstance(ctl.content, ft.Row) and len(ctl.content.controls) == 3]
    assert len(saved_rows) >= 5  # 运行方式/目录/版本/问题/设置文件


def test_settings_page_methods_have_no_cards_or_section_titles(tmp_path):
    calls = _calls(P.SettingsPage.build)
    assert "card" not in calls
    assert "section_title" not in calls
    assert "section_label" in calls
    assert "hairline" in calls
    assert "kv_row" in calls
    assert "inline_note" in calls
    assert "_flat" in calls  # settings_shell / ai_panel 的卡片统一去壳
    assert "button_palette_row" in calls
    src = inspect.getsource(P.SettingsPage.build)
    assert "panel=True" in src
    assert src.count("self._group(") == 4


def test_settings_body_has_no_card_containers_left(tmp_path):
    page, root = _build_settings(tmp_path)
    t = page.t
    leftovers = []
    for ctl in _walk(root):
        if not (isinstance(ctl, ft.Container) and ctl.border is not None
                and ctl.border_radius == t.radius("md")
                and isinstance(ctl.content, ft.Column) and ctl.content.controls):
            continue
        first = ctl.content.controls[0]
        if (isinstance(first, ft.Row) and first.controls
                and isinstance(first.controls[0], ft.Text)
                and first.controls[0].size == t.font("section")):
            leftovers.append(first.controls[0].value)
    assert not leftovers, f"设置页仍有「卡片 + section_title」：{leftovers}"


def test_settings_flat_unwraps_shell_card_in_place(tmp_path):
    page, _root = _build_settings(tmp_path)
    from app.ui import settings_shell as SHELL
    card = SHELL.tray_card(page)
    flat = page._flat(card, "窗口与托盘")
    assert isinstance(flat, ft.Column)
    first = flat.controls[0]
    assert isinstance(first, ft.Row)
    label = first.controls[0]
    assert isinstance(label, ft.Text) and label.value == "窗口与托盘"
    assert label.size == page.t.font("section-label")
    assert label.color == page.t.palette.text_muted
    assert len(flat.controls) == len(card.content.controls)  # 其余控件原样保留
    assert flat.controls[1] is card.content.controls[1]


def test_settings_unknown_card_is_returned_untouched(tmp_path):
    page, _root = _build_settings(tmp_path)
    card = C.card(ft.Column([C.section_title("别的卡", page.t)]), page.t)
    flat = page._flat(card, "窗口与托盘")
    assert isinstance(flat, ft.Column)
    assert flat.controls[0] is card.content.controls[0]
