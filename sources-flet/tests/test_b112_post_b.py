"""批 112 收尾后修复 B：单篇稿件页布局（高级参数 / 元数据折叠 / 日志浮层）。

锁四件事（不建窗口、不跑引擎）：
1. 高级参数（卷/期/年份/页码/收稿…）始终可见，不再藏在 `expanded=False` 的 ExpansionTile；
2. 元数据表单收进默认收起的 ExpansionTile（保留 MetadataPanel 本体与「预览元数据」）；
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
        self.engine_status = type("_S", (), {"ok": True})()
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


def _build_single():
    app = _FakeApp()
    page = P.SinglePage(app)
    root = page.build()
    return page, root


# ── 1. 高级参数始终可见 ────────────────────────────────────────────────────
def test_advanced_params_visible_not_inside_collapsed_tile():
    page, root = _build_single()
    labels = {ctl.label for ctl in _walk(root, skip_collapsed=True)
              if isinstance(ctl, ft.TextField) and ctl.label}
    required = {"卷", "期", "年份", "页码", "收稿日期", "作者", "篇名", "基金"}
    assert required <= labels, f"高级参数未直接出现在主流程: 缺 {required - labels}"
    # 折进去的 ExpansionTile 里不该再有这些字段（元数据表单另算）。
    tiles = [ctl for ctl in _walk(root, skip_collapsed=True)
             if isinstance(ctl, ft.ExpansionTile)]
    assert len(tiles) == 1, "单篇初排页只该有一个折叠块（元数据）"
    inside = [ctl for child in _children(tiles[0]) for ctl in _walk(child)]
    assert "卷" not in {ctl.label for ctl in inside if isinstance(ctl, ft.TextField)}


# ── 2. 元数据默认收起且保留面板与预览入口 ─────────────────────────────────
def test_metadata_folded_by_default_and_panel_kept():
    page, root = _build_single()
    tiles = [ctl for ctl in _walk(root) if isinstance(ctl, ft.ExpansionTile)]
    assert len(tiles) == 1
    tile = tiles[0]
    assert isinstance(tile.title, ft.Text)
    assert tile.title.value == "元数据（可选）"
    assert tile.expanded is False
    assert page._meta_panel is not None
    hidden = [ctl for child in _children(tile) for ctl in _walk(child)]
    hidden_labels = {ctl.label for ctl in hidden
                     if isinstance(ctl, ft.TextField) and ctl.label}
    assert {"中文题名", "英文题名"} <= hidden_labels, "元数据表单本体被搬走/重写了"
    button_labels = {str(getattr(ctl, "content", "")) for ctl in hidden
                     if isinstance(ctl, (ft.TextButton, ft.OutlinedButton))}
    assert "预览元数据" in button_labels


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
    assert isinstance(last, ft.Container) and last.height == page.t.px(64)
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
