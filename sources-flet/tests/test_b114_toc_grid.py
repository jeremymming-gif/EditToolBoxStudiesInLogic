"""批 114（用户 m03037）：生成目录的栏目编辑区改成等宽 2×2 网格，不再溢出/高矮不齐。

锁三组契约（离线，不建窗口）：

1. 「栏目类型 / 栏目名称 / 栏目英文名 / 主持人」四个控件排在
   `C.form_grid()`（ResponsiveRow）里：两列各 6 栅格、窄面板降单列（xs:12）；
   栏目类型用 48 高 `C.select_box`，与三个浮动输入框盒高一致。
2. 可加入的篇目一行用 `C.field_row()`：下拉 28 高、按钮 28 高、底边对齐；
   下拉与表单列都 expand，不再用固定 300/200/160/220 宽把行挤出面板。
3. 预览/读取/保存/生成 按钮组带 `wrap=True`，窄窗换行不溢出。
"""
from __future__ import annotations

import ast
from pathlib import Path

import flet as ft

from ui import components as C
from ui import toc as TOC
from ui.tokens import Tokens

APP = Path(__file__).resolve().parents[1] / "app"


def _t(zoom: float = 1.0) -> Tokens:
    return Tokens("light", zoom, 96)


def _panel(t: Tokens, *, values: dict | None = None,
           selected: list[str] | None = None,
           available: list[dict] | None = None) -> TOC.TocPanel:
    """不建页面，直接给 _editor_controls 需要的状态（沿用 b112 的 object.__new__ 手法）。"""
    panel = object.__new__(TOC.TocPanel)
    panel.t = t
    panel.values = dict(TOC.TS.defaults())
    if values:
        panel.values.update(values)
    panel.selected = list(selected or [])
    panel.available = list(available or [])
    panel.kind = None
    panel.editor_box = ft.Column(spacing=t.sp("1"))
    panel.info = ft.Container()
    panel.feedback = ft.Container()
    panel.add_box = C.select(t, value="", label="可加入的篇目", options=[], expand=True)
    return panel


def _grid(panel: TOC.TocPanel) -> tuple[ft.ResponsiveRow, list]:
    rows = panel._editor_controls()
    grid = rows[0]
    assert isinstance(grid, ft.ResponsiveRow), f"第一行应是表单网格，实际 {type(grid).__name__}"
    return grid, rows


def test_editor_grid_is_two_by_two_with_kind_first():
    t = _t()
    grid, _ = _grid(_panel(t))

    assert grid.columns == 12
    assert len(grid.controls) == 4
    # 两列：第一行 栏目类型 + 栏目名称，第二行 栏目英文名 + 主持人。
    labels = []
    for cell in grid.controls:
        assert isinstance(cell, ft.Container)
        assert cell.col == {"xs": 12, "sm": 6}, cell.col
        content = cell.content
        labels.append(getattr(content, "_form_label", None))
    # 2026-11-06 用户决定(b)：启用栏目四项必填，标签不再标「（可空）」；
    # 旧行为是「栏目英文名（可空）」「主持人（可空）」。
    assert labels == ["栏目类型", "栏目名称（中文）", "栏目英文名", "主持人"]
    # 窄面板降单列：xs 栅格必须是整行。
    assert all(cell.col["xs"] == 12 for cell in grid.controls)
    # 断点：两格放不下（< 2*min_cell 实际 px）时降单列。
    threshold = t.px(160 * 2)
    assert grid.breakpoints[ft.ResponsiveRowBreakpoint.XS] == 0
    for bp in (ft.ResponsiveRowBreakpoint.SM, ft.ResponsiveRowBreakpoint.MD,
               ft.ResponsiveRowBreakpoint.LG, ft.ResponsiveRowBreakpoint.XL,
               ft.ResponsiveRowBreakpoint.XXL):
        assert grid.breakpoints[bp] == threshold


def test_kind_is_select_box_same_height_as_floating_fields():
    t = _t()
    grid, _ = _grid(_panel(t))
    kind, name, name_en, host = (cell.content for cell in grid.controls)

    assert isinstance(kind, ft.PopupMenuButton), "栏目类型应改用 C.select_box（48 高浮动下拉）"
    assert kind._form_label == "栏目类型"
    kind_h = kind.content.height
    assert kind_h == t.px(48)
    for field in (name, name_en, host):
        assert isinstance(field, ft.TextField)
        assert field.height == kind_h, "栏目类型与浮动输入框必须盒高一致"
        assert field.bgcolor == t.palette.surface
    # 四格都 expand：等宽铺满网格格，不靠固定 width。
    assert kind.expand is True
    for field in (name, name_en, host):
        assert field.expand is True and field.width is None


def test_editor_grid_has_no_fixed_pixel_widths():
    """固定 width 是这次溢出的根因：网格内容不得再带 160/200/220/300 这类硬宽度。"""
    t = _t()
    grid, _ = _grid(_panel(t))
    for cell in grid.controls:
        content = cell.content
        assert getattr(content, "width", None) is None
        inner = getattr(content, "content", None)  # select_box 的 chrome
        assert getattr(inner, "width", None) is None


def test_kind_display_follows_current_value_and_selection():
    t = _t()
    panel = _panel(t, values={"columnKind": "custom-kind"})
    grid, _ = _grid(panel)
    kind = grid.controls[0].content
    assert kind._value_text.value == "custom-kind"
    # 选择回调写入设置值并原地更新显示文案（不重建整块面板）。
    panel._on_kind_select("columnB", "乙类")
    assert panel.values["columnKind"] == "columnB"
    assert kind._value_text.value == "乙类"


def test_article_checklist_replaces_add_row_when_report_loaded():
    """批 123（用户 m02698）：报告载入篇目后，编辑区直接给勾选清单，不再需要逐篇加入。"""
    t = _t()
    # 2026-11-06 用户决定(a)/(b)：勾选清单只在「专栏」显示；旧行为是无栏目也显示。
    panel = _panel(t, values={"columnKind": "专栏", "columnName": "逻辑与概率",
                              "columnNameEN": "Logic", "columnHost": "张三"},
                   available=[
        {"name": "甲篇", "title": "甲的标题"},
        {"name": "乙篇", "title": ""},
    ])
    _, rows = _grid(panel)
    checklist = rows[-1]
    assert isinstance(checklist, ft.Column), f"篇目区应是勾选清单，实际 {type(checklist).__name__}"
    header, *items = checklist.controls
    assert isinstance(header, ft.Row)
    texts = [c.value for c in header.controls if isinstance(c, ft.Text)]
    assert any("勾选 0 / 2 篇" in value for value in texts), texts
    buttons = [c for c in header.controls if isinstance(c, ft.TextButton)]
    assert [button.content for button in buttons] == ["全选", "清空"]
    checks = [c for item in items if isinstance(item, ft.Row)
              for c in item.controls if isinstance(c, ft.Checkbox)]
    assert [checkbox.label for checkbox in checks] == ["甲篇", "乙篇"]
    assert all(checkbox.value is False for checkbox in checks)


def test_params_button_row_wraps_in_narrow_panel():
    t = _t()
    panel = _panel(t)

    class _Store:
        def get(self, key, default=None):
            return {} if key == "options" else default

    panel.app = type("App", (), {"store": _Store()})()
    panel.page = type("Page", (), {
        "add_picker": lambda self, *a, **k: ft.Container(),
        "field": lambda self, key: "",
        "mark": lambda self, *a, **k: None,
        "set_field": lambda self, *a, **k: None,
        "page": type("FletPage", (), {"run_task": lambda self, *a, **k: None})(),
    })()
    controls = panel.params()
    button_rows = [
        c for c in controls
        if isinstance(c, ft.Row) and c.controls
        and all(isinstance(b, (ft.FilledButton, ft.OutlinedButton)) for b in c.controls)
    ]
    assert button_rows, "参数区应有操作按钮组"
    row = button_rows[0]
    assert row.wrap is True, "窄窗下按钮组必须换行，否则挤出面板"
    assert len(row.controls) == 4
    assert all(isinstance(b, (ft.FilledButton, ft.OutlinedButton)) for b in row.controls)


def test_editor_source_uses_form_grid_not_fixed_row():
    """AST/静态契约：_editor_controls 通过 C.form_grid 排四格，且不再有 4 控件硬 Row。"""
    source = (APP / "ui" / "toc.py").read_text(encoding="utf-8")
    assert "C.form_grid(" in source
    assert "width=self.t.px(300)" not in source
    assert "width=self.t.px(200)" not in source
    tree = ast.parse(source)
    editor = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_editor_controls"
    )
    calls = [node for node in ast.walk(editor) if isinstance(node, ast.Call)]
    assert any(
        isinstance(func, ast.Attribute) and func.attr == "form_grid" for func in
        (call.func for call in calls)
    ), "_editor_controls 必须用 C.form_grid 排 2×2 网格"


def test_article_name_truncates_before_pushing_buttons_out():
    """超长篇目名单行省略 + tooltip，不再用 intrinsic 宽度把 ↑↓/删除 挤出面板。"""
    t = _t()
    long_name = "很长的篇目名" * 12
    # 2026-11-06 用户决定(a)：本期无栏目不再显示篇目行；这里验专栏模式的截断。
    panel = _panel(t, values={"columnKind": "专栏"}, selected=[long_name])
    _, rows = _grid(panel)
    article_rows = [r for r in rows
                    if isinstance(r, ft.Row) and len(r.controls) == 4]
    assert article_rows, "选中篇目后应出现篇目行"
    row = article_rows[0]
    name_cell = row.controls[0]
    assert isinstance(name_cell, ft.Container) and name_cell.expand is True
    label = name_cell.content
    assert isinstance(label, ft.Text)
    assert label.max_lines == 1
    assert label.overflow == ft.TextOverflow.ELLIPSIS
    assert label.tooltip == long_name
    for control in row.controls[1:]:
        assert isinstance(control, ft.TextButton), "↑↓ 与删除按钮必须仍在行内"


def test_issue_step_fixed_field_widths_fit_narrow_right_panel():
    """整期右侧参数卡内宽 ≈398（1000 宽窗口）：固定宽的 _meta_field 不得再超过 300。"""
    source = (APP / "ui" / "assembly.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    widths: list[float] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "_meta_field":
            for arg in node.args[2:]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                    widths.append(float(arg.value))
    assert widths, "assembly 里应仍有固定宽的 _meta_field 用法（compile_only/merge_out）"
    assert max(widths) <= 300, f"固定宽 {max(widths)} 会在 1000 窗口下溢出右侧参数卡"
