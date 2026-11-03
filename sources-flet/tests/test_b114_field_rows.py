"""批 114 收尾（用户 m02816）：输入框统一白底 + 同表单行控件等高。

本文件锁两组契约（离线，不建窗口）：

1. 所有输入类控件显式 `bgcolor == palette.surface`（深浅色一致），不再靠
   「透明 + 父容器白卡片」——单篇稿件的源稿/输出目录直接躺在纸灰页面上，
   透明就是灰块；紧凑 28 框、浮动 48 框、搜索框、下拉（含 select_box）都算。
2. 同一表单行里输入框与右侧按钮/下拉严格同高：浮动字段 48、紧凑字段 28；
   `C.field_row()` 是唯一出口。标签上置（`form_field` 复合列）的行退化为
   底边对齐 END，按钮仍等于 control_h。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import flet as ft

from ui import components as C
from ui.tokens import DARK, LIGHT, Tokens, palette_remap

APP = Path(__file__).resolve().parents[1] / "app"
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:  # 复用 b114 打磨轮的假壳/遍历 helper
    sys.path.insert(0, str(TESTS))

BUTTON_TYPES = (ft.FilledButton, ft.OutlinedButton, ft.TextButton,
                ft.FilledTonalButton, ft.IconButton)
# 会产出「输入类控件」的 helper；有按钮同行时必须显式等高（见 _row_audit）。
FORM_HELPERS = {"text_field", "floating_field", "form_field", "path_picker",
                "select", "select_box", "search_field"}
BUTTON_HELPERS = {"primary_button", "secondary_button", "text_button", "icon_button",
                  "danger_button", "toggle_button"}


def _t(theme: str = "light", zoom: float = 1.0) -> Tokens:
    return Tokens(theme, zoom, 96)


# ── 1. 白底：输入类控件显式 palette.surface ────────────────────────────────
def test_input_controls_are_paper_surface_in_both_themes():
    for theme in ("light", "dark"):
        t = _t(theme)
        surface = t.palette.surface
        compact = C.text_field(t, value="x")
        floating = C.floating_field(t, "源稿件（必填）", value="")
        search = C.search_field(t)
        picker = C.path_picker("源稿件", "", "", t, required=True)
        picker_field = picker.controls[0].controls[0]
        select = C.select(t, value="a", options=[("a", "A")])
        box = C.select_box(t, "模板", value_text="默认")
        for name, control, actual in (
            ("text_field", compact, compact.bgcolor),
            ("floating_field", floating, floating.bgcolor),
            ("search_field", search, search.bgcolor),
            ("path_picker.field", picker_field, picker_field.bgcolor),
            ("select.chrome", select.content, select.content.bgcolor),
            ("select_box", box.content, box.content.bgcolor),
        ):
            assert actual == surface, f"{theme}/{name} 应为纸色底，实际 {actual!r}"
        # 仍是描边路线，不是填充灰块
        assert compact.filled is False and compact.fill_color is None
        assert floating.filled is False and floating.fill_color is None


def test_no_input_helper_uses_surface_2_as_fill():
    """输入 helper 里不许把 surface_2 当填充色（badge/图标底/日志底才允许）。"""
    t = _t()
    picker_field = C.path_picker("源稿件", "", "", t).controls[0].controls[0]
    for control in (C.text_field(t), C.floating_field(t, "卷"),
                    C.search_field(t), picker_field):
        assert control.bgcolor != t.palette.surface_2
    select = C.select(t, value="a", options=[("a", "A")])
    assert select.content.bgcolor != t.palette.surface_2


# ── 2. 同行等高 ────────────────────────────────────────────────────────────
def test_field_row_buttons_match_floating_field_height():
    t = _t()
    field = C.floating_field(t, "暂存目录（可空）", value="")
    browse = C.secondary_button("浏览…", lambda e: None, t, icon="FOLDER_OPEN")
    discover = C.secondary_button("自动发现暂存目录", lambda e: None, t)
    row = C.field_row(field, [browse, discover], t)
    assert isinstance(row, ft.Row)
    assert row.vertical_alignment == ft.CrossAxisAlignment.CENTER
    assert row.controls[0] is field
    target = t.px(48)
    assert field.height == target
    for button in row.controls[1:]:
        assert button.height == target, "同行按钮必须与输入框等高（m02816）"


def test_field_row_buttons_match_compact_field_height():
    t = _t()
    field = C.text_field(t, value="key")
    clear = C.text_button("清除", lambda e: None, t)
    row = C.field_row(field, clear, t)
    assert field.height == t.control_h()
    assert row.controls[1] is clear
    assert clear.height == t.control_h()
    # 显式 height 优先（path_picker 外层「替换…」这类）
    explicit = C.field_row(C.text_field(t), C.secondary_button("替换…", lambda e: None, t),
                           t, height=t.px(48))
    assert explicit.controls[1].height == t.px(48)


def test_field_row_flattens_existing_row_and_icons():
    t = _t()
    field = C.floating_field(t, "路径", value="")
    existing = ft.Row(controls=[
        C.text_button("选择…", lambda e: None, t),
        C.text_button("删除", lambda e: None, t),
    ])
    row = C.field_row(field, existing, t)
    assert len(row.controls) == 3
    for control in row.controls[1:]:
        assert control.height == t.px(48)


def test_field_row_label_above_control_aligns_bottom():
    """form_field 的「标签上置」复合列没有 height：按钮退化到底边对齐。"""
    t = _t()
    dropdown = C.select(t, value="a", options=[("a", "A")])
    wrapped = C.form_field(t, "回收目录", dropdown)
    assert isinstance(wrapped, ft.Column) and wrapped.height is None
    row = C.field_row(wrapped, C.secondary_button("恢复所选目录", lambda e: None, t), t)
    assert row.vertical_alignment == ft.CrossAxisAlignment.END
    assert row.controls[1].height == t.control_h()


def test_path_picker_browse_button_follows_field_height():
    t = _t()
    picker = C.path_picker("源稿件", "", "", t, required=True)
    row = picker.controls[0]
    assert isinstance(row, ft.Row)
    field, browse = row.controls[0], row.controls[1]
    assert field.height == browse.height == t.px(48), "源稿件与「浏览…」必须同高"


def test_single_page_form_rows_have_equal_height_controls():
    """全壳回归：单篇稿件页所有「输入框 + 按钮」同行，按钮高度 = 输入框高度。"""
    from test_b114_polish import _build_single, _walk

    page, root = _build_single()
    offenders: list[str] = []
    checked = 0
    for control in _walk(root):
        if not isinstance(control, ft.Row):
            continue
        fields = [ctl for ctl in control.controls
                  if isinstance(ctl, ft.TextField) and ctl.height]
        buttons = [ctl for ctl in control.controls if isinstance(ctl, BUTTON_TYPES)]
        if not fields or not buttons:
            continue
        checked += 1
        target = float(fields[0].height)
        for button in buttons:
            if abs(float(button.height or 0) - target) > 0.51:
                offenders.append(f"{type(button).__name__} {button.height} != {target}")
    assert checked >= 5, "单篇稿件页应有多条「输入框 + 按钮」同行"
    assert offenders == [], "存在高矮不齐的输入行：" + "; ".join(offenders)


def test_theme_switch_retints_input_bgcolor():
    """换肤热切换：新增的 TextField/下拉显式 bgcolor 也要跟着换，不能停在上一个主题。"""
    import main as M

    app = object.__new__(M.App)  # 只调纯递归方法，不初始化 App
    field = C.text_field(_t())
    assert field.bgcolor == LIGHT.surface
    app._retint_node(field, palette_remap("dark"), palette_remap("dark", role="fg"), set())
    assert field.bgcolor == DARK.surface


# ── 3. 静态契约：内联 ft.Row 混排输入 + 按钮时必须显式高度 ─────────────────
def _module_files() -> list[Path]:
    files = sorted(APP.rglob("*.py"))
    return [path for path in files if "__pycache__" not in path.parts]


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _call_keywords(node: ast.Call) -> set[str]:
    return {kw.arg for kw in node.keywords if kw.arg}


def test_inline_rows_mixing_inputs_and_buttons_must_set_height():
    """新出现的 `ft.Row([...输入 helper..., ...按钮 helper...])` 必须给按钮 height=。

    等高统一走 `C.field_row()`；确实要手写行的地方，至少让按钮显式 height 与
    输入框一致，防止 m02816 的「浏览按钮比输入框矮」再回来。
    """
    offenders: list[str] = []
    for path in _module_files():
        if path.name == "components.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "Row":
                continue
            row_text = ast.unparse(node)
            if "C.field_row(" in row_text:
                # 等高由 field_row 统一保证，外层 Row 只是排版壳。
                continue
            forms = [helper for helper in FORM_HELPERS if f"C.{helper}(" in row_text]
            buttons = [helper for helper in BUTTON_HELPERS if f"C.{helper}(" in row_text]
            if not forms or not buttons:
                continue
            button_calls = [child for child in ast.walk(node)
                            if isinstance(child, ast.Call)
                            and _call_name(child) in BUTTON_HELPERS]
            if all("height" in _call_keywords(call) for call in button_calls):
                continue
            offenders.append(f"{path.relative_to(APP)}:{node.lineno} "
                             f"form={forms[0]} buttons={buttons[0]}")
    assert offenders == [], "输入/按钮同行未显式等高，改用 C.field_row()：" + "; ".join(offenders)
