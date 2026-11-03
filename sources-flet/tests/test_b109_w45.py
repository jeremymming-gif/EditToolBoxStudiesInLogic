"""批 109 W4+W5 阻断性测试：排版 token / 品牌资产 / 主题派生 / 日志渲染合并 / 惰性构建。

原则（用户效率指令）：每功能只留 2 例左右「坏了就一定红」的断言，不做快照不写日志化。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

APP = Path(__file__).resolve().parents[1] / "app"


def _tokens():
    from ui.tokens import Tokens

    return Tokens()


def _app_stub():
    return SimpleNamespace(
        t=_tokens(),
        page=SimpleNamespace(run_task=lambda *a, **k: None),
        store=SimpleNamespace(get=lambda *a, **k: ""),
    )


def _walk_texts(control, out: list[str]) -> None:
    if isinstance(control, ft.Text):
        out.append(str(control.value))
        return
    for attr in ("controls", "content", "actions", "title", "subtitle", "controls"):
        value = getattr(control, attr, None)
        if isinstance(value, list):
            for item in value:
                _walk_texts(item, out)
        elif value is not None and hasattr(value, "__class__") and not isinstance(value, str):
            _walk_texts(value, out)


# ── W4 · 排版角色 ───────────────────────────────────────────────────────────
def test_title_roles_share_weight_and_control_is_body_size():
    from ui.tokens import TEXT_ROLE, Tokens

    t = Tokens()
    assert TEXT_ROLE["section"][1] == TEXT_ROLE["item-title"][1] == "semibold"
    assert TEXT_ROLE["page-title"][0] == "h1"
    assert t.font(TEXT_ROLE["control"][0]) == t.font("body")
    with pytest.raises(KeyError):
        t.text_role("no-such-role")


def test_components_render_roles_and_no_bare_text_outside_components():
    from ui import components as C

    t = _tokens()
    title = C.text("题目", t, role="item-title")
    assert title.size == t.font("h3") and title.weight == ft.FontWeight.W_600
    assert C.text("日志", t, role="log").font_family == t.mono_family
    offenders = [p.name for p in sorted(APP.rglob("*.py"))
                 if p.name != "components.py" and "ft.Text(" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_option_row_and_empty_state_use_the_same_title_weight():
    from ui import components as C

    t = _tokens()
    row = C.option_row("标题", "说明", t)
    row_title = row.controls[1].controls[0].controls[0]
    empty = C.empty_state("INBOX", "空", "无数据", t)
    empty_title = empty.content.controls[1]
    assert row_title.size == empty_title.size == t.font("h3")
    assert row_title.weight == empty_title.weight == ft.FontWeight.W_600


def test_every_input_field_uses_body_font_size():
    offenders = []
    for path in sorted((APP / "ui").glob("*.py")) + [APP / "main.py"]:
        if path.name == "components.py":
            continue  # 唯一构造点：字号在 helper 内由 tokens 决定，下面运行时锁死
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "text_size=" in line and 'font("body")' not in line:
                offenders.append(f"{path.name}:{number}")
    assert offenders == []
    from ui.components import text_field

    t = _tokens()
    assert text_field(t).text_size == t.font("body")
    assert text_field(t, multiline=True).text_size == t.font("body")
    assert text_field(t, mono=True).text_size == t.font("mono")


# ── W4 · 品牌资产 / 窗口图标降级 ────────────────────────────────────────────
def test_brand_assets_exist_and_sidebar_uses_png():
    from ui import brand

    png = brand.asset(brand.SIDEBAR_PNG)
    assert png.endswith("logo-256.png") and Path(png).is_file()
    assert brand.asset(brand.WINDOW_ICON).endswith("logo.ico")
    mark = brand.sidebar_mark(_tokens(), 36)
    assert isinstance(mark.content, ft.Image) and mark.content.src == png


def test_window_icon_degrades_without_window_and_tray_image_reads_png():
    from ui import brand

    page = SimpleNamespace()                       # 没有 window 属性
    assert brand.apply_window_icon(page) == ""
    page.window = SimpleNamespace()
    assert brand.apply_window_icon(page).endswith("logo.ico")
    image = brand.tray_image()
    assert image is not None and image.size == (64, 64)


# ── W4 · Flet 主题由 palette 派生（无 INDIGO 种子 / 无 Material3 第二真源）──
@pytest.mark.skipif(sys.platform != "win32", reason="main.py 依赖 Windows 平台旁路")
def test_flet_theme_derives_from_palette():
    from ui.tokens import DARK, LIGHT

    import main as M

    light = M.App._flet_theme(LIGHT)
    assert light.color_scheme.primary == LIGHT.accent
    assert light.color_scheme.surface == LIGHT.surface
    assert M.App._flet_theme(DARK).color_scheme.on_surface == DARK.text
    source = (APP / "main.py").read_text(encoding="utf-8")
    assert "color_scheme_seed" not in source and "use_material3" not in source


# ── W4 · 帮助页：专有许可 + 边界 + 单篇流程说明如实渲染 ───────────────────
def test_help_page_states_proprietary_license_and_boundaries():
    from ui.pages import HelpPage

    app = _app_stub()
    app.app_version = "v1.5.0"
    app.flet_version = "1.0.5"
    app.python_version = "3.14"
    app.engine_status = SimpleNamespace(version="1.5.0", mode="auto")
    texts: list[str] = []
    _walk_texts(HelpPage(app).build(), texts)
    joined = "\n".join(texts)
    assert "Jeremymming" in joined and "MIT" in joined
    assert "作者来稿只读" in joined and "绝不自动 push" in joined


# ── W5 · 日志渲染合并 + 空状态标记 ─────────────────────────────────────────
def test_log_console_batches_updates_and_keeps_tail():
    from ui import components as C

    console = C.LogConsole(_tokens(), height=150)
    calls: list[int] = []
    console._update = lambda: calls.append(1)
    for index in range(2000):
        console.add_line(f"line {index}")
    assert len(calls) <= 200                       # 2000 行 → 远少于 2000 次渲染
    assert len(console._lines) == len(console.list.controls) <= 500
    assert console.list.controls[-1].value == "line 1999"


def test_log_console_empty_marker_not_duplicated():
    from ui import components as C

    console = C.LogConsole(_tokens())
    console._update = lambda: None
    console.add_line("a")
    console.add_line("b")
    assert console.empty not in console.list.controls
    console.clear()
    assert console.list.controls == [console.empty]
    console.add_line("c")
    assert [c.value for c in console.list.controls] == ["c"]


# ── W5 · 惰性构建：只建当前页，其余首次导航时补建 ─────────────────────────
def test_lazy_page_builds_only_once():
    from ui.pages import BasePage

    class Page(BasePage):
        key = "x"
        builds = 0

        def build(self):
            Page.builds += 1
            return "root"

    page = Page(_app_stub())
    assert page.root is None and Page.builds == 0
    assert page.ensure_built() == "root"
    assert page.ensure_built() == "root"
    assert Page.builds == 1


def test_rebuild_shell_builds_only_current_page():
    source = (APP / "main.py").read_text(encoding="utf-8")
    assert "for page_obj in self.pages.values()" not in source
    assert "self.pages[key].ensure_built()" in source
