"""批 114 用户 m03074：高级参数清空能力 + 运行日志多行选择/复制契约。

锁六组契约（全部离线，不建窗口、不跑引擎、不联网）：

1. 高级参数 12 个文本字段都挂 suffix 紧凑清除按钮（空值时隐藏，有值才出现）。
2. 单项清除同步控件值 / error / store，并 mark `advanced_field_cleared`。
3. 「清空高级参数」一键清空 14 项（12 文本 + 语种/责编回默认），处理选项/元数据不动。
4. 语种/责编下拉带重置图标，且与下拉同一 `C.field_row` 内等高（48）。
5. LogConsole 正文包在 `ft.SelectionArea`（跨行拖选），工具栏有「复制」按钮，
   复制内容尊重「只看摘要」过滤。
6. 静态防回退：suffix 清除 / 清空全部 / SelectionArea / 复制入口不得被改回。
"""
from __future__ import annotations

import asyncio
import pathlib

import flet as ft

import main as M
from ui import components as C
from ui import pages as P
from ui.tokens import Tokens

from test_b114_polish import _FakeApp, _build_single, _button_contents, _walk


APP_UI = pathlib.Path(__file__).resolve().parents[1] / "app" / "ui"

TEXT_KEYS = (
    "convert_vol", "convert_issue", "convert_year", "convert_pages",
    "convert_author", "convert_name", "convert_received", "convert_grants",
    "convert_bib", "convert_refs", "convert_sty", "convert_skeleton",
)


# ── 1. suffix 清除按钮存在且空值隐藏 ─────────────────────────────────────
def test_every_advanced_text_field_has_clear_suffix():
    page, _root = _build_single()
    assert P._ADVANCED_TEXT_KEYS == TEXT_KEYS
    for key in TEXT_KEYS:
        field = page.fields[key][0]
        suffix = getattr(field, "suffix", None)
        assert isinstance(suffix, ft.IconButton), f"{key} 缺 suffix 清除按钮"
        assert suffix.icon == C.ic("CLOSE")
        assert suffix.icon_size <= page.t.px(16)
        assert suffix.visible is False, f"{key} 空值时清除按钮应隐藏"
        # 输入框高度仍由浮动字段固定 48，suffix 不改变盒型
        assert field.height == page.t.px(48)


def test_clear_single_advanced_field_syncs_store_error_and_mark():
    page, _root = _build_single()
    field = page.fields["convert_vol"][0]
    field.value = "21"
    C.sync_clearable(field)
    assert field.suffix.visible is True
    field.error = "格式不对"
    field.suffix.on_click(None)          # m03131：✕ 已由 helper 默认挂载并接管清空
    assert field.value == ""
    assert field.error is None
    assert field.suffix.visible is False
    assert page.app.store.get("form.convert_vol") == ""
    assert ("advanced_field_cleared", {"field": "convert_vol"}) in page.app.marked
    # 有值 → 输入时 ✕ 自动出现
    field.value = "3"
    C.sync_clearable(field)
    assert field.suffix.visible is True


# ── 2. 一键清空 14 项 ────────────────────────────────────────────────────
def test_clear_all_advanced_params_resets_store_fields_and_selects():
    app = _FakeApp()
    app.store.data["form"] = {key: "旧值" for key in TEXT_KEYS}
    app.store.data["form"]["convert_lang"] = "zh"
    app.store.data["form"]["convert_editor"] = "旧责编"
    page = P.SinglePage(app)
    root = page.build()
    assert "清空高级参数" in _button_contents(root)

    page._clear_advanced_params()

    for key in TEXT_KEYS + ("convert_lang", "convert_editor"):
        assert app.store.get(f"form.{key}") == "", f"{key} 未清空"
    for key in TEXT_KEYS:
        field = page.fields[key][0]
        assert field.value == ""
        assert field.suffix.visible is False
    assert page._lang_box._value_text.value == "自动判断"
    assert page._editor_box._value_text.value == "未指定"
    assert ("advanced_params_cleared", {"count": 14}) in app.marked
    assert any("已清空高级参数" in message for message in app.toasts)
    # 处理选项 / 元数据不受影响：清空后仍在页面里
    assert any("编译 PDF" in value for value in
               [str(getattr(c, "value", "")) for c in _walk(root)])


# ── 3. 下拉重置（m03131 已下沉到 select_box(reset=True)）────────────────
def test_select_reset_icons_are_in_equal_height_field_rows():
    page, _root = _build_single()
    boxes = [getattr(page, name) for name in ("_lang_box", "_editor_box")]
    labels = [getattr(box, "_form_label", None) for box in boxes]
    assert labels == ["语种", "责编"]
    for box in boxes:
        reset = getattr(box, "_reset_button", None)
        assert isinstance(reset, ft.IconButton)
        assert reset.tooltip in ("重置为「自动判断」", "重置为「未指定」")
        assert box.content.height == page.t.px(48)   # 盒高 48，↻ 在框内不改变盒型
        assert box.height == page.t.px(48)

    # 默认值：语种「自动判断」/ 责编「未指定」；有非默认值才显示 ↻
    lang_reset = getattr(page._lang_box, "_reset_button")
    assert lang_reset.visible is False
    page._lang_box._value_text.value = "中文（SLCN-MIX.sty）"
    page._lang_box._sync_reset()
    assert lang_reset.visible is True

    page._lang_box._reset_button.on_click(None)     # 点击 ↻ 的真实路径
    assert page.app.store.get("form.convert_lang") == ""
    assert page._lang_box._value_text.value == "自动判断"
    page._editor_box._reset_button.on_click(None)
    assert page.app.store.get("form.convert_editor") == ""
    assert page._editor_box._value_text.value == "未指定"
    events = [event for event, _fields in page.app.marked]
    assert events.count("advanced_field_reset") == 2


# ── 4. LogConsole：SelectionArea + 复制 ─────────────────────────────────
def test_log_console_wraps_list_in_selection_area_and_has_copy_button():
    t = Tokens("light", 1.0, 96)
    console = C.LogConsole(t, height=150)
    box = console._log_box.content
    assert isinstance(box, ft.Column)
    area = box.controls[-1]
    assert isinstance(area, ft.SelectionArea)
    assert area.content is console.list
    labels = [str(getattr(btn, "content", ""))
              for btn in _walk(console.root) if isinstance(btn, ft.TextButton)]
    assert "复制" in labels
    assert "清空" in labels
    assert isinstance(console._copy_btn, ft.TextButton)


def test_log_console_copy_respects_fold_filter_and_reports_result():
    t = Tokens("light", 1.0, 96)
    toasts: list[str] = []
    marks: list[tuple] = []
    console = C.LogConsole(t, toast=toasts.append,
                           mark=lambda event, **fields: marks.append((event, fields)))
    console.add_line("line-1", "info", True)
    console.add_line("raw-2", "dim", False)
    assert console._display_text() == "line-1\nraw-2"
    console._fold_only = True
    assert console._display_text() == "line-1"
    console._fold_only = False

    asyncio.run(console._on_copy(None))
    assert marks and marks[-1][0] == "log_copied"
    assert marks[-1][1]["lines"] == 2
    assert marks[-1][1]["ok"] is False  # 未挂载 → 无客户端剪贴板，诚实报失败
    assert toasts and ("复制" in toasts[-1] or "日志为空" in toasts[-1])


def test_all_log_console_call_sites_pass_toast_and_mark():
    for name in ("pages.py", "bib_advanced.py", "normalize.py"):
        source = (APP_UI / name).read_text(encoding="utf-8")
        for chunk in source.split("C.LogConsole(")[1:]:
            head = chunk.split(")", 1)[0]
            assert "toast=" in head and "mark=" in head, f"{name} 的 LogConsole 缺 toast/mark"


# ── 5. 静态防回退 ───────────────────────────────────────────────────────
def test_static_contracts_for_clear_and_selection():
    pages_src = (APP_UI / "pages.py").read_text(encoding="utf-8")
    components_src = (APP_UI / "components.py").read_text(encoding="utf-8")
    bib_src = (APP_UI / "bib_advanced.py").read_text(encoding="utf-8")
    # 高级参数：✕ 已下沉 helper（m03131），页面不再手工挂 suffix；一键清空保留
    assert "_ADVANCED_TEXT_KEYS" in pages_src
    assert "_attach_adv_clear" not in pages_src
    assert "control.suffix = button" not in pages_src
    assert "def make_clearable(" in components_src
    assert "make_clearable(field" in components_src
    assert 'self.app.mark("advanced_params_cleared"' in pages_src
    assert "C.secondary_button(\"清空高级参数\"" in pages_src
    # 日志：SelectionArea 跨行选择 + 复制按钮
    assert "ft.SelectionArea(content=self.list)" in components_src
    assert 'text_button("复制", self._on_copy, t)' in components_src
    # 审计：bib「命中原因」也可选中
    assert 'overflow=ft.TextOverflow.ELLIPSIS,\n                                       selectable=True' in bib_src


def test_selection_area_available_in_flet_version():
    assert issubclass(ft.SelectionArea, ft.Control)
    assert M  # main 可导入（页面构建路径无循环依赖）
