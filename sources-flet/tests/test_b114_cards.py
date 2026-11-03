"""批 114 收口（用户 m02855）：输入区白卡片化 + 顶栏搜索白卡胶囊。

锁四组契约（全部离线，不建窗口、不跑引擎、不联网）：

1. 单篇「输入」区（源稿件 / 输出根目录）包在 C.card 白卡里，与「高级参数」同材质
   同内边距（白底 surface + 1px 描边 + 14 设计 px）；组标题仍留在卡片外。
2. 一校核查（proofcheck）输入区（初排 tex + 语种 + 顺手修选项）同样包一张白卡，
   不再让字段直接躺在纸灰 page_body 上。
3. 单篇页面所有 TextField 都有白卡 Container 祖先（含元数据 / 高级参数 / AI 区）。
4. 顶栏搜索区是白色圆角胶囊：搜索框 + 外观图标，描边只在胶囊上，输入框本身无框
   （换肤/缩放时胶囊属性同步更新，不换控件）。
"""
from __future__ import annotations

import flet as ft

import app.main as M
from app.ui import checks as CK
from app.ui.tokens import Tokens
from test_b114_polish import _build_single, _walk


def _containers(control) -> list[ft.Container]:
    return [ctl for ctl in _walk(control) if isinstance(ctl, ft.Container)]


def _contains(control, target) -> bool:
    return any(ctl is target for ctl in _walk(control))


def _card_around(root, target, t: Tokens) -> ft.Container | None:
    """包含 target 的最内层「白卡」（surface 底 + Border 描边的 Container）。"""
    hits = [ctl for ctl in _containers(root)
            if ctl.bgcolor == t.palette.surface
            and isinstance(ctl.border, ft.Border)
            and _contains(ctl, target)]
    return hits[-1] if hits else None


def _padding_tuple(container: ft.Container) -> tuple[float, float, float, float]:
    pad = container.padding
    return (round(pad.left, 2), round(pad.top, 2),
            round(pad.right, 2), round(pad.bottom, 2))


# ── 1. 单篇「输入」区 = 白卡，与高级参数同材质 ───────────────────────────
def test_single_input_area_is_white_card_same_material_as_advanced():
    page, root = _build_single()
    t = page.t
    source = page.fields["single_source"][0]
    output = page.fields["single_out"][0]
    input_card = _card_around(root, source, t)
    assert input_card is not None, "源稿件字段应在白卡内"
    assert _card_around(root, output, t) is input_card, "两行应共用同一张输入卡"

    card_texts = [str(ctl.value) for ctl in _walk(input_card)
                  if isinstance(ctl, ft.Text)]
    assert any("可直接粘贴路径" in value for value in card_texts)
    browse = [ctl for ctl in _walk(input_card)
              if isinstance(ctl, ft.OutlinedButton)
              and "浏览…" == str(getattr(ctl, "content", ""))]
    assert len(browse) == 2, "卡片内两行各有一个「浏览…」按钮"

    # 高级参数卡：拿一个引擎附加文件字段反查它的白卡。
    advanced_card = _card_around(root, page.fields["convert_sty"][0], t)
    assert advanced_card is not None
    assert input_card is not advanced_card
    for card in (input_card, advanced_card):
        assert card.bgcolor == t.palette.surface
        assert isinstance(card.border, ft.Border)
        assert card.border.top.color == t.palette.border
        assert card.border.top.width > 0
        assert _padding_tuple(card) == (t.px(14),) * 4
    # 组标题不能被包进卡片（与「高级参数」排版一致：标题在卡外）。
    assert _find_text(input_card, "输入") is None
    assert _find_text(input_card, "处理选项") is None


def _find_text(control, value: str) -> ft.Text | None:
    for ctl in _walk(control):
        if isinstance(ctl, ft.Text) and str(ctl.value) == value:
            return ctl
    return None


# ── 2. 一校核查输入区 = 白卡 ───────────────────────────────────────────
def test_proofcheck_input_section_is_white_card():
    page, _ = _build_single()
    t = page.t
    section = CK.proofcheck_section(page)
    assert isinstance(section, ft.Column)
    assert _find_text(section.controls[0], "输入") is not None, "组标题仍在卡片外"
    input_card = section.controls[1]
    assert isinstance(input_card, ft.Container), "输入区应是白卡"
    assert input_card.bgcolor == t.palette.surface
    assert isinstance(input_card.border, ft.Border)
    assert _padding_tuple(input_card) == (t.px(14),) * 4
    field = page.fields["proof_tex"][0]
    assert _contains(input_card, field), "初排 tex 路径输入应在白卡内"
    assert not _contains(section.controls[0], field)
    # 语种下拉 + 「顺手改」选项也在同一张卡里。
    assert _find_text(input_card, "语种") is not None
    assert _find_text(input_card, "顺手把无歧义的改掉（另存新文件）") is not None
    assert any("浏览…" == str(getattr(ctl, "content", ""))
               for ctl in _walk(input_card))


# ── 3. 单篇页面：输入框不再直接躺在纸灰页面上 ─────────────────────────
def test_no_single_page_input_lies_directly_on_page_background():
    page, root = _build_single()
    t = page.t
    fields = [ctl for ctl in _walk(root) if isinstance(ctl, ft.TextField)]
    assert len(fields) >= 6, "单篇页应有源稿/输出/高级参数/元数据等输入框"
    for field in fields:
        label = getattr(field, "_form_label", getattr(field, "label", "?"))
        assert _card_around(root, field, t) is not None, f"输入框 {label} 不在白卡内"


# ── 4. 顶栏搜索白卡胶囊 ──────────────────────────────────────────────
def _app(t: Tokens):
    app = M.App.__new__(M.App)
    app.t = t
    app._search_query = ""
    app._toolbar_row = None
    app.current_page_key = "home"
    app.goto = lambda *a, **k: None
    app.toggle_sidebar = lambda *a, **k: None
    app._toolbar_spec = lambda: ("文稿", "", None)
    return app


def test_toolbar_search_is_white_pill_capsule_without_inner_border():
    t = Tokens("light", 1.0, 96)
    app = _app(t)
    row = app._toolbar_content()
    capsule = app._toolbar_search_card
    search = app._toolbar_search
    assert row.controls[-2] is capsule, "工具栏行内应放胶囊（而不是裸搜索框）"
    assert capsule.bgcolor == t.palette.surface
    assert isinstance(capsule.border, ft.Border)
    assert capsule.border.top.color == t.palette.border
    assert capsule.border_radius == t.radius("pill")
    assert capsule.height == t.px(32)
    assert capsule.content.controls == [search, app._toolbar_tune]
    assert search.border is None, "输入框本身无框，描边由白卡胶囊提供"
    assert search.bgcolor == t.palette.surface
    assert search.height == t.control_h()
    # 换肤+缩放只改属性不换控件。
    t2 = Tokens("dark", 1.25, 96)
    app.t = t2
    app._toolbar_content()
    assert app._toolbar_search is search and app._toolbar_search_card is capsule
    assert capsule.bgcolor == t2.palette.surface
    assert capsule.border.top.color == t2.palette.border
    assert capsule.height == t2.px(32)
    assert capsule.border_radius == t2.radius("pill")
    assert search.height == t2.control_h()
