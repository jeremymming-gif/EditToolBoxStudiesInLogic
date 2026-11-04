"""批 112 收尾后修复 B：单篇稿件页布局（高级参数 / 元数据折叠 / 日志浮层）。

锁四件事（不建窗口、不跑引擎）：
1. 高级参数（卷/期/年份/页码/收稿…）始终可见，不在任何折叠区里；
2. 元数据表单在默认展开的 CollapsibleSection 中（批 114 用户 m02696），保留
   MetadataPanel 本体与扁平「预览元数据」；
3. 日志浮层与 body 同在 Stack，浮层后入（z-order 高）、贴底、标题栏常驻，运行时自动展开；
4. `LogConsole.add_line/clear` 接口不变，IssuePage 的非 dock 用法保持旧布局。
"""
from __future__ import annotations

import inspect

import flet as ft

import main as M  # noqa: F401  （保证 app/ 在 sys.path，与其它壳测试一致）
from ui import components as C
from ui import pages as P
from ui.tokens import Tokens


# ── 假壳：够 SinglePage.build() 用，不碰窗口/引擎/设置文件 ─────────────────
class _Store:
    def __init__(self) -> None:
        self.data: dict = {}
        self.recent: dict = {}

    def get(self, key: str, default=None):
        current = self.data
        for part in str(key).split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set(self, key: str, value) -> None:
        parts = str(key).split(".")
        current = self.data
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    def set_recent(self, key: str, value) -> None:
        self.recent[key] = value


class _FletPage:
    def run_task(self, *args, **kwargs) -> None:
        pass

    def update(self) -> None:
        pass

    def show_dialog(self, *args, **kwargs) -> None:
        pass

    def pop_dialog(self) -> None:
        pass


class _FakeApp:
    def __init__(self) -> None:
        self.store = _Store()
        self.page = _FletPage()
        self.running = False
        self.t = Tokens("light", 1.0, 96)
        self.engine_status = type("_S", (), {"ok": True, "version": "1.6.0",
                                            "engine_dir": "/tmp/engine",
                                            "describe": lambda self: "源码引擎 · /tmp/engine"})()
        self.toasts: list[str] = []
        self.marked: list = []

    def toast(self, message: str) -> None:
        self.toasts.append(message)

    def mark(self, event: str, **fields) -> None:
        self.marked.append((event, fields))

    def open_path(self, value) -> None:
        pass

    def copy_text(self, value) -> None:
        pass

    def browse_field(self, *args, **kwargs) -> None:
        pass

    def stop_run(self) -> None:
        self.running = False

    def goto(self, key: str) -> None:
        pass

    def persist(self) -> None:
        pass

    def set_option(self, key: str, value) -> None:
        pass


def _children(control):
    """只走 controls/content（本页用到的容器形状），够做结构断言。"""
    for attr in ("controls", "content"):
        value = getattr(control, attr, None)
        if isinstance(value, (list, tuple)):
            yield from [item for item in value if isinstance(item, ft.Control)]
        elif isinstance(value, ft.Control):
            yield value


def _walk(control, *, skip_collapsed: bool = False):
    """递归遍历；`skip_collapsed=True` 不进 ExpansionTile 的隐藏内容。"""
    yield control
    if skip_collapsed and isinstance(control, ft.ExpansionTile):
        return
    for child in _children(control):
        yield from _walk(child, skip_collapsed=skip_collapsed)


def _field_label(ctl):
    """批 114 F1：浮动 label 改成 form_field 上置，控件上留 _form_label 元数据。"""
    return getattr(ctl, "_form_label", None) or getattr(ctl, "label", None)


def _build_single():
    app = _FakeApp()
    page = P.SinglePage(app)
    root = page.build()
    return page, root


# ── 1. 高级参数始终可见 ────────────────────────────────────────────────────
def test_advanced_params_visible_outside_metadata_section():
    page, root = _build_single()
    labels = {_field_label(ctl) for ctl in _walk(root)
              if isinstance(ctl, ft.TextField)}
    labels.discard(None)
    required = {"卷", "期", "年份", "页码", "收稿日期", "作者", "篇名", "基金"}
    assert required <= labels, f"高级参数未直接出现在主流程: 缺 {required - labels}"
    # 高级参数不在元数据折叠区里；且整页不再有 ExpansionTile（改用 CollapsibleSection）。
    assert not [ctl for ctl in _walk(root) if isinstance(ctl, ft.ExpansionTile)]
    meta_labels = {_field_label(ctl) for ctl in _walk(page._meta_section.body.content)
                   if isinstance(ctl, ft.TextField)}
    meta_labels.discard(None)
    assert required.isdisjoint(meta_labels), "高级参数被塞进元数据区"


# ── 2. 元数据默认展开且保留面板与扁平预览入口 ─────────────────────────────
def test_metadata_expanded_by_default_and_panel_kept():
    page, root = _build_single()
    section = page._meta_section
    assert isinstance(section, C.CollapsibleSection)
    assert section.expanded is True
    assert section.title_label.value == "元数据（可选）"
    assert section.body.visible is True
    assert isinstance(section._toggle_btn, ft.IconButton)   # 扁平箭头，不是 Material 折叠块
    assert page._meta_panel is not None
    panel_controls = list(_walk(section.body.content))
    panel_labels = {_field_label(ctl) for ctl in panel_controls
                    if isinstance(ctl, ft.TextField)}
    panel_labels.discard(None)
    assert {"中文题名", "英文题名"} <= panel_labels, "元数据表单本体被搬走/重写了"
    # 用户 m02990：预览是明确动作 → 描边按钮（不再是纯文字链）。
    previews = [ctl for ctl in panel_controls if isinstance(ctl, ft.OutlinedButton)
                and str(ctl.content) == "预览元数据"]
    assert previews, "「预览元数据」应为明确的描边按钮（m02990 按钮语义统一）"
    assert previews[0].icon is not None, "动作按钮应带图标，避免和链接混淆"
    assert not [ctl for ctl in _walk(root) if isinstance(ctl, ft.ExpansionTile)]


# ── 3. 日志浮层：Stack 后入、贴底、标题栏常驻、运行自动展开 ────────────────
def test_log_dock_is_stacked_on_top_and_collapsed_by_default():
    page, root = _build_single()
    assert isinstance(root, ft.Stack)
    assert len(root.controls) == 2
    body, dock = root.controls
    assert isinstance(body, ft.Container)
    assert dock is page._log.dock_root
    assert (dock.left, dock.right, dock.bottom) == (0, 0, 0)
    # 内容在前、浮层在后 = 浮层 z-order 更高。
    assert root.controls.index(dock) > root.controls.index(body)
    # 收起态：只剩标题栏；展开态：日志区可见。
    assert page._log.dock is True
    assert page._log.is_expanded is False
    assert page._log._log_box.visible is False
    toolbar = page._log.root.content.controls[0]
    assert any(isinstance(ctl, ft.Text) and ctl.value == "运行日志" for ctl in toolbar.controls)
    page._log.expand()
    assert page._log.is_expanded is True and page._log._log_box.visible is True
    page._log.collapse()
    assert page._log.is_expanded is False
    # 内容区底部给浮层让位。
    last = body.content.controls[-1]
    # 批 115 P3-C1：底部 spacer 按截图实测改为 40（原 64 是旧 overlay_reserve；
    # 实测收起 dock 26.0 逻辑px ≈ 设计 39，见 impl-single/reserve-measure.txt）。
    assert isinstance(last, ft.Container) and last.height == page.t.px(52)
    # 运行入口必须自动展开浮层（源码契约，运行时再截图复核）。
    assert "_log.expand()" in inspect.getsource(P.SinglePage._run)


# ── 4. LogConsole 接口不变；非 dock 用法保持旧布局（IssuePage 整期日志）────
def test_logconsole_api_unchanged_and_flow_mode_intact():
    t = Tokens("light", 1.0, 96)
    log = C.LogConsole(t, height=150, title="整期日志")
    assert callable(log.add_line) and callable(log.clear)
    assert log.dock is False
    assert log.is_expanded is True
    assert log._log_box.visible is True
    assert log._toggle_btn is None
    assert log.list.auto_scroll is True
    log.add_line("hello", "ok")
    assert len(log._lines) == 1
    log.clear()
    assert log._lines == [] and log.list.controls == [log.empty]
    # 用完即弃的控件不能有「挂着未 update」的副作用：上面调用均不抛异常。


# ── 5. 批 112 A-01：整期装配步骤行给标题列稳定宽度（缩放/窄窗不竖排） ──────
def _build_issue():
    app = _FakeApp()
    page = P.IssuePage(app)
    root = page.build()
    return page, root


def test_issue_overview_step_row_keeps_title_column_widable():
    # 批 114：步骤卡列表改为导航子页，父页只留一行式概览（序号/图标/标题/状态/运行）。
    page, _root = _build_issue()
    row = page._overview_step_row(0, page.STEPS[0])
    assert isinstance(row, ft.Container) and isinstance(row.content, ft.Row)
    title_cols = [c for c in row.content.controls if isinstance(c, ft.Column)]
    assert title_cols and title_cols[0].expand is True, "标题列必须 expand 吃剩余宽度"
    texts = [c for c in _walk(title_cols[0]) if isinstance(c, ft.Text)]
    assert texts[0].max_lines == 1 and texts[0].overflow == ft.TextOverflow.ELLIPSIS
    assert texts[1].max_lines == 1 and texts[1].overflow == ft.TextOverflow.ELLIPSIS
    buttons = [c for c in _walk(row) if isinstance(c, ft.OutlinedButton)]
    labels = [str(getattr(b.content, "value", b.content)) for b in buttons]
    assert any("运行" in label for label in labels), "概览行也要能直接运行该步"


# ── 6. 批 112 A-05：设置页缩放滑块量程 = 屏幕钳制后的真实上限 ──────────────
def test_zoom_slider_spec_follows_max_zoom():
    app = _FakeApp()
    app.max_zoom = 1.6
    app.store.set("zoom", 1.6)
    page = P.SettingsPage(app)
    spec = page._zoom_slider_spec(page.t)
    assert spec["max"] == 1.6, "滑块上限必须是实际 max_zoom（不再固定 2.0）"
    assert spec["value"] == 1.6
    assert spec["min"] <= 0.8
    assert isinstance(spec["divisions"], int) and spec["divisions"] >= 1
    app.store.set("zoom", 0.5)
    page2 = P.SettingsPage(app)
    assert page2._zoom_slider_spec(page2.t)["value"] >= spec["min"]
