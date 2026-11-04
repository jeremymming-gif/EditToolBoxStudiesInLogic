"""批 115 · P3-C3 帮助与关于页 S1m-native 契约。

锁：
1. build 走 `C.page_body(..., panel=True)`：白内容面板 + 顶部圆角；滚动容器即
   目录跳转容器（`_scroll` 是内层 Column）。
2. 页面级不再用 `C.card` / `C.section_title`（AST 检查整个模块，防回流）。
3. 8 段（目录 + 7 张内容段）都在：`C.section_label` 标题 + `C.hairline` 发丝线，
   不靠卡片分堆；原 7 张卡的段名一个不少。
4. 页内目录：7 行、两列、每行带滚动锚点；分区节点 `key=ft.ScrollKey("help.<段>")`
   与 `TOC`/`ANCHOR` 常量逐一对齐；跳转协程存在（`Page.run_task` 只收协程）。
5. 去 92px 标签列：入口 / 平台 / 细节 / 文档行都是「标题 + 说明」两行块。
6. 版本与许可走 `C.kv_row`（32 高）；`help_content` 文案一个不丢。
7. `refresh_running(self) -> None` 签名不变（空实现保留）。
8. 可见文本钉点：`Jeremymming` / `MIT` / `作者来稿只读` / `绝不自动 push`。
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from ui import help_content as HC
from ui import help_page as HP
from ui.tokens import Tokens

APP = Path(__file__).resolve().parents[1] / "app"
SOURCE = (APP / "ui" / "help_page.py").read_text(encoding="utf-8")


def _t() -> Tokens:
    return Tokens("light", 1.0, 96)


def _app_stub(ai_enabled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        t=_t(),
        page=SimpleNamespace(run_task=lambda *a, **k: None),
        store=SimpleNamespace(get=lambda key, default=None: ai_enabled),
        app_version="1.4.0",
        flet_version="0.70.0",
        python_version="3.14.8",
        engine_status=SimpleNamespace(version="1.5.0", mode="auto"),
    )


def _walk(control, out: list) -> None:
    out.append(control)
    for attr in ("controls", "content"):
        value = getattr(control, attr, None)
        if isinstance(value, list):
            for item in value:
                _walk(item, out)
        elif isinstance(value, ft.Control):
            _walk(value, out)


def _texts(control) -> list[str]:
    nodes: list = []
    _walk(control, nodes)
    return [str(n.value) for n in nodes
            if isinstance(n, ft.Text) and n.value is not None]


def _calls(func) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}


# ── 1. 白面板 + 滚动容器 ────────────────────────────────────────────────
def test_build_is_white_panel_and_scroll_host_is_toc_target():
    t = _t()
    page = HP.HelpPage(_app_stub())
    box = page.build()
    assert isinstance(box, ft.Container)
    assert box.bgcolor == t.palette.surface
    assert isinstance(box.border_radius, ft.BorderRadius)
    assert box.border_radius.top_left == t.radius("panel")
    assert isinstance(box.content.scroll, ft.Scrollbar)
    assert box.content.scroll.thumb_visibility is True
    assert page._scroll is box.content


# ── 2. 静态契约：无卡片 / 无 section_title / panel=True ─────────────────
def test_module_has_no_card_nor_section_title():
    funcs = [node for node in ast.walk(ast.parse(SOURCE))
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    used = {n.func.attr for f in funcs for n in ast.walk(f)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "card" not in used
    assert "section_title" not in used
    assert "section_label" in used and "hairline" in used

    build = next(f for f in funcs if f.name == "build")
    kw = {k.arg for n in ast.walk(build) if isinstance(n, ast.Call) for k in n.keywords}
    assert "panel" in kw
    panel_kw = [k for n in ast.walk(build) if isinstance(n, ast.Call)
                for k in n.keywords if k.arg == "panel"]
    assert any(getattr(k.value, "value", None) is True for k in panel_kw)


# ── 3. 8 段齐全；TOC 7 行、段名各出现两次 ──────────────────────────────
def test_all_sections_present_and_toc_lists_them():
    page = HP.HelpPage(_app_stub())
    texts = _texts(page.build())
    assert len(HP.TOC) == 7
    for _key, title in HP.TOC:
        assert texts.count(title) == 2, title  # 目录行 + 分区标题
    assert texts.count("目录") == 1


# ── 4. 滚动锚点与 TOC 常量对齐 ─────────────────────────────────────────
def test_section_anchor_keys_match_toc():
    page = HP.HelpPage(_app_stub())
    nodes: list = []
    _walk(page.build(), nodes)
    keys = {n.key.value for n in nodes
            if isinstance(getattr(n, "key", None), ft.ScrollKey)}
    assert keys == {"help.toc"} | {f"{HP.ANCHOR}{key}" for key, _title in HP.TOC}
    assert inspect.iscoroutinefunction(HP.HelpPage._scroll_to)


def test_jump_without_mount_is_silent_and_never_raises():
    page = HP.HelpPage(_app_stub())
    assert getattr(page, "_scroll", None) is None
    page._jump("entries")  # 未 build：静默返回


# ── 5. 去 92px 标签列：入口/平台/细节/文档都是标题+说明两行块 ───────────
def test_entry_rows_have_no_fixed_label_column():
    page = HP.HelpPage(_app_stub())
    nodes: list = []
    _walk(page.build(), nodes)
    widths = [n.width for n in nodes if isinstance(n, ft.Container)]
    assert 92 not in widths and page.t.px(92) not in widths
    texts = _texts(page.build())
    for name, where, _detail in HC.ENTRIES:
        assert name in texts and where in texts


# ── 6. 版本与许可走 kv_row；help_content 文案一个不丢 ───────────────────
def test_version_rows_use_kv_row_and_content_is_not_lost():
    page = HP.HelpPage(_app_stub())
    texts = _texts(page.build())
    nodes: list = []
    _walk(page.build(), nodes)
    row_h = page.t.row_h("table")
    assert len([n for n in nodes
                if isinstance(n, ft.Container) and n.height == row_h]) >= 7
    assert "1.4.0" in texts and "1.5.0 · auto" in texts
    assert "Flet 0.70.0 · Python 3.14.8" in texts
    for label, desc in HC.DETAILS:
        assert label in texts or any(text.startswith(f"{label}：") for text in texts)
        assert any(desc in text for text in texts)
    for label, text_ in HC.DOCS:
        assert label in texts and text_ in texts
    for line in HC.BOUNDARY:
        assert line in texts
    for name, _text in HC.PLATFORM:
        assert name in texts
    for line in (*HC.TRAY_STEPS, *HC.TRAY_LIMITS):
        assert line in texts
    assert HC.LICENSE in texts and HC.THIRD_PARTY in texts


def test_ai_entry_state_follows_store():
    enabled = _texts(HP.HelpPage(_app_stub(ai_enabled=True)).build())
    disabled = _texts(HP.HelpPage(_app_stub(ai_enabled=False)).build())
    assert any("当前已启用" in text for text in enabled)
    assert any("当前已关闭" in text for text in disabled)


def test_tray_disclosure_reveals_steps_in_place():
    page = HP.HelpPage(_app_stub())
    root = page.build()
    assert page._tray_body.visible is False
    page._toggle_tray()
    assert page._tray_body.visible is True
    texts = _texts(root)
    for line in (*HC.TRAY_STEPS, *HC.TRAY_LIMITS):
        assert line in texts


def test_toc_jump_schedules_scroll_on_the_scroll_host():
    calls: list = []
    app = _app_stub()
    app.page.run_task = lambda *a, **k: calls.append(a)
    page = HP.HelpPage(app)
    page.build()
    page._jump("docs")
    assert calls and calls[0][0] == page._scroll_to
    assert calls[0][1] == f"{HP.ANCHOR}docs"


def test_toc_rows_are_clickwired_to_every_section_anchor():
    calls: list = []
    app = _app_stub()
    app.page.run_task = lambda *a, **k: calls.append(a)
    page = HP.HelpPage(app)
    nodes: list = []
    _walk(page.build(), nodes)
    rows = [n for n in nodes if isinstance(n, ft.Container) and n.on_click]
    assert len(rows) >= len(HP.TOC) + 1  # 7 目录行 + 托盘折叠行
    for row in rows[:len(HP.TOC)]:
        row.on_click(SimpleNamespace())
    anchors = [call[1] for call in calls]
    assert [f"{HP.ANCHOR}{key}" for key, _title in HP.TOC] == anchors


# ── 7. refresh_running 签名不变 ────────────────────────────────────────
def test_refresh_running_signature_unchanged():
    sig = inspect.signature(HP.HelpPage.refresh_running)
    assert list(sig.parameters) == ["self"]
    assert sig.return_annotation == "None"
    assert HP.HelpPage(_app_stub()).refresh_running() is None


# ── 8. 可见文本钉点（版本/许可/红线口径）───────────────────────────────
def test_visible_text_pins():
    texts = _texts(HP.HelpPage(_app_stub()).build())
    joined = "\n".join(texts)
    assert "Jeremymming" in joined
    assert "MIT" in joined
    assert "作者来稿只读" in joined
    assert "绝不自动 push" in joined
